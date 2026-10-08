"""Lecturas de la capa as-of de estadísticas (M5.7A). Solo SELECT: nunca escribe ni bloquea filas.

Contrato en app/schemas/statistics_knowledge.py. La elección temporal se hace en SQL (índice
ix_fso_fixture_provider_available); la reconstrucción y la calidad, en
app/services/statistics_as_of.py.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, any_, bindparam, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from app.models import Fixture, FixtureStatisticsObservation, StatisticsRun

_O = FixtureStatisticsObservation.__table__.c


def eligible_observations(db: Session, fixture_ids: Iterable[int], provider: str, cutoff: datetime) -> dict[int, Any]:
    """Por partido: la versión vigente en T entre las disponibles en T (available_at <= T), por
    (observed_at, id) descendente, más cuántas versiones eran elegibles. UNA consulta."""
    ids = sorted(set(fixture_ids))
    if not ids:
        return {}
    ranked = (
        select(
            *_O,
            func.count().over(partition_by=_O.fixture_id).label("eligible_versions"),
            func.row_number().over(partition_by=_O.fixture_id, order_by=(_O.observed_at.desc(), _O.id.desc())).label("rank"),
        )
        .where(
            _O.fixture_id == any_(bindparam("ids", ids, type_=ARRAY(Integer))),
            _O.provider == provider,
            _O.available_at <= cutoff,
        )
        .subquery()
    )
    rows = db.execute(select(ranked).where(ranked.c.rank == 1).order_by(ranked.c.fixture_id)).mappings()
    return {row["fixture_id"]: row for row in rows}


def fixture_teams(db: Session, fixture_ids: Iterable[int]) -> dict[int, tuple[int, int]]:
    """{fixture_id: (home_team_id, away_team_id)}: la identidad del partido (no temporal)."""
    ids = sorted(set(fixture_ids))
    if not ids:
        return {}
    rows = db.execute(select(Fixture.id, Fixture.home_team_id, Fixture.away_team_id).where(Fixture.id.in_(ids)))
    return {fixture_id: (home, away) for fixture_id, home, away in rows}


def run_checks(db: Session, run_ids: Iterable[int]) -> dict[int, list[Any]]:
    """{run_id: checks} de los runs que crearon las observaciones (los borrados no aparecen)."""
    ids = sorted(set(run_ids))
    if not ids:
        return {}
    return {run_id: checks or [] for run_id, checks in db.execute(select(StatisticsRun.id, StatisticsRun.checks).where(StatisticsRun.id.in_(ids)))}
