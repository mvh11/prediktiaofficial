"""Política de reintentos y errores de 5DollarFootballAPI, con HTTP simulado (sin red ni BD).

Diferencia deliberada con API-Football: con un límite de 10 peticiones/minuto, 5Dollar reintenta
como mucho una vez (solo timeout, conexión y 5xx) y nunca reintenta un 429.
"""

import asyncio
import logging

import httpx
import pytest

from app.integrations import http
from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.integrations.odds import five_dollar
from app.integrations.odds.five_dollar import MAX_ATTEMPTS, TRANSIENT_RETRY_DELAY, FiveDollarFootballProvider

KEY = "secret-5d-key"
STATUS_OK = {"success": 1, "data": {"plan": "basic", "usage": {"today": 3}, "limits": {"rate_limit": 10}}}
REAL_ASYNC_CLIENT = httpx.AsyncClient  # antes de que el fixture lo sustituya


class Api:
    """Sustituye httpx.AsyncClient: responde en orden con `responses` y registra clientes y peticiones."""

    def __init__(self) -> None:
        self.responses: list = []
        self.clients: list[httpx.AsyncClient] = []
        self.requests: list[tuple[int, httpx.Request]] = []  # (índice del cliente, petición)

    def factory(self, **kwargs) -> httpx.AsyncClient:
        index = len(self.clients)

        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append((index, request))
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(respond), **kwargs)
        self.clients.append(client)
        return client


@pytest.fixture
def api(monkeypatch) -> Api:
    api = Api()
    monkeypatch.setattr(http.httpx, "AsyncClient", api.factory)
    return api


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(five_dollar, "_sleep", fake_sleep)
    return recorded


def _provider(api_key: str = KEY) -> FiveDollarFootballProvider:
    return FiveDollarFootballProvider(api_key=api_key, base_url="https://example.invalid/v1", timeout=1)


def _check(provider: FiveDollarFootballProvider):
    return asyncio.run(provider.check_status())


def _stats(provider: FiveDollarFootballProvider) -> tuple[int, int, int, int]:
    s = provider.request_stats
    return s.calls, s.attempts, s.retries, s.failed_attempts


# --- Sin reintentos: configuración, credenciales, 4xx, errores de la API y 429 ---------------


def test_missing_key_fails_fast_without_any_request(api, sleeps):
    provider = _provider(api_key="")
    with pytest.raises(ProviderNotConfiguredError):
        _check(provider)
    assert api.clients == [] and api.requests == [] and sleeps == []
    assert _stats(provider) == (0, 0, 0, 0)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_not_retried(api, sleeps, status):
    api.responses = [httpx.Response(status)]
    provider = _provider()
    with pytest.raises(ProviderAuthError):
        _check(provider)
    assert len(api.requests) == 1 and sleeps == []
    assert _stats(provider) == (1, 1, 0, 1)


PERMANENT = {
    "404": lambda: httpx.Response(404),
    "400": lambda: httpx.Response(400),
    "success_0": lambda: httpx.Response(200, json={"success": 0, "error": {"message": "Invalid parameter"}}),
    "invalid_json": lambda: httpx.Response(200, text="<html>no es json</html>"),
    "not_a_dict": lambda: httpx.Response(200, json=[1, 2, 3]),
}


@pytest.mark.parametrize("name", PERMANENT, ids=list(PERMANENT))
def test_permanent_errors_not_retried(api, sleeps, name):
    api.responses = [PERMANENT[name]()]
    provider = _provider()
    with pytest.raises(ProviderResponseError):
        _check(provider)
    assert len(api.requests) == 1 and sleeps == []
    assert _stats(provider) == (1, 1, 0, 1)


def test_rate_limit_fails_fast_without_retry(api, sleeps):
    # API-Football reintenta un 429 respetando Retry-After; 5Dollar no: con 10 peticiones/minuto
    # repetir solo alarga el límite. El Retry-After queda en el error para quien llama.
    api.responses = [httpx.Response(429, headers={"Retry-After": "30"})]
    provider = _provider()
    with pytest.raises(ProviderRateLimitError) as exc:
        _check(provider)
    assert exc.value.retry_after == 30.0
    assert len(api.requests) == 1 and sleeps == []
    assert _stats(provider) == (1, 1, 0, 1)


# --- Fallos transitorios: un reintento como mucho -------------------------------------------

TRANSIENT = {
    "timeout": (lambda: httpx.ReadTimeout("lento"), ProviderTimeoutError),
    "connection": (lambda: httpx.ConnectError("rechazada"), ProviderConnectionError),
    "503": (lambda: httpx.Response(503), ProviderResponseError),
    "500": (lambda: httpx.Response(500), ProviderResponseError),
}


@pytest.mark.parametrize("name", TRANSIENT, ids=list(TRANSIENT))
def test_transient_failure_retried_once_then_raised(api, sleeps, name):
    make, error_type = TRANSIENT[name]
    api.responses = [make(), make(), make()]  # una tercera respuesta que nunca se pide
    provider = _provider()
    with pytest.raises(error_type):
        _check(provider)
    assert MAX_ATTEMPTS == 2 and len(api.requests) == 2
    assert sleeps == [TRANSIENT_RETRY_DELAY] == [6.0]  # 60 s / 10 peticiones
    assert _stats(provider) == (1, 2, 1, 2)


@pytest.mark.parametrize("name", TRANSIENT, ids=list(TRANSIENT))
def test_transient_failure_recovered_by_retry(api, sleeps, name):
    make, _ = TRANSIENT[name]
    api.responses = [make(), httpx.Response(200, json=STATUS_OK)]
    provider = _provider()
    status = _check(provider)
    assert status.reachable and status.plan == "basic" and status.requests_used == 3
    assert len(api.requests) == 2 and sleeps == [6.0]
    assert _stats(provider) == (1, 2, 1, 1)


# --- Cliente HTTP: uno por llamada lógica, reutilizado por su reintento y cerrado ------------


def test_retry_reuses_the_call_client_and_it_is_closed(api, sleeps):
    api.responses = [httpx.Response(503), httpx.Response(200, json=STATUS_OK)]
    _check(_provider())
    assert len(api.clients) == 1 and [index for index, _ in api.requests] == [0, 0]
    assert api.clients[0].is_closed


def test_client_closed_after_failure_and_separate_calls_use_separate_clients(api, sleeps):
    provider = _provider()
    api.responses = [httpx.Response(404)]
    with pytest.raises(ProviderResponseError):
        _check(provider)
    api.responses = [httpx.Response(200, json=STATUS_OK)]
    _check(provider)
    assert len(api.clients) == 2 and all(c.is_closed for c in api.clients)
    assert _stats(provider) == (2, 2, 0, 1)


# --- Logs ------------------------------------------------------------------------------------


def test_logs_never_contain_the_api_key(api, sleeps, caplog):
    api.responses = [httpx.Response(503), httpx.Response(200, json=STATUS_OK)]
    with caplog.at_level(logging.DEBUG, logger=five_dollar.__name__), caplog.at_level(logging.DEBUG, logger=http.__name__):
        _check(_provider())
    assert "intento 1/2 falló en" in caplog.text and "intento 2/2 ok en" in caplog.text
    assert KEY not in caplog.text and "Bearer" not in caplog.text
    # la clave sí viaja en la cabecera, que nunca se registra
    assert all(request.headers["Authorization"] == f"Bearer {KEY}" for _, request in api.requests)
