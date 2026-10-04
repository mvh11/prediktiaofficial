"""Guarda de la BD de tests destructivos (sin BD: solo lógica de comparación y bloqueo)."""

import pytest

from tests import conftest as test_conftest
from tests.conftest import db_target, resolve_destructive_test_db

# URLs ficticias: ninguna apunta a una BD real
NEON_DIRECT = "postgresql+psycopg://user:x@ep-cool-river-123456.us-east-2.aws.neon.tech/neondb?sslmode=require"
NEON_POOLER = "postgresql+psycopg://user:x@ep-cool-river-123456-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
NEON_TARGET = "ep-cool-river-123456.us-east-2.aws.neon.tech:5432/neondb"
NEON_OTHER_BRANCH = "postgresql+psycopg://user:x@ep-other-branch-999-pooler.us-east-2.aws.neon.tech/neondb"
NEON_OTHER_TARGET = "ep-other-branch-999.us-east-2.aws.neon.tech:5432/neondb"
LOCAL_TEST = "postgresql+psycopg://postgres:x@localhost:5432/prediktia_tests"
LOCAL_TARGET = "localhost:5432/prediktia_tests"


def test_neon_pooler_and_direct_normalize_to_same_target():
    assert db_target(NEON_DIRECT) == db_target(NEON_POOLER) == NEON_TARGET


def test_same_database_url_blocked():
    url, reason = resolve_destructive_test_db(NEON_DIRECT, [NEON_DIRECT], NEON_TARGET)
    assert url is None and "mismo destino" in reason


@pytest.mark.parametrize(("test_url", "dev_url"), [(NEON_DIRECT, NEON_POOLER), (NEON_POOLER, NEON_DIRECT)])
def test_same_neon_endpoint_pooler_vs_direct_blocked(test_url, dev_url):
    url, reason = resolve_destructive_test_db(test_url, [dev_url], NEON_TARGET)
    assert url is None and "mismo destino" in reason


def test_same_server_different_database_blocked():
    other_db = NEON_POOLER.replace("/neondb", "/otra_bd")
    url, reason = resolve_destructive_test_db(other_db, [NEON_DIRECT], db_target(other_db))
    assert url is None and "comparte servidor" in reason


def test_loopback_aliases_are_same_target():
    url, reason = resolve_destructive_test_db(
        "postgresql+psycopg://a:x@127.0.0.1/prediktia_tests", [LOCAL_TEST], LOCAL_TARGET
    )
    assert url is None and "mismo destino" in reason


@pytest.mark.parametrize("authorization", [None, "", "1", "true", NEON_TARGET, "localhost:5432/otra_bd"])
def test_different_db_without_explicit_authorization_blocked(authorization):
    url, reason = resolve_destructive_test_db(NEON_OTHER_BRANCH, [NEON_POOLER], authorization)
    assert url is None and "autorización" in reason


def test_explicitly_authorized_target_allowed():
    url, reason = resolve_destructive_test_db(NEON_OTHER_BRANCH, [NEON_POOLER, None], NEON_OTHER_TARGET)
    assert (url, reason) == (NEON_OTHER_BRANCH, "ok")


def test_missing_test_url_never_falls_back_to_database_url():
    url, reason = resolve_destructive_test_db(None, [NEON_DIRECT], NEON_TARGET)
    assert url is None and "TEST_DATABASE_URL" in reason


@pytest.mark.parametrize("test_url", ["esto no es una url", "postgresql+psycopg:///solo_bd", "postgresql+psycopg://a:x@host/"])
def test_invalid_test_target_blocked(test_url):
    url, _ = resolve_destructive_test_db(test_url, [NEON_DIRECT], "host:5432/x")
    assert url is None


@pytest.mark.parametrize("dev_url", ["esto no es una url", "postgresql+psycopg:///solo_bd"])
def test_uninterpretable_database_url_blocks(dev_url):
    url, _ = resolve_destructive_test_db(NEON_OTHER_BRANCH, [dev_url], NEON_OTHER_TARGET)
    assert url is None


def test_destructive_operation_blocked_without_authorized_db(monkeypatch):
    from alembic import command as alembic_command

    calls = []
    monkeypatch.setattr(alembic_command, "downgrade", lambda *args: calls.append(args))
    monkeypatch.setattr(test_conftest, "TEST_DB_URL", None)
    with pytest.raises(RuntimeError, match="bloqueada"):
        test_conftest.alembic_run("downgrade", "base")
    assert calls == []


def test_destructive_operation_blocked_if_app_points_elsewhere(monkeypatch):
    from alembic import command as alembic_command

    calls = []
    monkeypatch.setattr(alembic_command, "downgrade", lambda *args: calls.append(args))
    # autorizada en apariencia, pero la app (settings/engine) no apunta a esa BD
    monkeypatch.setattr(test_conftest, "TEST_DB_URL", NEON_OTHER_BRANCH)
    with pytest.raises(RuntimeError, match="bloqueada"):
        test_conftest.alembic_run("downgrade", "base")
    assert calls == []
