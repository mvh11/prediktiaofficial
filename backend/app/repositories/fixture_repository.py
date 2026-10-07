"""Acceso a la BD para partidos (fixtures).

Igual que el catálogo, los upserts usan INSERT ... ON CONFLICT y una sola sentencia
por lote para no hacer una ida y vuelta a la BD por cada fila, y registran también
los IDs del proveedor en las tablas de mapeo (escritura doble).

El upsert de fixtures no es destructivo con los marcadores: ver _fixture_update_values.

Evidencia temporal (DI-A6, contrato en docs/data-integrity-status.md, "DI-A6C"): cada partido de
una respuesta del proveedor se guarda además en fixture_observations TAL COMO SE OBSERVÓ, en la
misma transacción. fixtures solo se actualiza si la evidencia gana la clave de orden
(observed_at, state_hash) a la que fijó su estado: la evidencia más antigua entra en la historia
pero nunca pisa un estado actual más nuevo, sea cual sea el orden de commit (READ COMMITTED: el
WHERE del ON CONFLICT se vuelve a evaluar sobre la última versión confirmada de la fila).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, and_, case, cast, func, literal, null, or_, select, tuple_, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.models import Fixture, FixtureObservation, FixtureProviderMapping, Season, Team, TeamProviderMapping
from app.repositories.provider_mapping_repository import upsert_origin_mappings
from app.schemas.catalog import TeamData
from app.schemas.fixture import FINISHED_STATUSES, FixtureData
from app.schemas.fixture_evidence import PROVIDER_SOURCES, FixtureEvidence

# Campos de FixtureData que se copian tal cual a la tabla
_FIXTURE_COLUMNS = [
    f for f in FixtureData.model_fields if f not in {"competition_external_id", "season", "home_team", "away_team"}
]

# Pares de marcador (local, visitante). Cada par se sustituye entero o no se toca: nunca se
# mezcla el local nuevo con el visitante viejo
_GOALS_PAIR = ("home_goals", "away_goals")
_HALFTIME_PAIR = ("halftime_home", "halftime_away")
_EXTRATIME_PAIR = ("extratime_home", "extratime_away")
_PENALTY_PAIR = ("penalty_home", "penalty_away")
_SCORE_PAIRS = [_GOALS_PAIR, _HALFTIME_PAIR, _EXTRATIME_PAIR, _PENALTY_PAIR]
_FULLTIME_PAIR = ("fulltime_home", "fulltime_away")
# Estado temporal de una observación, en el orden fijo de fixture_state_hash_v1 (migración 0008)
STATE_COLUMNS = (
    "kickoff_at", "status_short", "season_id", "home_team_id", "away_team_id",
    "home_goals", "away_goals", "halftime_home", "halftime_away", "fulltime_home", "fulltime_away",
    "extratime_home", "extratime_away", "penalty_home", "penalty_away",
)
_STATE_TYPES = {"kickoff_at": DateTime(timezone=True), "status_short": Text()}
# Estados en los que el partido no se está jugando ni se ha jugado (o se anuló, o aún no tiene
# fecha): un NULL entrante en ellos limpia los marcadores guardados. En el resto (terminado,
# adjudicado, en vivo, suspendido, interrumpido...) un NULL entrante es una respuesta parcial
# y se conserva lo guardado
SCORE_CLEARING_STATUSES = frozenset({"NS", "PST", "CANC", "ABD", "TBD"})
# La prórroga no existe en un partido que terminó en FT (corrección AET -> FT)
EXTRATIME_CLEARING_STATUSES = SCORE_CLEARING_STATUSES | {"FT"}
# La tanda de penaltis solo existe en PEN (terminado) y P (tanda en juego)
PENALTY_STATUSES = frozenset({"PEN", "P"})

# Única definición de la política para un par entrante incompleto: (par, estados, regla).
# regla "keep_if_in": se conserva el guardado si el estado entrante está en `estados`;
# regla "keep_unless_in": se conserva salvo que esté. Si no se conserva, el par pasa a NULL.
# fulltime es más estricto: solo existe en FT/AET/PEN (ck_fixtures_fulltime_finished)
_NULL_PAIR_RULES = [
    (_GOALS_PAIR, SCORE_CLEARING_STATUSES, "keep_unless_in"),
    (_HALFTIME_PAIR, SCORE_CLEARING_STATUSES, "keep_unless_in"),
    (_EXTRATIME_PAIR, EXTRATIME_CLEARING_STATUSES, "keep_unless_in"),
    (_PENALTY_PAIR, PENALTY_STATUSES, "keep_if_in"),
    (_FULLTIME_PAIR, FINISHED_STATUSES, "keep_if_in"),
]


def predict_score_values(stored: dict, incoming: dict) -> dict:
    """Marcadores que deja el upsert en una fila existente: versión Python de _fixture_update_values.

    Las dos salen de _NULL_PAIR_RULES, así que no pueden divergir; además un test de BD lo
    comprueba. La usa el backfill para predecir el resultado sin escribir (check PARITY).
    """
    status = incoming.get("status_short")
    result = {}
    for pair, statuses, rule in _NULL_PAIR_RULES:
        if all(incoming.get(col) is not None for col in pair):
            source = incoming  # par completo: sustituye al guardado
        elif (status in statuses) == (rule == "keep_if_in"):
            source = stored
        else:
            source = {}
        for col in pair:
            result[col] = source.get(col)
    return result


def _fixture_update_values(excluded, columns: list[str]) -> dict:
    """Valores del ON CONFLICT DO UPDATE de fixtures.

    Los marcadores se tratan siempre POR PARES (nunca medio par nuevo con medio par viejo):
    - Par entrante completo: sustituye al guardado (el proveedor puede corregirlo).
    - Par entrante con algún NULL y estado entrante en SCORE_CLEARING_STATUSES (NS, PST, CANC,
      ABD, TBD): el par pasa a NULL; no se conservan marcadores de un partido que no se ha jugado.
    - Par entrante con algún NULL en cualquier otro estado (terminado, adjudicado, en vivo,
      suspendido, interrumpido...): se conserva el guardado, para que una respuesta parcial no
      destruya un marcador conocido.
    - Con un NULL entrante, además: extratime se limpia si el estado es FT; penalty solo se
      conserva en PEN/P; fulltime solo en FT/AET/PEN.
    Un par entrante COMPLETO se guarda siempre tal cual, aunque no encaje con el estado
    (incoherencia del proveedor que detectan los controles de calidad).
    El resto de columnas (estado, horario, temporada, árbitro...) se sobrescriben tal cual.
    """
    table = Fixture.__table__
    values = {k: excluded[k] for k in columns}
    for pair, statuses, rule in _NULL_PAIR_RULES:
        incoming_complete = and_(*(excluded[col].is_not(None) for col in pair))
        in_statuses = excluded.status_short.in_(sorted(statuses))
        keep_stored = in_statuses if rule == "keep_if_in" else ~in_statuses
        for col in pair:
            values[col] = case((incoming_complete, excluded[col]), (keep_stored, table.c[col]), else_=null())
    return values


@dataclass(frozen=True)
class UpsertCounts:
    """Resultado de upsert_fixtures: received = created + updated + unchanged (sin duplicados).

    created: partidos nuevos. updated: existentes cuyos DATOS cambiaron. unchanged: el resto
    (confirmaciones, evidencia más antigua que el estado actual y repeticiones de una respuesta).
    Son informativos: la corrección del estado no depende de ellos.
    """

    received: int
    created: int
    updated: int
    unchanged: int


class EvidenceIdentityConflict(Exception):
    """El mismo (fixture_id, evidence_id) ya existe con otro contenido: reutilización incoherente.

    Es un error de programación, no un fallo aislable de la competición: se propaga.
    """


def _state_type(column: str):
    return _STATE_TYPES.get(column, Integer())


def state_hashes(db: Session, states: list[dict]) -> list[bytes]:
    """fixture_state_hash_v1 de cada estado, en el mismo orden. El hash solo se define en SQL."""
    selects = [
        select(
            literal(i, Integer).label("i"),
            func.fixture_state_hash_v1(*(cast(literal(state[c], _state_type(c)), _state_type(c)) for c in STATE_COLUMNS)).label("h"),
        )
        for i, state in enumerate(states)
    ]
    if not selects:
        return []
    stmt = selects[0] if len(selects) == 1 else union_all(*selects)
    by_index = dict(db.execute(stmt).all())
    return [bytes(by_index[i]) for i in range(len(states))]


def count_existing_teams(db: Session, external_ids: list[int]) -> int:
    """Cuántos de esos external_id de equipo ya existen (para contar los que creará ensure_teams)."""
    if not external_ids:
        return 0
    return db.scalar(select(func.count()).select_from(Team).where(Team.external_id.in_(external_ids)))


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
    db: Session,
    season_id: int,
    fixtures: list[FixtureData],
    team_ids: dict[int, int],
    provider: str,
    evidence: FixtureEvidence,
) -> UpsertCounts:
    """Guarda los partidos de UNA respuesta del proveedor (evidence) y su evidencia temporal.

    En la transacción del llamante, sin commit:
    1. fixtures: INSERT ... ON CONFLICT DO UPDATE solo si (observed_at, state_hash) de la evidencia
       supera a (last_observed_at, last_state_hash) de la fila. Con la fusión de pares de siempre
       y updated_at solo si algún dato cambia; una confirmación más nueva solo adelanta el orden.
    2. fixture_observations: una fila por partido con lo OBSERVADO (sin fusión), gane o no.
       Repetir la misma respuesta (mismo evidence_id) no crea filas; si el (fixture_id, evidence_id)
       ya existe con otro contenido se lanza EvidenceIdentityConflict.
    3. Mapeos del proveedor (last_seen_at), como antes.

    Contadores exactos sin concurrencia: se leen antes los datos de las filas existentes y RETURNING
    devuelve las filas en las que ganó la evidencia; updated = las existentes cuyos datos cambian.
    """
    if evidence.source not in PROVIDER_SOURCES or evidence.provider != provider:
        raise ValueError(f"evidencia {evidence.source}/{evidence.provider} no válida para el proveedor {provider!r}")
    # Orden estable por external_id: dos escritores concurrentes bloquean las filas en el mismo orden
    unique = sorted({f.external_id: f for f in fixtures}.values(), key=lambda f: f.external_id)
    if not unique:
        return UpsertCounts(received=0, created=0, updated=0, unchanged=0)
    external_ids = [f.external_id for f in unique]
    rows = [
        {
            **f.model_dump(include=set(_FIXTURE_COLUMNS)),
            "season_id": season_id,
            "home_team_id": team_ids[f.home_team.external_id],
            "away_team_id": team_ids[f.away_team.external_id],
        }
        for f in unique
    ]
    hashes = state_hashes(db, rows)
    table = Fixture.__table__
    data_columns = [k for k in rows[0] if k != "external_id"]
    before = {
        r[0]: tuple(r[1:])
        for r in db.execute(
            select(table.c.external_id, *(table.c[c] for c in data_columns)).where(table.c.external_id.in_(external_ids))
        )
    }

    stmt = insert(Fixture).values(
        [{**row, "last_observed_at": evidence.observed_at, "last_state_hash": h} for row, h in zip(rows, hashes)]
    )
    excluded = stmt.excluded
    new_values = _fixture_update_values(excluded, data_columns)
    changed = or_(*(table.c[k].is_distinct_from(v) for k, v in new_values.items()))
    newer = tuple_(table.c.last_observed_at, table.c.last_state_hash) < tuple_(
        excluded.last_observed_at, excluded.last_state_hash
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Fixture.external_id],
        set_={
            **new_values,
            "last_observed_at": excluded.last_observed_at,
            "last_state_hash": excluded.last_state_hash,
            # Solo se mueve si algún dato cambia: una confirmación no es una actualización de datos
            "updated_at": case((changed, func.now()), else_=table.c.updated_at),
        },
        where=newer,
    ).returning(table.c.external_id, *(table.c[c] for c in data_columns))
    written = {r[0]: tuple(r[1:]) for r in db.execute(stmt)}

    ids = dict(db.execute(select(Fixture.external_id, Fixture.id).where(Fixture.external_id.in_(external_ids))).all())
    observations = [
        {
            "evidence_id": evidence.evidence_id,
            "fixture_id": ids[row["external_id"]],
            "observed_at": evidence.observed_at,
            "source": evidence.source,
            "provider": evidence.provider,
            "state_hash": h,
            **{c: row[c] for c in STATE_COLUMNS},
        }
        for row, h in zip(rows, hashes)
    ]
    inserted = set(
        db.scalars(
            insert(FixtureObservation)
            .values(observations)
            .on_conflict_do_nothing(index_elements=["fixture_id", "evidence_id"])
            .returning(FixtureObservation.fixture_id)
        )
    )
    replayed = [o for o in observations if o["fixture_id"] not in inserted]
    if replayed:
        _check_replay(db, evidence, replayed)

    # El mapeo se actualiza siempre (last_seen_at = el proveedor volvió a observar el partido)
    upsert_origin_mappings(
        db, FixtureProviderMapping, provider, [(fixture_id, e, None) for e, fixture_id in ids.items()]
    )
    created = sum(1 for e in written if e not in before)
    updated = sum(1 for e, values in written.items() if e in before and values != before[e])
    return UpsertCounts(received=len(unique), created=created, updated=updated, unchanged=len(unique) - created - updated)


def _check_replay(db: Session, evidence: FixtureEvidence, replayed: list[dict]) -> None:
    """Las observaciones que ya existían con este evidence_id tienen que ser exactamente la misma evidencia."""
    o = FixtureObservation
    stored = {
        r.fixture_id: r
        for r in db.execute(
            select(o.fixture_id, o.observed_at, o.source, o.provider, o.state_hash).where(
                o.evidence_id == evidence.evidence_id, o.fixture_id.in_([x["fixture_id"] for x in replayed])
            )
        )
    }
    expected = {x["fixture_id"]: (x["observed_at"], x["source"], x["provider"], x["state_hash"]) for x in replayed}
    conflicts = sorted(
        fixture_id
        for fixture_id, values in expected.items()
        if fixture_id not in stored
        or (stored[fixture_id].observed_at, stored[fixture_id].source, stored[fixture_id].provider, bytes(stored[fixture_id].state_hash))
        != values
    )
    if conflicts:
        raise EvidenceIdentityConflict(
            f"evidence_id {evidence.evidence_id} reutilizado con otro contenido en {len(conflicts)} partido(s): {conflicts[:5]}"
        )


# --- Lecturas -----------------------------------------------------------------------------


def ordering_keys(db: Session, external_ids: list[int]) -> dict[int, tuple[datetime, bytes]]:
    """{external_id: (last_observed_at, last_state_hash)} de los partidos que ya existen."""
    if not external_ids:
        return {}
    rows = db.execute(
        select(Fixture.external_id, Fixture.last_observed_at, Fixture.last_state_hash).where(Fixture.external_id.in_(external_ids))
    )
    return {e: (observed_at, bytes(h)) for e, observed_at, h in rows}


def evidence_wins(evidence: FixtureEvidence, state_hash: bytes, stored_key: tuple[datetime, bytes] | None) -> bool:
    """La misma regla que el WHERE de upsert_fixtures: la evidencia actualiza fixtures si su clave es mayor."""
    return stored_key is None or (evidence.observed_at, state_hash) > stored_key


def observed_external_ids(db: Session, evidence_id) -> list[int]:
    """external_id de cada observación de esa evidencia (una por partido; repetidos si hubiera más)."""
    return list(
        db.scalars(
            select(Fixture.external_id)
            .join(FixtureObservation, FixtureObservation.fixture_id == Fixture.id)
            .where(FixtureObservation.evidence_id == evidence_id)
        )
    )


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
