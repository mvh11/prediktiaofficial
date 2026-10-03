"""Router principal: agrupa todas las rutas de la API."""

from fastapi import APIRouter

from app.api.routes import catalog, fixtures, health, providers

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(providers.router)
api_router.include_router(catalog.router)
api_router.include_router(fixtures.router)
