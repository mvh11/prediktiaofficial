"""Contrato interno de estadísticas de partido por equipo (M5).

Lo entrega un adapter ya traducido a nombres de Prediktia: nada fuera del adapter conoce los
nombres ni los formatos de estadística del proveedor.

Semántica de valores (estricta):
- un valor numérico del proveedor se conserva tal cual (0 es 0);
- null del proveedor → None;
- tipo ausente en la respuesta → None, pero distinguible: el campo no está en `present`;
- nunca se inventan ceros por ausencia (p. ej. tarjetas rojas a null siguen siendo None).

El adapter normaliza; la gravedad de cada anomalía (valor no parseable, fuera de rango, tipo
desconocido, equipo ausente) la decide el service/quality checks, no el contrato.
"""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Campos normalizados del contrato v1, por tipo de valor
INTEGER_FIELDS = (
    "shots_on_goal",
    "shots_off_goal",
    "shots_total",
    "shots_blocked",
    "shots_inside_box",
    "shots_outside_box",
    "fouls",
    "corners",
    "offsides",
    "yellow_cards",
    "red_cards",
    "goalkeeper_saves",
    "passes_total",
    "passes_accurate",
)
PERCENTAGE_FIELDS = ("possession_pct", "passes_pct")  # 0–100
DECIMAL_FIELDS = ("expected_goals",)  # decimal no negativo
STATISTIC_FIELDS = INTEGER_FIELDS + PERCENTAGE_FIELDS + DECIMAL_FIELDS

# Se esperan en toda competición con cobertura (pueden faltar: cuentan en cobertura, no bloquean)
CORE_FIELDS = frozenset(
    {"shots_on_goal", "shots_off_goal", "shots_total", "shots_blocked", "corners", "fouls", "yellow_cards", "possession_pct"}
)
OPTIONAL_FIELDS = frozenset(STATISTIC_FIELDS) - CORE_FIELDS


class TeamStatisticValues(BaseModel):
    """Valores normalizados v1 de un equipo en un partido. None = sin dato (null o ausente)."""

    model_config = ConfigDict(frozen=True)

    shots_on_goal: int | None = None
    shots_off_goal: int | None = None
    shots_total: int | None = None
    shots_blocked: int | None = None
    shots_inside_box: int | None = None
    shots_outside_box: int | None = None
    fouls: int | None = None
    corners: int | None = None
    offsides: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    goalkeeper_saves: int | None = None
    passes_total: int | None = None
    passes_accurate: int | None = None
    possession_pct: Decimal | None = None
    passes_pct: Decimal | None = None
    expected_goals: Decimal | None = None


class TeamStatisticsData(BaseModel):
    """Estadísticas de un equipo tal como las entregó el proveedor, ya normalizadas.

    - present: campos v1 cuyo tipo venía en la respuesta (con valor o con null). Un campo v1
      que no está aquí faltaba en la respuesta.
    - unparseable: campo v1 → valor original que no se pudo interpretar (bool, "-", texto...);
      su valor normalizado es None.
    - out_of_range: campo v1 → valor ya interpretado pero imposible (negativo, o porcentaje
      > 100); su valor normalizado es None y aquí queda la evidencia para los quality checks.
    - raw_only: tipos conocidos por el adapter que v1 no normaliza, con su valor original.
    - unknown: tipos que el adapter no conoce, valor original. No se pierden.
    - duplicated: campos v1 cuyo tipo vino más de una vez con valores distintos (se queda None).
    """

    model_config = ConfigDict(frozen=True)

    provider_team_id: int | None
    values: TeamStatisticValues = Field(default_factory=TeamStatisticValues)
    present: frozenset[str] = frozenset()
    unparseable: dict[str, Any] = Field(default_factory=dict)
    out_of_range: dict[str, int | Decimal] = Field(default_factory=dict)
    raw_only: dict[str, Any] = Field(default_factory=dict)
    unknown: dict[str, Any] = Field(default_factory=dict)
    duplicated: dict[str, list[Any]] = Field(default_factory=dict)
    malformed_entries: int = 0  # entradas sin "type" de texto (se conservan en el raw)

    @property
    def has_statistics(self) -> bool:
        """El proveedor envió al menos un tipo de estadística para este equipo."""
        return bool(self.present or self.raw_only or self.unknown)

    @property
    def missing(self) -> frozenset[str]:
        """Campos v1 que no venían en la respuesta (distinto de venir a null)."""
        return frozenset(STATISTIC_FIELDS) - self.present


class FixtureStatisticsData(BaseModel):
    """Estadísticas de un partido según un proveedor.

    raw_statistics es el array de estadísticas tal cual lo envió el proveedor (None si la
    respuesta no traía la clave), para auditoría, detección de cambios y re-normalización.
    teams conserva el orden y todas las entradas del proveedor, también las vacías: un partido
    sin estadísticas suele venir con sus dos equipos y listas vacías, y eso no son ceros.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    provider_fixture_id: int
    provider_status: str | None
    teams: list[TeamStatisticsData] = Field(default_factory=list)
    raw_statistics: list[Any] | None = None

    @property
    def teams_with_statistics(self) -> int:
        return sum(1 for t in self.teams if t.has_statistics)
