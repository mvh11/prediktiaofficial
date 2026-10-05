"""Sincroniza los partidos (y sus resultados) desde el proveedor de fútbol.

Usa las competiciones y temporadas ya guardadas por la sync del catálogo.
Coste en peticiones: 1 llamada a /fixtures por competición elegible (26 con la lista por defecto,
menos las temporadas "dormant", ver polling_eligibility).

Transacciones, por competición:
1. Lectura de la temporada actual y de su elegibilidad; la transacción se cierra ANTES de llamar
   al proveedor (no se mantiene ninguna transacción de BD abierta durante el HTTP).
2. Llamada al proveedor.
3. Nueva transacción: se revalida que esa temporada sigue siendo la actual (la sync del catálogo
   pudo cambiarla durante la descarga); si no, no se escribe nada (skipped).
4. Equipos, partidos y mapeos de esa competición y commit: cada competición es atómica y una
   competición que falla no afecta a las demás.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timezone

from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.exceptions import ProviderAuthError, ProviderError, ProviderRateLimitError
from app.integrations.football.base import FootballDataProvider
from app.repositories import catalog_repository, fixture_repository
from app.schemas.fixture import CompetitionFixtureSyncResult, FixtureSyncResult
from app.services.polling_eligibility import polling_eligibility
from app.services.provider_service import get_football_provider

logger = logging.getLogger(__name__)

# Errores de BD que afectan a los datos o a la conexión de UNA competición: se deshace esa
# competición y se sigue con las demás. Los errores de programación (SQL mal construido,
# esquema desalineado...) no se capturan: fallarían igual en todas y deben verse.
_ISOLATED_DB_ERRORS = (IntegrityError, DataError, OperationalError)

# Se llama dentro de la transacción de cada competición, justo antes de su commit (o en su propia
# transacción si la competición no escribe): permite auditar la competición de forma atómica con
# sus datos (ver app.jobs.live_sync).
CompetitionHook = Callable[[Session, CompetitionFixtureSyncResult], None]


async def sync_fixtures(
    db: Session,
    competition_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    *,
    provider: FootballDataProvider | None = None,
    on_competition: CompetitionHook | None = None,
    today: date | None = None,
) -> FixtureSyncResult:
    """Guarda los partidos de la temporada actual de cada competición (o de una sola).

    Con date_from/date_to solo se piden los partidos de ese rango, útil para
    actualizar resultados recientes sin descargar toda la temporada.
    Si falla una competición (proveedor o BD) se anota el error en su resultado y se sigue
    con las demás; un error de BD deshace solo los cambios de esa competición.
    Excepción: si el proveedor sigue limitando las peticiones tras los reintentos o la cuota
    diaria está agotada (ProviderRateLimitError), o si rechaza las credenciales
    (ProviderAuthError: HTTP 401/403 o errors.token), las competiciones restantes no se piden
    y quedan con el motivo en su error.
    Solo se sincronizan las competiciones de TRACKED_LEAGUE_IDS (IDs de API-Football) cuya
    temporada actual es elegible para el polling (las "dormant" quedan como skipped).
    """
    provider = provider or get_football_provider()
    today = today or datetime.now(timezone.utc).date()
    tracked = set(get_settings().tracked_league_ids)
    competitions = catalog_repository.list_competitions(db)
    if competition_id is not None:
        competitions = [c for c in competitions if c.id == competition_id]
    # Solo valores planos: la sesión se cierra (commit sin escrituras) antes de cada HTTP
    targets = [(c.id, c.name, c.external_id) for c in competitions]

    results: list[CompetitionFixtureSyncResult] = []
    stopped: str | None = None  # motivo por el que ya no se llama al proveedor
    for comp_id, comp_name, league_external_id in targets:
        result = CompetitionFixtureSyncResult(competition_id=comp_id, name=comp_name)
        if league_external_id not in tracked:
            if competition_id is not None:  # pedida explícitamente: se explica por qué no se sincroniza
                result.error = "No está en TRACKED_LEAGUE_IDS"
                results.append(result)
            continue
        results.append(result)
        if stopped:
            result.error = stopped
            _report(db, on_competition, result)
            continue

        # 1. Lecturas y cierre de la transacción antes del HTTP
        season = catalog_repository.get_season(db, comp_id, None)
        if season is None:
            result.error = "Sin temporada actual (ejecuta antes POST /sync/catalog)"
            _report(db, on_competition, result)
            continue
        season_id, season_year = season.id, season.year
        result.season = season_year
        eligibility = polling_eligibility(db, season_id, today)
        # Commit sin escrituras: termina la transacción de lectura (no deshace nada del llamador)
        db.commit()
        if not eligibility.eligible:
            result.skipped = eligibility.reason
            _report(db, on_competition, result)
            continue

        # 2. Proveedor, sin transacción de BD abierta
        try:
            fixtures = await provider.get_fixtures(league_external_id, season_year, date_from, date_to)
        except ProviderError as exc:
            logger.warning("No se pudieron obtener los partidos de %s: %s", comp_name, exc)
            result.error = exc.message
            if isinstance(exc, ProviderRateLimitError):
                stopped = f"No sincronizada: se detuvo la sync por el límite del proveedor ({exc.message})"
                logger.warning("Límite del proveedor: no se piden las competiciones restantes")
            elif isinstance(exc, ProviderAuthError):
                stopped = (
                    f"No sincronizada: se detuvo la sync porque el proveedor rechazó las credenciales ({exc.message})"
                )
                logger.warning("Credenciales rechazadas: no se piden las competiciones restantes")
            _report(db, on_competition, result)
            continue

        # 3. Revalidación y 4. escritura atómica de la competición
        try:
            current = catalog_repository.get_season(db, comp_id, None)
            if current is None or current.id != season_id:
                db.rollback()
                result.skipped = f"season changed: la temporada {season_year} dejó de ser la actual durante la descarga"
                _report(db, on_competition, result)
                continue
            teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
            team_external_ids = list({t.external_id for t in teams})
            existing_teams = fixture_repository.count_existing_teams(db, team_external_ids)
            team_ids = fixture_repository.ensure_teams(db, teams, provider.name)
            counts = fixture_repository.upsert_fixtures(db, season_id, fixtures, team_ids, provider.name)
            result.fixtures, result.created, result.updated, result.unchanged = (
                counts.received, counts.created, counts.updated, counts.unchanged,
            )
            result.teams_created = len(team_external_ids) - existing_teams
            if on_competition is not None:
                on_competition(db, result)
            db.commit()
        except _ISOLATED_DB_ERRORS as exc:
            db.rollback()
            logger.exception("Error de BD guardando los partidos de %s: se deshacen sus cambios", result.name)
            result.fixtures = result.created = result.updated = result.unchanged = result.teams_created = 0
            result.error = f"Error de BD al guardar los partidos ({exc.__class__.__name__}); cambios deshechos"
            _report(db, on_competition, result)
            continue
        logger.info("%s %s: %d partidos", comp_name, season_year, result.fixtures)

    return FixtureSyncResult(
        fixtures_synced=sum(r.fixtures for r in results),
        competitions=results,
    )


def _report(db: Session, on_competition: CompetitionHook | None, result: CompetitionFixtureSyncResult) -> None:
    """Auditoría de una competición que no escribe datos (error o skipped), en su propia transacción."""
    if on_competition is None:
        return
    on_competition(db, result)
    db.commit()
