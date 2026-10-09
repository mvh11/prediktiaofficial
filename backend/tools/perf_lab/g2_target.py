"""DI-A6 G2 en destino (solo laboratorio): rama Neon NO productiva, verificada en cada conexión.

Uso (desde backend/; la URL llega SOLO por entorno y nunca se imprime ni se guarda):
    DI_A6_G2_DATABASE_URL=<opaca>  G2_TARGET_CLASSIFICATION=NON_PRODUCTION  G2_TARGET_NEON_BRANCH_ID=br-...
    python -m tools.perf_lab.g2_target identity --expect-revision 0007 --expect-fixtures 18671 --output <json>
    python -m tools.perf_lab.g2_target upgrade --output <json>      # 0007 → 0008 real, con sonda de lectura
    python -m tools.perf_lab.g2_target verify --output <json>       # invariantes A6 (recuentos agregados)
    python -m tools.perf_lab.g2_target seed --output <json>         # dataset sintético aislado, por el escritor real
    python -m tools.perf_lab.g2_target writes --label <pase> --output <json>

Diferencias con el laboratorio local (todo lo demás es el arnés aceptado):
- Guarda propia y fail-closed: cada conexión nueva de cualquier engine del proceso (app, Alembic,
  mantenimiento, sonda) comprueba `neon.branch_id` contra G2_TARGET_NEON_BRANCH_ID antes de usarse.
- Sin marca COMMENT ON DATABASE ni VACUUM forzado: el HOT del destino se mide bajo autovacuum.
- Escritor sobre un dataset sintético determinista en rangos de ids reservados (el mismo calendario
  de `dataset.fixture_at`), creado con el escritor real después del ensayo de 0008. Nunca se escriben
  ni se leen valores de las filas reales derivadas de producción: solo recuentos, tamaños y metadatos.
"""

import argparse
import json
import os
import platform
import re
import statistics
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url

from tools.perf_lab.dataset import (
    AS_OF, COMPETITION_EXTERNAL_BASE, FIXTURE_EXTERNAL_BASE, FIXTURES_PER_SEASON, TEAM_EXTERNAL_BASE,
    DatasetConfig, fixture_at,
)

URL_ENV = "DI_A6_G2_DATABASE_URL"
BRANCH_ENV = "G2_TARGET_NEON_BRANCH_ID"
CLASSIFICATION_ENV = "G2_TARGET_CLASSIFICATION"
LAB_FIXTURES = 3800  # una liga sintética de 10 temporadas × 380: cubre el lote 2000 (6 temporadas)
LAB_SEED = 20261004
LAB_COMPETITION_EXTERNAL_ID = COMPETITION_EXTERNAL_BASE + 1
LAB_COMPETITION_NAME = "G2 laboratorio sintético (DI-A6)"
SECURE_SSLMODES = {"require", "verify-ca", "verify-full"}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class TargetRefused(RuntimeError):
    """El destino no es el autorizado: no se ejecuta nada más."""


@dataclass(frozen=True)
class TargetSpec:
    url: URL
    branch_id: str


def authorize_target(environ, *, url_env: str = URL_ENV, branch_env: str = BRANCH_ENV) -> TargetSpec:
    """Valida el entorno antes de importar la app o abrir una conexión."""
    if environ.get(CLASSIFICATION_ENV) != "NON_PRODUCTION":
        raise TargetRefused(f"{CLASSIFICATION_ENV} debe ser NON_PRODUCTION")
    branch = environ.get(branch_env, "")
    if not re.fullmatch(r"br-[a-z0-9-]{3,60}", branch):
        raise TargetRefused(f"{branch_env} ausente o inválido")
    if any(k.startswith("PG") and v for k, v in environ.items()):
        raise TargetRefused("El destino requiere un entorno sin variables PG*")
    raw = environ.get(url_env, "")
    if not raw:
        raise TargetRefused(f"Falta {url_env}")
    try:
        url = make_url(raw)
    except Exception as exc:  # noqa: BLE001  el mensaje podría contener la URL
        raise TargetRefused("URL del destino no interpretable") from exc
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise TargetRefused("Se requiere PostgreSQL con psycopg")
    if url.host not in LOOPBACK and url.query.get("sslmode") not in SECURE_SSLMODES:
        raise TargetRefused("Un destino remoto exige sslmode=require o superior")
    return TargetSpec(url, branch)


