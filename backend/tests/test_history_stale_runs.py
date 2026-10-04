"""Recuperación explícita de runs abandonados en 'running' y rango de Q1 (PostgreSQL real)."""

import asyncio

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.models import Fixture, SeasonBackfillRun
from app.repositories import backfill_repository as runs
from app.repositories import catalog_repository
from app.schemas.catalog import SeasonData
from app.services import history_backfill_service as service
from tests.conftest import make_competition
from tests.test_history_backfill import NOW, YEAR, FakeHistoryProvider, fx

pytestmark = pytest.mark.db


@pytest.fixture
def pair(db_session) -> int:
    competition_id, _ = make_competition(db_session, 39, name="Premier Test", current_year=2026)
    catalog_repository.upsert_seasons(db_session, competition_id, [SeasonData(year=YEAR, is_current=False)])
    db_session.commit()  # como en producción: datos ya confirmados antes de la operación administrativa
    return competition_id


def _run(db, competition_id: int, status: str = "running", minutes_ago: int = 0, year: int = YEAR) -> int:
    run_id = runs.create_run(
        db,
        competition_id=competition_id,
        requested_year=year,
        provider="api-football",
        is_dry_run=status == "dry_run_completed",
        is_refresh=False,
        season_id=None,
    )
    db.execute(
        update(SeasonBackfillRun)
        .where(SeasonBackfillRun.id == run_id)
        .values(started_at=func.now() - func.make_interval(0, 0, 0, 0, 0, minutes_ago))
    )
    if status != "running":
        runs.finish_run(db, run_id, status=status)
    db.commit()  # el run existe confirmado, como cuando lo dejó otro proceso
    return run_id


def _row(db, run_id: int) -> SeasonBackfillRun:
    db.expire_all()
    return db.get(SeasonBackfillRun, run_id)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_no_running_run_changes_nothing(db_session, pair):
    before = _count(db_session, SeasonBackfillRun)
    recovery = service.fail_stale_run(db_session, pair, YEAR, 60)
    assert recovery.outcome == "not_found" and recovery.run_id is None
    assert _count(db_session, SeasonBackfillRun) == before


def test_old_running_run_is_marked_failed(db_session, pair):
    run_id = _run(db_session, pair, minutes_ago=180)
    recovery = service.fail_stale_run(db_session, pair, YEAR, 120)
    row = _row(db_session, run_id)
    assert recovery.outcome == "recovered" and recovery.run_id == run_id
    assert row.status == "failed" and row.finished_at is not None
    assert "Recuperación administrativa" in row.error_message  # no se borra el run


def test_recent_running_run_is_rejected_and_untouched(db_session, pair):
    run_id = _run(db_session, pair, minutes_ago=5)
    recovery = service.fail_stale_run(db_session, pair, YEAR, 120)
    row = _row(db_session, run_id)
    assert recovery.outcome == "too_recent" and row.status == "running" and row.finished_at is None


@pytest.mark.parametrize("status", ["completed", "blocked", "dry_run_completed", "failed"])
def test_finished_runs_are_never_touched(db_session, pair, status):
    run_id = _run(db_session, pair, status=status, minutes_ago=600)
    before = _row(db_session, run_id)
    snapshot = (before.status, before.finished_at, before.error_message)
    recovery = service.fail_stale_run(db_session, pair, YEAR, 1)
    after = _row(db_session, run_id)
    assert recovery.outcome == "not_found"
    assert (after.status, after.finished_at, after.error_message) == snapshot


def test_recovery_does_not_start_a_backfill(db_session, pair):
    _run(db_session, pair, minutes_ago=300)
    fixtures_before, runs_before = _count(db_session, Fixture), _count(db_session, SeasonBackfillRun)
    service.fail_stale_run(db_session, pair, YEAR, 120)
    assert _count(db_session, Fixture) == fixtures_before
    assert _count(db_session, SeasonBackfillRun) == runs_before  # ningún run nuevo


def test_after_recovery_a_new_run_can_start(db_session, pair):
    _run(db_session, pair, minutes_ago=300)
    # Mientras sigue 'running', la unicidad impide otro run del par
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _run(db_session, pair, minutes_ago=0)
    assert service.fail_stale_run(db_session, pair, YEAR, 120).outcome == "recovered"
    result = asyncio.run(service.run_backfill(db_session, pair, YEAR, provider=FakeHistoryProvider([fx(1)]), now=NOW))
    assert result.status == "completed"


def test_running_uniqueness_still_enforced(db_session, pair):
    _run(db_session, pair, minutes_ago=0)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _run(db_session, pair, minutes_ago=0)
    _run(db_session, pair, minutes_ago=0, year=2024)  # otro par: permitido


def test_recovery_query_locks_the_row(db_session, pair):
    run_id = _run(db_session, pair, minutes_ago=300)
    statements = []
    from sqlalchemy import event

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        locked = runs.lock_running_run(db_session, pair, YEAR)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert locked.id == run_id
    assert any("FOR UPDATE" in s for s in statements)


def test_invalid_threshold_rejected(db_session, pair):
    with pytest.raises(ValueError):
        service.fail_stale_run(db_session, pair, YEAR, 0)


# --- Q1 con rango a través del servicio ----------------------------------------------------


def test_q1_range_blocks_below_min_and_records_range(db_session, pair):
    provider = FakeHistoryProvider([fx(1), fx(2, home=3, away=4)])
    result = asyncio.run(service.run_backfill(db_session, pair, YEAR, provider=provider, now=NOW, expected_range=(370, 390)))
    q1 = next(c for c in result.checks if c.id == "Q1")
    assert result.status == "blocked" and not q1.passed and "370–390" in q1.detail
    assert _count(db_session, Fixture) == 0
    stored = _row(db_session, result.run_id).checks
    assert any(c["id"] == "Q1" and "370–390" in c["detail"] for c in stored)


def test_q1_range_exact_bounds_pass(db_session, pair):
    provider = FakeHistoryProvider([fx(1), fx(2, home=3, away=4)])
    result = asyncio.run(
        service.run_backfill(db_session, pair, YEAR, provider=provider, now=NOW, expected_range=(2, 2), dry_run=True)
    )
    assert result.status == "dry_run_completed"
    assert next(c for c in result.checks if c.id == "Q1").passed
