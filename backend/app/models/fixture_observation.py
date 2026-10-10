"""Evidencia temporal inmutable de fixtures (DI-A6). Contrato: docs/data-integrity-status.md, "DI-A6C".

Cada fila es lo que un proveedor entregó para un partido en una respuesta lógica (evidence_id),
normalizado por el adapter y SIN la fusión de pares que sí se aplica a fixtures. Solo se insertan
filas (las escribe fixture_repository), nunca se actualizan.

Tiempos: observed_at = cuándo recibió Prediktia la evidencia (reloj de la app; única columna de
orden temporal). recorded_at = cuándo se escribió la fila (solo auditoría física).
season_id y los equipos son valores del momento, sin FK: la historia no depende de la referencia actual.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class FixtureObservation(Base):
    __tablename__ = "fixture_observations"
    __table_args__ = (
        UniqueConstraint("fixture_id", "evidence_id", name="uq_fixture_observations_fixture_evidence"),
        Index("ix_fixture_observations_fixture_observed", "fixture_id", "observed_at"),
        CheckConstraint("source IN ('sync', 'backfill', 'bootstrap')", name="ck_fixture_observations_source"),
        CheckConstraint("(source IN ('sync', 'backfill')) = (provider IS NOT NULL)", name="ck_fixture_observations_provider"),
        CheckConstraint("octet_length(state_hash) = 32", name="ck_fixture_observations_state_hash"),
        CheckConstraint("(fulltime_home IS NULL) = (fulltime_away IS NULL)", name="ck_fixture_observations_fulltime_pair"),
        CheckConstraint(
            "fulltime_home IS NULL OR status_short IN ('FT', 'AET', 'PEN')",
            name="ck_fixture_observations_fulltime_finished",
        ),
        CheckConstraint(
            "fulltime_home IS NULL OR (fulltime_home >= 0 AND fulltime_away >= 0)",
            name="ck_fixture_observations_fulltime_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id", ondelete="RESTRICT"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text, ForeignKey("providers.code", ondelete="RESTRICT"))
    state_hash: Mapped[bytes] = mapped_column(LargeBinary)

    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status_short: Mapped[str] = mapped_column(String(10))
    season_id: Mapped[int] = mapped_column(Integer)
    home_team_id: Mapped[int] = mapped_column(Integer)
    away_team_id: Mapped[int] = mapped_column(Integer)
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)
    halftime_home: Mapped[int | None] = mapped_column(Integer)
    halftime_away: Mapped[int | None] = mapped_column(Integer)
    fulltime_home: Mapped[int | None] = mapped_column(Integer)
    fulltime_away: Mapped[int | None] = mapped_column(Integer)
    extratime_home: Mapped[int | None] = mapped_column(Integer)
    extratime_away: Mapped[int | None] = mapped_column(Integer)
    penalty_home: Mapped[int | None] = mapped_column(Integer)
    penalty_away: Mapped[int | None] = mapped_column(Integer)
