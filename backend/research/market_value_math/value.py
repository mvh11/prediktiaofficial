"""Cuota justa teórica y valor esperado neto por unidad apostada (MOD-DEV-100). Puro: sin BD, red, disco ni app.

Transformación matemática de números que aporta el llamador. No estima probabilidades, no obtiene ni
autentica cuotas, no recomienda apuestas ni importes, no calcula Kelly ni CLV y no evalúa modelos ni
calibración. Un EV > 0 es una esperanza matemática sobre entradas no verificadas, no un beneficio real
ni realizable.

Interfaz (argumentos solo por nombre, así no se confunde el orden de las masas):
  evaluate_binary_value(*, p_win, decimal_odds)               liquidación WIN/LOSE (BINARY_WIN_LOSE)
  evaluate_push_value(*, p_win, p_lose, p_push, decimal_odds)  liquidación WIN/LOSE/PUSH (WIN_LOSE_PUSH)
Ambas devuelven un ValueEvaluation inmutable o lanzan ProbabilityError / OddsError (subclases de
MarketValueInputError, que es ValueError). No hay resultado parcial.

Convención (CONVENTION_VERSION): cuota decimal = pago total por unidad apostada, apuesta incluida; el
retorno neto es decimal_odds - 1 si gana, -1 si pierde y 0 si es PUSH (se devuelve la apuesta).
  Binario:  EV = p_win * decimal_odds - 1, igual en álgebra a p_win * (decimal_odds - 1) - (1 - p_win).
            fair_odds = 1 / p_win (p_win > 0).
  PUSH:     EV = p_win * (decimal_odds - 1) - p_lose.
            fair_odds = 1 + p_lose / p_win (p_win > 0): la cuota con EV = 0. Con masa PUSH no es 1 / p_win
            (0.4 / 0.4 / 0.2 -> 2.0, no 2.5).
fair_odds es la cuota de equilibrio teórica, no una cuota ofrecida ni cotizable.

Estados de fair_odds (fair_odds es None salvo en DEFINED; nunca se informa inf ni un precio arbitrario):
  DEFINED                          finita. Puede valer exactamente 1.0 (p_win = 1 en binario, p_lose = 0
                                   con p_win > 0 en PUSH): es la frontera matemática de equilibrio, no una
                                   cuota admisible (decimal_odds > 1), y toda cuota admisible da EV > 0.
  UNDEFINED_NO_WIN_PROBABILITY     p_win = 0 con pérdida posible: ninguna cuota finita compensa la pérdida.
                                   EV = -1 en binario y -p_lose en PUSH, para cualquier cuota.
  NON_UNIQUE_ALWAYS_PUSH           p_win = p_lose = 0 (todo PUSH): EV = 0 con cualquier cuota y no hay un
                                   precio justo único.
  NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE  p_win > 0 tan pequeña que la cuota justa no es un double finito.
El EV siempre es un double finito: |EV| <= max(decimal_odds, 1) con probabilidades en [0, 1].

Validación (sin recorte, normalización, imputación ni conversión de valores no válidos a 0):
  Probabilidades: int o float (bool no; str, Decimal, Fraction u otro tipo tampoco), finitas, en [0, 1].
    El rango se comprueba antes que la finitud: un int enorme (10**400) es ProbabilityError y no
    OverflowError. Se informan como double (int 0/1 -> 0.0/1.0; -0.0 -> 0.0).
    En los mensajes de error, un int con más dígitos que sys.get_int_max_str_digits() no se pasa a
    decimal: se describe por su signo y su número de bits (MOD-DEV-100-D1). Si falla el repr de otro valor
    ya rechazado (un Fraction, list, tuple o dict con un int así), se describe solo por su tipo:
    <Fraction no representable> (MOD-DEV-100-D2).
  PUSH: |math.fsum(p_win, p_lose, p_push) - 1| <= SUM_TOLERANCE (1e-9, la de research.m510a.evaluate y
    research.goals_markets). Las masas no se reescalan: el EV usa las suministradas.
  decimal_odds: int o float (bool no), > 1 y representable como double finito (NaN, +-inf o un int fuera
    del rango del double son OddsError). Un int se evalúa como el double más cercano (exacto hasta 2**53).
Precisión: aritmética double; el EV puede diferir de su valor exacto en unos pocos ulp de
max(p_win * decimal_odds, 1). No se afirma aritmética exacta más allá de esa representación.

Procedencia: INPUT_BASIS y LIMITATIONS lo dejan explícito; PIT_STATUS = NOT_CERTIFIED.
"""

