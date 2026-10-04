"""Configuración común de los tests.

Seguridad de la BD (fail-closed: ante cualquier duda los tests `db` no se ejecutan):
- Los tests `db` (y cualquier test que use las fixtures de BD) hacen `alembic downgrade base`
  y TRUNCATE, así que solo se ejecutan si se cumplen las tres condiciones:
  1. TEST_DATABASE_URL existe explícitamente (DATABASE_URL nunca se usa como sustituto).
  2. No apunta al mismo destino que la DATABASE_URL de desarrollo (la del entorno o la del
     .env). Esta comparación es conservadora (db_target): en Neon el host directo y el
     `-pooler` del mismo endpoint son la misma BD, localhost/127.0.0.1/::1 son el mismo
     servidor y el puerto no se tiene en cuenta (un pooler local en otro puerto también
     es la misma BD).
  3. TEST_DATABASE_ALLOW_DESTRUCTIVE contiene exactamente el destino estricto
     "<host>:<puerto>/<bd>" de TEST_DATABASE_URL (authorization_target; el motivo del skip
     indica el valor esperado). Aquí no se juntan alias: cada host literal y cada puerto
     (5432 si falta) es un destino distinto, así que autorizar un cluster no autoriza otro
     del mismo host con la misma BD.
- El destino tiene que ser inequívoco: una URL cuyo destino real pueda no ser el host y la
  BD visibles se rechaza. Solo se admiten los parámetros de query de _SAFE_QUERY_PARAMS
  (cualquier otro, como dbname, host, hostaddr, port, service u options, se rechaza), un
  único host TCP (sin listas de hosts ni sockets) y ninguna variable PG* del entorno que
  libpq pueda usar para redirigir la conexión (_LIBPQ_REDIRECT_ENV).
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
# Únicos parámetros de query admitidos: ninguno cambia a qué servidor ni a qué BD se conecta.
# Cualquier otro (dbname, database, host, hostaddr, port, service, servicefile, options,
# target_session_attrs...) se rechaza, porque libpq/psycopg lo aplicaría por encima del
# host y la BD visibles en la URL.
_SAFE_QUERY_PARAMS = {"sslmode", "sslrootcert", "channel_binding", "connect_timeout", "application_name"}
# Variables de entorno de libpq que pueden redirigir la conexión aunque la URL tenga host y BD
# (hostaddr se usa en lugar de resolver el host; service y options aportan esos mismos valores;
# PGPORT cambia el puerto de una URL sin puerto, que así siempre significa 5432)
_LIBPQ_REDIRECT_ENV = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPORT")
_DEFAULT_PG_PORT = 5432


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
    unsafe = sorted(set(u.query) - _SAFE_QUERY_PARAMS)
    if unsafe:
        raise ValueError(f"Parámetros de la URL que pueden cambiar el destino: {', '.join(unsafe)}")
    host = (u.host or "").strip().lower().rstrip(".")
    if not host or not u.database:
        raise ValueError("Falta host o nombre de BD")
    # Varios hosts ("a,b"), un socket ("/ruta", también codificado como %2F) o espacios:
    # el servidor real no sería uno solo y conocido
    if any(c in host for c in ",/% \t"):
        raise ValueError("El host no es un único servidor TCP")
    if host in _LOCAL_HOSTS:
        host = "localhost"
    return _without_neon_pooler(host), u.database


def _without_neon_pooler(host: str) -> str:
    """Neon: ep-xxx-pooler.region.aws.neon.tech es el mismo endpoint que ep-xxx.region.aws.neon.tech."""
    first, dot, rest = host.partition(".")
    if first.endswith("-pooler"):
        return first.removesuffix("-pooler") + dot + rest
    return host


def authorization_target(url: str | URL) -> str:
    """Destino exacto "<host>:<puerto>/<bd>" que TEST_DATABASE_ALLOW_DESTRUCTIVE debe nombrar.

    A diferencia de db_target, incluye el puerto (5432 si falta) y no junta localhost,
    127.0.0.1 y ::1. Hace las mismas validaciones: lanza ValueError si el destino es ambiguo.
    """
    db_target(url)
    u = make_url(url)
    host = _without_neon_pooler(u.host.strip().lower().rstrip(".").strip("[]"))
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"{host}:{u.port or _DEFAULT_PG_PORT}/{u.database}"


def libpq_redirect_env(environ: Mapping[str, str]) -> list[str]:
    """Variables PG* definidas que podrían llevar la conexión a otro destino."""
    return [name for name in _LIBPQ_REDIRECT_ENV if (environ.get(name) or "").strip()]


def resolve_test_db(environ: Mapping[str, str], dotenv_database_url: str | None) -> tuple[str | None, str]:
    """Devuelve (TEST_DATABASE_URL, "") si es seguro usarla, o (None, motivo) si no."""
    test_url = (environ.get("TEST_DATABASE_URL") or "").strip()
    if not test_url:
        return None, "Falta TEST_DATABASE_URL"
    redirect_env = libpq_redirect_env(environ)
    if redirect_env:
        return None, f"Variables de entorno de libpq que pueden cambiar el destino: {', '.join(redirect_env)}"
    try:
        target = db_target(test_url)
        expected = authorization_target(test_url)
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
    redirect_env = libpq_redirect_env(os.environ)
    if redirect_env:
        raise RuntimeError(f"Operación destructiva bloqueada: variables de libpq definidas ({', '.join(redirect_env)})")
    from app.core.config import get_settings
    from app.db.database import engine

    try:
        allowed = authorization_target(TEST_DB_URL)
        targets = {name: authorization_target(url) for name, url in (("engine", engine.url), ("settings", get_settings().database_url))}
    except ValueError as exc:
        raise RuntimeError(f"Operación destructiva bloqueada: destino ambiguo ({exc})") from exc
    for name, target in targets.items():
        if target != allowed:
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
