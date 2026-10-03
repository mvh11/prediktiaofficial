"""Traducción de los errores de proveedores externos a respuestas HTTP."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

logger = logging.getLogger(__name__)


async def run_provider_call[T](check: Callable[[], Awaitable[T]]) -> T:
    """Ejecuta la llamada al proveedor y convierte los errores del proveedor en respuestas HTTP."""
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
