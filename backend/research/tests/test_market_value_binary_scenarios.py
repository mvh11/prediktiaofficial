"""MOD-DEV-106A: sensibilidad del valor binario a un rango de probabilidades declarado. Sin BD, sin red, sin app.
    python -m pytest research/tests/test_market_value_binary_scenarios.py -q   (desde backend/)
Probabilidades y cuotas sintéticas: ninguna es una previsión, un intervalo de confianza ni una cotización auténtica."""

import ast
import dataclasses
import inspect
import math
import os
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction

import pytest

import research.market_value_binary_scenarios as scenarios
from research.market_value_binary_scenarios import (DIAGNOSTIC_ID, DIAGNOSTIC_VERSION, INPUT_BASIS, LIMITATIONS, PIT_STATUS, POINT, RANGE,
                                                    SCENARIO_BASIS, BinaryScenarioRange, evaluate_binary_scenario_range)
from research.market_value_math import value as market_value
from research.market_value_math.value import (BINARY_WIN_LOSE, DEFINED, NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE, UNDEFINED_NO_WIN_PROBABILITY,
                                              MarketValueInputError, OddsError, ProbabilityError, ValueEvaluation, evaluate_binary_value)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TINY = 5e-324  # el menor double positivo: 1 / TINY desborda a inf
JUST_ABOVE_ONE = math.nextafter(1.0, 2.0)


def rng(p_low, p_high, decimal_odds=2.0):
    return evaluate_binary_scenario_range(p_low=p_low, p_high=p_high, decimal_odds=decimal_odds)


def engine_error(**kwargs):
    with pytest.raises(MarketValueInputError) as info:
        evaluate_binary_value(**kwargs)
    return info.value


def assert_same_error(expected, p_low, p_high, decimal_odds):
    with pytest.raises(MarketValueInputError) as info:
        rng(p_low, p_high, decimal_odds)
    assert type(info.value) is type(expected)
    assert str(info.value) == str(expected)
    return info.value


@pytest.fixture
def engine_spy(monkeypatch):
    """Espía sobre el motor REAL: registra los argumentos (los mismos objetos) y los resultados que devolvió."""
    calls = []
    real = market_value.evaluate_binary_value

    def spy(**kwargs):
        result = real(**kwargs)
        calls.append((kwargs, result))
        return result

    monkeypatch.setattr(market_value, "evaluate_binary_value", spy)
    return calls


# A. 0.3 .. 0.7 con cuota 2.0

def test_a_range_endpoints_equal_independent_engine_calls():
    r = rng(0.3, 0.7, 2.0)
    assert isinstance(r, BinaryScenarioRange)
    assert r.ev_low == pytest.approx(-0.4, abs=1e-12) and r.ev_high == pytest.approx(0.4, abs=1e-12)
    assert r.low == evaluate_binary_value(p_win=0.3, decimal_odds=2.0)
    assert r.high == evaluate_binary_value(p_win=0.7, decimal_odds=2.0)
    assert r.ev_low == evaluate_binary_value(p_win=0.3, decimal_odds=2.0).expected_net_return  # el double del motor, sin redondear
    assert r.ev_high == evaluate_binary_value(p_win=0.7, decimal_odds=2.0).expected_net_return
    assert (r.p_low, r.p_high, r.scenario_shape) == (0.3, 0.7, RANGE)
    assert r.low.fair_odds_status == r.high.fair_odds_status == DEFINED
    assert r.low.fair_odds == evaluate_binary_value(p_win=0.3, decimal_odds=2.0).fair_odds


def test_a_engine_called_once_per_endpoint_with_original_objects(engine_spy):
    p_low, p_high, odds = 0.3, 0.7, 2.0
    r = rng(p_low, p_high, odds)
    assert [list(kw) for kw, _ in engine_spy] == [["p_win", "decimal_odds"], ["p_win", "decimal_odds"]]
    (kw_low, res_low), (kw_high, res_high) = engine_spy
    assert kw_low["p_win"] is p_low and kw_high["p_win"] is p_high and kw_low["decimal_odds"] is odds and kw_high["decimal_odds"] is odds
    assert r.low is res_low and r.high is res_high
    assert r.ev_low is res_low.expected_net_return and r.ev_high is res_high.expected_net_return