def branch_matches(dbapi_connection, expected: str) -> bool:
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('neon.branch_id', true), pg_is_in_recovery()")
        branch, in_recovery = cursor.fetchone()
    if not dbapi_connection.autocommit:
        dbapi_connection.rollback()  # no deja una transacción abierta antes de que SQLAlchemy configure la conexión
    return branch == expected and not in_recovery


def install_branch_guard(spec: TargetSpec) -> None:
    """Toda conexión DBAPI nueva de CUALQUIER engine del proceso se comprueba antes de usarse."""

    @event.listens_for(Engine, "connect")
    def _guard(dbapi_connection, _record):
        if not branch_matches(dbapi_connection, spec.branch_id):
            dbapi_connection.close()
            raise TargetRefused("La conexión no apunta a la rama NON_PRODUCTION autorizada")


def redact(message: str, url: URL) -> str:
    for part in (url.host, url.username, url.password, url.database, url.render_as_string(hide_password=False)):
        if part and len(str(part)) >= 3:
            message = message.replace(str(part), "***")
    return re.sub(r"\b[a-z0-9-]+\.(?:[a-z0-9-]+\.)*neon\.tech\b", "***", message)


def load_target_app(spec: TargetSpec) -> Engine:
    if "app.db.database" in sys.modules:
        raise RuntimeError("Ejecuta el destino en un proceso Python nuevo")
    install_branch_guard(spec)
    os.environ.update(
        DATABASE_URL=spec.url.render_as_string(hide_password=False),
        API_FOOTBALL_KEY="", FIVE_DOLLAR_FOOTBALL_API_KEY="",
        DB_CONNECT_TIMEOUT_SECONDS="15", DB_STATEMENT_TIMEOUT_MS="120000",
    )
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.db.database import engine

    if engine.url != spec.url:
        raise TargetRefused("El engine de la app no usa el destino autorizado")
    with engine.connect():  # dispara la guarda antes de cualquier otra cosa
        pass
    return engine


def maint_engine(engine: Engine) -> Engine:
    return create_engine(engine.url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 15})


def q(conn, sql: str, params=None):
    return conn.exec_driver_sql(sql, params or {}).scalar_one()


def lsn(conn) -> int | None:
    try:
        return q(conn, "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), '0/0')::bigint")
    except Exception:  # noqa: BLE001  función no disponible en el destino: WAL no medible
        return None


SETTINGS = (
    "server_version", "fsync", "synchronous_commit", "full_page_writes", "wal_compression", "shared_buffers", "work_mem",
    "max_wal_size", "checkpoint_timeout", "autovacuum", "autovacuum_naptime", "autovacuum_max_workers",
    "autovacuum_vacuum_threshold", "autovacuum_vacuum_scale_factor", "autovacuum_vacuum_insert_threshold",
    "autovacuum_vacuum_insert_scale_factor", "autovacuum_analyze_threshold", "autovacuum_analyze_scale_factor",
    "autovacuum_vacuum_cost_limit", "autovacuum_vacuum_cost_delay", "jit", "max_connections",
    "default_transaction_isolation",
)


def server_facts(conn) -> dict:
    facts = {"settings": {n: q(conn, f"SELECT current_setting('{n}', true)") for n in SETTINGS}}
    facts["ssl_in_use"] = q(conn, "SELECT coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()), false)")
    facts["fixtures_reloptions"] = q(conn, "SELECT coalesce(reloptions::text, 'default') FROM pg_class WHERE oid = 'fixtures'::regclass")
    facts["autovacuum_table_options"] = q(
        conn, "SELECT coalesce(string_agg(relname || ':' || reloptions::text, '; '), 'none') FROM pg_class "
              "WHERE relname IN ('fixtures', 'fixture_observations') AND reloptions IS NOT NULL")
    return facts


def round_trip(conn, n: int = 30) -> dict:
    samples = []
    for _ in range(n):
        started = time.perf_counter()
        conn.exec_driver_sql("SELECT 1").all()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return {"n": n, "min": samples[0], "p50": statistics.median(samples), "p95": samples[int(0.95 * (n - 1))],
            "max": samples[-1]}


