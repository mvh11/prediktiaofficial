"""Endpoints de prueba para comprobar la comunicación con los proveedores externos."""

from fastapi import APIRouter

from app.api.errors import run_provider_call
from app.schemas.provider import ProviderStatus
from app.services import provider_service

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/football/status", response_model=ProviderStatus)
async def football_provider_status() -> ProviderStatus:
    """Prueba la conexión con el proveedor de datos futbolísticos (API-Football)."""
    return await run_provider_call(provider_service.check_football_provider)


@router.get("/odds/status", response_model=ProviderStatus)
async def odds_provider_status() -> ProviderStatus:
    """Prueba la conexión con el proveedor de cuotas (5DollarFootballAPI)."""
    return await run_provider_call(provider_service.check_odds_provider)
