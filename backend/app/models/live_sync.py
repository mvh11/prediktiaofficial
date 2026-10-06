"""Registro auditado de cada ejecución de la sync en vivo (catálogo, fixtures, check de frescura)."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

JOB_TYPES = ("catalog", "fixtures", "check")
TRIGGERS = ("cli", "scheduler", "api")
# completed: todo bien · completed_with_errors: alguna competición falló o el proveedor limitó
# failed: fallo global (excepción, /leagues caído) · aborted: interrumpido o recuperado a mano
RUN_STATUSES = ("running", "completed", "completed_with_errors", "failed", "aborted")
# Catálogo y fixtures comparten ámbito (nunca a la vez); el check de solo lectura tiene el suyo
LOCK_SCOPES = ("live_sync", "freshness")

COUNTERS = (
    "competitions_attempted",
    "competitions_succeeded",
    "competitions_failed",
    "competitions_skipped",
    "fixtures_received",
    "fixtures_created",
    "fixtures_updated",
    "fixtures_unchanged",
    "teams_created",
    "provider_requests",
    "provider_retries",
    "warning_count",
    "error_count",
)


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


class LiveSyncRun(Base):
    __tablename__ = "live_sync_runs"
    __table_args__ = (
        CheckConstraint(f"job_type IN {_in(JOB_TYPES)}", name="ck_live_sync_runs_job_type"),
        CheckConstraint(f"trigger IN {_in(TRIGGERS)}", name="ck_live_sync_runs_trigger"),
        CheckConstraint(f"status IN {_in(RUN_STATUSES)}", name="ck_live_sync_runs_status"),
        CheckConstraint(f"lock_scope IN {_in(LOCK_SCOPES)}", name="ck_live_sync_runs_lock_scope"),
        CheckConstraint("(job_type = 'check') = (lock_scope = 'freshness')", name="ck_live_sync_runs_scope_by_job"),
        CheckConstraint("(finished_at IS NULL) = (status = 'running')", name="ck_live_sync_runs_finished"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_live_sync_runs_order"),
        CheckConstraint(" AND ".join(f"{c} >= 0" for c in COUNTERS), name="ck_live_sync_runs_counters"),
        CheckConstraint(
            "fixtures_received = fixtures_created + fixtures_updated + fixtures_unchanged",
            name="ck_live_sync_runs_fixture_counts",
        ),
        Index("ix_live_sync_runs_job_started", "job_type", "started_at"),
        Index(
            "uq_live_sync_runs_one_running",
            "lock_scope",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_type: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    lock_scope: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    competitions_attempted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    competitions_succeeded: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    competitions_failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    competitions_skipped: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_received: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_created: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_updated: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_unchanged: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    teams_created: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    provider_requests: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    provider_retries: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    warning_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    rate_limited: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    auth_failed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    # Una entrada por competición (o por check), escrita en la misma transacción que sus datos
    details: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    freshness: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Cambios de temporada actual detectados por la sync del catálogo
    catalog_changes: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    error_message: Mapped[str | None] = mapped_column(Text)
