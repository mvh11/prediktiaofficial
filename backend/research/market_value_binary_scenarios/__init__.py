"""Sensibilidad del valor binario WIN/LOSE a un rango de probabilidades declarado por el llamador (MOD-DEV-106A).
Puro: sin BD, red, disco ni app.

No hay un segundo motor económico: cada extremo lo evalúa research.market_value_math.value.evaluate_binary_value
(MOD-DEV-100) con el valor tal cual lo pasó el llamador, sin coerción, redondeo, recorte ni normalización previos, y
su ValueEvaluation se conserva intacto (procedencia, versión, fair_odds y fair_odds_status incluidos). ev_low y ev_high
son el expected_net_return de esos resultados, no un recálculo. Este módulo no hace aritmética.

Interfaz (argumentos solo por nombre):
  evaluate_binary_scenario_range(*, p_low, p_high, decimal_odds) -> BinaryScenarioRange
Orden: evaluate_binary_value(p_win=p_low, ...) y luego evaluate_binary_value(p_win=p_high, ...); sus ProbabilityError /
OddsError se propagan sin envolver (una probabilidad inválida de p_low se informa antes que una cuota inválida). Solo
con ambos extremos válidos se exige p_low <= p_high (sobre las probabilidades validadas que informa el motor); unos
extremos invertidos son ProbabilityError y nunca se intercambian ni se ordenan. No hay resultado parcial.

El rango es un escenario CALLER_DECLARED_NOT_CALIBRATED: no es una previsión, un intervalo de confianza ni una
calibración. Con p_low <= p_high el EV binario (p_win * decimal_odds - 1 en double) no decrece, así que
ev_low <= ev_high; se comprueba como invariante del motor (AssertionError si fallara, nunca se corrige). La cuota justa
de cada extremo se queda en su ValueEvaluation: no se construye un rango combinado de cuotas justas (con p = 0 es
UNDEFINED_NO_WIN_PROBABILITY y con p > 0 diminuta NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE, ambas con fair_odds None).
No hay umbrales, juicio de valor robusto, selección, stakes, Kelly ni recomendaciones. PIT_STATUS = NOT_CERTIFIED.
"""

from dataclasses import dataclass

from research.market_value_math import value as market_value

DIAGNOSTIC_ID = "MARKET_VALUE_BINARY_SCENARIOS"
DIAGNOSTIC_VERSION = "1"
SCENARIO_BASIS = "CALLER_DECLARED_NOT_CALIBRATED"
INPUT_BASIS = "CALLER_SUPPLIED"
PIT_STATUS = "NOT_CERTIFIED"
POINT, RANGE = "POINT", "RANGE"  # forma del escenario declarado: p_low == p_high o p_low < p_high
LIMITATIONS = (
    "SCENARIO_BOUNDS_CALLER_DECLARED_NOT_FORECASTS",
    "NOT_A_CONFIDENCE_OR_CREDIBLE_INTERVAL",
    "PROBABILITIES_NOT_CALIBRATED",
    "ODDS_SUPPLIED_BY_CALLER_NOT_AUTHENTICATED_QUOTES",
    "NO_COMBINED_FAIR_ODDS_RANGE",
    "NO_THRESHOLD_ROBUSTNESS_JUDGMENT_SELECTION_OR_RECOMMENDATION",
    "NO_STAKE_SIZING_OR_KELLY",
    "NOT_TRUE_PIT_CERTIFIED",
)


@dataclass(frozen=True)
class BinaryScenarioRange:
    diagnostic_id: str
    diagnostic_version: str
    p_low: object  # el valor del llamador tal cual, ya validado por el motor
    p_high: object
    low: market_value.ValueEvaluation  # resultado del motor para p_low, intacto
    high: market_value.ValueEvaluation  # resultado del motor para p_high, intacto
    ev_low: float  # low.expected_net_return
    ev_high: float  # high.expected_net_return
    scenario_shape: str  # POINT | RANGE
    scenario_basis: str
    input_basis: str
    pit_status: str
    limitations: tuple[str, ...]


def evaluate_binary_scenario_range(*, p_low, p_high, decimal_odds) -> BinaryScenarioRange:
    """EV y cuota justa de cada extremo con el motor existente, sin recalcular nada (ver el módulo)."""
    low = market_value.evaluate_binary_value(p_win=p_low, decimal_odds=decimal_odds)
    high = market_value.evaluate_binary_value(p_win=p_high, decimal_odds=decimal_odds)
    if not low.p_win <= high.p_win:
        raise market_value.ProbabilityError(f"extremos invertidos: p_low {low.p_win!r} > p_high {high.p_win!r} (no se intercambian)")
    if not low.expected_net_return <= high.expected_net_return:
        raise AssertionError("EV no monótono con p_low <= p_high")
    return BinaryScenarioRange(
        diagnostic_id=DIAGNOSTIC_ID, diagnostic_version=DIAGNOSTIC_VERSION, p_low=p_low, p_high=p_high, low=low, high=high,
        ev_low=low.expected_net_return, ev_high=high.expected_net_return, scenario_shape=POINT if low.p_win == high.p_win else RANGE,
        scenario_basis=SCENARIO_BASIS, input_basis=INPUT_BASIS, pit_status=PIT_STATUS, limitations=LIMITATIONS,
    )
