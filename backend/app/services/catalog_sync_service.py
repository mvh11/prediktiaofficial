"""Sincroniza el catálogo (competiciones, temporadas y equipos) desde el proveedor de fútbol.

Coste en peticiones: 1 llamada a /leagues + 1 llamada a /teams por competición.
Con las 26 competiciones por defecto son 27 peticiones.
"""

import logging

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.exceptions import ProviderError
from app.repositories import catalog_repository as repo
from app.schemas.catalog import CatalogSyncResult, CompetitionSyncResult
from app.services.provider_service import get_football_provider

logger = logging.getLogger(__name__)


async def sync_catalog(db: Session) -> CatalogSyncResult:
    """Guarda las competiciones seguidas, sus temporadas y los equipos de la temporada actual.

    Si falla una competición se anota el error y se sigue con las demás.
    Los errores de la llamada inicial a /leagues sí se propagan (sin ella no hay nada que hacer).
    """
    provider = get_football_provider()
    tracked_ids = get_settings().tracked_league_ids

    competitions = await provider.get_competitions(tracked_ids)
    found_ids = {c.external_id for c in competitions}
    results = [
        CompetitionSyncResult(external_id=i, error="No existe en el proveedor")
        for i in tracked_ids
        if i not in found_ids
    ]

    for comp in competitions:
        result = CompetitionSyncResult(external_id=comp.external_id, name=comp.name)
        results.append(result)

        competition_id = repo.upsert_competition(db, comp)
        repo.clear_current_flag(db, competition_id)
        season_ids = repo.upsert_seasons(db, competition_id, comp.seasons)
        db.commit()

        current = next((s for s in comp.seasons if s.is_current), None)
        if current is None:
            result.error = "El proveedor no indica temporada actual"
            continue
        result.season = current.year

        try:
            teams = await provider.get_teams(comp.external_id, current.year)
        except ProviderError as exc:
            logger.warning("No se pudieron obtener los equipos de %s: %s", comp.name, exc)
            result.error = exc.message
            continue

        team_ids = repo.upsert_teams(db, teams)
        repo.link_teams_to_season(db, season_ids[current.year], team_ids)
        db.commit()
        result.teams = len(teams)
        logger.info("%s %s: %d equipos", comp.name, current.year, len(teams))

    return CatalogSyncResult(
        competitions_synced=sum(1 for r in results if r.name is not None),
        teams_synced=sum(r.teams for r in results),
        competitions=results,
    )
