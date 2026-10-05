"""Qué hace una sync con cada fallo, dentro del run actual (sin estado entre runs).

Decisiones (los reintentos HTTP del adapter ya han terminado cuando se decide):
- FAIL_COMPETITION: se anota el error en esa competición y se sigue con la siguiente.
- ABORT_PROVIDER_RUN: se anota y ya no se llama al proveedor en lo que queda del run; el
  resto de competiciones quedan con el motivo.
Lo que no se clasifica aquí se propaga: errores de código o de contrato (TypeError,
KeyError, ProgrammingError...) y los errores de BD fuera del bloque que guarda una competición.
"""

from enum import StrEnum

from sqlalchemy.exc import DataError, DBAPIError, IntegrityError, OperationalError

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
)


class SyncAction(StrEnum):
    FAIL_COMPETITION = "fail_competition"
    ABORT_PROVIDER_RUN = "abort_provider_run"


class DbFailure(StrEnum):
    CONNECTION_LOST = "conexión perdida"
    QUERY_CANCELED = "consulta cancelada (SQLSTATE 57014)"
    OTHER_OPERATIONAL = "error operacional"
    CONSTRAINT = "datos rechazados por la BD"


# SQLSTATE de PostgreSQL: 57014 query_canceled (statement_timeout o una cancelación como
# pg_cancel_backend: el código no distingue la causa); clase 08
# (connection_exception) y 57P01/57P02/57P03 (servidor apagándose o sin aceptar conexiones)
_QUERY_CANCELED = "57014"
_CONNECTION_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})


def provider_failure_action(exc: ProviderError) -> SyncAction:
    """Credenciales rechazadas, falta de configuración, cuota diaria agotada o límite de peticiones
    que persiste tras los reintentos afectan a todas las competiciones: se corta el run. El resto
    (timeouts, conexión, 5xx tras agotar reintentos, 4xx, payload inválido) puede ser de una sola
    competición: se sigue con la siguiente."""
    if isinstance(exc, (ProviderAuthError, ProviderNotConfiguredError, ProviderRateLimitError)):
        return SyncAction.ABORT_PROVIDER_RUN
    return SyncAction.FAIL_COMPETITION


def provider_abort_reason(exc: ProviderError) -> str:
    """Motivo que se anota en las competiciones que ya no se piden (tras provider_failure_action)."""
    if isinstance(exc, ProviderAuthError):
        return f"se detuvo la sync porque el proveedor rechazó las credenciales ({exc.message})"
    if isinstance(exc, ProviderNotConfiguredError):
        return f"se detuvo la sync porque el proveedor no está configurado ({exc.message})"
    return f"se detuvo la sync por el límite del proveedor ({exc.message})"


def classify_db_error(exc: DBAPIError) -> DbFailure:
    """Tipo de un error de BD a partir de señales estructuradas, sin interpretar mensajes.

    - connection_invalidated: SQLAlchemy lo marca cuando el driver da la conexión por rota
      (psycopg: connection.closed / connection.broken).
    - SQLSTATE del error de psycopg (exc.orig.sqlstate): clase 08 y 57P01/57P02/57P03.
    Un OperationalError sin SQLSTATE y sin invalidar la conexión queda como OTHER_OPERATIONAL:
    no hay señal estructurada fiable de que sea una conexión perdida, y no se corta un run por
    una clasificación incierta.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if exc.connection_invalidated:
        return DbFailure.CONNECTION_LOST
    if isinstance(exc, OperationalError):
        if sqlstate == _QUERY_CANCELED:
            return DbFailure.QUERY_CANCELED
        if sqlstate is not None and (sqlstate.startswith("08") or sqlstate in _CONNECTION_SQLSTATES):
            return DbFailure.CONNECTION_LOST
        return DbFailure.OTHER_OPERATIONAL
    if isinstance(exc, (IntegrityError, DataError)):
        return DbFailure.CONSTRAINT
    raise TypeError(f"Error de BD no clasificable para aislar: {exc.__class__.__name__}") from exc


def db_failure_action(kind: DbFailure) -> SyncAction:
    """Sin conexión a la BD lo que se pida al proveedor no se podría guardar: se corta el run.
    Una consulta cancelada, un OperationalError de otro tipo o datos rechazados son de esa
    competición (su volumen o sus datos): se sigue con la siguiente."""
    if kind is DbFailure.CONNECTION_LOST:
        return SyncAction.ABORT_PROVIDER_RUN
    return SyncAction.FAIL_COMPETITION
