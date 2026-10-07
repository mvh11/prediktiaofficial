"""Integración Data Integrity + Modular M4.3: las dos líneas conviven en la misma sync.

Con el adapter real de API-Football (solo se sustituye get_json, o httpx.AsyncClient en el test
del job) y PostgreSQL: proveedor inyectado con su ciclo de vida en el llamador, un cliente HTTP
por run, reintentos y RequestStats, clasificación de fallos de sync_failures junto al hook de
auditoría, sin transacción de BD durante el HTTP y el contrato UpsertCounts hasta el resultado.
"""

import asyncio
import logging
from contextlib import nullcontext
from datetime import date

import httpx
import pytest
from sqlalchemy import select, text

from app.db.database import engine
from app.integrations import http
from app.integrations.exceptions import ProviderAuthError, ProviderResponseError
from app.integrations.football import api_football
from app.integrations.football.api_football import ApiFootballProvider
from app.jobs import live_sync as cli
from app.models import LiveSyncRun
from app.repositories import fixture_repository
from app.services import catalog_sync_service, fixture_sync_service
from tests.conftest import make_competition

pytestmark = pytest.mark.db

P = "api-football"
TODAY = date(2026, 10, 5)
LEAGUES = (265, 39, 140)  # todas en TRACKED_LEAGUE_IDS; la sync las recorre en este orden
EMPTY = {"errors": [], "results": 0, "paging": {"current": 1, "total": 1}, "response": []}
TARGET = "127.0.0.1:5432/integration_job_target"


def raw_fixture(fixture_id, *, status="FT", goals=(1, 0), date_iso="2026-09-01T20:00:00+00:00", home=1, away=2):
    """Un partido tal como lo devuelve GET /fixtures."""
    return {
        "fixture": {
            "id": fixture_id,
            "referee": None,
            "date": date_iso,
            "venue": {"name": "Estadio", "city": "Ciudad"},
            "status": {"long": "x", "short": status, "elapsed": None},
        },
        "league": {"id": 265, "season": 2026, "round": "Regular Season - 1"},
        "teams": {"home": {"id": home, "name": f"Equipo {home}"}, "away": {"id": away, "name": f"Equipo {away}"}},
        "goals": {"home": goals[0], "away": goals[1]},
        "score": {
            "halftime": {"home": None, "away": None},
            "fulltime": {"home": goals[0], "away": goals[1]},
            "extratime": {"home": None, "away": None},
            "penalty": {"home": None, "away": None},
        },
    }


def page(*items):
    return {**EMPTY, "results": len(items), "response": list(items)}


class RoutedApi:
    """get_json falso por (ruta, liga). Registra el cliente HTTP y si había transacción de BD abierta."""

    def __init__(self, db) -> None:
        self.db = db
        self.queues: dict[tuple[str, int | None], list] = {}
        self.calls: list[tuple[str, int | None]] = []
        self.clients: list = []
        self.in_transaction: list[bool] = []

    def on(self, path, league, *items) -> None:
        self.queues[(path, league)] = list(items)

    async def get_json(self, *, path, params=None, client=None, **_kwargs):
        key = (path, (params or {}).get("league"))
        self.calls.append(key)
        self.clients.append(client)
        self.in_transaction.append(self.db.in_transaction())
        queue = self.queues.get(key)
        item = queue.pop(0) if queue else EMPTY
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def api(db_session, monkeypatch) -> RoutedApi:
    routed = RoutedApi(db_session)
    monkeypatch.setattr(api_football, "get_json", routed.get_json)

    def no_default_provider():
        raise AssertionError("con un proveedor inyectado la sync no debe crear otro")

    monkeypatch.setattr(fixture_sync_service, "get_football_provider", no_default_provider)
    monkeypatch.setattr(catalog_sync_service, "get_football_provider", no_default_provider)
    return routed


@pytest.fixture
def competitions(db_session) -> dict[int, int]:
    ids = {}
    for external_id, name in zip(LEAGUES, ("Liga A", "Liga B", "Liga C")):
        ids[external_id], _ = make_competition(db_session, external_id, name=name)
    db_session.commit()
    return ids


