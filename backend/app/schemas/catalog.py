"""Esquemas propios del catálogo (competiciones, temporadas y equipos).

- *Data: lo que devuelve un adapter, ya traducido a formato Prediktia.
- *Out: lo que devuelven nuestros endpoints.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


# --- Datos que entregan los adapters -------------------------------------------------


class SeasonData(BaseModel):
    year: int
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False


class CompetitionData(BaseModel):
    external_id: int
    name: str
    type: str | None = None  # "League" o "Cup"
    country: str | None = None
    country_code: str | None = None
    logo_url: str | None = None
    seasons: list[SeasonData] = Field(default_factory=list)


class TeamData(BaseModel):
    external_id: int
    name: str
    code: str | None = None
    country: str | None = None
    founded: int | None = None
    is_national: bool = False
    logo_url: str | None = None


# --- Respuestas de la API --------------------------------------------------------------


class CompetitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: int
    name: str
    type: str | None
    country: str | None
    country_code: str | None
    logo_url: str | None
    current_season: int | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: int
    name: str
    code: str | None
    country: str | None
    founded: int | None
    is_national: bool
    logo_url: str | None


class CompetitionSyncResult(BaseModel):
    external_id: int
    name: str | None = None
    season: int | None = None
    previous_season: int | None = None  # temporada actual en la BD antes de esta sync
    teams: int = 0
    error: str | None = None


class CatalogSyncResult(BaseModel):
    competitions_synced: int
    teams_synced: int
    competitions: list[CompetitionSyncResult]
