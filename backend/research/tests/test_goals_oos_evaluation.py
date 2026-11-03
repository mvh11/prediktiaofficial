"""MOD-DEV-109A: evaluación walk-forward fuera de muestra de probabilidades de goles a 90'. Sin BD, sin red, sin app.
    python -m pytest research/tests/test_goals_oos_evaluation.py -q   (desde backend/)
Partidos, marcadores, instantes y probabilidades SINTÉTICOS: ni histórico real, ni procedencia acreditada, ni calibración."""

import copy
import json
import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research.goals_oos_evaluation import (ALL_ABSTAINED, AVAILABLE, BASE_RATE_UNAVAILABLE, BINARY_TWO_COMPONENT, COMPARED,
                                           LABEL_NOT_KNOWN_AT_TRAIN_END, NO_HOLDOUT_ROWS, NO_SETTLED_OUTCOMES, OUTCOME_OBSERVED_AT_MISSING,
                                           UNAVAILABLE, UNSUPPORTED_LINE, UNSUPPORTED_MARKET, UNVERIFIED_RESEARCH_ONLY, WAIT_REAL_SOURCE,
                                           ConfigError, DuplicateObservationError, FixtureConflictError, LeakageError, ObservationError,
                                           OOSInputError, ProbabilityInputError, TemporalError, evaluate_goals_oos)
from research.goals_markets.integer_evaluation import THREE_CLASS
from research.m58.labels import NOT_FINISHED
from research.quote_temporal_eligibility import BTTS, ONE_X_TWO, OVER_UNDER
from research.venue_goal_rates.baseline import ESTIMATE, NO_ESTIMATE

UTC = timezone.utc
O1, O2 = datetime(2025, 3, 1, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC)
HOLDOUT, CALIBRATION, EMBARGO = timedelta(days=28), timedelta(days=14), timedelta(days=3)
# Pliegue 0: train < 9-feb, calibration [12-feb, 26-feb), holdout [1-mar, 29-mar)
# Pliegue 1: train < 12-mar, calibration [15-mar, 29-mar), holdout [1-abr, 29-abr)
TRAIN_END_0, CAL_START_0, CAL_END_0 = datetime(2025, 2, 9, tzinfo=UTC), datetime(2025, 2, 12, tzinfo=UTC), datetime(2025, 2, 26, tzinfo=UTC)
TRAIN_END_1 = datetime(2025, 3, 12, tzinfo=UTC)
CONFIG = {"model_id": "SYNTHETIC_PRODUCER_109A", "origins": (O1, O2), "holdout_length": HOLDOUT, "calibration_length": CALIBRATION,
          "embargo": EMBARGO}

DEFAULT_PROBS = {BTTS: {"YES": 0.55, "NO": 0.45}, "half": {"OVER": 0.52, "UNDER": 0.48}, "integer": {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.25}}


def obs(fixture_id, kickoff, market=BTTS, line=None, *, status=ESTIMATE, probs="default", score=(1, 1), status_short="FT", observed="default",
        cutoff=None, feature=None):
    cutoff = cutoff or kickoff - timedelta(hours=1)
    if probs == "default":
        probs = None if status == NO_ESTIMATE else copy.deepcopy(
            DEFAULT_PROBS[BTTS] if market == BTTS else DEFAULT_PROBS["integer" if line and line.endswith(".0") else "half"])
    return {"fixture_id": fixture_id, "kickoff_at": kickoff, "cutoff_at": cutoff, "feature_observed_at": feature or cutoff,
            "market": market, "line": line, "status": status, "probabilities": probs,
            "outcome": {"status_short": status_short, "fulltime_home": score[0], "fulltime_away": score[1],
                        "observed_at": kickoff + timedelta(hours=2) if observed == "default" else observed}}


def day(month, d, hour=18):
    return datetime(2025, month, d, hour, tzinfo=UTC)


def group(report, fold, market=BTTS, line=None):
    (g,) = [g for g in report["folds"][fold]["groups"] if g["market"] == market and g["line"] == line]
    return g


