"""Estadísticas de partido por equipo (M5): runs auditados, observaciones raw y estado normalizado.

- statistics_runs: cada ejecución (backfill por temporada, lote de partidos o pasada en vivo).
  El run 'running' de lock_scope 'statistics' es el lock (índice único parcial, sin advisory locks).
- fixture_statistics_observations: cada versión DISTINTA (por hash) del array de estadísticas que
  dio un proveedor para un partido. Append-only: si el hash no cambia solo avanza
  last_observed_at. Exactamente una observación is_latest por (fixture, provider).
- fixture_team_statistics: estado actual normalizado (contrato v1), una fila por
  (fixture, equipo, provider), enlazada a la observación de la que sale. Una FK compuesta
  (observation_id, fixture_id, provider) impide enlazar con la observación de otro partido.

Tiempos de una observación: observed_at = cuándo Prediktia recibió esa versión (known_at real);
available_at = cuándo se considera disponible para features (en backfill histórico es una
disponibilidad sintética kickoff + 6 h; en vivo, observed_at). updated_at nunca es known_at.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.schemas.statistics import DECIMAL_FIELDS, INTEGER_FIELDS, PERCENTAGE_FIELDS

RUN_TRIGGERS = ("cli", "scheduler")
RUN_MODES = ("dry_run", "apply")
RUN_SCOPES = ("season", "fixtures", "live")
RUN_STATUSES = ("running", "completed", "completed_with_errors", "failed", "aborted", "dry_run_completed")
LOCK_SCOPES = ("statistics",)
# Estados finales en los que el reparto de partidos tiene que cuadrar (failed/aborted pueden
# haberse cortado a mitad de un lote)
ACCOUNTED_STATUSES = ("completed", "completed_with_errors", "dry_run_completed")

FIXTURE_OUTCOMES = (
    "fixtures_available",
    "fixtures_partial",
    "fixtures_empty",
    "fixtures_blocked",
    "fixtures_missing_in_response",
)
RUN_COUNTERS = (
    "fixtures_targeted",
    "fixtures_attempted",
    *FIXTURE_OUTCOMES,
    "provider_requests",
    "provider_retries",
    "observations_created",
    "observations_unchanged",
    "rows_created",
    "rows_updated",
    "rows_unchanged",
    "warning_count",
    "blocking_count",
)

OBSERVATION_SOURCES = ("backfill", "live", "manual")
AVAILABILITIES = ("available", "partial", "empty")
SIDES = ("home", "away")

# Escala de las columnas numéricas (también la usa el repository para comparar sin falsos cambios)
NUMERIC_SCALE = {"possession_pct": 2, "passes_pct": 2, "expected_goals": 3}


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _null_or(column: str, condition: str) -> str:
    return f"{column} IS NULL OR {condition}"


class StatisticsRun(Base):
    __tablename__ = "statistics_runs"
    __table_args__ = (
        CheckConstraint(f"trigger IN {_in(RUN_TRIGGERS)}", name="ck_statistics_runs_trigger"),
        CheckConstraint(f"mode IN {_in(RUN_MODES)}", name="ck_statistics_runs_mode"),
        CheckConstraint(f"scope IN {_in(RUN_SCOPES)}", name="ck_statistics_runs_scope"),
        CheckConstraint(f"status IN {_in(RUN_STATUSES)}", name="ck_statistics_runs_status"),
        CheckConstraint(f"lock_scope IN {_in(LOCK_SCOPES)}", name="ck_statistics_runs_lock_scope"),
        CheckConstraint(
            "scope <> 'season' OR (competition_id IS NOT NULL AND season_id IS NOT NULL)",
            name="ck_statistics_runs_season_scope",
        ),
        CheckConstraint(
            "(status = 'dry_run_completed' AND mode = 'dry_run') OR (status IN ('completed', 'completed_with_errors') "
            "AND mode = 'apply') OR status IN ('running', 'failed', 'aborted')",
            name="ck_statistics_runs_status_mode",
        ),
        CheckConstraint("(finished_at IS NULL) = (status = 'running')", name="ck_statistics_runs_finished"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_statistics_runs_order"),
        CheckConstraint(" AND ".join(f"{c} >= 0" for c in RUN_COUNTERS), name="ck_statistics_runs_counters"),
        CheckConstraint(
            f"status NOT IN {_in(ACCOUNTED_STATUSES)} OR "
            f"(fixtures_attempted = {' + '.join(FIXTURE_OUTCOMES)} AND fixtures_attempted <= fixtures_targeted)",
            name="ck_statistics_runs_fixture_accounting",
        ),
        Index("ix_statistics_runs_season_started", "season_id", "started_at"),
        Index(
            "uq_statistics_runs_one_running",
            "lock_scope",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trigger: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    competition_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("competitions.id", ondelete="CASCADE"))
    season_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("seasons.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text)
    lock_scope: Mapped[str] = mapped_column(Text, server_default=text("'statistics'"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Último partido procesado (id interno, orden ascendente): permite reanudar
    cursor_fixture_id: Mapped[int | None] = mapped_column(Integer)

    fixtures_targeted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_attempted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_available: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_partial: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_empty: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_blocked: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    fixtures_missing_in_response: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    provider_requests: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    provider_retries: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rate_limited: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    auth_failed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    observations_created: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    observations_unchanged: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_created: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_updated: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rows_unchanged: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    warning_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    blocking_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    checks: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    details: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error_message: Mapped[str | None] = mapped_column(Text)


class FixtureStatisticsObservation(Base):
    __tablename__ = "fixture_statistics_observations"
    __table_args__ = (
        CheckConstraint(f"source IN {_in(OBSERVATION_SOURCES)}", name="ck_fso_source"),
        CheckConstraint(f"availability IN {_in(AVAILABILITIES)}", name="ck_fso_availability"),
        CheckConstraint("teams_returned BETWEEN 0 AND 2", name="ck_fso_teams_returned"),
        # availability se deriva de cuántos equipos traen estadísticas
        CheckConstraint(
            "(availability = 'empty' AND teams_returned = 0) OR (availability = 'partial' AND teams_returned = 1) "
            "OR (availability = 'available' AND teams_returned = 2)",
            name="ck_fso_availability_teams",
        ),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_fso_payload_hash"),
        CheckConstraint("last_observed_at >= observed_at", name="ck_fso_observed_order"),
        # Destino de la FK compuesta de fixture_team_statistics
        UniqueConstraint("id", "fixture_id", "provider", name="uq_fso_id_fixture_provider"),
        Index(
            "uq_fso_one_latest",
            "fixture_id",
            "provider",
            unique=True,
            postgresql_where=text("is_latest"),
        ),
        Index("ix_fso_fixture_provider_available", "fixture_id", "provider", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text, ForeignKey("providers.code", ondelete="RESTRICT"))
    provider_fixture_id: Mapped[str] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statistics_runs.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(Text)
    fixture_status_at_fetch: Mapped[str | None] = mapped_column(Text)
    availability: Mapped[str] = mapped_column(Text)
    teams_returned: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[Any] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_latest: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


_STAT_CHECKS = (
    *(CheckConstraint(_null_or(c, f"{c} >= 0"), name=f"ck_fts_{c}_nonneg") for c in INTEGER_FIELDS),
    *(CheckConstraint(_null_or(c, f"{c} BETWEEN 0 AND 100"), name=f"ck_fts_{c}_range") for c in PERCENTAGE_FIELDS),
    *(CheckConstraint(_null_or(c, f"{c} >= 0"), name=f"ck_fts_{c}_nonneg") for c in DECIMAL_FIELDS),
)


class FixtureTeamStatistics(Base):
    __tablename__ = "fixture_team_statistics"
    __table_args__ = (
        CheckConstraint(f"side IN {_in(SIDES)}", name="ck_fts_side"),
        CheckConstraint("normalizer_version > 0", name="ck_fts_normalizer_version"),
        *_STAT_CHECKS,
        UniqueConstraint("fixture_id", "team_id", "provider", name="uq_fts_fixture_team_provider"),
        UniqueConstraint("fixture_id", "side", "provider", name="uq_fts_fixture_side_provider"),
        # La observación tiene que ser del mismo partido y proveedor
        ForeignKeyConstraint(
            ["observation_id", "fixture_id", "provider"],
            [
                "fixture_statistics_observations.id",
                "fixture_statistics_observations.fixture_id",
                "fixture_statistics_observations.provider",
            ],
            name="fk_fts_observation_same_fixture",
            ondelete="CASCADE",
        ),
        Index("ix_fts_team_fixture", "team_id", "fixture_id"),
        Index("ix_fts_observation", "observation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(Text, ForeignKey("providers.code", ondelete="RESTRICT"))
    observation_id: Mapped[int] = mapped_column(BigInteger)
    side: Mapped[str] = mapped_column(Text)
    normalizer_version: Mapped[int] = mapped_column(SmallInteger)

    shots_on_goal: Mapped[int | None] = mapped_column(Integer)
    shots_off_goal: Mapped[int | None] = mapped_column(Integer)
    shots_total: Mapped[int | None] = mapped_column(Integer)
    shots_blocked: Mapped[int | None] = mapped_column(Integer)
    shots_inside_box: Mapped[int | None] = mapped_column(Integer)
    shots_outside_box: Mapped[int | None] = mapped_column(Integer)
    fouls: Mapped[int | None] = mapped_column(Integer)
    corners: Mapped[int | None] = mapped_column(Integer)
    offsides: Mapped[int | None] = mapped_column(Integer)
    yellow_cards: Mapped[int | None] = mapped_column(Integer)
    red_cards: Mapped[int | None] = mapped_column(Integer)
    goalkeeper_saves: Mapped[int | None] = mapped_column(Integer)
    passes_total: Mapped[int | None] = mapped_column(Integer)
    passes_accurate: Mapped[int | None] = mapped_column(Integer)
    possession_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    passes_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    expected_goals: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