def _provider(api_key="test-key") -> ApiFootballProvider:
    return ApiFootballProvider(api_key=api_key, base_url="https://example.invalid", timeout=1)


def _run_injected(db, provider, audited=None, **kwargs):
    """Como app.jobs.live_sync: el llamador abre el run del proveedor y lo inyecta en la sync."""

    def audit(session, result):
        assert session is db
        audited.append(result.model_copy())

    async def run():
        async with provider:
            result = await fixture_sync_service.sync_fixtures(
                db, provider=provider, on_competition=audit if audited is not None else None, today=TODAY, **kwargs
            )
            # La sync no cierra un proveedor inyectado: el run sigue abierto hasta que sale el llamador
            assert provider._client is not None and not provider._client.is_closed
            return result

    result = asyncio.run(run())
    return result, {r.competition_id: r for r in result.competitions}


# --- Ciclo de vida, reintentos y métricas con proveedor inyectado -------------------------------


def test_injected_provider_one_client_retries_and_stats_without_db_transaction(db_session, competitions, api, retry_sleeps, caplog):
    api.on("/fixtures", 265, ProviderResponseError(P, "Respuesta HTTP 503", 503), page(raw_fixture(1), raw_fixture(2, home=3, away=4)))
    api.on("/fixtures", 39, page(raw_fixture(3, home=5, away=6)))
    provider = _provider()
    audited = []
    with caplog.at_level(logging.INFO, logger=fixture_sync_service.__name__):
        result, by_id = _run_injected(db_session, provider, audited)

    # Un solo cliente para todo el run (reintento incluido), cerrado por el llamador al salir
    assert len(api.clients) == 4 and api.clients[0] is not None and all(c is api.clients[0] for c in api.clients)
    assert api.clients[0].is_closed
    # Ninguna petición (tampoco el reintento) con una transacción de BD abierta
    assert api.in_transaction == [False, False, False, False]
    # Reintentos de DI y RequestStats
    assert retry_sleeps == [1.0]
    stats = provider.request_stats
    assert (stats.calls, stats.attempts, stats.retries, stats.failed_attempts) == (3, 4, 1, 1)
    lines = [r.getMessage() for r in caplog.records if r.name == fixture_sync_service.__name__]
    assert "peticiones 2 (reintentos 1, fallidas 1)" in next(m for m in lines if m.startswith("Sync fixtures · Liga A"))
    assert "peticiones 4 (reintentos 1, fallidas 1)" in next(m for m in lines if m.startswith("Sync fixtures · total"))
    # Contadores de Modular en el resultado y en la auditoría
    a = by_id[competitions[265]]
    assert (a.fixtures, a.created, a.updated, a.unchanged, a.teams_created, a.error) == (2, 2, 0, 0, 4, None)
    assert result.fixtures_synced == 3
    assert [r.competition_id for r in audited] == [competitions[l] for l in LEAGUES]


def test_upsert_counts_semantics_reach_the_sync_result(db_session, competitions, api, retry_sleeps):
    first = page(raw_fixture(1), raw_fixture(2, home=3, away=4), raw_fixture(2, home=3, away=4))  # duplicado
    api.on("/fixtures", 265, first, page(raw_fixture(1), raw_fixture(2, home=3, away=4, goals=(2, 2))))
    _, by_id = _run_injected(db_session, _provider())
    a = by_id[competitions[265]]
    # DI: `fixtures` eran los partidos recibidos sin duplicados (len(rows)) = UpsertCounts.received
    assert (a.fixtures, a.created, a.updated, a.unchanged) == (2, 2, 0, 0)

    result, by_id = _run_injected(db_session, _provider())
    a = by_id[competitions[265]]
    assert (a.fixtures, a.created, a.updated, a.unchanged) == (2, 0, 1, 1)
    assert a.fixtures == a.created + a.updated + a.unchanged
    assert result.fixtures_synced == 2


# --- Clasificación de fallos (DI) junto al hook de auditoría (Modular) -------------------------


