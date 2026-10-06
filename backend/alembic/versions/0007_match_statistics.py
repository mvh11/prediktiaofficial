"""Estadísticas de partido (M5): statistics_runs, fixture_statistics_observations, fixture_team_statistics

Tres tablas nuevas y vacías: no modifica ninguna tabla existente ni hace backfill de datos.
Downgrade: elimina las tres tablas (se pierden estadísticas y runs, no datos de M1–M4).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FIXTURE_OUTCOMES = [
    "fixtures_available",
    "fixtures_partial",
    "fixtures_empty",
    "fixtures_blocked",
    "fixtures_missing_in_response",
]
RUN_COUNTERS = [
    "fixtures_targeted",
    "fixtures_attempted",
    *FIXTURE_OUTCOMES,
    "provider_requests",
    "provider_retries",
    "observations_created",
    "observations_unchanged",
    "rows_created",
    "rows_updated",
    "rows_unchanged",
    "warning_count",
    "blocking_count",
]
INTEGER_STATS = [
    "shots_on_goal",
    "shots_off_goal",
    "shots_total",
    "shots_blocked",
    "shots_inside_box",
    "shots_outside_box",
    "fouls",
    "corners",
    "offsides",
    "yellow_cards",
    "red_cards",
    "goalkeeper_saves",
    "passes_total",
    "passes_accurate",
]
PERCENTAGE_STATS = ["possession_pct", "passes_pct"]


def _jsonb(name: str, default: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text(default), nullable=False)


def upgrade() -> None:
    op.create_table(
        "statistics_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("competition_id", sa.Integer(), sa.ForeignKey("competitions.id", ondelete="CASCADE")),
        sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id", ondelete="CASCADE")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("lock_scope", sa.Text(), server_default=sa.text("'statistics'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cursor_fixture_id", sa.Integer()),
        *[sa.Column(c, sa.Integer(), server_default=sa.text("0"), nullable=False) for c in RUN_COUNTERS],
        sa.Column("rate_limited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("auth_failed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _jsonb("checks", "'[]'::jsonb"),
        _jsonb("details", "'[]'::jsonb"),
        _jsonb("coverage", "'{}'::jsonb"),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("trigger IN ('cli', 'scheduler')", name="ck_statistics_runs_trigger"),
        sa.CheckConstraint("mode IN ('dry_run', 'apply')", name="ck_statistics_runs_mode"),
        sa.CheckConstraint("scope IN ('season', 'fixtures', 'live')", name="ck_statistics_runs_scope"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_errors', 'failed', 'aborted', 'dry_run_completed')",
            name="ck_statistics_runs_status",
        ),
        sa.CheckConstraint("lock_scope IN ('statistics')", name="ck_statistics_runs_lock_scope"),
        sa.CheckConstraint(
            "scope <> 'season' OR (competition_id IS NOT NULL AND season_id IS NOT NULL)",
            name="ck_statistics_runs_season_scope",
        ),
        # dry_run termina en dry_run_completed; apply en completed/completed_with_errors
        sa.CheckConstraint(
            "(status = 'dry_run_completed' AND mode = 'dry_run') OR (status IN ('completed', 'completed_with_errors') "
            "AND mode = 'apply') OR status IN ('running', 'failed', 'aborted')",
            name="ck_statistics_runs_status_mode",
        ),
        sa.CheckConstraint("(finished_at IS NULL) = (status = 'running')", name="ck_statistics_runs_finished"),
        sa.CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_statistics_runs_order"),
        sa.CheckConstraint(" AND ".join(f"{c} >= 0" for c in RUN_COUNTERS), name="ck_statistics_runs_counters"),
        # El reparto de partidos solo se exige al terminar bien: un run 'running' se construye por
        # lotes, y failed/aborted pueden haberse cortado a mitad de uno
        sa.CheckConstraint(
            "status NOT IN ('completed', 'completed_with_errors', 'dry_run_completed') OR "
            f"(fixtures_attempted = {' + '.join(FIXTURE_OUTCOMES)} AND fixtures_attempted <= fixtures_targeted)",
            name="ck_statistics_runs_fixture_accounting",
        ),
    )
    op.create_index("ix_statistics_runs_season_started", "statistics_runs", ["season_id", "started_at"])
    # El lock: nunca dos ejecuciones 'running' del mismo ámbito (mismo patrón que live_sync_runs)
    op.create_index(
        "uq_statistics_runs_one_running",
        "statistics_runs",
        ["lock_scope"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "fixture_statistics_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Text(), sa.ForeignKey("providers.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider_fixture_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("statistics_runs.id", ondelete="SET NULL")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("fixture_status_at_fetch", sa.Text()),
        sa.Column("availability", sa.Text(), nullable=False),
        sa.Column("teams_returned", sa.SmallInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_latest", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("source IN ('backfill', 'live', 'manual')", name="ck_fso_source"),
        sa.CheckConstraint("availability IN ('available', 'partial', 'empty')", name="ck_fso_availability"),
        sa.CheckConstraint("teams_returned BETWEEN 0 AND 2", name="ck_fso_teams_returned"),
        sa.CheckConstraint(
            "(availability = 'empty' AND teams_returned = 0) OR (availability = 'partial' AND teams_returned = 1) "
            "OR (availability = 'available' AND teams_returned = 2)",
            name="ck_fso_availability_teams",
        ),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_fso_payload_hash"),
        sa.CheckConstraint("last_observed_at >= observed_at", name="ck_fso_observed_order"),
        sa.UniqueConstraint("id", "fixture_id", "provider", name="uq_fso_id_fixture_provider"),
    )
    # Exactamente una observación vigente por partido y proveedor
    op.create_index(
        "uq_fso_one_latest",
        "fixture_statistics_observations",
        ["fixture_id", "provider"],
        unique=True,
        postgresql_where=sa.text("is_latest"),
    )
    # Consultas as-of (M5.7): última observación disponible antes de un cutoff
    op.create_index(
        "ix_fso_fixture_provider_available", "fixture_statistics_observations", ["fixture_id", "provider", "available_at"]
    )

    op.create_table(
        "fixture_team_statistics",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider", sa.Text(), sa.ForeignKey("providers.code", ondelete="RESTRICT"), nullable=False),
        sa.Column("observation_id", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("normalizer_version", sa.SmallInteger(), nullable=False),
        *[sa.Column(c, sa.Integer()) for c in INTEGER_STATS],
        sa.Column("possession_pct", sa.Numeric(5, 2)),
        sa.Column("passes_pct", sa.Numeric(5, 2)),
        sa.Column("expected_goals", sa.Numeric(6, 3)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("side IN ('home', 'away')", name="ck_fts_side"),
        sa.CheckConstraint("normalizer_version > 0", name="ck_fts_normalizer_version"),
        *[sa.CheckConstraint(f"{c} IS NULL OR {c} >= 0", name=f"ck_fts_{c}_nonneg") for c in INTEGER_STATS],
        *[sa.CheckConstraint(f"{c} IS NULL OR {c} BETWEEN 0 AND 100", name=f"ck_fts_{c}_range") for c in PERCENTAGE_STATS],
        sa.CheckConstraint("expected_goals IS NULL OR expected_goals >= 0", name="ck_fts_expected_goals_nonneg"),
        sa.UniqueConstraint("fixture_id", "team_id", "provider", name="uq_fts_fixture_team_provider"),
        sa.UniqueConstraint("fixture_id", "side", "provider", name="uq_fts_fixture_side_provider"),
        # La observación enlazada tiene que ser del mismo partido y proveedor
        sa.ForeignKeyConstraint(
            ["observation_id", "fixture_id", "provider"],
            [
                "fixture_statistics_observations.id",
                "fixture_statistics_observations.fixture_id",
                "fixture_statistics_observations.provider",
            ],
            name="fk_fts_observation_same_fixture",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_fts_team_fixture", "fixture_team_statistics", ["team_id", "fixture_id"])
    op.create_index("ix_fts_observation", "fixture_team_statistics", ["observation_id"])


def downgrade() -> None:
    op.drop_index("ix_fts_observation", table_name="fixture_team_statistics")
    op.drop_index("ix_fts_team_fixture", table_name="fixture_team_statistics")
    op.drop_table("fixture_team_statistics")
    op.drop_index("ix_fso_fixture_provider_available", table_name="fixture_statistics_observations")
    op.drop_index("uq_fso_one_latest", table_name="fixture_statistics_observations")
    op.drop_table("fixture_statistics_observations")
    op.drop_index("uq_statistics_runs_one_running", table_name="statistics_runs")
    op.drop_index("ix_statistics_runs_season_started", table_name="statistics_runs")
    op.drop_table("statistics_runs")
