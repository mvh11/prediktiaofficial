"""Medidas repetidas sobre PostgreSQL real; sin providers ni cambios de esquema."""

import hashlib
import math
import platform
import random
import subprocess
import sys
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import psycopg
import sqlalchemy
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session

from tools.perf_lab.dataset import (
    COMPETITION_EXTERNAL_BASE, FIXTURE_EXTERNAL_BASE, TEAM_EXTERNAL_BASE,
    DatasetConfig, analyze, fixture_at,
)

BACKEND = Path(__file__).resolve().parents[2]
BATCHES = (1, 10, 100, 380, 1000)
QUERIES = {
    "mapping_api": "SELECT fixture_id FROM fixture_provider_mappings WHERE provider = :provider AND external_id = :external_id",
    "mapping_5dollar": "SELECT fixture_id FROM fixture_provider_mappings WHERE provider = :provider AND external_id = :external_id",
    "fixture_internal": "SELECT id, season_id, home_team_id, away_team_id, kickoff_at FROM fixtures WHERE id = :id",
    "fixture_external": "SELECT id, season_id, home_team_id, away_team_id, kickoff_at FROM fixtures WHERE external_id = :external_id",
    "candidate": """SELECT id, season_id, home_team_id, away_team_id, kickoff_at FROM fixtures
        WHERE season_id = :season_id AND home_team_id = :home_team_id AND away_team_id = :away_team_id
          AND kickoff_at >= :kickoff_from AND kickoff_at <= :kickoff_to""",
}


def distribution(values: list[float]) -> dict:
    """Percentiles interpolados (posición (n-1)*p); conserva muestras crudas aparte."""
    if len(values) < 2 or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Se necesitan al menos dos muestras finitas y no negativas")
    ordered = sorted(values)

    def percentile(p):
        position = (len(ordered) - 1) * p
        lower = math.floor(position)
        upper = math.ceil(position)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {"samples": len(values), "min": ordered[0], "p50": percentile(.50),
            "p95": percentile(.95), "p99": percentile(.99), "max": ordered[-1]}


class SQLMeter:
    """Tiempo driver/round-trip de cursor.execute, NO tiempo exclusivo del servidor.

    Excluye compilación SQLAlchemy y fetch/materialización posterior. EXPLAIN mide
    por separado ejecución del servidor. COMMIT se mide con reloj propio.
    """

    def __init__(self, engine):
        self.engine = engine
        self.enabled = False
        self.reset()

    def reset(self):
        self.nanoseconds = 0
        self.count = 0

    def before(self, conn, cursor, statement, parameters, context, executemany):
        if self.enabled:
            context._lab_started = time.perf_counter_ns()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        if self.enabled:
            self.nanoseconds += time.perf_counter_ns() - context._lab_started
            self.count += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self.before)
        event.listen(self.engine, "after_cursor_execute", self.after)
        return self

    def __exit__(self, *_):
        event.remove(self.engine, "before_cursor_execute", self.before)
        event.remove(self.engine, "after_cursor_execute", self.after)


def query_parameters(kind: str, hit: bool, index: int, config: DatasetConfig, window_minutes: int) -> dict:
    row = fixture_at(index, config)
    if kind.startswith("mapping"):
        if kind == "mapping_api":
            return {"provider": "api-football", "external_id": str(row["external_id"]) if hit else f"missing-{index}"}
        # Escoge siempre una entidad con cobertura del segundo proveedor.
        fid = row["id"] if row["id"] % 5 else row["id"] - 1
        return {"provider": "5dollarfootballapi", "external_id": f"lab-fixture_id-{fid}" if hit else f"missing-{index}"}
    if kind == "fixture_internal":
        return {"id": row["id"] if hit else config.fixtures + index + 1}
    if kind == "fixture_external":
        return {"external_id": row["external_id"] if hit else FIXTURE_EXTERNAL_BASE + config.fixtures + index + 1}
    # La misma pareja solo juega una vez por localía/temporada. Ventana desplazada
    # 30 días para miss (ventanas admitidas < un día).
    kickoff = row["kickoff_at"] + (timedelta(0) if hit else timedelta(days=30))
    return {"season_id": row["season_id"], "home_team_id": row["home_team_id"], "away_team_id": row["away_team_id"],
            "kickoff_from": kickoff - timedelta(minutes=window_minutes),
            "kickoff_to": kickoff + timedelta(minutes=window_minutes)}


