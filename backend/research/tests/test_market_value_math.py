"""MOD-DEV-100: cuota justa y valor esperado por unidad apostada (WIN/LOSE y WIN/LOSE/PUSH). Sin BD, sin red, sin app.
    python -m pytest research/tests/test_market_value_math.py -q   (desde backend/)
Probabilidades y cuotas sintéticas; los valores esperados están calculados a mano (no derivados de la
implementación). Ninguna cuota es una cotización auténtica de una casa de apuestas ni un histórico PIT."""

import dataclasses
import math
import os
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction

import pytest

from research.market_value_math.value import (BINARY_WIN_LOSE, CONVENTION_VERSION, DEFINED, EVALUATOR_ID, EVALUATOR_VERSION,
                                              INPUT_BASIS, LIMITATIONS, NON_UNIQUE_ALWAYS_PUSH,
                                              NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE, PIT_STATUS, SUM_TOLERANCE,
                                              UNDEFINED_NO_WIN_PROBABILITY, WIN_LOSE_PUSH, MarketValueInputError, OddsError,
                                              ProbabilityError, ValueEvaluation, evaluate_binary_value, evaluate_push_value)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPROX = 1e-15  # unos pocos ulp de valores de orden 1
MAX = sys.float_info.max


def binary(p, odds):
    return evaluate_binary_value(p_win=p, decimal_odds=odds)


def push(win, lose, p_push, odds):
    return evaluate_push_value(p_win=win, p_lose=lose, p_push=p_push, decimal_odds=odds)


# --- A/B/C/D: binario, cuota justa, EV a mano y equilibrio -------------------------------------------

def test_binary_fair_odds_identity_and_exact_zero_ev_at_fair_odds():
    r = binary(0.25, 4.0)
    assert (r.settlement, r.p_win, r.p_lose, r.p_push, r.decimal_odds) == (BINARY_WIN_LOSE, 0.25, 0.75, None, 4.0)
    assert (r.fair_odds, r.fair_odds_status, r.expected_net_return) == (4.0, DEFINED, 0.0)  # 0.25 * 4 - 1 = 0, exacto
    assert binary(0.5, 3.0).fair_odds == 2.0 and binary(0.8, 3.0).fair_odds == 1.25


def test_binary_ev_hand_calculations():
    assert binary(0.5, 2.5).expected_net_return == 0.25  # 0.5 * 1.5 - 0.5, exacto
    assert binary(0.5, 1.8).expected_net_return == pytest.approx(-0.1, abs=APPROX)  # 0.5 * 0.8 - 0.5
    assert binary(0.6, 2.0).expected_net_return == pytest.approx(0.2, abs=APPROX)  # 0.6 * 1 - 0.4
    assert binary(0.1, 5.0).expected_net_return == pytest.approx(-0.5, abs=APPROX)  # 0.1 * 4 - 0.9


@pytest.mark.parametrize("p", [0, 0.1, 0.25, 1 / 3, 0.5, 0.7, 0.999, 1])
@pytest.mark.parametrize("odds", [1.01, 1.5, 2.0, 3.3, 10, 1000.5])
def test_binary_ev_equals_the_win_minus_loss_form(p, odds):
    # p * o - 1 y p * (o - 1) - (1 - p) son iguales en álgebra; en double difieren en pocos ulp de max(o, 1)
    r = binary(p, odds)
    assert abs(r.expected_net_return - (p * (odds - 1) - (1 - p))) <= APPROX * (1 + odds)


def test_binary_odds_just_above_and_below_break_even():
    above, below = math.nextafter(4.0, math.inf), math.nextafter(4.0, 0.0)
    assert binary(0.25, above).expected_net_return > 0 > binary(0.25, below).expected_net_return
    assert binary(0.25, above).expected_net_return == 2 ** -52 and binary(0.25, below).expected_net_return == -(2 ** -53)  # exactos


# --- E/F: p_win = 0 y p_win = 1 ---------------------------------------------------------------------

@pytest.mark.parametrize("p", [0, 0.0, -0.0])
def test_binary_zero_win_probability_has_no_fair_price(p):
    for odds in (1.01, 2.0, 1e300):
        r = binary(p, odds)
        assert (r.fair_odds, r.fair_odds_status, r.expected_net_return, r.p_win, r.p_lose) == (None, UNDEFINED_NO_WIN_PROBABILITY, -1.0, 0.0, 1.0)
        assert math.copysign(1, r.p_win) == 1


