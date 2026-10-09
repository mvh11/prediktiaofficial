"""DI-A6: ensayo operativo NO productivo de `0007 → 0008` (solo laboratorio).

Reutiliza la guarda fail-closed de g2_target: cada conexión nueva de cualquier engine del proceso
comprueba `neon.branch_id` contra REHEARSAL_NEON_BRANCH_ID. La URL llega solo por entorno
(DI_A6_0008_REHEARSAL_DATABASE_URL) y nunca se imprime ni se guarda.

    python -m tools.perf_lab.rehearsal_0008 preflight --expect-fixtures 18671 --output <json>
    python -m tools.perf_lab.rehearsal_0008 migrate --output <json>     # `alembic upgrade 0008` (punto de entrada del CLI)
    python -m tools.perf_lab.rehearsal_0008 validate --output <json>    # bootstrap e integridad (agregados)
    python -m tools.perf_lab.rehearsal_0008 smoke --output <json>       # UNA prueba del escritor con datos propios del ensayo

Solo recuentos, tamaños, metadatos y tiempos: nunca valores de filas reales.
"""

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text

from tools.perf_lab import g2_target as t

URL_ENV = "DI_A6_0008_REHEARSAL_DATABASE_URL"
BRANCH_ENV = "REHEARSAL_NEON_BRANCH_ID"
BACKEND = Path(__file__).resolve().parents[2]
SMOKE_COMPETITION_EXTERNAL_ID = 500_801  # rango reservado del laboratorio (500 000–999 999), distinto del de G2
SMOKE_TEAM_BASE = 1_080_000
SMOKE_FIXTURE_BASE = 10_800_000
SMOKE_FIXTURES = 20
RUN_TABLES = ("live_sync_runs", "season_backfill_runs", "statistics_runs")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migration_module():
    path = BACKEND / "alembic" / "versions" / "0008_fixture_observations.py"
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # solo constantes; upgrade() no se ejecuta aquí
    return module


def migration_graph() -> dict:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    revisions = sorted(r.revision for r in script.walk_revisions())
    path = [r.revision for r in script.iterate_revisions("0008", "0007")]
    return {"heads": list(script.get_heads()), "revisions": revisions, "has_0009": "0009" in revisions,
            "upgrade_path_0007_to_0008": path, "0008_down_revision": script.get_revision("0008").down_revision}


# --- quiescencia ----------------------------------------------------------------------------------


