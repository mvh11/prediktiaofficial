"""Variante del adapter de API-Football para el backfill histórico.

No cambia el parseo ni la normalización (hereda get_fixtures tal cual). Solo conserva, para
cada partido de la última respuesta de /fixtures, la liga y la temporada QUE DECLARA EL
PROVEEDOR (league.id, league.season). FixtureData lleva la liga/temporada pedidas, no las
recibidas, y el backfill necesita comparar ambas antes de escribir (check Q12).
"""

from typing import Any

from app.integrations.football.api_football import ApiFootballProvider


class RecordingApiFootballProvider(ApiFootballProvider):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # {external_id: (league.id, league.season)} de la última respuesta de /fixtures
        self.last_fixture_identity: dict[int, tuple[int | None, int | None]] | None = None

    async def _get(self, path: str, params: dict[str, Any] | None = None, *, allow_paging: bool = False) -> Any:
        data = await super()._get(path, params, allow_paging=allow_paging)
        if path == "/fixtures":
            identity: dict[int, tuple[int | None, int | None]] = {}
            for item in data.get("response") or []:
                fixture_id = (item.get("fixture") or {}).get("id")
                league = item.get("league") or {}
                if fixture_id is not None:
                    identity[fixture_id] = (league.get("id"), league.get("season"))
            self.last_fixture_identity = identity
        return data
