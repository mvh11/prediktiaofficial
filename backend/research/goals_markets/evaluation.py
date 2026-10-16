"""Evaluación pura de mercados de goles a 90' (MOD-DEV-068-R1): BTTS y Over/Under. Sin BD, red ni disco.

La liquidación es la de research.m58.goal_labels (marcador fulltime a 90' en FT/AET/PEN; motivos de
exclusión status_not_finished, fulltime_incomplete y fulltime_invalid); aquí no se reimplementa.

Observación: un Mapping con status_short, fulltime_home, fulltime_away y, en los mercados binarios,
probabilities. Las observaciones van en una lista o tupla (nunca un iterable de un solo uso) y no se
modifican.

Probabilidades (D1, D4): vector ordenado de dos valores, (p_yes, p_no) en BTTS y (p_over, p_under) en
líneas .5; lista o tupla; cada valor int o float finito (bool no), en [0, 1]; |suma - 1| <= 1e-9 (la
regla de research.m510a.evaluate, con dos componentes). No se renormaliza ni se recorta. Se exigen en
toda observación liquidada sin PUSH de un mercado binario: si faltan o no son válidas se lanza
ProbabilityEvidenceError (fail-closed) y no hay resultado parcial. En las filas excluidas no se leen.

Métricas (D3, D5), media aritmética sobre las observaciones puntuadas:
  Brier = (p_1 - y_1)^2 + (p_2 - y_2)^2  (dos componentes, sin dividir entre 2; 0-2)
  log_loss = -log(max(p_observada, EPS)), con EPS = research.m58.metrics.EPS (1e-15) solo en el log
Sin observaciones puntuadas, ambas son None (D6). Nada más (D8): ni ECE, ni calibración, ni umbrales.

Líneas enteras (D2): solo liquidación y recuento (OVER, UNDER, PUSH); las probabilidades no se leen y
las métricas son None. Ahí `scored` cuenta las liquidaciones sin PUSH (scored_basis =
SETTLEMENT_ONLY): NO son observaciones puntuadas con probabilidad.

Recuento (D7): total_input = scored + push_void + excluded_total y eligible_settled = scored +
push_void. PUSH (anulada) nunca es acierto ni fallo.
"""

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from research.m58.goal_labels import (BTTS_NO, BTTS_YES, OVER, PUSH, SUPPORTED_LINE_TEXTS, SUPPORTED_LINES, UNDER, label_btts,
                                      label_over_under, over_under_line)
from research.m58.metrics import EPS

BTTS = "BTTS"
OVER_UNDER = "OVER_UNDER"
HALF_LINE, INTEGER_LINE = "HALF", "INTEGER"
PROBABILITY_SCORED = "PROBABILITY_SCORED"  # scored = observaciones con probabilidades válidas que entran en las métricas
SETTLEMENT_ONLY = "SETTLEMENT_ONLY"  # scored = liquidaciones sin PUSH; ninguna métrica probabilística
SUM_TOLERANCE = 1e-9  # D4: la tolerancia de research.m510a.evaluate

BTTS_OUTCOMES = (BTTS_YES, BTTS_NO)  # orden del vector (p_yes, p_no)
OVER_UNDER_OUTCOMES = (OVER, UNDER)  # orden del vector (p_over, p_under)
OBSERVATION_FIELDS = ("status_short", "fulltime_home", "fulltime_away")
PROBABILITIES = "probabilities"


class ObservationError(ValueError):
    """La colección o una observación no tienen la forma pedida (error del llamador)."""


class ProbabilityEvidenceError(ValueError):
    """Faltan o no son válidas las probabilidades de una observación que hay que puntuar."""


@dataclass(frozen=True)
class MarketEvaluation:
    market: str  # BTTS | OVER_UNDER
    line: str | None  # texto canónico de la línea ("2.5"); None en BTTS
    line_type: str | None  # HALF | INTEGER; None en BTTS
    scored_basis: str  # PROBABILITY_SCORED | SETTLEMENT_ONLY
    total_input: int
    eligible_settled: int
    scored: int
    push_void: int
    excluded_total: int
    excluded_by_reason: Mapping[str, int]  # solo lectura, ordenado por motivo
    outcome_counts: Mapping[str, int]  # solo lectura; liquidaciones por resultado, PUSH incluido si aplica
    brier: float | None
    log_loss: float | None


