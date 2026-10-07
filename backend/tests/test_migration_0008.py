"""Migración 0008 (DI-A6): esquema de fixture_observations, metadatos de orden en fixtures y bootstrap.

Bootstrap: un único instante real y un único evidence_id, una observación por fixture, sin tocar
los fixtures; un fallo deshace la migración entera y se puede volver a lanzar; con un escritor
activo falla por lock_timeout en vez de esperar. Solo BD de tests.
"""

import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

from tests.conftest import alembic_run

pytestmark = pytest.mark.db

STATE = (
    "kickoff_at, status_short, season_id, home_team_id, away_team_id, home_goals, away_goals, halftime_home, "
    "halftime_away, fulltime_home, fulltime_away, extratime_home, extratime_away, penalty_home, penalty_away"
)
SEED_0007 = """
INSERT INTO competitions (id, external_id, name) VALUES (1, 265, 'Liga Test');
INSERT INTO seasons (id, competition_id, year, is_current) VALUES (1, 1, 2026, true);
INSERT INTO teams (id, external_id, name) VALUES (1, 2323, 'Equipo A'), (2, 2329, 'Equipo B'), (3, 2315, 'Equipo C');
INSERT INTO fixtures (id, external_id, season_id, kickoff_at, status_short, home_team_id, away_team_id,
                      home_goals, away_goals, halftime_home, halftime_away, fulltime_home, fulltime_away,
                      extratime_home, extratime_away, penalty_home, penalty_away, created_at, updated_at) VALUES
  (1, 1505353, 1, '2026-01-30 23:00+00', 'FT',  1, 2, 2, 1, 1, 0, 2, 1, NULL, NULL, NULL, NULL, '2026-01-01+00', '2026-02-01+00'),
  (2, 1622633, 1, '2026-08-27 17:00+00', 'AET', 1, 3, 5, 1, 1, 1, 1, 1, 4, 0, NULL, NULL, '2026-01-01+00', '2026-08-28+00'),
  (3, 1631509, 1, '2026-09-16 22:00+00', 'PEN', 2, 3, 3, 3, 0, 1, 2, 2, 1, 1, 4, 3, '2026-01-01+00', '2026-09-17+00'),
  (4, 1505531, 1, '2026-10-10 18:00+00', 'NS',  3, 1, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-01-01+00', '2026-01-01+00');
"""
# Tabla con el nombre del índice final de 0008: hace fallar la migración DESPUÉS del bootstrap
BLOCKER = "ix_fixture_observations_fixture_observed"
CLEANUP = "TRUNCATE fixture_observations, fixtures, season_teams, seasons, teams, competitions CASCADE"
HASH_ARGS = (
    "timestamptz, text, integer, integer, integer, integer, integer, integer, integer, integer, integer, "
    "integer, integer, integer, integer"
)


def _function_exists(conn) -> bool:
    return conn.execute(text("SELECT to_regprocedure(:sig) IS NOT NULL"), {"sig": f"fixture_state_hash_v1({HASH_ARGS})"}).scalar_one()


