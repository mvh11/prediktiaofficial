"""Router principal: agrupa todas las rutas de la API."""

from fastapi import APIRouter

from app.api.routes import health, providers

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(providers.router)
