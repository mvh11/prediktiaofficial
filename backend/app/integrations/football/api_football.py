"""Adapter de API-Football (v3).

Documentación oficial: https://www.api-football.com/documentation-v3
- Base URL: https://v3.football.api-sports.io
- Autenticación: cabecera "x-apisports-key"
- Todas las respuestas usan el formato: {get, parameters, errors, results, paging, response}
- Ojo: con una key inválida la API puede responder HTTP 200 con el detalle en "errors".
"""

from typing import Any

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderNotConfiguredError,
    ProviderResponseError,
)
from app.integrations.football.base import FootballDataProvider
from app.integrations.http import get_json
from app.schemas.provider import ProviderStatus


class ApiFootballProvider(FootballDataProvider):
    name = "api-football"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self._api_key:
            raise ProviderNotConfiguredError(self.name, "Falta API_FOOTBALL_KEY en el archivo .env")

        data = await get_json(
            provider=self.name,
            base_url=self._base_url,
            path=path,
            headers={"x-apisports-key": self._api_key},
            timeout=self._timeout,
            params=params,
        )
        if not isinstance(data, dict):
            raise ProviderResponseError(self.name, "Formato de respuesta inesperado")

        errors = data.get("errors")
        if errors:  # puede venir como lista o como diccionario
            text = str(errors)
            if isinstance(errors, dict) and "token" in errors:
                raise ProviderAuthError(self.name, f"Error de autenticación: {text}")
            raise ProviderResponseError(self.name, f"La API devolvió errores: {text}")
        return data

    async def check_status(self) -> ProviderStatus:
        """Llama a GET /status (información de la cuenta y consumo diario)."""
        data = await self._get("/status")
        info = data.get("response") or {}
        if not isinstance(info, dict):
            info = {}

        subscription = info.get("subscription") or {}
        requests = info.get("requests") or {}

        # No devolvemos "account" (nombre y email) para no exponer datos personales.
        return ProviderStatus(
            provider=self.name,
            reachable=True,
            plan=subscription.get("plan"),
            requests_used=requests.get("current"),
            requests_limit=requests.get("limit_day"),
            details={
                "subscription_active": subscription.get("active"),
                "subscription_end": subscription.get("end"),
            },
        )