def client_facts(spec: TargetSpec) -> dict:
    import psycopg
    import sqlalchemy

    return {"os": platform.platform(), "python": sys.version.split()[0], "sqlalchemy": sqlalchemy.__version__,
            "psycopg": psycopg.__version__, "pooled_endpoint": "-pooler" in (spec.url.host or ""),
            "sslmode": spec.url.query.get("sslmode"), "target_classification": "NON_PRODUCTION",
            "neon_branch_id_verified_on_every_connection": True}


# --- identity (solo lectura) --------------------------------------------------------------------


def run_identity(engine: Engine, spec: TargetSpec, args) -> dict:
    # BEGIN READ ONLY por transacción (nada de SET de sesión: con un pooler en modo transacción se filtraría)
    maint = create_engine(engine.url, connect_args={"connect_timeout": 15}, execution_options={"postgresql_readonly": True})
    with maint.connect() as conn:
        facts = {
            "transaction_read_only": q(conn, "SHOW transaction_read_only"),
            "neon_branch_id_matches": q(conn, "SELECT current_setting('neon.branch_id', true)") == spec.branch_id,
            "in_recovery": q(conn, "SELECT pg_is_in_recovery()"),
            "server_version": q(conn, "SHOW server_version"),
            "alembic_version": q(conn, "SELECT version_num FROM alembic_version"),
            "fixtures": q(conn, "SELECT count(*) FROM fixtures"),
            "fixture_provider_mappings": q(conn, "SELECT count(*) FROM fixture_provider_mappings"),
            "fixture_observations_present": q(conn, "SELECT count(*) FROM pg_class WHERE relname = 'fixture_observations'") > 0,
            "lab_range_rows": lab_range_rows(conn),
            "server": server_facts(conn),
            "select1_round_trip_ms": round_trip(conn),
        }
        facts["wal_lsn_readable"] = lsn(conn) is not None  # el último: si falla, aborta solo esta transacción
        conn.rollback()
    maint.dispose()
    checks = {
        "read_only_session": facts["transaction_read_only"] == "on",
        "branch": facts["neon_branch_id_matches"] and not facts["in_recovery"],
        "revision": facts["alembic_version"] == args.expect_revision,
        "fixtures": facts["fixtures"] == args.expect_fixtures,
        "postgres": facts["server_version"].split()[0] == args.expect_postgres,
        "observations_absent": not facts["fixture_observations_present"] if args.expect_revision == "0007" else True,
    }
    return {"status": "VERIFIED" if all(checks.values()) else "TARGET_IDENTITY_MISMATCH", "checks": checks,
            "facts": facts, "client": client_facts(spec), "created_at": datetime.now(timezone.utc).isoformat()}


def lab_range_rows(conn) -> dict:
    """Filas en los rangos reservados del laboratorio (deben ser 0 antes de sembrar)."""
    return {
        "fixtures": q(conn, "SELECT count(*) FROM fixtures WHERE external_id >= %(b)s", {"b": FIXTURE_EXTERNAL_BASE}),
        "teams": q(conn, "SELECT count(*) FROM teams WHERE external_id BETWEEN %(a)s AND %(b)s",
                   {"a": TEAM_EXTERNAL_BASE, "b": FIXTURE_EXTERNAL_BASE - 1}),
        "competitions": q(conn, "SELECT count(*) FROM competitions WHERE external_id BETWEEN %(a)s AND %(b)s",
                          {"a": COMPETITION_EXTERNAL_BASE, "b": TEAM_EXTERNAL_BASE - 1}),
    }


# --- upgrade 0007 → 0008 ------------------------------------------------------------------------


class StatementClock:
    """Duración de cada sentencia de la conexión de Alembic (texto de la migración, sin parámetros)."""

    def __init__(self):
        self.rows: list[dict] = []
        self.enabled = False
        self.lock = threading.Lock()

    def before(self, conn, cursor, statement, parameters, context, executemany):
        conn.info["g2_started"] = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        started = conn.info.pop("g2_started", None)
        if self.enabled and started is not None and not threading.current_thread().name.startswith("g2-probe"):
            with self.lock:
                self.rows.append({"sql": " ".join(statement.split())[:90], "ms": (time.perf_counter() - started) * 1000})


