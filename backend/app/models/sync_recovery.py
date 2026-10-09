"""Estado persistente de recovery (DI-A5D). Contrato: docs/data-integrity-status.md, "DI-A5D DESIGN: FROZEN".

Solo esquema: en A5D ningún servicio lee ni escribe estas tablas (eso es A5E).

- SyncUnitState (sync_unit_states): estado actual de una unidad recuperable, identificada por
  (unit_kind, provider, season_id). Sin fila = nunca intentada. El éxito se registra en la MISMA
  transacción de dominio que lo produce (last_success_at = statement_timestamp()); un fallo se
  registra después del rollback, en otra transacción y best-effort. "Interrumpida" no se guarda:
  es last_attempt_started_at posterior a last_outcome_at.
- ProviderIncident (provider_incidents): un episodio por proveedor y tipo; abierto = closed_at IS
  NULL, como mucho uno abierto por (provider, kind). Retención indefinida. 'outage' está reservado:
  la persistencia lo acepta, pero nada lo abre todavía.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

UNIT_KINDS = ("fixtures_season", "catalog_season_teams")
OUTCOMES = ("succeeded", "failed", "skipped")
ERROR_SCOPES = ("unit", "provider", "database")
INCIDENT_KINDS = ("auth", "quota", "rate_limit", "outage")
CLOSE_REASONS = ("provider_recovered", "manual")
# Instante de cada sentencia, no el inicio de la transacción (now() / CURRENT_TIMESTAMP)
STATEMENT_TS = text("statement_timestamp()")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class SyncUnitState(Base):
    __tablename__ = "sync_unit_states"
    __table_args__ = (
        PrimaryKeyConstraint("unit_kind", "provider", "season_id", name="pk_sync_unit_states"),
        Index("ix_sync_unit_states_season", "season_id"),
        CheckConstraint(_in("unit_kind", UNIT_KINDS), name="ck_sync_unit_states_unit_kind"),
        CheckConstraint("last_outcome IS NULL OR " + _in("last_outcome", OUTCOMES), name="ck_sync_unit_states_outcome"),
        CheckConstraint(
            "last_error_scope IS NULL OR " + _in("last_error_scope", ERROR_SCOPES), name="ck_sync_unit_states_error_scope"
        ),
        CheckConstraint("consecutive_failures >= 0", name="ck_sync_unit_states_consecutive_failures"),
        CheckConstraint(
            "last_error_status IS NULL OR last_error_status BETWEEN 100 AND 599", name="ck_sync_unit_states_error_status"
        ),
        CheckConstraint(
            "last_error_message IS NULL OR char_length(last_error_message) <= 500",
            name="ck_sync_unit_states_error_message",
        ),
        CheckConstraint("(last_outcome IS NULL) = (last_outcome_at IS NULL)", name="ck_sync_unit_states_outcome_at"),
        CheckConstraint(
            "last_outcome IS DISTINCT FROM 'succeeded' "
            "OR (last_success_at IS NOT DISTINCT FROM last_outcome_at AND consecutive_failures = 0)",
            name="ck_sync_unit_states_succeeded",
        ),
        CheckConstraint(
            "last_outcome IS DISTINCT FROM 'failed' OR (last_failure_at IS NOT DISTINCT FROM last_outcome_at AND consecutive_failures >= 1 "
            "AND last_error_class IS NOT NULL AND last_error_scope IS NOT NULL)",
            name="ck_sync_unit_states_failed",
        ),
        CheckConstraint(
            "(last_failure_at IS NULL) = (last_error_class IS NULL) "
            "AND (last_failure_at IS NULL) = (last_error_scope IS NULL) "
            "AND (last_failure_at IS NOT NULL OR (last_error_status IS NULL AND last_error_message IS NULL "
            "AND consecutive_failures = 0))",
            name="ck_sync_unit_states_failure_metadata",
        ),
        CheckConstraint(
            "(last_success_at IS NULL AND last_failure_at IS NULL) OR (last_outcome_at IS NOT NULL "
            "AND (last_success_at IS NULL OR last_success_at <= last_outcome_at) "
            "AND (last_failure_at IS NULL OR last_failure_at <= last_outcome_at))",
            name="ck_sync_unit_states_outcome_order",
        ),
    )

    unit_kind: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, ForeignKey("providers.code", ondelete="RESTRICT"))
    season_id: Mapped[int] = mapped_column(Integer, ForeignKey("seasons.id", ondelete="CASCADE"))
    last_attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outcome: Mapped[str | None] = mapped_column(Text)
    last_outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_scope: Mapped[str | None] = mapped_column(Text)
    last_error_class: Mapped[str | None] = mapped_column(Text)
    last_error_status: Mapped[int | None] = mapped_column(Integer)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=STATEMENT_TS)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=STATEMENT_TS)


class ProviderIncident(Base):
    __tablename__ = "provider_incidents"
    __table_args__ = (
        Index(
            "uq_provider_incidents_open", "provider", "kind", unique=True, postgresql_where=text("closed_at IS NULL")
        ),
        Index("ix_provider_incidents_provider_opened", "provider", "opened_at"),
        CheckConstraint(_in("kind", INCIDENT_KINDS), name="ck_provider_incidents_kind"),
        CheckConstraint(
            "close_reason IS NULL OR " + _in("close_reason", CLOSE_REASONS), name="ck_provider_incidents_close_reason"
        ),
        CheckConstraint("(closed_at IS NULL) = (close_reason IS NULL)", name="ck_provider_incidents_closed"),
        CheckConstraint("last_failure_at >= opened_at", name="ck_provider_incidents_last_failure_order"),
        CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="ck_provider_incidents_closed_order"),
        CheckConstraint("failure_count >= 1", name="ck_provider_incidents_failure_count"),
        CheckConstraint("affected_units >= 0", name="ck_provider_incidents_affected_units"),
        CheckConstraint(
            "last_status_code IS NULL OR last_status_code BETWEEN 100 AND 599", name="ck_provider_incidents_status_code"
        ),
        CheckConstraint(
            "last_endpoint IS NULL OR last_endpoint ~ '^/[A-Za-z0-9/_-]*$'", name="ck_provider_incidents_endpoint"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider: Mapped[str] = mapped_column(Text, ForeignKey("providers.code", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=STATEMENT_TS)
    last_failure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(Text)
    failure_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    affected_units: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error_class: Mapped[str] = mapped_column(Text)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_endpoint: Mapped[str | None] = mapped_column(Text)
    retry_after_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
