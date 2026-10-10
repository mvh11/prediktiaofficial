"""Feature prepartido team_recent_form_v1 (M5.7B): forma reciente de un equipo en estadísticas,
leída en un corte T con lo recibido hasta un horizonte H (ver statistics_knowledge).

Contrato:
- El corte T lo fija el llamador (independiente del kickoff). El partido objetivo y su kickoff K
  salen de la evidencia de DI-A6, STRICT_KNOWLEDGE(H); con H = T eso es conocerlo en T. T > K,
  objetivo desconocido, ambiguo o sin el equipo -> fail-closed, sin ventana ni métricas.
- Ventana: los últimos `window` partidos JUGADOS del equipo antes de T (evidencia en H: equipo en
  home/away, estado FT/AET/PEN, kickoff < T), por (kickoff, id) descendente, sin el objetivo.
  La ventana es por partidos jugados: uno sin estadísticas cuenta en la ventana y no se salta a
  partidos más viejos.
- Muestras: AVAILABLE/PARTIAL con el lado del equipo. AET/PEN se excluyen de las medias (incluyen
  la prórroga). Todo lo demás es una muestra ausente con su motivo; nunca un cero.
- Métricas: media por partido de cada campo, a favor (el equipo) y en contra (el rival, mismo
  partido), sobre los valores no nulos; None si hay menos de `min_samples`.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.schemas.statistics_knowledge import KnowledgeRegime, StatisticsProvenance

FEATURE_NAME = "team_recent_form_v1"
FORM_METRICS = ("shots_total", "shots_on_goal", "corners", "possession_pct", "expected_goals")


class FeatureStatus(StrEnum):
    OK = "OK"
    TARGET_UNKNOWN = "TARGET_UNKNOWN"  # el objetivo no se conoce en H
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"  # TEMPORAL_AMBIGUITY en H
    TARGET_TEAM_MISMATCH = "TARGET_TEAM_MISMATCH"  # la evidencia del objetivo no tiene al equipo
    CUTOFF_AFTER_KICKOFF = "CUTOFF_AFTER_KICKOFF"  # T posterior al kickoff conocido: no es prepartido
    AMBIGUOUS_HISTORY = "AMBIGUOUS_HISTORY"  # un partido del equipo antes de T es ambiguo en H
    AMBIGUOUS_ORDER = "AMBIGUOUS_ORDER"  # dos partidos del equipo con el mismo kickoff en la frontera de la ventana


class SampleStatus(StrEnum):
    USED = "USED"
    EXCLUDED_EXTRA_TIME = "EXCLUDED_EXTRA_TIME"  # AET/PEN
    UNKNOWN_AT_T = "UNKNOWN_AT_T"
    EMPTY = "EMPTY"
    BLOCKED = "BLOCKED"
    RECONSTRUCTION_MISMATCH = "RECONSTRUCTION_MISMATCH"
    IDENTITY_UNVERIFIED = "IDENTITY_UNVERIFIED"
    TEAM_STATISTICS_MISSING = "TEAM_STATISTICS_MISSING"  # PARTIAL sin el lado del equipo


class MetricSide(StrEnum):
    FOR = "for"
    AGAINST = "against"


@dataclass(frozen=True)
class WindowMatch:
    fixture_id: int
    kickoff_at: datetime
    venue: str  # home | away
    fixture_status: str  # según la evidencia en H
    fixture_known_at: datetime  # t* de la evidencia del partido
    sample: SampleStatus
    statistics_observation_id: int | None = None
    statistics_observed_at: datetime | None = None
    statistics_available_at: datetime | None = None
    statistics_payload_hash: str | None = None
    provenance: StatisticsProvenance | None = None

    def fixture_retrospective(self, cutoff: datetime) -> bool:
        """Hecho del partido conocido después de T (solo posible con H > T)."""
        return self.fixture_known_at > cutoff

    def statistics_retrospective(self, cutoff: datetime) -> bool:
        return self.statistics_observed_at is not None and self.statistics_observed_at > cutoff


@dataclass(frozen=True)
class MetricValue:
    name: str
    side: MetricSide
    value: Decimal | None  # None: menos de min_samples
    samples: int


@dataclass(frozen=True)
class TeamRecentForm:
    team_id: int
    target_fixture_id: int
    provider: str
    cutoff: datetime
    horizon: datetime
    regime: KnowledgeRegime
    status: FeatureStatus
    window_size: int
    min_samples: int
    normalizer_version: int
    target_kickoff: datetime | None = None
    target_known_at: datetime | None = None
    window: tuple[WindowMatch, ...] = ()
    metrics: tuple[MetricValue, ...] = ()
    feature: str = FEATURE_NAME

    def __post_init__(self) -> None:
        if self.status is not FeatureStatus.OK and (self.window or self.metrics):
            raise ValueError(f"{self.status}: sin ventana ni métricas")
        if len(self.window) > self.window_size:
            raise ValueError("ventana mayor que window_size")
        if any(m.kickoff_at >= self.cutoff for m in self.window):
            raise ValueError("un partido de la ventana no es anterior al corte")

    @property
    def target_retrospective(self) -> bool:
        return self.target_known_at is not None and self.target_known_at > self.cutoff

    @property
    def is_retrospective(self) -> bool:
        """Algún hecho o estadística se conoció después de T (régimen HISTORICAL_BACKTEST)."""
        return self.target_retrospective or any(
            m.fixture_retrospective(self.cutoff) or m.statistics_retrospective(self.cutoff) for m in self.window
        )

    def metric(self, name: str, side: MetricSide = MetricSide.FOR) -> MetricValue:
        return next(m for m in self.metrics if m.name == name and m.side is side)

    def sample_counts(self) -> dict[SampleStatus, int]:
        return {s: sum(1 for m in self.window if m.sample is s) for s in SampleStatus}

    def provenance_counts(self) -> dict[StatisticsProvenance, int]:
        return {p: sum(1 for m in self.window if m.sample is SampleStatus.USED and m.provenance is p) for p in StatisticsProvenance}
