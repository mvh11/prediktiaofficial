"""Lectura as-of de estadísticas de partido (M5.7A, horizonte M5.7B): qué estadísticas tenía
Prediktia disponibles de un partido en un corte T, con lo recibido hasta un horizonte H.

Contrato (por partido y proveedor):
- Dos instantes: T (corte de disponibilidad) y H >= T (horizonte de conocimiento). Solo cuentan
  las observaciones con available_at <= T y observed_at <= H (ambos incluidos). Entre ellas manda
  la versión más reciente por (observed_at, id): el orden de versionado del esquema (append-only;
  una versión nueva nunca es anterior a la vigente).
- Régimen: OPERATIONAL_STRICT si H = T (solo lo que Prediktia había recibido en T) o
  HISTORICAL_BACKTEST si H > T (reproducible con H fijo, pero retrospectivo: puede usar evidencia
  recibida después de T; cada observación lo indica con observed_after(T)).
- Identidad: home/away salen de la evidencia de fixtures de DI-A6, STRICT_KNOWLEDGE(H), nunca del
  estado actual de fixtures; y cada mapping de equipo tiene que existir ya cuando se registró la
  observación (created_at <= observed_at). Si no se puede verificar: IDENTITY_UNVERIFIED.
- Los valores se RECONSTRUYEN desde el raw de esa observación con el normalizador vigente; nunca
  se leen de fixture_team_statistics (estado actual fusionado, no historia) ni se fusionan con
  versiones anteriores.
- La calidad se recalcula sobre ese raw con los mismos checks del service: con algún BLOCKING el
  partido queda BLOCKED y no expone valores (igual que nunca se normalizó).
- Procedencia, sin mezclarse nunca:
  - HISTORICAL_SYNTHETIC (source='backfill'): available_at = kickoff + 6 h, una disponibilidad
    SINTÉTICA de la política histórica, no cuándo la conoció Prediktia (observed_at);
  - OPERATIONAL (source 'live' o 'manual'): available_at = observed_at, conocimiento real.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.schemas.statistics import TeamStatisticValues


class StatisticsProvenance(StrEnum):
    HISTORICAL_SYNTHETIC = "HISTORICAL_SYNTHETIC"
    OPERATIONAL = "OPERATIONAL"


class KnowledgeRegime(StrEnum):
    OPERATIONAL_STRICT = "OPERATIONAL_STRICT"  # H = T
    HISTORICAL_BACKTEST = "HISTORICAL_BACKTEST"  # H > T: retrospectivo


def regime_of(cutoff: datetime, horizon: datetime) -> KnowledgeRegime:
    if horizon < cutoff:
        raise ValueError("el horizonte H no puede ser anterior al corte T")
    return KnowledgeRegime.OPERATIONAL_STRICT if horizon == cutoff else KnowledgeRegime.HISTORICAL_BACKTEST


class StatisticsAsOfStatus(StrEnum):
    UNKNOWN_AT_T = "UNKNOWN_AT_T"  # ninguna observación disponible en T
    AVAILABLE = "AVAILABLE"  # los dos equipos con estadísticas
    PARTIAL = "PARTIAL"  # un solo equipo con estadísticas
    EMPTY = "EMPTY"  # observación válida sin estadísticas (no son ceros)
    BLOCKED = "BLOCKED"  # algún check BLOCKING: sin valores
    RECONSTRUCTION_MISMATCH = "RECONSTRUCTION_MISMATCH"  # el raw no reproduce lo registrado: sin valores
    IDENTITY_UNVERIFIED = "IDENTITY_UNVERIFIED"  # identidad de partido o equipos no verificable en H: sin valores


SYNTHETIC_SOURCES = frozenset({"backfill"})
OPERATIONAL_SOURCES = frozenset({"live", "manual"})


def provenance_of(source: str, observed_at: datetime, available_at: datetime) -> StatisticsProvenance:
    """Procedencia de una observación. Una operativa con available_at != observed_at incumple la
    política temporal y no se reinterpreta: ValueError."""
    if source in SYNTHETIC_SOURCES:
        return StatisticsProvenance.HISTORICAL_SYNTHETIC
    if source in OPERATIONAL_SOURCES:
        if available_at != observed_at:
            raise ValueError(f"observación {source} con available_at distinto de observed_at")
        return StatisticsProvenance.OPERATIONAL
    raise ValueError(f"source desconocido: {source!r}")


@dataclass(frozen=True)
class StatisticsObservationRef:
    """La observación elegida en T, tal cual está guardada (no se modifica nunca)."""

    observation_id: int
    run_id: int | None
    source: str
    provenance: StatisticsProvenance
    observed_at: datetime
    available_at: datetime
    payload_hash: str
    availability: str  # la registrada: available | partial | empty
    teams_returned: int

    def observed_after(self, cutoff: datetime) -> bool:
        """Disponible por política sintética en T, pero Prediktia la recibió después de T."""
        return self.observed_at > cutoff


@dataclass(frozen=True)
class TeamStatisticsAsOf:
    side: str  # home | away
    team_id: int
    provider_team_id: int
    values: TeamStatisticValues
    present: frozenset[str]  # campos v1 que venían en el raw (con valor o null)


@dataclass(frozen=True)
class QualityIssueRef:
    severity: str
    code: str
    provider_team_id: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class FixtureStatisticsAsOf:
    fixture_id: int
    provider: str
    cutoff: datetime
    status: StatisticsAsOfStatus
    observation: StatisticsObservationRef | None = None
    eligible_versions: int = 0  # observaciones con available_at <= T
    teams: tuple[TeamStatisticsAsOf, ...] = ()  # home antes que away; vacío salvo AVAILABLE/PARTIAL
    issues: tuple[QualityIssueRef, ...] = ()  # BLOCKING y WARNING recalculados (y desajustes)
    recorded_blocking: tuple[str, ...] | None = None  # BLOCKING que registró su run (None: run no disponible)
    normalizer_version: int | None = None
    horizon: datetime | None = None  # None = T (régimen estricto)

    def __post_init__(self) -> None:
        if self.horizon is None:
            object.__setattr__(self, "horizon", self.cutoff)
        regime_of(self.cutoff, self.horizon)
        unknown = self.status is StatisticsAsOfStatus.UNKNOWN_AT_T
        if unknown != (self.observation is None):
            raise ValueError("UNKNOWN_AT_T si y solo si no hay observación")
        if self.observation is not None and self.observation.available_at > self.cutoff:
            raise ValueError("la observación elegida no puede estar disponible después del corte")
        if self.observation is not None and self.observation.observed_at > self.horizon:
            raise ValueError("la observación elegida no puede haberse recibido después del horizonte")
        with_values = self.status in (StatisticsAsOfStatus.AVAILABLE, StatisticsAsOfStatus.PARTIAL)
        expected = {StatisticsAsOfStatus.AVAILABLE: 2, StatisticsAsOfStatus.PARTIAL: 1}.get(self.status, 0)
        if len(self.teams) != expected or (self.teams and not with_values):
            raise ValueError(f"{self.status} incoherente con {len(self.teams)} equipos")

    @property
    def regime(self) -> KnowledgeRegime:
        return regime_of(self.cutoff, self.horizon)

    @property
    def provenance(self) -> StatisticsProvenance | None:
        return self.observation.provenance if self.observation is not None else None

    @property
    def is_usable(self) -> bool:
        """Hay valores utilizables (AVAILABLE o PARTIAL)."""
        return bool(self.teams)

    def team(self, side: str) -> TeamStatisticsAsOf | None:
        return next((t for t in self.teams if t.side == side), None)


@dataclass(frozen=True)
class StatisticsAsOfReport:
    cutoff: datetime
    provider: str
    results: dict[int, FixtureStatisticsAsOf] = field(default_factory=dict)
    horizon: datetime | None = None  # None = T

    def __post_init__(self) -> None:
        if self.horizon is None:
            object.__setattr__(self, "horizon", self.cutoff)
        regime_of(self.cutoff, self.horizon)

    @property
    def regime(self) -> KnowledgeRegime:
        return regime_of(self.cutoff, self.horizon)

    def with_status(self, status: StatisticsAsOfStatus) -> list[int]:
        return sorted(f for f, r in self.results.items() if r.status is status)

    @property
    def counts(self) -> dict[StatisticsAsOfStatus, int]:
        return {s: len(self.with_status(s)) for s in StatisticsAsOfStatus}
