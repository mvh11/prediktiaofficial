"""Endpoints de prueba para comprobar la comunicación con los proveedores externos."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.schemas.provider import ProviderStatus
from app.services import provider_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/providers", tags=["providers"])


async def _run_check(check: Callable[[], Awaitable[ProviderStatus]]) -> ProviderStatus:
    """Ejecuta la comprobación y convierte los errores del proveedor en respuestas HTTP."""
    try:
        return await check()
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    except ProviderAuthError as exc:
        logger.warning("%s", exc)
        raise HTTPException(status_code=502, detail=f"{exc.provider}: {exc.message}") from exc
    except ProviderRateLimitError as exc:
        logger.warning("%s", exc)
        raise HTTPException(status_code=429, detail=f"{exc.provider}: {exc.message}") from exc
    except ProviderTimeoutError as exc:
        logger.warning("%s", exc)
        raise HTTPException(status_code=504, detail=f"{exc.provider}: {exc.message}") from exc
    except ProviderError as exc:
        logger.warning("%s", exc)
        raise HTTPException(status_code=502, detail=f"{exc.provider}: {exc.message}") from exc


@router.get("/football/status", response_model=ProviderStatus)
async def football_provider_status() -> ProviderStatus:
    """Prueba la conexión con el proveedor de datos futbolísticos (API-Football)."""
    return await _run_check(provider_service.check_football_provider)


@router.get("/odds/status", response_model=ProviderStatus)
async def odds_provider_status() -> ProviderStatus:
    """Prueba la conexión con el proveedor de cuotas (5DollarFootballAPI)."""
    return await _run_check(provider_service.check_odds_provider)