def brier2(p, y):
    return (p - y) ** 2 + ((1 - p) - (1 - y)) ** 2


# ---------------------------------------------------------------------------------------------- 1. pliegues cronológicos

def test_01_folds_are_chronological_disjoint_and_walk_forward():
    rows = [obs(1, day(1, 10)), obs(2, day(2, 15)), obs(3, day(3, 5)), obs(4, day(3, 20)), obs(5, day(4, 10)), obs(6, day(6, 1))]
    report = evaluate_goals_oos(rows, CONFIG)
    f0, f1 = report["folds"]
    assert f0["boundaries"] == {"train_end": TRAIN_END_0.isoformat(), "calibration_start": CAL_START_0.isoformat(),
                                "calibration_end": CAL_END_0.isoformat(), "holdout_start": O1.isoformat(),
                                "holdout_end": (O1 + HOLDOUT).isoformat()}
    assert f0["cohort_fixtures"] == {"train": 1, "calibration": 1, "holdout": 2, "embargoed": 0}
    # el holdout del pliegue 0 (3 y 4) es pasado del pliegue 1: 3 entra en su train, 4 en su calibración
    assert f1["cohort_fixtures"] == {"train": 3, "calibration": 1, "holdout": 1, "embargoed": 0}
    assert report["counts"]["supported_in_no_holdout"] == 3  # 1, 2 y 6 nunca son holdout


def test_01b_holdouts_must_not_overlap_and_origins_must_increase():
    with pytest.raises(ConfigError, match="solaparía"):
        evaluate_goals_oos([], dict(CONFIG, origins=(O1, O1 + timedelta(days=10))))
    with pytest.raises(ConfigError, match="crecientes"):
        evaluate_goals_oos([], dict(CONFIG, origins=(O2, O1)))
    for bad in ({"origins": ()}, {"origins": (datetime(2025, 3, 1),)}, {"holdout_length": timedelta(0)}, {"embargo": timedelta(days=-1)},
                {"calibration_length": 3}, {"model_id": " x"}, {"model_id": ""}):
        with pytest.raises(ConfigError):
            evaluate_goals_oos([], dict(CONFIG, **bad))


def test_01c_a_caller_admission_flag_cannot_raise_the_evidence_status():
    with pytest.raises(ConfigError, match="sobran"):
        evaluate_goals_oos([], dict(CONFIG, source_admitted=True))
    report = evaluate_goals_oos([obs(1, day(3, 5))], CONFIG)
    assert report["evidence_status"] == UNVERIFIED_RESEARCH_ONLY and report["source_status"] == WAIT_REAL_SOURCE
    assert {"RESEARCH_ONLY", "DATASET_CALLER_SUPPLIED", "POISSON_REFERENCE_NOT_CALIBRATED", "NOT_CERTIFIED"} <= set(report["classifications"])
    text = json.dumps(report)
    assert "CERTIFIED" not in text.replace("NOT_CERTIFIED", "") and "PASS" not in text


# ------------------------------------------------------------------------------------------------------- 2. embargo

def test_02_embargo_gaps_belong_to_no_set():
    rows = [obs(1, TRAIN_END_0 - timedelta(seconds=1)), obs(2, TRAIN_END_0), obs(3, CAL_START_0 - timedelta(seconds=1)), obs(4, CAL_START_0),
            obs(5, CAL_END_0 - timedelta(seconds=1)), obs(6, CAL_END_0), obs(7, O1 - timedelta(seconds=1)), obs(8, O1)]
    f0 = evaluate_goals_oos(rows, CONFIG)["folds"][0]
    assert f0["cohort_rows"] == {"train": 1, "calibration": 2, "holdout": 1, "embargoed": 4}


