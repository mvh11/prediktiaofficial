"""CLI de la sync en vivo (M4.6C4) contra PostgreSQL con proveedores falsos: destino, lock,
registro del run, códigos de salida, fail-safe de `all` y recuperación de runs abandonados."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.integrations.exceptions import ProviderAuthError, ProviderResponseError
from app.jobs import live_sync as cli
from app.models import Fixture, LiveSyncRun
from app.repositories import live_sync_repository as runs
from app.schemas.catalog import CompetitionData, SeasonData, TeamData
from tests.conftest import make_competition
from tests.test_live_sync_fixtures import FakeProvider, ft

pytestmark = pytest.mark.db

TARGET = "ep-test.us-east-2.aws.neon.tech:5432/neondb"


class FakeCatalogProvider(FakeProvider):
    def __init__(self, competitions=None, error=None, teams_error=None, **kwargs):
        super().__init__(**kwargs)
        self.competitions = competitions or []
        self.error = error
        self.teams_error = teams_error

    async def get_competitions(self, external_ids):
        if self.error:
            raise self.error
        return list(self.competitions)

    async def get_teams(self, competition_external_id, season):
        if self.teams_error:
            raise self.teams_error
        return [TeamData(external_id=1, name="Equipo 1"), TeamData(external_id=2, name="Equipo 2")]


@pytest.fixture
def wired(db_session, monkeypatch):
    """main() usa la sesión de test y un proveedor falso; nunca la BD real ni la API."""
    state = {"provider": FakeProvider()}
    monkeypatch.setattr(cli, "_configured_target", lambda: TARGET)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(cli, "default_provider", lambda: state["provider"])
    monkeypatch.delenv(cli.TARGET_ENV, raising=False)
    return state


def _runs(db, job_type=None):
    db.expire_all()
    stmt = select(LiveSyncRun).order_by(LiveSyncRun.id)
    if job_type:
        stmt = stmt.where(LiveSyncRun.job_type == job_type)
    return list(db.scalars(stmt))


# --- Destino ---------------------------------------------------------------------------------


def test_missing_target_is_rejected_before_touching_anything(wired, db_session, capsys):
    assert cli.main(["fixtures"]) == 2
    assert "falta --confirm-target" in capsys.readouterr().err
    assert _runs(db_session) == []


def test_wrong_target_is_rejected(wired, db_session, capsys):
    assert cli.main(["fixtures", "--confirm-target", "otro:5432/neondb"]) == 2
    assert "no coincide" in capsys.readouterr().err and _runs(db_session) == []


def test_target_from_environment_variable(wired, db_session, monkeypatch):
    make_competition(db_session, 265)
    wired["provider"] = FakeProvider({265: [ft(1)]})
    monkeypatch.setenv(cli.TARGET_ENV, TARGET)
    assert cli.main(["fixtures"]) == 0


# --- Fixtures ----------------------------------------------------------------------------------


def test_fixtures_job_records_counters_and_details(wired, db_session, capsys):
    make_competition(db_session, 265)
    make_competition(db_session, 39, name="Otra")
    wired["provider"] = FakeProvider({265: [ft(1), ft(2, home=3, away=4)], 39: [ft(3, home=5, away=6)]})
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 0
    (run,) = _runs(db_session, "fixtures")
    assert (run.status, run.lock_scope, run.trigger) == ("completed", "live_sync", "cli")
    assert (run.competitions_attempted, run.competitions_succeeded, run.competitions_failed, run.competitions_skipped) == (2, 2, 0, 0)
    assert (run.fixtures_received, run.fixtures_created, run.fixtures_updated, run.fixtures_unchanged) == (3, 3, 0, 0)
    assert run.teams_created == 6 and run.finished_at is not None
    assert [d["fixtures"] for d in run.details] == [2, 1]
    out = capsys.readouterr().out
    assert f"Destino BD:  {TARGET}" in out and "fixtures: run #" in out


def test_fixtures_job_with_a_failed_competition_exits_1(wired, db_session, monkeypatch):
    make_competition(db_session, 265)
    make_competition(db_session, 39, name="Otra")

    class Partial(FakeProvider):
        async def get_fixtures(self, league, season, date_from=None, date_to=None):
            if league == 265:
                raise ProviderResponseError("api-football", "boom")
            return await super().get_fixtures(league, season, date_from, date_to)

    wired["provider"] = Partial({39: [ft(3, home=5, away=6)]})
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 1
    (run,) = _runs(db_session, "fixtures")
    assert run.status == "completed_with_errors" and run.competitions_failed == 1 and run.error_count == 1


def test_auth_failure_exits_2(wired, db_session):
    make_competition(db_session, 265)

    class Denied(FakeProvider):
        auth_failed = False

        async def get_fixtures(self, *a, **k):
            self.auth_failed = True
            raise ProviderAuthError("api-football", "token")

    wired["provider"] = Denied()
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 2


def test_unexpected_exception_marks_run_failed_and_exits_2(wired, db_session, monkeypatch, capsys):
    make_competition(db_session, 265)

    async def broken(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr("app.services.fixture_sync_service.sync_fixtures", broken)
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 2
    (run,) = _runs(db_session, "fixtures")
    assert run.status == "failed" and run.error_message == "Error inesperado (RuntimeError)"
    err = capsys.readouterr().err
    assert "fixtures: failed (RuntimeError)" in err and "Traceback" not in err


def test_lock_held_by_another_run_exits_2_without_syncing(wired, db_session):
    make_competition(db_session, 265)
    runs.create_run(db_session, job_type="catalog", trigger="cli")  # catálogo en curso
    db_session.commit()
    provider = FakeProvider({265: [ft(1)]})
    wired["provider"] = provider
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 2
    assert provider.calls == [] and db_session.scalar(select(func.count()).select_from(Fixture)) == 0


# --- Catálogo y `all` ----------------------------------------------------------------------


@pytest.fixture
def tracked_265(monkeypatch):
    """El catálogo solo sigue la liga 265 (las demás ligas por defecto no existen en el proveedor falso)."""
    monkeypatch.setattr("app.services.catalog_sync_service.get_settings", lambda: SimpleNamespace(tracked_league_ids=[265]))


def _competition(external_id=265, current=2026):
    return CompetitionData(external_id=external_id, name="Liga Test", type="League", seasons=[SeasonData(year=current, is_current=True)])


def test_catalog_job_records_season_change(wired, tracked_265, db_session):
    make_competition(db_session, 265, current_year=2026)
    wired["provider"] = FakeCatalogProvider(competitions=[_competition(current=2027)])
    assert cli.main(["catalog", "--confirm-target", TARGET]) == 0
    (run,) = _runs(db_session, "catalog")
    assert run.status == "completed"
    assert run.catalog_changes == [{"external_id": 265, "name": "Liga Test", "previous_season": 2026, "season": 2027}]


def test_all_stops_before_fixtures_if_catalog_fails(wired, db_session, capsys):
    make_competition(db_session, 265)
    provider = FakeCatalogProvider(error=ProviderResponseError("api-football", "leagues caído"), fixtures={265: [ft(1)]})
    wired["provider"] = provider
    assert cli.main(["all", "--confirm-target", TARGET]) == 2
    assert [r.status for r in _runs(db_session, "catalog")] == ["failed"]
    assert _runs(db_session, "fixtures") == [] and provider.calls == []  # fail-safe: ni una petición de fixtures
    assert [r.status for r in _runs(db_session, "check")] == ["completed"]  # el check sí se ejecuta
    assert "fail-safe" in capsys.readouterr().err


def test_all_runs_catalog_fixtures_and_check_in_order(wired, tracked_265, db_session):
    make_competition(db_session, 265, current_year=2026)
    wired["provider"] = FakeCatalogProvider(competitions=[_competition()], fixtures={265: [ft(1)]})
    code = cli.main(["all", "--confirm-target", TARGET])
    assert [r.job_type for r in _runs(db_session)] == ["catalog", "fixtures", "check"]
    assert all(r.status == "completed" for r in _runs(db_session))
    assert code in (0, 1)  # el check puede avisar (p. ej. kickoff de prueba ya pasado); nunca 2 aquí
    assert code == cli.FRESHNESS_EXIT[_runs(db_session, "check")[0].freshness["status"]]


# --- Recuperación de runs abandonados -----------------------------------------------------


def test_fail_stale_run(wired, db_session):
    run_id = runs.create_run(db_session, job_type="fixtures", trigger="scheduler")
    db_session.commit()
    assert cli.main(["fixtures", "--confirm-target", TARGET, "--fail-stale-run"]) == 1  # demasiado reciente
    db_session.execute(update(LiveSyncRun).where(LiveSyncRun.id == run_id).values(started_at=func.now() - func.make_interval(0, 0, 0, 0, 0, 120)))
    db_session.commit()
    assert cli.main(["fixtures", "--confirm-target", TARGET, "--fail-stale-run"]) == 0
    (run,) = _runs(db_session)
    assert run.status == "aborted" and "Recuperación administrativa" in run.error_message


def test_parse_args_validation():
    with pytest.raises(SystemExit):
        cli.parse_args(["catalog", "--competition-id", "5"])
    with pytest.raises(SystemExit):
        cli.parse_args(["all", "--fail-stale-run"])
    with pytest.raises(SystemExit):
        cli.parse_args(["fixtures", "--stale-after-minutes", "5"])
    assert cli.parse_args(["check", "--fail-stale-run"]).stale_after_minutes == cli.DEFAULT_STALE_AFTER_MINUTES


def test_counting_provider_counts_requests_and_retries(monkeypatch):
    provider = cli.CountingApiFootballProvider(api_key="k", base_url="https://example.invalid", timeout=1)
    attempts = {"n": 0}

    async def fake_once(self, path, params=None, *, allow_paging=False):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ProviderResponseError("api-football", "503", status_code=503)
        return {"response": [], "errors": [], "paging": {"current": 1, "total": 1}}

    monkeypatch.setattr(cli.ApiFootballProvider, "_get_once", fake_once)

    async def no_sleep(_s):
        return None

    monkeypatch.setattr("app.integrations.football.api_football._sleep", no_sleep)
    asyncio.run(provider._get("/fixtures", {"league": 1, "season": 2026}))
    assert (provider.logical_calls, provider.http_requests, provider.retries) == (1, 2, 1)
