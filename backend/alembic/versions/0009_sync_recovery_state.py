"""Estado persistente de recovery (DI-A5D): sync_unit_states y provider_incidents

Contrato: docs/data-integrity-status.md, sección "DI-A5D DESIGN: FROZEN".

- sync_unit_states: estado actual de cada unidad recuperable (fixtures_season, catalog_season_teams),
  identificada por (unit_kind, provider, season_id). provider con ON DELETE RESTRICT; season_id con
  ON DELETE CASCADE (el estado de una temporada borrada no significa nada).
- provider_incidents: un episodio por proveedor y tipo; abierto = closed_at IS NULL, como mucho uno
  abierto por (provider, kind). Retención indefinida. 'outage' se acepta pero nada lo abre todavía.

Solo esquema: tablas vacías, sin carga inicial y sin tocar tablas existentes. Ningún servicio las
usa hasta A5E. Las FK nuevas toman bloqueos breves en providers y seasons: con un escritor activo la
migración falla por lock_timeout (5 s) y se deshace entera.

Downgrade: elimina las dos tablas (se pierde el estado de recovery y la historia de incidentes).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "5s"
UNIT_KINDS = ("fixtures_season", "catalog_season_teams")
OUTCOMES = ("succeeded", "failed", "skipped")
ERROR_SCOPES = ("unit", "provider", "database")
INCIDENT_KINDS = ("auth", "quota", "rate_limit", "outage")
CLOSE_REASONS = ("provider_recovered", "manual")
# Instante de cada sentencia, no el inicio de la transacción (now() / CURRENT_TIMESTAMP)
STATEMENT_TS = sa.text("statement_timestamp()")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.create_table(
        "provider_incidents",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("provider", sa.Text(), sa.ForeignKey("providers.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=STATEMENT_TS, nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.Text()),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("affected_units", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error_class", sa.Text(), nullable=False),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("last_endpoint", sa.Text()),
        sa.Column("retry_after_until", sa.DateTime(timezone=True)),
        sa.CheckConstraint(_in("kind", INCIDENT_KINDS), name="ck_provider_incidents_kind"),
        sa.CheckConstraint(
            "close_reason IS NULL OR " + _in("close_reason", CLOSE_REASONS), name="ck_provider_incidents_close_reason"
        ),
        sa.CheckConstraint("(closed_at IS NULL) = (close_reason IS NULL)", name="ck_provider_incidents_closed"),
        sa.CheckConstraint("last_failure_at >= opened_at", name="ck_provider_incidents_last_failure_order"),
        sa.CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="ck_provider_incidents_closed_order"),
        sa.CheckConstraint("failure_count >= 1", name="ck_provider_incidents_failure_count"),
        sa.CheckConstraint("affected_units >= 0", name="ck_provider_incidents_affected_units"),
        sa.CheckConstraint(
            "last_status_code IS NULL OR last_status_code BETWEEN 100 AND 599", name="ck_provider_incidents_status_code"
        ),
        # Solo la ruta: ni host ni query string (nada donde pueda ir una credencial)
        sa.CheckConstraint(
            "last_endpoint IS NULL OR last_endpoint ~ '^/[A-Za-z0-9/_-]*$'", name="ck_provider_incidents_endpoint"
        ),
    )
    op.create_index(
        "uq_provider_incidents_open",
        "provider_incidents",
        ["provider", "kind"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_index("ix_provider_incidents_provider_opened", "provider_incidents", ["provider", "opened_at"])

    op.create_table(
        "sync_unit_states",
        sa.Column("unit_kind", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), sa.ForeignKey("providers.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_attempt_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_outcome", sa.Text()),
        sa.Column("last_outcome_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_scope", sa.Text()),
        sa.Column("last_error_class", sa.Text()),
        sa.Column("last_error_status", sa.Integer()),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=STATEMENT_TS, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=STATEMENT_TS, nullable=False),
        sa.PrimaryKeyConstraint("unit_kind", "provider", "season_id", name="pk_sync_unit_states"),
        sa.CheckConstraint(_in("unit_kind", UNIT_KINDS), name="ck_sync_unit_states_unit_kind"),
        sa.CheckConstraint(
            "last_outcome IS NULL OR " + _in("last_outcome", OUTCOMES), name="ck_sync_unit_states_outcome"
        ),
        sa.CheckConstraint(
            "last_error_scope IS NULL OR " + _in("last_error_scope", ERROR_SCOPES), name="ck_sync_unit_states_error_scope"
        ),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_sync_unit_states_consecutive_failures"),
        sa.CheckConstraint(
            "last_error_status IS NULL OR last_error_status BETWEEN 100 AND 599", name="ck_sync_unit_states_error_status"
        ),
        sa.CheckConstraint(
            "last_error_message IS NULL OR char_length(last_error_message) <= 500",
            name="ck_sync_unit_states_error_message",
        ),
        sa.CheckConstraint("(last_outcome IS NULL) = (last_outcome_at IS NULL)", name="ck_sync_unit_states_outcome_at"),
        sa.CheckConstraint(
            "last_outcome IS DISTINCT FROM 'succeeded' "
            "OR (last_success_at IS NOT DISTINCT FROM last_outcome_at AND consecutive_failures = 0)",
            name="ck_sync_unit_states_succeeded",
        ),
        sa.CheckConstraint(
            "last_outcome IS DISTINCT FROM 'failed' OR (last_failure_at IS NOT DISTINCT FROM last_outcome_at AND consecutive_failures >= 1 "
            "AND last_error_class IS NOT NULL AND last_error_scope IS NOT NULL)",
            name="ck_sync_unit_states_failed",
        ),
        sa.CheckConstraint(
            "(last_failure_at IS NULL) = (last_error_class IS NULL) "
            "AND (last_failure_at IS NULL) = (last_error_scope IS NULL) "
            "AND (last_failure_at IS NOT NULL OR (last_error_status IS NULL AND last_error_message IS NULL "
            "AND consecutive_failures = 0))",
            name="ck_sync_unit_states_failure_metadata",
        ),
        sa.CheckConstraint(
            "(last_success_at IS NULL AND last_failure_at IS NULL) OR (last_outcome_at IS NOT NULL "
            "AND (last_success_at IS NULL OR last_success_at <= last_outcome_at) "
            "AND (last_failure_at IS NULL OR last_failure_at <= last_outcome_at))",
            name="ck_sync_unit_states_outcome_order",
        ),
    )
    op.create_index("ix_sync_unit_states_season", "sync_unit_states", ["season_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_unit_states_season", table_name="sync_unit_states")
    op.drop_table("sync_unit_states")
    op.drop_index("ix_provider_incidents_provider_opened", table_name="provider_incidents")
    op.drop_index("uq_provider_incidents_open", table_name="provider_incidents")
    op.drop_table("provider_incidents")
