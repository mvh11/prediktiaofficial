"""Ciclo de vida del cliente HTTP: uno por run del provider, cerrado al terminar (sin red ni BD)."""

import asyncio

import httpx
import pytest

from app.integrations import http
from app.integrations.exceptions import ProviderResponseError
from app.integrations.football.api_football import MAX_ATTEMPTS, ApiFootballProvider
from app.services import catalog_sync_service

EMPTY = {"errors": [], "paging": {"current": 1, "total": 1}, "response": []}
REAL_ASYNC_CLIENT = httpx.AsyncClient  # antes de que el fixture lo sustituya


class ClientSpy:
    """Sustituye httpx.AsyncClient en http.py: cada cliente creado responde con `handler` y se registra."""

    def __init__(self) -> None:
        self.clients: list[httpx.AsyncClient] = []
        self.requests: list[tuple[int, str]] = []  # (índice del cliente que la envió, ruta)
        self.handler = lambda request: httpx.Response(200, json=EMPTY)

    def factory(self, **kwargs) -> httpx.AsyncClient:
        index = len(self.clients)

        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append((index, request.url.path))
            return self.handler(request)

        client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(respond), **kwargs)
        self.clients.append(client)
        return client


@pytest.fixture
def spy(monkeypatch) -> ClientSpy:
    spy = ClientSpy()
    monkeypatch.setattr(http.httpx, "AsyncClient", spy.factory)
    return spy


def _provider() -> ApiFootballProvider:
    return ApiFootballProvider(api_key="test-key", base_url="https://example.invalid", timeout=1)


def test_requests_of_a_run_reuse_one_client_closed_at_the_end(spy):
    async def run(provider):
        async with provider:
            await provider.get_fixtures(39, 2025)
            await provider.get_teams(39, 2025)
            await provider.get_fixtures(140, 2025)

    asyncio.run(run(_provider()))
    assert len(spy.clients) == 1
    assert [index for index, _ in spy.requests] == [0, 0, 0]
    assert spy.clients[0].is_closed


def test_client_closed_when_the_run_raises(spy):
    async def run(provider):
        async with provider:
            await provider.get_fixtures(39, 2025)
            raise RuntimeError("fallo del llamador a mitad del run")

    with pytest.raises(RuntimeError):
        asyncio.run(run(_provider()))
    assert len(spy.clients) == 1 and spy.clients[0].is_closed


def test_retries_use_the_same_client_and_it_closes_after_exhaustion(spy):
    spy.handler = lambda request: httpx.Response(503)
    provider = _provider()

    async def run():
        async with provider:
            await provider.get_fixtures(39, 2025)

    with pytest.raises(ProviderResponseError):
        asyncio.run(run())
    assert len(spy.clients) == 1 and spy.clients[0].is_closed
    assert [index for index, _ in spy.requests] == [0] * MAX_ATTEMPTS
    s = provider.request_stats  # semántica DI-A2 intacta: intentos HTTP iniciados por Prediktia
    assert (s.calls, s.attempts, s.retries, s.failed_attempts) == (1, MAX_ATTEMPTS, MAX_ATTEMPTS - 1, MAX_ATTEMPTS)


def test_separate_runs_do_not_share_a_client(spy):
    provider = _provider()

    async def run(p):
        async with p:
            await p.get_fixtures(39, 2025)

    asyncio.run(run(provider))
    asyncio.run(run(provider))  # el mismo provider en otro run abre otro cliente
    asyncio.run(run(_provider()))
    assert len(spy.clients) == 3 and len({id(c) for c in spy.clients}) == 3
    assert [index for index, _ in spy.requests] == [0, 1, 2]
    assert all(c.is_closed for c in spy.clients)


def test_run_cannot_be_opened_twice_on_the_same_provider(spy):
    provider = _provider()

    async def run():
        async with provider:
            async with provider:
                pass

    with pytest.raises(RuntimeError, match="ya hay un run abierto"):
        asyncio.run(run())
    assert len(spy.clients) == 1 and spy.clients[0].is_closed


def test_without_a_run_each_request_opens_and_closes_its_own_client(spy):
    # Llamadas sueltas (check_status, odds, backfill) mantienen el comportamiento anterior
    provider = _provider()
    asyncio.run(provider.get_fixtures(39, 2025))
    asyncio.run(provider.get_fixtures(39, 2025))
    assert len(spy.clients) == 2 and all(c.is_closed for c in spy.clients)


def test_sync_closes_its_client_when_the_initial_request_fails(spy, monkeypatch):
    # /leagues falla tras los reintentos: el error se propaga (antes de tocar la BD) y el cliente se cierra
    spy.handler = lambda request: httpx.Response(503)
    monkeypatch.setattr(catalog_sync_service, "get_football_provider", _provider)
    with pytest.raises(ProviderResponseError):
        asyncio.run(catalog_sync_service.sync_catalog(db=None))
    assert len(spy.clients) == 1 and spy.clients[0].is_closed
    assert [path for _, path in spy.requests] == ["/leagues"] * MAX_ATTEMPTS