def measure_reads(engine: Engine, config: DatasetConfig, samples: int, warmup: int,
                  window_minutes: int, only: str) -> tuple[list[dict], list[dict]]:
    results, plans = [], []
    kinds = list(QUERIES) if only == "all" else (["candidate"] if only == "candidate" else list(QUERIES)[:-1])
    rng = random.Random(config.seed)
    indices = [rng.randrange(config.fixtures) for _ in range(samples + warmup)]
    with engine.connect() as conn, SQLMeter(engine) as meter:
        # Excluye apertura/conexión y BEGIN de las muestras de lecturas.
        conn.execute(text("SELECT 1"))
        for kind in kinds:
            for hit in (True, False):
                case = f"{kind}_{'hit' if hit else 'miss'}"
                statement = text(QUERIES[kind])
                parameters = [query_parameters(kind, hit, i, config, window_minutes) for i in indices]
                wall, sql = [], []
                for i, params in enumerate(parameters):
                    meter.reset()
                    meter.enabled = True
                    started = time.perf_counter_ns()
                    rows = conn.execute(statement, params).all()
                    elapsed = (time.perf_counter_ns() - started) / 1e6
                    meter.enabled = False
                    if len(rows) != int(hit):
                        raise AssertionError(f"{case}: se esperaba {int(hit)} fila, llegaron {len(rows)}")
                    if i >= warmup:
                        wall.append(elapsed)
                        sql.append(meter.nanoseconds / 1e6)
                results.append({"operation": case, "status": "MEDIDO", "unit": "ms",
                                "wall_ms": distribution(wall), "sql_cursor_ms": distribution(sql),
                                "raw_ms": {"wall": wall, "sql_cursor": sql}})
                # Plan separado, posterior a las muestras: no contamina su reloj.
                params = parameters[warmup]
                plan = conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + QUERIES[kind]), params).scalar_one()
                plans.append({"operation": case, "status": "MEDIDO", "sql": QUERIES[kind],
                              "parameters": params, "explain": plan})
                print(f"{case}: p50={distribution(wall)['p50']:.3f} ms", flush=True)
    return results, plans


def payload_groups(config: DatasetConfig, batch: int, mode: str, variant: int = 0) -> dict:
    from app.schemas.catalog import TeamData
    from app.schemas.fixture import FixtureData

    groups = defaultdict(list)
    for index in range(batch):
        row = fixture_at(index, config)
        sid = row["season_id"]
        league = (sid - 1) // 10 + 1
        fields = {k: v for k, v in row.items() if k not in {"id", "season_id", "home_team_id", "away_team_id"}}
        if mode == "insert":
            fields["external_id"] += 100_000_000
        if mode == "update":
            fields["venue_name"] += f" revision {variant}"
        groups[sid].append(FixtureData(
            **fields, competition_external_id=COMPETITION_EXTERNAL_BASE + league, season=config.year(sid),
            home_team=TeamData(external_id=TEAM_EXTERNAL_BASE + row["home_team_id"], name=f"Equipo laboratorio {row['home_team_id']}"),
            away_team=TeamData(external_id=TEAM_EXTERNAL_BASE + row["away_team_id"], name=f"Equipo laboratorio {row['away_team_id']}"),
        ))
    return dict(groups)


def repository_write(db: Session, groups: dict) -> int:
    from app.repositories.fixture_repository import ensure_teams, upsert_fixtures

    count = 0
    for season_id, fixtures in groups.items():
        teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
        ids = ensure_teams(db, teams, "api-football")
        count += upsert_fixtures(db, season_id, fixtures, ids, "api-football").received
    return count


def verify_write(engine: Engine, groups: dict) -> None:
    """Valida fuera del reloj los valores persistidos y el mapping de cada fila."""
    from app.models import Fixture, FixtureProviderMapping
    from sqlalchemy import select

    incoming = {f.external_id: (sid, f) for sid, fixtures in groups.items() for f in fixtures}
    with engine.connect() as conn:
        rows = conn.execute(select(Fixture.external_id, Fixture.season_id, Fixture.venue_name, Fixture.kickoff_at)
                            .where(Fixture.external_id.in_(incoming))).all()
        assert len(rows) == len(incoming)
        for external_id, sid, venue, kickoff in rows:
            expected_sid, f = incoming[external_id]
            assert (sid, venue, kickoff) == (expected_sid, f.venue_name, f.kickoff_at)
        mapped = conn.execute(select(FixtureProviderMapping.external_id)
                              .where(FixtureProviderMapping.provider == "api-football",
                                     FixtureProviderMapping.external_id.in_([str(i) for i in incoming]))).all()
        assert len(mapped) == len(incoming)