import math
from dataclasses import dataclass

EVALUATOR_ID = "MARKET_VALUE_MATH"
EVALUATOR_VERSION = "1"
CONVENTION_VERSION = "DECIMAL_ODDS_NET_RETURN_PER_UNIT_STAKE_PUSH_REFUNDS_STAKE_V1"
SUM_TOLERANCE = 1e-9  # la de research.m510a.evaluate y research.goals_markets

BINARY_WIN_LOSE, WIN_LOSE_PUSH = "BINARY_WIN_LOSE", "WIN_LOSE_PUSH"
DEFINED = "DEFINED"
UNDEFINED_NO_WIN_PROBABILITY = "UNDEFINED_NO_WIN_PROBABILITY"
NON_UNIQUE_ALWAYS_PUSH = "NON_UNIQUE_ALWAYS_PUSH"
NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE = "NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE"

INPUT_BASIS = "CALLER_SUPPLIED_PROBABILITIES_AND_DECIMAL_ODDS"
PIT_STATUS = "NOT_CERTIFIED"
LIMITATIONS = (
    "PROBABILITIES_SUPPLIED_BY_CALLER_NOT_VALIDATED_FORECASTS",
    "ODDS_SUPPLIED_BY_CALLER_NOT_AUTHENTICATED_QUOTES",
    "NO_AS_OF_PRICE_TIMESTAMP",
    "NO_LIQUIDITY_AVAILABILITY_EXECUTION_OR_SETTLEMENT_GUARANTEE",
    "EXPECTATION_IS_NOT_REALIZED_OR_REALIZABLE_PROFIT",
    "NO_STAKE_SIZING_OR_RECOMMENDATION",
    "NOT_TRUE_PIT_CERTIFIED",
)


class MarketValueInputError(ValueError):
    """Una entrada no cumple el contrato numérico (error del llamador)."""


class ProbabilityError(MarketValueInputError):
    """Una probabilidad no es un int/float finito en [0, 1], o las masas PUSH no suman 1."""


class OddsError(MarketValueInputError):
    """La cuota decimal no es un int/float > 1 representable como double finito."""


@dataclass(frozen=True)
class ValueEvaluation:
    evaluator_id: str
    evaluator_version: str
    convention_version: str
    settlement: str  # BINARY_WIN_LOSE | WIN_LOSE_PUSH
    p_win: float  # suministrada
    p_lose: float  # binario: 1 - p_win (complemento calculado); WIN_LOSE_PUSH: suministrada
    p_push: float | None  # None en binario (el resultado no existe); WIN_LOSE_PUSH: suministrada
    decimal_odds: float  # suministrada, como double
    fair_odds: float | None  # solo en DEFINED
    fair_odds_status: str  # DEFINED | UNDEFINED_NO_WIN_PROBABILITY | NON_UNIQUE_ALWAYS_PUSH | NOT_COMPUTABLE_...
    expected_net_return: float  # EV por unidad apostada, siempre finito
    input_basis: str
    pit_status: str
    limitations: tuple[str, ...]


