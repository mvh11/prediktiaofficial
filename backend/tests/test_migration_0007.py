"""Migración 0007 (estadísticas de partido): upgrade/downgrade, columnas, constraints, únicos,
observación vigente única y lock de runs. Solo BD de tests."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models import Fixture
from app.repositories import fixture_repository
from tests.conftest import alembic_run, make_competition, make_fixture_data

pytestmark = pytest.mark.db

TABLES = ("statistics_runs", "fixture_statistics_observations", "fixture_team_statistics")
T0 = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
HASH = "a" * 64


# --- Upgrade / downgrade ---------------------------------------------------------------------


def test_upgrade_downgrade_upgrade_0007(migrated_db):
    from app.db.database import engine

    try:
        alembic_run("downgrade", "0006")
        with engine.connect() as conn:
            names = inspect(conn).get_table_names()
            assert not set(TABLES) & set(names)
            assert {"live_sync_runs", "fixtures", "teams"} <= set(names)  # M1–M4 intactas
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006"
        alembic_run("upgrade", "0007")
        with engine.connect() as conn:
            insp = inspect(conn)
            assert set(TABLES) <= set(insp.get_table_names())
            for table in TABLES:  # vacías tras la migración
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"
    finally:
        alembic_run("upgrade", "head")


def test_columns_and_types(migrated_db):
    from app.db.database import engine

    with engine.connect() as conn:
        insp = inspect(conn)
        fts = {c["name"]: c for c in insp.get_columns("fixture_team_statistics")}
        assert (fts["possession_pct"]["type"].precision, fts["possession_pct"]["type"].scale) == (5, 2)
        assert (fts["passes_pct"]["type"].precision, fts["passes_pct"]["type"].scale) == (5, 2)
        assert (fts["expected_goals"]["type"].precision, fts["expected_goals"]["type"].scale) == (6, 3)
        ints = ["shots_on_goal", "shots_off_goal", "shots_total", "shots_blocked", "shots_inside_box", "shots_outside_box",
                "fouls", "corners", "offsides", "yellow_cards", "red_cards", "goalkeeper_saves", "passes_total", "passes_accurate"]
        for c in ints:
            assert fts[c]["nullable"] and fts[c]["type"].python_type is int
        for c in ("fixture_id", "team_id", "provider", "observation_id", "side", "normalizer_version"):
            assert not fts[c]["nullable"]

        fso = {c["name"]: c for c in insp.get_columns("fixture_statistics_observations")}
        for c in ("fixture_id", "provider", "provider_fixture_id", "source", "availability", "teams_returned", "payload",
                  "payload_hash", "observed_at", "last_observed_at", "available_at", "is_latest"):
            assert not fso[c]["nullable"], c
        assert fso["run_id"]["nullable"] and fso["fixture_status_at_fetch"]["nullable"]

        runs = {c["name"] for c in insp.get_columns("statistics_runs")}
        assert {
            "trigger", "mode", "scope", "competition_id", "season_id", "status", "lock_scope", "started_at", "finished_at",
            "cursor_fixture_id", "fixtures_targeted", "fixtures_attempted", "fixtures_available", "fixtures_partial",
            "fixtures_empty", "fixtures_blocked", "fixtures_missing_in_response", "provider_requests", "provider_retries",
            "rate_limited", "auth_failed", "observations_created", "observations_unchanged", "rows_created", "rows_updated",
            "rows_unchanged", "warning_count", "blocking_count", "checks", "details", "coverage", "error_message",
        } <= runs


def test_keys_indexes_and_foreign_keys(migrated_db):
    from app.db.database import engine

    with engine.connect() as conn:
        insp = inspect(conn)
        fts_fks = {fk["name"]: fk for fk in insp.get_foreign_keys("fixture_team_statistics")}
        composite = fts_fks["fk_fts_observation_same_fixture"]
        assert composite["constrained_columns"] == ["observation_id", "fixture_id", "provider"]
        assert composite["referred_table"] == "fixture_statistics_observations"
        assert composite["options"].get("ondelete") == "CASCADE"
        by_table = {fk["referred_table"]: fk["options"].get("ondelete") for fk in insp.get_foreign_keys("fixture_team_statistics")}
        assert by_table["fixtures"] == "CASCADE" and by_table["teams"] == "RESTRICT" and by_table["providers"] == "RESTRICT"
        fso_fks = {fk["referred_table"]: fk["options"].get("ondelete") for fk in insp.get_foreign_keys("fixture_statistics_observations")}
        assert fso_fks == {"fixtures": "CASCADE", "providers": "RESTRICT", "statistics_runs": "SET NULL"}

        uniques = {u["name"] for u in insp.get_unique_constraints("fixture_team_statistics")}
        assert {"uq_fts_fixture_team_provider", "uq_fts_fixture_side_provider"} <= uniques
        idx = {i["name"]: i for t in TABLES for i in insp.get_indexes(t)}
        assert idx["uq_fso_one_latest"]["unique"] and idx["uq_statistics_runs_one_running"]["unique"]
        assert "ix_fso_fixture_provider_available" in idx and "ix_fts_team_fixture" in idx
        defs = dict(conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN ('uq_fso_one_latest', 'uq_statistics_runs_one_running')")).all())
        assert "WHERE is_latest" in defs["uq_fso_one_latest"]
        assert "WHERE (status = 'running'::text)" in defs["uq_statistics_runs_one_running"]


# --- Datos de apoyo -------------------------------------------------------------------------


@pytest.fixture
def match(db_session):
    """Un partido FT con sus dos equipos: (fixture_id, home_team_id, away_team_id)."""
    _, sid = make_competition(db_session, 265)
    data = [make_fixture_data(1, status="FT", home_goals=1, away_goals=0, fulltime_home=1, fulltime_away=0),
            make_fixture_data(2, home=3, away=4, status="FT", home_goals=0, away_goals=0, fulltime_home=0, fulltime_away=0)]
    team_ids = fixture_repository.ensure_teams(db_session, [t for f in data for t in (f.home_team, f.away_team)], "api-football")
    fixture_repository.upsert_fixtures(db_session, sid, data, team_ids, "api-football")
    rows = db_session.execute(select(Fixture.id, Fixture.home_team_id, Fixture.away_team_id).order_by(Fixture.external_id)).all()
    return tuple(rows[0]), tuple(rows[1])


def _insert(db, table, **values):
    cols = ", ".join(values)
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k == "payload" else f":{k}" for k in values)
    with db.begin_nested():
        return db.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params}) RETURNING id"), values).scalar_one()


def _observation(db, fixture_id, **overrides):
    values = dict(fixture_id=fixture_id, provider="api-football", provider_fixture_id="1", source="backfill",
                  availability="available", teams_returned=2, payload="[]", payload_hash=HASH, observed_at=T0,
                  last_observed_at=T0, available_at=T0 + timedelta(hours=6), is_latest=True)
    values.update(overrides)
    return _insert(db, "fixture_statistics_observations", **values)


def _team_row(db, fixture_id, team_id, observation_id, **overrides):
    values = dict(fixture_id=fixture_id, team_id=team_id, provider="api-football", observation_id=observation_id,
                  side="home", normalizer_version=1)
    values.update(overrides)
    return _insert(db, "fixture_team_statistics", **values)


def _run(db, **overrides):
    values = dict(trigger="cli", mode="apply", scope="fixtures", status="running")
    values.update(overrides)
    return _insert(db, "statistics_runs", **values)


# --- statistics_runs -------------------------------------------------------------------------

FINISHED = "2099-01-01T00:00:00+00"


@pytest.mark.parametrize(
    "overrides",
    [
        {"trigger": "api"},
        {"mode": "live"},
        {"scope": "league"},
        {"status": "done"},
        {"lock_scope": "live_sync"},
        {"scope": "season"},  # season sin competition/season
        {"status": "completed"},  # terminado sin finished_at
        {"finished_at": FINISHED},  # running con finished_at
        {"status": "completed", "finished_at": "2000-01-01T00:00:00+00"},  # antes de started_at
        {"status": "dry_run_completed", "finished_at": FINISHED},  # apply no termina en dry_run_completed
        {"mode": "dry_run", "status": "completed", "finished_at": FINISHED},
        {"provider_requests": -1},
        {"rows_updated": -1},
        # reparto de partidos que no cuadra al terminar
        {"status": "completed", "finished_at": FINISHED, "fixtures_targeted": 5, "fixtures_attempted": 5, "fixtures_available": 4},
        {"status": "completed", "finished_at": FINISHED, "fixtures_targeted": 1, "fixtures_attempted": 2, "fixtures_available": 2},
    ],
)
def test_statistics_runs_constraints_reject(db_session, overrides):
    with pytest.raises(IntegrityError):
        _run(db_session, **overrides)


def test_statistics_runs_valid_lifecycles(db_session):
    # running incremental: el reparto todavía no cuadra y está permitido
    _run(db_session, fixtures_targeted=40, fixtures_attempted=20, fixtures_available=3)
    _run(db_session, status="completed", finished_at=FINISHED, fixtures_targeted=6, fixtures_attempted=5,
         fixtures_available=1, fixtures_partial=1, fixtures_empty=1, fixtures_blocked=1, fixtures_missing_in_response=1)
    _run(db_session, mode="dry_run", status="dry_run_completed", finished_at=FINISHED)
    _run(db_session, status="failed", finished_at=FINISHED, fixtures_attempted=7)  # cortado a mitad
    cid, sid = make_competition(db_session, 39, name="Otra")
    _run(db_session, scope="season", competition_id=cid, season_id=sid, status="aborted", finished_at=FINISHED)


def test_only_one_running_statistics_run(db_session):
    _run(db_session)
    with pytest.raises(IntegrityError) as excinfo:
        _run(db_session, scope="live", mode="dry_run")
    assert "uq_statistics_runs_one_running" in str(excinfo.value)
    _run(db_session, status="completed", finished_at=FINISHED)  # los terminados no cuentan


# --- fixture_statistics_observations --------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "api"},
        {"availability": "full"},
        {"teams_returned": 3},
        {"teams_returned": -1},
        {"availability": "empty", "teams_returned": 2},
        {"availability": "partial", "teams_returned": 2},
        {"payload_hash": "xyz"},
        {"payload_hash": "A" * 64},
        {"last_observed_at": T0 - timedelta(seconds=1)},
        {"provider": "desconocido"},
        {"available_at": None},
        {"payload": None},
    ],
)
def test_observation_constraints_reject(db_session, match, overrides):
    with pytest.raises(IntegrityError):
        _observation(db_session, match[0][0], **overrides)


def test_one_latest_observation_per_fixture_and_provider(db_session, match):
    fid = match[0][0]
    _observation(db_session, fid)
    with pytest.raises(IntegrityError) as excinfo:
        _observation(db_session, fid, payload_hash="b" * 64)
    assert "uq_fso_one_latest" in str(excinfo.value)
    _observation(db_session, fid, payload_hash="c" * 64, is_latest=False)  # historial: permitido
    _observation(db_session, match[1][0])  # otro partido: permitido
    _observation(db_session, fid, provider="5dollarfootballapi")  # otro proveedor: permitido


def test_empty_and_partial_observations_are_valid(db_session, match):
    _observation(db_session, match[0][0], availability="empty", teams_returned=0, payload='[{"team": {"id": 1}, "statistics": []}]')
    _observation(db_session, match[1][0], availability="partial", teams_returned=1, source="live", run_id=None)


# --- fixture_team_statistics ----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"side": "neutral"},
        {"normalizer_version": 0},
        {"shots_on_goal": -1},
        {"red_cards": -1},
        {"passes_accurate": -5},
        {"possession_pct": -0.01},
        {"possession_pct": 100.01},
        {"passes_pct": 101},
        {"expected_goals": -0.001},
        {"possession_pct": 1000},  # fuera de numeric(5,2)
    ],
)
def test_team_statistics_constraints_reject(db_session, match, overrides):
    (fid, home, _away), _ = match
    obs = _observation(db_session, fid)
    with pytest.raises((IntegrityError, DataError)):  # DataError: desbordamiento de numeric(5,2)
        _team_row(db_session, fid, home, obs, **overrides)


def test_team_statistics_accepts_nulls_zeros_and_bounds(db_session, match):
    (fid, home, away), _ = match
    obs = _observation(db_session, fid)
    _team_row(db_session, fid, home, obs)  # todo NULL
    _team_row(db_session, fid, away, obs, side="away", shots_on_goal=0, red_cards=0, possession_pct=100,
              passes_pct=0, expected_goals=0, shots_total=3)  # 0 y extremos válidos
    # Datos raros del proveedor que NO se rechazan en SQL (son warnings de calidad)
    db_session.execute(text("UPDATE fixture_team_statistics SET shots_on_goal = 9, shots_total = 2, passes_total = 1, passes_accurate = 5 WHERE team_id = :t"), {"t": home})


def test_duplicate_fixture_team_and_side_rejected(db_session, match):
    (fid, home, away), _ = match
    obs = _observation(db_session, fid)
    _team_row(db_session, fid, home, obs)
    with pytest.raises(IntegrityError) as e1:
        _team_row(db_session, fid, home, obs, side="away")
    assert "uq_fts_fixture_team_provider" in str(e1.value)
    with pytest.raises(IntegrityError) as e2:
        _team_row(db_session, fid, away, obs)  # otro equipo, mismo lado
    assert "uq_fts_fixture_side_provider" in str(e2.value)


def test_team_statistics_cannot_point_to_another_fixtures_observation(db_session, match):
    (fid, home, _), (other_fid, _, _) = match
    foreign = _observation(db_session, other_fid)
    with pytest.raises(IntegrityError) as excinfo:
        _team_row(db_session, fid, home, foreign)
    assert "fk_fts_observation_same_fixture" in str(excinfo.value)


def test_deleting_a_fixture_cleans_its_statistics(db_session, match):
    (fid, home, _), (other_fid, other_home, _) = match
    _team_row(db_session, fid, home, _observation(db_session, fid))
    _team_row(db_session, other_fid, other_home, _observation(db_session, other_fid))
    db_session.execute(text("DELETE FROM fixtures WHERE id = :f"), {"f": fid})
    count = lambda t, f: db_session.execute(text(f"SELECT count(*) FROM {t} WHERE fixture_id = :f"), {"f": f}).scalar_one()  # noqa: E731
    assert count("fixture_statistics_observations", fid) == count("fixture_team_statistics", fid) == 0
    assert count("fixture_statistics_observations", other_fid) == count("fixture_team_statistics", other_fid) == 1


def test_deleting_a_run_keeps_its_observations(db_session, match):
    run_id = _run(db_session, status="completed", finished_at=FINISHED)
    obs = _observation(db_session, match[0][0], run_id=run_id)
    db_session.execute(text("DELETE FROM statistics_runs WHERE id = :r"), {"r": run_id})
    assert db_session.execute(text("SELECT run_id FROM fixture_statistics_observations WHERE id = :o"), {"o": obs}).scalar_one() is None