def measure_upserts(engine: Engine, config: DatasetConfig, samples: int, warmup: int) -> list[dict]:
    from app.models import Fixture
    from sqlalchemy import delete

    results = []
    with SQLMeter(engine) as meter:
        for batch in BATCHES:
            if batch > config.fixtures:
                results.append({"operation": f"upsert_{batch}", "status": "NO MEDIDO", "reason": "dataset menor que lote"})
                continue
            base = payload_groups(config, batch, "unchanged")
            for mode in ("unchanged", "update", "insert"):
                # Preparación, normalización y VACUUM excluidos del benchmark.
                with Session(engine) as db:
                    repository_write(db, base)
                    db.commit()
                analyze(engine)
                variants = [payload_groups(config, batch, mode, v) for v in (0, 1)]
                external_ids = [f.external_id for fs in variants[0].values() for f in fs]
                raw = {k: [] for k in ("wall_before_commit", "sql_cursor", "python_other", "commit", "total")}
                sql_counts = []
                with engine.connect() as conn:
                    for i in range(warmup + samples):
                        groups = variants[i % 2]
                        with Session(bind=conn) as db:
                            meter.reset()
                            meter.enabled = True
                            started = time.perf_counter_ns()
                            count = repository_write(db, groups)
                            before_commit = time.perf_counter_ns()
                            db.commit()
                            finished = time.perf_counter_ns()
                            meter.enabled = False
                        assert count == batch
                        before_ms = (before_commit - started) / 1e6
                        cursor_ms = meter.nanoseconds / 1e6
                        if i >= warmup:
                            raw["wall_before_commit"].append(before_ms)
                            raw["sql_cursor"].append(cursor_ms)
                            raw["python_other"].append(max(0, before_ms - cursor_ms))
                            raw["commit"].append((finished - before_commit) / 1e6)
                            raw["total"].append((finished - started) / 1e6)
                            sql_counts.append(meter.count)
                        if i == warmup:
                            verify_write(engine, groups)
                        if mode == "insert":
                            # El próximo intento vuelve a ser INSERT real. Limpieza fuera
                            # del reloj, CASCADE elimina mappings; conserva N fixtures.
                            with engine.begin() as cleanup:
                                cleanup.execute(delete(Fixture).where(Fixture.external_id.in_(external_ids)))
                results.append({"operation": f"upsert_{mode}_{batch}", "status": "MEDIDO", "unit": "ms",
                                "batch": batch, "season_groups": len(base), "mode": mode,
                                **{f"{key}_ms": distribution(values) for key, values in raw.items()},
                                "sql_statements": distribution(sql_counts), "raw_ms": raw,
                                "first_measured_sample_verified": True})
                print(f"upsert_{mode}_{batch}: p50 total={distribution(raw['total'])['p50']:.3f} ms", flush=True)
            # Deja los datos semánticamente iguales al dataset inicial.
            with Session(engine) as db:
                repository_write(db, base)
                db.commit()
    return results


def environment(engine: Engine) -> dict:
    with engine.connect() as conn:
        version = conn.exec_driver_sql("SELECT version()").scalar_one()
        settings = {name: conn.exec_driver_sql(f"SHOW {name}").scalar_one() for name in (
            "shared_buffers", "work_mem", "effective_cache_size", "synchronous_commit", "fsync",
            "full_page_writes", "max_connections", "default_statistics_target", "autovacuum", "statement_timeout",
        )}
        sizes = [dict(row) for row in conn.execute(text("""
            SELECT relname, pg_total_relation_size(relid) AS total_bytes
            FROM pg_statio_user_tables ORDER BY relname
        """)).mappings()]
        indexes = [dict(row) for row in conn.execute(text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename, indexname"
        )).mappings()]
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=BACKEND, text=True).strip()

    sources = {}
    for folder in (BACKEND / "app", BACKEND / "alembic", BACKEND / "tools" / "perf_lab"):
        for file in sorted(folder.rglob("*.py")):
            sources[str(file.relative_to(BACKEND)).replace("\\", "/")] = hashlib.sha256(file.read_bytes()).hexdigest()
    return {"python": sys.version, "sqlalchemy": sqlalchemy.__version__, "psycopg": psycopg.__version__,
            "os": platform.platform(), "processor": platform.processor(), "postgresql": version,
            "postgresql_settings": settings, "table_sizes": sizes, "indexes": indexes,
            "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
            "git_status": git("status", "--short"), "source_sha256": sources}
