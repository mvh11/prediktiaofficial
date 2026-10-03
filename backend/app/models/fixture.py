"""Partidos (fixtures) y sus resultados."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ID del partido en el proveedor de datos (API-Football)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), index=True)
    round: Mapped[str | None] = mapped_column(String(100))
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Estado: código corto (NS, 1H, HT, FT, AET, PEN, PST, CANC...) y descripción
    status_short: Mapped[str] = mapped_column(String(10), index=True)
    status_long: Mapped[str | None] = mapped_column(String(50))
    elapsed: Mapped[int | None] = mapped_column(Integer)

    venue_name: Mapped[str | None] = mapped_column(String(200))
    venue_city: Mapped[str | None] = mapped_column(String(100))
    referee: Mapped[str | None] = mapped_column(String(150))

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)

    # Marcador final (incluye prórroga, sin penaltis) y parciales
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)
    halftime_home: Mapped[int | None] = mapped_column(Integer)
    halftime_away: Mapped[int | None] = mapped_column(Integer)
    extratime_home: Mapped[int | None] = mapped_column(Integer)
    extratime_away: Mapped[int | None] = mapped_column(Integer)
    penalty_home: Mapped[int | None] = mapped_column(Integer)
    penalty_away: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    season: Mapped["Season"] = relationship()  # noqa: F821
    home_team: Mapped["Team"] = relationship(foreign_keys=[home_team_id])  # noqa: F821
    away_team: Mapped["Team"] = relationship(foreign_keys=[away_team_id])  # noqa: F821
