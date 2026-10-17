"""MOD-DEV-074: puntuación opcional de tres clases (OVER, UNDER, PUSH) en líneas enteras. Puro: sin BD, sin red.
    python -m pytest research/tests/test_integer_goals_markets.py -q   (desde backend/)
Observaciones sintéticas; los valores esperados están calculados a mano, no con la implementación."""

import copy
import dataclasses
import math
from decimal import Decimal

import pytest

from research.goals_markets.evaluation import (INTEGER_LINE, OVER_UNDER, SETTLEMENT_ONLY, ObservationError, ProbabilityEvidenceError,
                                               evaluate_btts, evaluate_over_under)
from research.goals_markets.integer_evaluation import (INTEGER_OUTCOMES, THREE_CLASS, IntegerLineEvaluation, evaluate_integer_over_under,
                                                       validate_integer_probabilities)
from research.m58.goal_labels import FULLTIME_INVALID
from research.m58.labels import FULLTIME_INCOMPLETE, NOT_FINISHED

APPROX = 1e-12
P_A = {"OVER": 0.5, "UNDER": 0.3, "PUSH": 0.2}
P_B = {"OVER": 0.2, "UNDER": 0.7, "PUSH": 0.1}
P_C = {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.25}


def obs(status, home, away, probabilities=None, **extra):
    row = {"status_short": status, "fulltime_home": home, "fulltime_away": away, **extra}
    if probabilities is not None:
        row["probabilities"] = probabilities
    return row


def counts(r):
    return (r.total_input, r.eligible_settled, r.probability_scored, r.settlement_over, r.settlement_under, r.settlement_push,
            r.betting_void, r.excluded_total)


# --- A, B, C: métricas exactas por resultado (línea 2.0) --------------------------------------------

def test_over_observed():
    r = evaluate_integer_over_under([obs("FT", 2, 1, P_A)], "2.0")
    assert (r.market, r.line, r.line_type, r.metric_basis) == (OVER_UNDER, "2.0", INTEGER_LINE, THREE_CLASS)
    assert counts(r) == (1, 1, 1, 1, 0, 0, 0, 0)
    assert r.brier == pytest.approx(0.25 + 0.09 + 0.04, abs=APPROX)  # 0.38
    assert r.log_loss == pytest.approx(0.6931471805599453, abs=APPROX)  # -ln 0.5


def test_under_observed():
    r = evaluate_integer_over_under([obs("FT", 1, 0, P_B)], "2.0")
    assert counts(r) == (1, 1, 1, 0, 1, 0, 0, 0)
    assert r.brier == pytest.approx(0.04 + 0.09 + 0.01, abs=APPROX)  # 0.14
    assert r.log_loss == pytest.approx(0.35667494393873245, abs=APPROX)  # -ln 0.7


def test_push_observed_scores_with_p_push_and_is_betting_void():
    r = evaluate_integer_over_under([obs("FT", 1, 1, P_C)], "2.0")
    assert counts(r) == (1, 1, 1, 0, 0, 1, 1, 0)  # D: puntuada; E: anulación para apuestas
    assert r.brier == pytest.approx(0.16 + 0.1225 + 0.5625, abs=APPROX)  # 0.845: PUSH es una clase real
    assert r.log_loss == pytest.approx(1.3862943611198906, abs=APPROX)  # -ln 0.25


def test_push_is_never_a_betting_win_or_loss():
    fields = {f.name for f in dataclasses.fields(IntegerLineEvaluation)}
    assert not any("win" in f or "loss" in f.replace("log_loss", "") for f in fields)  # F: sin aciertos ni fallos de apuesta
    assert fields >= {"betting_void", "settlement_push", "probability_scored"}


# --- G, H, I, J: cohortes ------------------------------------------------------------------------------