def test_02b_embargo_is_configurable():
    rows = [obs(1, O1 - timedelta(days=1)), obs(2, O1 - timedelta(days=19))]  # 28-feb y 10-feb: los dos huecos de embargo
    with_embargo = evaluate_goals_oos(rows, CONFIG)["folds"][0]
    without = evaluate_goals_oos(rows, dict(CONFIG, embargo=timedelta(0)))["folds"][0]
    assert with_embargo["cohort_rows"] == {"train": 0, "calibration": 0, "holdout": 0, "embargoed": 2}
    assert without["cohort_rows"] == {"train": 1, "calibration": 1, "holdout": 0, "embargoed": 0}
    assert without["boundaries"]["train_end"] == without["boundaries"]["calibration_start"] == (O1 - CALIBRATION).isoformat()


# -------------------------------------------------------------------------------------- 3. partido en dos conjuntos

def test_03_a_fixture_cannot_straddle_boundaries_with_conflicting_kickoffs_or_outcomes():
    with pytest.raises(FixtureConflictError):  # train por una fila, holdout por otra
        evaluate_goals_oos([obs(1, day(1, 10)), obs(1, day(3, 5), OVER_UNDER, "2.5")], CONFIG)
    with pytest.raises(FixtureConflictError):
        evaluate_goals_oos([obs(1, day(3, 5)), obs(1, day(3, 5), OVER_UNDER, "2.5", score=(3, 0))], CONFIG)
    same_instant = day(3, 5).astimezone(timezone(timedelta(hours=-3)))
    report = evaluate_goals_oos([obs(1, day(3, 5)), obs(1, same_instant, OVER_UNDER, "2.5")], CONFIG)
    assert report["counts"]["fixtures"] == 1 and report["folds"][0]["cohort_fixtures"]["holdout"] == 1


# --------------------------------------------------------------------------------------------- 4. identidad duplicada

def test_04_duplicate_fixture_market_line_is_rejected_even_if_identical():
    row = obs(1, day(3, 5), OVER_UNDER, "2.5")
    with pytest.raises(DuplicateObservationError):
        evaluate_goals_oos([row, copy.deepcopy(row)], CONFIG)
    with pytest.raises(DuplicateObservationError):
        evaluate_goals_oos([obs(1, day(3, 5)), obs(1, day(3, 5), status=NO_ESTIMATE)], CONFIG)
    ok = evaluate_goals_oos([row, obs(1, day(3, 5), OVER_UNDER, "3.5"), obs(1, day(3, 5), OVER_UNDER, "3.0")], CONFIG)
    assert ok["counts"]["rows"] == 3 and ok["counts"]["fixtures"] == 1


def test_04b_malformed_identities_are_typed_errors():
    for change in ({"fixture_id": 0}, {"fixture_id": True}, {"fixture_id": "1"}, {"market": 7}, {"line": 2.5}, {"status": "estimate"}):
        with pytest.raises(ObservationError):
            evaluate_goals_oos([dict(obs(1, day(3, 5), OVER_UNDER, "2.5"), **change)], CONFIG)
    with pytest.raises(ObservationError):
        evaluate_goals_oos([obs(1, day(3, 5), BTTS, "2.5")], CONFIG)
    with pytest.raises(ObservationError):
        evaluate_goals_oos([obs(1, day(3, 5), OVER_UNDER, None)], CONFIG)
    extra = dict(obs(1, day(3, 5)), source="x")
    with pytest.raises(ObservationError, match="sobran"):
        evaluate_goals_oos([extra], CONFIG)
    with pytest.raises(ObservationError):
        evaluate_goals_oos({"rows": []}, CONFIG)


# ------------------------------------------------------------------------------------ 5. fuga de entradas del productor

def test_05_feature_observed_one_second_after_named_t_is_leakage():
    t = day(3, 5) - timedelta(hours=1)
    assert evaluate_goals_oos([obs(1, day(3, 5), cutoff=t, feature=t)], CONFIG)["counts"]["rows"] == 1
    with pytest.raises(LeakageError):
        evaluate_goals_oos([obs(1, day(3, 5), cutoff=t, feature=t + timedelta(seconds=1))], CONFIG)
    with pytest.raises(LeakageError):  # también en filas de train: fail-closed sobre todo el conjunto
        evaluate_goals_oos([obs(1, day(3, 5)), obs(2, day(1, 5), feature=day(1, 5) - timedelta(minutes=59))], CONFIG)


