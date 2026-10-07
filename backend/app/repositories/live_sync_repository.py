"""Acceso a la BD para el registro de ejecuciones de la sync en vivo (live_sync_runs)."""

from typing import Any

from sqlalchemy import bindparam, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models import LiveSyncRun

LOCK_SCOPE_BY_JOB = {"catalog": "live_sync", "fixtures": "live_sync", "check": "freshness"}
RUNNING_UNIQUE_INDEX = "uq_live_sync_runs_one_running"


def create_run(db: Session, *, job_type: str, trigger: str) -> int:
    """Registra el run 'running'. Si ya hay otro 'running' del mismo ámbito, el índice único
    parcial lanza IntegrityError (es el lock: no hay advisory locks con el pooler de Neon)."""
    stmt = (
        insert(LiveSyncRun)
        .values(job_type=job_type, trigger=trigger, status="running", lock_scope=LOCK_SCOPE_BY_JOB[job_type])
        .returning(LiveSyncRun.id)
    )
    return db.execute(stmt).scalar_one()


def append_detail(db: Session, run_id: int, entry: dict[str, Any]) -> None:
    """Añade una entrada a details. Se llama dentro de la transacción de la competición."""
    db.execute(
        update(LiveSyncRun)
        .where(LiveSyncRun.id == run_id)
        .values(details=LiveSyncRun.details.op("||")(bindparam("detail_entry", value=[entry], type_=JSONB)))
    )


def finish_run(db: Session, run_id: int, *, status: str, **values: Any) -> None:
    db.execute(update(LiveSyncRun).where(LiveSyncRun.id == run_id).values(status=status, finished_at=func.now(), **values))


def get_run(db: Session, run_id: int) -> LiveSyncRun | None:
    return db.get(LiveSyncRun, run_id)


def lock_running_run(db: Session, lock_scope: str) -> LiveSyncRun | None:
    """Run en curso del ámbito, bloqueado con SELECT ... FOR UPDATE hasta el fin de la transacción."""
    stmt = select(LiveSyncRun).where(LiveSyncRun.lock_scope == lock_scope, LiveSyncRun.status == "running").with_for_update()
    return db.scalars(stmt).first()


def is_older_than(db: Session, run_id: int, minutes: int) -> bool:
    stmt = select(LiveSyncRun.started_at < func.now() - func.make_interval(0, 0, 0, 0, 0, minutes)).where(LiveSyncRun.id == run_id)
    return bool(db.scalar(stmt))


def latest_finished_run(db: Session, job_type: str) -> LiveSyncRun | None:
    """Última ejecución terminada (de cualquier estado final) de ese tipo."""
    stmt = (
        select(LiveSyncRun)
        .where(LiveSyncRun.job_type == job_type, LiveSyncRun.status != "running")
        .order_by(LiveSyncRun.finished_at.desc(), LiveSyncRun.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def is_lock_conflict(exc: Exception) -> bool:
    return RUNNING_UNIQUE_INDEX in str(getattr(exc, "orig", exc))


def now(db: Session):
    return db.scalar(text("SELECT now()"))
