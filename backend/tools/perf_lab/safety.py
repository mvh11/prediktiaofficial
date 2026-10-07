"""Guarda independiente: solo una BD local, vacía al inicializar y marcada como laboratorio.

Sigue la autorización host:puerto/bd de tests/conftest.py, sin importarlo (tiene
efectos sobre el entorno de pytest). No acepta DATABASE_URL como entrada.
"""

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Engine, text
from sqlalchemy.engine import URL, make_url

MARKER = "PREDIKTIA_DI_A3B_DISPOSABLE_V1"


@dataclass(frozen=True)
class LabTarget:
    url: URL
    authorization: str


def authorize(environ: Mapping[str, str]) -> LabTarget:
    """Valida antes de importar la app o abrir siquiera una conexión."""
    raw = environ.get("PERF_LAB_DATABASE_URL", "")
    if not raw:
        raise ValueError("Falta PERF_LAB_DATABASE_URL; DATABASE_URL nunca es un fallback")
    # Fail-closed también ante variables libpq no conocidas por esta versión.
    if any(k.startswith("PG") and v for k, v in environ.items()):
        raise ValueError("El laboratorio requiere un entorno sin variables PG*")
    try:
        url = make_url(raw)
        port = url.port
    except Exception as exc:
        raise ValueError("URL del laboratorio no interpretable") from exc
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Se requiere postgresql+psycopg")
    if url.host not in {"127.0.0.1", "::1"}:
        raise ValueError("Solo se admiten IPs loopback literales: 127.0.0.1 o ::1")
    if not port or not 1 <= port <= 65535 or port == 5432:
        raise ValueError("Se requiere puerto explícito no estándar, distinto de 5432")
    if url.query:
        raise ValueError("No se admiten parámetros de query en la URL del laboratorio")
    if not re.fullmatch(r"prediktia_lab_[a-z0-9_]+", url.database or ""):
        raise ValueError("La BD debe llamarse prediktia_lab_<nombre>")
    if not url.username:
        raise ValueError("Se requiere un usuario explícito")
    host = f"[{url.host}]" if url.host == "::1" else url.host
    expected = f"{host}:{port}/{url.database}"
    if environ.get("PERF_LAB_ALLOW_DESTRUCTIVE") != expected:
        raise ValueError(f"Se requiere PERF_LAB_ALLOW_DESTRUCTIVE={expected}")
    return LabTarget(url, expected)


def load_app(target: LabTarget) -> Engine:
    """Usa el engine real, con configuración local fijada ANTES de importar modelos."""
    if "app.db.database" in sys.modules:
        raise RuntimeError("Ejecuta el laboratorio en un proceso Python nuevo")
    # Sobrescribe cualquier valor de .env; no se importa main, services ni adapters.
    os.environ.update(
        DATABASE_URL=target.url.render_as_string(hide_password=False),
        API_FOOTBALL_KEY="",
        FIVE_DOLLAR_FOOTBALL_API_KEY="",
        DB_CONNECT_TIMEOUT_SECONDS="3",
        DB_STATEMENT_TIMEOUT_MS="60000",
    )
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.db.database import engine

    assert_target(engine, target)
    return engine


def assert_target(engine: Engine, target: LabTarget) -> None:
    current = authorize(os.environ)
    if current != target or engine.url != target.url:
        raise RuntimeError("El destino o la autorización cambiaron")


def verify_server(engine: Engine, target: LabTarget) -> None:
    assert_target(engine, target)
    with engine.connect() as conn:
        db, host, port = conn.execute(
            text("SELECT current_database(), host(inet_server_addr()), inet_server_port()")
        ).one()
    if (db, host, port) != (target.url.database, target.url.host, target.url.port):
        raise RuntimeError("El servidor real no coincide con el destino local autorizado")


def read_metadata(engine: Engine, target: LabTarget) -> dict:
    verify_server(engine, target)
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = current_database()")
        ).scalar_one()
    try:
        metadata = json.loads(value or "{}")
    except ValueError as exc:
        raise RuntimeError("La BD no tiene una marca de laboratorio válida") from exc
    if not isinstance(metadata, dict) or metadata.get("marker") != MARKER:
        raise RuntimeError("BD no inicializada por este laboratorio; operación rechazada")
    return metadata


def write_metadata(engine: Engine, target: LabTarget, metadata: dict) -> None:
    assert_target(engine, target)
    # Nombre validado por regexp; quoting de SQLAlchemy y literal JSON escapado.
    name = engine.dialect.identifier_preparer.quote(target.url.database)
    value = json.dumps({**metadata, "marker": MARKER}, sort_keys=True).replace("'", "''")
    with engine.begin() as conn:
        conn.exec_driver_sql(f"COMMENT ON DATABASE {name} IS '{value}'")


def require_empty_database(engine: Engine, target: LabTarget) -> None:
    verify_server(engine, target)
    with engine.connect() as conn:
        occupied = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
            )
        """)).scalar_one()
    if occupied:
        raise RuntimeError("init solo acepta una BD vacía; no se borra ni sustituye un esquema existente")
