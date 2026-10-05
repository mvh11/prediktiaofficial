"""Límites de tiempo de la BD: configuración, engine y efecto real en PostgreSQL."""

import time

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db import database
from tests import conftest

URL = "postgresql+psycopg://u:p@db.example.invalid:5432/prediktia"


def _settings(**overrides) -> Settings:
    # _env_file=None: los tests no dependen del .env ni de las variables del entorno de desarrollo
    return Settings(_env_file=None, database_url=URL, **overrides)


def test_defaults_are_conservative():
    s = _settings()
    assert (s.db_connect_timeout_seconds, s.db_statement_timeout_ms) == (10, 60_000)


def test_timeouts_configurable_from_environment(monkeypatch):
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "1500")
    s = _settings()
    assert (s.db_connect_timeout_seconds, s.db_statement_timeout_ms) == (3, 1500)


@pytest.mark.parametrize("overrides", [{"db_connect_timeout_seconds": 0}, {"db_statement_timeout_ms": -1}])
def test_invalid_timeouts_rejected(overrides):
    with pytest.raises(ValidationError):
        _settings(**overrides)


def test_engine_passes_connect_timeout(monkeypatch):
    captured = {}
    real = database.create_engine

    def spy(url, **kwargs):
        captured.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr(database, "create_engine", spy)
    database.build_engine(_settings(db_connect_timeout_seconds=4))
    assert captured["connect_args"] == {"connect_timeout": 4}
    assert captured["pool_pre_ping"] is True


def test_statement_timeout_listener_only_when_enabled():
    with_limit = database.build_engine(_settings(db_statement_timeout_ms=1000))
    without_limit = database.build_engine(_settings(db_statement_timeout_ms=0))
    assert len(with_limit.dispatch.begin) == 1
    assert len(without_limit.dispatch.begin) == 0


def test_app_engine_url_unchanged_by_timeouts():
    # Los límites van en connect_args y en un evento, nunca en la URL: la guarda de tests
    # destructivos sigue comparando exactamente la URL configurada
    engine = database.build_engine(_settings())
    assert engine.url.render_as_string(hide_password=False) == URL


def test_unreachable_database_fails_within_connect_timeout():
    # 192.0.2.1 es TEST-NET-1 (RFC 5737): no enruta a ningún sitio, así que la conexión no
    # recibe respuesta y debe cortarse por connect_timeout, no quedarse colgada
    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@192.0.2.1:5432/x", db_connect_timeout_seconds=2)
    engine = database.build_engine(s)
    started = time.monotonic()
    with pytest.raises(OperationalError):
        with engine.connect():
            pass
    assert time.monotonic() - started < 15


# --- Con BD (desechable, autorizada por la guarda) --------------------------------------------


@pytest.mark.db
def test_statement_timeout_applied_in_each_transaction(migrated_db):
    engine = database.build_engine(
        Settings(_env_file=None, database_url=conftest.TEST_DB_URL, db_statement_timeout_ms=1500)
    )
    try:
        for _ in range(2):  # en cada transacción, no solo en la primera de la conexión
            with engine.begin() as conn:
                assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "1500ms"
    finally:
        engine.dispose()


@pytest.mark.db
def test_statement_timeout_cancels_long_query(migrated_db):
    engine = database.build_engine(
        Settings(_env_file=None, database_url=conftest.TEST_DB_URL, db_statement_timeout_ms=300)
    )
    try:
        started = time.monotonic()
        with pytest.raises(OperationalError, match="statement timeout"):
            with engine.begin() as conn:
                conn.execute(text("SELECT pg_sleep(5)"))
        assert time.monotonic() - started < 4
    finally:
        engine.dispose()
