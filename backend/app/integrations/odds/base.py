"""Interfaz que debe cumplir cualquier proveedor de cuotas.

El resto de Prediktia depende de esta clase, NO de un proveedor concreto.
"""

from abc import ABC, abstractmethod

from app.schemas.provider import ProviderStatus


class OddsProvider(ABC):
    name: str

    @abstractmethod
    async def check_status(self) -> ProviderStatus:
        """Comprueba que el proveedor responde y devuelve el estado de la cuenta."""

    # Métodos futuros (cuotas por partido, bookmakers, mercados, historial)
    # se añadirán aquí en próximos módulos.
