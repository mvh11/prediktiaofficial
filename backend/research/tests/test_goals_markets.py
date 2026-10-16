"""MOD-DEV-068-R1: evaluación pura de mercados de goles (BTTS y Over/Under). Sin BD, sin red, sin app.
    python -m pytest research/tests/test_goals_markets.py -q   (desde backend/)
Observaciones sintéticas; los valores esperados están escritos a mano (no derivados de la implementación)."""

import copy
import dataclasses
import math
import os
import subprocess
import sys
from decimal import Decimal

import pytest

from research.goals_markets.evaluation import (BTTS, HALF_LINE, INTEGER_LINE, OVER_UNDER, PROBABILITY_SCORED, SETTLEMENT_ONLY, SUM_TOLERANCE,
                                               ObservationError, ProbabilityEvidenceError, evaluate_btts, evaluate_over_under,
                                               validate_binary_probabilities)
from research.m58 import metrics
from research.m58.goal_labels import FULLTIME_INVALID
from research.m58.labels import FULLTIME_INCOMPLETE, NOT_FINISHED

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPROX = 1e-12


def obs(status, home, away, probabilities=None, **extra):
    row = {"status_short": status, "fulltime_home": home, "fulltime_away": away, **extra}
    if probabilities is not None:
        row["probabilities"] = probabilities
    return row


def counts(result):
    return (result.total_input, result.eligible_settled, result.scored, result.push_void, result.excluded_total)


# --- BTTS -----------------------------------------------------------------------------------------

def test_btts_yes_and_no_observed_with_exact_metrics():
    result = evaluate_btts([obs("FT", 1, 1, (0.8, 0.2)), obs("FT", 2, 0, (0.3, 0.7))])
    assert (result.market, result.line, result.line_type, result.scored_basis) == (BTTS, None, None, PROBABILITY_SCORED)
    assert dict(result.outcome_counts) == {"YES": 1, "NO": 1}
    assert counts(result) == (2, 2, 2, 0, 0) and dict(result.excluded_by_reason) == {}
    assert result.brier == pytest.approx((0.04 + 0.04 + 0.09 + 0.09) / 2, abs=APPROX)  # 0.13
    assert result.log_loss == pytest.approx((0.2231435513142097 + 0.35667494393873245) / 2, abs=APPROX)


def test_btts_correct_prediction_scores_better_than_incorrect():
    correct = evaluate_btts([obs("FT", 1, 1, (0.8, 0.2))])
    incorrect = evaluate_btts([obs("FT", 1, 1, (0.2, 0.8))])
    assert correct.brier == pytest.approx(0.08, abs=APPROX) and incorrect.brier == pytest.approx(1.28, abs=APPROX)
    assert correct.log_loss == pytest.approx(0.2231435513142097, abs=APPROX)
    assert incorrect.log_loss == pytest.approx(1.6094379124341003, abs=APPROX)


def test_btts_probability_extremes_use_eps_only_inside_the_log():
    certain_right = evaluate_btts([obs("FT", 1, 2, (1.0, 0.0)), obs("FT", 0, 0, (0, 1))])
    assert certain_right.brier == 0.0 and certain_right.log_loss == 0.0
    certain_wrong = evaluate_btts([obs("FT", 1, 2, (0.0, 1.0))])
    assert certain_wrong.brier == 2.0  # dos componentes, sin dividir entre 2
    assert certain_wrong.log_loss == pytest.approx(34.538776394910684, abs=APPROX)  # -log(1e-15)
    assert metrics.EPS == 1e-15


# --- Over/Under en líneas .5 ----------------------------------------------------------------------

def test_half_line_over_and_under_with_exact_metrics():
    result = evaluate_over_under([obs("FT", 2, 1, (0.7, 0.3)), obs("FT", 1, 1, (0.6, 0.4))], "2.5")
    assert (result.market, result.line, result.line_type, result.scored_basis) == (OVER_UNDER, "2.5", HALF_LINE, PROBABILITY_SCORED)
    assert dict(result.outcome_counts) == {"OVER": 1, "UNDER": 1} and counts(result) == (2, 2, 2, 0, 0)
    assert result.brier == pytest.approx((0.18 + 0.72) / 2, abs=APPROX)  # 0.45
    assert result.log_loss == pytest.approx((0.35667494393873245 + 0.916290731874155) / 2, abs=APPROX)


@pytest.mark.parametrize("line, home, away, expected", [("0.5", 0, 0, "UNDER"), ("0.5", 1, 0, "OVER"), ("0.5", 0, 1, "OVER"),
                                                          ("6.5", 3, 3, "UNDER"), ("6.5", 4, 3, "OVER"), ("1.5", 1, 0, "UNDER"),
                                                          ("1.5", 1, 1, "OVER")])