def _input_repr(value) -> str:
    """repr para los mensajes de error; un int más allá del límite de CPython se describe por sus bits (MOD-DEV-100-D1)
    y otro valor ya rechazado cuyo repr falla, solo por su tipo (MOD-DEV-100-D2)."""
    try:
        return repr(value)
    except ValueError:  # int -> str: "Exceeds the limit (4300 digits)", directo o dentro de Fraction, list, dict...
        if isinstance(value, int):
            return f"<int {'negativo ' if value < 0 else ''}de {value.bit_length()} bits>"
    except (OverflowError, RecursionError):  # RecursionError: p. ej. una list anidada a gran profundidad
        pass
    return f"<{type(value).__name__} no representable>"


def _probability(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProbabilityError(f"{name} debe ser int o float, no {type(value).__name__}: {_input_repr(value)}")
    if not 0 <= value <= 1 or not math.isfinite(value):  # el rango primero: math.isfinite(10**400) desborda
        raise ProbabilityError(f"{name} fuera de [0, 1] o no finita: {_input_repr(value)}")
    return float(value) + 0.0  # -0.0 -> 0.0


def _decimal_odds(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OddsError(f"decimal_odds debe ser int o float, no {type(value).__name__}: {_input_repr(value)}")
    if not value > 1:  # también NaN
        raise OddsError(f"decimal_odds debe ser > 1: {_input_repr(value)}")
    try:
        odds = float(value)
    except OverflowError:
        raise OddsError(f"decimal_odds no es representable como double finito: {_input_repr(value)}") from None
    if not math.isfinite(odds):
        raise OddsError(f"decimal_odds no finita: {_input_repr(value)}")
    return odds


def _fair_odds(p_win: float, p_lose: float, price) -> tuple[float | None, str]:
    """(cuota justa, estado); price(p_win) solo se evalúa con p_win > 0."""
    if p_win == 0:
        return None, NON_UNIQUE_ALWAYS_PUSH if p_lose == 0 else UNDEFINED_NO_WIN_PROBABILITY
    fair = price(p_win)
    return (fair, DEFINED) if math.isfinite(fair) else (None, NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE)


def _evaluation(settlement, p_win, p_lose, p_push, odds, fair, status, ev) -> ValueEvaluation:
    if not math.isfinite(ev):
        raise AssertionError("EV no finito con entradas válidas")
    return ValueEvaluation(
        evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION, convention_version=CONVENTION_VERSION,
        settlement=settlement, p_win=p_win, p_lose=p_lose, p_push=p_push, decimal_odds=odds, fair_odds=fair,
        fair_odds_status=status, expected_net_return=ev, input_basis=INPUT_BASIS, pit_status=PIT_STATUS, limitations=LIMITATIONS,
    )


def evaluate_binary_value(*, p_win, decimal_odds) -> ValueEvaluation:
    """WIN/LOSE: p_lose = 1 - p_win; EV = p_win * decimal_odds - 1; fair_odds = 1 / p_win (ver el módulo)."""
    p, odds = _probability(p_win, "p_win"), _decimal_odds(decimal_odds)
    fair, status = _fair_odds(p, 1.0 - p, lambda w: 1.0 / w)
    return _evaluation(BINARY_WIN_LOSE, p, 1.0 - p, None, odds, fair, status, p * odds - 1.0)


def evaluate_push_value(*, p_win, p_lose, p_push, decimal_odds) -> ValueEvaluation:
    """WIN/LOSE/PUSH: EV = p_win * (decimal_odds - 1) - p_lose; fair_odds = 1 + p_lose / p_win (ver el módulo)."""
    win, lose, push = _probability(p_win, "p_win"), _probability(p_lose, "p_lose"), _probability(p_push, "p_push")
    if abs(math.fsum((win, lose, push)) - 1) > SUM_TOLERANCE:
        raise ProbabilityError(f"p_win + p_lose + p_push no suman 1 (tolerancia {SUM_TOLERANCE}): {(p_win, p_lose, p_push)!r}")
    odds = _decimal_odds(decimal_odds)
    fair, status = _fair_odds(win, lose, lambda w: 1.0 + lose / w)
    return _evaluation(WIN_LOSE_PUSH, win, lose, push, odds, fair, status, win * (odds - 1.0) - lose)
