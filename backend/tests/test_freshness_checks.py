"""Check de frescura (M4.6C5) contra PostgreSQL con datos sintéticos: umbrales F1–F9, exclusión
de temporadas dormant y que el check solo lee."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text, update

from app.models import Fixture, LiveSyncRun, Season
from app.repositories import fixture_repository
from app.repositories import live_sync_repository as runs
from app.services.freshness_checks import run_checks
from tests.conftest import make_competition, make_evidence, make_fixture_data

pytestmark = pytest.mark.db


@pytest.fixture
def now(db_session):
    return runs.now(db_session)  # mismo reloj que el last_seen_at que escribe el upsert


def _add(db, sid, *specs):
    """specs: (external_id, status, horas desde el kickoff)."""
    data = [make_fixture_data(e, status=s, kickoff_at=runs.now(db) - timedelta(hours=h)) for e, s, h in specs]
    team_ids = fixture_repository.ensure_teams(db, [f.home_team for f in data] + [f.away_team for f in data], "api-football")
    fixture_repository.upsert_fixtures(db, sid, data, team_ids, "api-football", make_evidence())
    db.flush()


def _fixtures_run(db, finished_at, **values):
    started = finished_at - timedelta(minutes=1)
    cols = {"job_type": "fixtures", "trigger": "cli", "status": "completed", "lock_scope": "live_sync", "started_at": started, "finished_at": finished_at, **values}
    db.execute(text(f"INSERT INTO live_sync_runs ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)})"), cols)
    db.flush()


def _check(report, check_id):
    return next(c for c in report["checks"] if c["id"] == check_id)


def test_empty_database_only_warns_about_missing_sync(db_session, now):
    report = run_checks(db_session, now)
    assert report["status"] == "WARNING" and report["eligible_seasons"] == 0
    assert _check(report, "F6_last_fixtures_sync")["status"] == "WARNING"
    assert [c["id"] for c in report["checks"] if c["status"] not in ("PASS", "INFO")] == ["F6_last_fixtures_sync"]


@pytest.mark.parametrize(
    ("check_id", "status", "hours", "expected"),
    [
        ("F1_ns_overdue", "NS", 2, "PASS"),
        ("F1_ns_overdue", "TBD", 5, "WARNING"),
        ("F1_ns_overdue", "NS", 30, "ERROR"),
        ("F2_live_stale", "2H", 3, "PASS"),
        ("F2_live_stale", "HT", 6, "WARNING"),
        ("F2_live_stale", "1H", 30, "ERROR"),
        ("F3_suspended", "SUSP", 10, "PASS"),
        ("F3_suspended", "INT", 30, "WARNING"),
    ],
)
def test_fixture_age_thresholds(db_session, now, check_id, status, hours, expected):
    _, sid = make_competition(db_session, 265)
    _add(db_session, sid, (1, status, hours))
    found = _check(run_checks(db_session, now), check_id)
    assert found["status"] == expected
    assert found["samples"] == ([] if expected == "PASS" else [1])


@pytest.mark.parametrize(("hours", "expected"), [(1, "PASS"), (3, "WARNING"), (30, "ERROR")])
def test_last_seen_thresholds(db_session, now, hours, expected):
    _, sid = make_competition(db_session, 265)
    _add(db_session, sid, (1, "FT", 48))
    db_session.execute(text("UPDATE fixture_provider_mappings SET last_seen_at = :t"), {"t": now - timedelta(hours=hours)})
    assert _check(run_checks(db_session, now), "F4_last_seen_stale")["status"] == expected


def test_current_season_without_fixtures(db_session, now):
    _, cup = make_competition(db_session, 1, name="Copa")
    report = run_checks(db_session, now)
    assert _check(report, "F5_current_without_fixtures")["status"] == "WARNING"
    # Una liga ya empezada sin partidos es un error
    cid, league = make_competition(db_session, 2, name="Liga")
    db_session.execute(text("UPDATE competitions SET type = 'League' WHERE id = :id"), {"id": cid})
    db_session.execute(update(Season).where(Season.id == league).values(start_date=now.date() - timedelta(days=10)))
    found = _check(run_checks(db_session, now), "F5_current_without_fixtures")
    assert found["status"] == "ERROR" and found["count"] == 2


@pytest.mark.parametrize(
    ("age_hours", "values", "f6", "f7"),
    [
        (1, {}, "PASS", "PASS"),
        (3, {}, "WARNING", "PASS"),
        (30, {}, "ERROR", "PASS"),
        (1, {"status": "completed_with_errors", "competitions_failed": 2, "error_count": 2}, "PASS", "WARNING"),
        (1, {"rate_limited": True}, "PASS", "WARNING"),
        (1, {"auth_failed": True}, "PASS", "ERROR"),
        (1, {"status": "failed"}, "WARNING", "PASS"),  # un run failed no cuenta como sync terminada
    ],
)
def test_last_sync_and_provider_failures(db_session, now, age_hours, values, f6, f7):
    _fixtures_run(db_session, now - timedelta(hours=age_hours), **values)
    report = run_checks(db_session, now)
    assert (_check(report, "F6_last_fixtures_sync")["status"], _check(report, "F7_provider_failures")["status"]) == (f6, f7)


def test_info_checks_do_not_change_the_global_status(db_session, now):
    _fixtures_run(db_session, now)
    cid, sid = make_competition(db_session, 265)
    _add(db_session, sid, (1, "PST", 24 * 10), (2, "FT", 24 * 10))
    # Temporada pasada con datos y sin backfill completed: pendiente de conciliación (solo aviso)
    old = Season(competition_id=cid, year=2024, is_current=False, end_date=date(2025, 5, 30))
    db_session.add(old)
    db_session.flush()
    _add_to_season = make_fixture_data(3, status="FT", season=2024)
    team_ids = fixture_repository.ensure_teams(db_session, [_add_to_season.home_team, _add_to_season.away_team], "api-football")
    fixture_repository.upsert_fixtures(db_session, old.id, [_add_to_season], team_ids, "api-football", make_evidence())
    report = run_checks(db_session, now)
    assert (_check(report, "F8_old_postponed")["status"], _check(report, "F8_old_postponed")["samples"]) == ("INFO", [1])
    assert (_check(report, "F9_pending_reconciliation")["status"], _check(report, "F9_pending_reconciliation")["count"]) == ("INFO", 1)
    assert report["status"] == "PASS" and report["warnings"] == report["errors"] == 0


def test_dormant_current_seasons_are_not_checked(db_session, now):
    _fixtures_run(db_session, now)
    _, closed = make_competition(db_session, 4, name="Torneo cerrado", current_year=2024)
    _add(db_session, closed, (1, "FT", 24 * 400))
    db_session.execute(update(Season).where(Season.id == closed).values(start_date=date(2024, 6, 14), end_date=date(2024, 7, 14)))
    db_session.execute(text("UPDATE fixture_provider_mappings SET last_seen_at = :t"), {"t": now - timedelta(days=300)})
    _, empty = make_competition(db_session, 5, name="Copa extinta", current_year=2019)
    db_session.execute(update(Season).where(Season.id == empty).values(start_date=date(2019, 6, 1), end_date=date(2019, 7, 1)))
    report = run_checks(db_session, now)
    assert report["eligible_seasons"] == 0 and report["status"] == "PASS"


def test_check_only_reads(db_session, now):
    _, sid = make_competition(db_session, 265)
    _add(db_session, sid, (1, "NS", 30))
    db_session.flush()
    before = (db_session.scalar(select(func.count()).select_from(Fixture)), db_session.scalar(select(func.count()).select_from(LiveSyncRun)))
    run_checks(db_session, now)
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    assert before == (db_session.scalar(select(func.count()).select_from(Fixture)), db_session.scalar(select(func.count()).select_from(LiveSyncRun)))
