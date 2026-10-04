"""Adapter de API-Football (v3).

Documentación oficial: https://www.api-football.com/documentation-v3
- Base URL: https://v3.football.api-sports.io
- Autenticación: cabecera "x-apisports-key"
- Todas las respuestas usan el formato: {get, parameters, errors, results, paging, response}
- Ojo: con una key inválida o al superar el límite de peticiones la API puede responder
  HTTP 200 con el detalle en "errors".
"""

import asyncio
import logging
from datetime import date
from typing import Any

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.integrations.football.base import FootballDataProvider
from app.integrations.http import get_json
from app.schemas.catalog import CompetitionData, SeasonData, TeamData
from app.schemas.fixture import FINISHED_STATUSES, FixtureData
from app.schemas.provider import ProviderStatus

logger = logging.getLogger(__name__)

# Reintentos de _get(): como mucho MAX_ATTEMPTS peticiones por llamada, sin jitter.
MAX_ATTEMPTS = 3
TRANSIENT_BACKOFF = (1.0, 2.0)  # timeout, conexión y 5xx
RATE_LIMIT_BACKOFF = (5.0, 10.0)  # límite de peticiones sin Retry-After utilizable
# Espera total máxima de una llamada a _get(): si la siguiente espera la superaría, no se
# espera y se lanza el error (con un límite de peticiones, el service corta la sync)
MAX_TOTAL_WAIT = 60.0
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


async def _sleep(seconds: float) -> None:
    """Espera entre reintentos (los tests la sustituyen para no esperar de verdad)."""
    await asyncio.sleep(seconds)


def _retry_delay(exc: ProviderError, retry_number: int) -> float | None:
    """Segundos a esperar antes del reintento número `retry_number` (0, 1...), o None si no se reintenta.

    - Cuota diaria agotada: nunca (no se renueva en segundos).
    - Límite de peticiones: lo que pida Retry-After, o RATE_LIMIT_BACKOFF si no lo indica.
    - Timeout, conexión y HTTP 500/502/503/504: TRANSIENT_BACKOFF.
    - Todo lo demás (4xx, autenticación, errores de la API, JSON inválido...) es permanente.
    """
    if isinstance(exc, ProviderQuotaExceededError):
        return None
    if isinstance(exc, ProviderRateLimitError):
        return RATE_LIMIT_BACKOFF[retry_number] if exc.retry_after is None else exc.retry_after
    if isinstance(exc, (ProviderTimeoutError, ProviderConnectionError)):
        return TRANSIENT_BACKOFF[retry_number]
    if isinstance(exc, ProviderResponseError) and exc.status_code in RETRYABLE_STATUS:
        return TRANSIENT_BACKOFF[retry_number]
    return None


ScorePair = tuple[int | None, int | None]


def _is_goal_count(value: Any) -> bool:
    # bool es subclase de int en Python: True/False no son goles
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_absent_score(raw: Any) -> bool:
    """True si el marcador falta o viene con local y visitante a NULL (ausente, no inválido)."""
    return raw is None or (isinstance(raw, dict) and raw.get("home") is None and raw.get("away") is None)