def test_half_line_boundary_totals(line, home, away, expected):
    result = evaluate_over_under([obs("FT", home, away, (0.5, 0.5))], line)
    assert dict(result.outcome_counts) == {"OVER": int(expected == "OVER"), "UNDER": int(expected == "UNDER")}
    assert result.push_void == 0 and result.brier == pytest.approx(0.5, abs=APPROX)


def test_line_metadata_and_line_validation():
    assert evaluate_over_under([], Decimal("2.50")).line == "2.5"
    assert evaluate_over_under([], "3.0").line_type == INTEGER_LINE
    for bad in ("2.25", "7.5", 2.5, None, Decimal("NaN")):
        with pytest.raises(ValueError):
            evaluate_over_under([], bad)  # error del llamador aunque no haya filas


# --- Líneas enteras: solo liquidación y recuento (D2) ---------------------------------------------

def test_integer_line_settlement_push_and_counts_without_metrics():
    rows = [obs("FT", 2, 1), obs("FT", 1, 0), obs("FT", 1, 1), obs("PEN", 2, 0), obs("NS", None, None)]
    result = evaluate_over_under(rows, "2.0")
    assert (result.line, result.line_type, result.scored_basis) == ("2.0", INTEGER_LINE, SETTLEMENT_ONLY)
    assert dict(result.outcome_counts) == {"OVER": 1, "UNDER": 1, "PUSH": 2}
    assert counts(result) == (5, 4, 2, 2, 1) and dict(result.excluded_by_reason) == {NOT_FINISHED: 1}
    assert result.brier is None and result.log_loss is None


def test_integer_line_never_reads_probabilities_and_push_is_never_a_win_or_loss():
    rows = [obs("FT", 1, 1, (0.2, 0.3, 0.5)), obs("FT", 3, 0, "garbage"), obs("FT", 0, 0, (math.nan, 1.0))]
    result = evaluate_over_under(rows, "2.0")  # sin masa de PUSH inventada ni renormalización: no se leen
    assert dict(result.outcome_counts) == {"OVER": 1, "UNDER": 1, "PUSH": 1}
    assert result.push_void == 1 and result.scored == 2 and result.brier is None and result.log_loss is None
    all_push = evaluate_over_under([obs("FT", 2, 1), obs("AET", 0, 3)], "3.0")
    assert counts(all_push) == (2, 2, 0, 2, 0) and dict(all_push.outcome_counts) == {"OVER": 0, "UNDER": 0, "PUSH": 2}


# --- Recuento de la muestra (D6, D7) --------------------------------------------------------------

def test_mixed_valid_and_excluded_keeps_canonical_reasons_and_totals():
    rows = [obs("FT", 1, 1, (0.6, 0.4)), obs("NS", None, None), obs("FT", None, 1), obs("FT", -1, 0), obs("AWD", 3, 0),
            obs("FT", 2, 2, (0.5, 0.5)), obs("FT", True, 0)]
    result = evaluate_btts(rows)
    assert counts(result) == (7, 2, 2, 0, 5)
    assert dict(result.excluded_by_reason) == {FULLTIME_INCOMPLETE: 1, FULLTIME_INVALID: 2, NOT_FINISHED: 2}
    assert list(result.excluded_by_reason) == sorted(result.excluded_by_reason)
    assert result.brier == pytest.approx((0.16 + 0.16 + 0.25 + 0.25) / 2, abs=APPROX)


def test_all_excluded_and_empty_input_return_none_metrics_and_preserved_counts():
    excluded = evaluate_over_under([obs("PST", None, None), obs("FT", 1, None), obs("FT", 1.0, 0)], "2.5")
    assert counts(excluded) == (3, 0, 0, 0, 3) and excluded.brier is None and excluded.log_loss is None
    assert dict(excluded.excluded_by_reason) == {FULLTIME_INCOMPLETE: 1, FULLTIME_INVALID: 1, NOT_FINISHED: 1}
    for empty in (evaluate_btts([]), evaluate_btts(()), evaluate_over_under([], "1.5"), evaluate_over_under([], "1.0")):
        assert counts(empty) == (0, 0, 0, 0, 0) and empty.brier is None and empty.log_loss is None
        assert dict(empty.excluded_by_reason) == {}


def test_accounting_identities_hold():
    for result in (evaluate_btts([obs("FT", 1, 1, (0.5, 0.5)), obs("NS", 0, 0)]),
                   evaluate_over_under([obs("FT", 1, 1), obs("FT", 3, 1), obs("CANC", None, None)], "2.0")):
        assert result.total_input == result.scored + result.push_void + result.excluded_total
        assert result.eligible_settled == result.scored + result.push_void


# --- Validación de probabilidades (D1, D4) --------------------------------------------------------

@pytest.mark.parametrize("probabilities", [(True, False), (1, False), (-0.1, 1.1), (1.2, -0.2), (math.nan, 0.5), (math.inf, 0.0),
                                           (-math.inf, 1.0), (0.5,), (0.3, 0.3, 0.4), [], (0.4, 0.4), (0.6, 0.6), ("0.5", "0.5"),
                                           (Decimal("0.5"), Decimal("0.5")), {"YES": 0.5, "NO": 0.5}, "0.5,0.5", 0.5],
                         ids=repr)
