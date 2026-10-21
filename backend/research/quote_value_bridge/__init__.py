"""Puente sintético cuota elegible -> cuota justa y EV (MOD-DEV-103A). Puro: sin BD, red, disco ni app.

Une dos piezas existentes sin reimplementarlas:
  research.quote_temporal_eligibility.evaluate_quote_eligibility  (qué cuota se conocía al emitir)
  research.market_value_math.value.evaluate_binary_value / evaluate_push_value  (cuota justa y EV)
Todo es SINTÉTICO y lo aporta el llamador: ni las cuotas están autenticadas ni las probabilidades
calibradas. PIT_STATUS = NOT_CERTIFIED. No hay importes, stakes, bankroll, picks ni recomendaciones, y
un EV > 0 es una esperanza matemática sobre entradas no verificadas, no un beneficio.

Interfaz:
  evaluate_quote_value(context, quotes, probabilities) -> QuoteValueResult
context y quotes son los de evaluate_quote_eligibility, que el puente ejecuta él mismo: nunca acepta un
EligibilityReport ni un QualifiedQuote ya hechos, así que no puede recibir una cuota cualificada
fabricada. Un context o una colección mal formados lanzan su EligibilityInputError (sin resultado
parcial). probabilities es un SelectionProbabilities del llamador; cualquier problema en él es un
resultado NO_VALUE, nunca una excepción.

Orden (el primer fallo decide; el motor de valor solo se llama si todo lo anterior pasa):
  1. Semántica del objetivo: mercado no soportado, línea Over/Under de cuarto (n.25 / n.75) u otra
     línea no aprobada -> UNSUPPORTED (sin aproximar una liquidación partida).
  2. Elegibilidad: exactamente UNA QualifiedQuote. Cero -> NO_ELIGIBLE_QUOTE; más de una (también
     duplicados equivalentes o recepciones distintas) -> MULTIPLE_ELIGIBLE_QUOTES. Nunca se elige la
     mejor cuota, la más nueva, la primera ni un bookmaker. Se informan los motivos de rechazo.
  3. La cuota cualificada tiene que ser exactamente la del objetivo (defensivo).
  4. Probabilidades: del mismo prediction_id, fixture, mercado, selección y línea; forma según el
     mercado (abajo). Se pasan tal cual al motor, que las valida (ProbabilityError -> NO_VALUE).
  5. Cuota: el Decimal ofrecido se conserva intacto; al motor va su double más cercano, solo si es
     finito y > 1 (si no, ODDS_NOT_REPRESENTABLE_AS_DOUBLE). El resultado es aritmética double
     aproximada (NUMERIC_BASIS), nunca un EV exacto en Decimal.

Probabilidades por mercado (masas de la selección pedida, no del mercado entero):
  1X2_FT90, BTTS_FT90 y OVER_UNDER_FT90 en línea n.5: p_win; p_lose y p_push tienen que ser None
    -> evaluate_binary_value (p_lose = 1 - p_win lo calcula el motor; no hay PUSH).
  OVER_UNDER_FT90 en línea entera n.0: p_win, p_lose y p_push explícitas (p_push = P(goles == n))
    -> evaluate_push_value, que exige |suma - 1| <= 1e-9. Nunca se infiere la masa PUSH.

Salida inmutable y determinista: no depende del orden de las cuotas (solo se informan recuentos y
motivos ordenados, no posiciones).
"""

import math
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from research.market_value_math import value as market_value
from research.quote_temporal_eligibility import MARKET_SELECTIONS, OVER_UNDER
from research.quote_temporal_eligibility import REASONS as ELIGIBILITY_REASONS
from research.quote_temporal_eligibility import (SETTLEMENT_CONVENTION_FT90, SUPPORTED_LINE_TEXTS, EvaluationContext, QualifiedQuote,
                                                 QuoteTarget, evaluate_quote_eligibility)

BRIDGE_ID = "QUOTE_VALUE_BRIDGE"
BRIDGE_VERSION = "1"
VALUE_EVALUATED, NO_VALUE, UNSUPPORTED = "VALUE_EVALUATED", "NO_VALUE", "UNSUPPORTED"
NUMERIC_BASIS = "IEEE754_DOUBLE_APPROXIMATE_FROM_NEAREST_DOUBLE_OF_DECIMAL_ODDS"
_LINE_DIGIT_BOUND = 30  # una línea con forma: menos de 10**31 y como mucho 30 decimales (antes de operar con ella)

