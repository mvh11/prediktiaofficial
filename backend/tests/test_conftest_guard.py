"""Guarda de la BD de tests destructivos (sin BD: solo lógica de comparación y bloqueo).

Contrato: tests/conftest.py (resolve_test_db, db_target, authorization_target y la autorización
TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>:<puerto>/<bd>). Los casos de URLs ambiguas, parámetros
de query y variables de libpq están en test_api_football_adapter.py.
"""

import pytest

from tests import conftest as test_conftest
from tests.conftest import ALLOW_DESTRUCTIVE_ENV, authorization_target, db_target, resolve_test_db

# URLs ficticias: ninguna apunta a una BD real
NEON_DIRECT = "postgresql+psycopg://user:x@ep-cool-river-123456.us-east-2.aws.neon.tech/neondb?sslmode=require"
NEON_POOLER = "postgresql+psycopg://user:x@ep-cool-river-123456-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
NEON_TARGET = "ep-cool-river-123456.us-east-2.aws.neon.tech:5432/neondb"
NEON_OTHER_BRANCH = "postgresql+psycopg://user:x@ep-other-branch-999-pooler.us-east-2.aws.neon.tech/neondb"
NEON_OTHER_TARGET = "ep-other-branch-999.us-east-2.aws.neon.tech:5432/neondb"
LOCAL_DEV = "postgresql+psycopg://postgres:x@localhost:5432/prediktia"
LOCAL_TEST = "postgresql+psycopg://postgres:x@localhost:5432/prediktia_tests"
LOCAL_TARGET = "localhost:5432/prediktia_tests"


def _resolve(test_url, dev_urls=(), allow=None):
    """resolve_test_db con la primera URL de desarrollo en el entorno y la segunda como la del .env."""
    dev = list(dev_urls) + [None, None]
    env = {"TEST_DATABASE_URL": test_url, "DATABASE_URL": dev[0], ALLOW_DESTRUCTIVE_ENV: allow}
    return resolve_test_db({k: v for k, v in env.items() if v is not None}, dev[1])


def test_neon_pooler_and_direct_normalize_to_same_target():
    assert db_target(NEON_DIRECT) == db_target(NEON_POOLER)
    assert authorization_target(NEON_DIRECT) == authorization_target(NEON_POOLER) == NEON_TARGET


def test_same_database_url_blocked():
    url, reason = _resolve(NEON_DIRECT, [NEON_DIRECT], NEON_TARGET)
    assert url is None and "misma BD" in reason


@pytest.mark.parametrize(("test_url", "dev_url"), [(NEON_DIRECT, NEON_POOLER), (NEON_POOLER, NEON_DIRECT)])
def test_same_neon_endpoint_pooler_vs_direct_blocked(test_url, dev_url):
    url, reason = _resolve(test_url, [dev_url], NEON_TARGET)
    assert url is None and "misma BD" in reason


@pytest.mark.parametrize("dev_url", [NEON_DIRECT, NEON_POOLER], ids=["dev-directo", "dev-pooler"])
def test_same_remote_server_different_database_blocked(dev_url):
    # Mismo endpoint remoto (y puerto) que desarrollo, otra BD: se bloquea aunque esté autorizada
    other_db = NEON_POOLER.replace("/neondb", "/otra_bd")
    url, reason = _resolve(other_db, [dev_url], authorization_target(other_db))
    assert url is None and "comparte servidor remoto" in reason


def test_same_remote_server_detected_from_dotenv():
    other_db = NEON_DIRECT.replace("/neondb", "/otra_bd")
    url, reason = _resolve(other_db, [None, NEON_POOLER], authorization_target(other_db))
    assert url is None and "comparte servidor remoto" in reason and ".env" in reason


def test_same_remote_host_other_port_is_another_server():
    dev = "postgresql+psycopg://u:x@db.example.com:5432/prediktia"
    test = "postgresql+psycopg://u:x@db.example.com:6543/prediktia_tests"
    assert _resolve(test, [dev], "db.example.com:6543/prediktia_tests") == (test, "")


