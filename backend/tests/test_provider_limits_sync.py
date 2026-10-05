"""Syncs de fixtures y catálogo ante límites y credenciales rechazadas, con el adapter real de API-Football.

Solo se sustituye get_json (respuestas por ruta y liga) y la espera entre reintentos, así que
se prueba la cadena completa: reintentos en ApiFootballProvider._get() y corte en el service.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.integrations.football import api_football
from app.integrations.football.api_football import ApiFootballProvider
from app.models import Competition, Fixture
from app.services import catalog_sync_service, fixture_sync_service
from tests.conftest import load_json, make_competition

pytestmark = pytest.mark.db

P = "api-football"
EMPTY = {"errors": [], "results": 0, "paging": {"current": 1, "total": 1}, "response": []}
LEAGUES = (265, 39, 140)  # todas en TRACKED_LEAGUE_IDS


class RoutedApi:
    """get_json falso: cola de respuestas por (ruta, liga); sin cola definida devuelve una respuesta vacía."""

    def __init__(self) -> None:
        self.queues: dict[tuple[str, int | None], list] = {}
        self.calls: list[tuple[str, int | None]] = []
        self.clients: list = []  # cliente HTTP recibido en cada petición

    def on(self, path: str, league: int | None, *items) -> None:
        self.queues[(path, league)] = list(items)

    def calls_to(self, path: str, league: int | None = None) -> int:
        return self.calls.count((path, league))

    async def get_json(self, *, path, params=None, client=None, **_kwargs):
        key = (path, (params or {}).get("league"))
        self.calls.append(key)
        self.clients.append(client)
        queue = self.queues.get(key)
        item = queue.pop(0) if queue else EMPTY
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def api(monkeypatch) -> RoutedApi:
    routed = RoutedApi()
    provider = ApiFootballProvider(api_key="test-key", base_url="https://example.invalid", timeout=1)
    monkeypatch.setattr(api_football, "get_json", routed.get_json)
    monkeypatch.setattr(fixture_sync_service, "get_football_provider", lambda: provider)
    monkeypatch.setattr(catalog_sync_service, "get_football_provider", lambda: provider)
    return routed


PERSISTENT_LIMITS = {
    "429": lambda: ProviderRateLimitError(P, "Límite de peticiones superado", 429),
    "errors.rateLimit": lambda: load_json("api_football/errors_rate_limit.json"),
}
TRANSIENT_FAILURES = {
    "timeout": lambda: ProviderTimeoutError(P, "Timeout tras 1s"),
    "503": lambda: ProviderResponseError(P, "Respuesta HTTP 503", 503),
}
# Lo que get_json lanzaría con 401/403 y lo que la API devuelve con HTTP 200 y errors.token
AUTH_FAILURES = {
    "401": lambda: ProviderAuthError(P, "API key inválida o sin permisos", 401),
    "403": lambda: ProviderAuthError(P, "API key inválida o sin permisos", 403),
    "errors.token": lambda: load_json("api_football/errors_token.json"),
}
# Errores permanentes que no son de credenciales: fallan esa competición pero no cortan la sync
NON_AUTH_PERMANENT = {
    "404": lambda: ProviderResponseError(P, "Respuesta HTTP 404", 404),
    "errors_other": lambda: load_json("api_football/errors_other.json"),
}
AUTH_STOP = "se detuvo la sync porque el proveedor rechazó las credenciales"


# --- Sync de fixtures -------------------------------------------------------------------------


@pytest.fixture
def three_competitions(db_session) -> dict[int, int]:
    """Liga A (265), Liga B (39) y Liga C (140): la sync las recorre en ese orden (por país y nombre)."""
    ids = {}
    for external_id, name in zip(LEAGUES, ("Liga A", "Liga B", "Liga C")):
        ids[external_id], _ = make_competition(db_session, external_id, name=name)
    return ids


def _sync_fixtures(db):
    result = asyncio.run(fixture_sync_service.sync_fixtures(db))
    return result, {r.competition_id: r for r in result.competitions}


@pytest.mark.parametrize("limit", PERSISTENT_LIMITS, ids=list(PERSISTENT_LIMITS))
def test_fixtures_persistent_rate_limit_stops_remaining(db_session, three_competitions, api, retry_sleeps, limit):
    api.on("/fixtures", 265, *(PERSISTENT_LIMITS[limit]() for _ in range(3)))
    _, by_id = _sync_fixtures(db_session)

    assert api.calls_to("/fixtures", 265) == 3  # se reintentó dentro de _get()
    assert api.calls_to("/fixtures", 39) == api.calls_to("/fixtures", 140) == 0  # y no se siguió martillando
    assert retry_sleeps == [5.0, 10.0]
    assert "Límite" in by_id[three_competitions[265]].error
    for league in (39, 140):
        assert "se detuvo la sync por el límite del proveedor" in by_id[three_competitions[league]].error


def test_fixtures_daily_quota_no_retry_and_stops_remaining(db_session, three_competitions, api, retry_sleeps):
    # La primera competición se guarda bien; la cuota se agota en la segunda
    api.on("/fixtures", 265, load_json("api_football/fixtures_mixed.json"))
    api.on("/fixtures", 39, load_json("api_football/errors_daily_quota.json"))
    result, by_id = _sync_fixtures(db_session)

    assert (api.calls_to("/fixtures", 265), api.calls_to("/fixtures", 39), api.calls_to("/fixtures", 140)) == (1, 1, 0)
    assert retry_sleeps == []
    assert by_id[three_competitions[265]].error is None and by_id[three_competitions[265]].fixtures == 6
    assert "Cuota diaria" in by_id[three_competitions[39]].error
    assert "Cuota diaria" in by_id[three_competitions[140]].error
    # lo ya guardado se conserva
    db_session.expire_all()
    assert len(db_session.scalars(select(Fixture.id)).all()) == 6 == result.fixtures_synced


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES, ids=list(TRANSIENT_FAILURES))
def test_fixtures_transient_failure_exhausted_continues(db_session, three_competitions, api, retry_sleeps, failure):
    api.on("/fixtures", 265, *(TRANSIENT_FAILURES[failure]() for _ in range(3)))
    _, by_id = _sync_fixtures(db_session)

    assert api.calls_to("/fixtures", 265) == 3
    assert api.calls_to("/fixtures", 39) == api.calls_to("/fixtures", 140) == 1  # la siguiente sí se intenta
    assert retry_sleeps == [1.0, 2.0]
    assert by_id[three_competitions[265]].error is not None
    assert by_id[three_competitions[39]].error is None and by_id[three_competitions[140]].error is None


def test_fixtures_retry_after_over_60_fails_fast(db_session, three_competitions, api, retry_sleeps):
    api.on("/fixtures", 265, ProviderRateLimitError(P, "Límite de peticiones superado", 429, retry_after=120.0))
    _, by_id = _sync_fixtures(db_session)

    assert api.calls_to("/fixtures", 265) == 1 and retry_sleeps == []
    assert api.calls_to("/fixtures", 39) == api.calls_to("/fixtures", 140) == 0
    assert "se detuvo la sync" in by_id[three_competitions[140]].error


# --- Sync de catálogo -------------------------------------------------------------------------


def _leagues_payload() -> dict:
    return {
        **EMPTY,
        "response": [
            {"league": {"id": league, "name": f"Liga {league}"}, "country": {"name": "Test"},
             "seasons": [{"year": 2026, "current": True}]}
            for league in LEAGUES
        ],
    }


def _teams_payload(*team_ids: int) -> dict:
    return {**EMPTY, "response": [{"team": {"id": t, "name": f"Equipo {t}"}} for t in team_ids]}


def _sync_catalog(db):
    result = asyncio.run(catalog_sync_service.sync_catalog(db))
    return result, {r.external_id: r for r in result.competitions}


@pytest.mark.parametrize("limit", PERSISTENT_LIMITS, ids=list(PERSISTENT_LIMITS))
def test_catalog_persistent_rate_limit_stops_remaining_teams(db_session, api, retry_sleeps, limit):
    api.on("/leagues", None, _leagues_payload())
    api.on("/teams", 265, *(PERSISTENT_LIMITS[limit]() for _ in range(3)))
    _, by_ext = _sync_catalog(db_session)

    assert api.calls_to("/teams", 265) == 3
    assert api.calls_to("/teams", 39) == api.calls_to("/teams", 140) == 0
    assert retry_sleeps == [5.0, 10.0]
    for league in (39, 140):
        assert "se detuvo la sync por el límite del proveedor" in by_ext[league].error
    # los datos de /leagues de las competiciones restantes sí se guardan
    db_session.expire_all()
    assert set(db_session.scalars(select(Competition.external_id))) >= set(LEAGUES)


def test_catalog_daily_quota_no_retry_and_stops_remaining(db_session, api, retry_sleeps):
    api.on("/leagues", None, _leagues_payload())
    api.on("/teams", 265, _teams_payload(1, 2))
    api.on("/teams", 39, load_json("api_football/errors_daily_quota.json"))
    _, by_ext = _sync_catalog(db_session)

    assert (api.calls_to("/teams", 265), api.calls_to("/teams", 39), api.calls_to("/teams", 140)) == (1, 1, 0)
    assert retry_sleeps == []
    assert by_ext[265].error is None and by_ext[265].teams == 2
    assert "Cuota diaria" in by_ext[39].error and "Cuota diaria" in by_ext[140].error


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES, ids=list(TRANSIENT_FAILURES))
def test_catalog_transient_failure_exhausted_continues(db_session, api, retry_sleeps, failure):
    api.on("/leagues", None, _leagues_payload())
    api.on("/teams", 265, *(TRANSIENT_FAILURES[failure]() for _ in range(3)))
    _, by_ext = _sync_catalog(db_session)

    assert api.calls_to("/teams", 265) == 3
    assert api.calls_to("/teams", 39) == api.calls_to("/teams", 140) == 1
    assert retry_sleeps == [1.0, 2.0]
    assert by_ext[265].error is not None
    assert by_ext[39].error is None and by_ext[140].error is None


def test_catalog_retry_after_over_60_fails_fast(db_session, api, retry_sleeps):
    api.on("/leagues", None, _leagues_payload())
    api.on("/teams", 265, ProviderRateLimitError(P, "Límite de peticiones superado", 429, retry_after=61.0))
    _, by_ext = _sync_catalog(db_session)

    assert api.calls_to("/teams", 265) == 1 and retry_sleeps == []
    assert api.calls_to("/teams", 39) == api.calls_to("/teams", 140) == 0


# --- Credenciales rechazadas (ProviderAuthError): corte sin reintentos -------------------------


@pytest.mark.parametrize("failure", AUTH_FAILURES, ids=list(AUTH_FAILURES))
def test_fixtures_auth_error_stops_remaining(db_session, three_competitions, api, retry_sleeps, failure):
    api.on("/fixtures", 265, AUTH_FAILURES[failure]())
    result, by_id = _sync_fixtures(db_session)

    assert len(api.calls) == 1 and api.calls_to("/fixtures", 265) == 1  # 1 llamada en total, sin reintentos
    assert retry_sleeps == []
    assert by_id[three_competitions[265]].error  # el error propio de esa competición
    assert AUTH_STOP not in by_id[three_competitions[265]].error
    for league in (39, 140):
        assert AUTH_STOP in by_id[three_competitions[league]].error
        assert by_id[three_competitions[league]].fixtures == 0
    assert result.fixtures_synced == 0


def test_fixtures_auth_error_after_success_keeps_saved_data(db_session, three_competitions, api, retry_sleeps):
    api.on("/fixtures", 265, load_json("api_football/fixtures_mixed.json"))
    api.on("/fixtures", 39, AUTH_FAILURES["401"]())
    result, by_id = _sync_fixtures(db_session)

    assert (api.calls_to("/fixtures", 265), api.calls_to("/fixtures", 39), api.calls_to("/fixtures", 140)) == (1, 1, 0)
    assert by_id[three_competitions[265]].error is None and by_id[three_competitions[265]].fixtures == 6
    assert "API key inválida" in by_id[three_competitions[39]].error
    assert AUTH_STOP in by_id[three_competitions[140]].error
    db_session.expire_all()
    assert len(db_session.scalars(select(Fixture.id)).all()) == 6 == result.fixtures_synced


@pytest.mark.parametrize("failure", NON_AUTH_PERMANENT, ids=list(NON_AUTH_PERMANENT))
def test_fixtures_non_auth_permanent_error_continues(db_session, three_competitions, api, retry_sleeps, failure):
    api.on("/fixtures", 265, NON_AUTH_PERMANENT[failure]())
    _, by_id = _sync_fixtures(db_session)

    assert (api.calls_to("/fixtures", 265), api.calls_to("/fixtures", 39), api.calls_to("/fixtures", 140)) == (1, 1, 1)
    assert retry_sleeps == []
    assert by_id[three_competitions[265]].error is not None
    assert by_id[three_competitions[39]].error is None and by_id[three_competitions[140]].error is None


@pytest.mark.parametrize("failure", AUTH_FAILURES, ids=list(AUTH_FAILURES))
def test_catalog_auth_error_in_teams_stops_remaining_teams(db_session, api, retry_sleeps, failure):
    api.on("/leagues", None, _leagues_payload())
    api.on("/teams", 265, AUTH_FAILURES[failure]())
    _, by_ext = _sync_catalog(db_session)

    assert api.calls_to("/teams", 265) == 1
    assert api.calls_to("/teams", 39) == api.calls_to("/teams", 140) == 0
    assert retry_sleeps == []
    assert by_ext[265].error and AUTH_STOP not in by_ext[265].error
    for league in (39, 140):
        assert AUTH_STOP in by_ext[league].error
    # los datos de /leagues de las competiciones restantes sí se guardan
    db_session.expire_all()
    assert set(db_session.scalars(select(Competition.external_id))) >= set(LEAGUES)


def test_catalog_auth_error_in_leagues_propagates(db_session, api, retry_sleeps):
    api.on("/leagues", None, AUTH_FAILURES["401"]())
    with pytest.raises(ProviderAuthError):
        _sync_catalog(db_session)

    assert api.calls == [("/leagues", None)]  # 1 llamada y ninguna a /teams
    assert retry_sleeps == []


@pytest.mark.parametrize("failure", NON_AUTH_PERMANENT, ids=list(NON_AUTH_PERMANENT))
def test_catalog_non_auth_permanent_error_continues(db_session, api, retry_sleeps, failure):
    api.on("/leagues", None, _leagues_payload())
    api.on("/teams", 265, NON_AUTH_PERMANENT[failure]())
    _, by_ext = _sync_catalog(db_session)

    assert (api.calls_to("/teams", 265), api.calls_to("/teams", 39), api.calls_to("/teams", 140)) == (1, 1, 1)
    assert by_ext[265].error is not None
    assert by_ext[39].error is None and by_ext[140].error is None


# --- Payload inválido y medición ------------------------------------------------------------------


def _payload_without_date(index: int = 0) -> dict:
    payload = load_json("api_football/fixtures_mixed.json")
    payload["response"][index]["fixture"].pop("date")
    return payload


def test_fixtures_invalid_payload_fails_only_that_competition(db_session, three_competitions, api, retry_sleeps):
    # Liga A trae un partido sin fecha: falla entera y no se guarda NINGÚN partido suyo
    api.on("/fixtures", 265, _payload_without_date())
    api.on("/fixtures", 39, load_json("api_football/fixtures_mixed.json"))
    result, by_id = _sync_fixtures(db_session)

    assert (api.calls_to("/fixtures", 265), api.calls_to("/fixtures", 39), api.calls_to("/fixtures", 140)) == (1, 1, 1)
    assert retry_sleeps == []
    assert "sin fixture.date" in by_id[three_competitions[265]].error
    assert by_id[three_competitions[265]].fixtures == 0
    assert by_id[three_competitions[39]].error is None and by_id[three_competitions[39]].fixtures == 6
    assert by_id[three_competitions[140]].error is None
    db_session.expire_all()
    from app.models import Season

    season_a = db_session.scalars(select(Season.id).where(Season.competition_id == three_competitions[265])).one()
    assert db_session.scalars(select(Fixture.id).where(Fixture.season_id == season_a)).all() == []  # nada de Liga A
    assert result.fixtures_synced == 6


def test_fixtures_sync_logs_duration_and_request_counts(db_session, three_competitions, api, retry_sleeps, caplog):
    import logging

    api.on("/fixtures", 265, ProviderResponseError(P, "Respuesta HTTP 503", 503), EMPTY)  # 1 reintento
    with caplog.at_level(logging.INFO, logger=fixture_sync_service.__name__):
        _sync_fixtures(db_session)
    lines = [r.getMessage() for r in caplog.records if r.name == fixture_sync_service.__name__]
    per_competition = [m for m in lines if m.startswith("Sync fixtures · Liga")]
    assert len(per_competition) == 3 and all(" ms · peticiones " in m for m in per_competition)
    assert "peticiones 2 (reintentos 1, fallidas 1)" in per_competition[0]
    total = [m for m in lines if m.startswith("Sync fixtures · total")]
    assert len(total) == 1 and "peticiones 4 (reintentos 1, fallidas 1)" in total[0]
    assert "test-key" not in caplog.text


def test_fixtures_sync_run_reuses_one_http_client_and_closes_it(db_session, three_competitions, api, retry_sleeps):
    api.on("/fixtures", 265, ProviderResponseError(P, "Respuesta HTTP 503", 503), EMPTY)  # 1 reintento
    _sync_fixtures(db_session)
    first_run = api.clients[:]
    assert len(first_run) == 4  # 3 competiciones + 1 reintento, todas con el mismo cliente
    assert first_run[0] is not None and all(c is first_run[0] for c in first_run)
    assert first_run[0].is_closed

    _sync_fixtures(db_session)  # otra sync: otro cliente, también cerrado al terminar
    second_run = api.clients[len(first_run):]
    assert len(second_run) == 3 and all(c is second_run[0] for c in second_run)
    assert second_run[0] is not first_run[0] and second_run[0].is_closed