# B. Extremos iguales

@pytest.mark.parametrize("p", [0.0, 0.25, 0.5, 1.0, TINY])
def test_b_equal_endpoints(p):
    r = rng(p, p, 1.9)
    assert r.low == r.high == evaluate_binary_value(p_win=p, decimal_odds=1.9)
    assert r.ev_low == r.ev_high
    assert r.scenario_shape == POINT


def test_b_signed_zero_endpoints_are_equal_point():
    r = rng(0.0, -0.0)  # el motor informa -0.0 como 0.0: no es un rango invertido
    assert r.scenario_shape == POINT and r.low == r.high
    assert math.copysign(1.0, r.low.p_win) == 1.0 and math.copysign(1.0, r.high.p_win) == 1.0


# C. Extremos 0 y 1, probabilidad diminuta y extremos invertidos

def test_c_zero_and_one_endpoints_keep_engine_fair_odds_status():
    r = rng(0, 1, 2.5)
    assert r.low == evaluate_binary_value(p_win=0, decimal_odds=2.5) and r.high == evaluate_binary_value(p_win=1, decimal_odds=2.5)
    assert (r.low.fair_odds, r.low.fair_odds_status, r.ev_low) == (None, UNDEFINED_NO_WIN_PROBABILITY, -1.0)
    assert (r.high.fair_odds, r.high.fair_odds_status, r.ev_high) == (1.0, DEFINED, 1.5)
    assert type(r.p_low) is int and type(r.p_high) is int  # los originales del llamador; el motor informa 0.0 / 1.0
    assert (r.low.p_win, r.high.p_win) == (0.0, 1.0)


def test_c_tiny_probability_keeps_not_computable_fair_odds():
    r = rng(TINY, 0.5)
    assert r.low == evaluate_binary_value(p_win=TINY, decimal_odds=2.0)
    assert (r.low.fair_odds, r.low.fair_odds_status) == (None, NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE)
    assert (r.high.fair_odds, r.high.fair_odds_status) == (2.0, DEFINED)
    r = rng(0.0, TINY)
    assert (r.low.fair_odds_status, r.high.fair_odds_status) == (UNDEFINED_NO_WIN_PROBABILITY, NOT_COMPUTABLE_OUTSIDE_FLOAT_RANGE)
    assert r.low.fair_odds is None and r.high.fair_odds is None


def test_c_no_combined_fair_odds_range_field():
    names = {f.name for f in dataclasses.fields(BinaryScenarioRange)}
    assert not {n for n in names if "fair" in n}


@pytest.mark.parametrize("p_low, p_high", [(0.7, 0.3), (1, 0), (0.5, 0.49999999999999994), (TINY, 0.0), (1.0, 0.0)])
def test_c_reversed_bounds_rejected_with_probability_error_not_swapped(p_low, p_high):
    with pytest.raises(ProbabilityError) as info:
        rng(p_low, p_high)
    assert type(info.value) is ProbabilityError and "invertidos" in str(info.value)


# D. Probabilidades inválidas: la misma clase y el mismo mensaje que el motor

INVALID_PROBABILITIES = [True, False, math.nan, math.inf, -math.inf, -0.1, -1, 1.0000000000000002, 2, "0.5", b"0.5", None,
                         Decimal("0.5"), Fraction(1, 2), 0.5j, [0.5], 10 ** 400, -(10 ** 400), 2 ** 1024,
                         pytest.param(10 ** 5000, id="int_beyond_str_digit_limit")]  # str() de ese int falla: id explícito


@pytest.mark.parametrize("bad", INVALID_PROBABILITIES)
def test_d_invalid_p_low_raises_engine_probability_error(bad):
    err = assert_same_error(engine_error(p_win=bad, decimal_odds=2.0), bad, 0.5, 2.0)
    assert type(err) is ProbabilityError


