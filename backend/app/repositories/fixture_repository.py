"""Acceso a la BD para partidos (fixtures).

Igual que el catálogo, los upserts usan INSERT ... ON CONFLICT y una sola sentencia
por lote para no hacer una ida y vuelta a la BD por cada fila.
"""

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.models import Fixture, Season, Team
from app.schemas.catalog import TeamData
from app.schemas.fixture import FINISHED_STATUSES, FixtureData

# Campos de FixtureData que se copian tal cual a la tabla
_FIXTURE_COLUMNS = [
    f for f in FixtureData.model_fields if f not in {"competition_external_id", "season", "home_team", "away_team"}
]


def ensure_teams(db: Session, teams: list[TeamData]) -> dict[int, int]:
    """Crea los equipos que aún no existen (sin tocar los existentes). Devuelve {external_id: id}.

    Hace falta porque en un partido puede aparecer un equipo que no salió en /teams
    (por ejemplo, de una ronda previa de una copa).
    """
    unique = {t.external_id: t for t in teams}
    if not unique:
        return {}
    stmt = insert(Team).values(
        [{"external_id": t.external_id, "name": t.name, "logo_url": t.logo_url} for t in unique.values()]
    )
    db.execute(stmt.on_conflict_do_nothing(index_elements=[Team.external_id]))
    rows = db.execute(select(Team.external_id, Team.id).where(Team.external_id.in_(unique)))
    return {external_id: team_id for external_id, team_id in rows}


def upsert_fixtures(
    db: Session, season_id: int, fixtures: list[FixtureData], team_ids: dict[int, int]
) -> int:
    """Guarda los partidos de una temporada en una sola sentencia. Devuelve cuántos se guardaron."""
    unique = list({f.external_id: f for f in fixtures}.values())
    if not unique:
        return 0
    rows = [
        {
            **f.model_dump(include=set(_FIXTURE_COLUMNS)),
            "season_id": season_id,
            "home_team_id": team_ids[f.home_team.external_id],
            "away_team_id": team_ids[f.away_team.external_id],
        }
        for f in unique
    ]
    stmt = insert(Fixture).values(rows)
    updatable = [k for k in rows[0] if k != "external_id"]
    stmt = stmt.on_conflict_do_update(
        index_elements=[Fixture.external_id],
        set_={**{k: stmt.excluded[k] for k in updatable}, "updated_at": func.now()},
    )
    db.execute(stmt)
    return len(rows)


# --- Lecturas -----------------------------------------------------------------------------


def list_fixtures(
    db: Session,
    *,
    competition_id: int | None = None,
    season: int | None = None,
    team_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    finished: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Fixture]:
    stmt = (
        select(Fixture)
        .join(Season, Season.id == Fixture.season_id)
        .options(joinedload(Fixture.season), joinedload(Fixture.home_team), joinedload(Fixture.away_team))
    )
    if competition_id is not None:
        stmt = stmt.where(Season.competition_id == competition_id)
    if season is not None:
        stmt = stmt.where(Season.year == season)
    if team_id is not None:
        stmt = stmt.where(or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id))
    if date_from is not None:
        stmt = stmt.where(Fixture.kickoff_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Fixture.kickoff_at < date_to)
    if finished is True:
        stmt = stmt.where(Fixture.status_short.in_(FINISHED_STATUSES))
    elif finished is False:
        stmt = stmt.where(Fixture.status_short.not_in(FINISHED_STATUSES))

    stmt = stmt.order_by(Fixture.kickoff_at, Fixture.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get_fixture(db: Session, fixture_id: int) -> Fixture | None:
    stmt = (
        select(Fixture)
        .where(Fixture.id == fixture_id)
        .options(joinedload(Fixture.season), joinedload(Fixture.home_team), joinedload(Fixture.away_team))
    )
    return db.scalars(stmt).first()
