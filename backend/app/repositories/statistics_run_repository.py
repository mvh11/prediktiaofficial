"""Ciclo de vida de statistics_runs (M5.3). Sin commits: los controla el service/job.

El run 'running' es el lock (índice único parcial uq_statistics_runs_one_running): crear un
segundo run mientras otro está en curso lanza IntegrityError (is_lock_conflict lo reconoce).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models import LiveSyncRun, StatisticsRun

RUNNING_UNIQUE_INDEX = "uq_statistics_runs_one_running"


def create_run(
    db: Session, *, trigger: str, mode: str, scope: str, competition_id: int | None = None, season_id: int | None = None
) -> int:
    stmt = (
        insert(StatisticsRun)
        .values(trigger=trigger, mode=mode, scope=scope, competition_id=competition_id, season_id=season_id, status="running")
        .returning(StatisticsRun.id)
    )
    return db.execute(stmt).scalar_one()


def is_lock_conflict(exc: Exception) -> bool:
    return RUNNING_UNIQUE_INDEX in str(getattr(exc, "orig", exc))


def get_run(db: Session, run_id: int) -> StatisticsRun | None:
    return db.scalars(select(StatisticsRun).where(StatisticsRun.id == run_id).execution_options(populate_existing=True)).first()


def add_counters(db: Session, run_id: int, **increments: int) -> None:
    """Suma atómica de contadores (los que no se pasan no cambian)."""
    values = {name: getattr(StatisticsRun, name) + n for name, n in increments.items() if n}
    if values:
        db.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(**values))


def set_flags(db: Session, run_id: int, **flags: Any) -> None:
    db.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(**flags))


def append_detail(db: Session, run_id: int, entry: dict[str, Any]) -> None:
    db.execute(
        update(StatisticsRun)
        .where(StatisticsRun.id == run_id)
        .values(details=StatisticsRun.details.op("||")(bindparam("detail_entry", value=[entry], type_=JSONB)))
    )


def append_checks(db: Session, run_id: int, entries: list[dict[str, Any]]) -> None:
    if entries:
        db.execute(
            update(StatisticsRun)
            .where(StatisticsRun.id == run_id)
            .values(checks=StatisticsRun.checks.op("||")(bindparam("check_entries", value=entries, type_=JSONB)))
        )


def set_cursor(db: Session, run_id: int, fixture_id: int) -> None:
    db.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(cursor_fixture_id=fixture_id))


def set_coverage(db: Session, run_id: int, coverage: dict[str, Any]) -> None:
    db.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(coverage=coverage))


def finish_run(db: Session, run_id: int, *, status: str, error_message: str | None = None) -> None:
    db.execute(
        update(StatisticsRun)
        .where(StatisticsRun.id == run_id)
        .values(status=status, finished_at=func.now(), error_message=error_message)
    )


def latest_resumable_run(db: Session, season_id: int, mode: str) -> StatisticsRun | None:
    """Último run interrumpido (aborted/failed) de esa temporada y modo con cursor: --resume
    continúa después de su cursor."""
    stmt = (
        select(StatisticsRun)
        .where(
            StatisticsRun.season_id == season_id,
            StatisticsRun.mode == mode,
            StatisticsRun.status.in_(("aborted", "failed")),
            StatisticsRun.cursor_fixture_id.is_not(None),
        )
        .order_by(StatisticsRun.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def lock_running_run(db: Session) -> StatisticsRun | None:
    stmt = select(StatisticsRun).where(StatisticsRun.status == "running").with_for_update()
    return db.scalars(stmt).first()


def is_older_than(db: Session, run_id: int, minutes: int) -> bool:
    stmt = select(StatisticsRun.started_at < func.now() - func.make_interval(0, 0, 0, 0, 0, minutes)).where(StatisticsRun.id == run_id)
    return bool(db.scalar(stmt))


def requests_since(db: Session, since: datetime) -> tuple[int, int]:
    """Peticiones al proveedor registradas desde `since`: (estadísticas, live sync)."""
    stats = db.scalar(select(func.coalesce(func.sum(StatisticsRun.provider_requests), 0)).where(StatisticsRun.started_at >= since))
    live = db.scalar(select(func.coalesce(func.sum(LiveSyncRun.provider_requests), 0)).where(LiveSyncRun.started_at >= since))
    return int(stats), int(live)


def now(db: Session) -> datetime:
    return db.scalar(text("SELECT now()"))
