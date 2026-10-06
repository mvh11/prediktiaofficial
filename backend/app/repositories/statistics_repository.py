"""Acceso a la BD de las estadísticas de partido (M5): observaciones raw y estado normalizado.

Nunca hace commit: el service controla las transacciones (una por lote). Solo execute/flush.

Observaciones (fixture_statistics_observations), por (fixture, provider):
- sin observación previa → se inserta como is_latest ("created");
- mismo hash que la vigente → no se inserta nada; solo avanza last_observed_at ("unchanged");
- hash distinto → la vigente pasa a is_latest = false y se inserta la nueva ("changed").
  El historial se conserva entero: es lo que permite reconstruir qué sabíamos y cuándo.

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

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.models import FixtureStatisticsObservation, FixtureTeamStatistics
from app.models.statistics import NUMERIC_SCALE
from app.schemas.statistics import STATISTIC_FIELDS, TeamStatisticValues

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
