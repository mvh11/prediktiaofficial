"""Acceso a la BD para el catálogo (competiciones, temporadas y equipos).

Los upserts usan INSERT ... ON CONFLICT de PostgreSQL: si el registro ya existe
(mismo external_id), se actualiza en lugar de duplicarse.

Escritura doble (transición multi-proveedor): cada upsert registra también el ID del
proveedor en su tabla de mapeo, en la misma transacción.
"""

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    Competition,
    CompetitionProviderMapping,
    Season,
    SeasonTeam,
    Team,
    TeamProviderMapping,
)
from app.repositories.provider_mapping_repository import upsert_origin_mappings
from app.schemas.catalog import CompetitionData, SeasonData, TeamData


def upsert_competition(db: Session, data: CompetitionData, provider: str) -> int:
    values = data.model_dump(exclude={"seasons"})
    stmt = insert(Competition).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Competition.external_id],
        set_={**{k: stmt.excluded[k] for k in values if k != "external_id"}, "updated_at": func.now()},
    ).returning(Competition.id)
    competition_id = db.execute(stmt).scalar_one()
    upsert_origin_mappings(
        db, CompetitionProviderMapping, provider, [(competition_id, data.external_id, data.name)]
    )
    return competition_id


def upsert_seasons(db: Session, competition_id: int, seasons: list[SeasonData]) -> dict[int, int]:
    """Guarda todas las temporadas en una sola sentencia. Devuelve {año: id}."""
    if not seasons:
        return {}
    stmt = insert(Season).values([{"competition_id": competition_id, **s.model_dump()} for s in seasons])
    stmt = stmt.on_conflict_do_update(
        constraint="uq_seasons_competition_year",
        set_={k: stmt.excluded[k] for k in ("start_date", "end_date", "is_current")},
    ).returning(Season.year, Season.id)
    return {year: season_id for year, season_id in db.execute(stmt)}


def clear_current_flag(db: Session, competition_id: int) -> None:
    """Marca todas las temporadas como no actuales antes de volver a sincronizarlas."""
    db.execute(update(Season).where(Season.competition_id == competition_id).values(is_current=False))


def upsert_teams(db: Session, teams: list[TeamData], provider: str) -> list[int]:
    """Guarda todos los equipos en una sola sentencia. Devuelve sus ids internos."""
    if not teams:
        return []
    # Sin duplicados: ON CONFLICT no admite tocar la misma fila dos veces en una sentencia
    unique = list({t.external_id: t for t in teams}.values())
    stmt = insert(Team).values([t.model_dump() for t in unique])
    columns = [k for k in TeamData.model_fields if k != "external_id"]
    stmt = stmt.on_conflict_do_update(
        index_elements=[Team.external_id],
        set_={**{k: stmt.excluded[k] for k in columns}, "updated_at": func.now()},
    ).returning(Team.external_id, Team.id)
    ids = {external_id: team_id for external_id, team_id in db.execute(stmt)}
    upsert_origin_mappings(
        db, TeamProviderMapping, provider, [(ids[t.external_id], t.external_id, t.name) for t in unique]
    )
    return list(ids.values())


def link_teams_to_season(db: Session, season_id: int, team_ids: list[int]) -> None:
    if not team_ids:
        return
    stmt = insert(SeasonTeam).values([{"season_id": season_id, "team_id": t} for t in team_ids])
    db.execute(stmt.on_conflict_do_nothing())


# --- Lecturas -----------------------------------------------------------------------------


def list_competitions(db: Session) -> list[Competition]:
    return list(db.scalars(select(Competition).order_by(Competition.country, Competition.name)))


def list_competitions_with_current_season(db: Session) -> list[tuple[Competition, int | None]]:
    """Competiciones con el año de su temporada actual, en una sola consulta."""
    stmt = (
        select(Competition, Season.year)
        .outerjoin(Season, and_(Season.competition_id == Competition.id, Season.is_current.is_(True)))
        .order_by(Competition.country, Competition.name)
    )
    return [(competition, year) for competition, year in db.execute(stmt)]


def get_competition(db: Session, competition_id: int) -> Competition | None:
    return db.get(Competition, competition_id)


def get_season(db: Session, competition_id: int, year: int | None) -> Season | None:
    """Devuelve la temporada pedida o, si year es None, la temporada actual."""
    stmt = select(Season).where(Season.competition_id == competition_id)
    stmt = stmt.where(Season.year == year) if year is not None else stmt.where(Season.is_current.is_(True))
    return db.scalars(stmt).first()


def list_teams_for_season(db: Session, season_id: int) -> list[Team]:
    stmt = (
        select(Team)
        .join(SeasonTeam, SeasonTeam.team_id == Team.id)
        .where(SeasonTeam.season_id == season_id)
        .order_by(Team.name)
    )
    return list(db.scalars(stmt))
