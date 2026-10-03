"""Partidos (fixtures) y resultados

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fixtures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column(
            "season_id",
            sa.Integer(),
            sa.ForeignKey("seasons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round", sa.String(100)),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_short", sa.String(10), nullable=False),
        sa.Column("status_long", sa.String(50)),
        sa.Column("elapsed", sa.Integer()),
        sa.Column("venue_name", sa.String(200)),
        sa.Column("venue_city", sa.String(100)),
        sa.Column("referee", sa.String(150)),
        sa.Column("home_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("away_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("home_goals", sa.Integer()),
        sa.Column("away_goals", sa.Integer()),
        sa.Column("halftime_home", sa.Integer()),
        sa.Column("halftime_away", sa.Integer()),
        sa.Column("extratime_home", sa.Integer()),
        sa.Column("extratime_away", sa.Integer()),
        sa.Column("penalty_home", sa.Integer()),
        sa.Column("penalty_away", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fixtures_external_id", "fixtures", ["external_id"], unique=True)
    op.create_index("ix_fixtures_season_id", "fixtures", ["season_id"])
    op.create_index("ix_fixtures_kickoff_at", "fixtures", ["kickoff_at"])
    op.create_index("ix_fixtures_status_short", "fixtures", ["status_short"])
    op.create_index("ix_fixtures_home_team_id", "fixtures", ["home_team_id"])
    op.create_index("ix_fixtures_away_team_id", "fixtures", ["away_team_id"])


def downgrade() -> None:
    op.drop_table("fixtures")
