"""Migración inicial vacía (solo verifica que Alembic conecta con la BD)

Revision ID: 0001
Revises:
Create Date: 2026-10-01

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
