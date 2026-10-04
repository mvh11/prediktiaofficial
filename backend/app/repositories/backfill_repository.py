"""Acceso a la BD para el backfill histórico: registro de ejecuciones y lecturas de estado.

Las lecturas sirven para comparar lo recibido con lo guardado ANTES de escribir (contadores,
protección cross-season, equipos y mapeos que faltarían) sin tocar los datos de dominio.
"""

from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.models import Fixture, FixtureProviderMapping, SeasonBackfillRun, SeasonTeam, Team

# Columnas de fixtures que el backfill compara para decidir si un partido cambiaría
FIXTURE_STATE_COLUMNS = [
    "id",
    "external_id",
    "season_id",
    "round",
    "kickoff_at",
    "status_short",
    "status_long",
    "elapsed",
    "venue_name",
    "venue_city",
    "referee",
    "home_team_id",
    "away_team_id",
    "home_goals",
    "away_goals",
    "halftime_home",
    "halftime_away",
    "extratime_home",
    "extratime_away",
    "penalty_home",
    "penalty_away",
    "fulltime_home",
    "fulltime_away",
]


# --- Registro de ejecuciones ---------------------------------------------------------------


def create_run(
    db: Session,
    *,
    competition_id: int,
    requested_year: int,
    provider: str,
    is_dry_run: bool,
    is_refresh: bool,
    season_id: int | None,
) -> int:
    stmt = (
        insert(SeasonBackfillRun)
        .values(
            competition_id=competition_id,
            requested_year=requested_year,
            provider=provider,
            status="running",
            is_dry_run=is_dry_run,
            is_refresh=is_refresh,
            season_id=season_id,
        )
        .returning(SeasonBackfillRun.id)
    )
    return db.execute(stmt).scalar_one()


def finish_run(db: Session, run_id: int, *, status: str, **values: Any) -> None:
    db.execute(
        update(SeasonBackfillRun)
        .where(SeasonBackfillRun.id == run_id)
        .values(status=status, finished_at=func.now(), **values)
    )


def has_completed_run(db: Session, competition_id: int, requested_year: int) -> bool:
    stmt = select(SeasonBackfillRun.id).where(
        SeasonBackfillRun.competition_id == competition_id,
        SeasonBackfillRun.requested_year == requested_year,
        SeasonBackfillRun.status == "completed",
    )
    return db.execute(stmt.limit(1)).first() is not None


def get_run(db: Session, run_id: int) -> SeasonBackfillRun | None:
    return db.get(SeasonBackfillRun, run_id)


# --- Lecturas del estado previo -------------------------------------------------------------


def existing_fixtures(db: Session, external_ids: list[int]) -> dict[int, dict]:
    """{external_id: estado actual} de los partidos que ya existen."""
    if not external_ids:
        return {}
    columns = [Fixture.__table__.c[c] for c in FIXTURE_STATE_COLUMNS]
    rows = db.execute(select(*columns).where(Fixture.external_id.in_(external_ids))).mappings()
    return {row["external_id"]: dict(row) for row in rows}


def known_teams(db: Session, team_external_ids: list[int]) -> dict[int, int]:
    if not team_external_ids:
        return {}
    rows = db.execute(select(Team.external_id, Team.id).where(Team.external_id.in_(team_external_ids)))
    return {external_id: team_id for external_id, team_id in rows}


def season_team_ids(db: Session, season_id: int) -> set[int]:
    return set(db.scalars(select(SeasonTeam.team_id).where(SeasonTeam.season_id == season_id)))


def mapped_fixture_external_ids(db: Session, provider: str, external_ids: list[int]) -> set[int]:
    """external_id (de los dados) que tienen mapeo del proveedor apuntando a un fixture con ese external_id."""
    if not external_ids:
        return set()
    stmt = (
        select(Fixture.external_id)
        .join(FixtureProviderMapping, FixtureProviderMapping.fixture_id == Fixture.id)
        .where(
            FixtureProviderMapping.provider == provider,
            FixtureProviderMapping.external_id == func.cast(Fixture.external_id, FixtureProviderMapping.external_id.type),
            Fixture.external_id.in_(external_ids),
        )
    )
    return set(db.scalars(stmt))


def count_fixtures(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Fixture))


def duplicate_mapping_count(db: Session) -> int:
    dupes = (
        select(FixtureProviderMapping.provider, FixtureProviderMapping.external_id)
        .group_by(FixtureProviderMapping.provider, FixtureProviderMapping.external_id)
        .having(func.count() > 1)
        .subquery()
    )
    return db.scalar(select(func.count()).select_from(dupes))


def orphan_mapping_count(db: Session) -> int:
    stmt = (
        select(func.count())
        .select_from(FixtureProviderMapping)
        .outerjoin(Fixture, Fixture.id == FixtureProviderMapping.fixture_id)
        .where(Fixture.id.is_(None))
    )
    return db.scalar(stmt)


def lock_running_run(db: Session, competition_id: int, requested_year: int) -> SeasonBackfillRun | None:
    """Run en curso del par, bloqueado con SELECT ... FOR UPDATE hasta el fin de la transacción.

    Si otro proceso administrativo lo está modificando a la vez, este espera; al obtener el
    bloqueo PostgreSQL vuelve a evaluar status = 'running', así que el segundo ya no lo encuentra.
    """
    stmt = (
        select(SeasonBackfillRun)
        .where(
            SeasonBackfillRun.competition_id == competition_id,
            SeasonBackfillRun.requested_year == requested_year,
            SeasonBackfillRun.status == "running",
        )
        .with_for_update()
    )
    return db.scalars(stmt).first()


def is_older_than(db: Session, run_id: int, minutes: int) -> bool:
    """started_at < now() - minutos, con el reloj de la BD (el mismo que fijó started_at)."""
    stmt = select(SeasonBackfillRun.started_at < func.now() - func.make_interval(0, 0, 0, 0, 0, minutes)).where(
        SeasonBackfillRun.id == run_id
    )
    return bool(db.scalar(stmt))