def _score_pair(raw: Any, label: str, fixture_id: Any) -> ScorePair:
    """Normaliza un marcador del proveedor a un par (local, visitante) completo o (None, None).

    Un par incompleto, negativo, con valores que no son enteros o con estructura inválida se
    descarta ENTERO (nunca se devuelve medio par) y se registra; el resto del partido se sigue
    ingiriendo.
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        logger.warning("Partido %s: marcador %s con formato inesperado (%r), se descarta", fixture_id, label, raw)
        return None, None
    home, away = raw.get("home"), raw.get("away")
    if home is None and away is None:
        return None, None
    if _is_goal_count(home) and _is_goal_count(away):
        return home, away
    logger.warning("Partido %s: marcador %s inválido (home=%r, away=%r), se descarta", fixture_id, label, home, away)
    return None, None


def _fulltime_pair(status_short: str, score: dict, goals: ScorePair, fixture_id: Any) -> ScorePair:
    """Marcador a 90' + descuento (sin prórroga ni penaltis), solo en FT/AET/PEN.

    - FT: score.fulltime. Si FALTA (sin la clave, null o con local y visitante a null), goals:
      sin prórroga, goals ES el 90'. Si viene pero es inválido o incompleto, (None, None): es un
      dato erróneo del proveedor, no un dato ausente, y no se sustituye por otro.
    - AET/PEN: solo score.fulltime. Nunca goals ni goals - extratime: extratime no tiene una
      semántica consistente en el histórico del proveedor (fixture 5862: acumulado).
    - Resto de estados: (None, None).
    Las incoherencias del proveedor (p. ej. fixture 6570: fulltime 4-0 con goals 2-0) se guardan
    tal cual para que los controles de calidad las detecten; aquí no se corrigen.
    """
    if status_short not in FINISHED_STATUSES:
        return None, None
    raw = score.get("fulltime")
    if status_short == "FT" and _is_absent_score(raw):
        return goals
    return _score_pair(raw, "fulltime", fixture_id)


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
        (allow_paging=False), falla en lugar de perder datos en silencio.

        Los errores transitorios se reintentan (ver _retry_delay) hasta MAX_ATTEMPTS peticiones
        y sin esperar en total más de MAX_TOTAL_WAIT segundos; si no se puede reintentar, se
        lanza el último error.
        """
        if not self._api_key:
            raise ProviderNotConfiguredError(self.name, "Falta API_FOOTBALL_KEY en el archivo .env")

        waited = 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._get_once(path, params, allow_paging=allow_paging)
            except ProviderError as exc:
                delay = _retry_delay(exc, attempt - 1) if attempt < MAX_ATTEMPTS else None
                if delay is not None and waited + delay > MAX_TOTAL_WAIT:
                    logger.warning(
                        "%s %s: no se reintenta, la espera total pasaría de %.0fs (%.1fs + %.1fs)",
                        self.name, path, MAX_TOTAL_WAIT, waited, delay,
                    )
                    raise
                if delay is None:
                    if attempt > 1:
                        logger.warning("%s %s: falla tras %d intentos: %s", self.name, path, attempt, exc)
                    raise
                logger.warning(
                    "%s %s: intento %d/%d falló (%s), reintento en %.1fs",
                    self.name, path, attempt, MAX_ATTEMPTS, exc, delay,
                )
                await _sleep(delay)
                waited += delay
        raise AssertionError("inalcanzable: el último intento siempre devuelve o lanza")

    async def _get_once(self, path: str, params: dict[str, Any] | None, *, allow_paging: bool) -> Any:
        """Una sola petición, con la traducción de errores de la API a excepciones."""
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
                # La cuota diaria va antes: si vienen las dos, reintentar no serviría de nada
                if "requests" in errors:
                    raise ProviderQuotaExceededError(self.name, f"Cuota diaria de peticiones agotada: {text}")
                if "rateLimit" in errors:
                    raise ProviderRateLimitError(self.name, f"Límite de peticiones por minuto superado: {text}")
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
            score = item.get("score")
            if not isinstance(score, dict):
                score = {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            if fixture.get("id") is None or home.get("id") is None or away.get("id") is None:
                continue
            fixture_id = fixture["id"]
            status_short = status.get("short") or "TBD"
            goals = _score_pair(item.get("goals"), "goals", fixture_id)
            halftime = _score_pair(score.get("halftime"), "halftime", fixture_id)
            extratime = _score_pair(score.get("extratime"), "extratime", fixture_id)
            penalty = _score_pair(score.get("penalty"), "penalty", fixture_id)
            fulltime = _fulltime_pair(status_short, score, goals, fixture_id)

            fixtures.append(
                FixtureData(
                    external_id=fixture_id,
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
                    home_goals=goals[0],
                    away_goals=goals[1],
                    halftime_home=halftime[0],
                    halftime_away=halftime[1],
                    extratime_home=extratime[0],
                    extratime_away=extratime[1],
                    penalty_home=penalty[0],
                    penalty_away=penalty[1],
                    fulltime_home=fulltime[0],
                    fulltime_away=fulltime[1],
                )
            )
        return fixtures