def test_05b_prediction_time_must_precede_kickoff_and_instants_need_a_zone():
    with pytest.raises(TemporalError):
        evaluate_goals_oos([obs(1, day(3, 5), cutoff=day(3, 5))], CONFIG)
    with pytest.raises(TemporalError):
        evaluate_goals_oos([obs(1, datetime(2025, 3, 5, 18))], CONFIG)
    assert issubclass(LeakageError, TemporalError) and issubclass(TemporalError, OOSInputError)


# ------------------------------------------------------------------------------------- 6. fuga de etiquetas de train

def test_06_train_labels_observed_after_the_fit_boundary_are_not_used():
    known = [obs(10 + i, day(1, 5 + i), score=(1, 1)) for i in range(3)]  # YES conocidos antes de train_end
    late = obs(20, day(2, 1), score=(0, 0), observed=TRAIN_END_0 + timedelta(seconds=1))  # NO conocido 1 s después
    edge = obs(21, day(2, 2), score=(1, 0), observed=TRAIN_END_0)  # NO conocido exactamente en la frontera: entra
    report = evaluate_goals_oos(known + [late, edge, obs(1, day(3, 5))], CONFIG)
    base = group(report, 0)["base_rate"]
    assert base["train_labels_used"] == 4 and base["train_excluded_by_reason"] == {LABEL_NOT_KNOWN_AT_TRAIN_END: 1}
    assert base["probabilities"] == {"YES": 0.75, "NO": 0.25} and base["fit_boundary"] == TRAIN_END_0.isoformat()


def test_06b_holdout_and_calibration_outcomes_never_reach_the_base_rate():
    train = [obs(10, day(1, 5), score=(1, 1)), obs(11, day(1, 6), score=(0, 1))]
    a = evaluate_goals_oos(train + [obs(1, day(3, 5), score=(1, 1)), obs(2, day(2, 15), score=(1, 1))], CONFIG)
    b = evaluate_goals_oos(train + [obs(1, day(3, 5), score=(0, 0)), obs(2, day(2, 15), score=(0, 0))], CONFIG)
    assert group(a, 0)["base_rate"] == group(b, 0)["base_rate"]
    assert a["folds"][0]["cohort_rows"] == b["folds"][0]["cohort_rows"]  # la cohorte no depende del resultado


# ---------------------------------------------------------------------------- 7. etiquetas retrospectivas válidas

def test_07_outcome_learned_after_prediction_time_is_a_valid_label():
    late = obs(1, day(3, 5), score=(2, 1), observed=day(3, 5) + timedelta(days=10))
    g = group(evaluate_goals_oos([obs(10, day(1, 5)), late], CONFIG), 0)
    assert g["holdout_settled"] == 1 and g["model"]["scored"] == 1 and g["status"] == COMPARED


def test_07b_final_status_observed_at_or_before_kickoff_is_impossible():
    for observed in (day(3, 5), day(3, 5) - timedelta(minutes=1)):
        with pytest.raises(TemporalError):
            evaluate_goals_oos([obs(1, day(3, 5), observed=observed)], CONFIG)
    # un estado no final observado antes del saque no es contradictorio (no liquida)
    g = group(evaluate_goals_oos([obs(1, day(3, 5), status_short="NS", score=(None, None), observed=day(3, 4))], CONFIG), 0)
    assert g["holdout_excluded_by_reason"] == {NOT_FINISHED: 1}


# ------------------------------------------------------------------------------------- 8. BTTS y líneas .5