@pytest.mark.parametrize("p", [1, 1.0])
def test_binary_certain_win_break_even_is_the_boundary_one(p):
    r = binary(p, 1.5)
    assert (r.fair_odds, r.fair_odds_status, r.expected_net_return, r.p_lose) == (1.0, DEFINED, 0.5, 0.0)
    assert binary(p, math.nextafter(1.0, 2.0)).expected_net_return == 2 ** -52  # toda cuota admisible da EV > 0


# --- G/H: PUSH con masa no nula -----------------------------------------------------------------------

def test_push_fair_odds_differ_from_naive_inverse_win_probability():
    r = push(0.4, 0.4, 0.2, 2.0)
    assert (r.settlement, r.p_win, r.p_lose, r.p_push) == (WIN_LOSE_PUSH, 0.4, 0.4, 0.2)
    assert (r.fair_odds, r.fair_odds_status) == (2.0, DEFINED)  # 1 + 0.4 / 0.4
    assert r.fair_odds != 1 / 0.4 == 2.5  # la ingenua 1 / p_win daría EV > 0 en 2.5, no 0
    assert push(0.4, 0.4, 0.2, 2.5).expected_net_return == pytest.approx(0.2, abs=APPROX)
    other = push(0.5, 0.25, 0.25, 3.0)
    assert (other.fair_odds, other.expected_net_return) == (1.5, 0.75)  # 1 + 0.25 / 0.5; 0.5 * 2 - 0.25, exactos
    assert other.fair_odds != 1 / 0.5


def test_push_ev_hand_calculation():
    assert push(0.4, 0.4, 0.2, 2.0).expected_net_return == 0.0  # 0.4 * 1 - 0.4, exacto
    assert push(0.4, 0.4, 0.2, 2.2).expected_net_return == pytest.approx(0.08, abs=APPROX)  # 0.4 * 1.2 - 0.4
    assert push(0.3, 0.5, 0.2, 1.5).expected_net_return == pytest.approx(-0.35, abs=APPROX)  # 0.3 * 0.5 - 0.5
    above, below = math.nextafter(2.0, math.inf), math.nextafter(2.0, 0.0)
    assert push(0.4, 0.4, 0.2, above).expected_net_return > 0 > push(0.4, 0.4, 0.2, below).expected_net_return


@pytest.mark.parametrize("win, lose, p_push", [(0.4, 0.4, 0.2), (0.25, 0.5, 0.25), (0.1, 0.7, 0.2), (0.3, 0.6, 0.1)])
def test_push_ev_is_zero_at_its_fair_odds(win, lose, p_push):
    fair = push(win, lose, p_push, 2.0).fair_odds
    assert abs(push(win, lose, p_push, fair).expected_net_return) <= APPROX


def test_push_without_push_mass_matches_binary():
    for p, odds in ((0.25, 4.0), (0.3, 2.7), (0.9, 1.05)):
        b, w = binary(p, odds), push(p, 1 - p, 0, odds)
        assert abs(w.expected_net_return - b.expected_net_return) <= APPROX * (1 + odds)
        assert w.fair_odds == pytest.approx(b.fair_odds, rel=1e-15)


# --- I/J/K: todo PUSH, sin victoria posible, sin pérdida posible ---------------------------------------

def test_all_push_has_zero_ev_and_no_unique_fair_price():
    for odds in (1.01, 2.0, 1e300):
        r = push(0, 0, 1, odds)
        assert (r.fair_odds, r.fair_odds_status, r.expected_net_return) == (None, NON_UNIQUE_ALWAYS_PUSH, 0.0)


def test_zero_win_positive_loss_has_no_fair_price_and_loses_p_lose():
    for odds in (1.01, 50.0, 1e300):
        r = push(0, 0.6, 0.4, odds)
        assert (r.fair_odds, r.fair_odds_status, r.expected_net_return) == (None, UNDEFINED_NO_WIN_PROBABILITY, -0.6)


def test_zero_loss_positive_win_break_even_is_the_boundary_one():
    r = push(0.3, 0, 0.7, 1.5)
    assert (r.fair_odds, r.fair_odds_status) == (1.0, DEFINED)  # frontera matemática, no una cuota admisible
    assert r.expected_net_return == pytest.approx(0.15, abs=APPROX)  # 0.3 * 0.5
    assert push(0.3, 0, 0.7, math.nextafter(1.0, 2.0)).expected_net_return > 0