def _version(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _fixture_snapshot(conn) -> list[tuple]:
    return conn.execute(text(f"SELECT id, external_id, {STATE}, created_at, updated_at FROM fixtures ORDER BY id")).all()


@pytest.fixture
def db_at_0007_with_data(migrated_db):
    from app.db.database import engine

    alembic_run("downgrade", "0007")
    with engine.begin() as conn:
        conn.execute(text(SEED_0007))
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            if conn.execute(text("SELECT relkind FROM pg_class WHERE relname = :n"), {"n": BLOCKER}).scalar() == "r":
                conn.execute(text(f"DROP TABLE {BLOCKER}"))  # la tabla que bloquea el índice, si quedó
        alembic_run("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(text(CLEANUP))


# --- Upgrade / downgrade y esquema ---------------------------------------------------------


def test_upgrade_downgrade_upgrade_0008(migrated_db):
    from app.db.database import engine

    try:
        alembic_run("downgrade", "0007")
        with engine.connect() as conn:
            insp = inspect(conn)
            assert "fixture_observations" not in insp.get_table_names()
            assert not {"last_observed_at", "last_state_hash"} & {c["name"] for c in insp.get_columns("fixtures")}
            assert not _function_exists(conn)
            assert "fixture_statistics_observations" in insp.get_table_names()  # 0007 intacta
            assert _version(conn) == "0007"
        alembic_run("upgrade", "0008")
        with engine.connect() as conn:
            assert "fixture_observations" in inspect(conn).get_table_names()
            assert _function_exists(conn)
            assert conn.execute(text("SELECT count(*) FROM fixture_observations")).scalar_one() == 0
            assert _version(conn) == "0008"
    finally:
        alembic_run("upgrade", "head")


def test_alembic_graph_is_linear_with_head_0008():
    from alembic.script import ScriptDirectory

    from tests.conftest import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == ["0008"]
    assert script.get_revision("0008").down_revision == "0007"
    assert [r.revision for r in script.walk_revisions()] == ["0008", "0007", "0006", "0005", "0004", "0003", "0002", "0001"]


def test_columns_types_and_nullability(migrated_db):
    from app.db.database import engine

    with engine.connect() as conn:
        insp = inspect(conn)
        cols = {c["name"]: c for c in insp.get_columns("fixture_observations")}
        not_null = ("id", "evidence_id", "fixture_id", "observed_at", "recorded_at", "source", "state_hash",
                    "kickoff_at", "status_short", "season_id", "home_team_id", "away_team_id")
        for c in not_null:
            assert not cols[c]["nullable"], c
        assert cols["provider"]["nullable"]
        for c in ("home_goals", "away_goals", "halftime_home", "halftime_away", "fulltime_home", "fulltime_away",
                  "extratime_home", "extratime_away", "penalty_home", "penalty_away"):
            assert cols[c]["nullable"], c
        assert cols["observed_at"]["default"] is None  # nunca now() de la BD
        assert "now()" in cols["recorded_at"]["default"]
        assert cols["evidence_id"]["default"] is None
        assert str(cols["evidence_id"]["type"]) == "UUID"
        assert str(cols["state_hash"]["type"]) == "BYTEA"

        fixture_cols = {c["name"]: c for c in insp.get_columns("fixtures")}
        assert not fixture_cols["last_observed_at"]["nullable"] and not fixture_cols["last_state_hash"]["nullable"]
        assert "state_observed_at" not in fixture_cols  # reemplazada por el contrato
        assert not any("last_" in col for ix in insp.get_indexes("fixtures") for col in ix["column_names"])


def test_keys_indexes_and_foreign_keys(migrated_db):
    from app.db.database import engine

    with engine.connect() as conn:
        insp = inspect(conn)
        uniques = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("fixture_observations")}
        assert uniques == {"uq_fixture_observations_fixture_evidence": ["fixture_id", "evidence_id"]}
        indexes = {i["name"]: (i["column_names"], i["unique"]) for i in insp.get_indexes("fixture_observations")}
        assert indexes["ix_fixture_observations_fixture_observed"] == (["fixture_id", "observed_at"], False)
        fks = {tuple(fk["constrained_columns"]): (fk["referred_table"], fk["options"].get("ondelete")) for fk in insp.get_foreign_keys("fixture_observations")}
        assert fks == {("fixture_id",): ("fixtures", "RESTRICT"), ("provider",): ("providers", "RESTRICT")}
        checks = {c["name"] for c in insp.get_check_constraints("fixture_observations")}
        assert checks == {
            "ck_fixture_observations_source", "ck_fixture_observations_provider", "ck_fixture_observations_state_hash",
            "ck_fixture_observations_fulltime_pair", "ck_fixture_observations_fulltime_finished",
            "ck_fixture_observations_fulltime_nonneg",
        }
        assert "ck_fixtures_last_state_hash" in {c["name"] for c in insp.get_check_constraints("fixtures")}


# --- Constraints (dentro de una transacción que se deshace) --------------------------------


@pytest.fixture
def one_fixture(db_session) -> int:
    from tests.conftest import make_competition, make_fixture_data, make_evidence
    from app.repositories import fixture_repository

    _, season_id = make_competition(db_session, 265)
    data = make_fixture_data(1)
    team_ids = fixture_repository.ensure_teams(db_session, [data.home_team, data.away_team], "api-football")
    fixture_repository.upsert_fixtures(db_session, season_id, [data], team_ids, "api-football", make_evidence())
    db_session.flush()
    return db_session.execute(text("SELECT id FROM fixtures WHERE external_id = 1")).scalar_one()


def _insert_observation(db, fixture_id: int, **overrides) -> None:
    values = {
        "evidence_id": uuid.uuid4(), "fixture_id": fixture_id, "observed_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "source": "sync", "provider": "api-football", "state_hash": b"\x01" * 32,
        "kickoff_at": datetime(2026, 9, 1, 20, tzinfo=timezone.utc), "status_short": "NS",
        "season_id": 999_999, "home_team_id": 888_888, "away_team_id": 777_777,  # sin FK: valores del momento
        "fulltime_home": None, "fulltime_away": None,
    }
    values.update(overrides)
    cols = ", ".join(values)
    db.execute(text(f"INSERT INTO fixture_observations ({cols}) VALUES ({', '.join(':' + c for c in values)})"), values)


def test_snapshot_ids_have_no_live_foreign_keys(db_session, one_fixture):
    _insert_observation(db_session, one_fixture)  # season/team inexistentes: se aceptan
    db_session.flush()


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        ({"source": "manual", "provider": None}, "ck_fixture_observations_source"),
        ({"source": "reassertion", "provider": None}, "ck_fixture_observations_source"),
        ({"source": "sync", "provider": None}, "ck_fixture_observations_provider"),
        ({"source": "backfill", "provider": None}, "ck_fixture_observations_provider"),
        ({"source": "bootstrap", "provider": "api-football"}, "ck_fixture_observations_provider"),
        ({"state_hash": b"\x01" * 31}, "ck_fixture_observations_state_hash"),
        ({"status_short": "FT", "fulltime_home": 1, "fulltime_away": None}, "ck_fixture_observations_fulltime_pair"),
        ({"status_short": "NS", "fulltime_home": 1, "fulltime_away": 0}, "ck_fixture_observations_fulltime_finished"),
        ({"status_short": "FT", "fulltime_home": -1, "fulltime_away": 0}, "ck_fixture_observations_fulltime_nonneg"),
        ({"provider": "no-such-provider"}, "fixture_observations_provider_fkey"),
    ],
)
def test_constraints_reject_invalid_evidence(db_session, one_fixture, overrides, constraint):
    with pytest.raises(IntegrityError) as excinfo:
        _insert_observation(db_session, one_fixture, **overrides)
    assert constraint in str(excinfo.value)


