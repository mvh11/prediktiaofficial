"""Interfaz que debe cumplir cualquier proveedor de datos futbolísticos.

El resto de Prediktia depende de esta clase, NO de un proveedor concreto.
Para cambiar de proveedor basta con crear otro adapter que la implemente.
"""

from abc import ABC, abstractmethod

from app.schemas.provider import ProviderStatus


class FootballDataProvider(ABC):
    name: str

    @abstractmethod
    async def check_status(self) -> ProviderStatus:
        """Comprueba que el proveedor responde y devuelve el estado de la cuenta."""

    # Métodos futuros (competiciones, equipos, fixtures, etc.) se añadirán
    # aquí en próximos módulos.
