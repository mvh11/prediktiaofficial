"""Esquemas propios de partidos (fixtures) y resultados.

- *Data: lo que devuelve un adapter, ya traducido a formato Prediktia.
- *Out: lo que devuelven nuestros endpoints.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.catalog import TeamData

# Estados (código corto de API-Football) en los que el partido ya terminó jugándose.
# Solo en ellos existe marcador a 90' (fulltime)
FINISHED_STATUSES = {"FT", "AET", "PEN"}
# Estados finales: terminado jugándose o con resultado administrativo (AWD = adjudicado,
# WO = incomparecencia). Su marcador ya no cambia salvo corrección del proveedor
FINAL_STATUSES = FINISHED_STATUSES | {"AWD", "WO"}


# --- Datos que entregan los adapters -------------------------------------------------


class FixtureData(BaseModel):
    external_id: int
    competition_external_id: int
    season: int
    round: str | None = None
    kickoff_at: datetime
    status_short: str
    status_long: str | None = None
    elapsed: int | None = None
    venue_name: str | None = None
    venue_city: str | None = None
    referee: str | None = None
    home_team: TeamData
    away_team: TeamData
    home_goals: int | None = None
    away_goals: int | None = None
    halftime_home: int | None = None
    halftime_away: int | None = None
    extratime_home: int | None = None
    extratime_away: int | None = None
    penalty_home: int | None = None
    penalty_away: int | None = None
    # Marcador a 90' (sin prórroga ni penaltis). Solo en FT, AET y PEN
    fulltime_home: int | None = None
    fulltime_away: int | None = None


# --- Respuestas de la API --------------------------------------------------------------


class FixtureTeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    logo_url: str | None


class FixtureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: int
    competition_id: int
    season: int
    round: str | None
    kickoff_at: datetime
    status_short: str
    status_long: str | None
    elapsed: int | None
    venue_name: str | None
    venue_city: str | None
    referee: str | None
    home_team: FixtureTeamOut
    away_team: FixtureTeamOut
    home_goals: int | None
    away_goals: int | None
    halftime_home: int | None
    halftime_away: int | None
    extratime_home: int | None
    extratime_away: int | None
    penalty_home: int | None
    penalty_away: int | None
    fulltime_home: int | None = Field(default=None, description="Goles local a los 90' (sin prórroga ni penaltis)")
    fulltime_away: int | None = Field(default=None, description="Goles visitante a los 90' (sin prórroga ni penaltis)")
    is_finished: bool = Field(default=False, description="True si el partido ya terminó (FT, AET o PEN)")


class CompetitionFixtureSyncResult(BaseModel):
    competition_id: int
    name: str
    season: int | None = None
    fixtures: int = 0  # recibidos (sin duplicados) y guardados: created + updated + unchanged
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    teams_created: int = 0
    skipped: str | None = None  # motivo si no se consultó o no se escribió (no es un error)
    error: str | None = None


class FixtureSyncResult(BaseModel):
    fixtures_synced: int
    competitions: list[CompetitionFixtureSyncResult]
