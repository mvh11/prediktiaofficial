"""Modelos ORM de Prediktia.

Importa aquí cada modelo nuevo para que Alembic lo detecte.
"""

from app.db.database import Base
from app.models.competition import Competition
from app.models.fixture import Fixture
from app.models.season import Season, SeasonTeam
from app.models.team import Team

__all__ = ["Base", "Competition", "Fixture", "Season", "SeasonTeam", "Team"]
