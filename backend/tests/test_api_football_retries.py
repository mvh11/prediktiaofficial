"""Reintentos y clasificación de errores de API-Football, y get_json con HTTP simulado (sin red ni BD)."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.integrations import http
from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.integrations.football import api_football
from app.integrations.football.api_football import ApiFootballProvider
from app.integrations.odds import five_dollar
from app.integrations.odds.five_dollar import FiveDollarFootballProvider
from tests.conftest import load_json

OK = {"get": "fixtures", "errors": [], "results": 0, "paging": {"current": 1, "total": 1}, "response": []}
P = "api-football"


@pytest.fixture
def provider() -> ApiFootballProvider:
    return ApiFootballProvider(api_key="test-key", base_url="https://example.invalid", timeout=1)


class FakeResponses:
    """Respuestas sucesivas de get_json: un dict se devuelve y una excepción se lanza."""

    def __init__(self) -> None:
        self.queue: list = []
        self.calls: list[dict] = []

    def set(self, *items) -> None:
        self.queue = list(items)

    async def get_json(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def responses(monkeypatch) -> FakeResponses:
    fake = FakeResponses()
    monkeypatch.setattr(api_football, "get_json", fake.get_json)
    return fake


def _fixtures(provider):
    return asyncio.run(provider.get_fixtures(265, 2026))


# --- Errores transitorios: se reintentan ------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ProviderTimeoutError(P, "Timeout tras 1s"),
        ProviderConnectionError(P, "Error de conexión: ConnectError"),
        ProviderResponseError(P, "Respuesta HTTP 500", 500),
        ProviderResponseError(P, "Respuesta HTTP 502", 502),
        ProviderResponseError(P, "Respuesta HTTP 503", 503),
        ProviderResponseError(P, "Respuesta HTTP 504", 504),
    ],
    ids=["timeout", "conexion", "500", "502", "503", "504"],
)
def test_transient_error_retried_then_success(provider, responses, retry_sleeps, error):
    responses.set(error, OK)
    assert _fixtures(provider) == []
    assert len(responses.calls) == 2
    assert retry_sleeps == [1.0]


def test_transient_backoff_is_1_then_2(provider, responses, retry_sleeps):
    responses.set(ProviderResponseError(P, "HTTP 503", 503), ProviderTimeoutError(P, "Timeout"), OK)
    assert _fixtures(provider) == []
    assert len(responses.calls) == 3
    assert retry_sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    "error_type", [ProviderTimeoutError, ProviderConnectionError], ids=["timeout", "conexion"]
)
def test_transient_retries_exhausted_raise_last_error(provider, responses, retry_sleeps, error_type):
    responses.set(*(error_type(P, f"fallo {i}") for i in range(3)))
    with pytest.raises(error_type, match="fallo 2"):
        _fixtures(provider)
    assert len(responses.calls) == 3  # máximo 3 intentos, ni uno más
    assert retry_sleeps == [1.0, 2.0]


def test_5xx_retries_exhausted(provider, responses, retry_sleeps):
    responses.set(*(ProviderResponseError(P, "HTTP 502", 502) for _ in range(3)))
    with pytest.raises(ProviderResponseError) as exc:
        _fixtures(provider)
    assert exc.value.status_code == 502
    assert len(responses.calls) == 3 and retry_sleeps == [1.0, 2.0]


# --- Límite de peticiones ---------------------------------------------------------------------


def test_429_with_retry_after_waits_that_long(provider, responses, retry_sleeps):
    responses.set(ProviderRateLimitError(P, "429", 429, retry_after=7.0), OK)
    assert _fixtures(provider) == []
    assert retry_sleeps == [7.0]


def test_429_retry_after_zero(provider, responses, retry_sleeps):
    responses.set(ProviderRateLimitError(P, "429", 429, retry_after=0.0), OK)
    assert _fixtures(provider) == []
    assert retry_sleeps == [0.0]


def test_429_without_retry_after_uses_backoff(provider, responses, retry_sleeps):
    responses.set(ProviderRateLimitError(P, "429", 429), ProviderRateLimitError(P, "429", 429), OK)
    assert _fixtures(provider) == []
    assert retry_sleeps == [5.0, 10.0]


def test_429_persistent_raises_rate_limit_after_retries(provider, responses, retry_sleeps):
    responses.set(*(ProviderRateLimitError(P, "429", 429) for _ in range(3)))
    with pytest.raises(ProviderRateLimitError) as exc:
        _fixtures(provider)
    assert not isinstance(exc.value, ProviderQuotaExceededError)
    assert len(responses.calls) == 3 and retry_sleeps == [5.0, 10.0]


def test_retry_after_over_60s_fails_immediately(provider, responses, retry_sleeps):
    responses.set(ProviderRateLimitError(P, "429", 429, retry_after=61.0), OK)
    with pytest.raises(ProviderRateLimitError):
        _fixtures(provider)
    assert len(responses.calls) == 1 and retry_sleeps == []


def test_retry_after_exactly_60s_is_waited(provider, responses, retry_sleeps):
    responses.set(ProviderRateLimitError(P, "429", 429, retry_after=60.0), OK)
    assert _fixtures(provider) == []
    assert retry_sleeps == [60.0]


# --- Presupuesto de espera acumulada por llamada (MAX_TOTAL_WAIT = 60s) -----------------------


def _limit(retry_after: float | None = None):
    return ProviderRateLimitError(P, "429", 429, retry_after=retry_after)


def test_retry_after_30_plus_30_within_budget(provider, responses, retry_sleeps):
    responses.set(_limit(30.0), _limit(30.0), OK)
    assert _fixtures(provider) == []
    assert len(responses.calls) == 3 and retry_sleeps == [30.0, 30.0]


def test_retry_after_40_plus_40_second_wait_not_done(provider, responses, retry_sleeps):
    responses.set(_limit(40.0), _limit(40.0), OK)
    with pytest.raises(ProviderRateLimitError) as exc:
        _fixtures(provider)
    assert exc.value.retry_after == 40.0  # se lanza el segundo rate limit, sin esperarlo
    assert len(responses.calls) == 2 and retry_sleeps == [40.0]


@pytest.mark.parametrize("second", [60.0, 1.0, None], ids=["retry-after-60", "retry-after-1", "sin-retry-after"])
def test_retry_after_60_leaves_no_budget(provider, responses, retry_sleeps, second):
    responses.set(_limit(60.0), _limit(second), OK)
    with pytest.raises(ProviderRateLimitError):
        _fixtures(provider)
    assert len(responses.calls) == 2 and retry_sleeps == [60.0]


def test_retry_after_61_no_sleep(provider, responses, retry_sleeps):
    responses.set(_limit(61.0), OK)
    with pytest.raises(ProviderRateLimitError):
        _fixtures(provider)
    assert len(responses.calls) == 1 and retry_sleeps == []


def test_fallback_backoff_fits_budget(provider, responses, retry_sleeps):
    responses.set(_limit(), _limit(), OK)
    assert _fixtures(provider) == []
    assert retry_sleeps == [5.0, 10.0] and sum(retry_sleeps) <= api_football.MAX_TOTAL_WAIT


def test_budget_counts_transient_waits_too(provider, responses, retry_sleeps):
    # 1s esperado por un 503; después un Retry-After de 60s llevaría el total a 61s
    responses.set(ProviderResponseError(P, "HTTP 503", 503), _limit(60.0), OK)
    with pytest.raises(ProviderRateLimitError):
        _fixtures(provider)
    assert retry_sleeps == [1.0]


def test_sleep_fixture_only_replaces_api_football_sleep(retry_sleeps):
    # El fixture autouse sustituye solo api_football._sleep; asyncio.sleep sigue siendo el real
    assert api_football._sleep.__name__ == "_fake_sleep"
    assert asyncio.sleep.__module__ == "asyncio.tasks"
    assert api_football.asyncio.sleep is asyncio.sleep
    asyncio.run(asyncio.sleep(0))
    assert retry_sleeps == []  # una espera ajena no pasa por el registro


def test_errors_ratelimit_in_body_retried(provider, responses, retry_sleeps):
    responses.set(load_json("api_football/errors_rate_limit.json"), OK)
    assert _fixtures(provider) == []
    assert retry_sleeps == [5.0]


def test_errors_ratelimit_in_body_persistent(provider, responses, retry_sleeps):
    responses.set(*(load_json("api_football/errors_rate_limit.json") for _ in range(3)))
    with pytest.raises(ProviderRateLimitError) as exc:
        _fixtures(provider)
    assert not isinstance(exc.value, ProviderQuotaExceededError)
    assert len(responses.calls) == 3 and retry_sleeps == [5.0, 10.0]


# --- Cuota diaria y errores permanentes: sin reintentos ---------------------------------------


def test_daily_quota_not_retried(provider, responses, retry_sleeps):
    responses.set(load_json("api_football/errors_daily_quota.json"), OK)
    with pytest.raises(ProviderQuotaExceededError) as exc:
        _fixtures(provider)
    assert isinstance(exc.value, ProviderRateLimitError)  # compatibilidad: sigue siendo un rate limit
    assert len(responses.calls) == 1 and retry_sleeps == []


def test_quota_wins_over_ratelimit_in_same_body(provider, responses, retry_sleeps):
    body = load_json("api_football/errors_rate_limit.json")
    body["errors"]["requests"] = "You have reached the request limit for the day."
    responses.set(body, OK)
    with pytest.raises(ProviderQuotaExceededError):
        _fixtures(provider)
    assert retry_sleeps == []


@pytest.mark.parametrize(
    "error",
    [
        ProviderResponseError(P, "Respuesta HTTP 400", 400),
        ProviderAuthError(P, "API key inválida o sin permisos", 401),
        ProviderAuthError(P, "API key inválida o sin permisos", 403),
        ProviderResponseError(P, "Respuesta HTTP 404", 404),
        ProviderResponseError(P, "Respuesta HTTP 501", 501),
        ProviderResponseError(P, "La respuesta no es JSON válido", 200),
    ],
    ids=["400", "401", "403", "404", "501", "json-invalido"],
)
def test_permanent_errors_not_retried(provider, responses, retry_sleeps, error):
    responses.set(error, OK)
    with pytest.raises(type(error)):
        _fixtures(provider)
    assert len(responses.calls) == 1 and retry_sleeps == []


@pytest.mark.parametrize("payload", ["errors_token.json", "errors_other.json", "paging_multi.json"])
def test_permanent_api_errors_not_retried(provider, responses, retry_sleeps, payload):
    responses.set(load_json(f"api_football/{payload}"), OK)
    with pytest.raises((ProviderAuthError, ProviderResponseError)):
        _fixtures(provider)
    assert len(responses.calls) == 1 and retry_sleeps == []


# --- get_json con HTTP simulado ---------------------------------------------------------------


@pytest.fixture
def http_handler(monkeypatch):
    """get_json usa un transporte simulado: el test fija la función que responde cada petición."""
    state: dict = {}
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(lambda request: state["handler"](request)), **kwargs)

    monkeypatch.setattr(http.httpx, "AsyncClient", client_factory)

    def _set(handler) -> None:
        state["handler"] = handler

    return _set


def _get_json():
    return asyncio.run(
        http.get_json(provider=P, base_url="https://example.invalid", path="/fixtures", headers={}, timeout=1)
    )


def test_get_json_429_exposes_retry_after_seconds(http_handler):
    http_handler(lambda r: httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(ProviderRateLimitError) as exc:
        _get_json()
    assert (exc.value.status_code, exc.value.retry_after) == (429, 7.0)
    assert exc.value.message == "Límite de peticiones superado (reintentar en 7s)"  # mensaje sin cambios


def test_get_json_429_without_retry_after(http_handler):
    http_handler(lambda r: httpx.Response(429))
    with pytest.raises(ProviderRateLimitError) as exc:
        _get_json()
    assert exc.value.retry_after is None


def test_get_json_connection_error_is_connection_error(http_handler):
    def fail(request):
        raise httpx.ConnectError("sin red", request=request)

    http_handler(fail)
    with pytest.raises(ProviderConnectionError) as exc:
        _get_json()
    assert isinstance(exc.value, ProviderResponseError)  # compatibilidad con quien capture la clase base
    assert exc.value.status_code is None


def test_get_json_timeout_is_timeout_error(http_handler):
    def slow(request):
        raise httpx.ReadTimeout("lento", request=request)

    http_handler(slow)
    with pytest.raises(ProviderTimeoutError):
        _get_json()


@pytest.mark.parametrize(("status", "error_type"), [(401, ProviderAuthError), (403, ProviderAuthError),
                                                     (404, ProviderResponseError), (503, ProviderResponseError)])
def test_get_json_status_mapping_unchanged(http_handler, status, error_type):
    http_handler(lambda r: httpx.Response(status))
    with pytest.raises(error_type) as exc:
        _get_json()
    assert exc.value.status_code == status
    assert not isinstance(exc.value, ProviderConnectionError)


NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7", 7.0),
        (" 0 ", 0.0),
        ("1.5", 1.5),
        (format_datetime(NOW + timedelta(seconds=30), usegmt=True), 30.0),
        (format_datetime(NOW - timedelta(seconds=30), usegmt=True), 0.0),  # fecha vencida: nunca negativo
        ("-5", None),
        ("nan", None),
        ("mañana", None),
        ("", None),
        (None, None),
    ],
    ids=["segundos", "cero", "decimal", "fecha-futura", "fecha-vencida", "negativo", "nan", "texto", "vacio", "falta"],
)
def test_parse_retry_after(value, expected):
    assert http.parse_retry_after(value, now=NOW) == expected


# --- Odds no recibe reintentos --------------------------------------------------------------


def test_odds_provider_not_retried(monkeypatch, retry_sleeps):
    calls: list = []

    async def failing_get_json(**kwargs):
        calls.append(kwargs)
        raise ProviderResponseError("5dollarfootballapi", "Respuesta HTTP 503", 503)

    monkeypatch.setattr(five_dollar, "get_json", failing_get_json)
    odds = FiveDollarFootballProvider(api_key="k", base_url="https://example.invalid", timeout=1)
    with pytest.raises(ProviderResponseError):
        asyncio.run(odds.check_status())
    assert len(calls) == 1 and retry_sleeps == []
