"""Sincroniza el catálogo (competiciones, temporadas y equipos) desde el proveedor de fútbol.

Coste en peticiones: 1 llamada a /leagues + 1 llamada a /teams por competición.
Con las 26 competiciones por defecto son 27 peticiones.
"""

import logging
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.exceptions import ProviderAuthError, ProviderError, ProviderRateLimitError
from app.integrations.request_stats import describe, stats_of
from app.repositories import catalog_repository as repo
from app.schemas.catalog import CatalogSyncResult, CompetitionSyncResult
from app.services.provider_service import get_football_provider

logger = logging.getLogger(__name__)


async def sync_catalog(db: Session) -> CatalogSyncResult:
    """Guarda las competiciones seguidas, sus temporadas y los equipos de la temporada actual.

    Si falla una competición se anota el error y se sigue con las demás.
    Excepción: si el proveedor sigue limitando las peticiones tras los reintentos o la cuota
    diaria está agotada (ProviderRateLimitError), o si rechaza las credenciales
    (ProviderAuthError: HTTP 401/403 o errors.token), ya no se piden los equipos de las
    competiciones restantes (sus datos de /leagues sí se guardan) y quedan con el motivo.
    Los errores de la llamada inicial a /leagues sí se propagan (sin ella no hay nada que hacer).
    Registra en el log la duración y las peticiones (con reintentos) de cada competición y del total.
    """
    sync_started = time.perf_counter()
    provider = get_football_provider()
    stats = stats_of(provider)
    stats_at_start = stats.snapshot() if stats else None
    tracked_ids = get_settings().tracked_league_ids

    competitions = await provider.get_competitions(tracked_ids)
    found_ids = {c.external_id for c in competitions}
    results = [
        CompetitionSyncResult(external_id=i, error="No existe en el proveedor")
        for i in tracked_ids
        if i not in found_ids
    ]

    stopped: str | None = None  # motivo por el que ya no se llama al proveedor
    for comp in competitions:
        result = CompetitionSyncResult(external_id=comp.external_id, name=comp.name)
        results.append(result)

        competition_started = time.perf_counter()
        stats_before = stats.snapshot() if stats else None
        crashed = False
        try:
            competition_id = repo.upsert_competition(db, comp, provider.name)
            repo.clear_current_flag(db, competition_id)
            season_ids = repo.upsert_seasons(db, competition_id, comp.seasons)
            db.commit()

            current = next((s for s in comp.seasons if s.is_current), None)
            if current is None:
                result.error = "El proveedor no indica temporada actual"
                continue
            result.season = current.year
            if stopped:
                result.error = stopped
                continue

            try:
                teams = await provider.get_teams(comp.external_id, current.year)
            except ProviderError as exc:
                logger.warning("No se pudieron obtener los equipos de %s: %s", comp.name, exc)
                result.error = exc.message
                if isinstance(exc, ProviderRateLimitError):
                    stopped = f"Equipos no sincronizados: se detuvo la sync por el límite del proveedor ({exc.message})"
                    logger.warning("Límite del proveedor: no se piden los equipos de las competiciones restantes")
                elif isinstance(exc, ProviderAuthError):
                    stopped = (
                        "Equipos no sincronizados: se detuvo la sync porque el proveedor rechazó las credenciales "
                        f"({exc.message})"
                    )
                    logger.warning("Credenciales rechazadas: no se piden los equipos de las competiciones restantes")
                continue

            team_ids = repo.upsert_teams(db, teams, provider.name)
            repo.link_teams_to_season(db, season_ids[current.year], team_ids)
            db.commit()
            result.teams = len(teams)
        except BaseException:
            crashed = True  # error no previsto: se propaga, pero el log no debe decir "ok"
            raise
        finally:
            logger.info(
                "Sync catálogo · %s: %s · %d equipos · %.0f ms · %s",
                comp.name,
                "excepción no controlada" if crashed else ("ok" if result.error is None else f"error ({result.error})"),
                result.teams,
                (time.perf_counter() - competition_started) * 1000,
                describe(stats.since(stats_before) if stats else None),
            )

    logger.info(
        "Sync catálogo · total: %d competiciones (%d con error) · %d equipos · %.0f ms · %s",
        len(results),
        sum(1 for r in results if r.error is not None),
        sum(r.teams for r in results),
        (time.perf_counter() - sync_started) * 1000,
        describe(stats.since(stats_at_start) if stats else None),
    )
    return CatalogSyncResult(
        competitions_synced=sum(1 for r in results if r.name is not None),
        teams_synced=sum(r.teams for r in results),
        competitions=results,
    )
