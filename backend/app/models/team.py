"""Equipos (clubes y selecciones)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ID del equipo en API-Football. Deprecado como identidad: la fuente de verdad
    # multi-proveedor es team_provider_mappings (se mantiene durante la transición)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str | None] = mapped_column(String(100))
    founded: Mapped[int | None] = mapped_column(Integer)
    is_national: Mapped[bool] = mapped_column(Boolean, default=False)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
