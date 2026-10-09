"""DI-A6 G2 (solo laboratorio): ensayo de 0007 → 0008 y matriz del escritor con contadores de BD.

Uso (desde backend/, con PERF_LAB_DATABASE_URL / PERF_LAB_ALLOW_DESTRUCTIVE como el resto del laboratorio):
    python -m tools.perf_lab.g2 upgrade --output <json>
    python -m tools.perf_lab.g2 writes --label <pase> --batches 380 1000 2000 --samples 30 --warmup 5 --output <json>
    python -m tools.perf_lab.g2 summarize <json>...

No cambia el escritor ni la migración: envuelve tools.perf_lab (run_upgrade y a6.measure_writes) y
añade lo que G2 necesita fuera del reloj:
- upgrade: tamaños por relación antes/después y una sonda de lectura sobre fixtures en otra conexión
  (cuánto tiempo bloquea la migración a un lector);
- writes: pg_stat_database (commits, rollbacks, deadlocks, conflictos) y pg_stat_user_tables
  (inserciones, actualizaciones y HOT de fixtures) antes/después del pase, y tamaños por relación.
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from tools.perf_lab.safety import authorize, load_app, read_metadata

RELATIONS = ("fixtures", "fixture_observations", "fixture_provider_mappings", "team_provider_mappings", "teams")
STAT_SETTLE_SECONDS = 2.0  # el volcado de estadísticas acumuladas no es inmediato (PG15+)


def _maint(engine):
    return create_engine(engine.url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})


def relation_sizes(conn) -> dict:
    out = {}
    for rel in RELATIONS:
        if conn.exec_driver_sql(f"SELECT to_regclass('{rel}') IS NOT NULL").scalar_one():
            out[rel] = dict(conn.exec_driver_sql(
                f"SELECT pg_relation_size('{rel}') AS heap_bytes, pg_indexes_size('{rel}') AS index_bytes, "
                f"pg_total_relation_size('{rel}') AS total_bytes, (SELECT count(*) FROM {rel}) AS rows"
            ).mappings().one())
    return out


def size_delta(before: dict, after: dict) -> dict:
    return {rel: {k: after[rel][k] - before.get(rel, {}).get(k, 0) for k in after[rel]} for rel in after}


def db_counters(conn) -> dict:
    time.sleep(STAT_SETTLE_SECONDS)
    conn.exec_driver_sql("SELECT pg_stat_clear_snapshot()")
    database = dict(conn.exec_driver_sql(
        "SELECT xact_commit, xact_rollback, deadlocks, conflicts, temp_files FROM pg_stat_database "
        "WHERE datname = current_database()").mappings().one())
    tables = {r["relname"]: {k: r[k] for k in ("n_tup_ins", "n_tup_upd", "n_tup_hot_upd", "n_tup_del", "n_dead_tup",
                                                "autovacuum_count", "vacuum_count")}
              for r in conn.exec_driver_sql(
                  "SELECT relname, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del, n_dead_tup, autovacuum_count, vacuum_count "
                  "FROM pg_stat_user_tables WHERE relname = ANY(%(r)s)", {"r": list(RELATIONS)}).mappings()}
    return {"database": database, "tables": tables}


def counters_delta(before: dict, after: dict) -> dict:
    return {
        "database": {k: after["database"][k] - before["database"][k] for k in after["database"]},
        "tables": {t: {k: after["tables"][t][k] - before["tables"].get(t, {}).get(k, 0) for k in after["tables"][t]}
                   for t in after["tables"]},
    }


def alembic_version(conn) -> str:
    return conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()


class ReadProbe(threading.Thread):
    """Lector en otra conexión: una lectura de fixtures cada interval segundos, cronometrada."""

    def __init__(self, url, interval: float = 0.05):
        super().__init__(daemon=True)
        self.engine = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
        self.interval = interval
        self.stop = threading.Event()
        self.samples: list[tuple[float, float]] = []  # (inicio relativo s, duración ms)
        self.errors: list[str] = []

    def run(self):
        t0 = time.perf_counter()
        with self.engine.connect() as conn:
            while not self.stop.is_set():
                started = time.perf_counter()
                try:
                    conn.exec_driver_sql("SELECT id FROM fixtures ORDER BY id LIMIT 1").all()
                except Exception as exc:  # noqa: BLE001  (se registra el tipo, no se interrumpe la migración)
                    self.errors.append(exc.__class__.__name__)
                self.samples.append((started - t0, (time.perf_counter() - started) * 1000))
                time.sleep(self.interval)
        self.engine.dispose()

    def summary(self) -> dict:
        durations = sorted(d for _, d in self.samples)
        blocked = [d for d in durations if d >= 100]
        return {"probe_reads": len(durations), "interval_s": self.interval, "max_read_ms": durations[-1] if durations else None,
                "reads_over_100ms": len(blocked), "max_blocked_read_ms": max(blocked) if blocked else 0.0,
                "errors": self.errors}


def run_upgrade_g2(engine, target, metadata) -> dict:
    from tools.perf_lab.__main__ import run_upgrade

    maint = _maint(engine)
    with maint.connect() as m:
        version_before = alembic_version(m)
        sizes_before = relation_sizes(m)
    probe = ReadProbe(engine.url)
    probe.start()
    time.sleep(0.5)
    started = time.perf_counter()
    report = run_upgrade(engine, target, metadata)
    elapsed = time.perf_counter() - started
    time.sleep(0.5)
    probe.stop.set()
    probe.join()
    with maint.connect() as m:
        sizes_after = relation_sizes(m)
        version_after = alembic_version(m)
    maint.dispose()
    report.update({
        "g2": {
            "version_before": version_before, "version_after": version_after,
            "wrapper_wall_seconds": elapsed,
            "relation_sizes_before": sizes_before, "relation_sizes_after": sizes_after,
            "relation_size_delta": size_delta(sizes_before, sizes_after),
            "read_probe": probe.summary(),
            "note": "alembic upgrade es UNA transacción (DDL + bootstrap + índices): su duración es la del bloqueo "
                    "ACCESS EXCLUSIVE sobre fixtures desde LOCK TABLE hasta el COMMIT",
        }
    })
    return report


def run_writes_g2(engine, target, metadata, args) -> dict:
    from tools.perf_lab import a6
    from tools.perf_lab.__main__ import _dataset_config
    from tools.perf_lab.benchmark import environment

    config = _dataset_config(engine, metadata)
    maint = _maint(engine)
    with maint.connect() as m:
        version = alembic_version(m)
        if version != "0008":
            raise RuntimeError(f"G2 exige el esquema 0008 (hay {version})")
        sizes_before = relation_sizes(m)
        counters_before = db_counters(m)
    env_before = environment(engine)
    started = time.perf_counter()
    results = a6.measure_writes(engine, config, args.batches, args.modes, args.samples, args.warmup)
    elapsed = time.perf_counter() - started
    with maint.connect() as m:
        counters_after = db_counters(m)
        sizes_after = relation_sizes(m)
        version_after = alembic_version(m)
    maint.dispose()
    env_after = environment(engine)
    return {
        "status": "MEDIDO", "created_at": datetime.now(timezone.utc).isoformat(), "target": target.authorization,
        "label": args.label, "dataset": metadata["dataset"], "alembic_version": [version, version_after],
        "environment": env_before, "source_unchanged_during_run": env_before["source_sha256"] == env_after["source_sha256"],
        "methodology": {
            "harness": "tools.perf_lab.a6.measure_writes (escritor real: ensure_teams + upsert_fixtures UNNEST, un COMMIT por lote)",
            "samples": args.samples, "warmup": args.warmup, "batches": args.batches, "modes": args.modes,
            "percentiles": "interpolación lineal (n-1)*p sobre las muestras medidas (sin descartar ninguna)",
            "pass_counters": "pg_stat_database / pg_stat_user_tables antes y después del pase (incluye limpiezas de insert "
                             "y normalizaciones fuera del reloj; los valores por muestra están en cada resultado)",
        },
        "pass_seconds": elapsed,
        "pass_counters_delta": counters_delta(counters_before, counters_after),
        "pass_relation_sizes_before": sizes_before, "pass_relation_sizes_after": sizes_after,
        "pass_relation_size_delta": size_delta(sizes_before, sizes_after),
        "results": results,
    }


def summarize(paths: list[Path]) -> str:
    lines = []
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if "results" not in report:
            g2 = report["g2"]
            lines.append(f"## {path.name}: upgrade {g2['version_before']} → {g2['version_after']}")
            lines.append(f"- bootstrap {report['bootstrap_wall_seconds']:.2f} s; counts {report['counts']}")
            lines.append(f"- probe {g2['read_probe']}")
            for rel, d in g2["relation_size_delta"].items():
                lines.append(f"- {rel}: heap {d['heap_bytes'] / 2**20:+.1f} MiB, index {d['index_bytes'] / 2**20:+.1f} MiB, rows {d['rows']:+d}")
            continue
        lines.append(f"## {path.name}: pase {report['label']} ({report['pass_seconds']:.0f} s)")
        lines.append(f"- contadores del pase: {report['pass_counters_delta']['database']}")
        lines.append("| caso | n | p50 ms | p95 ms | max ms | commit p50 | commit p95 | WAL p50 B | HOT fixtures | obs ins/muestra | created/updated/unchanged p50 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for r in report["results"]:
            if r.get("status") != "MEDIDO":
                lines.append(f"| {r['operation']} | — | {r.get('status')} |")
                continue
            obs = r["xact_inserted"].get("fixture_observations", {}).get("p50", 0)
            uc = r["upsert_counts"]
            hot = r["fixtures_hot_ratio"]
            lines.append(
                f"| {r['operation']} | {len(r['raw_ms']['total'])} | {r['total_ms']['p50']:.1f} | {r['total_ms']['p95']:.1f} | "
                f"{r['total_ms']['max']:.1f} | {r['commit_ms']['p50']:.2f} | {r['commit_ms']['p95']:.2f} | "
                f"{r['wal_bytes']['p50']:.0f} | {'—' if hot is None else f'{hot:.1%}'} | {obs:.0f} | "
                f"{uc['created']['p50']:.0f}/{uc['updated']['p50']:.0f}/{uc['unchanged']['p50']:.0f} |"
            )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.perf_lab.g2")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upgrade")
    up.add_argument("--output", type=Path, required=True)
    wr = sub.add_parser("writes")
    wr.add_argument("--label", required=True)
    wr.add_argument("--output", type=Path, required=True)
    wr.add_argument("--batches", type=int, nargs="+", default=[380, 1000, 2000])
    wr.add_argument("--modes", nargs="+", default=["insert", "update", "unchanged", "older", "mixed", "tie", "replay"])
    wr.add_argument("--samples", type=int, default=30)
    wr.add_argument("--warmup", type=int, default=5)
    sm = sub.add_parser("summarize")
    sm.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args(argv)
    if args.command == "summarize":
        print(summarize(args.paths))
        return 0
    if args.output.exists():
        raise ValueError("El archivo de salida ya existe; elige otro nombre")
    target = authorize(os.environ)
    engine = load_app(target)
    try:
        metadata = read_metadata(engine, target)
        report = run_upgrade_g2(engine, target, metadata) if args.command == "upgrade" else run_writes_g2(engine, target, metadata, args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
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
