"""Función común para hacer peticiones GET a proveedores externos con manejo de errores."""

import logging
from typing import Any

import httpx

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)

logger = logging.getLogger(__name__)


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
        raise ProviderResponseError(provider, f"Error de conexión: {exc.__class__.__name__}") from exc

    status = response.status_code
    if status in (401, 403):
        raise ProviderAuthError(provider, "API key inválida o sin permisos", status)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        msg = "Límite de peticiones superado"
        if retry_after:
            msg += f" (reintentar en {retry_after}s)"
        raise ProviderRateLimitError(provider, msg, status)
    if status >= 400:
        raise ProviderResponseError(provider, f"Respuesta HTTP {status}", status)

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderResponseError(provider, "La respuesta no es JSON válido", status) from exc
