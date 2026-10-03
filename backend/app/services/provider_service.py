"""Servicio que decide qué adapter usar para cada tipo de dato.

Los endpoints llaman a estas funciones y nunca importan un adapter concreto.
Si mañana cambiamos de proveedor, solo hay que tocar este archivo.
"""

from app.core.config import get_settings
from app.integrations.football.api_football import ApiFootballProvider
from app.integrations.football.base import FootballDataProvider
from app.integrations.odds.base import OddsProvider
from app.integrations.odds.five_dollar import FiveDollarFootballProvider
from app.schemas.provider import ProviderStatus


def get_football_provider() -> FootballDataProvider:
    settings = get_settings()
    return ApiFootballProvider(
        api_key=settings.api_football_key.get_secret_value(),
        base_url=settings.api_football_base_url,
        timeout=settings.http_timeout_seconds,
    )


def get_odds_provider() -> OddsProvider:
    settings = get_settings()
    return FiveDollarFootballProvider(
        api_key=settings.five_dollar_football_api_key.get_secret_value(),
        base_url=settings.five_dollar_football_base_url,
        timeout=settings.http_timeout_seconds,
    )


async def check_football_provider() -> ProviderStatus:
    return await get_football_provider().check_status()


async def check_odds_provider() -> ProviderStatus:
    return await get_odds_provider().check_status()
