"""python -m tools.perf_lab --help (desde backend/)."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from tools.perf_lab.benchmark import BACKEND, BATCHES, environment, measure_reads, measure_upserts
from tools.perf_lab.dataset import DatasetConfig, seed_database
from tools.perf_lab.profiler import PROFILE_MODES
from tools.perf_lab.safety import authorize, load_app, read_metadata, require_empty_database, write_metadata


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="DI-A3B: PostgreSQL local/desechable; ninguna llamada a proveedores")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Aplica las migraciones EXISTENTES exclusivamente sobre una BD vacía")
    seed = sub.add_parser("seed", help="Genera datos, sin incluir preparación en los benchmarks")
    seed.add_argument("--fixtures", required=True, type=int)
    seed.add_argument("--seed", type=int, default=20261004)
    seed.add_argument("--output", type=Path, required=True)
    for name in ("bench", "plans"):
        run = sub.add_parser(name)
        run.add_argument("--output", type=Path, required=True)
        run.add_argument("--only", choices=("all", "lookups", "candidate", "upsert"), default="all")
        run.add_argument("--samples", type=int, default=100)
        run.add_argument("--warmup", type=int, default=5)
        run.add_argument("--window-minutes", type=int, default=360)
    profile = sub.add_parser("profile", help="DI-A3C: atribuye el tiempo del upsert real por fases (sin cambiar producción)")
    profile.add_argument("--output", type=Path, required=True)
    profile.add_argument("--batches", type=int, nargs="+", default=[100, 380, 1000], choices=BATCHES)
    profile.add_argument("--modes", nargs="+", default=["update"], choices=PROFILE_MODES)
    profile.add_argument("--samples", type=int, default=30, help="muestras plain y otras tantas instrumentadas")
    profile.add_argument("--warmup", type=int, default=5)
    profile.add_argument("--cprofile-samples", type=int, default=5)
    profile.add_argument("--prepare-threshold", default="driver",
                         help="driver (sin tocar), off o un entero; solo en la conexión del laboratorio")
    args = parser.parse_args(argv)
    if args.command == "seed":
        if not 1 <= args.fixtures <= 1_000_000:
            parser.error("--fixtures debe estar entre 1 y 1.000.000")
    if args.command == "profile":
        if args.samples < 10 or args.warmup < 1:
            parser.error("Se requieren --samples >= 10 y --warmup >= 1")
        if not 0 <= args.cprofile_samples <= 20:
            parser.error("--cprofile-samples debe estar entre 0 y 20")
        if args.prepare_threshold not in {"driver", "off"} and not (
            args.prepare_threshold.isdigit() and int(args.prepare_threshold) <= 100
        ):
            parser.error("--prepare-threshold debe ser driver, off o un entero entre 0 y 100")
    if args.command in {"bench", "plans"}:
        if args.samples < 10 or args.warmup < 1:
            parser.error("Se requieren --samples >= 10 y --warmup >= 1")
        if not 1 <= args.window_minutes < 1440:
            parser.error("--window-minutes debe estar entre 1 y 1439")
        if args.command == "plans" and args.only == "upsert":
            parser.error("plans solo explica SELECTs; no ejecuta escrituras mediante EXPLAIN")
    return args


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # No sobrescribe evidencia previa accidentalmente.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, default=str)
        stream.write("\n")


def _dataset_config(engine, metadata) -> DatasetConfig:
    dataset = metadata.get("dataset")
    if not dataset:
        raise ValueError("Primero ejecuta seed")
    config = DatasetConfig(dataset["fixtures"], dataset["seed"])
    with engine.connect() as conn:
        actual = conn.execute(text("SELECT count(*) FROM fixtures")).scalar_one()
    if actual != config.fixtures:
        raise RuntimeError("El conteo del dataset cambió; utiliza una BD nueva")
    return config


def run_profile(engine, target, metadata, args) -> dict:
    from tools.perf_lab.profiler import database_statistics, measure_profile

    config = _dataset_config(engine, metadata)
    before = environment(engine)
    results = measure_profile(engine, config, args.batches, args.modes, args.samples, args.warmup,
                              args.cprofile_samples, args.prepare_threshold)
    report = {
        "status": "MEDIDO", "created_at": datetime.now(timezone.utc).isoformat(),
        "target": target.authorization, "dataset": metadata["dataset"], "environment": before,
        "methodology": {
            "clock": "time.perf_counter_ns; CPU del hilo con time.thread_time_ns",
            "samples": args.samples, "warmup": args.warmup, "cprofile_samples": args.cprofile_samples,
            "prepare_threshold": args.prepare_threshold,
            "interleaving": "tras el warm-up, muestras plain e instrumentadas alternas en la misma conexión",
            "plain": "repository_write + commit reales con listeners del laboratorio inactivos",
            "instrumented": "eventos del engine + envoltorios temporales de ensure_teams/upsert_fixtures/"
                            "upsert_origin_mappings; los segmentos teselan exactamente wall_before_commit",
            "segment_categories": {
                "sqlalchemy_pre_cursor": "MEDIDO: before_execute -> before_cursor_execute (clave de caché, "
                                         "compilación si miss, construcción de parámetros)",
                "cursor_execute": "MEDIDO: round-trip de cursor.execute (adaptación psycopg + red local + servidor)",
                "sqlalchemy_post_cursor": "MEDIDO: after_cursor_execute -> after_execute",
                "python": "MEDIDO como intervalo entre fronteras; contenido INFERIDO (cProfile)",
            },
            "cprofile": "pase separado, no mezclado con los tiempos; inflado por el perfilador",
            "gc": "MEDIDO con gc.callbacks durante cada muestra (incluye commit)",
            "excluded": "construcción de payloads (medida aparte), normalización, VACUUM ANALYZE, "
                        "validación, limpieza de insert",
            "not_measured": ["tiempo exclusivo del servidor por sentencia", "external providers", "WAN",
                             "concurrent writers", "cold cache"],
        },
        "results": results, "server_stats_end": database_statistics(engine),
    }
    after = environment(engine)
    report["source_unchanged_during_run"] = before["source_sha256"] == after["source_sha256"]
    if not report["source_unchanged_during_run"]:
        report["status"] = "NO COMPARABLE: código cambió durante la ejecución"
        report["source_sha256_after"] = after["source_sha256"]
    return report


def main(argv=None) -> int:
    args = parse_args(argv)
    if getattr(args, "output", None) and args.output.exists():
        raise ValueError("El archivo de salida ya existe; elige otro nombre")
    target = authorize(os.environ)
    engine = load_app(target)
    try:
        if args.command == "init":
            require_empty_database(engine, target)
            from alembic import command
            from alembic.config import Config

            config = Config(str(BACKEND / "alembic.ini"))
            config.set_main_option("script_location", str(BACKEND / "alembic"))
            command.upgrade(config, "head")
            write_metadata(engine, target, {"created_at": datetime.now(timezone.utc).isoformat()})
            print(f"Laboratorio inicializado: {target.authorization}")
            return 0
        metadata = read_metadata(engine, target)
        if args.command == "seed":
            report = seed_database(engine, target, DatasetConfig(args.fixtures, args.seed))
        elif args.command == "profile":
            report = run_profile(engine, target, metadata, args)
        else:
            dataset = metadata.get("dataset")
            if not dataset:
                raise ValueError("Primero ejecuta seed")
            config = DatasetConfig(dataset["fixtures"], dataset["seed"])
            with engine.connect() as conn:
                actual = conn.execute(text("SELECT count(*) FROM fixtures")).scalar_one()
            if actual != config.fixtures:
                raise RuntimeError("El conteo del dataset cambió; utiliza una BD nueva")
            results, plans = [], []
            before = environment(engine)
            if args.only != "upsert":
                results, plans = measure_reads(engine, config, args.samples, args.warmup, args.window_minutes, args.only)
            if args.command == "bench" and args.only in {"all", "upsert"}:
                results += measure_upserts(engine, config, args.samples, args.warmup)
            report = {
                "status": "MEDIDO", "created_at": datetime.now(timezone.utc).isoformat(),
                "target": target.authorization, "dataset": dataset, "environment": before,
                "methodology": {
                    "clock": "time.perf_counter_ns", "samples": args.samples, "warmup": args.warmup,
                    "window_minutes_each_side": args.window_minutes, "percentiles": "linear interpolation (n-1)*p",
                    "cache": "warm-up; no cold-cache eviction; random read keys; single client",
                    "read_scope": "SQLAlchemy execute + fetchall; connection/BEGIN excluded",
                    "write_scope": "real ensure_teams + upsert_fixtures per season; one real COMMIT per batch",
                    "sql_cursor_scope": "driver execute round-trip, not PostgreSQL server-only execution",
                    "python_other_scope": "INFERIDO: measured wall_before_commit minus measured cursor time; includes compilation/fetch/Python, not a CPU profile",
                    "excluded": "dataset generation, payload construction, connection checkout, validation, insert cleanup, maintenance",
                    "insert_cleanup": "DELETE + cascaded mappings after every committed sample, outside timer; may affect cache/bloat",
                    "not_measured": ["external providers", "network WAN", "concurrent writers", "cold cache", "new indexes"],
                },
                "results": results, "plans": plans,
            }
            after = environment(engine)
            report["source_unchanged_during_run"] = before["source_sha256"] == after["source_sha256"]
            if not report["source_unchanged_during_run"]:
                report["status"] = "NO COMPARABLE: código cambió durante la ejecución"
                report["source_sha256_after"] = after["source_sha256"]
        write_report(args.output, report)
        print(f"Evidencia: {args.output}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"Laboratorio bloqueado: {exc}", file=sys.stderr)
        raise SystemExit(2)
