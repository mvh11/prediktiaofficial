"""Función común para hacer peticiones GET a proveedores externos con manejo de errores."""

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)

logger = logging.getLogger(__name__)


def safe_target(url: str) -> str:
    """host + ruta de una URL, para los logs: sin credenciales (userinfo) ni parámetros de query."""
    parsed = httpx.URL(url)
    return f"{parsed.host}{parsed.path}"


def new_client(timeout: float) -> httpx.AsyncClient:
    """Cliente HTTP para varias peticiones (p. ej. un run de un provider). Quien lo crea lo cierra."""
    return httpx.AsyncClient(timeout=timeout)


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Segundos de espera de una cabecera Retry-After (segundos o fecha HTTP).

    Devuelve None si falta o no se puede interpretar; una fecha ya pasada es 0, nunca negativo.
    """
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
        return max(seconds, 0.0)
    return seconds if seconds >= 0 else None


async def get_json(
    *,
    provider: str,
    base_url: str,
    path: str,
    headers: dict[str, str],
    timeout: float,
    params: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> Any:
    """Hace un GET y devuelve el JSON. Traduce los errores de red/HTTP a ProviderError.

    Con `client` reutiliza ese cliente (y sus conexiones) y no lo cierra: es de quien lo creó.
    Sin él, abre y cierra un cliente solo para esta petición.
    Nunca registra las cabeceras (contienen la API key) ni la URL completa: solo host y ruta.
    Registra en DEBUG la duración de la petición (los reintentos y su duración los registra el
    adapter que llama).
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    target = safe_target(url)
    started = time.perf_counter()

    try:
        if client is not None:
            response = await client.get(url, headers=headers, params=params, timeout=timeout)
        else:
            async with new_client(timeout) as own_client:
                response = await own_client.get(url, headers=headers, params=params)
    except httpx.TimeoutException as exc:
        logger.debug("GET %s (%s): timeout en %.0f ms", target, provider, (time.perf_counter() - started) * 1000)
        raise ProviderTimeoutError(provider, f"Timeout tras {timeout}s") from exc
    except httpx.RequestError as exc:
        logger.debug("GET %s (%s): %s en %.0f ms", target, provider, exc.__class__.__name__, (time.perf_counter() - started) * 1000)
        raise ProviderConnectionError(provider, f"Error de conexión: {exc.__class__.__name__}") from exc

    status = response.status_code
    logger.debug("GET %s (%s): HTTP %s en %.0f ms", target, provider, status, (time.perf_counter() - started) * 1000)
    if status in (401, 403):
        raise ProviderAuthError(provider, "API key inválida o sin permisos", status)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        msg = "Límite de peticiones superado"
        if retry_after:
            msg += f" (reintentar en {retry_after}s)"
        raise ProviderRateLimitError(provider, msg, status, retry_after=parse_retry_after(retry_after))
    if status >= 400:
        raise ProviderResponseError(provider, f"Respuesta HTTP {status}", status)

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderResponseError(provider, "La respuesta no es JSON válido", status) from exc
