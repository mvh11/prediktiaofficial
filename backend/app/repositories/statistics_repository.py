"""Acceso a la BD de las estadísticas de partido (M5): observaciones raw y estado normalizado.

Nunca hace commit: el service controla las transacciones (una por lote). Solo execute/flush.

Observaciones (fixture_statistics_observations), por (fixture, provider):
- sin observación previa → se inserta como is_latest ("created");
- mismo hash que la vigente → no se inserta nada; solo avanza last_observed_at ("unchanged");
- hash distinto → la vigente pasa a is_latest = false y se inserta la nueva ("changed").
  El historial se conserva entero: es lo que permite reconstruir qué sabíamos y cuándo.

Las funciones por partido (record_observation, upsert_team_statistics) son la referencia de la
semántica. El backfill usa las funciones POR LOTE del final del módulo (M5.4D): misma semántica,
decidida en Python por el service, con un número de sentencias que no depende de los partidos.

Estado normalizado (fixture_team_statistics), upsert por (fixture, team, provider):
- "created": no había fila;
- "updated": cambió algún valor normalizado o el lado (home/away), es decir, un cambio material;
- "unchanged": valores y lado iguales. Si la fila viene de una observación nueva (o de otra
  versión del normalizador) se re-enlaza (observation_id/normalizer_version) SIN contar como
  updated: el resultado lo indica aparte con relinked = True.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from sqlalchemy import BigInteger, Integer, Numeric, SmallInteger, Text, cast, column, func, insert, select, update, values
from sqlalchemy.orm import Session

from app.models import Fixture, FixtureProviderMapping, FixtureStatisticsObservation, FixtureTeamStatistics, TeamProviderMapping
from app.models.statistics import NUMERIC_SCALE
from app.schemas.statistics import DECIMAL_FIELDS, INTEGER_FIELDS, PERCENTAGE_FIELDS, STATISTIC_FIELDS, TeamStatisticValues

ObservationOutcome = Literal["created", "changed", "unchanged"]
RowOutcome = Literal["created", "updated", "unchanged"]


def canonical_json(payload: Any) -> str:
    """Serialización determinista: claves de objeto ordenadas, sin espacios, UTF-8 sin escapar.
    Los arrays conservan su orden (el orden de equipos y estadísticas es parte del dato)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def payload_hash(payload: Any) -> str:
    """SHA-256 (hex) del JSON canónico."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ObservationResult:
    observation_id: int
    outcome: ObservationOutcome
    previous_observation_id: int | None = None  # la vigente antes de un "changed"


@dataclass(frozen=True)
class RowResult:
    outcome: RowOutcome
    relinked: bool = False  # solo re-enlazada a otra observación / versión, sin cambio material


def get_latest_observation(db: Session, fixture_id: int, provider: str, *, for_update: bool = False):
    stmt = select(FixtureStatisticsObservation).where(
        FixtureStatisticsObservation.fixture_id == fixture_id,
        FixtureStatisticsObservation.provider == provider,
        FixtureStatisticsObservation.is_latest.is_(True),
    ).execution_options(populate_existing=True)  # nunca un objeto cacheado en la sesión
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def record_observation(
    db: Session,
    *,
    fixture_id: int,
    provider: str,
    provider_fixture_id: str,
    payload: Any,
    source: str,
    availability: str,
    teams_returned: int,
    observed_at: datetime,
    available_at: datetime,
    fixture_status_at_fetch: str | None = None,
    run_id: int | None = None,
) -> ObservationResult:
    """Registra lo que devolvió el proveedor para un partido (ver docstring del módulo).

    available_at lo decide el llamador (en backfill histórico, kickoff + 6 h; en vivo,
    observed_at); aquí solo se guarda. En "unchanged" se conservan observed_at y available_at
    de la versión original: es cuándo se conoció ESE contenido por primera vez.
    """
    digest = payload_hash(payload)
    latest = get_latest_observation(db, fixture_id, provider, for_update=True)
    if latest is not None and latest.payload_hash == digest:
        db.execute(
            update(FixtureStatisticsObservation)
            .where(FixtureStatisticsObservation.id == latest.id)
            .values(last_observed_at=func.greatest(FixtureStatisticsObservation.last_observed_at, observed_at))
        )
        return ObservationResult(latest.id, "unchanged")

    if latest is not None:
        if observed_at < latest.last_observed_at:
            raise ValueError(
                f"Observación del {observed_at.isoformat()} anterior a la vigente del partido {fixture_id} "
                f"({latest.last_observed_at.isoformat()}): no se puede versionar fuera de orden"
            )
        db.execute(
            update(FixtureStatisticsObservation)
            .where(FixtureStatisticsObservation.id == latest.id)
            .values(is_latest=False)
        )
    new_id = db.execute(
        insert(FixtureStatisticsObservation)
        .values(
            fixture_id=fixture_id,
            provider=provider,
            provider_fixture_id=provider_fixture_id,
            run_id=run_id,
            source=source,
            fixture_status_at_fetch=fixture_status_at_fetch,
            availability=availability,
            teams_returned=teams_returned,
            payload=payload,
            payload_hash=digest,
            observed_at=observed_at,
            last_observed_at=observed_at,
            available_at=available_at,
            is_latest=True,
        )
        .returning(FixtureStatisticsObservation.id)
    ).scalar_one()
    if latest is None:
        return ObservationResult(new_id, "created")
    return ObservationResult(new_id, "changed", previous_observation_id=latest.id)


def column_values(values: TeamStatisticValues) -> dict[str, Any]:
    """Valores tal como los guardará PostgreSQL (numéricos redondeados a la escala de su
    columna), para que comparar con lo guardado no dé falsos cambios."""
    out = values.model_dump()
    for field, scale in NUMERIC_SCALE.items():
        if out[field] is not None:
            out[field] = Decimal(out[field]).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    return out


def get_team_statistics(db: Session, fixture_id: int, team_id: int, provider: str, *, for_update: bool = False):
    stmt = select(FixtureTeamStatistics).where(
        FixtureTeamStatistics.fixture_id == fixture_id,
        FixtureTeamStatistics.team_id == team_id,
        FixtureTeamStatistics.provider == provider,
    ).execution_options(populate_existing=True)
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def upsert_team_statistics(
    db: Session,
    *,
    fixture_id: int,
    team_id: int,
    provider: str,
    side: str,
    observation_id: int,
    values: TeamStatisticValues,
    normalizer_version: int,
) -> RowResult:
    """Guarda el estado normalizado de un equipo en un partido (ver docstring del módulo).

    La identidad (que team_id sea el local o el visitante del partido, y side) la valida el
    service; la FK compuesta garantiza que observation_id es de este mismo partido y proveedor.
    """
    new = {**column_values(values), "side": side}
    current = get_team_statistics(db, fixture_id, team_id, provider, for_update=True)
    if current is None:
        db.execute(
            insert(FixtureTeamStatistics).values(
                fixture_id=fixture_id,
                team_id=team_id,
                provider=provider,
                observation_id=observation_id,
                normalizer_version=normalizer_version,
                **new,
            )
        )
        return RowResult("created")

    stored = {field: getattr(current, field) for field in (*STATISTIC_FIELDS, "side")}
    material = stored != new
    relink = current.observation_id != observation_id or current.normalizer_version != normalizer_version
    if not material and not relink:
        return RowResult("unchanged")

    changes: dict[str, Any] = {"observation_id": observation_id, "normalizer_version": normalizer_version}
    if material:
        changes.update(new, updated_at=func.now())
    db.execute(update(FixtureTeamStatistics).where(FixtureTeamStatistics.id == current.id).values(**changes))
    return RowResult("updated") if material else RowResult("unchanged", relinked=True)


# --- Por lote (M5.4D) ------------------------------------------------------------------------
# Precarga de un lote de partidos y escrituras en bloque. Ninguna decide semántica: el service
# calcula en Python qué es created/changed/unchanged y created/updated/relinked/unchanged con
# las mismas reglas que las funciones por partido de arriba; aquí solo se lee y se escribe.


def out_of_order(latest_last_observed_at: datetime, observed_at: datetime, fixture_id: int) -> ValueError:
    """El mismo error que record_observation para una versión nueva anterior a la vigente."""
    return ValueError(
        f"Observación del {observed_at.isoformat()} anterior a la vigente del partido {fixture_id} "
        f"({latest_last_observed_at.isoformat()}): no se puede versionar fuera de orden"
    )


def classify_team_row(existing, side: str, values: TeamStatisticValues, observation_id: int | None, normalizer_version: int) -> str:
    """created | updated | relinked | unchanged, con la regla de upsert_team_statistics.
    observation_id None = la fila apuntará a una observación nueva (siempre distinta)."""
    if existing is None:
        return "created"
    stored = {field: getattr(existing, field) for field in (*STATISTIC_FIELDS, "side")}
    if stored != {**column_values(values), "side": side}:
        return "updated"
    if observation_id is None or existing.observation_id != observation_id or existing.normalizer_version != normalizer_version:
        return "relinked"
    return "unchanged"


def prefetch_fixtures(db: Session, fixture_ids: list[int], provider: str) -> tuple[dict[int, Any], dict[int, set[str]]]:
    """Revalidación del lote en UNA consulta: {fixture_id: fila} y {fixture_id: ids externos
    activos (canónicos)}. Sin lock, como la revalidación por partido."""
    from app.repositories.provider_mapping_repository import canonical_external_id

    rows = db.execute(
        select(Fixture.id, Fixture.status_short, Fixture.kickoff_at, Fixture.home_team_id, Fixture.away_team_id, FixtureProviderMapping.external_id)
        .outerjoin(
            FixtureProviderMapping,
            (FixtureProviderMapping.fixture_id == Fixture.id)
            & (FixtureProviderMapping.provider == provider)
            & FixtureProviderMapping.is_active.is_(True),
        )
        .where(Fixture.id.in_(fixture_ids))
        .order_by(Fixture.id)
    ).all()
    fixtures: dict[int, Any] = {}
    active: dict[int, set[str]] = {}
    for row in rows:
        fixtures[row.id] = row
        ids = active.setdefault(row.id, set())
        if row.external_id is not None:
            ids.add(canonical_external_id(row.external_id))
    return fixtures, active


def prefetch_team_mappings(db: Session, external_ids: set[str], provider: str) -> dict[str, int]:
    """Mappings activos de los equipos del lote en UNA consulta: {id externo canónico: team_id}."""
    if not external_ids:
        return {}
    rows = db.execute(
        select(TeamProviderMapping.external_id, TeamProviderMapping.team_id).where(
            TeamProviderMapping.provider == provider,
            TeamProviderMapping.is_active.is_(True),
            TeamProviderMapping.external_id.in_(sorted(external_ids)),
        )
    ).all()
    return {external_id: team_id for external_id, team_id in rows}


def prefetch_latest_observations(db: Session, fixture_ids: list[int], provider: str, *, for_update: bool) -> dict[int, Any]:
    """Observación vigente de cada partido del lote en UNA consulta (FOR UPDATE en apply,
    siempre en orden de fixture_id para un orden de locks estable)."""
    stmt = (
        select(FixtureStatisticsObservation)
        .where(
            FixtureStatisticsObservation.provider == provider,
            FixtureStatisticsObservation.is_latest.is_(True),
            FixtureStatisticsObservation.fixture_id.in_(fixture_ids),
        )
        .order_by(FixtureStatisticsObservation.fixture_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return {obs.fixture_id: obs for obs in db.scalars(stmt)}


def prefetch_team_statistics(db: Session, fixture_ids: list[int], provider: str, *, for_update: bool) -> dict[tuple[int, int], Any]:
    """Filas normalizadas del lote en UNA consulta: {(fixture_id, team_id): fila}. Se pide
    después de las observaciones (orden de locks: observaciones → filas)."""
    stmt = (
        select(FixtureTeamStatistics)
        .where(FixtureTeamStatistics.provider == provider, FixtureTeamStatistics.fixture_id.in_(fixture_ids))
        .order_by(FixtureTeamStatistics.fixture_id, FixtureTeamStatistics.team_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return {(row.fixture_id, row.team_id): row for row in db.scalars(stmt)}


def write_observations(
    db: Session,
    *,
    unchanged_ids: list[int],
    superseded_ids: list[int],
    new_rows: list[dict[str, Any]],
    observed_at: datetime,
) -> dict[int, int]:
    """Escrituras de observaciones de un lote, como mucho 3 sentencias y en este orden:
    D1 last_observed_at de las que no cambian; D2 is_latest = false de las sustituidas (antes del
    INSERT por el índice único parcial); D3 INSERT multi-fila RETURNING. Devuelve
    {fixture_id: id de la observación nueva}."""
    if unchanged_ids:
        db.execute(
            update(FixtureStatisticsObservation)
            .where(FixtureStatisticsObservation.id.in_(unchanged_ids))
            .values(last_observed_at=func.greatest(FixtureStatisticsObservation.last_observed_at, observed_at))
            .execution_options(synchronize_session=False)
        )
    if superseded_ids:
        db.execute(
            update(FixtureStatisticsObservation)
            .where(FixtureStatisticsObservation.id.in_(superseded_ids))
            .values(is_latest=False)
            .execution_options(synchronize_session=False)
        )
    if not new_rows:
        return {}
    for row in new_rows:
        row["payload_hash"] = payload_hash(row["payload"])
        row["last_observed_at"] = row["observed_at"]
        row["is_latest"] = True
    returned = db.execute(
        insert(FixtureStatisticsObservation.__table__)
        .values(new_rows)
        .returning(FixtureStatisticsObservation.__table__.c.fixture_id, FixtureStatisticsObservation.__table__.c.id)
    ).all()
    return {fixture_id: obs_id for fixture_id, obs_id in returned}


def _typed(name: str):
    if name in INTEGER_FIELDS:
        return Integer
    if name in PERCENTAGE_FIELDS:
        return Numeric(5, 2)
    if name in DECIMAL_FIELDS:
        return Numeric(6, 3)
    raise AssertionError(name)  # pragma: no cover


def write_team_statistics(
    db: Session,
    *,
    created: list[dict[str, Any]],
    updated: list[dict[str, Any]],
    relinked: list[dict[str, Any]],
) -> None:
    """Escrituras de filas normalizadas de un lote, como mucho 3 sentencias:
    F1 INSERT multi-fila de las nuevas; F2 UPDATE de las que cambian de valores o de lado (con
    updated_at); F3 UPDATE de las que solo se re-enlazan (observation_id/normalizer_version, SIN
    tocar updated_at). Los valores llegan ya fusionados y redondeados (column_values)."""
    table = FixtureTeamStatistics.__table__
    if created:
        db.execute(insert(table).values(created))
    if updated:
        cols = ["id", "observation_id", "normalizer_version", "side", *STATISTIC_FIELDS]
        v = values(
            column("id", BigInteger), column("observation_id", BigInteger), column("normalizer_version", SmallInteger),
            column("side", Text), *(column(name, _typed(name)) for name in STATISTIC_FIELDS), name="v",
        ).data([tuple(row[c] for c in cols) for row in updated])
        db.execute(
            update(table)
            .where(table.c.id == v.c.id)
            .values(
                observation_id=v.c.observation_id,
                normalizer_version=v.c.normalizer_version,
                side=v.c.side,
                updated_at=func.now(),
                **{name: cast(v.c[name], _typed(name)) for name in STATISTIC_FIELDS},
            )
        )
    if relinked:
        v = values(column("id", BigInteger), column("observation_id", BigInteger), column("normalizer_version", SmallInteger), name="v").data(
            [(row["id"], row["observation_id"], row["normalizer_version"]) for row in relinked]
        )
        db.execute(
            update(table).where(table.c.id == v.c.id).values(observation_id=v.c.observation_id, normalizer_version=v.c.normalizer_version)
        )
