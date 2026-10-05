"""Registro de ejecuciones de la sync en vivo (live_sync_runs)

Tabla nueva y aislada: no modifica ninguna tabla existente ni hace backfill de datos.
Downgrade: elimina la tabla (se pierde el historial de ejecuciones, no datos deportivos).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COUNTERS = [
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
]


def upgrade() -> None:
    op.create_table(
        "live_sync_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("lock_scope", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *[sa.Column(c, sa.Integer(), server_default=sa.text("0"), nullable=False) for c in COUNTERS],
        sa.Column("rate_limited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("auth_failed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *[
            sa.Column(c, postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text(default), nullable=False)
            for c, default in (("details", "'[]'::jsonb"), ("freshness", "'{}'::jsonb"), ("catalog_changes", "'[]'::jsonb"))
        ],
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("job_type IN ('catalog', 'fixtures', 'check')", name="ck_live_sync_runs_job_type"),
        sa.CheckConstraint("trigger IN ('cli', 'scheduler', 'api')", name="ck_live_sync_runs_trigger"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_errors', 'failed', 'aborted')",
            name="ck_live_sync_runs_status",
        ),
        sa.CheckConstraint("lock_scope IN ('live_sync', 'freshness')", name="ck_live_sync_runs_lock_scope"),
        # catálogo y fixtures comparten el lock 'live_sync'; el check de solo lectura tiene el suyo
        sa.CheckConstraint("(job_type = 'check') = (lock_scope = 'freshness')", name="ck_live_sync_runs_scope_by_job"),
        sa.CheckConstraint("(finished_at IS NULL) = (status = 'running')", name="ck_live_sync_runs_finished"),
        sa.CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_live_sync_runs_order"),
        sa.CheckConstraint(" AND ".join(f"{c} >= 0" for c in COUNTERS), name="ck_live_sync_runs_counters"),
        sa.CheckConstraint(
            "fixtures_received = fixtures_created + fixtures_updated + fixtures_unchanged",
            name="ck_live_sync_runs_fixture_counts",
        ),
    )
    op.create_index("ix_live_sync_runs_job_started", "live_sync_runs", ["job_type", "started_at"])
    # Nunca dos ejecuciones 'running' del mismo ámbito (catálogo y fixtures comparten 'live_sync')
    op.create_index(
        "uq_live_sync_runs_one_running",
        "live_sync_runs",
        ["lock_scope"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_live_sync_runs_one_running", table_name="live_sync_runs")
    op.drop_index("ix_live_sync_runs_job_started", table_name="live_sync_runs")
    op.drop_table("live_sync_runs")