@pytest.mark.parametrize("bad", INVALID_PROBABILITIES)
def test_d_invalid_p_high_raises_engine_probability_error(bad):
    err = assert_same_error(engine_error(p_win=bad, decimal_odds=2.0), 0.5, bad, 2.0)
    assert type(err) is ProbabilityError


def test_d_endpoint_validation_precedes_bounds_check():
    # p_high inválida y "menor" que p_low: decide el motor, no el control de orden
    for bad in (-0.1, False, math.nan):
        err = assert_same_error(engine_error(p_win=bad, decimal_odds=2.0), 0.7, bad, 2.0)
        assert "invertidos" not in str(err)


# E. Cuotas decimales

@pytest.mark.parametrize("odds", [JUST_ABOVE_ONE, 1.0000001, 2, 1e308, 10 ** 300, sys.float_info.max])
def test_e_valid_odds_match_engine(odds):
    r = rng(0.2, 0.8, odds)
    assert r.low == evaluate_binary_value(p_win=0.2, decimal_odds=odds) and r.high == evaluate_binary_value(p_win=0.8, decimal_odds=odds)
    assert math.isfinite(r.ev_low) and math.isfinite(r.ev_high) and r.ev_low <= r.ev_high


INVALID_ODDS = [1, 1.0, 0.999, 0, -2.0, math.nan, math.inf, -math.inf, True, False, "2.0", None, Decimal("2.0"), Fraction(3, 2),
                [2.0], 10 ** 400, 2 ** 1024]


@pytest.mark.parametrize("bad", INVALID_ODDS)
def test_e_invalid_odds_raise_engine_odds_error(bad):
    err = assert_same_error(engine_error(p_win=0.3, decimal_odds=bad), 0.3, 0.7, bad)
    assert type(err) is OddsError


def test_e_error_precedence_follows_engine_call_order():
    # p_low se evalúa primero y el motor valida p_win antes que la cuota
    assert type(assert_same_error(engine_error(p_win=-1, decimal_odds=1.0), -1, 0.7, 1.0)) is ProbabilityError
    assert type(assert_same_error(engine_error(p_win=0.3, decimal_odds=1.0), 0.3, -1, 1.0)) is OddsError


# F. Integridad estructural

def test_f_result_is_frozen_and_preserves_engine_provenance():
    r = rng(0.3, 0.7)
    for obj, field in ((r, "ev_low"), (r, "low"), (r.low, "expected_net_return")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, 0.0)
    for e in (r.low, r.high):
        assert isinstance(e, ValueEvaluation)
        assert (e.evaluator_id, e.evaluator_version, e.convention_version, e.settlement) == (
            market_value.EVALUATOR_ID, market_value.EVALUATOR_VERSION, market_value.CONVENTION_VERSION, BINARY_WIN_LOSE)
        assert (e.input_basis, e.pit_status, e.limitations) == (market_value.INPUT_BASIS, market_value.PIT_STATUS, market_value.LIMITATIONS)
    assert hash(r) == hash(rng(0.3, 0.7))


def test_f_trust_labels():
    r = rng(0.3, 0.7)
    assert (r.diagnostic_id, r.diagnostic_version) == (DIAGNOSTIC_ID, DIAGNOSTIC_VERSION)
    assert (r.scenario_basis, r.input_basis, r.pit_status) == ("CALLER_DECLARED_NOT_CALIBRATED", "CALLER_SUPPLIED", "NOT_CERTIFIED")
    assert (SCENARIO_BASIS, INPUT_BASIS, PIT_STATUS) == ("CALLER_DECLARED_NOT_CALIBRATED", "CALLER_SUPPLIED", "NOT_CERTIFIED")
    assert r.limitations == LIMITATIONS and {"NOT_A_CONFIDENCE_OR_CREDIBLE_INTERVAL", "PROBABILITIES_NOT_CALIBRATED",
                                             "NO_COMBINED_FAIR_ODDS_RANGE", "NOT_TRUE_PIT_CERTIFIED"} <= set(LIMITATIONS)


