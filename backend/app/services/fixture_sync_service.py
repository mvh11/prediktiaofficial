"""Sincroniza los partidos (y sus resultados) desde el proveedor de fútbol.

Usa las competiciones y temporadas ya guardadas por la sync del catálogo.
Coste en peticiones: 1 llamada a /fixtures por competición (26 con la lista por defecto).
"""

import logging
from datetime import date

from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.exceptions import ProviderAuthError, ProviderError, ProviderRateLimitError
from app.repositories import catalog_repository, fixture_repository
from app.schemas.fixture import CompetitionFixtureSyncResult, FixtureSyncResult
from app.services.provider_service import get_football_provider

logger = logging.getLogger(__name__)


async def sync_fixtures(
    db: Session,
    competition_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
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
    Solo se sincronizan las competiciones de TRACKED_LEAGUE_IDS (IDs de API-Football).
    """
    provider = get_football_provider()
    tracked = set(get_settings().tracked_league_ids)
    competitions = catalog_repository.list_competitions(db)
    if competition_id is not None:
        competitions = [c for c in competitions if c.id == competition_id]

    results: list[CompetitionFixtureSyncResult] = []
    stopped: str | None = None  # motivo por el que ya no se llama al proveedor
    for comp in competitions:
        result = CompetitionFixtureSyncResult(competition_id=comp.id, name=comp.name)
        if comp.external_id not in tracked:
            if competition_id is not None:  # pedida explícitamente: se explica por qué no se sincroniza
                result.error = "No está en TRACKED_LEAGUE_IDS"
                results.append(result)
            continue
        results.append(result)
        if stopped:
            result.error = stopped
            continue

        season = catalog_repository.get_season(db, comp.id, None)
        if season is None:
            result.error = "Sin temporada actual (ejecuta antes POST /sync/catalog)"
            continue
        result.season = season.year

        try:
            fixtures = await provider.get_fixtures(comp.external_id, season.year, date_from, date_to)
        except ProviderError as exc:
            logger.warning("No se pudieron obtener los partidos de %s: %s", comp.name, exc)
            result.error = exc.message
            if isinstance(exc, ProviderRateLimitError):
                stopped = f"No sincronizada: se detuvo la sync por el límite del proveedor ({exc.message})"
                logger.warning("Límite del proveedor: no se piden las competiciones restantes")
            elif isinstance(exc, ProviderAuthError):
                stopped = (
                    f"No sincronizada: se detuvo la sync porque el proveedor rechazó las credenciales ({exc.message})"
                )
                logger.warning("Credenciales rechazadas: no se piden las competiciones restantes")
            continue

        try:
            team_ids = fixture_repository.ensure_teams(
                db, [f.home_team for f in fixtures] + [f.away_team for f in fixtures], provider.name
            )
            saved = fixture_repository.upsert_fixtures(db, season.id, fixtures, team_ids, provider.name)
            db.commit()
        except (IntegrityError, DataError, OperationalError) as exc:
            db.rollback()
            logger.exception("Error de BD guardando los partidos de %s: se deshacen sus cambios", result.name)
            result.error = f"Error de BD al guardar los partidos ({exc.__class__.__name__}); cambios deshechos"
            continue
        result.fixtures = saved
        logger.info("%s %s: %d partidos", comp.name, season.year, result.fixtures)

    return FixtureSyncResult(
        fixtures_synced=sum(r.fixtures for r in results),
        competitions=results,
    )