def test_invalid_probability_vectors_are_rejected(probabilities):
    with pytest.raises(ProbabilityEvidenceError):
        validate_binary_probabilities(probabilities)
    with pytest.raises(ProbabilityEvidenceError):
        evaluate_btts([obs("FT", 1, 1, probabilities)])


def test_missing_probability_evidence_fails_closed_and_is_never_an_exclusion():
    for row in (obs("FT", 1, 0), obs("FT", 1, 0, None) | {"probabilities": None}):
        with pytest.raises(ProbabilityEvidenceError, match="observación 1"):
            evaluate_btts([obs("FT", 1, 1, (0.5, 0.5)), row])
        with pytest.raises(ProbabilityEvidenceError):
            evaluate_over_under([row], "0.5")


def test_probabilities_of_excluded_rows_are_not_read():
    result = evaluate_btts([obs("NS", None, None, "garbage"), obs("FT", None, None)])
    assert counts(result) == (2, 0, 0, 0, 2) and result.brier is None


def test_sum_tolerance_boundary_and_no_normalization():
    assert SUM_TOLERANCE == 1e-9
    assert validate_binary_probabilities((0.0, 0.999999999)) == (0.0, 0.999999999)  # |suma - 1| = 9.99999997e-10
    assert validate_binary_probabilities((0.5, 0.5000000005)) == (0.5, 0.5000000005)
    for outside in ((0.5, 0.500000001), (0.5, 0.500000002)):  # |suma - 1| = 1.00000008e-9 y 2.0e-9
        with pytest.raises(ProbabilityEvidenceError):
            validate_binary_probabilities(outside)
    result = evaluate_btts([obs("FT", 1, 1, (0.7, 0.3000000001))])  # dentro de la tolerancia: se usa tal cual
    assert result.brier == (0.7 - 1) ** 2 + 0.3000000001 ** 2 and result.log_loss == -math.log(0.7)


# --- Estabilidad, pureza y forma de la entrada ----------------------------------------------------

def test_repeated_calls_are_identical_and_inputs_unchanged():
    rows = [obs("FT", 2, 1, [0.7, 0.3]), obs("AET", 1, 1, (0.4, 0.6)), obs("NS", None, None)]
    before = copy.deepcopy(rows)
    first = evaluate_over_under(rows, "2.5")
    assert all(evaluate_over_under(rows, "2.5") == first for _ in range(5)) and rows == before
    with pytest.raises(TypeError):
        first.excluded_by_reason["x"] = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.brier = 0.0


def test_ft_aet_pen_use_only_the_90_minute_score():
    # AET 1-1 a los 90' (3-1 tras la prórroga) y PEN 0-0: los campos extra no cuentan
    rows = [obs("AET", 1, 1, (0.5, 0.5), goals_home=3, goals_away=1), obs("PEN", 0, 0, (0.5, 0.5), penalty_home=4, penalty_away=3),
            obs("FT", 2, 1, (0.5, 0.5))]
    assert dict(evaluate_over_under(rows, "2.5").outcome_counts) == {"OVER": 1, "UNDER": 2}
    assert dict(evaluate_btts(rows).outcome_counts) == {"YES": 2, "NO": 1}
    assert dict(evaluate_over_under([obs("AET", 1, 1)], "2.0").outcome_counts) == {"OVER": 0, "UNDER": 0, "PUSH": 1}


def test_malformed_collections_and_observations_are_rejected():
    for bad in ((obs("FT", 1, 1, (0.5, 0.5)) for _ in range(1)), None, obs("FT", 1, 1, (0.5, 0.5)), "rows"):
        with pytest.raises(ObservationError):
            evaluate_btts(bad)
    for bad_row in ([("FT", 1, 1)], [{"status_short": "FT", "fulltime_home": 1}]):
        with pytest.raises(ObservationError):
            evaluate_over_under(bad_row, "2.5")


def test_binary_metrics_match_the_m58_formulas_on_an_embedded_vector():
    rows = [obs("FT", 1, 1, (0.8, 0.2)), obs("FT", 2, 0, (0.3, 0.7)), obs("FT", 0, 0, (0.55, 0.45))]
    result = evaluate_btts(rows)
    probs = [[0.8, 0.2, 0.0], [0.3, 0.7, 0.0], [0.55, 0.45, 0.0]]  # YES -> H, NO -> D; la tercera clase nunca ocurre
    labels = ["H", "D", "D"]
    assert result.brier == pytest.approx(metrics.brier(probs, labels), abs=1e-15)
    assert result.log_loss == pytest.approx(metrics.log_loss(probs, labels), abs=1e-15)


def test_the_module_imports_without_the_app_or_a_database():
    code = ("import sys; import research.goals_markets.evaluation; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"