# --- L/M/N: masas y probabilidades no válidas ---------------------------------------------------------

@pytest.mark.parametrize("masses", [(0.5, 0.4, 0.2), (0.4, 0.4, 0.1), (0, 0, 0), (1, 1, 0), (0.4, 0.4, 0.2 + 2e-9), (0.4, 0.4, 0.2 - 2e-9)])
def test_inconsistent_push_masses_are_rejected_not_normalized(masses):
    with pytest.raises(ProbabilityError):
        push(*masses, 2.0)


def test_masses_within_tolerance_are_used_as_supplied():
    r = push(0.4, 0.4, 0.2 + 5e-10, 2.0)
    assert r.p_push == 0.2 + 5e-10 and SUM_TOLERANCE == 1e-9  # no se reescalan
    assert (r.fair_odds, r.expected_net_return) == (2.0, 0.0)


@pytest.mark.parametrize("masses", [(0.5, 0.6, -0.1), (0.6, -0.1, 0.5), (-0.1, 0.6, 0.5), (0.5, 0.5, -0.0000001)])
def test_negative_masses_are_rejected_even_if_the_sum_is_one(masses):
    with pytest.raises(ProbabilityError):
        push(*masses, 2.0)


BAD_PROBABILITIES = [-0.1, -1e-300, -1, 1.0000000000000002, 1.5, 2, "0.5", Decimal("0.5"), Fraction(1, 2), None, [0.5], 1j]


@pytest.mark.parametrize("p", BAD_PROBABILITIES)
def test_invalid_probabilities_are_rejected_without_clamping(p):
    with pytest.raises(ProbabilityError):
        binary(p, 2.0)
    for masses in ((p, 0.5, 0.5), (0.5, p, 0.5), (0.5, 0.5, p)):
        with pytest.raises(ProbabilityError):
            push(*masses, 2.0)


# --- O/P/Q: cuotas no válidas, NaN/inf y bool ---------------------------------------------------------

@pytest.mark.parametrize("odds", [1, 1.0, math.nextafter(1.0, 0.0), 0.5, 0, -2.0, "2.0", Decimal("2.0"), Fraction(5, 2), None, [2.0]])
def test_invalid_decimal_odds_are_rejected(odds):
    with pytest.raises(OddsError):
        binary(0.5, odds)
    with pytest.raises(OddsError):
        push(0.4, 0.4, 0.2, odds)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nan_and_infinities_are_rejected(bad):
    with pytest.raises(ProbabilityError):
        binary(bad, 2.0)
    with pytest.raises(ProbabilityError):
        push(0.5, 0.5, bad, 2.0)
    with pytest.raises(OddsError):
        binary(0.5, bad)
    with pytest.raises(OddsError):
        push(0.4, 0.4, 0.2, bad)


@pytest.mark.parametrize("flag", [True, False])
def test_booleans_are_rejected(flag):
    with pytest.raises(ProbabilityError):
        binary(flag, 2.0)
    with pytest.raises(ProbabilityError):
        push(flag, 0, 1 - flag, 2.0)  # True + 0 + 0 sumaría 1
    with pytest.raises(OddsError):
        binary(0.5, flag)


def test_errors_share_the_documented_hierarchy_and_arguments_are_keyword_only():
    assert issubclass(ProbabilityError, MarketValueInputError) and issubclass(OddsError, MarketValueInputError)
    assert issubclass(MarketValueInputError, ValueError)
    with pytest.raises(TypeError):
        evaluate_binary_value(0.5, 2.0)
    with pytest.raises(TypeError):
        evaluate_push_value(0.4, 0.4, 0.2, 2.0)


# --- R: límites numéricos extremos ----------------------------------------------------------------------

def test_huge_integers_raise_documented_errors_not_overflow():
    with pytest.raises(ProbabilityError):
        binary(10 ** 400, 2.0)
    with pytest.raises(ProbabilityError):
        push(0.5, 0.5, 10 ** 400, 2.0)
    for odds in (10 ** 400, 2 ** 1024):  # > 1 pero fuera del rango del double
        with pytest.raises(OddsError):
            binary(0.5, odds)
        with pytest.raises(OddsError):
            push(0.4, 0.4, 0.2, odds)


