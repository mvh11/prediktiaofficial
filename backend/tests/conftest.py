"""Configuración común de los tests.

Seguridad de la BD (fail-closed):
- Los tests marcados `db` son DESTRUCTIVOS (`alembic downgrade base`, TRUNCATE ... CASCADE).
  Solo se ejecutan si se cumplen TODAS estas condiciones:
  1. TEST_DATABASE_URL existe en el entorno (nunca se usa DATABASE_URL como sustituto);
  2. su destino normalizado (host:puerto/bd) se puede determinar y no coincide con el de la
     DATABASE_URL de desarrollo (la del entorno y la del .env). Neon expone el mismo endpoint
     directo (ep-xxx.region.aws.neon.tech) y con pooler (ep-xxx-pooler.region...): cuentan
     como el mismo host. Fuera de localhost, compartir host ya basta para bloquear;
  3. PREDIKTIA_DESTRUCTIVE_TEST_DB contiene exactamente ese destino normalizado (el motivo
     del skip indica el valor esperado). La autorización vale solo para esa BD.
- Si algo falta o no se puede interpretar, los tests `db` se saltan y DATABASE_URL se sustituye
  por una URL inalcanzable, de modo que ningún test pueda conectarse por accidente a la BD de
  desarrollo/producción. Además, `migrated_db` y `alembic_run` vuelven a comprobar el destino
  justo antes de cada operación destructiva.

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
DESTRUCTIVE_AUTH_VAR = "PREDIKTIA_DESTRUCTIVE_TEST_DB"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def db_target(url: str | None) -> str | None:
    """Destino normalizado `host:puerto/bd` de una URL, o None si no se puede determinar.

    La normalización solo junta destinos, nunca los separa: se quita el sufijo `-pooler` del
    primer segmento del host (Neon) y las direcciones de loopback cuentan como `localhost`.
    """
    try:
        parsed = make_url((url or "").strip())
    except Exception:
        return None
    host = (parsed.host or "").strip().lower().rstrip(".")
    if not host or not parsed.database:
        return None  # p. ej. socket unix o host en la query: no se puede comparar
    first, dot, rest = host.partition(".")
    host = first.removesuffix("-pooler") + dot + rest
    if host in _LOOPBACK_HOSTS:
        host = "localhost"
    return f"{host}:{parsed.port or 5432}/{parsed.database}"


def _host_port(target: str) -> str:
    return target.split("/", 1)[0]


def resolve_destructive_test_db(
    test_url: str | None, dev_urls: list[str | None], authorization: str | None
) -> tuple[str | None, str]:
    """Devuelve (url, motivo). La url es None si los tests destructivos NO deben ejecutarse."""
    test_url = (test_url or "").strip()
    if not test_url:
        return None, "falta TEST_DATABASE_URL"
    target = db_target(test_url)
    if target is None:
        return None, "TEST_DATABASE_URL no tiene un host y una BD identificables"
    for dev_url in dev_urls:
        if not (dev_url or "").strip():
            continue
        dev_target = db_target(dev_url)
        if dev_target is None:
            return None, "no se puede interpretar DATABASE_URL: no se descarta que sea el mismo destino"
        if dev_target == target:
            return None, "TEST_DATABASE_URL apunta al mismo destino que DATABASE_URL"
        if not target.startswith("localhost:") and _host_port(dev_target) == _host_port(target):
            return None, "TEST_DATABASE_URL comparte servidor/endpoint con DATABASE_URL"
    if (authorization or "").strip() != target:
        return None, f"falta autorización explícita: {DESTRUCTIVE_AUTH_VAR} debe ser exactamente '{target}'"
    return test_url, "ok"


_DEV_URLS = [os.environ.get("DATABASE_URL"), dotenv_values(BACKEND_DIR / ".env").get("DATABASE_URL")]
TEST_DB_URL, TEST_DB_REASON = resolve_destructive_test_db(
    os.environ.get("TEST_DATABASE_URL"), _DEV_URLS, os.environ.get(DESTRUCTIVE_AUTH_VAR)
)
os.environ["DATABASE_URL"] = TEST_DB_URL or UNREACHABLE_DB_URL


def require_destructive_test_db() -> None:
    """Última comprobación antes de una operación destructiva: la app debe apuntar exactamente
    a la TEST_DATABASE_URL autorizada. En cualquier otro caso lanza RuntimeError."""
    if TEST_DB_URL is None:
        raise RuntimeError(f"Operación destructiva bloqueada: {TEST_DB_REASON}")
    from app.core.config import get_settings
    from app.db.database import engine

    expected = db_target(TEST_DB_URL)
    for url in (get_settings().database_url, engine.url.render_as_string(hide_password=False)):
        if db_target(url) != expected:
            raise RuntimeError("Operación destructiva bloqueada: la app no apunta a la BD de tests autorizada")
    for dev_url in _DEV_URLS:
        if dev_url and db_target(dev_url) == expected:
            raise RuntimeError("Operación destructiva bloqueada: el destino coincide con DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DB_URL:
        return
    skip = pytest.mark.skip(reason=f"Tests de BD NO ejecutados: {TEST_DB_REASON}")
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

    require_destructive_test_db()
    getattr(alembic_command, command)(_alembic_config(), revision)


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[None]:
    """BD de pruebas vacía y en la última versión del esquema."""
    require_destructive_test_db()
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
