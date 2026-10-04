"""Syncs de fixtures y catálogo ante límites del proveedor, con el adapter real de API-Football.

Solo se sustituye get_json (respuestas por ruta y liga) y la espera entre reintentos, así que
se prueba la cadena completa: reintentos en ApiFootballProvider._get() y corte en el service.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.integrations.exceptions import ProviderRateLimitError, ProviderResponseError, ProviderTimeoutError
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

    def on(self, path: str, league: int | None, *items) -> None:
        self.queues[(path, league)] = list(items)

    def calls_to(self, path: str, league: int | None = None) -> int:
        return self.calls.count((path, league))

    async def get_json(self, *, path, params=None, **_kwargs):
        key = (path, (params or {}).get("league"))
        self.calls.append(key)
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