def test_auth_failure_aborts_remaining_and_every_competition_is_audited(db_session, competitions, api, retry_sleeps):
    api.on("/fixtures", 265, ProviderAuthError(P, "API key inválida o sin permisos", 401))
    audited = []
    _, by_id = _run_injected(db_session, _provider(), audited)

    assert api.calls == [("/fixtures", 265)]  # no se gastan peticiones en las demás
    for league in (39, 140):
        assert "se detuvo la sync porque el proveedor rechazó las credenciales" in by_id[competitions[league]].error
    assert [r.competition_id for r in audited] == [competitions[l] for l in LEAGUES]
    assert all(r.error for r in audited)


def test_provider_not_configured_aborts_with_injected_provider(db_session, competitions, api):
    provider = _provider(api_key="")
    audited = []
    _, by_id = _run_injected(db_session, provider, audited)

    assert api.calls == [] and provider.request_stats.attempts == 0
    assert "Falta API_FOOTBALL_KEY" in by_id[competitions[265]].error
    for league in (39, 140):
        assert "se detuvo la sync porque el proveedor no está configurado" in by_id[competitions[league]].error
    assert len(audited) == 3


def test_local_competition_failure_stays_isolated_with_hook(db_session, competitions, api, retry_sleeps):
    api.on("/fixtures", 265, ProviderResponseError(P, "Respuesta HTTP 404", 404))
    api.on("/fixtures", 39, page(raw_fixture(1)))
    audited = []
    _, by_id = _run_injected(db_session, _provider(), audited)

    assert by_id[competitions[265]].error and by_id[competitions[39]].error is None
    assert by_id[competitions[39]].created == 1 and by_id[competitions[140]].error is None
    assert ("/fixtures", 39) in api.calls and ("/fixtures", 140) in api.calls


def _terminate_own_connection(db) -> None:
    pid = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
    with engine.connect() as killer:
        killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    db.execute(text("SELECT 1"))  # OperationalError con la conexión invalidada (57P01)


def test_db_connection_lost_aborts_remaining_zeroes_counters_and_audits(db_session, competitions, api, retry_sleeps, monkeypatch):
    api.on("/fixtures", 265, page(raw_fixture(1)))
    original = fixture_repository.upsert_fixtures
    pending = [True]

    def sabotaged(db, *args, **kwargs):
        counts = original(db, *args, **kwargs)
        if pending:
            pending.clear()
            _terminate_own_connection(db)
        return counts

    monkeypatch.setattr(fixture_repository, "upsert_fixtures", sabotaged)
    audited = []
    result, by_id = _run_injected(db_session, _provider(), audited)

    a = by_id[competitions[265]]
    assert "conexión perdida" in a.error
    assert (a.fixtures, a.created, a.updated, a.unchanged, a.teams_created) == (0, 0, 0, 0, 0)
    assert api.calls == [("/fixtures", 265)]
    for league in (39, 140):
        assert by_id[competitions[league]].error == "No sincronizada: se detuvo la sync porque se perdió la conexión con la BD"
    assert [r.competition_id for r in audited] == [competitions[l] for l in LEAGUES]
    assert result.fixtures_synced == 0


def test_season_recheck_with_real_adapter(db_session, competitions, api, retry_sleeps, monkeypatch):
    from app.repositories import catalog_repository
    from app.schemas.catalog import SeasonData

    def change_season():
        # La sync del catálogo cambia la temporada actual mientras se descarga /fixtures
        catalog_repository.clear_current_flag(db_session, competitions[265])
        catalog_repository.upsert_seasons(db_session, competitions[265], [SeasonData(year=2027, is_current=True)])
        db_session.commit()
        return page(raw_fixture(1))

    original = api.get_json

    async def get_json(**kwargs):
        if (kwargs.get("params") or {}).get("league") == 265:
            await original(**kwargs)
            return change_season()
        return await original(**kwargs)

    monkeypatch.setattr(api_football, "get_json", get_json)
    _, by_id = _run_injected(db_session, _provider())
    a = by_id[competitions[265]]
    assert a.skipped and a.skipped.startswith("season changed") and a.fixtures == 0
    assert by_id[competitions[39]].skipped is None


# --- Catálogo con proveedor inyectado ---------------------------------------------------------


