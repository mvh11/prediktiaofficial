"""Competiciones (ligas y copas)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ID de la competición en API-Football. Deprecado como identidad: la fuente de verdad
    # multi-proveedor es competition_provider_mappings (se mantiene durante la transición)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    type: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str | None] = mapped_column(String(10))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    seasons: Mapped[list["Season"]] = relationship(  # noqa: F821
        back_populates="competition", order_by="Season.year"
    )