HUGE = 10 ** 5000  # 5001 dígitos: más que sys.get_int_max_str_digits() (4300 por defecto en CPython)


@pytest.mark.parametrize("huge", [HUGE, -HUGE], ids=["positive", "negative"])
def test_integers_beyond_the_str_digit_limit_are_described_not_converted(huge):
    limit = sys.get_int_max_str_digits()
    assert 0 < limit < 5001  # precondición: repr(HUGE) lanzaría ValueError
    bits = f"<int {'negativo ' if huge < 0 else ''}de 16610 bits>"  # (10**5000).bit_length() == 16610
    with pytest.raises(ProbabilityError) as exc:
        binary(huge, 2.0)
    assert type(exc.value) is ProbabilityError and str(exc.value) == f"p_win fuera de [0, 1] o no finita: {bits}"
    for name, masses in (("p_win", (huge, 0.3, 0.2)), ("p_lose", (0.5, huge, 0.2)), ("p_push", (0.5, 0.3, huge))):
        with pytest.raises(ProbabilityError) as exc:
            push(*masses, 2.0)
        assert type(exc.value) is ProbabilityError and str(exc.value) == f"{name} fuera de [0, 1] o no finita: {bits}"
    # +HUGE pasa "> 1" y desborda en float(); -HUGE no pasa "> 1"
    odds_message = f"decimal_odds no es representable como double finito: {bits}" if huge > 0 else f"decimal_odds debe ser > 1: {bits}"
    for call in (lambda: binary(0.5, huge), lambda: push(0.4, 0.4, 0.2, huge)):
        with pytest.raises(OddsError) as exc:
            call()
        assert type(exc.value) is OddsError and str(exc.value) == odds_message
    assert sys.get_int_max_str_digits() == limit  # el límite global no se toca


def test_str_digit_limit_boundary_and_valid_integer_controls():
    limit = sys.get_int_max_str_digits()
    assert limit > 0
    edge, beyond = 10 ** (limit - 1), 10 ** limit  # limit dígitos (convertible) y limit + 1 (no convertible)
    with pytest.raises(ProbabilityError, match=r"^p_lose fuera de \[0, 1\] o no finita: 10{%d}$" % (limit - 1)):
        push(0.5, edge, 0.2, 2.0)
    with pytest.raises(ProbabilityError) as exc:
        push(0.5, beyond, 0.2, 2.0)
    assert str(exc.value) == f"p_lose fuera de [0, 1] o no finita: <int de {beyond.bit_length()} bits>"
    with pytest.raises(OddsError) as exc:
        binary(0.5, -beyond)
    assert str(exc.value) == f"decimal_odds debe ser > 1: <int negativo de {beyond.bit_length()} bits>"
    # enteros válidos: se evalúan igual que antes
    assert (binary(1, 3).expected_net_return, binary(0.5, 3).fair_odds) == (2.0, 2.0)
    assert (push(0, 0, 1, 10 ** 300).fair_odds_status, push(0.4, 0.4, 0.2, 2).fair_odds) == (NON_UNIQUE_ALWAYS_PUSH, 2.0)


def test_ordinary_invalid_input_messages_are_unchanged():
    cases = [
        (ProbabilityError, lambda: binary(-0.1, 2.0), "p_win fuera de [0, 1] o no finita: -0.1"),
        (ProbabilityError, lambda: binary(math.nan, 2.0), "p_win fuera de [0, 1] o no finita: nan"),
        (ProbabilityError, lambda: push(0.5, 0.5, 2, 2.0), "p_push fuera de [0, 1] o no finita: 2"),
        (ProbabilityError, lambda: binary(10 ** 400, 2.0), f"p_win fuera de [0, 1] o no finita: {10 ** 400}"),
        (ProbabilityError, lambda: binary("0.5", 2.0), "p_win debe ser int o float, no str: '0.5'"),
        (ProbabilityError, lambda: binary(True, 2.0), "p_win debe ser int o float, no bool: True"),
        (ProbabilityError, lambda: push(0.4, 0.4, 0.1, 2.0), "p_win + p_lose + p_push no suman 1 (tolerancia 1e-09): (0.4, 0.4, 0.1)"),
        (OddsError, lambda: binary(0.5, 1.0), "decimal_odds debe ser > 1: 1.0"),
        (OddsError, lambda: binary(0.5, math.nan), "decimal_odds debe ser > 1: nan"),
        (OddsError, lambda: binary(0.5, math.inf), "decimal_odds no finita: inf"),
        (OddsError, lambda: binary(0.5, Decimal("2.0")), "decimal_odds debe ser int o float, no Decimal: Decimal('2.0')"),
        (OddsError, lambda: push(0.4, 0.4, 0.2, 10 ** 400), f"decimal_odds no es representable como double finito: {10 ** 400}"),
        (OddsError, lambda: binary(0.5, -(10 ** 400)), f"decimal_odds debe ser > 1: {-(10 ** 400)}"),
    ]
    for error, call, message in cases:
        with pytest.raises(error) as exc:
            call()
        assert type(exc.value) is error and str(exc.value) == message


