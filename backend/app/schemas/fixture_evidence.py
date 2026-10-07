"""Identidad de la evidencia de una respuesta del proveedor (DI-A6). Contrato: data-integrity-status.md, "DI-A6C".

Una respuesta lógica del proveedor = un FixtureEvidence: todos los partidos de esa respuesta comparten
evidence_id y observed_at. Lo crea el servicio llamante JUSTO DESPUÉS de recibir la respuesta con
éxito (reintentos incluidos) y antes de abrir la transacción de escritura, con received().

- Repetir exactamente la misma respuesta (el mismo objeto) reutiliza su evidence_id: no crea filas.
- Volver a pedir los datos al proveedor es otra respuesta: received() da un evidence_id nuevo.
- observed_at = reloj de la app al recibir. Nunca la fecha del partido, de la temporada ni de la BD.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

EVIDENCE_SOURCES = ("sync", "backfill", "bootstrap")
# Fuentes que son evidencia de un proveedor (y por eso llevan provider). bootstrap solo lo usa la migración 0008
PROVIDER_SOURCES = ("sync", "backfill")


@dataclass(frozen=True)
class FixtureEvidence:
    source: str
    provider: str | None
    observed_at: datetime
    evidence_id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        if self.source not in EVIDENCE_SOURCES:
            raise ValueError(f"source de evidencia desconocido: {self.source!r}")
        if (self.source in PROVIDER_SOURCES) != (self.provider is not None):
            raise ValueError(f"source {self.source!r} y provider {self.provider!r} no encajan")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("observed_at tiene que ser un instante en UTC (con zona horaria)")
        if not isinstance(self.evidence_id, uuid.UUID):
            raise ValueError("evidence_id tiene que ser un UUID")

    @classmethod
    def received(cls, source: str, provider: str) -> "FixtureEvidence":
        """Evidencia de una respuesta recién recibida: evidence_id nuevo y observed_at = ahora (UTC)."""
        return cls(source=source, provider=provider, observed_at=datetime.now(timezone.utc))
