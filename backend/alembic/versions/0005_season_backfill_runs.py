"""Registro de ejecuciones del backfill histórico (season_backfill_runs)

Tabla nueva y aislada: no modifica ninguna tabla existente ni hace backfill de datos.
Downgrade: elimina la tabla (se pierde el historial de ejecuciones, no datos deportivos).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COUNTERS = [
    "received_count",
    "new_count",
    "existing_count",
    "changed_count",
    "unchanged_count",
    "new_teams_count",
    "new_season_teams_count",
    "warning_count",
    "blocking_count",
]


def upgrade() -> None:
    op.create_table(
        "season_backfill_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "competition_id",
            sa.Integer(),
            sa.ForeignKey("competitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id", ondelete="CASCADE")),
        sa.Column("requested_year", sa.Integer(), nullable=False),
        sa.Column(
            "provider",
            sa.Text(),
            sa.ForeignKey("providers.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_dry_run", sa.Boolean(), nullable=False),
        sa.Column("is_refresh", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *[sa.Column(c, sa.Integer(), server_default=sa.text("0"), nullable=False) for c in COUNTERS],
        sa.Column(
            "checks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'dry_run_completed', 'blocked', 'failed')",
            name="ck_season_backfill_runs_status",
        ),
        sa.CheckConstraint(
            "(finished_at IS NULL) = (status = 'running')", name="ck_season_backfill_runs_finished"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="ck_season_backfill_runs_order"
        ),
        sa.CheckConstraint(
            "(status <> 'completed' OR NOT is_dry_run) AND (status <> 'dry_run_completed' OR is_dry_run)",
            name="ck_season_backfill_runs_dry_run",
        ),
        sa.CheckConstraint(
            " AND ".join(f"{c} >= 0" for c in COUNTERS), name="ck_season_backfill_runs_counters"
        ),
    )
    op.create_index(
        "ix_season_backfill_runs_competition_year", "season_backfill_runs", ["competition_id", "requested_year"]
    )
    op.create_index("ix_season_backfill_runs_season_status", "season_backfill_runs", ["season_id", "status"])
    op.create_index(
        "uq_season_backfill_runs_one_running",
        "season_backfill_runs",
        ["competition_id", "requested_year"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_table("season_backfill_runs")