def post_migration_checks(conn) -> dict:
    return {
        "alembic_version": q(conn, "SELECT version_num FROM alembic_version"),
        "fixtures": q(conn, "SELECT count(*) FROM fixtures"),
        "observations": q(conn, "SELECT count(*) FROM fixture_observations"),
        "bootstrap_observations": q(conn, "SELECT count(*) FROM fixture_observations WHERE source = 'bootstrap'"),
        "bootstrap_evidence_ids": q(conn, "SELECT count(DISTINCT evidence_id) FROM fixture_observations WHERE source = 'bootstrap'"),
        "fixtures_without_bootstrap_observation": q(conn,
            "SELECT count(*) FROM fixtures f WHERE NOT EXISTS (SELECT 1 FROM fixture_observations o "
            "WHERE o.fixture_id = f.id AND o.source = 'bootstrap')"),
        "duplicate_bootstrap_per_fixture": q(conn,
            "SELECT count(*) FROM (SELECT fixture_id FROM fixture_observations WHERE source = 'bootstrap' "
            "GROUP BY fixture_id HAVING count(*) > 1) x"),
        "winner_not_its_bootstrap_observation": q(conn,
            "SELECT count(*) FROM fixtures f WHERE NOT EXISTS (SELECT 1 FROM fixture_observations o WHERE o.fixture_id = f.id "
            "AND o.observed_at = f.last_observed_at AND o.state_hash = f.last_state_hash)"),
        "orphan_observations": q(conn,
            "SELECT count(*) FROM fixture_observations o LEFT JOIN fixtures f ON f.id = o.fixture_id WHERE f.id IS NULL"),
        "bad_hash_length": q(conn, "SELECT count(*) FROM fixture_observations WHERE octet_length(state_hash) <> 32"),
        "null_order_columns": q(conn, "SELECT count(*) FROM fixtures WHERE last_observed_at IS NULL OR last_state_hash IS NULL"),
    }


def run_upgrade(engine: Engine, spec: TargetSpec, args) -> dict:
    from alembic import command
    from alembic.config import Config

    from tools.perf_lab.benchmark import BACKEND, environment
    from tools.perf_lab.g2 import ReadProbe, alembic_version, relation_sizes, size_delta

    maint = maint_engine(engine)
    with maint.connect() as m:
        before_version = alembic_version(m)
        if before_version != "0007":
            raise TargetRefused(f"El ensayo exige 0007 (hay {before_version})")
        if q(m, "SELECT count(*) FROM pg_class WHERE relname = 'fixture_observations'"):
            raise TargetRefused("fixture_observations ya existe")
        fixtures_before = q(m, "SELECT count(*) FROM fixtures")
        sizes_before = relation_sizes(m)
        lsn_before = lsn(m)
    clock = StatementClock()
    event.listen(Engine, "before_cursor_execute", clock.before)
    event.listen(Engine, "after_cursor_execute", clock.after)
    probe = ReadProbe(engine.url)
    probe.name = "g2-probe"
    probe.start()
    time.sleep(1.0)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["configure_logger"] = False
    error = None
    clock.enabled = True
    started = time.perf_counter()
    try:
        command.upgrade(cfg, "0008")
    except Exception as exc:  # noqa: BLE001  se registra (redactado) y se informa; la migración se deshace sola
        error = f"{exc.__class__.__name__}: {redact(str(exc), spec.url)[:300]}"
    elapsed = time.perf_counter() - started
    clock.enabled = False
    time.sleep(1.0)
    probe.stop.set()
    probe.join()
    with maint.connect() as m:
        lsn_after = lsn(m)
        sizes_after = relation_sizes(m)
        checks = post_migration_checks(m) if alembic_version(m) == "0008" else {"alembic_version": alembic_version(m)}
    maint.dispose()
    bootstrap = [r for r in clock.rows if re.search(r"fixture_observations \(evidence_id|UPDATE fixtures SET last_observed_at|"
                                                       r"bootstrap|LOCK TABLE fixtures", r["sql"], re.I)]
    passed = (error is None and checks.get("alembic_version") == "0008" and checks["fixtures"] == fixtures_before
              and checks["observations"] == fixtures_before == checks["bootstrap_observations"]
              and checks["bootstrap_evidence_ids"] == 1
              and all(checks[k] == 0 for k in ("fixtures_without_bootstrap_observation", "duplicate_bootstrap_per_fixture",
                                               "winner_not_its_bootstrap_observation", "orphan_observations",
                                               "bad_hash_length", "null_order_columns")))
    return {
        "status": "MEDIDO" if passed else "FALLIDO", "error": error, "created_at": datetime.now(timezone.utc).isoformat(),
        "from_revision": before_version, "to_revision": checks.get("alembic_version"), "fixtures_before": fixtures_before,
        "migration_wall_seconds": elapsed,
        "statements": clock.rows, "bootstrap_statements": bootstrap,
        "bootstrap_statements_seconds": sum(r["ms"] for r in bootstrap) / 1000,
        "wal_bytes": (lsn_after - lsn_before) if None not in (lsn_before, lsn_after) else None,
        "relation_sizes_before": sizes_before, "relation_sizes_after": sizes_after,
        "relation_size_delta": size_delta(sizes_before, sizes_after),
        "read_probe": probe.summary(), "checks": checks,
        "vacuum_after_migration": "NONE (autovacuum only)",
        "environment": environment(engine), "client": client_facts(spec),
        "note": "alembic upgrade 0008 = UNA transacción (DDL + bootstrap + índices): ACCESS EXCLUSIVE sobre fixtures hasta el COMMIT",
    }


