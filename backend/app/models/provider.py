"""Proveedores externos de datos (API-Football, 5DollarFootballAPI, ...)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z0-9][a-z0-9_-]{1,39}$'", name="ck_providers_code_format"),
    )

    # Mismo valor que el atributo `name` del adapter (p. ej. "api-football")
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    # Familia de IDs de la cuenta, si el proveedor la informa (5DollarFootballAPI: "public_v1")
    id_scheme: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
