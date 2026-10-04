"""Configuración común de los tests.

Seguridad de la BD (fail-closed: ante cualquier duda los tests `db` no se ejecutan):
- Los tests `db` (y cualquier test que use las fixtures de BD) hacen `alembic downgrade base`
  y TRUNCATE, así que solo se ejecutan si se cumplen las tres condiciones:
  1. TEST_DATABASE_URL existe explícitamente (DATABASE_URL nunca se usa como sustituto).
  2. No apunta al mismo destino que la DATABASE_URL de desarrollo (la del entorno o la del
     .env). El destino se normaliza: en Neon el host directo y el `-pooler` del mismo
     endpoint son la misma BD, y localhost/127.0.0.1/::1 son el mismo servidor. El puerto
     no se tiene en cuenta (un pooler local en otro puerto también es la misma BD).
  3. TEST_DATABASE_ALLOW_DESTRUCTIVE contiene exactamente el destino normalizado
     "<host>/<bd>" de TEST_DATABASE_URL (el motivo del skip indica el valor esperado).
- Si no es así, se saltan y además DATABASE_URL se sustituye por una URL inalcanzable,
  de modo que ningún test pueda conectarse por accidente a la BD de desarrollo/producción.
- Justo antes de cada operación destructiva se vuelve a comprobar que el engine de la app y
  la configuración que usa Alembic apuntan al destino autorizado.

Esto se decide aquí, antes de importar `app`, porque el engine se crea al importarlo.
"""

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).parent / "data"
UNREACHABLE_DB_URL = "postgresql+psycopg://tests:tests@127.0.0.1:9/tests_sin_bd"
ALLOW_DESTRUCTIVE_ENV = "TEST_DATABASE_ALLOW_DESTRUCTIVE"
# Fixtures que tocan la BD de pruebas: un test que las use se salta aunque no esté marcado `db`
DB_FIXTURES = {"migrated_db", "db_session"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def db_target(url: str | URL) -> tuple[str, str]:
    """Destino normalizado (host, BD) de una URL de PostgreSQL.

    Lanza ValueError si el destino no se puede determinar con certeza.
    """
    try:
        u = make_url(url)
    except Exception as exc:
        raise ValueError("URL no interpretable") from exc
    if not u.drivername.startswith("postgresql"):
        raise ValueError(f"No es PostgreSQL: {u.drivername}")
    # libpq permite fijar el host (o el endpoint de Neon) en la query: el host visible no sería el real
    if {"host", "hostaddr", "service"} & set(u.query) or "endpoint" in str(u.query.get("options", "")):
        raise ValueError("El destino se define en los parámetros de la URL")
    host = (u.host or "").strip().lower().rstrip(".")
    if not host or not u.database:
        raise ValueError("Falta host o nombre de BD")
    if host in _LOCAL_HOSTS:
        host = "localhost"
    # Neon: ep-xxx-pooler.region.aws.neon.tech es el mismo endpoint que ep-xxx.region.aws.neon.tech
    first, dot, rest = host.partition(".")
    if first.endswith("-pooler"):
        host = first.removesuffix("-pooler") + dot + rest
    return host, u.database


def resolve_test_db(environ: Mapping[str, str], dotenv_database_url: str | None) -> tuple[str | None, str]:
    """Devuelve (TEST_DATABASE_URL, "") si es seguro usarla, o (None, motivo) si no."""
    test_url = (environ.get("TEST_DATABASE_URL") or "").strip()
    if not test_url:
        return None, "Falta TEST_DATABASE_URL"
    try:
        target = db_target(test_url)
    except ValueError as exc:
        return None, f"TEST_DATABASE_URL no identifica un destino seguro: {exc}"

    for name, dev_url in (("DATABASE_URL del entorno", environ.get("DATABASE_URL")), (".env", dotenv_database_url)):
        if not (dev_url or "").strip():
            continue
        try:
            dev_target = db_target(dev_url)
        except ValueError:
            return None, f"No se puede interpretar la {name}: no se puede descartar que sea la misma BD"
        if dev_target == target:
            return None, f"TEST_DATABASE_URL apunta a la misma BD que la {name}"

    expected = "/".join(target)
    if (environ.get(ALLOW_DESTRUCTIVE_ENV) or "").strip() != expected:
        return None, f"Falta la autorización explícita {ALLOW_DESTRUCTIVE_ENV}={expected}"
    return test_url, ""


TEST_DB_URL, TEST_DB_SKIP_REASON = resolve_test_db(
    os.environ, dotenv_values(BACKEND_DIR / ".env").get("DATABASE_URL")
)
os.environ["DATABASE_URL"] = TEST_DB_URL or UNREACHABLE_DB_URL


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DB_URL:
        return
    skip = pytest.mark.skip(reason=f"BD de pruebas no autorizada: {TEST_DB_SKIP_REASON}")
    for item in items:
        if "db" in item.keywords or DB_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(skip)


def assert_destructive_db_allowed() -> None:
    """Última comprobación antes de downgrade/TRUNCATE: la app y Alembic apuntan al destino autorizado."""
    if not TEST_DB_URL:
        raise RuntimeError(f"Operación destructiva bloqueada: {TEST_DB_SKIP_REASON}")
    from app.core.config import get_settings
    from app.db.database import engine

    allowed = db_target(TEST_DB_URL)
    for name, url in (("engine", engine.url), ("settings", get_settings().database_url)):
        if db_target(url) != allowed:
            raise RuntimeError(f"Operación destructiva bloqueada: el {name} no apunta a la BD de pruebas autorizada")


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

    assert_destructive_db_allowed()
    getattr(alembic_command, command)(_alembic_config(), revision)


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[None]:
    """BD de pruebas vacía y en la última versión del esquema."""
    assert_destructive_db_allowed()
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