# Motivos, en el orden en que se informan
UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"
UNSUPPORTED_QUARTER_LINE = "UNSUPPORTED_QUARTER_LINE"
UNSUPPORTED_LINE = "UNSUPPORTED_LINE"
NO_ELIGIBLE_QUOTE = "NO_ELIGIBLE_QUOTE"
MULTIPLE_ELIGIBLE_QUOTES = "MULTIPLE_ELIGIBLE_QUOTES"
QUALIFIED_IDENTITY_MISMATCH = "QUALIFIED_IDENTITY_MISMATCH"
PROBABILITY_MISSING = "PROBABILITY_MISSING"
PROBABILITY_MALFORMED = "PROBABILITY_MALFORMED"
PROBABILITY_IDENTITY_MISMATCH = "PROBABILITY_IDENTITY_MISMATCH"
PROBABILITY_SHAPE_INVALID = "PROBABILITY_SHAPE_INVALID"
PROBABILITY_INVALID = "PROBABILITY_INVALID"
ODDS_NOT_REPRESENTABLE_AS_DOUBLE = "ODDS_NOT_REPRESENTABLE_AS_DOUBLE"
REASONS = (UNSUPPORTED_MARKET, UNSUPPORTED_QUARTER_LINE, UNSUPPORTED_LINE, NO_ELIGIBLE_QUOTE, MULTIPLE_ELIGIBLE_QUOTES,
           QUALIFIED_IDENTITY_MISMATCH, PROBABILITY_MISSING, PROBABILITY_MALFORMED, PROBABILITY_IDENTITY_MISMATCH,
           PROBABILITY_SHAPE_INVALID, PROBABILITY_INVALID, ODDS_NOT_REPRESENTABLE_AS_DOUBLE)

INPUT_BASIS = "SYNTHETIC_CALLER_SUPPLIED_QUOTES_AND_PROBABILITIES"
PIT_STATUS = "NOT_CERTIFIED"
LIMITATIONS = (
    "SYNTHETIC_QUOTES_NOT_PROVIDER_AUTHENTICATED",
    "RECEIVED_AT_CALLER_SUPPLIED_NOT_PROVEN_KNOWLEDGE_TIME",
    "PROBABILITIES_CALLER_SUPPLIED_NOT_CALIBRATED",
    "DOUBLE_PRECISION_APPROXIMATION_NOT_DECIMAL_EXACT",
    "QUARTER_AND_SPLIT_LINES_UNSUPPORTED",
    "NO_STAKE_BANKROLL_PICK_OR_RECOMMENDATION",
    "NOT_TRUE_PIT_CERTIFIED",
)


@dataclass(frozen=True)
class SelectionProbabilities:
    """Masas del llamador para la selección pedida de UNA predicción (no se estiman aquí)."""

    prediction_id: str
    fixture_id: int
    market: str
    selection: str
    line: str | None
    p_win: float | None
    p_lose: float | None = None  # solo en Over/Under de línea entera
    p_push: float | None = None  # solo en Over/Under de línea entera


@dataclass(frozen=True)
class QuoteValueResult:
    bridge_id: str
    bridge_version: str
    status: str  # VALUE_EVALUATED | NO_VALUE | UNSUPPORTED
    reasons: tuple[str, ...]  # vacío si y solo si VALUE_EVALUATED
    prediction_id: str
    target: QuoteTarget
    quotes_received: int  # cuántas QuoteObservation entraron (0 si UNSUPPORTED: no se evalúan)
    eligible_count: int | None  # None si no se llegó a evaluar la elegibilidad
    eligibility_reasons: tuple[str, ...]  # motivos de rechazo de elegibilidad, distintos y en el orden de REASONS de 102C
    qualified: QualifiedQuote | None  # la única cuota cualificada, si la hubo
    offered_decimal_odds: Decimal | None  # el Decimal ofrecido, intacto
    engine_decimal_odds: float | None  # su double más cercano, el que recibe el motor
    odds_conversion_exact: bool | None  # True solo si el double representa exactamente el Decimal
    valuation: market_value.ValueEvaluation | None  # el resultado del motor existente, tal cual; solo en VALUE_EVALUATED
    engine_error: str | None  # mensaje de ProbabilityError del motor, si la rechazó
    numeric_basis: str
    settlement_convention: str
    input_basis: str
    pit_status: str
    limitations: tuple[str, ...]


def _line_kind(line) -> str | None:
    """'HALF', 'INTEGER', 'QUARTER' u otro motivo de UNSUPPORTED (None si no es una línea con forma)."""
    if isinstance(line, str) and line in SUPPORTED_LINE_TEXTS:
        return "INTEGER" if line.endswith(".0") else "HALF"
    if not isinstance(line, str):
        return None
    try:
        value = Decimal(line)
    except (InvalidOperation, ValueError):
        return None
    # Sin aritmética Decimal (con 1E+30 o 9E+999999999 lanzaba InvalidOperation u Overflow, MOD-DEV-103A-R1): fuera de una
    # magnitud y una precisión acotadas no es una línea; dentro, la parte fraccionaria exacta decide si es de cuarto
    if not value.is_finite() or value.is_signed() or value.adjusted() > _LINE_DIGIT_BOUND or value.as_tuple().exponent < -_LINE_DIGIT_BOUND:
        return None
    return "QUARTER" if Fraction(value) % 1 in (Fraction(1, 4), Fraction(3, 4)) else None


def _unsupported(target) -> str | None:
    if not isinstance(target, QuoteTarget):
        return None  # lo rechaza evaluate_quote_eligibility con su error
    if not isinstance(target.market, str) or target.market not in MARKET_SELECTIONS:
        return UNSUPPORTED_MARKET
    if target.market == OVER_UNDER:
        kind = _line_kind(target.line)
        if kind == "QUARTER":
            return UNSUPPORTED_QUARTER_LINE
        if kind is None:
            return UNSUPPORTED_LINE
    return None