# --- verify (invariantes A6, solo recuentos) ----------------------------------------------------


def run_verify(engine: Engine, spec: TargetSpec, args) -> dict:
    maint = maint_engine(engine)
    with maint.connect() as m:
        lab = {"b": FIXTURE_EXTERNAL_BASE}
        result = {
            "alembic_version": q(m, "SELECT version_num FROM alembic_version"),
            "fixtures_real": q(m, "SELECT count(*) FROM fixtures WHERE external_id < %(b)s", lab),
            "fixtures_lab": q(m, "SELECT count(*) FROM fixtures WHERE external_id >= %(b)s", lab),
            "observations_by_source": dict(m.exec_driver_sql(
                "SELECT source, count(*) FROM fixture_observations GROUP BY source ORDER BY source").all()),
            "real_observations_non_bootstrap": q(m,
                "SELECT count(*) FROM fixture_observations o JOIN fixtures f ON f.id = o.fixture_id "
                "WHERE f.external_id < %(b)s AND o.source <> 'bootstrap'", lab),
            "winner_not_an_observation": q(m,
                "SELECT count(*) FROM fixtures f WHERE NOT EXISTS (SELECT 1 FROM fixture_observations o WHERE o.fixture_id = f.id "
                "AND o.observed_at = f.last_observed_at AND o.state_hash = f.last_state_hash)"),
            "newer_observation_than_winner": q(m,
                "SELECT count(*) FROM fixtures f WHERE EXISTS (SELECT 1 FROM fixture_observations o WHERE o.fixture_id = f.id "
                "AND (o.observed_at, o.state_hash) > (f.last_observed_at, f.last_state_hash))"),
            "duplicate_fixture_evidence": q(m,
                "SELECT count(*) FROM (SELECT fixture_id, evidence_id FROM fixture_observations GROUP BY 1, 2 HAVING count(*) > 1) x"),
            "unique_fixture_evidence_constraint": q(m,
                "SELECT count(*) FROM pg_constraint WHERE conrelid = 'fixture_observations'::regclass AND contype = 'u'"),
            "evidence_spanning_seasons": q(m,
                "SELECT count(*) FROM (SELECT evidence_id FROM fixture_observations GROUP BY 1 HAVING count(DISTINCT season_id) > 1) x"),
            "max_rows_per_sync_evidence": q(m,
                "SELECT coalesce(max(c), 0) FROM (SELECT count(*) c FROM fixture_observations WHERE source = 'sync' GROUP BY evidence_id) x"),
            "orphan_observations": q(m,
                "SELECT count(*) FROM fixture_observations o LEFT JOIN fixtures f ON f.id = o.fixture_id WHERE f.id IS NULL"),
            "real_fixtures_without_mapping_informative": q(m,
                "SELECT count(*) FROM fixtures f WHERE f.external_id < %(b)s AND NOT EXISTS "
                "(SELECT 1 FROM fixture_provider_mappings p WHERE p.fixture_id = f.id)", lab),
            "lab_fixtures_without_api_football_mapping": q(m,
                "SELECT count(*) FROM fixtures f WHERE f.external_id >= %(b)s AND NOT EXISTS (SELECT 1 FROM fixture_provider_mappings p "
                "WHERE p.fixture_id = f.id AND p.provider = 'api-football' AND p.external_id = f.external_id::text)", lab),
            "bad_hash_length": q(m, "SELECT count(*) FROM fixture_observations WHERE octet_length(state_hash) <> 32"),
            "deadlocks_total": q(m, "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()"),
        }
        if args.rollback_probe:
            result["rollback_probe"] = rollback_probe(engine, m)
    maint.dispose()
    zero = ("winner_not_an_observation", "newer_observation_than_winner", "duplicate_fixture_evidence", "orphan_observations",
            "lab_fixtures_without_api_football_mapping", "bad_hash_length",
            "real_observations_non_bootstrap")
    ok = all(result[k] == 0 for k in zero) and result["evidence_spanning_seasons"] <= 1 \
        and result.get("rollback_probe", {}).get("atomic", True)
    return {"status": "PASS" if ok else "FAIL", "created_at": datetime.now(timezone.utc).isoformat(), **result}