def test_08_btts_scores_match_the_published_binary_definition():
    train = [obs(10, day(1, 5), score=(1, 1)), obs(11, day(1, 6), score=(0, 1)), obs(12, day(1, 7), score=(2, 2)), obs(13, day(1, 8), score=(0, 0))]
    hold = [obs(1, day(3, 5), score=(1, 1), probs={"YES": 0.7, "NO": 0.3}), obs(2, day(3, 6), score=(0, 2), probs={"YES": 0.6, "NO": 0.4})]
    g = group(evaluate_goals_oos(train + hold, CONFIG), 0)
    assert g["metric_basis"] == BINARY_TWO_COMPONENT and g["line_type"] is None and g["status"] == COMPARED
    assert g["outcome_counts"] == {"YES": 1, "NO": 1}
    assert g["model"]["brier"] == pytest.approx((brier2(0.7, 1) + brier2(0.6, 0)) / 2)
    assert g["model"]["log_loss"] == pytest.approx(-(math.log(0.7) + math.log(0.4)) / 2)
    assert g["base_rate"]["probabilities"] == {"YES": 0.5, "NO": 0.5}
    assert g["base_rate_on_model_set"]["brier"] == pytest.approx(0.5) and g["base_rate_on_model_set"]["log_loss"] == pytest.approx(math.log(2))


def test_08b_half_line_over_under_uses_its_own_group_and_line():
    rows = [obs(10, day(1, 5), OVER_UNDER, "2.5", score=(2, 1)), obs(1, day(3, 5), OVER_UNDER, "2.5", score=(1, 1), probs={"OVER": 0.3, "UNDER": 0.7}),
            obs(1, day(3, 5), OVER_UNDER, "1.5", score=(1, 1), probs={"OVER": 0.8, "UNDER": 0.2})]
    report = evaluate_goals_oos(rows, CONFIG)
    g25, g15 = group(report, 0, OVER_UNDER, "2.5"), group(report, 0, OVER_UNDER, "1.5")
    assert g25["line_type"] == "HALF" and g25["outcome_counts"] == {"OVER": 0, "UNDER": 1}
    assert g25["model"]["brier"] == pytest.approx(brier2(0.3, 0)) and g25["base_rate"]["probabilities"] == {"OVER": 1.0, "UNDER": 0.0}
    assert g15["outcome_counts"] == {"OVER": 1, "UNDER": 0} and g15["status"] == BASE_RATE_UNAVAILABLE  # sin train en 1.5
    assert g15["base_rate"]["status"] == UNAVAILABLE and g15["base_rate_on_model_set"] is None and g15["coverage"]["compared"] == 0


# ------------------------------------------------------------------------------------------ 9. líneas enteras y PUSH

def test_09_integer_line_preserves_push_outcomes_and_probabilities():
    train = [obs(10, day(1, 5), OVER_UNDER, "2.0", score=(1, 1)), obs(11, day(1, 6), OVER_UNDER, "2.0", score=(3, 0)),
             obs(12, day(1, 7), OVER_UNDER, "2.0", score=(0, 2)), obs(13, day(1, 8), OVER_UNDER, "2.0", score=(0, 0))]
    p = {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.25}
    hold = [obs(1, day(3, 5), OVER_UNDER, "2.0", score=(1, 1), probs=p), obs(2, day(3, 6), OVER_UNDER, "2.0", score=(2, 1), probs=p)]
    g = group(evaluate_goals_oos(train + hold, CONFIG), 0, OVER_UNDER, "2.0")
    assert g["metric_basis"] == THREE_CLASS and g["line_type"] == "INTEGER"
    assert g["outcome_counts"] == {"OVER": 1, "UNDER": 0, "PUSH": 1} and g["holdout_settled"] == 2
    assert g["base_rate"]["probabilities"] == {"OVER": 0.25, "UNDER": 0.25, "PUSH": 0.5}
    push_brier = 0.4 ** 2 + 0.35 ** 2 + 0.75 ** 2
    over_brier = 0.6 ** 2 + 0.35 ** 2 + 0.25 ** 2
    assert g["model"]["brier"] == pytest.approx((push_brier + over_brier) / 2)
    assert g["model"]["log_loss"] == pytest.approx(-(math.log(0.25) + math.log(0.4)) / 2)


def test_09b_integer_line_requires_all_three_classes():
    with pytest.raises(ProbabilityInputError):
        evaluate_goals_oos([obs(1, day(3, 5), OVER_UNDER, "2.0", probs={"OVER": 0.5, "UNDER": 0.5})], CONFIG)
    with pytest.raises(ProbabilityInputError):
        evaluate_goals_oos([obs(1, day(3, 5), OVER_UNDER, "2.5", probs={"OVER": 0.5, "UNDER": 0.4, "PUSH": 0.1})], CONFIG)