def test_largest_finite_odds_give_finite_ev():
    r = binary(0.5, MAX)
    assert (r.fair_odds, r.expected_net_return) == (2.0, 0.5 * MAX - 1) and math.isfinite(r.expected_net_return)
    w = push(1, 0, 0, MAX)
    assert (w.fair_odds, w.expected_net_return) == (1.0, MAX - 1) and math.isfinite(w.expected_net_return)
    assert binary(1, 10 ** 308).decimal_odds == 1e308  # un int representable se evalúa como su double


def test_fair_odds_beyond_the_double_range_are_not_computable_not_infinite():
    tiny = 5e-324  # el menor double positivo: 1 / tiny no es finito
    r = binary(tiny, 2.0)
    assert (r.fair_odds, r.fair_odds_status, r.expected_net_return) == (None, NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE, -1.0)
    w = push(tiny, 0.5, 0.5, 2.0)
    assert (w.fair_odds, w.fair_odds_status, w.expected_net_return) == (None, NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE, -0.5)
    assert binary(1e-300, 2.0).fair_odds == pytest.approx(1e300, rel=1e-15)  # grande pero finita: DEFINED


# --- S/T: determinismo e inmutabilidad ------------------------------------------------------------------

def test_results_are_repeatable_frozen_and_inputs_unchanged():
    kwargs = {"p_win": 0.4, "p_lose": 0.4, "p_push": 0.2, "decimal_odds": 2.2}
    before = dict(kwargs)
    first, second = evaluate_push_value(**kwargs), evaluate_push_value(**kwargs)
    assert first == second and kwargs == before
    assert binary(0.25, 4.0) == binary(0.25, 4.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.expected_net_return = 1.0
    assert isinstance(first, ValueEvaluation) and isinstance(first.limitations, tuple)


# --- U/V: aislamiento y procedencia ---------------------------------------------------------------------

def test_identity_and_provenance_limitations_are_explicit():
    r = binary(0.5, 2.0)
    assert (r.evaluator_id, r.evaluator_version, r.convention_version) == (EVALUATOR_ID, EVALUATOR_VERSION, CONVENTION_VERSION)
    assert (r.input_basis, r.pit_status) == ("CALLER_SUPPLIED_PROBABILITIES_AND_DECIMAL_ODDS", "NOT_CERTIFIED") == (INPUT_BASIS, PIT_STATUS)
    assert r.limitations == LIMITATIONS
    for needed in ("NOT_VALIDATED_FORECASTS", "NOT_AUTHENTICATED_QUOTES", "NO_AS_OF_PRICE_TIMESTAMP", "NOT_REALIZED_OR_REALIZABLE_PROFIT",
                   "NO_STAKE_SIZING_OR_RECOMMENDATION", "NOT_TRUE_PIT_CERTIFIED"):
        assert any(needed in item for item in LIMITATIONS)


def test_output_schema_has_no_recommendation_staking_or_ranking():
    names = {f.name for f in dataclasses.fields(ValueEvaluation)}
    assert names == {"evaluator_id", "evaluator_version", "convention_version", "settlement", "p_win", "p_lose", "p_push", "decimal_odds",
                     "fair_odds", "fair_odds_status", "expected_net_return", "input_basis", "pit_status", "limitations"}
    banned = ("stake", "kelly", "recommend", "pick", "rank", "edge", "clv", "profit", "bet", "signal", "true_pit")
    assert not [n for n in names if any(b in n for b in banned)]


def test_module_imports_no_app_database_provider_or_numeric_stack():
    code = ("import sys; import research.market_value_math.value; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'requests', 'httpx')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"
