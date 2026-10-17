"""Puntuación probabilística OPCIONAL de líneas Over/Under enteras con tres clases (MOD-DEV-074). Puro.

Interfaz aparte: research.goals_markets.evaluation (MOD-DEV-068-R1) no cambia; su evaluate_over_under
sigue siendo solo liquidación y recuento en líneas enteras.

Liquidación: research.m58.goal_labels.label_over_under (marcador fulltime a 90' en FT/AET/PEN; motivos
de exclusión canónicos). Solo líneas enteras aprobadas (1.0 a 6.0); una línea .5 o no aprobada es
ValueError antes de mirar las observaciones, también con la lista vacía.

Observaciones: la misma forma que en evaluation (lista o tupla de Mappings con status_short,
fulltime_home y fulltime_away; ObservationError si no). Probabilidades en el campo probabilities: un
Mapping con exactamente las claves OVER, UNDER y PUSH (sin orden posicional); cada valor int o float
finito (bool no) en [0, 1]; |p_OVER + p_UNDER + p_PUSH - 1| <= 1e-9 (SUM_TOLERANCE; suma con math.fsum,
MOD-DEV-079). No se recorta, renormaliza ni imputa. Se exigen en toda observación liquidada, PUSH incluido;
si faltan o no son válidas: ProbabilityEvidenceError y ningún resultado parcial. En las filas
excluidas no se leen.

Métricas (media aritmética sobre las observaciones liquidadas, todas puntuadas):
  Brier de tres componentes = (p_OVER - y_OVER)^2 + (p_UNDER - y_UNDER)^2 + (p_PUSH - y_PUSH)^2 (0-2;
  no es el Brier binario de MOD-DEV-068-R1); log_loss = -log(max(p_observada, EPS)) con
  EPS = research.m58.metrics.EPS (1e-15) solo dentro del log. Sin observaciones liquidadas: None.

PUSH es una clase observada que puntúa con p_PUSH y, para apuestas, una anulación (betting_void): nunca
acierto ni fallo. OVER y UNDER son resultados del total de goles, no aciertos de apuesta: sin un lado
apostado no se informan aciertos ni fallos.

Recuento: total_input = eligible_settled + excluded_total; eligible_settled = settlement_over +
settlement_under + settlement_push = probability_scored; betting_void = settlement_push.
"""

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from research.goals_markets.evaluation import (INTEGER_LINE, OVER_UNDER, PROBABILITIES, SUM_TOLERANCE, ProbabilityEvidenceError,
                                               _observations)
from research.m58.goal_labels import OVER, PUSH, SUPPORTED_LINE_TEXTS, SUPPORTED_LINES, UNDER, label_over_under, over_under_line
from research.m58.metrics import EPS

INTEGER_OUTCOMES = (OVER, UNDER, PUSH)  # orden canónico de la suma y del Brier (las claves deciden, no la posición)
THREE_CLASS = "THREE_CLASS_OVER_UNDER_PUSH"  # base de las métricas: no es el Brier binario de MOD-DEV-068-R1


@dataclass(frozen=True)
class IntegerLineEvaluation:
    market: str  # OVER_UNDER
    line: str  # texto canónico de la línea entera ("2.0")
    line_type: str  # INTEGER
    metric_basis: str  # THREE_CLASS_OVER_UNDER_PUSH
    total_input: int
    eligible_settled: int
    probability_scored: int
    settlement_over: int
    settlement_under: int
    settlement_push: int
    betting_void: int
    excluded_total: int
    excluded_by_reason: Mapping[str, int]  # solo lectura, ordenado por motivo
    brier: float | None
    log_loss: float | None


def validate_integer_probabilities(probabilities) -> Mapping[str, float]:
    """Las tres probabilidades por clase (vista de solo lectura, mismos valores) o ProbabilityEvidenceError."""
    if not isinstance(probabilities, Mapping):
        raise ProbabilityEvidenceError(f"se espera un Mapping con las claves {INTEGER_OUTCOMES}, no {type(probabilities).__name__}")
    if len(probabilities) != len(INTEGER_OUTCOMES) or any(k not in probabilities for k in INTEGER_OUTCOMES):
        raise ProbabilityEvidenceError(f"se esperan exactamente las claves {INTEGER_OUTCOMES}: {sorted(map(repr, probabilities))}")
    for k in INTEGER_OUTCOMES:
        p = probabilities[k]
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ProbabilityEvidenceError(f"probabilidad {k} no válida: {p!r}")
    if abs(math.fsum(probabilities[k] for k in INTEGER_OUTCOMES) - 1) > SUM_TOLERANCE:
        raise ProbabilityEvidenceError(f"las probabilidades no suman 1 (tolerancia {SUM_TOLERANCE}): {dict(probabilities)!r}")
    return MappingProxyType({k: probabilities[k] for k in INTEGER_OUTCOMES})


def evaluate_integer_over_under(observations, line) -> IntegerLineEvaluation:
    """Liquidación, recuento y métricas de tres clases para una línea entera aprobada."""
    approved = over_under_line(line)
    text = SUPPORTED_LINE_TEXTS[SUPPORTED_LINES.index(approved)]
    if not text.endswith(".0"):
        raise ValueError(f"la puntuación de tres clases es solo para líneas enteras, no {text!r}")
    rows = _observations(observations)
    excluded, settled = Counter(), Counter()
    briers, losses = [], []
    for i, obs in enumerate(rows):
        result, reason = label_over_under(obs["status_short"], obs["fulltime_home"], obs["fulltime_away"], approved)
        if result is None:
            excluded[reason] += 1  # motivo canónico; las probabilidades no se leen
            continue
        if PROBABILITIES not in obs or obs[PROBABILITIES] is None:
            raise ProbabilityEvidenceError(f"observación {i}: faltan las probabilidades de un resultado liquidado ({result})")
        try:
            p = validate_integer_probabilities(obs[PROBABILITIES])
        except ProbabilityEvidenceError as exc:
            raise ProbabilityEvidenceError(f"observación {i}: {exc}") from None
        settled[result] += 1
        briers.append(sum((p[k] - (k == result)) ** 2 for k in INTEGER_OUTCOMES))
        losses.append(-math.log(max(p[result], EPS)))

    over, under, push = settled[OVER], settled[UNDER], settled[PUSH]
    evaluation = IntegerLineEvaluation(
        market=OVER_UNDER, line=text, line_type=INTEGER_LINE, metric_basis=THREE_CLASS, total_input=len(rows),
        eligible_settled=over + under + push, probability_scored=len(briers), settlement_over=over, settlement_under=under,
        settlement_push=push, betting_void=push, excluded_total=sum(excluded.values()),
        excluded_by_reason=MappingProxyType(dict(sorted(excluded.items()))),
        brier=sum(briers) / len(briers) if briers else None, log_loss=sum(losses) / len(losses) if losses else None,
    )
    if not (evaluation.total_input == evaluation.eligible_settled + evaluation.excluded_total
            and evaluation.probability_scored == evaluation.eligible_settled and evaluation.betting_void == evaluation.settlement_push):
        raise AssertionError("recuento incoherente en la evaluación de tres clases")
    return evaluation