def lab_fingerprint(m) -> dict:
    return dict(m.exec_driver_sql(
        "SELECT (SELECT count(*) FROM fixture_observations o JOIN fixtures f ON f.id = o.fixture_id "
        "WHERE f.external_id >= %(b)s) AS lab_observations, "
        "(SELECT md5(string_agg(encode(last_state_hash, 'hex') || last_observed_at::text, ',' ORDER BY id)) "
        " FROM fixtures WHERE external_id >= %(b)s) AS lab_state", {"b": FIXTURE_EXTERNAL_BASE}).mappings().one())


def rollback_probe(engine: Engine, m) -> dict:
    """Atomicidad sobre filas del laboratorio: el escritor real escribe un lote y la transacción se deshace."""
    from sqlalchemy.orm import Session

    from tools.perf_lab import a6

    season_map = lab_season_map(m)
    before = lab_fingerprint(m)
    groups = remap(a6.groups_for(DatasetConfig(LAB_FIXTURES, LAB_SEED), 380, "update", 7), season_map)
    with Session(engine) as db:
        totals = a6.write_groups(db, groups, a6._evidences(groups, a6.EvidenceClock.newer))
        db.rollback()
    after = lab_fingerprint(m)
    return {"written_before_rollback": totals, "before": before, "after": after, "atomic": before == after}


# --- dataset sintético aislado ------------------------------------------------------------------


def lab_season_map(conn) -> dict[int, int]:
    """Temporada sintética k (1..10) → id real de la temporada del laboratorio, por año (determinista)."""
    config = DatasetConfig(LAB_FIXTURES, LAB_SEED)
    rows = dict(conn.exec_driver_sql(
        "SELECT s.year, s.id FROM seasons s JOIN competitions c ON c.id = s.competition_id WHERE c.external_id = %(e)s",
        {"e": LAB_COMPETITION_EXTERNAL_ID}).all())
    mapping = {sid: rows.get(config.year(sid)) for sid in range(1, config.seasons + 1)}
    if None in mapping.values() or len(rows) != config.seasons:
        raise TargetRefused("Las temporadas del laboratorio no están completas: ejecuta seed")
    return mapping


def remap(groups: dict, season_map: dict[int, int]) -> dict:
    return {season_map[sid]: fixtures for sid, fixtures in groups.items()}


