"""Engine de SQLAlchemy y clase base para los modelos ORM."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings, get_settings


class Base(DeclarativeBase):
    """Todos los modelos de app/models heredarán de esta clase."""


def build_engine(settings: Settings) -> Engine:
    """Engine de la app con límites de tiempo explícitos.

    - connect_timeout (libpq): una BD inaccesible falla en ese tiempo en vez de colgar el proceso.
    - statement_timeout: se fija con SET LOCAL al empezar cada transacción. No se usa el parámetro
      de arranque `options` ni un SET de sesión porque un pooler en modo transacción (PgBouncer,
      como el de Neon) puede rechazar el primero y no conserva el segundo entre transacciones.
    """
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,  # comprueba que la conexión sigue viva antes de usarla
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
    )
    statement_timeout_ms = int(settings.db_statement_timeout_ms)
    if statement_timeout_ms > 0:

        @event.listens_for(engine, "begin")
        def _set_statement_timeout(conn) -> None:
            # Entero validado en Settings (>= 0): no hay interpolación de texto libre
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {statement_timeout_ms}")

    return engine


engine = build_engine(get_settings())
