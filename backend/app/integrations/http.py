"""Función común para hacer peticiones GET a proveedores externos con manejo de errores."""

import logging
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
) -> Any:
    """Hace un GET y devuelve el JSON. Traduce los errores de red/HTTP a ProviderError.

    Nunca registra las cabeceras (contienen la API key).
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    logger.info("GET %s (%s)", url, provider)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers, params=params)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(provider, f"Timeout tras {timeout}s") from exc
    except httpx.RequestError as exc:
        raise ProviderConnectionError(provider, f"Error de conexión: {exc.__class__.__name__}") from exc

    status = response.status_code
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
