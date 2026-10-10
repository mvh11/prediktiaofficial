"""Lectura temporal de fixtures (DI-A6, Checkpoint B). Contrato: docs/data-integrity-status.md, "DI-A6C".

Dos modos de evaluación que nunca se mezclan, cada uno con su tipo y su marca (`mode`):

- STRICT_KNOWLEDGE: qué sabía Prediktia de un partido en un corte T, solo desde la evidencia
  inmutable (fixture_observations). Para cada partido, t* = max(observed_at <= T):
  - sin observación con observed_at <= T            -> UNKNOWN_AT_T;
  - un solo state_hash distinto en t*               -> KNOWN, con la evidencia AS_OBSERVED (G1);
  - más de uno                                      -> TEMPORAL_AMBIGUITY (el hash no es cronología).
  Nunca consulta fixtures, nunca fusiona con observaciones anteriores y nunca usa recorded_at.
- RETROSPECTIVE_FINAL_RESULTS: el resultado final tal como está HOY en fixtures (estado operativo
  actual, con la fusión de pares). No es conocimiento a fecha T y nunca sustituye al modo estricto.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar


class EvaluationMode(StrEnum):
    STRICT_KNOWLEDGE = "STRICT_KNOWLEDGE"
    RETROSPECTIVE_FINAL_RESULTS = "RETROSPECTIVE_FINAL_RESULTS"


class KnowledgeStatus(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN_AT_T = "UNKNOWN_AT_T"
    TEMPORAL_AMBIGUITY = "TEMPORAL_AMBIGUITY"


def require_utc(cutoff: datetime) -> None:
    """El corte es un instante: con zona horaria (como observed_at). Uno naive sería ambiguo."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("el corte T tiene que ser un instante con zona horaria")


@dataclass(frozen=True)
class ObservedFixtureState:
    """Una fila de fixture_observations tal cual: lo que entregó el proveedor en una respuesta.

    recorded_at es solo metadato de auditoría (cuándo se escribió la fila): nunca decide nada.
    """

    observation_id: int
    evidence_id: uuid.UUID
    fixture_id: int
    observed_at: datetime
    recorded_at: datetime
    source: str
    provider: str | None
    state_hash: bytes
    kickoff_at: datetime
    status_short: str
    season_id: int
    home_team_id: int
    away_team_id: int
    home_goals: int | None
    away_goals: int | None
    halftime_home: int | None
    halftime_away: int | None
    fulltime_home: int | None
    fulltime_away: int | None
    extratime_home: int | None
    extratime_away: int | None
    penalty_home: int | None
    penalty_away: int | None


@dataclass(frozen=True)
class FixtureKnowledge:
    """Clasificación STRICT_KNOWLEDGE de un partido en el corte T.

    `observations` son las filas reales en t* (vacío si UNKNOWN_AT_T). Con KNOWN todas comparten
    state_hash (mismo estado, quizá de respuestas distintas) y `state` da ese estado; con
    TEMPORAL_AMBIGUITY se conservan para auditoría, pero `state` es None: no se elige ganador.
    """

    mode: ClassVar[EvaluationMode] = EvaluationMode.STRICT_KNOWLEDGE

    fixture_id: int
    cutoff: datetime
    status: KnowledgeStatus
    known_at: datetime | None = None  # t*
    observations: tuple[ObservedFixtureState, ...] = ()

    def __post_init__(self) -> None:
        hashes = {o.state_hash for o in self.observations}
        if any(o.fixture_id != self.fixture_id or o.observed_at != self.known_at for o in self.observations):
            raise ValueError("todas las observaciones tienen que ser del partido y de t*")
        if self.status is KnowledgeStatus.UNKNOWN_AT_T:
            ok = self.known_at is None and not self.observations
        else:
            expected = len(hashes) == 1 if self.status is KnowledgeStatus.KNOWN else len(hashes) > 1
            ok = self.known_at is not None and self.known_at <= self.cutoff and expected
        if not ok:
            raise ValueError(f"FixtureKnowledge incoherente: {self.status} con {len(hashes)} estados en t*")

    @property
    def state(self) -> ObservedFixtureState | None:
        """La evidencia observada en t* (AS_OBSERVED) si es KNOWN; si no, None."""
        return self.observations[0] if self.status is KnowledgeStatus.KNOWN else None


@dataclass(frozen=True)
class StrictKnowledgeReport:
    """STRICT_KNOWLEDGE de varios partidos en un corte T.

    Por defecto el conjunto utilizable es `known`; UNKNOWN_AT_T y TEMPORAL_AMBIGUITY quedan fuera
    y se cuentan (`counts`), nunca se resuelven ni se rellenan desde fixtures.
    """

    mode: ClassVar[EvaluationMode] = EvaluationMode.STRICT_KNOWLEDGE

    cutoff: datetime
    results: dict[int, FixtureKnowledge] = field(default_factory=dict)

    def _with(self, status: KnowledgeStatus) -> dict[int, FixtureKnowledge]:
        return {f: k for f, k in self.results.items() if k.status is status}

    @property
    def known(self) -> dict[int, FixtureKnowledge]:
        return self._with(KnowledgeStatus.KNOWN)

    @property
    def unknown_at_t(self) -> list[int]:
        return sorted(self._with(KnowledgeStatus.UNKNOWN_AT_T))

    @property
    def temporal_ambiguity(self) -> list[int]:
        return sorted(self._with(KnowledgeStatus.TEMPORAL_AMBIGUITY))

    @property
    def counts(self) -> dict[KnowledgeStatus, int]:
        return {s: len(self._with(s)) for s in KnowledgeStatus}


@dataclass(frozen=True)
class RetrospectiveFinalResult:
    """RETROSPECTIVE_FINAL_RESULTS: resultado final de un partido según el estado operativo ACTUAL
    de fixtures.

    Es retrospectivo: puede llevar pares fusionados de respuestas distintas y correcciones
    posteriores a cualquier corte. No tiene t* ni evidencia: no es conocimiento a fecha T y no se
    puede usar como sustituto ni como relleno de STRICT_KNOWLEDGE.
    """

    mode: ClassVar[EvaluationMode] = EvaluationMode.RETROSPECTIVE_FINAL_RESULTS

    fixture_id: int
    status_short: str
    home_goals: int | None
    away_goals: int | None
    halftime_home: int | None
    halftime_away: int | None
    fulltime_home: int | None
    fulltime_away: int | None
    extratime_home: int | None
    extratime_away: int | None
    penalty_home: int | None
    penalty_away: int | None