def _result(context, status, reasons, *, quotes_received=0, eligible_count=None, eligibility_reasons=(), qualified=None,
            engine_odds=None, exact=None, valuation=None, engine_error=None) -> QuoteValueResult:
    return QuoteValueResult(
        bridge_id=BRIDGE_ID, bridge_version=BRIDGE_VERSION, status=status, reasons=tuple(r for r in REASONS if r in reasons),
        prediction_id=context.prediction_id, target=context.target, quotes_received=quotes_received, eligible_count=eligible_count,
        eligibility_reasons=eligibility_reasons, qualified=qualified, offered_decimal_odds=qualified.decimal_odds if qualified else None,
        engine_decimal_odds=engine_odds, odds_conversion_exact=exact, valuation=valuation, engine_error=engine_error,
        numeric_basis=NUMERIC_BASIS, settlement_convention=SETTLEMENT_CONVENTION_FT90, input_basis=INPUT_BASIS,
        pit_status=PIT_STATUS, limitations=LIMITATIONS,
    )


def _probability_reasons(p, context: EvaluationContext, integer_line: bool) -> set[str]:
    if p is None:
        return {PROBABILITY_MISSING}
    if not isinstance(p, SelectionProbabilities):
        return {PROBABILITY_MALFORMED}
    t = context.target
    if (p.prediction_id, p.fixture_id, p.market, p.selection, p.line) != (context.prediction_id, t.fixture_id, t.market, t.selection, t.line) \
            or type(p.fixture_id) is not int:
        return {PROBABILITY_IDENTITY_MISMATCH}
    masses = (p.p_win, p.p_lose, p.p_push) if integer_line else (p.p_win,)
    if any(m is None for m in masses):
        return {PROBABILITY_MISSING}
    if not integer_line and (p.p_lose is not None or p.p_push is not None):
        return {PROBABILITY_SHAPE_INVALID}  # en binario el motor calcula p_lose y no hay PUSH: no se ignora lo que sobra
    return set()


def evaluate_quote_value(context: EvaluationContext, quotes, probabilities) -> QuoteValueResult:
    """Cuota justa y EV de la única cuota elegible del objetivo, con el motor existente (ver el módulo)."""
    unsupported = _unsupported(getattr(context, "target", None)) if isinstance(context, EvaluationContext) else None
    if unsupported:
        return _result(context, UNSUPPORTED, {unsupported})

    report = evaluate_quote_eligibility(context, quotes)  # valida context y quotes; lanza EligibilityInputError
    found = Counter(r for o in report.outcomes for r in o.reasons)
    eligibility_reasons = tuple(r for r in ELIGIBILITY_REASONS if r in found)
    eligible = report.eligible
    common = {"quotes_received": len(report.outcomes), "eligible_count": len(eligible), "eligibility_reasons": eligibility_reasons}
    if len(eligible) != 1:
        return _result(context, NO_VALUE, {NO_ELIGIBLE_QUOTE if not eligible else MULTIPLE_ELIGIBLE_QUOTES}, **common)

    (qualified,) = eligible
    t = context.target
    if (report.settlement_convention != SETTLEMENT_CONVENTION_FT90
            or (qualified.provider, qualified.bookmaker_id, qualified.provider_event_id, qualified.fixture_id, qualified.market,
                qualified.selection, qualified.line) != (t.provider, t.bookmaker_id, t.provider_event_id, t.fixture_id, t.market, t.selection, t.line)):
        return _result(context, NO_VALUE, {QUALIFIED_IDENTITY_MISMATCH}, qualified=qualified, **common)

    integer_line = t.market == OVER_UNDER and _line_kind(t.line) == "INTEGER"
    problems = _probability_reasons(probabilities, context, integer_line)
    if problems:
        return _result(context, NO_VALUE, problems, qualified=qualified, **common)

    try:
        engine_odds = float(qualified.decimal_odds)  # el double más cercano; un Decimal enorme da inf
    except (OverflowError, ValueError):
        engine_odds = math.inf
    if not math.isfinite(engine_odds) or not engine_odds > 1:
        return _result(context, NO_VALUE, {ODDS_NOT_REPRESENTABLE_AS_DOUBLE}, qualified=qualified, **common)
    exact = Decimal(engine_odds) == qualified.decimal_odds

    p = probabilities
    try:
        if integer_line:
            valuation = market_value.evaluate_push_value(p_win=p.p_win, p_lose=p.p_lose, p_push=p.p_push, decimal_odds=engine_odds)
        else:
            valuation = market_value.evaluate_binary_value(p_win=p.p_win, decimal_odds=engine_odds)
    except market_value.ProbabilityError as exc:
        return _result(context, NO_VALUE, {PROBABILITY_INVALID}, qualified=qualified, engine_odds=engine_odds, exact=exact,
                       engine_error=str(exc), **common)
    return _result(context, VALUE_EVALUATED, set(), qualified=qualified, engine_odds=engine_odds, exact=exact, valuation=valuation, **common)
