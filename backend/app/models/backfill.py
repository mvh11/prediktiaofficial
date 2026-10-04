"""Registro auditado de cada intento de backfill histórico por competición + temporada."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# running: en curso · completed: importación aplicada · dry_run_completed: evaluación sin escribir
# blocked: detenido por integridad (precondición o check bloqueante), sin escrituras de dominio
# failed: fallo técnico (proveedor o BD), con rollback de las escrituras de dominio
RUN_STATUSES = ("running", "completed", "dry_run_completed", "blocked", "failed")

_COUNTERS = (
    "received_count",
    "new_count",
    "existing_count",
    "changed_count",
    "unchanged_count",
    "new_teams_count",
    "new_season_teams_count",
    "warning_count",
    "blocking_count",
)


class SeasonBackfillRun(Base):
    __tablename__ = "season_backfill_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in RUN_STATUSES) + ")",
            name="ck_season_backfill_runs_status",
        ),
        # finished_at existe si y solo si el run ya no está en curso
        CheckConstraint(
            "(finished_at IS NULL) = (status = 'running')", name="ck_season_backfill_runs_finished"
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="ck_season_backfill_runs_order"
        ),
        # Un dry-run nunca puede figurar como importación aplicada, ni al revés
        CheckConstraint(
            "(status <> 'completed' OR NOT is_dry_run) AND (status <> 'dry_run_completed' OR is_dry_run)",
            name="ck_season_backfill_runs_dry_run",
        ),
        CheckConstraint(
            " AND ".join(f"{c} >= 0" for c in _COUNTERS), name="ck_season_backfill_runs_counters"
        ),
        Index("ix_season_backfill_runs_competition_year", "competition_id", "requested_year"),
        Index("ix_season_backfill_runs_season_status", "season_id", "status"),
        # Nunca dos runs simultáneos del mismo par
        Index(
            "uq_season_backfill_runs_one_running",
            "competition_id",
            "requested_year",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    competition_id: Mapped[int] = mapped_column(Integer, ForeignKey("competitions.id", ondelete="CASCADE"))
    # NULL solo si la temporada pedida no existe en Prediktia (run bloqueado)
    season_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("seasons.id", ondelete="CASCADE"))
    requested_year: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(Text, ForeignKey("providers.code", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text)
    is_dry_run: Mapped[bool] = mapped_column(Boolean)
    is_refresh: Mapped[bool] = mapped_column(Boolean)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # En un dry-run los contadores significan "se insertarían / cambiarían / quedarían igual"
    received_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    new_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    existing_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    changed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    unchanged_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    new_teams_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    new_season_teams_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    warning_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    blocking_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    # Resultado de Q1–Q15 (id, severidad, passed, count, detalle y como mucho unos pocos ids)
    checks: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    error_message: Mapped[str | None] = mapped_column(Text)
