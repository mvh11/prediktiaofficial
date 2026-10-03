"""Acceso a la BD para el catálogo (competiciones, temporadas y equipos).

Los upserts usan INSERT ... ON CONFLICT de PostgreSQL: si el registro ya existe
(mismo external_id), se actualiza en lugar de duplicarse.
"""

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Competition, Season, SeasonTeam, Team
from app.schemas.catalog import CompetitionData, SeasonData, TeamData


def upsert_competition(db: Session, data: CompetitionData) -> int:
    values = data.model_dump(exclude={"seasons"})
    stmt = insert(Competition).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Competition.external_id],
        set_={**{k: stmt.excluded[k] for k in values if k != "external_id"}, "updated_at": func.now()},
    ).returning(Competition.id)
    return db.execute(stmt).scalar_one()


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


def upsert_teams(db: Session, teams: list[TeamData]) -> list[int]:
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
    ).returning(Team.id)
    return list(db.scalars(stmt))


def link_teams_to_season(db: Session, season_id: int, team_ids: list[int]) -> None:
    if not team_ids:
        return
    stmt = insert(SeasonTeam).values([{"season_id": season_id, "team_id": t} for t in team_ids])
    db.execute(stmt.on_conflict_do_nothing())


# --- Lecturas -----------------------------------------------------------------------------


def list_competitions(db: Session) -> list[Competition]:
    return list(db.scalars(select(Competition).order_by(Competition.country, Competition.name)))


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
