"""Endpoints de partidos (fixtures) y resultados."""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.routes.providers import run_provider_call
from app.db.session import get_db
from app.models import Fixture
from app.repositories import fixture_repository as repo
from app.schemas.fixture import FINISHED_STATUSES, FixtureOut, FixtureSyncResult
from app.services import fixture_sync_service

router = APIRouter(tags=["fixtures"])


def _to_out(fixture: Fixture) -> FixtureOut:
    return FixtureOut.model_validate(
        {
            **{c: getattr(fixture, c) for c in FixtureOut.model_fields if hasattr(fixture, c) and c != "season"},
            "competition_id": fixture.season.competition_id,
            "season": fixture.season.year,
            "is_finished": fixture.status_short in FINISHED_STATUSES,
        }
    )


def _day_start(day: date | None) -> datetime | None:
    return datetime.combine(day, time.min, tzinfo=timezone.utc) if day else None


@router.get("/fixtures", response_model=list[FixtureOut])
def list_fixtures(
    competition_id: int | None = Query(default=None, description="ID interno de la competición"),
    season: int | None = Query(default=None, description="Año de la temporada"),
    team_id: int | None = Query(default=None, description="Partidos de este equipo (local o visitante)"),
    date_from: date | None = Query(default=None, description="Desde este día (UTC, incluido)"),
    date_to: date | None = Query(default=None, description="Hasta este día (UTC, incluido)"),
    finished: bool | None = Query(default=None, description="true: solo terminados; false: solo pendientes"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[FixtureOut]:
    """Partidos guardados en la BD, ordenados por fecha."""
    fixtures = repo.list_fixtures(
        db,
        competition_id=competition_id,
        season=season,
        team_id=team_id,
        date_from=_day_start(date_from),
        date_to=_day_start(date_to + timedelta(days=1)) if date_to else None,
        finished=finished,
        limit=limit,
        offset=offset,
    )
    return [_to_out(f) for f in fixtures]


@router.get("/fixtures/{fixture_id}", response_model=FixtureOut)
def get_fixture(fixture_id: int, db: Session = Depends(get_db)) -> FixtureOut:
    fixture = repo.get_fixture(db, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    return _to_out(fixture)


@router.post("/sync/fixtures", response_model=FixtureSyncResult)
async def sync_fixtures(
    competition_id: int | None = Query(default=None, description="Solo esta competición (por defecto, todas)"),
    date_from: date | None = Query(default=None, description="Solo partidos desde este día"),
    date_to: date | None = Query(default=None, description="Solo partidos hasta este día"),
    db: Session = Depends(get_db),
) -> FixtureSyncResult:
    """Descarga del proveedor los partidos y resultados de la temporada actual."""
    if (date_from is None) != (date_to is None):
        raise HTTPException(status_code=422, detail="Indica date_from y date_to juntos")
    return await run_provider_call(
        lambda: fixture_sync_service.sync_fixtures(db, competition_id, date_from, date_to)
    )