def test_catalog_injected_provider_previous_season_and_stats(db_session, api, retry_sleeps, caplog, monkeypatch):
    monkeypatch.setattr(catalog_sync_service, "get_settings", lambda: type("S", (), {"tracked_league_ids": [265]})())
    make_competition(db_session, 265, name="Liga A", current_year=2025)
    db_session.commit()
    api.on("/leagues", None, page({
        "league": {"id": 265, "name": "Liga A", "type": "League"},
        "country": {"name": "Chile", "code": "CL"},
        "seasons": [{"year": 2025, "current": False}, {"year": 2026, "current": True}],
    }))
    provider = _provider()

    async def run():
        async with provider:
            result = await catalog_sync_service.sync_catalog(db_session, provider=provider)
            assert not provider._client.is_closed
            return result

    with caplog.at_level(logging.INFO, logger=catalog_sync_service.__name__):
        result = asyncio.run(run())
    (comp,) = result.competitions
    assert (comp.previous_season, comp.season, comp.error) == (2025, 2026, None)
    assert len(api.clients) == 2 and api.clients[0] is api.clients[1] and api.clients[0].is_closed
    assert api.in_transaction == [False, False]
    assert provider.request_stats.attempts == 2
    assert any(r.getMessage().startswith("Sync catálogo · total") and "peticiones 2" in r.getMessage() for r in caplog.records)


# --- Adapter: estados no jugados (Modular) + validación estructural (DI) ------------------------


def test_adapter_unplayed_status_is_normalized_and_still_requires_kickoff(api):
    provider = _provider()
    api.on("/fixtures", 265, page(raw_fixture(1, status="Canc", goals=(0, 0))))
    (parsed,) = asyncio.run(provider.get_fixtures(265, 2026))
    assert parsed.status_short == "CANC"
    assert (parsed.home_goals, parsed.away_goals, parsed.fulltime_home, parsed.fulltime_away) == (None, None, None, None)

    api.on("/fixtures", 265, page(raw_fixture(2, status="Canc", date_iso=None)))
    with pytest.raises(ProviderResponseError, match="sin fixture.date"):
        asyncio.run(provider.get_fixtures(265, 2026))


# --- Job live_sync: el run es dueño del proveedor (un cliente HTTP por job) -----------------------


class ClientSpy:
    """Sustituye httpx.AsyncClient: cada cliente creado responde con `handler` y se registra."""

    def __init__(self) -> None:
        self.clients: list[httpx.AsyncClient] = []
        self.requests: list[tuple[int, str]] = []
        self.handler = lambda request: httpx.Response(200, json=EMPTY)

    def factory(self, **kwargs) -> httpx.AsyncClient:
        index = len(self.clients)

        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append((index, request.url.path))
            return self.handler(request)

        client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(respond), **kwargs)
        self.clients.append(client)
        return client


REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def job(db_session, monkeypatch, retry_sleeps):
    """main() con la sesión de test y el CountingApiFootballProvider real (HTTP falso, sin red)."""
    spy = ClientSpy()
    state = {"spy": spy, "api_key": "k", "providers": []}
    monkeypatch.setattr(http.httpx, "AsyncClient", spy.factory)
    monkeypatch.setattr(cli, "_configured_target", lambda: TARGET)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: nullcontext(db_session))

    def default_provider():
        provider = cli.CountingApiFootballProvider(api_key=state["api_key"], base_url="https://example.invalid", timeout=1)
        state["providers"].append(provider)
        return provider

    monkeypatch.setattr(cli, "default_provider", default_provider)
    monkeypatch.delenv(cli.TARGET_ENV, raising=False)
    return state


def _fixtures_run(db):
    db.expire_all()
    (run,) = db.scalars(select(LiveSyncRun).where(LiveSyncRun.job_type == "fixtures"))
    return run


def test_live_sync_fixtures_job_uses_one_client_for_the_whole_run(db_session, competitions, job):
    spy = job["spy"]
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 0
    assert len(spy.clients) == 1 and spy.clients[0].is_closed
    assert [i for i, _ in spy.requests] == [0, 0, 0]
    run = _fixtures_run(db_session)
    assert (run.status, run.provider_requests, run.provider_retries, run.competitions_attempted) == ("completed", 3, 0, 3)