def test_local_server_different_database_allowed():
    # Una BD desechable en el mismo servidor local que desarrollo es lo habitual
    assert _resolve(LOCAL_TEST, [LOCAL_DEV], LOCAL_TARGET) == (LOCAL_TEST, "")


def test_loopback_aliases_are_same_database_as_dev():
    url, reason = _resolve("postgresql+psycopg://a:x@127.0.0.1/prediktia_tests", [LOCAL_TEST], "127.0.0.1:5432/prediktia_tests")
    assert url is None and "misma BD" in reason


@pytest.mark.parametrize("allow", [None, "", "1", "true", NEON_TARGET, "localhost:5432/otra_bd", NEON_OTHER_TARGET.split(":")[0] + "/neondb"])
def test_different_db_without_exact_authorization_blocked(allow):
    url, reason = _resolve(NEON_OTHER_BRANCH, [NEON_POOLER], allow)
    assert url is None and f"{ALLOW_DESTRUCTIVE_ENV}={NEON_OTHER_TARGET}" in reason


def test_explicitly_authorized_target_allowed():
    assert _resolve(NEON_OTHER_BRANCH, [NEON_POOLER], NEON_OTHER_TARGET) == (NEON_OTHER_BRANCH, "")


def test_missing_test_url_never_falls_back_to_database_url():
    url, reason = _resolve(None, [NEON_DIRECT], NEON_TARGET)
    assert url is None and "TEST_DATABASE_URL" in reason


@pytest.mark.parametrize("test_url", ["esto no es una url", "postgresql+psycopg:///solo_bd", "postgresql+psycopg://a:x@host/"])
def test_invalid_test_target_blocked(test_url):
    url, _ = _resolve(test_url, [NEON_DIRECT], "host:5432/x")
    assert url is None


@pytest.mark.parametrize("dev_url", ["esto no es una url", "postgresql+psycopg:///solo_bd"])
def test_uninterpretable_database_url_blocks(dev_url):
    url, reason = _resolve(NEON_OTHER_BRANCH, [dev_url], NEON_OTHER_TARGET)
    assert url is None and "interpretar" in reason


@pytest.fixture
def alembic_calls(monkeypatch) -> list:
    from alembic import command as alembic_command

    calls: list = []
    for name in ("downgrade", "upgrade"):
        monkeypatch.setattr(alembic_command, name, lambda *args, _n=name: calls.append(_n))
    return calls


def test_destructive_operation_blocked_without_authorized_db(monkeypatch, alembic_calls):
    monkeypatch.setattr(test_conftest, "TEST_DB_URL", None)
    with pytest.raises(RuntimeError, match="bloqueada"):
        test_conftest.alembic_run("downgrade", "base")
    assert alembic_calls == []


def test_destructive_operation_blocked_if_app_points_elsewhere(monkeypatch, alembic_calls):
    # autorizada en apariencia, pero la app (settings/engine) no apunta a esa BD
    monkeypatch.setattr(test_conftest, "TEST_DB_URL", NEON_OTHER_BRANCH)
    with pytest.raises(RuntimeError, match="bloqueada"):
        test_conftest.alembic_run("downgrade", "base")
    assert alembic_calls == []


# --- Alembic dentro de pytest no reconfigura el logging (único test de este archivo con BD) ----


@pytest.mark.db
def test_alembic_run_keeps_app_loggers_and_pytest_logging(migrated_db):
    import logging

    import app.integrations.http  # noqa: F401  (el logger tiene que existir antes de migrar)

    app_logger = logging.getLogger("app.integrations.http")
    root = logging.getLogger()
    root_level, root_handlers = root.level, list(root.handlers)

    test_conftest.alembic_run("upgrade", "head")  # ejecuta alembic/env.py aunque no haya nada que migrar

    assert app_logger.disabled is False
    assert root.level == root_level and root.handlers == root_handlers  # handlers de caplog intactos
