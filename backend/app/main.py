"""Punto de entrada de la aplicación FastAPI.

Arrancar con (desde la carpeta backend/):
    uvicorn app.main:app --reload
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import mask_secret, setup_logging

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("prediktia")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Arrancando %s", settings.app_name)
    logger.info("API-Football key: %s", mask_secret(settings.api_football_key.get_secret_value()))
    logger.info(
        "5DollarFootballAPI key: %s",
        mask_secret(settings.five_dollar_football_api_key.get_secret_value()),
    )
    yield
    logger.info("Apagando %s", settings.app_name)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
