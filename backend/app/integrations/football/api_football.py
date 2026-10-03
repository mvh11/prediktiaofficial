"""Adapter de API-Football (v3).

Documentación oficial: https://www.api-football.com/documentation-v3
- Base URL: https://v3.football.api-sports.io
- Autenticación: cabecera "x-apisports-key"
- Todas las respuestas usan el formato: {get, parameters, errors, results, paging, response}
- Ojo: con una key inválida o al superar el límite de peticiones la API puede responder
  HTTP 200 con el detalle en "errors".
"""

from datetime import date
from typing import Any

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from app.integrations.football.base import FootballDataProvider
from app.integrations.http import get_json
from app.schemas.catalog import CompetitionData, SeasonData, TeamData
from app.schemas.fixture import FINISHED_STATUSES, FixtureData
from app.schemas.provider import ProviderStatus


class ApiFootballProvider(FootballDataProvider):
    name = "api-football"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout

    async def _get(
        self, path: str, params: dict[str, Any] | None = None, *, allow_paging: bool = False
    ) -> Any:
        """GET a la API. Si la respuesta tiene más de una página y el llamador no pagina
        (allow_paging=False), falla en lugar de perder datos en silencio."""
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
            if isinstance(errors, dict):
                if "token" in errors:
                    raise ProviderAuthError(self.name, f"Error de autenticación: {text}")
                if "rateLimit" in errors:
                    raise ProviderRateLimitError(self.name, f"Límite de peticiones por minuto superado: {text}")
                if "requests" in errors:
                    raise ProviderRateLimitError(self.name, f"Cuota diaria de peticiones agotada: {text}")
            raise ProviderResponseError(self.name, f"La API devolvió errores: {text}")

        paging = data.get("paging")
        total_pages = (paging.get("total") or 1) if isinstance(paging, dict) else 1
        if total_pages > 1 and not allow_paging:
            raise ProviderResponseError(
                self.name, f"Respuesta paginada ({total_pages} páginas) en {path}: este método no pagina"
            )
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

    async def get_competitions(self, external_ids: list[int]) -> list[CompetitionData]:
        """Llama una sola vez a GET /leagues y se queda con las competiciones pedidas.

        Una sola petición sale más barata que una por liga (/leagues?id=X).
        """
        data = await self._get("/leagues")
        wanted = set(external_ids)
        competitions: list[CompetitionData] = []

        for item in data.get("response") or []:
            league = item.get("league") or {}
            if league.get("id") not in wanted:
                continue
            country = item.get("country") or {}
            seasons = [
                SeasonData(
                    year=s["year"],
                    start_date=s.get("start"),
                    end_date=s.get("end"),
                    is_current=bool(s.get("current")),
                )
                for s in item.get("seasons") or []
                if s.get("year") is not None
            ]
            competitions.append(
                CompetitionData(
                    external_id=league["id"],
                    name=league.get("name") or "",
                    type=league.get("type"),
                    country=country.get("name"),
                    country_code=country.get("code"),
                    logo_url=league.get("logo"),
                    seasons=seasons,
                )
            )
        return competitions

    async def get_teams(self, competition_external_id: int, season: int) -> list[TeamData]:
        """Llama a GET /teams?league=X&season=Y."""
        data = await self._get("/teams", params={"league": competition_external_id, "season": season})
        teams: list[TeamData] = []
        for item in data.get("response") or []:
            team = item.get("team") or {}
            if team.get("id") is None:
                continue
            teams.append(
                TeamData(
                    external_id=team["id"],
                    name=team.get("name") or "",
                    code=team.get("code"),
                    country=team.get("country"),
                    founded=team.get("founded"),
                    is_national=bool(team.get("national")),
                    logo_url=team.get("logo"),
                )
            )
        return teams

    async def get_fixtures(
        self,
        competition_external_id: int,
        season: int,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[FixtureData]:
        """Llama a GET /fixtures?league=X&season=Y (y from/to si se indican).

        Devuelve todos los partidos de la temporada en una sola petición.
        """
        params: dict[str, Any] = {"league": competition_external_id, "season": season}
        if date_from:
            params["from"] = date_from.isoformat()
        if date_to:
            params["to"] = date_to.isoformat()
        data = await self._get("/fixtures", params=params)

        fixtures: list[FixtureData] = []
        for item in data.get("response") or []:
            fixture = item.get("fixture") or {}
            status = fixture.get("status") or {}
            venue = fixture.get("venue") or {}
            teams = item.get("teams") or {}
            goals = item.get("goals") or {}
            score = item.get("score") or {}
            halftime = score.get("halftime") or {}
            extratime = score.get("extratime") or {}
            penalty = score.get("penalty") or {}
            fulltime = score.get("fulltime") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            if fixture.get("id") is None or home.get("id") is None or away.get("id") is None:
                continue
            status_short = status.get("short") or "TBD"
            # El marcador a 90' solo tiene sentido en partidos terminados (FT, AET, PEN)
            finished = status_short in FINISHED_STATUSES

            fixtures.append(
                FixtureData(
                    external_id=fixture["id"],
                    competition_external_id=competition_external_id,
                    season=season,
                    round=(item.get("league") or {}).get("round"),
                    kickoff_at=fixture["date"],
                    status_short=status_short,
                    status_long=status.get("long"),
                    elapsed=status.get("elapsed"),
                    venue_name=venue.get("name"),
                    venue_city=venue.get("city"),
                    referee=fixture.get("referee"),
                    home_team=TeamData(external_id=home["id"], name=home.get("name") or "", logo_url=home.get("logo")),
                    away_team=TeamData(external_id=away["id"], name=away.get("name") or "", logo_url=away.get("logo")),
                    home_goals=goals.get("home"),
                    away_goals=goals.get("away"),
                    halftime_home=halftime.get("home"),
                    halftime_away=halftime.get("away"),
                    extratime_home=extratime.get("home"),
                    extratime_away=extratime.get("away"),
                    penalty_home=penalty.get("home"),
                    penalty_away=penalty.get("away"),
                    fulltime_home=fulltime.get("home") if finished else None,
                    fulltime_away=fulltime.get("away") if finished else None,
                )
            )
        return fixtures