def test_all_over_all_under_and_all_push_cohorts():
    over = evaluate_integer_over_under([obs("FT", 3, 1, P_A), obs("PEN", 2, 2, P_A)], "3.0")  # 4 > 3 en los dos
    assert counts(over) == (2, 2, 2, 2, 0, 0, 0, 0) and over.brier == pytest.approx(0.38, abs=APPROX)
    under = evaluate_integer_over_under([obs("FT", 0, 0, P_B), obs("AET", 1, 0, P_B)], "2.0")
    assert counts(under) == (2, 2, 2, 0, 2, 0, 0, 0) and under.log_loss == pytest.approx(0.35667494393873245, abs=APPROX)
    push = evaluate_integer_over_under([obs("FT", 2, 0, P_C), obs("FT", 0, 2, P_C), obs("AET", 1, 1, P_C)], "2.0")
    assert counts(push) == (3, 3, 3, 0, 0, 3, 3, 0) and push.brier == pytest.approx(0.845, abs=APPROX)


def test_mixed_over_under_push():
    r = evaluate_integer_over_under([obs("FT", 2, 1, P_A), obs("FT", 1, 0, P_B), obs("FT", 1, 1, P_C)], "2.0")
    assert counts(r) == (3, 3, 3, 1, 1, 1, 1, 0)
    assert r.brier == pytest.approx((0.38 + 0.14 + 0.845) / 3, abs=APPROX)  # 0.455
    assert r.log_loss == pytest.approx((0.6931471805599453 + 0.35667494393873245 + 1.3862943611198906) / 3, abs=APPROX)


def test_mixed_settled_and_excluded_keep_canonical_reasons_and_skip_probabilities():
    rows = [obs("FT", 1, 1, P_C), obs("NS", None, None, "garbage"), obs("FT", None, 2, {"OVER": 2}), obs("FT", -1, 0),
            obs("AWD", 3, 0), obs("FT", 3, 1, P_A)]
    r = evaluate_integer_over_under(rows, "2.0")
    assert counts(r) == (6, 2, 2, 1, 0, 1, 1, 4)
    assert dict(r.excluded_by_reason) == {FULLTIME_INCOMPLETE: 1, FULLTIME_INVALID: 1, NOT_FINISHED: 2}
    assert r.brier == pytest.approx((0.845 + 0.38) / 2, abs=APPROX)


def test_empty_and_all_excluded_return_none_metrics_with_counts():
    for rows in ([], ()):
        r = evaluate_integer_over_under(rows, "1.0")
        assert counts(r) == (0,) * 8 and r.brier is None and r.log_loss is None and dict(r.excluded_by_reason) == {}
    r = evaluate_integer_over_under([obs("PST", None, None), obs("FT", 1, None), obs("FT", 1.0, 0)], "4.0")
    assert counts(r) == (3, 0, 0, 0, 0, 0, 0, 3) and r.brier is None and r.log_loss is None
    assert dict(r.excluded_by_reason) == {FULLTIME_INCOMPLETE: 1, FULLTIME_INVALID: 1, NOT_FINISHED: 1}


# --- K: líneas -------------------------------------------------------------------------------------------

def test_half_lines_and_unapproved_lines_are_rejected_even_without_rows():
    for line in ("2.5", "0.5", Decimal("6.5")):
        with pytest.raises(ValueError, match="enteras"):
            evaluate_integer_over_under([], line)
    for line in ("2.25", "7.0", 2.0, None, Decimal("NaN")):
        with pytest.raises(ValueError):
            evaluate_integer_over_under([], line)
    assert evaluate_integer_over_under([], Decimal("3")).line == "3.0"


# --- L, M: extremos y EPS -----------------------------------------------------------------------------

def test_zero_and_one_probabilities_and_eps_only_inside_the_log():
    certain = evaluate_integer_over_under([obs("FT", 2, 1, {"OVER": 1, "UNDER": 0, "PUSH": 0})], "2.0")
    assert certain.brier == 0.0 and certain.log_loss == 0.0
    wrong = evaluate_integer_over_under([obs("FT", 1, 1, {"OVER": 1.0, "UNDER": 0.0, "PUSH": 0.0})], "2.0")
    assert wrong.brier == 2.0  # (1 - 0)^2 + 0 + (0 - 1)^2, sin dividir
    assert wrong.log_loss == pytest.approx(34.538776394910684, abs=APPROX)  # -log(1e-15)