def validate_binary_probabilities(probabilities) -> tuple[float, float]:
    """Las mismas dos probabilidades, en el mismo orden, o ProbabilityEvidenceError (D1, D4)."""
    if not isinstance(probabilities, (list, tuple)) or len(probabilities) != 2:
        raise ProbabilityEvidenceError(f"se esperan dos probabilidades en una lista o tupla: {probabilities!r}")
    for p in probabilities:
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ProbabilityEvidenceError(f"probabilidad no válida: {probabilities!r}")
    if abs(probabilities[0] + probabilities[1] - 1) > SUM_TOLERANCE:
        raise ProbabilityEvidenceError(f"las probabilidades no suman 1 (tolerancia {SUM_TOLERANCE}): {probabilities!r}")
    return probabilities[0], probabilities[1]


def _observations(observations) -> tuple:
    if not isinstance(observations, (list, tuple)):
        raise ObservationError(f"las observaciones van en una lista o tupla, no en {type(observations).__name__}")
    for i, obs in enumerate(observations):
        if not isinstance(obs, Mapping):
            raise ObservationError(f"observación {i}: se espera un Mapping, no {type(obs).__name__}")
        missing = [f for f in OBSERVATION_FIELDS if f not in obs]
        if missing:
            raise ObservationError(f"observación {i}: faltan los campos {missing}")
    return tuple(observations)


def _evaluate(observations, settle, outcomes, market, line, line_type) -> MarketEvaluation:
    rows = _observations(observations)
    probabilistic = line_type != INTEGER_LINE
    excluded, settled = Counter(), Counter()
    briers, losses = [], []
    for i, obs in enumerate(rows):
        result, reason = settle(obs["status_short"], obs["fulltime_home"], obs["fulltime_away"])
        if result is None:
            excluded[reason] += 1  # motivo canónico de goal_labels; las probabilidades no se leen
            continue
        settled[result] += 1
        if result == PUSH or not probabilistic:
            continue
        if PROBABILITIES not in obs or obs[PROBABILITIES] is None:
            raise ProbabilityEvidenceError(f"observación {i}: faltan las probabilidades de un resultado liquidado")
        try:
            p = validate_binary_probabilities(obs[PROBABILITIES])
        except ProbabilityEvidenceError as exc:
            raise ProbabilityEvidenceError(f"observación {i}: {exc}") from None
        observed = outcomes.index(result)
        briers.append(sum((p[k] - (k == observed)) ** 2 for k in range(2)))
        losses.append(-math.log(max(p[observed], EPS)))

    push = settled[PUSH]
    scored = sum(settled.values()) - push
    if probabilistic and scored != len(briers):
        raise AssertionError("recuento incoherente: puntuadas != métricas calculadas")
    evaluation = MarketEvaluation(
        market=market, line=line, line_type=line_type, scored_basis=PROBABILITY_SCORED if probabilistic else SETTLEMENT_ONLY,
        total_input=len(rows), eligible_settled=scored + push, scored=scored, push_void=push, excluded_total=sum(excluded.values()),
        excluded_by_reason=MappingProxyType(dict(sorted(excluded.items()))),
        outcome_counts=MappingProxyType({o: settled[o] for o in outcomes + ((PUSH,) if line_type == INTEGER_LINE else ())}),
        brier=sum(briers) / len(briers) if briers else None, log_loss=sum(losses) / len(losses) if losses else None,
    )
    if evaluation.total_input != evaluation.scored + evaluation.push_void + evaluation.excluded_total:
        raise AssertionError("recuento incoherente: total_input != scored + push_void + excluded_total")
    return evaluation


def evaluate_btts(observations) -> MarketEvaluation:
    """BTTS: probabilidades (p_yes, p_no) en cada observación liquidada."""
    return _evaluate(observations, label_btts, BTTS_OUTCOMES, BTTS, None, None)


def evaluate_over_under(observations, line) -> MarketEvaluation:
    """Over/Under en una línea aprobada. Línea .5: probabilidades (p_over, p_under) y métricas; línea
    entera: solo liquidación y recuento (D2). Una línea no aprobada es ValueError, aunque no haya filas."""
    approved = over_under_line(line)
    text = SUPPORTED_LINE_TEXTS[SUPPORTED_LINES.index(approved)]
    line_type = INTEGER_LINE if text.endswith(".0") else HALF_LINE

    def settle(status_short, fulltime_home, fulltime_away):
        return label_over_under(status_short, fulltime_home, fulltime_away, approved)

    return _evaluate(observations, settle, OVER_UNDER_OUTCOMES, OVER_UNDER, text, line_type)
