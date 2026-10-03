"""Sincroniza los partidos (y sus resultados) desde el proveedor de fútbol.

Usa las competiciones y temporadas ya guardadas por la sync del catálogo.
Coste en peticiones: 1 llamada a /fixtures por competición (26 con la lista por defecto).
"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.integrations.exceptions import ProviderError
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
    Si falla una competición se anota el error y se sigue con las demás.
    """
    provider = get_football_provider()
    competitions = catalog_repository.list_competitions(db)
    if competition_id is not None:
        competitions = [c for c in competitions if c.id == competition_id]

    results: list[CompetitionFixtureSyncResult] = []
    for comp in competitions:
        result = CompetitionFixtureSyncResult(competition_id=comp.id, name=comp.name)
        results.append(result)

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
            continue

        team_ids = fixture_repository.ensure_teams(
            db, [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
        )
        result.fixtures = fixture_repository.upsert_fixtures(db, season.id, fixtures, team_ids)
        db.commit()
        logger.info("%s %s: %d partidos", comp.name, season.year, result.fixtures)

    return FixtureSyncResult(
        fixtures_synced=sum(r.fixtures for r in results),
        competitions=results,
    )
