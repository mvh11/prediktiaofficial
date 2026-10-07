"""Lectura STRICT_KNOWLEDGE de fixtures en un corte T (DI-A6, Checkpoint B). Tipos y contrato en
app/schemas/fixture_knowledge.py y docs/data-integrity-status.md, "DI-A6C".

Solo lee fixture_observations: ni fixtures (estado operativo actual), ni recorded_at, ni la
evidencia de estadísticas (otro dominio). Una consulta para todos los partidos, sobre el índice
(fixture_id, observed_at): t* por partido y las filas que hay justo en t*.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Integer, and_, any_, bindparam, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from app.models import FixtureObservation
from app.schemas.fixture_knowledge import (
    FixtureKnowledge,
    KnowledgeStatus,
    ObservedFixtureState,
    StrictKnowledgeReport,
    require_utc,
)

_O = FixtureObservation.__table__.c


def _observed_state(row) -> ObservedFixtureState:
    values = dict(row)
    values["observation_id"] = values.pop("id")
    values["state_hash"] = bytes(values["state_hash"])
    return ObservedFixtureState(**values)


def strict_knowledge(db: Session, fixture_ids: Iterable[int], cutoff: datetime) -> StrictKnowledgeReport:
    """STRICT_KNOWLEDGE de cada partido pedido en el corte T (incluido: observed_at <= T).

    Los partidos se identifican por fixtures.id; enumerarlos (p. ej. desde fixtures) es cosa del
    llamador y no aporta estado. Todos los pedidos salen en el informe, también los UNKNOWN_AT_T.
    """
    require_utc(cutoff)
    ids = sorted(set(fixture_ids))
    if not ids:
        return StrictKnowledgeReport(cutoff)
    t_star = (
        select(_O.fixture_id, func.max(_O.observed_at).label("t_star"))
        .where(_O.fixture_id == any_(bindparam("ids", ids, type_=ARRAY(Integer))), _O.observed_at <= cutoff)
        .group_by(_O.fixture_id)
        .subquery()
    )
    # El orden por evidence_id solo hace estable la salida; no decide nada (en t* o hay un solo
    # estado o hay ambigüedad). Ni recorded_at ni el id de la fila intervienen
    rows = db.execute(
        select(*_O)
        .join(t_star, and_(_O.fixture_id == t_star.c.fixture_id, _O.observed_at == t_star.c.t_star))
        .order_by(_O.fixture_id, _O.evidence_id)
    ).mappings()
    at_t_star: dict[int, list[ObservedFixtureState]] = defaultdict(list)
    for row in rows:
        at_t_star[row["fixture_id"]].append(_observed_state(row))

    results = {}
    for fixture_id in ids:
        observations = tuple(at_t_star.get(fixture_id, ()))
        if not observations:
            results[fixture_id] = FixtureKnowledge(fixture_id, cutoff, KnowledgeStatus.UNKNOWN_AT_T)
            continue
        distinct = len({o.state_hash for o in observations})
        status = KnowledgeStatus.KNOWN if distinct == 1 else KnowledgeStatus.TEMPORAL_AMBIGUITY
        results[fixture_id] = FixtureKnowledge(fixture_id, cutoff, status, observations[0].observed_at, observations)
    return StrictKnowledgeReport(cutoff, results)


def strict_knowledge_at(db: Session, fixture_id: int, cutoff: datetime) -> FixtureKnowledge:
    """STRICT_KNOWLEDGE de un solo partido en el corte T."""
    return strict_knowledge(db, [fixture_id], cutoff).results[fixture_id]