# ------------------------------------------------------------------------------------------ 10. mercados no soportados

def test_10_unsupported_markets_and_lines_are_reported_not_scored():
    rows = [obs(1, day(3, 5)), obs(1, day(3, 5), ONE_X_TWO, None, probs=object()), obs(1, day(3, 5), OVER_UNDER, "2.25", probs="??"),
            obs(1, day(3, 5), OVER_UNDER, "2.50", probs=None)]
    report = evaluate_goals_oos(rows, CONFIG)
    assert report["counts"]["supported"] == 1 and report["counts"]["unsupported"] == 3
    assert report["unsupported"] == [{"market": ONE_X_TWO, "line": None, "reason": UNSUPPORTED_MARKET, "rows": 1},
                                     {"market": OVER_UNDER, "line": "2.25", "reason": UNSUPPORTED_LINE, "rows": 1},
                                     {"market": OVER_UNDER, "line": "2.50", "reason": UNSUPPORTED_LINE, "rows": 1}]
    assert [(g["market"], g["line"]) for g in report["folds"][0]["groups"]] == [(BTTS, None)]


# ------------------------------------------------------------------------------------- 11. NO_ESTIMATE y cobertura

def test_11_no_estimate_abstentions_are_kept_and_coverage_is_disclosed():
    train = [obs(10, day(1, 5), score=(1, 1)), obs(11, day(1, 6), score=(0, 1))]
    hold = [obs(1, day(3, 5)), obs(2, day(3, 6), score=(0, 0)), obs(3, day(3, 7)), obs(4, day(3, 8), status=NO_ESTIMATE),
            obs(5, day(3, 9), status=NO_ESTIMATE, score=(0, 1)), obs(6, day(3, 10), status_short="PST", score=(None, None)),
            obs(7, day(3, 11), observed=None)]
    g = group(evaluate_goals_oos(train + hold, CONFIG), 0)
    assert (g["holdout_rows"], g["holdout_estimate"], g["holdout_no_estimate"], g["holdout_settled"]) == (7, 5, 2, 5)
    assert g["holdout_excluded_by_reason"] == {NOT_FINISHED: 1, OUTCOME_OBSERVED_AT_MISSING: 1}
    assert g["coverage"] == {"settled": 5, "model_scored": 3, "abstained_settled": 2, "compared": 3}
    assert g["base_rate_on_model_set"]["scored"] == 3 and g["base_rate_on_all_settled"]["scored"] == 5


def test_11b_all_abstained_no_outcomes_and_empty_groups_have_explicit_status():
    rows = [obs(1, day(3, 5), status=NO_ESTIMATE), obs(2, day(4, 5), OVER_UNDER, "2.5", observed=None)]
    report = evaluate_goals_oos(rows, CONFIG)
    assert group(report, 0)["status"] == ALL_ABSTAINED and group(report, 0)["model"] == {"scored": 0, "brier": None, "log_loss": None}
    assert group(report, 0, OVER_UNDER, "2.5")["status"] == NO_HOLDOUT_ROWS
    assert group(report, 1, OVER_UNDER, "2.5")["status"] == NO_SETTLED_OUTCOMES
    assert report["counts"]["no_estimate"] == 1 and report["counts"]["estimate"] == 1


def test_11c_estimate_and_no_estimate_must_be_consistent_with_probabilities():
    with pytest.raises(ProbabilityInputError):
        evaluate_goals_oos([obs(1, day(3, 5), probs=None)], CONFIG)
    with pytest.raises(ObservationError, match=NO_ESTIMATE):
        evaluate_goals_oos([obs(1, day(3, 5), status=NO_ESTIMATE, probs={"YES": 0.5, "NO": 0.5})], CONFIG)


# --------------------------------------------------------------------------------------- 12. probabilidades no válidas