def quiescence(conn) -> dict:
    """Actividad de OTRAS sesiones y runs abiertos. Solo recuentos; no se toca ninguna sesión."""
    q = lambda sql: conn.exec_driver_sql(sql).scalar_one()  # noqa: E731
    activity = dict(conn.exec_driver_sql(
        "SELECT coalesce(state, 'unknown'), count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend' GROUP BY 1").all())
    facts = {
        "other_sessions_by_state": activity,
        "other_sessions_in_transaction": q(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND backend_type = 'client backend' AND xact_start IS NOT NULL"),
        "other_sessions_with_write_xid": q(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND backend_xid IS NOT NULL"),
        "other_locks_on_fixtures": q(
            "SELECT count(*) FROM pg_locks WHERE relation = 'fixtures'::regclass AND pid <> pg_backend_pid()"),
        "prepared_transactions": q("SELECT count(*) FROM pg_prepared_xacts WHERE database = current_database()"),
        "running_runs": {table: q(f"SELECT count(*) FROM {table} WHERE status = 'running'") for table in RUN_TABLES},
    }
    facts["quiescent"] = (facts["other_sessions_in_transaction"] == 0 and facts["other_sessions_with_write_xid"] == 0
                          and facts["other_locks_on_fixtures"] == 0 and facts["prepared_transactions"] == 0
                          and not any(facts["running_runs"].values()))
    return facts


def baseline(conn) -> dict:
    from tools.perf_lab.g2 import relation_sizes

    q = lambda sql: conn.exec_driver_sql(sql).scalar_one()  # noqa: E731
    return {
        "alembic_version": q("SELECT version_num FROM alembic_version"),
        "fixtures": q("SELECT count(*) FROM fixtures"),
        "fixture_provider_mappings": q("SELECT count(*) FROM fixture_provider_mappings"),
        "fixtures_without_mapping": q(
            "SELECT count(*) FROM fixtures f WHERE NOT EXISTS (SELECT 1 FROM fixture_provider_mappings m WHERE m.fixture_id = f.id)"),
        "fixture_observations_present": q("SELECT count(*) FROM pg_class WHERE relname = 'fixture_observations'") > 0,
        "relation_sizes": relation_sizes(conn),
    }


def writable_probe(engine: Engine) -> dict:
    """La conexión autorizada puede ejecutar DDL transaccional: CREATE TABLE dentro de una transacción
    que se deshace siempre (no queda nada). No se toca ninguna tabla existente ni ningún ajuste."""
    with engine.connect() as conn:
        tx = conn.begin()
        read_only = conn.exec_driver_sql("SHOW transaction_read_only").scalar_one()
        default_read_only = conn.exec_driver_sql("SHOW default_transaction_read_only").scalar_one()
        try:
            conn.exec_driver_sql("CREATE TABLE rehearsal_0008_write_probe (x integer)")
            ddl_ok = True
        except Exception as exc:  # noqa: BLE001
            ddl_ok = False
            error = exc.__class__.__name__
        finally:
            tx.rollback()
        left = conn.exec_driver_sql("SELECT to_regclass('rehearsal_0008_write_probe') IS NOT NULL").scalar_one()
        owner_ok = conn.exec_driver_sql(
            "SELECT pg_has_role(current_user, (SELECT relowner FROM pg_class WHERE oid = 'fixtures'::regclass), 'USAGE')").scalar_one()
    return {"transaction_read_only": read_only, "default_transaction_read_only": default_read_only,
            "transactional_ddl": ddl_ok, "probe_left_behind": left, "can_alter_fixtures": owner_ok,
            **({"error_class": error} if not ddl_ok else {})}


# --- comandos -------------------------------------------------------------------------------------


def run_preflight(engine: Engine, spec, args) -> dict:
    identity = t.run_identity(engine, spec, argparse.Namespace(
        expect_revision="0007", expect_fixtures=args.expect_fixtures, expect_postgres="18.6"))
    maint = t.maint_engine(engine)
    with maint.connect() as m:
        base = baseline(m)
        quiet = quiescence(m)
    maint.dispose()
    graph = migration_graph()
    writable = writable_probe(engine)
    checks = {
        **{f"identity_{k}": v for k, v in identity["checks"].items()},
        "pooled_ssl_client": identity["client"]["pooled_endpoint"] and identity["client"]["sslmode"] == "require",
        "single_head_0008": graph["heads"] == ["0008"] and not graph["has_0009"],
        "upgrade_path_only_0008": graph["upgrade_path_0007_to_0008"] == ["0008"],
        "writable": writable["transaction_read_only"] == "off" and writable["transactional_ddl"]
                    and not writable["probe_left_behind"] and writable["can_alter_fixtures"],
        "lab_ranges_empty": not any(identity["facts"]["lab_range_rows"].values()),
        "quiescent": quiet["quiescent"],
    }
    status = "PASS" if all(checks.values()) else ("TARGET_NOT_WRITABLE" if not checks["writable"] and all(
        v for k, v in checks.items() if k != "writable") else "BLOCKED")
    return {"status": status, "created_at": now(), "checks": checks, "identity": identity, "baseline": base,
            "quiescence": quiet, "migration_graph": graph, "writable_probe": writable}


def run_migrate(engine: Engine, spec, args) -> dict:
    """El comando operativo: `alembic upgrade 0008` desde backend/ (alembic.config.main, el punto de entrada del CLI),
    con la sonda de lectura y el reloj por sentencia de g2_target. Sin reintentos."""
    from alembic.config import main as alembic_cli

    from tools.perf_lab.g2 import ReadProbe, relation_sizes, size_delta

    graph = migration_graph()
    if graph["heads"] != ["0008"] or graph["has_0009"] or graph["upgrade_path_0007_to_0008"] != ["0008"]:
        raise t.TargetRefused(f"Grafo de migraciones inesperado: {graph}")
    maint = t.maint_engine(engine)
    with maint.connect() as m:
        before = baseline(m)
        quiet = quiescence(m)
        lsn_before = t.lsn(m)
    if before["alembic_version"] != "0007" or before["fixture_observations_present"]:
        raise t.TargetRefused("El ensayo exige 0007 sin fixture_observations")
    if not quiet["quiescent"]:
        raise t.TargetRefused(f"ABORT: el destino no está en reposo: {quiet}")
    clock = t.StatementClock()
    event.listen(Engine, "before_cursor_execute", clock.before)
    event.listen(Engine, "after_cursor_execute", clock.after)
    probe = ReadProbe(engine.url)
    probe.name = "g2-probe"
    probe.start()
    time.sleep(1.0)
    started_at = now()
    error = None
    clock.enabled = True
    started = time.perf_counter()
    cwd = os.getcwd()
    try:
        os.chdir(BACKEND)
        alembic_cli(argv=["-c", "alembic.ini", "upgrade", "0008"])
    except SystemExit as exc:  # el CLI sale con código distinto de 0 si falla
        if exc.code not in (0, None):
            error = f"SystemExit({exc.code})"
    except Exception as exc:  # noqa: BLE001  se clasifica; la transacción de la migración se deshace sola
        error = f"{exc.__class__.__name__}: {t.redact(str(exc), spec.url)[:300]}"
    finally:
        os.chdir(cwd)
    elapsed = time.perf_counter() - started
    finished_at = now()
    clock.enabled = False
    time.sleep(1.0)
    probe.stop.set()
    probe.join()
    with maint.connect() as m:
        after = baseline(m)
        lsn_after = t.lsn(m)
        checks = t.post_migration_checks(m) if after["alembic_version"] == "0008" else {}
    maint.dispose()
    if error is None and after["alembic_version"] == "0008":
        state = "COMMITTED_0008"
    elif after["alembic_version"] == "0007" and not after["fixture_observations_present"]:
        state = "ROLLED_BACK_AT_0007"
    else:
        state = "AMBIGUOUS"
    bootstrap = [r for r in clock.rows if any(s in r["sql"] for s in (
        "LOCK TABLE fixtures", "INSERT INTO fixture_observations (evidence_id", "UPDATE fixtures SET last_observed_at"))]
    return {
        "status": "MIGRATED" if state == "COMMITTED_0008" else state, "error": error, "schema_state": state,
        "command": "cd backend && alembic -c alembic.ini upgrade 0008  (con DATABASE_URL del entorno)",
        "started_at": started_at, "finished_at": finished_at, "migration_wall_seconds": elapsed,
        "statements": clock.rows, "statements_seconds": sum(r["ms"] for r in clock.rows) / 1000,
        "bootstrap_statements": bootstrap, "bootstrap_seconds": sum(r["ms"] for r in bootstrap) / 1000,
        "read_probe": probe.summary(), "wal_bytes": (lsn_after - lsn_before) if None not in (lsn_before, lsn_after) else None,
        "before": before, "after": after, "relation_size_delta": size_delta(before["relation_sizes"], after["relation_sizes"]),
        "quiescence_before": quiet, "post_migration_checks": checks, "vacuum_after_migration": "NONE (autovacuum)",
        "migration_graph": graph,
    }


def run_validate(engine: Engine, spec, args) -> dict:
    hash_expr = migration_module().HASH_OF_FIXTURE_ROW
    maint = t.maint_engine(engine)
    with maint.connect() as m:
        q = lambda sql: m.exec_driver_sql(sql).scalar_one()  # noqa: E731
        bootstrap = t.post_migration_checks(m)
        bootstrap.update({
            "bootstrap_observed_at_distinct": q("SELECT count(DISTINCT observed_at) FROM fixture_observations WHERE source = 'bootstrap'"),
            "bootstrap_with_provider": q("SELECT count(*) FROM fixture_observations WHERE source = 'bootstrap' AND provider IS NOT NULL"),
            "last_observed_at_not_bootstrap_instant": q(
                "SELECT count(*) FROM fixtures f WHERE f.last_observed_at <> (SELECT max(observed_at) FROM fixture_observations "
                "WHERE source = 'bootstrap')"),
            "last_state_hash_not_recomputable": q(f"SELECT count(*) FROM fixtures WHERE last_state_hash <> {hash_expr}"),
            "observation_hash_differs_from_fixture": q(
                "SELECT count(*) FROM fixture_observations o JOIN fixtures f ON f.id = o.fixture_id "
                "WHERE o.source = 'bootstrap' AND o.state_hash <> f.last_state_hash"),
            "recorded_at_null": q("SELECT count(*) FROM fixture_observations WHERE recorded_at IS NULL"),
        })
        integrity = {
            "newer_observation_than_winner": q(
                "SELECT count(*) FROM fixtures f WHERE EXISTS (SELECT 1 FROM fixture_observations o WHERE o.fixture_id = f.id "
                "AND (o.observed_at, o.state_hash) > (f.last_observed_at, f.last_state_hash))"),
            "duplicate_fixture_evidence": q(
                "SELECT count(*) FROM (SELECT fixture_id, evidence_id FROM fixture_observations GROUP BY 1, 2 HAVING count(*) > 1) x"),
            "unique_fixture_evidence_constraint": q(
                "SELECT count(*) FROM pg_constraint WHERE conrelid = 'fixture_observations'::regclass AND contype = 'u'"),
            "fk_observation_fixture_restrict": q(
                "SELECT count(*) FROM pg_constraint WHERE conrelid = 'fixture_observations'::regclass AND contype = 'f' "
                "AND confrelid = 'fixtures'::regclass AND confdeltype = 'r'"),
            "fixtures_without_mapping": q(
                "SELECT count(*) FROM fixtures f WHERE NOT EXISTS (SELECT 1 FROM fixture_provider_mappings m WHERE m.fixture_id = f.id)"),
            "fixture_provider_mappings": q("SELECT count(*) FROM fixture_provider_mappings"),
            "mappings_without_fixture": q(
                "SELECT count(*) FROM fixture_provider_mappings m LEFT JOIN fixtures f ON f.id = m.fixture_id WHERE f.id IS NULL"),
        }
        quiet = quiescence(m)
    maint.dispose()
    zero_bootstrap = ("fixtures_without_bootstrap_observation", "duplicate_bootstrap_per_fixture", "winner_not_its_bootstrap_observation",
                      "orphan_observations", "bad_hash_length", "null_order_columns", "bootstrap_with_provider",
                      "last_observed_at_not_bootstrap_instant", "last_state_hash_not_recomputable",
                      "observation_hash_differs_from_fixture", "recorded_at_null")
    ok_bootstrap = (bootstrap["alembic_version"] == "0008" and bootstrap["fixtures"] == args.expect_fixtures
                    == bootstrap["observations"] == bootstrap["bootstrap_observations"] and bootstrap["bootstrap_evidence_ids"] == 1
                    and bootstrap["bootstrap_observed_at_distinct"] == 1 and all(bootstrap[k] == 0 for k in zero_bootstrap))
    ok_integrity = (all(integrity[k] == 0 for k in ("newer_observation_than_winner", "duplicate_fixture_evidence",
                                                     "mappings_without_fixture"))
                    and integrity["unique_fixture_evidence_constraint"] >= 1 and integrity["fk_observation_fixture_restrict"] >= 1
                    and integrity["fixtures_without_mapping"] == args.expect_unmapped
                    and integrity["fixture_provider_mappings"] == args.expect_mappings)
    return {"status": "PASS" if ok_bootstrap and ok_integrity else "FAIL", "created_at": now(),
            "bootstrap": bootstrap, "bootstrap_pass": ok_bootstrap, "integrity": integrity, "integrity_pass": ok_integrity,
            "quiescence": quiet}


def _smoke_fixtures(variant: int | None):
    from app.schemas.catalog import TeamData
    from app.schemas.fixture import FixtureData

    out = []
    for i in range(SMOKE_FIXTURES):
        home, away = SMOKE_TEAM_BASE + 1 + (2 * i) % 10, SMOKE_TEAM_BASE + 2 + (2 * i) % 10
        out.append(FixtureData(
            external_id=SMOKE_FIXTURE_BASE + i + 1, competition_external_id=SMOKE_COMPETITION_EXTERNAL_ID, season=2026,
            round=f"Ensayo - {i // 5 + 1}", kickoff_at=datetime(2026, 8, 1 + i, 15, tzinfo=timezone.utc),
            status_short="NS" if variant is None else "FT", status_long="Not Started" if variant is None else "Match Finished",
            elapsed=None if variant is None else 90, venue_name=f"Estadio ensayo {i}", venue_city="Ensayo", referee=None,
            home_team=TeamData(external_id=home, name=f"Equipo ensayo {home - SMOKE_TEAM_BASE}"),
            away_team=TeamData(external_id=away, name=f"Equipo ensayo {away - SMOKE_TEAM_BASE}"),
            home_goals=None if variant is None else variant % 4, away_goals=None if variant is None else (variant + 1) % 3,
            halftime_home=None, halftime_away=None, fulltime_home=None if variant is None else variant % 4,
            fulltime_away=None if variant is None else (variant + 1) % 3,
        ))
    return out


def run_smoke(engine: Engine, spec, args) -> dict:
    """UNA prueba mínima del escritor real con datos propios del ensayo (rangos reservados): escritura,
    repetición, actualización, evidencia antigua, atomicidad y protección DELETE RESTRICT."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from app.repositories.fixture_repository import ensure_teams, upsert_fixtures
    from app.schemas.fixture_evidence import FixtureEvidence

    maint = t.maint_engine(engine)
    ext = [SMOKE_FIXTURE_BASE + i + 1 for i in range(SMOKE_FIXTURES)]
    with maint.connect() as m:
        if m.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != "0008":
            raise t.TargetRefused("La prueba del escritor exige 0008")
        if m.exec_driver_sql("SELECT count(*) FROM fixtures WHERE external_id = ANY(%(e)s)", {"e": ext}).scalar_one():
            raise t.TargetRefused("Los ids de la prueba ya existen: no se repite")
        real_before = m.exec_driver_sql(
            "SELECT count(*), md5(string_agg(encode(last_state_hash, 'hex') || last_observed_at::text, ',' ORDER BY id)) "
            "FROM fixtures WHERE external_id < %(b)s", {"b": t.FIXTURE_EXTERNAL_BASE}).one()
    with engine.begin() as conn:
        competition_id = conn.exec_driver_sql(
            "INSERT INTO competitions (external_id, name, type, country) VALUES (%(e)s, 'Ensayo 0008 (DI-A6)', 'League', 'Ensayo') "
            "RETURNING id", {"e": SMOKE_COMPETITION_EXTERNAL_ID}).scalar_one()
        season_id = conn.exec_driver_sql(
            "INSERT INTO seasons (competition_id, year, start_date, end_date, is_current) "
            "VALUES (%(c)s, 2026, '2026-08-01', '2027-05-31', true) RETURNING id", {"c": competition_id}).scalar_one()

    def state():
        with maint.connect() as m:
            return dict(m.exec_driver_sql(
                "SELECT (SELECT count(*) FROM fixtures WHERE external_id = ANY(%(e)s)) AS fixtures, "
                "(SELECT count(*) FROM fixture_observations o JOIN fixtures f ON f.id = o.fixture_id WHERE f.external_id = ANY(%(e)s)) AS observations, "
                "(SELECT count(*) FROM fixtures f WHERE f.external_id = ANY(%(e)s) AND NOT EXISTS (SELECT 1 FROM fixture_observations o "
                " WHERE o.fixture_id = f.id AND o.observed_at = f.last_observed_at AND o.state_hash = f.last_state_hash)) AS winner_not_observation, "
                "(SELECT md5(string_agg(encode(last_state_hash, 'hex') || last_observed_at::text || coalesce(home_goals::text, '-'), ',' "
                " ORDER BY external_id)) FROM fixtures WHERE external_id = ANY(%(e)s)) AS fingerprint, "
                "(SELECT count(*) FROM fixture_provider_mappings WHERE provider = 'api-football' AND external_id = ANY(%(s)s)) AS mappings",
                {"e": ext, "s": [str(e) for e in ext]}).mappings().one())

    def write(fixtures, evidence, commit=True):
        with Session(engine) as db:
            teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
            ids = ensure_teams(db, teams, "api-football")
            counts = upsert_fixtures(db, season_id, fixtures, ids, "api-football", evidence)
            if commit:
                db.commit()
            else:
                db.rollback()
        return {k: getattr(counts, k) for k in ("received", "created", "updated", "unchanged")}

    steps = {}
    e1 = FixtureEvidence.received("sync", "api-football")
    steps["1_insert"] = {"counts": write(_smoke_fixtures(None), e1), "state": state()}
    steps["2_replay_same_evidence"] = {"counts": write(_smoke_fixtures(None), e1), "state": state()}
    e2 = FixtureEvidence.received("sync", "api-football")
    steps["3_newer_update"] = {"counts": write(_smoke_fixtures(1), e2), "state": state()}
    stale = FixtureEvidence("sync", "api-football", e1.observed_at - timedelta(days=1))
    steps["4_stale_evidence"] = {"counts": write(_smoke_fixtures(2), stale), "state": state()}
    e3 = FixtureEvidence.received("sync", "api-football")
    steps["5_rolled_back_write"] = {"counts": write(_smoke_fixtures(3), e3, commit=False), "state": state()}
    delete_protected = False
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.exec_driver_sql("DELETE FROM fixtures WHERE external_id = %(e)s", {"e": ext[0]})
        except IntegrityError:
            delete_protected = True
        finally:
            tx.rollback()
    steps["6_delete_with_history"] = {"rejected_by_restrict": delete_protected, "state": state()}
    with maint.connect() as m:
        real_after = m.exec_driver_sql(
            "SELECT count(*), md5(string_agg(encode(last_state_hash, 'hex') || last_observed_at::text, ',' ORDER BY id)) "
            "FROM fixtures WHERE external_id < %(b)s", {"b": t.FIXTURE_EXTERNAL_BASE}).one()
    maint.dispose()
    n = SMOKE_FIXTURES
    s = steps
    checks = {
        "insert_counts": s["1_insert"]["counts"] == {"received": n, "created": n, "updated": 0, "unchanged": 0},
        "insert_evidence": s["1_insert"]["state"]["observations"] == n and s["1_insert"]["state"]["fixtures"] == n,
        "insert_winner_is_observation": s["1_insert"]["state"]["winner_not_observation"] == 0,
        "insert_mappings": s["1_insert"]["state"]["mappings"] == n,
        "replay_counts": s["2_replay_same_evidence"]["counts"] == {"received": n, "created": 0, "updated": 0, "unchanged": n},
        "replay_no_new_evidence": s["2_replay_same_evidence"]["state"]["observations"] == n,
        "replay_state_unchanged": s["2_replay_same_evidence"]["state"]["fingerprint"] == s["1_insert"]["state"]["fingerprint"],
        "update_counts": s["3_newer_update"]["counts"] == {"received": n, "created": 0, "updated": n, "unchanged": 0},
        "update_evidence": s["3_newer_update"]["state"]["observations"] == 2 * n,
        "update_winner_is_observation": s["3_newer_update"]["state"]["winner_not_observation"] == 0,
        "update_changed_state": s["3_newer_update"]["state"]["fingerprint"] != s["1_insert"]["state"]["fingerprint"],
        "stale_counts": s["4_stale_evidence"]["counts"] == {"received": n, "created": 0, "updated": 0, "unchanged": n},
        "stale_history_only": s["4_stale_evidence"]["state"]["observations"] == 3 * n,
        "stale_did_not_overwrite": s["4_stale_evidence"]["state"]["fingerprint"] == s["3_newer_update"]["state"]["fingerprint"],
        "rollback_atomic": (s["5_rolled_back_write"]["state"]["observations"] == 3 * n
                            and s["5_rolled_back_write"]["state"]["fingerprint"] == s["3_newer_update"]["state"]["fingerprint"]
                            and s["5_rolled_back_write"]["counts"]["updated"] == n),
        "delete_with_history_rejected": delete_protected and s["6_delete_with_history"]["state"]["fixtures"] == n,
        "real_rows_untouched": tuple(real_before) == tuple(real_after),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "created_at": now(), "checks": checks, "steps": steps,
            "scope": {"fixtures": n, "fixture_external_ids": f"{ext[0]}..{ext[-1]}", "season": "1 (ensayo)",
                      "teams": 10, "competition_external_id": SMOKE_COMPETITION_EXTERNAL_ID},
            "real_rows": {"count": real_before[0]}}


def run_invariants(engine: Engine, spec, args) -> dict:
    """Invariantes A6 agregados sobre toda la BD tras la prueba del escritor (los de g2_target.verify)."""
    report = t.run_verify(engine, spec, argparse.Namespace(rollback_probe=False))
    maint = t.maint_engine(engine)
    with maint.connect() as m:
        report["quiescence"] = quiescence(m)
    maint.dispose()
    return report


COMMANDS = {"preflight": run_preflight, "migrate": run_migrate, "validate": run_validate, "smoke": run_smoke,
            "invariants": run_invariants}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.perf_lab.rehearsal_0008")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
    sub.choices["preflight"].add_argument("--expect-fixtures", type=int, required=True)
    for name in ("validate",):
        sub.choices[name].add_argument("--expect-fixtures", type=int, required=True)
        sub.choices[name].add_argument("--expect-mappings", type=int, required=True)
        sub.choices[name].add_argument("--expect-unmapped", type=int, default=0)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("El archivo de salida ya existe; elige otro nombre")
    args.output = args.output.resolve()
    spec = t.authorize_target(os.environ, url_env=URL_ENV, branch_env=BRANCH_ENV)
    try:
        engine = t.load_target_app(spec)
        try:
            report = COMMANDS[args.command](engine, spec, args)
        finally:
            engine.dispose()
    except t.TargetRefused:
        raise
    except Exception as exc:  # noqa: BLE001  nunca se propaga un mensaje con datos de conexión
        raise RuntimeError(f"{exc.__class__.__name__}: {t.redact(str(exc), spec.url)[:400]}") from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"{args.command}: {report.get('status')} → {args.output.name}")
    return 0 if report.get("status") in ("PASS", "MIGRATED") else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except t.TargetRefused as exc:
        print(f"TARGET_REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(4)
    except (ValueError, RuntimeError) as exc:
        print(f"Ensayo bloqueado: {exc}", file=sys.stderr)
        raise SystemExit(2)
