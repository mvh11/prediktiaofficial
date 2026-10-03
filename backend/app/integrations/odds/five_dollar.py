"""Adapter de 5DollarFootballAPI.

Documentación oficial: https://5dollarfootballapi.com/docs
- Base URL: https://api.5dollarfootballapi.com/v1
- Autenticación: cabecera "Authorization: Bearer <API_KEY>" (también acepta "X-API-Key")
- Respuesta correcta:  {"success": 1, "data": ...}
- Respuesta de error:  {"success": 0, "error": {"type", "code", "message", ...}}
"""

from typing import Any

from app.integrations.exceptions import ProviderNotConfiguredError, ProviderResponseError
from app.integrations.http import get_json
from app.integrations.odds.base import OddsProvider
from app.schemas.provider import ProviderStatus


class FiveDollarFootballProvider(OddsProvider):
    name = "5dollarfootballapi"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self._api_key:
            raise ProviderNotConfiguredError(
                self.name, "Falta FIVE_DOLLAR_FOOTBALL_API_KEY en el archivo .env"
            )

        data = await get_json(
            provider=self.name,
            base_url=self._base_url,
            path=path,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
            params=params,
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
