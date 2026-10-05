"""Adapter de 5DollarFootballAPI.

Documentación oficial: https://5dollarfootballapi.com/docs
- Base URL: https://api.5dollarfootballapi.com/v1
- Autenticación: cabecera "Authorization: Bearer <API_KEY>" (también acepta "X-API-Key")
- Respuesta correcta:  {"success": 1, "data": ...}
- Respuesta de error:  {"success": 0, "error": {"type", "code", "message", ...}}
- Límite conocido: 10 peticiones por minuto (no se conocen cuota diaria, ráfagas ni ventanas).
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from app.integrations.exceptions import (
    ProviderConnectionError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.integrations.http import get_json, new_client
from app.integrations.odds.base import OddsProvider
from app.integrations.request_stats import RequestStats
from app.schemas.provider import ProviderStatus

logger = logging.getLogger(__name__)

# Reintentos de _get(): con 10 peticiones/minuto cada reintento gasta presupuesto, así que como
# mucho 1 (2 intentos por llamada) y solo ante fallos transitorios.
# TRANSIENT_RETRY_DELAY: espaciado conservador del reintento derivado del único límite conocido
# (10 peticiones/minuto -> 60 s / 10 = 6 s). NO es un limitador global y no garantiza respetar
# la cuota ni el límite del proveedor entre llamadas: no se conoce si la ventana es fija o
# deslizante, cómo cuenta las ráfagas, ni se controlan peticiones concurrentes o llamadas
# lógicas consecutivas.
MAX_ATTEMPTS = 2
TRANSIENT_RETRY_DELAY = 6.0
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


async def _sleep(seconds: float) -> None:
    """Espera entre reintentos (los tests la sustituyen para no esperar de verdad)."""
    await asyncio.sleep(seconds)


def _is_transient(exc: ProviderError) -> bool:
    """Timeout, conexión o HTTP 500/502/503/504. Nunca 429 (con 10 peticiones/minuto reintentar
    agrava el límite: se falla rápido con el Retry-After en el error), credenciales, otros 4xx,
    success=0 ni respuestas mal formadas: son permanentes o no mejoran al repetir."""
    if isinstance(exc, (ProviderTimeoutError, ProviderConnectionError)):
        return True
    return isinstance(exc, ProviderResponseError) and exc.status_code in RETRYABLE_STATUS


class FiveDollarFootballProvider(OddsProvider):
    name = "5dollarfootballapi"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        # Intentos HTTP iniciados por esta instancia (cada reintento cuenta como un intento más;
        # no es el consumo de cuota confirmado por el proveedor)
        self.request_stats = RequestStats()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a la API con reintentos acotados (ver _is_transient y MAX_ATTEMPTS).

        No hay un run de varias llamadas (hoy solo se usa check_status): el cliente HTTP vive lo
        que dura esta llamada lógica y sus reintentos lo reutilizan.
        """
        if not self._api_key:
            raise ProviderNotConfiguredError(
                self.name, "Falta FIVE_DOLLAR_FOOTBALL_API_KEY en el archivo .env"
            )

        self.request_stats.calls += 1
        async with new_client(self._timeout) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                self.request_stats.attempts += 1
                if attempt > 1:
                    self.request_stats.retries += 1
                started = time.perf_counter()
                try:
                    data = await self._get_once(path, params, client)
                except ProviderError as exc:
                    self.request_stats.failed_attempts += 1
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    if attempt == MAX_ATTEMPTS or not _is_transient(exc):
                        logger.warning(
                            "%s %s: intento %d/%d falló en %.0f ms (%s); no se reintenta",
                            self.name, path, attempt, MAX_ATTEMPTS, elapsed_ms, exc,
                        )
                        raise
                    logger.warning(
                        "%s %s: intento %d/%d falló en %.0f ms (%s), reintento en %.1fs",
                        self.name, path, attempt, MAX_ATTEMPTS, elapsed_ms, exc, TRANSIENT_RETRY_DELAY,
                    )
                    await _sleep(TRANSIENT_RETRY_DELAY)
                    continue
                logger.info(
                    "%s %s: intento %d/%d ok en %.0f ms",
                    self.name, path, attempt, MAX_ATTEMPTS, (time.perf_counter() - started) * 1000,
                )
                return data
        raise AssertionError("inalcanzable: el último intento siempre devuelve o lanza")

    async def _get_once(self, path: str, params: dict[str, Any] | None, client: httpx.AsyncClient) -> Any:
        """Una sola petición, con la traducción de la respuesta de error de la API a excepciones."""
        data = await get_json(
            provider=self.name,
            base_url=self._base_url,
            path=path,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
            params=params,
            client=client,
        )
        if not isinstance(data, dict):
            raise ProviderResponseError(self.name, "Formato de respuesta inesperado")

        if data.get("success") != 1:
            error = data.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else None
            raise ProviderResponseError(self.name, message or "La API indicó un error")
        return data.get("data")

    async def check_status(self) -> ProviderStatus:
        """Llama a GET /status (plan, límites y uso de la cuenta)."""
        info = await self._get("/status")
        if not isinstance(info, dict):
            info = {}

        limits = info.get("limits") or {}
        usage = info.get("usage") or {}

        return ProviderStatus(
            provider=self.name,
            reachable=True,
            plan=info.get("plan"),
            requests_used=usage.get("today"),
            requests_limit=None,  # la API informa el límite por ventana de tiempo (ver details)
            details={
                "rate_limit": limits.get("rate_limit"),
                "rate_window_seconds": limits.get("rate_window_seconds"),
                "burst_per_minute": limits.get("burst_per_minute"),
            },
        )
