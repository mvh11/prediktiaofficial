"""Migración 0005 (season_backfill_runs): upgrade/downgrade y constraints."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import alembic_run, make_competition

pytestmark = pytest.mark.db


def test_upgrade_and_downgrade_0005(migrated_db):
    from app.db.database import engine

    try:
        alembic_run("downgrade", "0004")
        with engine.connect() as conn:
            assert "season_backfill_runs" not in inspect(conn).get_table_names()
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004"
        alembic_run("upgrade", "0005")
        with engine.connect() as conn:
            assert "season_backfill_runs" in inspect(conn).get_table_names()
            indexes = {i["name"] for i in inspect(conn).get_indexes("season_backfill_runs")}
            assert "uq_season_backfill_runs_one_running" in indexes
    finally:
        alembic_run("upgrade", "head")


def _insert(db, **overrides):
    values = dict(competition_id=None, requested_year=2025, provider="api-football", status="running", is_dry_run=False, is_refresh=False)
    values.update(overrides)
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    with db.begin_nested():
        return db.execute(text(f"INSERT INTO season_backfill_runs ({cols}) VALUES ({params}) RETURNING id"), values).scalar_one()


@pytest.fixture
def competition_id(db_session) -> int:
    cid, _ = make_competition(db_session, 39)
    return cid


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "done"},  # estado desconocido
        {"status": "completed"},  # terminado sin finished_at
        {"status": "running", "finished_at": "2026-10-04T10:00:00+00"},  # en curso con finished_at
        {"status": "completed", "is_dry_run": True, "finished_at": "2099-01-01T00:00:00+00"},  # dry-run como importación
        {"status": "dry_run_completed", "is_dry_run": False, "finished_at": "2099-01-01T00:00:00+00"},
        {"received_count": -1},
        {"provider": "nadie"},  # FK providers
    ],
)
def test_constraints_reject_invalid_runs(db_session, competition_id, overrides):
    with pytest.raises(IntegrityError):
        _insert(db_session, competition_id=competition_id, **overrides)


def test_fk_competition_and_season(db_session, competition_id):
    with pytest.raises(IntegrityError):
        _insert(db_session, competition_id=999_999_999)
    with pytest.raises(IntegrityError):
        _insert(db_session, competition_id=competition_id, season_id=999_999_999)


def test_only_one_running_run_per_pair(db_session, competition_id):
    _insert(db_session, competition_id=competition_id)
    with pytest.raises(IntegrityError):
        _insert(db_session, competition_id=competition_id)
    _insert(db_session, competition_id=competition_id, requested_year=2024)  # otro par: permitido


def test_checks_json_roundtrip(db_session, competition_id):
    checks = '[{"id": "Q9", "severity": "warning", "passed": false, "count": 1, "detail": "x", "samples": [1593527]}]'
    run_id = _insert(db_session, competition_id=competition_id)
    db_session.execute(
        text("UPDATE season_backfill_runs SET status='blocked', finished_at=now(), checks=CAST(:c AS jsonb) WHERE id=:id"),
        {"c": checks, "id": run_id},
    )
    stored = db_session.execute(text("SELECT checks FROM season_backfill_runs WHERE id=:id"), {"id": run_id}).scalar_one()
    assert stored[0]["id"] == "Q9" and stored[0]["samples"] == [1593527]
