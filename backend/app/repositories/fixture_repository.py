"""Acceso a la BD para partidos (fixtures).

Igual que el catálogo, los upserts usan INSERT ... ON CONFLICT y una sola sentencia
por lote para no hacer una ida y vuelta a la BD por cada fila, y registran también
los IDs del proveedor en las tablas de mapeo (escritura doble).

El upsert de fixtures no es destructivo con los marcadores: ver _fixture_update_values.
"""

from datetime import datetime

from sqlalchemy import and_, case, func, null, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.models import Fixture, FixtureProviderMapping, Season, Team, TeamProviderMapping
from app.repositories.provider_mapping_repository import upsert_origin_mappings
from app.schemas.catalog import TeamData
from app.schemas.fixture import FINAL_STATUSES, FINISHED_STATUSES, FixtureData

# Campos de FixtureData que se copian tal cual a la tabla
_FIXTURE_COLUMNS = [
    f for f in FixtureData.model_fields if f not in {"competition_external_id", "season", "home_team", "away_team"}
]


# Pares de marcador protegidos frente a respuestas parciales del proveedor
_SCORE_PAIRS = [
    ("home_goals", "away_goals"),
    ("halftime_home", "halftime_away"),
    ("extratime_home", "extratime_away"),
    ("penalty_home", "penalty_away"),
]
_FULLTIME_PAIR = ("fulltime_home", "fulltime_away")


def _fixture_update_values(excluded, columns: list[str]) -> dict:
    """Valores del ON CONFLICT DO UPDATE de fixtures.

    Los marcadores se tratan siempre POR PARES (nunca medio par nuevo con medio par viejo):
    - Par entrante completo: sustituye al guardado (el proveedor puede corregirlo).
    - Par entrante NULL con estado entrante final (FT/AET/PEN/AWD/WO): se conserva el guardado,
      para que una respuesta parcial no destruya un marcador conocido.
    - Par entrante NULL con estado entrante no final (NS, PST, CANC, ABD...): se acepta el NULL;
      no se conservan marcadores de un partido que ya no está terminado.
    fulltime es más estricto: solo se conserva si el estado entrante sigue siendo FT/AET/PEN y
    en otro caso pasa a NULL (ck_fixtures_fulltime_finished).
    El resto de columnas (estado, horario, temporada, árbitro...) se sobrescriben tal cual.
    """
    table = Fixture.__table__
    values = {k: excluded[k] for k in columns}

    def pair_policy(pair: tuple[str, str], keep_statuses: set[str]) -> None:
        home, away = pair
        incoming_complete = and_(excluded[home].is_not(None), excluded[away].is_not(None))
        keep_stored = excluded.status_short.in_(keep_statuses)
        for col in pair:
            values[col] = case((incoming_complete, excluded[col]), (keep_stored, table.c[col]), else_=null())

    for pair in _SCORE_PAIRS:
        pair_policy(pair, FINAL_STATUSES)
    pair_policy(_FULLTIME_PAIR, FINISHED_STATUSES)
    return values


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


def upsert_fixtures(
    db: Session, season_id: int, fixtures: list[FixtureData], team_ids: dict[int, int], provider: str
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
    new_values = _fixture_update_values(stmt.excluded, [k for k in rows[0] if k != "external_id"])
    # Solo se reescribe la fila (y se mueve updated_at) si algún valor cambia de verdad
    changed = or_(*(Fixture.__table__.c[k].is_distinct_from(v) for k, v in new_values.items()))
    stmt = stmt.on_conflict_do_update(
        index_elements=[Fixture.external_id],
        set_={**new_values, "updated_at": func.now()},
        where=changed,
    )
    db.execute(stmt)
    # RETURNING no devolvería las filas sin cambios: los ids se leen aparte. El mapeo sí se
    # actualiza siempre (last_seen_at = el proveedor volvió a observar el partido)
    external_ids = [f.external_id for f in unique]
    ids = dict(db.execute(select(Fixture.external_id, Fixture.id).where(Fixture.external_id.in_(external_ids))).all())
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
