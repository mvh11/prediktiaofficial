"""Clasificación de fallos de una sync (sync_failures): decisiones sobre el proveedor y tipos de error de BD.

Los errores de BD se construyen con las clases reales de SQLAlchemy y psycopg (SQLSTATE
incluido) y, con BD, se provocan de verdad contra PostgreSQL.
"""

import psycopg
import psycopg.errors
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from app.core.config import Settings
from app.db import database
from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderNotConfiguredError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.services.sync_failures import (
    DbFailure,
    SyncAction,
    classify_db_error,
    db_failure_action,
    provider_failure_action,
)
from tests import conftest

P = "api-football"

# --- Proveedor: cortar el run o fallar solo esa competición -----------------------------------

ABORT = {
    "auth": ProviderAuthError(P, "API key inválida o sin permisos", 401),
    "not_configured": ProviderNotConfiguredError(P, "Falta API_FOOTBALL_KEY"),
    "quota": ProviderQuotaExceededError(P, "Cuota diaria de peticiones agotada"),
    "rate_limit": ProviderRateLimitError(P, "Límite de peticiones superado", 429),
}
FAIL_COMPETITION = {
    "timeout": ProviderTimeoutError(P, "Timeout tras 1s"),
    "connection": ProviderConnectionError(P, "Error de conexión: ConnectError"),
    "503": ProviderResponseError(P, "Respuesta HTTP 503", 503),
    "404": ProviderResponseError(P, "Respuesta HTTP 404", 404),
    "invalid_payload": ProviderResponseError(P, "Respuesta inválida de /fixtures"),
}


@pytest.mark.parametrize("name", ABORT, ids=list(ABORT))
def test_provider_wide_failures_abort_the_run(name):
    assert provider_failure_action(ABORT[name]) is SyncAction.ABORT_PROVIDER_RUN


@pytest.mark.parametrize("name", FAIL_COMPETITION, ids=list(FAIL_COMPETITION))
def test_possibly_local_failures_only_fail_that_competition(name):
    # un 5xx, timeout o error de conexión que agota los reintentos no prueba una caída global
    assert provider_failure_action(FAIL_COMPETITION[name]) is SyncAction.FAIL_COMPETITION


# --- BD: tipo de error a partir de señales estructuradas ---------------------------------------


def _operational(orig, **kwargs) -> OperationalError:
    return OperationalError("SELECT 1", None, orig, **kwargs)


DB_CASES = {
    "query_canceled_57014": (_operational(psycopg.errors.QueryCanceled("canceling statement")), DbFailure.QUERY_CANCELED),
    "invalidated": (_operational(psycopg.OperationalError("x"), connection_invalidated=True), DbFailure.CONNECTION_LOST),
    "admin_shutdown_57P01": (_operational(psycopg.errors.AdminShutdown("terminating")), DbFailure.CONNECTION_LOST),
    "connection_failure_08006": (_operational(psycopg.errors.ConnectionFailure("x")), DbFailure.CONNECTION_LOST),
    # sin SQLSTATE ni conexión invalidada no hay señal fiable de conexión perdida: no se corta el run
    "no_sqlstate_not_invalidated": (_operational(psycopg.OperationalError("connection failed")), DbFailure.OTHER_OPERATIONAL),
    "deadlock_40P01": (_operational(psycopg.errors.DeadlockDetected("x")), DbFailure.OTHER_OPERATIONAL),
    "integrity": (IntegrityError("INSERT", None, psycopg.errors.CheckViolation("x")), DbFailure.CONSTRAINT),
    "data": (DataError("INSERT", None, psycopg.errors.NumericValueOutOfRange("x")), DbFailure.CONSTRAINT),
}


@pytest.mark.parametrize("name", DB_CASES, ids=list(DB_CASES))
def test_db_error_classification(name):
    exc, expected = DB_CASES[name]
    assert classify_db_error(exc) is expected


def test_only_a_lost_connection_aborts_the_run():
    assert db_failure_action(DbFailure.CONNECTION_LOST) is SyncAction.ABORT_PROVIDER_RUN
    for kind in (DbFailure.QUERY_CANCELED, DbFailure.OTHER_OPERATIONAL, DbFailure.CONSTRAINT):
        assert db_failure_action(kind) is SyncAction.FAIL_COMPETITION


def test_programming_error_is_not_classified_as_isolable():
    with pytest.raises(TypeError):
        classify_db_error(ProgrammingError("selec 1", None, psycopg.errors.SyntaxError("x")))


# --- Con BD: los errores reales de PostgreSQL se clasifican igual ------------------------------


@pytest.fixture
def short_timeout_engine(migrated_db):
    engine = database.build_engine(
        Settings(_env_file=None, database_url=conftest.TEST_DB_URL, db_statement_timeout_ms=200)
    )
    yield engine
    engine.dispose()


@pytest.mark.db
def test_real_terminated_connection_is_connection_lost(short_timeout_engine):
    with short_timeout_engine.connect() as victim:
        pid = victim.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with short_timeout_engine.connect() as killer:
            killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with pytest.raises(OperationalError) as exc:
            victim.execute(text("SELECT 1"))
    assert exc.value.connection_invalidated
    assert classify_db_error(exc.value) is DbFailure.CONNECTION_LOST


@pytest.mark.db
def test_real_statement_timeout_is_query_canceled_not_connection_lost(short_timeout_engine):
    with pytest.raises(OperationalError) as exc:
        with short_timeout_engine.begin() as conn:
            conn.execute(text("SELECT pg_sleep(3)"))
    assert not exc.value.connection_invalidated
    assert classify_db_error(exc.value) is DbFailure.QUERY_CANCELED
    assert db_failure_action(classify_db_error(exc.value)) is SyncAction.FAIL_COMPETITION


def test_query_canceled_label_does_not_claim_statement_timeout():
    # 57014 también es pg_cancel_backend: el clasificador no puede atribuir la causa
    assert "statement_timeout" not in str(DbFailure.QUERY_CANCELED)
    assert "57014" in str(DbFailure.QUERY_CANCELED)
