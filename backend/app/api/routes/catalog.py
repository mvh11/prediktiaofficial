"""Endpoints del catálogo: competiciones, equipos y sincronización con el proveedor."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.errors import require_sync_endpoints_enabled, run_provider_call
from app.db.session import get_db
from app.repositories import catalog_repository as repo
from app.schemas.catalog import CatalogSyncResult, CompetitionOut, TeamOut
from app.services import catalog_sync_service

router = APIRouter(tags=["catalog"])


@router.get("/competitions", response_model=list[CompetitionOut])
def list_competitions(db: Session = Depends(get_db)) -> list[CompetitionOut]:
    """Competiciones guardadas en la BD, con su temporada actual."""
    return [
        CompetitionOut.model_validate(comp).model_copy(update={"current_season": current})
        for comp, current in repo.list_competitions_with_current_season(db)
    ]


@router.get("/competitions/{competition_id}/teams", response_model=list[TeamOut])
def list_competition_teams(
    competition_id: int,
    season: int | None = Query(default=None, description="Año de la temporada (por defecto, la actual)"),
    db: Session = Depends(get_db),
) -> list[TeamOut]:
    """Equipos de una competición en una temporada."""
    if repo.get_competition(db, competition_id) is None:
        raise HTTPException(status_code=404, detail="Competición no encontrada")
    season_row = repo.get_season(db, competition_id, season)
    if season_row is None:
        raise HTTPException(status_code=404, detail="Temporada no encontrada")
    return [TeamOut.model_validate(t) for t in repo.list_teams_for_season(db, season_row.id)]


@router.post("/sync/catalog", response_model=CatalogSyncResult, dependencies=[Depends(require_sync_endpoints_enabled)])
async def sync_catalog(db: Session = Depends(get_db)) -> CatalogSyncResult:
    """Descarga del proveedor las competiciones seguidas, sus temporadas y sus equipos."""
    return await run_provider_call(lambda: catalog_sync_service.sync_catalog(db))
