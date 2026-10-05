"""Migración 0006 (live_sync_runs): upgrade/downgrade, constraints y lock por índice único parcial."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.repositories import live_sync_repository as runs
from tests.conftest import alembic_run

pytestmark = pytest.mark.db


def test_upgrade_and_downgrade_0006(migrated_db):
    from app.db.database import engine

    try:
        alembic_run("downgrade", "0005")
        with engine.connect() as conn:
            assert "live_sync_runs" not in inspect(conn).get_table_names()
            assert "season_backfill_runs" in inspect(conn).get_table_names()  # 0005 intacta
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005"
        alembic_run("upgrade", "0006")
        with engine.connect() as conn:
            assert "live_sync_runs" in inspect(conn).get_table_names()
            indexes = {i["name"] for i in inspect(conn).get_indexes("live_sync_runs")}
            assert {"uq_live_sync_runs_one_running", "ix_live_sync_runs_job_started"} <= indexes
            checks = {c["name"] for c in inspect(conn).get_check_constraints("live_sync_runs")}
            assert {
                "ck_live_sync_runs_job_type",
                "ck_live_sync_runs_trigger",
                "ck_live_sync_runs_status",
                "ck_live_sync_runs_lock_scope",
                "ck_live_sync_runs_scope_by_job",
                "ck_live_sync_runs_finished",
                "ck_live_sync_runs_order",
                "ck_live_sync_runs_counters",
                "ck_live_sync_runs_fixture_counts",
            } <= checks
    finally:
        alembic_run("upgrade", "head")


def _insert(db, **overrides):
    values = dict(job_type="fixtures", trigger="cli", status="running", lock_scope="live_sync")
    values.update(overrides)
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    with db.begin_nested():
        return db.execute(text(f"INSERT INTO live_sync_runs ({cols}) VALUES ({params}) RETURNING id"), values).scalar_one()


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_type": "odds"},
        {"trigger": "cron"},
        {"status": "done"},
        {"lock_scope": "otro"},
        {"lock_scope": "freshness"},  # fixtures fuera del lock live_sync
        {"job_type": "check"},  # check con el lock live_sync
        {"status": "completed"},  # terminado sin finished_at
        {"finished_at": "2026-10-05T10:00:00+00"},  # en curso con finished_at
        {"status": "completed", "started_at": "2026-10-05T10:00:00+00", "finished_at": "2026-10-05T09:00:00+00"},
        {"provider_requests": -1},
        {"fixtures_received": 3, "fixtures_created": 1, "fixtures_updated": 1, "fixtures_unchanged": 0},  # 3 != 2
    ],
)
def test_constraints_reject_invalid_runs(db_session, overrides):
    with pytest.raises(IntegrityError):
        _insert(db_session, **overrides)


def test_valid_runs_are_accepted(db_session):
    _insert(db_session, status="completed", finished_at="2099-01-01T00:00:00+00", fixtures_received=3, fixtures_created=1, fixtures_updated=1, fixtures_unchanged=1)
    _insert(db_session, job_type="check", lock_scope="freshness")


def test_only_one_running_per_lock_scope(db_session):
    _insert(db_session)
    with pytest.raises(IntegrityError) as excinfo:
        _insert(db_session, job_type="catalog")  # catálogo y fixtures comparten ámbito
    assert runs.is_lock_conflict(excinfo.value)
    _insert(db_session, job_type="check", lock_scope="freshness")  # otro ámbito: permitido
    _insert(db_session, status="failed", finished_at="2099-01-01T00:00:00+00")  # no running: permitido
