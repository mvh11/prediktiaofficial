"""Configuración común de los tests.

Seguridad de la BD:
- Los tests marcados `db` solo se ejecutan si TEST_DATABASE_URL existe y apunta a una BD
  distinta de la DATABASE_URL de desarrollo (la del .env o la del entorno).
- Si no es así, se saltan y además DATABASE_URL se sustituye por una URL inalcanzable,
  de modo que ningún test pueda conectarse por accidente a la BD de desarrollo/producción.

Esto se decide aquí, antes de importar `app`, porque el engine se crea al importarlo.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).parent / "data"
UNREACHABLE_DB_URL = "postgresql+psycopg://tests:tests@127.0.0.1:9/tests_sin_bd"


def _same_database(a: str, b: str) -> bool:
    try:
        ua, ub = make_url(a), make_url(b)
    except Exception:
        return a.strip() == b.strip()
    return (ua.host, ua.port or 5432, ua.database) == (ub.host, ub.port or 5432, ub.database)


def _resolve_test_db_url() -> str | None:
    test_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not test_url:
        return None
    dev_urls = [
        os.environ.get("DATABASE_URL", ""),
        dotenv_values(BACKEND_DIR / ".env").get("DATABASE_URL") or "",
    ]
    if any(u and _same_database(test_url, u) for u in dev_urls):
        return None  # misma BD que desarrollo: nunca se usa
    return test_url


TEST_DB_URL = _resolve_test_db_url()
os.environ["DATABASE_URL"] = TEST_DB_URL or UNREACHABLE_DB_URL


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DB_URL:
        return
    skip = pytest.mark.skip(reason="Sin TEST_DATABASE_URL segura y separada de la BD de desarrollo")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def load_json(relative: str) -> dict:
    return json.loads((DATA_DIR / relative).read_text(encoding="utf-8"))


# --- Fixtures de BD (solo se usan en tests `db`) ------------------------------------------


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def alembic_run(command: str, revision: str) -> None:
    from alembic import command as alembic_command

    getattr(alembic_command, command)(_alembic_config(), revision)


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[None]:
    """BD de pruebas vacía y en la última versión del esquema."""
    alembic_run("downgrade", "base")
    alembic_run("upgrade", "head")
    yield


@pytest.fixture
def db_session(migrated_db):
    """Sesión dentro de una transacción que se deshace al terminar el test.

    Los commit() del código se convierten en savepoints, así que nada persiste.
    """
    from sqlalchemy.orm import Session

    from app.db.database import engine

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def query_counter():
    """Cuenta las sentencias SQL ejecutadas mientras está activo."""
    from sqlalchemy import event

    from app.db.database import engine

    counter = {"n": 0}

    def _count(*_args, **_kwargs):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _count)


# --- Datos de ejemplo para tests `db` -----------------------------------------------------


def make_competition(db, external_id: int = 265, name: str = "Liga Test", current_year: int | None = 2026):
    """Crea una competición (con su mapeo de API-Football) y, si se indica, su temporada actual."""
    from app.repositories import catalog_repository
    from app.schemas.catalog import CompetitionData, SeasonData

    seasons = [SeasonData(year=current_year, is_current=True)] if current_year else []
    data = CompetitionData(external_id=external_id, name=name, seasons=seasons)
    competition_id = catalog_repository.upsert_competition(db, data, "api-football")
    season_ids = catalog_repository.upsert_seasons(db, competition_id, data.seasons)
    db.flush()
    return competition_id, season_ids.get(current_year)


def make_fixture_data(external_id: int, home: int = 1, away: int = 2, status: str = "NS", **kwargs):
    """FixtureData mínimo; kwargs permite fijar goles, fulltime, kickoff, etc."""
    from datetime import datetime, timezone

    from app.schemas.catalog import TeamData
    from app.schemas.fixture import FixtureData

    values = {
        "external_id": external_id,
        "competition_external_id": 265,
        "season": 2026,
        "kickoff_at": datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc),
        "status_short": status,
        "home_team": TeamData(external_id=home, name=f"Equipo {home}"),
        "away_team": TeamData(external_id=away, name=f"Equipo {away}"),
    }
    values.update(kwargs)
    return FixtureData(**values)
