"""Acceso a la BD para partidos (fixtures).

Igual que el catálogo, los upserts usan INSERT ... ON CONFLICT y una sola sentencia
por lote para no hacer una ida y vuelta a la BD por cada fila, y registran también
los IDs del proveedor en las tablas de mapeo (escritura doble).
"""

from datetime import datetime

from sqlalchemy import and_, case, func, null, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.models import Fixture, FixtureProviderMapping, Season, Team, TeamProviderMapping
from app.repositories.provider_mapping_repository import upsert_origin_mappings
from app.schemas.catalog import TeamData
from app.schemas.fixture import FINISHED_STATUSES, FixtureData

# Campos de FixtureData que se copian tal cual a la tabla
_FIXTURE_COLUMNS = [
    f for f in FixtureData.model_fields if f not in {"competition_external_id", "season", "home_team", "away_team"}
]

# Marcadores (local, visitante) que una respuesta con NULL no puede borrar. Cada par se
# sustituye entero o no se toca: nunca se mezcla el local nuevo con el visitante viejo.
# El resto de columnas (estado, horario, árbitro, estadio...) sigue al proveedor tal cual
_SCORE_PAIRS = [
    ("home_goals", "away_goals"),
    ("halftime_home", "halftime_away"),
    ("fulltime_home", "fulltime_away"),
    ("penalty_home", "penalty_away"),
]


def ensure_teams(db: Session, teams: list[TeamData], provider: str) -> dict[int, int]:
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
    ids = {external_id: team_id for external_id, team_id in rows}
    # Equipos nuevos y existentes: los nuevos obtienen su mapeo y los demás actualizan last_seen_at
    upsert_origin_mappings(
        db, TeamProviderMapping, provider, [(ids[e], e, t.name) for e, t in unique.items()]
    )
    return ids


def _conflict_values(excluded, columns: list[str]) -> dict:
    """Valores del UPDATE cuando el partido ya existe.

    - Marcadores: un par entrante completo sustituye al guardado (correcciones del proveedor);
      un par con algún NULL conserva el guardado.
    - fulltime solo existe en partidos terminados (ck_fixtures_fulltime_finished): si el
      partido deja de estarlo (p. ej. FT -> AWD) se vacía en lugar de conservarse.
    """
    current = Fixture.__table__.c
    values = {k: excluded[k] for k in columns}
    for home, away in _SCORE_PAIRS:
        complete = and_(excluded[home].is_not(None), excluded[away].is_not(None))
        for column in (home, away):
            values[column] = case((complete, excluded[column]), else_=current[column])
    not_finished = excluded.status_short.not_in(sorted(FINISHED_STATUSES))
    for column in ("fulltime_home", "fulltime_away"):
        values[column] = case((not_finished, null()), else_=values[column])
    return values


def upsert_fixtures(
    db: Session, season_id: int, fixtures: list[FixtureData], team_ids: dict[int, int], provider: str
) -> int:
    """Guarda los partidos de una temporada en una sola sentencia. Devuelve cuántos se guardaron.

    Las filas que no cambian no se reescriben (ni se toca su updated_at).
    """
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
    new_values = _conflict_values(stmt.excluded, updatable)
    current = Fixture.__table__.c
    changed = tuple_(*(current[k] for k in updatable)).is_distinct_from(tuple_(*new_values.values()))
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[Fixture.external_id],
            set_={**new_values, "updated_at": func.now()},
            where=changed,
        )
    )
    # RETURNING no devuelve las filas sin cambios: los ids se leen aparte para todos los mapeos
    ids = dict(
        db.execute(
            select(Fixture.external_id, Fixture.id).where(Fixture.external_id.in_([f.external_id for f in unique]))
        ).all()
    )
    upsert_origin_mappings(
        db, FixtureProviderMapping, provider, [(fixture_id, e, None) for e, fixture_id in ids.items()]
    )
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