def run_seed(engine: Engine, spec: TargetSpec, args) -> dict:
    from sqlalchemy.orm import Session

    from tools.perf_lab import a6
    from tools.perf_lab.g2 import alembic_version

    config = DatasetConfig(LAB_FIXTURES, LAB_SEED)
    maint = maint_engine(engine)
    with maint.connect() as m:
        if alembic_version(m) != "0008":
            raise TargetRefused("El dataset sintético se siembra después del ensayo de 0008")
        occupied = lab_range_rows(m)
        if any(occupied.values()):
            raise TargetRefused(f"Los rangos reservados del laboratorio no están vacíos: {occupied}")
    started = time.perf_counter()
    with engine.begin() as conn:
        competition_id = q(conn,
            "INSERT INTO competitions (external_id, name, type, country) VALUES (%(e)s, %(n)s, 'League', 'Laboratorio') RETURNING id",
            {"e": LAB_COMPETITION_EXTERNAL_ID, "n": LAB_COMPETITION_NAME})
        conn.exec_driver_sql(
            "INSERT INTO competition_provider_mappings (competition_id, provider, external_id, match_method, confidence, last_seen_at) "
            "VALUES (%(c)s, 'api-football', %(e)s, 'origin', 1, %(t)s)",
            {"c": competition_id, "e": str(LAB_COMPETITION_EXTERNAL_ID), "t": AS_OF})
        for sid in range(1, config.seasons + 1):
            year = config.year(sid)
            conn.exec_driver_sql(
                "INSERT INTO seasons (competition_id, year, start_date, end_date, is_current) VALUES (%(c)s, %(y)s, %(s)s, %(e)s, %(cur)s)",
                {"c": competition_id, "y": year, "s": date(year, 8, 1), "e": date(year + 1, 5, 31), "cur": sid == config.seasons})
    with maint.connect() as m:
        season_map = lab_season_map(m)
    # Equipos, partidos, mappings y la observación inicial: por el escritor real, una respuesta por temporada
    clock = a6.EvidenceClock(None)
    counts = defaultdict(int)
    for sid in range(1, config.seasons + 1):
        groups = remap({sid: [a6._fixture_data(config, i)[1] for i in range((sid - 1) * FIXTURES_PER_SEASON,
                                                                               sid * FIXTURES_PER_SEASON)]}, season_map)
        with Session(engine) as db:
            for key, value in a6.write_groups(db, groups, a6._evidences(groups, clock.newer)).items():
                counts[key] += value
            db.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO season_teams (season_id, team_id) SELECT s.id, t.id FROM seasons s CROSS JOIN teams t "
            "WHERE s.id = ANY(%(s)s) AND t.external_id BETWEEN %(a)s AND %(b)s ON CONFLICT DO NOTHING",
            {"s": list(season_map.values()), "a": TEAM_EXTERNAL_BASE + 1, "b": TEAM_EXTERNAL_BASE + config.teams})
    with maint.connect() as m:
        rows = lab_range_rows(m)
        observations = q(m, "SELECT count(*) FROM fixture_observations o JOIN fixtures f ON f.id = o.fixture_id "
                            "WHERE f.external_id >= %(b)s", {"b": FIXTURE_EXTERNAL_BASE})
    maint.dispose()
    assert rows == {"fixtures": config.fixtures, "teams": config.teams, "competitions": 1}, rows
    assert counts["created"] == config.fixtures == observations, (counts, observations)
    return {"status": "SEMBRADO", "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {"fixtures": config.fixtures, "seed": config.seed, "seasons": config.seasons, "teams": config.teams,
                        "competitions": 1, "as_of": AS_OF.isoformat()},
            "ranges": {"fixture_external_id": f">= {FIXTURE_EXTERNAL_BASE} (inserts temporales >= {a6.INSERT_OFFSET})",
                       "team_external_id": f"{TEAM_EXTERNAL_BASE + 1}..{TEAM_EXTERNAL_BASE + config.teams}",
                       "competition_external_id": LAB_COMPETITION_EXTERNAL_ID},
            "writer_counts": dict(counts), "lab_rows": rows, "lab_observations": observations,
            "seconds": time.perf_counter() - started,
            "method": "competición y temporadas por INSERT; equipos, partidos, mappings y observaciones por el escritor real "
                      "(ensure_teams + upsert_fixtures con evidencia), una respuesta por temporada"}


# --- escritor -----------------------------------------------------------------------------------