def test_bootstrap_source_without_provider_is_valid(db_session, one_fixture):
    _insert_observation(db_session, one_fixture, source="bootstrap", provider=None)


def test_unique_fixture_evidence(db_session, one_fixture):
    evidence = uuid.uuid4()
    _insert_observation(db_session, one_fixture, evidence_id=evidence)
    with pytest.raises(IntegrityError) as excinfo:
        _insert_observation(db_session, one_fixture, evidence_id=evidence, observed_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert "uq_fixture_observations_fixture_evidence" in str(excinfo.value)


def test_fixture_with_history_cannot_be_deleted(db_session, one_fixture):
    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(text("DELETE FROM fixtures WHERE id = :f"), {"f": one_fixture})
    assert "fixture_observations_fixture_id_fkey" in str(excinfo.value)


def test_provider_with_evidence_cannot_be_deleted(db_session, one_fixture):
    _insert_observation(db_session, one_fixture, provider="5dollarfootballapi", source="backfill")
    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(text("DELETE FROM providers WHERE code = '5dollarfootballapi'"))
    assert "fixture_observations_provider_fkey" in str(excinfo.value)


def test_fixtures_ordering_metadata_is_required(db_session, one_fixture):
    with pytest.raises(IntegrityError):
        db_session.execute(text("UPDATE fixtures SET last_state_hash = NULL WHERE id = :f"), {"f": one_fixture})


# --- Bootstrap -----------------------------------------------------------------------------


def test_bootstrap_one_real_instant_one_observation_per_fixture(db_at_0007_with_data):
    engine = db_at_0007_with_data
    with engine.connect() as conn:
        before_fixtures = _fixture_snapshot(conn)
    started = datetime.now(timezone.utc)
    alembic_run("upgrade", "0008")
    finished = datetime.now(timezone.utc)

    with engine.connect() as conn:
        assert _fixture_snapshot(conn) == before_fixtures  # ni datos ni created_at/updated_at cambian
        rows = conn.execute(
            text(
                f"SELECT o.evidence_id, o.observed_at, o.source, o.provider, o.state_hash, f.last_observed_at, "
                f"f.last_state_hash, fixture_state_hash_v1({', '.join('f.' + c.strip() for c in STATE.split(','))}) AS h, "
                f"({', '.join('o.' + c.strip() for c in STATE.split(','))}) IS NOT DISTINCT FROM ({', '.join('f.' + c.strip() for c in STATE.split(','))}) AS same "
                "FROM fixtures f JOIN fixture_observations o ON o.fixture_id = f.id ORDER BY f.id"
            )
        ).all()
        assert len(rows) == len(before_fixtures) == 4
        assert conn.execute(text("SELECT count(*) FROM fixture_observations")).scalar_one() == 4
        assert len({r.evidence_id for r in rows}) == 1
        bootstrap_at = rows[0].observed_at
        assert started <= bootstrap_at <= finished  # instante real, no created_at/updated_at/kickoff
        for r in rows:
            assert r.observed_at == r.last_observed_at == bootstrap_at
            assert (r.source, r.provider) == ("bootstrap", None)
            assert bytes(r.state_hash) == bytes(r.last_state_hash) == bytes(r.h)
            assert r.same
        # Nada es conocido antes del bootstrap: no hay ninguna observación anterior
        assert conn.execute(
            text("SELECT count(*) FROM fixture_observations WHERE observed_at < :t"), {"t": bootstrap_at}
        ).scalar_one() == 0


def test_bootstrap_failure_rolls_back_everything_and_rerun_succeeds(db_at_0007_with_data):
    """Un fallo después del bootstrap (al crear el índice final) no deja nada: ni tabla, ni columnas,
    ni función, ni observaciones; tras quitar la causa, la migración se vuelve a lanzar sin más."""
    engine = db_at_0007_with_data
    with engine.begin() as conn:
        before_fixtures = _fixture_snapshot(conn)
        conn.execute(text(f"CREATE TABLE {BLOCKER} (x int)"))
    with pytest.raises(Exception, match="already exists"):
        alembic_run("upgrade", "0008")

    with engine.connect() as conn:
        insp = inspect(conn)
        assert _version(conn) == "0007"
        assert "fixture_observations" not in insp.get_table_names()
        assert not {"last_observed_at", "last_state_hash"} & {c["name"] for c in insp.get_columns("fixtures")}
        assert not _function_exists(conn)
        assert _fixture_snapshot(conn) == before_fixtures

    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE {BLOCKER}"))
    alembic_run("upgrade", "0008")
    with engine.connect() as conn:
        assert _version(conn) == "0008"
        assert conn.execute(text("SELECT count(DISTINCT evidence_id), count(*) FROM fixture_observations")).one() == (1, 4)
        assert _fixture_snapshot(conn) == before_fixtures


def test_bootstrap_fails_fast_while_a_writer_holds_fixtures(db_at_0007_with_data):
    """Con un escritor activo la migración no espera indefinidamente: lock_timeout y rollback completo."""
    engine = db_at_0007_with_data
    holding = threading.Event()
    release = threading.Event()

    def writer():
        with engine.connect() as conn:
            conn.execute(text("UPDATE fixtures SET round = round WHERE id = 1"))  # transacción abierta
            holding.set()
            release.wait(30)
            conn.rollback()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert holding.wait(10)
        with pytest.raises(OperationalError, match="lock timeout"):
            alembic_run("upgrade", "0008")
    finally:
        release.set()
        thread.join(30)
    with engine.connect() as conn:
        assert _version(conn) == "0007"
        assert "fixture_observations" not in inspect(conn).get_table_names()