# --- N, O, P, Q, R: validación de probabilidades --------------------------------------------------------

@pytest.mark.parametrize("probabilities", [
    {"OVER": True, "UNDER": False, "PUSH": False}, {"OVER": 0.5, "UNDER": 0.5, "PUSH": False},
    {"OVER": math.nan, "UNDER": 0.5, "PUSH": 0.5}, {"OVER": math.inf, "UNDER": 0.0, "PUSH": 0.0},
    {"OVER": -math.inf, "UNDER": 0.5, "PUSH": 0.5}, {"OVER": -0.1, "UNDER": 0.6, "PUSH": 0.5},
    {"OVER": 1.1, "UNDER": -0.05, "PUSH": -0.05}, {"OVER": 0.2, "UNDER": 0.2, "PUSH": 1.2},
    {"OVER": 0.5, "UNDER": 0.5}, {"OVER": 0.4, "UNDER": 0.3, "PUSH": 0.2, "VOID": 0.1}, {"over": 0.4, "under": 0.35, "push": 0.25},
    {"OVER": "0.4", "UNDER": 0.35, "PUSH": 0.25}, {"OVER": Decimal("0.4"), "UNDER": 0.35, "PUSH": 0.25},
    [0.4, 0.35, 0.25], (0.4, 0.35, 0.25), {"OVER": 0.4, "UNDER": 0.4, "PUSH": 0.4}, {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.250000002},
    {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.250000001}, "OVER=1",
], ids=repr)
def test_invalid_probability_evidence_is_rejected(probabilities):
    with pytest.raises(ProbabilityEvidenceError):
        validate_integer_probabilities(probabilities)
    with pytest.raises(ProbabilityEvidenceError, match="observación 0"):
        evaluate_integer_over_under([obs("FT", 1, 1, probabilities)], "2.0")


def test_sum_within_tolerance_is_used_without_renormalization():
    p = {"PUSH": 0.2500000005, "UNDER": 0.35, "OVER": 0.4}  # |suma - 1| = 5.0e-10; el orden de las claves no importa
    assert dict(validate_integer_probabilities(p)) == {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.2500000005}
    r = evaluate_integer_over_under([obs("FT", 2, 0, p)], "2.0")  # PUSH
    assert r.brier == 0.4 ** 2 + 0.35 ** 2 + (0.2500000005 - 1) ** 2 and r.log_loss == -math.log(0.2500000005)


# --- MOD-DEV-079: masa total con math.fsum y sus dos fronteras (SUM_TOLERANCE = 1e-9, simétrica) ----------

LOW_OVER, LOW_UNDER, LOW_PUSH = 0.5, 2 ** -54 - 2 ** -70, float.fromhex("0x1.ffffffeed1f41p-2")
HIGH_PUSH = float.fromhex("0x1.12e0bcp-30")  # 0.5 + 0.5 + HIGH_PUSH: el mayor total representable aceptado
ABOVE_PUSH = float.fromhex("0x1.12e0cp-30")  # 0.5 + 0.5 + ABOVE_PUSH: el total representable siguiente


def assert_rejected(probabilities):
    result = sentinel = object()
    with pytest.raises(ProbabilityEvidenceError):
        result = validate_integer_probabilities(probabilities)
    assert result is sentinel  # sin resultado parcial
    with pytest.raises(ProbabilityEvidenceError, match="observación 0"):
        result = evaluate_integer_over_under([obs("FT", 1, 1, probabilities)], "2.0")
    assert result is sentinel