def run_writes(engine: Engine, spec: TargetSpec, args) -> dict:
    from tools.perf_lab import a6
    from tools.perf_lab.benchmark import environment
    from tools.perf_lab.g2 import alembic_version, counters_delta, db_counters, relation_sizes, size_delta

    config = DatasetConfig(LAB_FIXTURES, LAB_SEED)
    maint = maint_engine(engine)
    with maint.connect() as m:
        if alembic_version(m) != "0008":
            raise TargetRefused("El escritor A6 exige 0008")
        season_map = lab_season_map(m)
        if lab_range_rows(m)["fixtures"] != config.fixtures:
            raise TargetRefused("El dataset sintético no está en su estado base")
        sizes_before = relation_sizes(m)
        counters_before = db_counters(m)
        facts = server_facts(m)
        rtt_before = round_trip(m)
    original_groups_for, original_analyze = a6.groups_for, a6.analyze
    a6.groups_for = lambda cfg, batch, mode, variant: remap(original_groups_for(cfg, batch, mode, variant), season_map)
    a6.analyze = lambda _engine: None  # sin VACUUM forzado: HOT y estado de páginas bajo autovacuum
    env_before = environment(engine)
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    try:
        results = a6.measure_writes(engine, config, args.batches, args.modes, args.samples, args.warmup)
    finally:
        a6.groups_for, a6.analyze = original_groups_for, original_analyze
    elapsed = time.perf_counter() - started
    with maint.connect() as m:
        counters_after = db_counters(m)
        sizes_after = relation_sizes(m)
        version_after = alembic_version(m)
        rtt_after = round_trip(m)
    maint.dispose()
    env_after = environment(engine)
    delta = counters_delta(counters_before, counters_after)
    fixtures_delta = delta["tables"].get("fixtures", {})
    return {
        "status": "MEDIDO", "created_at": datetime.now(timezone.utc).isoformat(), "label": args.label,
        "dataset": {"fixtures": config.fixtures, "seed": config.seed, "synthetic_isolated": True},
        "alembic_version": ["0008", version_after], "environment": env_before, "client": client_facts(spec),
        "server": facts, "select1_round_trip_ms": {"before": rtt_before, "after": rtt_after},
        "source_unchanged_during_run": env_before["source_sha256"] == env_after["source_sha256"],
        "methodology": {
            "harness": "tools.perf_lab.a6.measure_writes (escritor real UNNEST, un COMMIT por lote) sobre el dataset sintético",
            "samples": args.samples, "warmup": args.warmup, "batches": args.batches, "modes": args.modes,
            "forced_vacuum": False,
            "hot_source": "pg_stat_xact_user_tables por muestra (fixtures_hot_ratio) y pg_stat_user_tables por pase",
            "percentiles": "interpolación lineal (n-1)*p sobre las muestras medidas (sin descartar ninguna)",
        },
        "measurement_interval": {"start": started_at.isoformat(), "end": datetime.now(timezone.utc).isoformat()},
        "pass_seconds": elapsed,
        "pass_counters_delta": delta,
        "pass_hot": {"n_tup_upd": fixtures_delta.get("n_tup_upd"), "n_tup_hot_upd": fixtures_delta.get("n_tup_hot_upd"),
                     "ratio": (fixtures_delta["n_tup_hot_upd"] / fixtures_delta["n_tup_upd"]) if fixtures_delta.get("n_tup_upd") else None,
                     "autovacuum_runs": {t: d.get("autovacuum_count") for t, d in delta["tables"].items()},
                     "manual_vacuum_runs": {t: d.get("vacuum_count") for t, d in delta["tables"].items()}},
        "pass_relation_sizes_before": sizes_before, "pass_relation_sizes_after": sizes_after,
        "pass_relation_size_delta": size_delta(sizes_before, sizes_after),
        "results": results,
    }


COMMANDS = {"identity": run_identity, "upgrade": run_upgrade, "verify": run_verify, "seed": run_seed, "writes": run_writes}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.perf_lab.g2_target")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
    ident = sub.choices["identity"]
    ident.add_argument("--expect-revision", required=True)
    ident.add_argument("--expect-fixtures", type=int, required=True)
    ident.add_argument("--expect-postgres", default="18.6")
    sub.choices["verify"].add_argument("--rollback-probe", action="store_true")
    wr = sub.choices["writes"]
    wr.add_argument("--label", required=True)
    wr.add_argument("--batches", type=int, nargs="+", default=[380, 1000, 2000])
    wr.add_argument("--modes", nargs="+", default=["insert", "update", "unchanged", "older", "mixed", "tie", "replay"])
    wr.add_argument("--samples", type=int, default=30)
    wr.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("El archivo de salida ya existe; elige otro nombre")
    spec = authorize_target(os.environ)
    try:
        engine = load_target_app(spec)
        try:
            report = COMMANDS[args.command](engine, spec, args)
        finally:
            engine.dispose()
    except TargetRefused:
        raise
    except Exception as exc:  # noqa: BLE001  nunca se propaga un mensaje con datos de conexión
        raise RuntimeError(f"{exc.__class__.__name__}: {redact(str(exc), spec.url)[:400]}") from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"{args.command}: {report.get('status')} → {args.output.name}")
    return 0 if report.get("status") in ("VERIFIED", "MEDIDO", "PASS", "SEMBRADO") else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TargetRefused as exc:
        print(f"TARGET_REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(4)
    except (ValueError, RuntimeError) as exc:
        print(f"Destino bloqueado: {exc}", file=sys.stderr)
        raise SystemExit(2)
