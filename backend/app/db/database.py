"""Engine de SQLAlchemy y clase base para los modelos ORM."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Todos los modelos de app/models heredarán de esta clase."""


engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,  # comprueba que la conexión sigue viva antes de usarla
)