def test_lower_boundary_exact_is_accepted_with_fsum_and_values_unchanged():
    p = {"OVER": LOW_OVER, "UNDER": LOW_UNDER, "PUSH": LOW_PUSH}
    assert math.fsum((LOW_OVER, LOW_UNDER, LOW_PUSH)) == 1.0 - 1e-9
    assert abs((LOW_OVER + LOW_UNDER) + LOW_PUSH - 1) > 1e-9  # la suma estricta de izquierda a derecha queda por debajo
    before = dict(p)
    got = validate_integer_probabilities(p)
    assert [got[k].hex() for k in INTEGER_OUTCOMES] == [LOW_OVER.hex(), LOW_UNDER.hex(), LOW_PUSH.hex()] and p == before
    r = evaluate_integer_over_under([obs("FT", 1, 1, p)], "2.0")  # PUSH: puntúa con p_PUSH sin renormalizar
    assert r.probability_scored == r.betting_void == 1 and r.log_loss == -math.log(LOW_PUSH)


def test_lower_boundary_minus_one_step_is_rejected():
    push = math.nextafter(LOW_PUSH, 0.0)
    assert math.fsum((LOW_OVER, LOW_UNDER, push)) < 1.0 - 1e-9
    assert_rejected({"OVER": LOW_OVER, "UNDER": LOW_UNDER, "PUSH": push})


def test_upper_boundary_exact_and_exceeded():
    assert math.fsum((0.5, 0.5, HIGH_PUSH)) - 1 <= 1e-9
    assert validate_integer_probabilities({"OVER": 0.5, "UNDER": 0.5, "PUSH": HIGH_PUSH})["PUSH"].hex() == HIGH_PUSH.hex()
    assert math.fsum((0.5, 0.5, ABOVE_PUSH)) == math.nextafter(math.fsum((0.5, 0.5, HIGH_PUSH)), 2.0)  # un paso de total por encima
    assert math.fsum((0.5, 0.5, ABOVE_PUSH)) - 1 > 1e-9
    assert_rejected({"OVER": 0.5, "UNDER": 0.5, "PUSH": ABOVE_PUSH})
    # 1 + 2**-52 está dentro de la tolerancia simétrica vigente: se acepta tal cual
    assert dict(validate_integer_probabilities({"OVER": 1.0, "UNDER": 2 ** -52, "PUSH": 0.0})) == {"OVER": 1.0, "UNDER": 2 ** -52, "PUSH": 0.0}


def test_exact_unit_mass_and_ordinary_triple_are_accepted():
    assert dict(validate_integer_probabilities({"PUSH": 0.25, "OVER": 0.5, "UNDER": 0.25})) == {"OVER": 0.5, "UNDER": 0.25, "PUSH": 0.25}
    assert dict(validate_integer_probabilities(P_C)) == P_C


@pytest.mark.parametrize("probabilities", [
    {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.250000002}, {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.249999998},
    {"OVER": 1.0 + 2 ** -52, "UNDER": 0.0, "PUSH": 0.0}, {"OVER": -0.0 - 2 ** -60, "UNDER": 0.5, "PUSH": 0.5},
    {"OVER": math.nan, "UNDER": 0.5, "PUSH": 0.5}, {"OVER": 0.5, "UNDER": math.inf, "PUSH": 0.5}, {"OVER": True, "UNDER": 0, "PUSH": 0},
    {"OVER": 0.5, "UNDER": 0.5}, {"OVER": 0.5, "UNDER": 0.25, "PUSH": 0.25, "VOID": 0.0},
], ids=repr)
def test_malformed_mass_and_values_fail_closed(probabilities):
    before = copy.deepcopy(probabilities)
    assert_rejected(probabilities)
    assert probabilities == before  # sin mutación (deepcopy conserva el mismo objeto NaN: la comparación es por identidad)


# --- S, T: evidencia obligatoria ------------------------------------------------------------------------

