"""Interfaz que debe cumplir cualquier proveedor de datos futbolísticos.

El resto de Prediktia depende de esta clase, NO de un proveedor concreto.
Para cambiar de proveedor basta con crear otro adapter que la implemente.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Self

from app.schemas.catalog import CompetitionData, TeamData
from app.schemas.fixture import FixtureData
from app.schemas.provider import ProviderStatus


class FootballDataProvider(ABC):
    name: str

    # Un run (p. ej. una sync) se ejecuta dentro de `async with provider:`. Por defecto no hace
    # nada; un adapter con recursos (un cliente HTTP) los abre al entrar y los libera al salir,
    # también si el run termina con una excepción.
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    @abstractmethod
    async def check_status(self) -> ProviderStatus:
        """Comprueba que el proveedor responde y devuelve el estado de la cuenta."""

    @abstractmethod
    async def get_competitions(self, external_ids: list[int]) -> list[CompetitionData]:
        """Devuelve las competiciones pedidas, con sus temporadas."""

    @abstractmethod
    async def get_teams(self, competition_external_id: int, season: int) -> list[TeamData]:
        """Devuelve los equipos que juegan una temporada de una competición."""

    @abstractmethod
    async def get_fixtures(
        self,
        competition_external_id: int,
        season: int,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[FixtureData]:
        """Devuelve los partidos de una temporada, opcionalmente entre dos fechas."""

    # Métodos futuros (estadísticas, alineaciones, etc.) se añadirán
    # aquí en próximos módulos.
