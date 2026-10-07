"""Contadores de peticiones de Prediktia a un proveedor.

Cada reintento es un intento HTTP más que inicia Prediktia, así que se cuentan por separado las
llamadas lógicas (lo que pide el código) y los intentos (lo que sale por la red).
`attempts` = intentos HTTP iniciados por Prediktia. NO es el consumo de cuota confirmado por el
proveedor: un intento que falla por timeout o error de conexión puede no llegar a contarle; el
consumo real solo lo confirma el propio proveedor (p. ej. GET /status).
"""

from dataclasses import dataclass, replace


@dataclass
class RequestStats:
    calls: int = 0  # llamadas lógicas (una por cada _get)
    attempts: int = 0  # intentos HTTP iniciados por Prediktia (no es cuota confirmada por el proveedor)
    retries: int = 0  # intentos que fueron reintentos (attempts - calls cuando todo termina)
    failed_attempts: int = 0  # intentos que terminaron en error (con o sin respuesta HTTP)

    def snapshot(self) -> "RequestStats":
        return replace(self)

    def since(self, earlier: "RequestStats") -> "RequestStats":
        """Diferencia de contadores desde `earlier` (un snapshot anterior)."""
        return RequestStats(
            calls=self.calls - earlier.calls,
            attempts=self.attempts - earlier.attempts,
            retries=self.retries - earlier.retries,
            failed_attempts=self.failed_attempts - earlier.failed_attempts,
        )


def stats_of(provider: object) -> RequestStats | None:
    """Contadores del provider si los lleva (los providers falsos de los tests pueden no llevarlos)."""
    stats = getattr(provider, "request_stats", None)
    return stats if isinstance(stats, RequestStats) else None


def describe(stats: RequestStats | None) -> str:
    if stats is None:
        return "peticiones n/d"
    return f"peticiones {stats.attempts} (reintentos {stats.retries}, fallidas {stats.failed_attempts})"