@pytest.mark.parametrize("home, away, outcome", [(1, 1, "PUSH"), (3, 0, "OVER"), (0, 1, "UNDER")])
def test_missing_probabilities_on_any_settled_outcome_fail_closed(home, away, outcome):
    for row in (obs("FT", home, away), obs("FT", home, away) | {"probabilities": None}):
        with pytest.raises(ProbabilityEvidenceError, match=f"observación 1: .*{outcome}"):
            evaluate_integer_over_under([obs("FT", 2, 1, P_A), row], "2.0")


# --- U, V, W: 90', pureza y recuento --------------------------------------------------------------------

def test_ft_aet_pen_use_only_the_90_minute_score():
    rows = [obs("AET", 1, 1, P_C, home_goals=3, away_goals=1), obs("PEN", 0, 0, P_B, penalty_home=5, penalty_away=4),
            obs("FT", 2, 1, P_A)]
    r = evaluate_integer_over_under(rows, "2.0")
    assert (r.settlement_over, r.settlement_under, r.settlement_push) == (1, 1, 1)


def test_inputs_unchanged_results_repeatable_and_immutable():
    rows = [obs("FT", 2, 1, dict(P_A)), obs("FT", 1, 1, dict(P_C)), obs("NS", None, None)]
    before = copy.deepcopy(rows)
    first = evaluate_integer_over_under(rows, "2.0")
    assert all(evaluate_integer_over_under(rows, "2.0") == first for _ in range(5)) and rows == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.brier = 0.0
    with pytest.raises(TypeError):
        first.excluded_by_reason["x"] = 1
    with pytest.raises(TypeError):
        validate_integer_probabilities(P_A)["OVER"] = 0.0


def test_accounting_identities():
    r = evaluate_integer_over_under([obs("FT", 2, 1, P_A), obs("FT", 1, 1, P_C), obs("FT", 0, 0, P_B), obs("CANC", None, None)], "2.0")
    assert r.total_input == r.eligible_settled + r.excluded_total == 4
    assert r.eligible_settled == r.settlement_over + r.settlement_under + r.settlement_push == r.probability_scored == 3
    assert r.betting_void == r.settlement_push == 1 and INTEGER_OUTCOMES == ("OVER", "UNDER", "PUSH")


def test_malformed_collections_and_observations_are_rejected():
    for bad in ((obs("FT", 1, 1, P_A) for _ in range(1)), None, obs("FT", 1, 1, P_A), [("FT", 1, 1)], [{"status_short": "FT"}]):
        with pytest.raises(ObservationError):
            evaluate_integer_over_under(bad, "2.0")


# --- X: las interfaces de MOD-DEV-068-R1 no cambian ----------------------------------------------------

def test_existing_binary_and_settlement_only_interfaces_are_unchanged():
    rows = [obs("FT", 2, 1, P_A), obs("FT", 1, 1, P_C), obs("FT", 1, 0, P_B), obs("NS", None, None)]
    legacy = evaluate_over_under(rows, "2.0")  # entera: solo liquidación, no lee probabilidades
    assert (legacy.scored_basis, legacy.scored, legacy.push_void, legacy.brier, legacy.log_loss) == (SETTLEMENT_ONLY, 2, 1, None, None)
    assert dict(legacy.outcome_counts) == {"OVER": 1, "UNDER": 1, "PUSH": 1}
    new = evaluate_integer_over_under(rows, "2.0")
    assert (new.settlement_over, new.settlement_under, new.settlement_push, new.excluded_total) == (1, 1, 1, legacy.excluded_total)
    assert new.probability_scored == 3 != legacy.scored  # campos distintos: no confundir el scored de liquidación
    binary = evaluate_over_under([obs("FT", 2, 1, (0.7, 0.3)), obs("FT", 1, 1, (0.6, 0.4))], "2.5")
    assert binary.brier == pytest.approx(0.45, abs=APPROX) and binary.log_loss == pytest.approx(0.6364828379064438, abs=APPROX)
    assert evaluate_btts([obs("FT", 1, 1, (0.8, 0.2))]).brier == pytest.approx(0.08, abs=APPROX)