def test_f_no_confidence_calibration_decision_or_timestamp_fields():
    names = {f.name for f in dataclasses.fields(BinaryScenarioRange)}
    banned = ("confidence", "interval", "calibrat", "robust", "threshold", "select", "stake", "kelly", "recommend", "pick", "time", "_at")
    assert not {n for n in names if any(b in n for b in banned)}


def test_f_deterministic_and_inputs_untouched():
    p_low, p_high, odds = 0.15, 0.85, 3.25
    first, second = rng(p_low, p_high, odds), rng(p_low, p_high, odds)
    assert first == second
    assert (p_low, p_high, odds) == (0.15, 0.85, 3.25)
    assert first.p_low is p_low and first.p_high is p_high


def test_f_keyword_only_signature():
    params = inspect.signature(evaluate_binary_scenario_range).parameters
    assert list(params) == ["p_low", "p_high", "decimal_odds"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty for p in params.values())
    with pytest.raises(TypeError):
        evaluate_binary_scenario_range(0.3, 0.7, 2.0)


def test_f_no_partial_result_and_no_second_engine_call_after_invalid_p_low(engine_spy):
    with pytest.raises(ProbabilityError):
        rng(math.nan, 0.5)
    assert engine_spy == []  # el motor lanzó en la primera llamada; no hubo segunda
    with pytest.raises(ProbabilityError):
        rng(0.9, 0.1)
    assert len(engine_spy) == 2  # ambos extremos validados por el motor antes del control de orden


def test_f_module_has_no_arithmetic_or_alternative_engine():
    tree = ast.parse(inspect.getsource(scenarios))
    # sin operaciones aritméticas (solo comparaciones y el `not` lógico)
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.BinOp, ast.AugAssign))
                or (isinstance(n, ast.UnaryOp) and not isinstance(n.op, ast.Not))]
    imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported == {"dataclass", "value"} and modules == {"dataclasses", "research.market_value_math"}
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert calls == {"market_value.evaluate_binary_value", "market_value.ProbabilityError", "AssertionError", "BinaryScenarioRange",
                     "dataclass"}  # dataclass: el decorador @dataclass(frozen=True)


def test_f_import_isolated_without_app_database_provider_or_network():
    code = ("import sys; import research.market_value_binary_scenarios; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'requests', 'httpx',"
            " 'socket', 'urllib3') or m.startswith('research.')))")
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ADMIN_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE", "PYTHONPATH")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0
    assert out.stdout.strip() == str(["research.market_value_binary_scenarios", "research.market_value_math", "research.market_value_math.value"])


# G. Monotonía

GRID_P = [0, 0.0, TINY, 1e-300, 1e-17, 0.1, 0.3, 1 / 3, 0.5, 0.7, 0.9, 0.9999999999999999, 1.0, 1]
GRID_ODDS = [JUST_ABOVE_ONE, 1.01, 1.5, 2.0, 3.7, 101.0, 1e308]


def test_g_monotonic_ev_over_independent_pairs():
    checked = 0
    for odds in GRID_ODDS:
        for i, p_low in enumerate(GRID_P):
            for p_high in GRID_P[i:]:
                r = rng(p_low, p_high, odds)
                assert r.ev_low <= r.ev_high
                assert r.low == evaluate_binary_value(p_win=p_low, decimal_odds=odds)
                assert r.high == evaluate_binary_value(p_win=p_high, decimal_odds=odds)
                assert (r.p_low is p_low) and (r.p_high is p_high)
                assert r.scenario_shape == (POINT if r.low.p_win == r.high.p_win else RANGE)
                if r.scenario_shape == POINT:
                    assert r.low == r.high and r.ev_low == r.ev_high
                checked += 1
    assert checked == len(GRID_ODDS) * len(GRID_P) * (len(GRID_P) + 1) // 2
