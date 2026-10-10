"""Modo RETROSPECTIVE_FINAL_RESULTS (DI-A6, Checkpoint B): resultados finales del estado ACTUAL.

Separado a propósito de fixture_knowledge_repository (STRICT_KNOWLEDGE): otro módulo, otro tipo
(RetrospectiveFinalResult) y sin corte T. Lee fixtures, el estado operativo actual, con la fusión
de pares; por eso nunca es conocimiento a fecha T y ningún camino estricto debe recurrir a él.
"""

from collections.abc import Iterable

from sqlalchemy import Integer, any_, bindparam, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from app.models import Fixture
from app.schemas.fixture import FINAL_STATUSES
from app.schemas.fixture_knowledge import RetrospectiveFinalResult

_RESULT_COLUMNS = (
    "status_short", "home_goals", "away_goals", "halftime_home", "halftime_away", "fulltime_home", "fulltime_away",
    "extratime_home", "extratime_away", "penalty_home", "penalty_away",
)


def retrospective_final_results(db: Session, fixture_ids: Iterable[int]) -> dict[int, RetrospectiveFinalResult]:
    """{fixtures.id: resultado} de los partidos pedidos que HOY están en un estado final
    (FINAL_STATUSES). Los que no lo están no aparecen. No admite corte: no es una lectura temporal."""
    ids = sorted(set(fixture_ids))
    if not ids:
        return {}
    t = Fixture.__table__.c
    rows = db.execute(
        select(t.id, *(t[c] for c in _RESULT_COLUMNS))
        .where(t.id == any_(bindparam("ids", ids, type_=ARRAY(Integer))), t.status_short.in_(sorted(FINAL_STATUSES)))
        .order_by(t.id)
    ).mappings()
    return {row["id"]: RetrospectiveFinalResult(fixture_id=row["id"], **{c: row[c] for c in _RESULT_COLUMNS}) for row in rows}