# --- Job live_sync: contrato de salida. La clasificación interna (sync_failures) y el código de
# salida del proceso son contratos distintos y se comprueban los dos ---------------------------


def test_live_sync_fixtures_job_without_api_key_aborts_run_and_exits_2(db_session, competitions, job):
    job["api_key"] = ""
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 2

    # Interno: la primera competición falla por configuración y el resto se corta (no se intenta)
    (provider,) = job["providers"]
    assert provider.config_failed and not provider.auth_failed
    assert provider.logical_calls == 1 and provider.http_requests == 0 and job["spy"].requests == []
    run = _fixtures_run(db_session)
    assert run.status == "completed_with_errors" and run.competitions_failed == 3
    assert (run.provider_requests, run.auth_failed, run.rate_limited) == (0, False, False)
    first, *rest = run.details
    assert "Falta API_FOOTBALL_KEY" in first["error"]
    assert len(rest) == 2 and all("se detuvo la sync porque el proveedor no está configurado" in d["error"] for d in rest)


def test_live_sync_catalog_job_without_api_key_exits_2(db_session, job):
    job["api_key"] = ""
    assert cli.main(["catalog", "--confirm-target", TARGET]) == 2
    (provider,) = job["providers"]
    assert provider.config_failed and job["spy"].requests == []


def test_live_sync_ordinary_competition_failure_still_exits_1(db_session, competitions, job):
    def handler(request):
        if request.url.params.get("league") == "265":
            return httpx.Response(404, json={})
        return httpx.Response(200, json=EMPTY)

    job["spy"].handler = handler
    assert cli.main(["fixtures", "--confirm-target", TARGET]) == 1
    (provider,) = job["providers"]
    assert not provider.config_failed and not provider.auth_failed
    run = _fixtures_run(db_session)
    assert run.status == "completed_with_errors" and run.competitions_failed == 1
    assert run.provider_requests == 3  # el 404 no corta el run: las otras dos se piden


# --- C6 sobre el árbol integrado: ops_tick + live_sync de DI ---------------------------------


def test_ops_tick_live_keeps_scheduler_skip_di_config_failure_and_provider_lifecycle(db_session, competitions, job):
    """A: scheduler + lock ocupado → SKIPPED / exit 0 sin peticiones. B: sin API key (config_failed
    de DI) → exit 2. C: un run normal usa UN cliente HTTP y lo cierra. D: ops_tick clasifica el run
    cortado por DI como FAILED (no como un DEGRADED de competiciones)."""
    from datetime import datetime, timezone

    from app.jobs import ops_tick
    from app.repositories import live_sync_repository as runs

    spy = job["spy"]

    def tick():
        def runner(module, args):
            assert module == ops_tick.LIVE_SYNC  # live solo lanza live_sync (fixtures y check)
            return cli.main(args)

        return ops_tick.run_tick("live", target=TARGET, now=datetime.now(timezone.utc),
                                 session_factory=lambda: nullcontext(db_session), runner=runner)

    def fixtures_step(result):
        return next(s for s in result.steps if s["step"] == "fixtures")

    # A
    holder = runs.create_run(db_session, job_type="catalog", trigger="cli")
    db_session.commit()
    skipped = tick()
    assert fixtures_step(skipped)["class"] == "SKIPPED" and fixtures_step(skipped)["exit"] == 0
    assert spy.requests == [] and job["providers"][-1].http_requests == 0
    runs.finish_run(db_session, holder, status="completed")
    db_session.commit()

    # C (el run saltado también abrió y cerró su cliente al entrar en `async with provider`)
    before = len(spy.clients)
    ok = tick()
    assert fixtures_step(ok)["class"] == "SUCCESS"
    assert len(spy.clients) == before + 1 and all(c.is_closed for c in spy.clients)
    assert [i for i, _ in spy.requests] == [before] * 3  # un solo cliente para las 3 competiciones

    # B + D
    job["api_key"] = ""
    cut = tick()
    step = fixtures_step(cut)
    assert step["exit"] == 2 and step["class"] == "FAILED" and cut.cls == "FAILED"
    assert cut.reason == "job_exit_error" and job["providers"][-1].config_failed
    assert "job_failed" in [e["event"] for e in cut.events] and cut.exit_code() == 2
