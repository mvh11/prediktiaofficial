"""Esquemas propios de Prediktia para describir el estado de un proveedor externo.

Son independientes de cualquier proveedor: cada adapter traduce la respuesta
de su API a este formato.
"""

from typing import Any

from pydantic import BaseModel, Field


class ProviderStatus(BaseModel):
    provider: str = Field(description="Nombre interno del proveedor")
    reachable: bool = Field(description="True si la API respondió correctamente")
    plan: str | None = Field(default=None, description="Plan contratado, si la API lo informa")
    requests_used: int | None = Field(default=None, description="Peticiones consumidas (según el proveedor)")
    requests_limit: int | None = Field(default=None, description="Límite de peticiones, si la API lo informa")
    details: dict[str, Any] = Field(default_factory=dict, description="Información adicional no sensible")