@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -0.1, 1.5, True, "0.5", Decimal("0.5"), None, 10 ** 400])
def test_12_nonfinite_or_malformed_probabilities_are_rejected(bad):
    with pytest.raises(ProbabilityInputError):
        evaluate_goals_oos([obs(1, day(3, 5), probs={"YES": bad, "NO": 0.5})], CONFIG)


def test_12b_probabilities_must_sum_to_one_and_use_exact_classes():
    for probs in ({"YES": 0.6, "NO": 0.6}, {"YES": 0.5}, {"YES": 0.5, "NO": 0.5, "PUSH": 0.0}, {"Y": 0.5, "N": 0.5}, [0.5, 0.5]):
        with pytest.raises(ProbabilityInputError):
            evaluate_goals_oos([obs(1, day(3, 5), probs=probs)], CONFIG)
    with pytest.raises(ProbabilityInputError):
        evaluate_goals_oos([obs(1, day(3, 5), OVER_UNDER, "3.0", probs={"OVER": 0.5, "UNDER": 0.5, "PUSH": math.nan})], CONFIG)


# -------------------------------------------------------------------------------------- 13. determinismo e integridad

def dataset():
    return [obs(10, day(1, 5), score=(1, 1)), obs(11, day(1, 6), OVER_UNDER, "2.0", score=(1, 1)), obs(12, day(1, 7), OVER_UNDER, "2.5"),
            obs(1, day(3, 5)), obs(1, day(3, 5), OVER_UNDER, "2.0"), obs(2, day(3, 6), OVER_UNDER, "2.5", score=(3, 1)),
            obs(3, day(4, 5), status=NO_ESTIMATE), obs(4, day(4, 6), ONE_X_TWO, None, probs=None)]


def test_13_repeated_and_reordered_evaluation_is_identical():
    rows = dataset()
    before = copy.deepcopy(rows)
    first = evaluate_goals_oos(rows, CONFIG)
    assert rows == before  # las entradas no se modifican
    shuffled = rows[:]
    random.Random(109).shuffle(shuffled)
    assert evaluate_goals_oos(rows, CONFIG) == first == evaluate_goals_oos(shuffled, dict(CONFIG, origins=list(CONFIG["origins"])))
    assert json.loads(json.dumps(first)) == first  # JSON-serializable sin pérdida
    assert len(first["report_digest"]) == 64 and len(first["dataset_fingerprint"]) == 64


# ------------------------------------------------------------------------------------------ 14. huellas y mutaciones

def test_14_any_input_or_config_mutation_changes_the_digests():
    base = evaluate_goals_oos(dataset(), CONFIG)
    mutated = dataset()
    mutated[3]["probabilities"] = {"YES": 0.55 + 1e-12, "NO": 0.45 - 1e-12}
    m = evaluate_goals_oos(mutated, CONFIG)
    assert m["dataset_fingerprint"] != base["dataset_fingerprint"] and m["report_digest"] != base["report_digest"]
    assert m["config_digest"] == base["config_digest"]
    later = dataset()
    later[0]["outcome"]["observed_at"] += timedelta(seconds=1)
    assert evaluate_goals_oos(later, CONFIG)["dataset_fingerprint"] != base["dataset_fingerprint"]
    for change in ({"embargo": timedelta(days=4)}, {"model_id": "OTHER"}, {"origins": (O1,)}):
        other = evaluate_goals_oos(dataset(), dict(CONFIG, **change))
        assert other["config_digest"] != base["config_digest"] and other["report_digest"] != base["report_digest"]
    tampered = dict(base, evidence_status="CERTIFIED")
    from research.m59.canonical import sha256_hex
    assert sha256_hex({k: v for k, v in tampered.items() if k != "report_digest"}) != base["report_digest"]


def test_15_module_is_pure_and_offline():
    import inspect

    import research.goals_oos_evaluation as module
    source = inspect.getsource(module)
    for forbidden in ("sqlalchemy", "requests", "urllib", "socket", "os.environ", "open(", "datetime.now", "time.time", "random"):
        assert forbidden not in source
    assert AVAILABLE == "AVAILABLE"
