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

from app.models import Fixture, FixtureStatisticsObservation, StatisticsRun, TeamProviderMapping

_O = FixtureStatisticsObservation.__table__.c


def eligible_observations(db: Session, fixture_ids: Iterable[int], provider: str, cutoff: datetime, horizon: datetime) -> dict[int, Any]:
    """Por partido: la versión vigente entre las elegibles (available_at <= T y observed_at <= H),
    por (observed_at, id) descendente, más cuántas versiones eran elegibles. UNA consulta."""
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
            _O.observed_at <= horizon,
        )
        .subquery()
    )
    rows = db.execute(select(ranked).where(ranked.c.rank == 1).order_by(ranked.c.fixture_id)).mappings()
    return {row["fixture_id"]: row for row in rows}


def enumerate_team_fixtures(db: Session, team_id: int) -> list[int]:
    """Partidos en los que el estado ACTUAL de fixtures pone al equipo. Solo enumera candidatos
    (DI-A6C): ningún hecho del partido sale de aquí; se verifican con la evidencia."""
    return list(db.scalars(
        select(Fixture.id).where((Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id)).order_by(Fixture.id)
    ))


def team_mappings(db: Session, external_ids: Iterable[str], provider: str) -> dict[str, tuple[int, datetime]]:
    """Mappings ACTIVOS de equipos: {id externo canónico: (team_id, created_at)}."""
    ids = sorted(set(external_ids))
    if not ids:
        return {}
    rows = db.execute(
        select(TeamProviderMapping.external_id, TeamProviderMapping.team_id, TeamProviderMapping.created_at).where(
            TeamProviderMapping.provider == provider,
            TeamProviderMapping.is_active.is_(True),
            TeamProviderMapping.external_id.in_(ids),
        )
    )
    return {external_id: (team_id, created_at) for external_id, team_id, created_at in rows}


def run_checks(db: Session, run_ids: Iterable[int]) -> dict[int, list[Any]]:
    """{run_id: checks} de los runs que crearon las observaciones (los borrados no aparecen)."""
    ids = sorted(set(run_ids))
    if not ids:
        return {}
    return {run_id: checks or [] for run_id, checks in db.execute(select(StatisticsRun.id, StatisticsRun.checks).where(StatisticsRun.id.in_(ids)))}
