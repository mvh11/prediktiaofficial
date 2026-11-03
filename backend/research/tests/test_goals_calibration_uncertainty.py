"""MOD-DEV-109B: fiabilidad (ECE) e incertidumbre por bloques de semana de probabilidades de goles FT90. Sin BD, red ni app.
    python -m pytest research/tests/test_goals_calibration_uncertainty.py -q   (desde backend/)
Filas sintéticas del llamador; los valores esperados están escritos a mano (probabilidades diádicas, aritmética exacta) o
recalculados aquí sin la implementación. Ningún dato es un recibo de proveedor, una admisión de Data Integrity ni un histórico PIT."""

import copy
import dataclasses
import hashlib
import json
import math
import os
import random
import subprocess
import sys
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from research.goals_calibration_uncertainty import (BOOTSTRAP_METHOD, CALIBRATION_STATUS, ECE_METHOD_BINARY, ECE_METHOD_THREE_CLASS,
                                                     INTERVAL_SEMANTICS, LIMITATIONS, PIT_STATUS, RECEIPT_STATUS, ConfigError,
                                                     DuplicateSampleError, InconsistentPushLabelError, InputError, LabelError,
                                                     ProbabilityError, ReliabilityUncertaintyReport, RowError, UncertaintyConfig,
                                                     UnsupportedTargetError, WeekError, block_draw, canonical_report_json,
                                                     goals_reliability_uncertainty)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BTTS, OU = "BTTS_FT90", "OVER_UNDER_FT90"
CFG = UncertaintyConfig(seed=7, iterations=200)
CFG2W = UncertaintyConfig(seed=7, iterations=200, min_weeks=2)


def row(sample_id, fixture_id, week, probabilities, label, *, market=OU, line="2.5", status="ESTIMATE", **extra):
    return {"sample_id": sample_id, "fixture_id": fixture_id, "week": week, "market": market, "line": line,
            "estimate_status": status, "probabilities": probabilities, "label": label, **extra}


def btts(sample_id, fixture_id, week, p_yes, label, **kw):
    probs = None if p_yes is None else {"YES": p_yes, "NO": 1 - p_yes}
    return row(sample_id, fixture_id, week, probs, label, market=BTTS, line=None, **kw)


def ou(sample_id, fixture_id, week, p_over, label, *, line="2.5", **kw):
    return row(sample_id, fixture_id, week, None if p_over is None else {"OVER": p_over, "UNDER": 1 - p_over}, label, line=line, **kw)


def ou3(sample_id, fixture_id, week, over, under, push, label, *, line="2.0", **kw):
    return row(sample_id, fixture_id, week, {"OVER": over, "UNDER": under, "PUSH": push}, label, line=line, **kw)


def run(rows, market=OU, line="2.5", config=CFG):
    return goals_reliability_uncertainty(rows, market=market, line=line, config=config)


def nonempty(cls):
    return [(b.index, b.count, b.mean_predicted, b.observed_frequency, b.abs_gap) for b in cls.bins if b.count]


BTTS_ROWS = [btts("s1", 1, "2026-W01", 0.25, "YES"), btts("s2", 2, "2026-W01", 0.25, "NO"),
             btts("s3", 3, "2026-W02", 0.75, "YES"), btts("s4", 4, "2026-W02", 0.75, "YES")]
INT_ROWS = [ou3("s1", 1, "2026-W01", 0.5, 0.25, 0.25, "OVER"), ou3("s2", 2, "2026-W01", 0.5, 0.25, 0.25, "PUSH"),
            ou3("s3", 3, "2026-W02", 0.25, 0.5, 0.25, "UNDER"), ou3("s4", 4, "2026-W02", 0.25, 0.5, 0.25, "UNDER")]


# --- A. Mercados y fiabilidad ------------------------------------------------------------------------------------------------

def test_btts_binary_reliability_and_ece():
    r = run(BTTS_ROWS, market=BTTS, line=None)
    assert (r.market, r.line, r.line_type, r.metric_basis, r.ece_method) == (BTTS, None, None, "BINARY_EVENT", ECE_METHOD_BINARY)
    assert r.settlement_convention == "FT90_V1"
    (cls,) = r.classes
    assert cls.event == "YES"
    # bin 2: n=2, media 0.25, frecuencia 0.5; bin 7: n=2, media 0.75, frecuencia 1.0
    assert nonempty(cls) == [(2, 2, 0.25, 0.5, 0.25), (7, 2, 0.75, 1.0, 0.25)]
    assert cls.ece == r.ece == 0.25  # (2 * 0.25 + 2 * 0.25) / 4
    assert (cls.mean_predicted, cls.observed_frequency) == (0.5, 0.75)
    assert dict(r.outcome_counts) == {"YES": 3, "NO": 1}
    assert len(cls.bins) == 10 and cls.bins[0].interval == "[0.0, 0.1)" and cls.bins[9].interval == "[0.9, 1.0]"


def test_half_line_over_under_binary_reliability_and_edges():
    rows = [ou("a", 1, "2026-W01", 0.0, "UNDER"), ou("b", 2, "2026-W01", 1.0, "OVER"), ou("c", 3, "2026-W01", 0.5, "OVER"),
            ou("d", 4, "2026-W02", 0.5, "OVER"), ou("e", 5, "2026-W02", 0.5, "OVER"), ou("f", 6, "2026-W02", 0.5, "UNDER")]
    r = run(rows)
    assert (r.line_type, r.metric_basis) == ("HALF", "BINARY_EVENT")
    (cls,) = r.classes
    assert cls.event == "OVER"
    assert nonempty(cls) == [(0, 1, 0.0, 0.0, 0.0), (5, 4, 0.5, 0.75, 0.25), (9, 1, 1.0, 1.0, 0.0)]
    assert r.ece == 1 / 6  # |2 - 3| / 6, redondeado una vez
    assert dict(r.outcome_counts) == {"OVER": 4, "UNDER": 2}


def test_bin_edges_are_exact_double_comparisons():
    below = math.nextafter(0.3, 0)
    r = run([btts("a", 1, "2026-W01", 0.3, "YES"), btts("b", 2, "2026-W01", below, "NO")], market=BTTS, line=None)
    counts = {b.index: b.count for b in r.classes[0].bins if b.count}
    assert counts == {2: 1, 3: 1}


def test_integer_line_three_class_reliability_preserves_push_mass():
    r = run(INT_ROWS, line="2.0")
    assert (r.line_type, r.metric_basis, r.ece_method) == ("INTEGER", "THREE_CLASS_OVER_UNDER_PUSH", ECE_METHOD_THREE_CLASS)
    over, under, push = r.classes
    assert (over.event, under.event, push.event) == ("OVER", "UNDER", "PUSH")
    assert nonempty(over) == [(2, 2, 0.25, 0.0, 0.25), (5, 2, 0.5, 0.5, 0.0)] and over.ece == 0.125
    assert nonempty(under) == [(2, 2, 0.25, 0.0, 0.25), (5, 2, 0.5, 1.0, 0.5)] and under.ece == 0.375
    assert nonempty(push) == [(2, 4, 0.25, 0.25, 0.0)] and push.ece == 0.0
    assert (push.mean_predicted, push.observed_frequency) == (0.25, 0.25)  # la masa PUSH se informa, no se reparte
    assert r.ece == 1 / 6  # (0.125 + 0.375 + 0) / 3
    assert dict(r.outcome_counts) == {"OVER": 1, "UNDER": 2, "PUSH": 1}
    assert r.all_push is False


def test_integer_line_is_never_scored_as_binary():
    with pytest.raises(ProbabilityError):
        run([ou("a", 1, "2026-W01", 0.5, "OVER", line="2.0")], line="2.0")  # sin PUSH en una línea entera
    with pytest.raises(ProbabilityError):
        run([ou3("a", 1, "2026-W01", 0.5, 0.25, 0.25, "OVER", line="2.5")], line="2.5")  # PUSH en una línea .5


# --- B. Validación ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("probs", [
    {"OVER": 0.5, "UNDER": 0.5, "PUSH": 0.1}, {"OVER": 0.5, "UNDER": 0.25, "PUSH": 0.2}, {"OVER": 1.25, "UNDER": -0.25, "PUSH": 0.0},
    {"OVER": 0.5, "UNDER": 0.5}, {"OVER": 0.5, "UNDER": 0.25, "PUSH": 0.25, "VOID": 0.0}, [0.5, 0.25, 0.25], None,
])
def test_three_class_simplex_validation(probs):
    with pytest.raises(ProbabilityError):
        run([row("a", 1, "2026-W01", probs, "OVER", line="2.0")], line="2.0")


@pytest.mark.parametrize("probs", [
    {"YES": 0.6, "NO": 0.6}, {"YES": 0.5}, {"YES": 0.5, "NO": 0.5, "PUSH": 0.0}, {"Yes": 0.5, "NO": 0.5}, (0.5, 0.5),
    {"YES": True, "NO": 0}, {"YES": "0.5", "NO": 0.5}, {"YES": Decimal("0.5"), "NO": 0.5}, {"YES": Fraction(1, 2), "NO": 0.5},
])
def test_binary_simplex_validation(probs):
    with pytest.raises(ProbabilityError):
        run([row("a", 1, "2026-W01", probs, "YES", market=BTTS, line=None)], market=BTTS, line=None)


def test_simplex_tolerance_and_int_equivalence():
    ok = run([ou3("a", 1, "2026-W01", 0.5, 0.25, 0.25 + 5e-10, "OVER")], line="2.0")  # |suma - 1| <= 1e-9
    assert ok.coverage.scored == 1
    as_int = run([btts("a", 1, "2026-W01", 1, "YES")], market=BTTS, line=None)
    as_float = run([btts("a", 1, "2026-W01", 1.0, "YES")], market=BTTS, line=None)
    assert as_int.dataset_identity == as_float.dataset_identity and as_int.report_identity == as_float.report_identity


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 10 ** 400, -(10 ** 400)])
def test_nonfinite_values_rejected(bad):
    with pytest.raises(ProbabilityError):
        run([ou3("a", 1, "2026-W01", bad, 0.5, 0.5, "OVER")], line="2.0")
    with pytest.raises(ProbabilityError):
        run([row("a", 1, "2026-W01", {"OVER": bad, "UNDER": 0.5}, "OVER")])


def test_reordering_invariance():
    rows = BTTS_ROWS + [btts("s5", 5, "2026-W03", 0.5, "NO"), btts("s6", 6, "2026-W03", None, None, status="NO_ESTIMATE"),
                        btts("s7", 7, "2026-W04", 0.125, None)]
    cfg = UncertaintyConfig(seed=11, iterations=50, min_weeks=2)
    base = run(rows, market=BTTS, line=None, config=cfg)
    for seed in range(5):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        other = run(tuple(shuffled), market=BTTS, line=None, config=cfg)
        assert other == base and canonical_report_json(other) == canonical_report_json(base)


def test_duplicate_and_contradictory_identities_rejected():
    with pytest.raises(DuplicateSampleError):  # filas idénticas no se colapsan
        run([ou("a", 1, "2026-W01", 0.5, "OVER"), ou("a", 1, "2026-W01", 0.5, "OVER")])
    with pytest.raises(DuplicateSampleError):  # mismo sample_id, otro partido
        run([ou("a", 1, "2026-W01", 0.5, "OVER"), ou("a", 2, "2026-W01", 0.5, "OVER")])
    with pytest.raises(DuplicateSampleError):  # mismo partido y mercado con dos muestras contradictorias
        run([ou("a", 1, "2026-W01", 0.5, "OVER"), ou("b", 1, "2026-W02", 0.25, "UNDER")])


@pytest.mark.parametrize("market,line", [
    (OU, "2.25"), (OU, "2.75"), (OU, "2.50"), (OU, "7.5"), (OU, "-0.5"), (OU, 2.5), (OU, Decimal("2.5")), (OU, None), (OU, " 2.5"),
    (BTTS, "2.5"), (BTTS, ""), ("1X2_FT90", None), ("BTTS", None), ("OVER_UNDER", "2.5"), ("btts_ft90", None), (None, None),
])
def test_unsupported_markets_and_malformed_lines(market, line):
    with pytest.raises(UnsupportedTargetError):
        goals_reliability_uncertainty([], market=market, line=line, config=CFG)


def test_rows_must_match_target():
    with pytest.raises(RowError):
        run([ou("a", 1, "2026-W01", 0.5, "OVER", line="3.5")])
    with pytest.raises(RowError):
        run([row("a", 1, "2026-W01", {"HOME": 0.5, "AWAY": 0.5}, "HOME", market="1X2_FT90", line=None)])


@pytest.mark.parametrize("market,line,label,error", [
    (OU, "2.5", "PUSH", InconsistentPushLabelError), (BTTS, None, "PUSH", InconsistentPushLabelError),
    (OU, "2.5", "YES", LabelError), (BTTS, None, "OVER", LabelError), (OU, "2.0", "push", LabelError),
    (OU, "2.0", "VOID", LabelError), (OU, "2.0", 1, LabelError), (OU, "2.0", True, LabelError),
])
def test_inconsistent_push_and_foreign_labels(market, line, label, error):
    probs = {"YES": 0.5, "NO": 0.5} if market == BTTS else {"OVER": 0.5, "UNDER": 0.5} if line == "2.5" else \
        {"OVER": 0.5, "UNDER": 0.25, "PUSH": 0.25}
    with pytest.raises(error):
        run([row("a", 1, "2026-W01", probs, label, market=market, line=line)], market=market, line=line)


@pytest.mark.parametrize("week", ["2026-W1", "2026-W00", "2025-W53", "2026-W54", "2026W09", "2026-w09", " 2026-W09", "2026-W09 ",
                                  "0000-W01", "２０２６-W09", date(2026, 3, 2), 202609, None])
def test_malformed_weeks_rejected(week):
    with pytest.raises(WeekError):
        run([ou("a", 1, week, 0.5, "OVER")])


def test_valid_long_iso_year_week_accepted():
    assert run([ou("a", 1, "2026-W53", 0.5, "OVER")]).coverage.weeks_input == 1  # 2026 tiene 53 semanas ISO


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(sample_id=" a"), lambda r: r.update(sample_id=""), lambda r: r.update(sample_id=1),
    lambda r: r.update(fixture_id=0), lambda r: r.update(fixture_id=True), lambda r: r.update(fixture_id="1"),
    lambda r: r.update(estimate_status="estimate"), lambda r: r.update(estimate_status=None),
    lambda r: r.update(extra=1), lambda r: r.pop("label"),
])
def test_malformed_rows_rejected(mutate):
    r = ou("a", 1, "2026-W01", 0.5, "OVER")
    mutate(r)
    with pytest.raises(RowError):
        run([r])


def test_collection_and_row_container_rejected():
    with pytest.raises(InputError):
        run(iter([ou("a", 1, "2026-W01", 0.5, "OVER")]))
    with pytest.raises(RowError):
        run([[("sample_id", "a")]])


def test_estimate_status_and_probabilities_must_agree():
    with pytest.raises(ProbabilityError):  # una abstención con probabilidades
        run([row("a", 1, "2026-W01", {"OVER": 0.5, "UNDER": 0.5}, "OVER", status="NO_ESTIMATE")])
    with pytest.raises(ProbabilityError):  # una estimación sin probabilidades: no se infieren
        run([row("a", 1, "2026-W01", None, "OVER")])


@pytest.mark.parametrize("config", [
    None, {"seed": 1, "iterations": 10}, UncertaintyConfig(seed=-1, iterations=10), UncertaintyConfig(seed=True, iterations=10),
    UncertaintyConfig(seed=1, iterations=0), UncertaintyConfig(seed=1, iterations=100_001), UncertaintyConfig(seed=1, iterations=10.0),
    UncertaintyConfig(seed=1, iterations=10, bin_count=0), UncertaintyConfig(seed=1, iterations=10, bin_count=101),
    UncertaintyConfig(seed=1, iterations=10, min_weeks=1), UncertaintyConfig(seed=1, iterations=10, min_bin_support=0),
    UncertaintyConfig(seed=1, iterations=10, min_total_support=0),
])
def test_invalid_config_rejected(config):
    with pytest.raises(ConfigError):
        run([], config=config)


def test_inputs_are_not_mutated():
    rows = [dict(r, probabilities=dict(r["probabilities"])) for r in INT_ROWS]
    snapshot = copy.deepcopy(rows)
    run(rows, line="2.0")
    assert rows == snapshot


# --- C/D. Cobertura, soporte y casos degenerados -------------------------------------------------------------------------

def test_no_estimate_abstention_and_missing_label_coverage():
    rows = [btts("s1", 1, "2026-W01", None, "YES", status="NO_ESTIMATE"), btts("s2", 2, "2026-W02", None, None, status="NO_ESTIMATE"),
            btts("s3", 3, "2026-W03", 0.25, None), btts("s4", 4, "2026-W01", 0.25, "YES"), btts("s5", 5, "2026-W01", 0.75, "NO")]
    r = run(rows, market=BTTS, line=None)
    c = r.coverage
    assert (c.total_input, c.no_estimate, c.label_missing, c.scored) == (5, 2, 1, 2)
    assert dict(c.exclusions) == {"LABEL_MISSING": 1, "NO_ESTIMATE": 2}
    assert (c.abstention_rate, c.scored_coverage) == (0.4, 0.4)
    assert (c.weeks_input, c.weeks_scored) == (3, 1)
    assert nonempty(r.classes[0]) == [(2, 1, 0.25, 1.0, 0.75), (7, 1, 0.75, 0.0, 0.75)]  # sin etiqueta: no se puntúa
    assert r.ece == 0.75


def test_empty_dataset():
    for market, line in ((BTTS, None), (OU, "2.5"), (OU, "2.0")):
        r = goals_reliability_uncertainty((), market=market, line=line, config=CFG)
        assert (r.sample_status, r.outcome_status, r.ece, r.all_push) == ("NO_DATA", "NO_SCORED_SAMPLES", None, False)
        assert (r.coverage.total_input, r.coverage.abstention_rate, r.coverage.scored_coverage) == (0, None, None)
        assert all(c.ece is None and c.mean_predicted is None and all(b.support == "EMPTY" for b in c.bins) for c in r.classes)
        u = r.uncertainty
        assert (u.status, u.replicates, u.weeks, u.point_ece, u.quantiles, u.replicate_ece_sorted) == ("NO_SCORED_SAMPLES", 0, 0, None, (), ())


def test_all_no_estimate_is_full_abstention():
    r = run([ou("a", 1, "2026-W01", None, None, status="NO_ESTIMATE"), ou("b", 2, "2026-W02", None, "OVER", status="NO_ESTIMATE")])
    assert (r.coverage.abstention_rate, r.coverage.scored_coverage, r.sample_status, r.ece) == (1.0, 0.0, "NO_DATA", None)
    assert r.uncertainty.status == "NO_SCORED_SAMPLES"


def test_all_push_is_explicit_and_not_bootstrapped():
    rows = [ou3(f"s{i}", i, f"2026-W0{i}", 0.5, 0.25, 0.25, "PUSH", line="3.0") for i in range(1, 7)]
    r = run(rows, line="3.0", config=CFG2W)
    assert (r.all_push, r.outcome_status) == (True, "SINGLE_OBSERVED_CLASS")
    over, under, push = r.classes
    assert (over.ece, under.ece, push.ece) == (0.5, 0.25, 0.75)
    assert (push.mean_predicted, push.observed_frequency) == (0.25, 1.0)
    assert r.ece == 0.5
    assert (r.uncertainty.status, r.uncertainty.replicates, r.uncertainty.quantiles) == ("SINGLE_OBSERVED_CLASS", 0, ())


def test_single_week_has_insufficient_bootstrap_support():
    r = run([ou("a", 1, "2026-W05", 0.25, "OVER"), ou("b", 2, "2026-W05", 0.75, "UNDER")], config=CFG2W)
    u = r.uncertainty
    assert (u.status, u.weeks, u.min_weeks, u.replicates, u.quantiles, u.replicate_min) == ("INSUFFICIENT_WEEKS", 1, 2, 0, (), None)
    assert u.point_ece == r.ece == 0.75
    assert (u.rows_per_week_min, u.rows_per_week_max) == (2, 2)


def test_insufficient_support_output_and_thresholds():
    r = run(BTTS_ROWS, market=BTTS, line=None)
    assert r.sample_status == "INSUFFICIENT_SUPPORT"
    supports = {b.index: b.support for b in r.classes[0].bins}
    assert supports[2] == supports[7] == "BELOW_MIN_SUPPORT" and supports[0] == "EMPTY"
    assert r.uncertainty.status == "INSUFFICIENT_WEEKS"  # 2 semanas < min_weeks por defecto (5)
    met = run(BTTS_ROWS, market=BTTS, line=None, config=UncertaintyConfig(seed=7, iterations=10, min_bin_support=2, min_total_support=4))
    assert met.sample_status == "MEETS_MIN_SUPPORT" and met.classes[0].bins[2].support == "MEETS_MIN_SUPPORT"
    assert met.ece == r.ece  # los umbrales son de presentación: no cambian la métrica


# --- D. Bootstrap por bloques de semana ------------------------------------------------------------------------------------

BLOCK_ROWS = [btts("w1a", 1, "2026-W01", 0.25, "YES"), btts("w1b", 2, "2026-W01", 0.25, "YES"),
              btts("w2a", 3, "2026-W02", 0.25, "NO"), btts("w2b", 4, "2026-W02", 0.25, "NO")]


def independent_draw(seed, replicate, draw, weeks):
    digest = hashlib.sha256(f"WEEK_BLOCK_BOOTSTRAP_SHA256_COUNTER_DRAWS_V1|{seed}|{replicate}|{draw}".encode()).digest()
    return int.from_bytes(digest, "big") % weeks


def expected_block_replicates(seed, iterations):
    # k copias de W01 (YES, YES) y 2 - k de W02 (NO, NO), todas en el bin 2: ECE = |1 - 2k| / 4
    out = []
    for r in range(iterations):
        k = sum(1 for j in range(2) if independent_draw(seed, r, j, 2) == 0)
        out.append(abs(1 - 2 * k) / 4)
    return tuple(sorted(out))


def test_multi_week_block_resampling_keeps_weeks_whole():
    r = run(BLOCK_ROWS, market=BTTS, line=None, config=CFG2W)
    u = r.uncertainty
    assert (u.status, u.method, u.weeks, u.replicates, u.iterations) == ("EXPLORATORY_BOOTSTRAP", BOOTSTRAP_METHOD, 2, 200, 200)
    assert u.interval_semantics == INTERVAL_SEMANTICS and "NOT_A_CONFIDENCE_INTERVAL" in u.interval_semantics
    assert u.point_ece == r.ece == 0.25
    # semanas enteras: solo {0.25, 0.75} son alcanzables; remuestrear filas daría también 0.5 (3 YES y 1 NO)
    assert set(u.replicate_ece_sorted) == {0.25, 0.75}
    assert u.replicate_ece_sorted == expected_block_replicates(7, 200)
    assert (u.replicate_min, u.replicate_max) == (0.25, 0.75)
    assert [name for name, _ in u.quantiles] == ["P10", "P50", "P90"]
    assert not any("95" in name for name, _ in u.quantiles)


def test_block_draw_matches_documented_rule():
    for seed, rep, draw, weeks in ((0, 0, 0, 1), (7, 3, 1, 2), (123, 45, 6, 13), (2 ** 40, 99, 0, 52)):
        assert block_draw(seed, rep, draw, weeks) == independent_draw(seed, rep, draw, weeks)
        assert 0 <= block_draw(seed, rep, draw, weeks) < weeks


def test_deterministic_seed_iterations_and_nearest_rank_quantiles():
    cfg = UncertaintyConfig(seed=3, iterations=10, min_weeks=2)
    a, b = run(BLOCK_ROWS, market=BTTS, line=None, config=cfg), run(BLOCK_ROWS, market=BTTS, line=None, config=cfg)
    assert a == b and a.report_identity == b.report_identity
    reps = a.uncertainty.replicate_ece_sorted
    assert reps == expected_block_replicates(3, 10) and len(reps) == 10
    assert a.uncertainty.quantiles == (("P10", reps[0]), ("P50", reps[4]), ("P90", reps[8]))  # ceil(q * 10) - 1
    other_seed = run(BLOCK_ROWS, market=BTTS, line=None, config=dataclasses.replace(cfg, seed=4))
    assert other_seed.uncertainty.replicate_ece_sorted == expected_block_replicates(4, 10)
    assert other_seed.config_identity != a.config_identity and other_seed.report_identity != a.report_identity
    more = run(BLOCK_ROWS, market=BTTS, line=None, config=dataclasses.replace(cfg, iterations=25))
    assert more.uncertainty.replicate_ece_sorted == expected_block_replicates(3, 25)


def test_three_class_bootstrap_runs_on_whole_weeks():
    rows = INT_ROWS + [ou3("s5", 5, "2026-W03", 0.25, 0.25, 0.5, "PUSH"), ou3("s6", 6, "2026-W03", 0.5, 0.25, 0.25, "OVER")]
    r = run(rows, line="2.0", config=UncertaintyConfig(seed=5, iterations=30, min_weeks=3))
    u = r.uncertainty
    assert (u.status, u.weeks, u.replicates, u.rows_per_week_min, u.rows_per_week_max) == ("EXPLORATORY_BOOTSTRAP", 3, 30, 2, 2)
    assert all(0 <= v <= 1 for v in u.replicate_ece_sorted) and list(u.replicate_ece_sorted) == sorted(u.replicate_ece_sorted)
    assert run(rows, line="2.0", config=UncertaintyConfig(seed=5, iterations=30, min_weeks=4)).uncertainty.status == "INSUFFICIENT_WEEKS"


# --- E. Identidad y serialización ------------------------------------------------------------------------------------------

def _identity_of(report):
    payload = json.loads(canonical_report_json(report))
    assert payload.pop("report_identity") == report.report_identity
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def test_report_identity_is_sha256_of_canonical_serialization():
    r = run(INT_ROWS, line="2.0", config=CFG2W)
    assert _identity_of(r) == r.report_identity and len(r.report_identity) == 64
    assert canonical_report_json(r) == canonical_report_json(run(INT_ROWS, line="2.0", config=CFG2W))


@pytest.mark.parametrize("change", [
    lambda rows: rows[0].update(probabilities={"YES": 0.375, "NO": 0.625}), lambda rows: rows[1].update(label="YES"),
    lambda rows: rows[2].update(week="2026-W03"), lambda rows: rows[3].update(sample_id="s4b"),
    lambda rows: rows[3].update(fixture_id=40), lambda rows: rows[0].update(label=None),
    lambda rows: rows.append(btts("s9", 9, "2026-W01", None, None, status="NO_ESTIMATE")),
])
def test_data_mutation_changes_dataset_and_report_identity(change):
    base = run(BTTS_ROWS, market=BTTS, line=None)
    rows = copy.deepcopy(BTTS_ROWS)
    change(rows)
    other = run(rows, market=BTTS, line=None)
    assert other.dataset_identity != base.dataset_identity and other.report_identity != base.report_identity
    assert other.config_identity == base.config_identity


def test_abstention_row_mutation_changes_identity():
    rows = BTTS_ROWS + [btts("s9", 9, "2026-W01", None, None, status="NO_ESTIMATE")]
    a = run(rows, market=BTTS, line=None)
    rows2 = copy.deepcopy(rows)
    rows2[-1]["week"] = "2026-W02"
    b = run(rows2, market=BTTS, line=None)
    assert a.ece == b.ece and a.dataset_identity != b.dataset_identity and a.report_identity != b.report_identity


@pytest.mark.parametrize("field,value", [("seed", 8), ("iterations", 201), ("bin_count", 5), ("min_bin_support", 31),
                                         ("min_total_support", 301), ("min_weeks", 3)])
def test_config_mutation_changes_config_and_report_identity(field, value):
    base = run(BTTS_ROWS, market=BTTS, line=None)
    other = run(BTTS_ROWS, market=BTTS, line=None, config=dataclasses.replace(CFG, **{field: value}))
    assert other.config_identity != base.config_identity and other.report_identity != base.report_identity
    assert other.dataset_identity == base.dataset_identity


def test_bin_count_changes_binning():
    r = run(BTTS_ROWS, market=BTTS, line=None, config=dataclasses.replace(CFG, bin_count=2))
    assert [(b.interval, b.count) for b in r.classes[0].bins] == [("[0.0, 0.5)", 2), ("[0.5, 1.0]", 2)]
    assert r.ece == 0.25


def test_report_is_immutable():
    r = run(INT_ROWS, line="2.0")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ece = 0.0
    with pytest.raises(TypeError):
        r.outcome_counts["OVER"] = 9
    with pytest.raises(TypeError):
        r.coverage.exclusions["NO_ESTIMATE"] = 9


# --- Procedencia y pureza ----------------------------------------------------------------------------------------------------

def test_no_fake_certification_or_receipt_status():
    r = run(BLOCK_ROWS, market=BTTS, line=None, config=CFG2W)
    assert isinstance(r, ReliabilityUncertaintyReport)
    assert (r.pit_status, r.calibration_status, r.receipt_status) == (PIT_STATUS, CALIBRATION_STATUS, RECEIPT_STATUS)
    assert r.pit_status == "NOT_CERTIFIED" and r.calibration_status.startswith("NOT_CERTIFIED")
    assert r.receipt_status.startswith("NO_") and r.limitations == LIMITATIONS
    for needed in ("NOT_TRUE_PIT_CERTIFIED", "NO_CALIBRATION_PASS_THRESHOLD_OR_CERTIFICATION",
                   "WEEK_AND_FT90_LABEL_NOT_PROOF_OF_DI_ADMISSION_OR_AUTHENTIC_CHRONOLOGY", "ROWS_WITHIN_A_WEEK_NOT_ASSUMED_INDEPENDENT",
                   "BOOTSTRAP_PERCENTILES_EXPLORATORY_NOT_CONFIDENCE_INTERVALS", "NO_BASELINE_COMPARISON"):
        assert needed in r.limitations
    assert not any(word in f.name.lower() for f in dataclasses.fields(r) for word in ("pass", "certified", "receipt_at", "confidence"))
    assert r.evidence_basis.startswith("SYNTHETIC_CALLER_SUPPLIED")


def test_module_is_pure_and_offline():
    code = ("import sys, research.goals_calibration_uncertainty as m; "
            "bad = sorted(n for n in sys.modules if n.split('.')[0] in "
            "('app', 'sqlalchemy', 'psycopg', 'psycopg2', 'requests', 'httpx', 'urllib3', 'dotenv', 'socket')); "
            "print(bad)")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "DATABASE_URL", "TEST_DATABASE_URL")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    out = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_module_has_no_random_or_global_statistical_state():
    import ast

    import research.goals_calibration_uncertainty as module
    with open(module.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"random", "numpy", "secrets", "time"}  # el sorteo es sha256 de (semilla, réplica, sorteo)
    assert not any(isinstance(node, ast.Global) for node in ast.walk(tree))


# --- MOD-DEV109B-R1: enteros enormes, diagnósticos acotados y entradas hostiles ------------------------------------------

BIG = 10 ** 5000  # más dígitos que el límite por defecto de CPython (4300) para int -> str
MAX_MESSAGE = 500
MIXED_ROWS = BTTS_ROWS + [btts("s5", 5, "2026-W03", 0.5, "NO"), btts("s6", 6, "2026-W03", None, None, status="NO_ESTIMATE"),
                          btts("s7", 7, "2026-W04", 0.125, None)]

# Identidades calculadas con el código previo a R1 (f137c6b) sobre estas mismas entradas ordinarias: R1 no las cambia
GOLDEN = {
    "BTTS_CFG": ("07d783b8a0d807692857cfa2af1d6cd28510ec1b3daa1f92008a42887f520a6c",
                 "b2f3f03a015905796ee47073ab1d233e8bbcba4aa324f5cf6253af09a445a045",
                 "31a9bc8c7e94e7f60b93493cf96929c04f759d5153028c21f5820b2d60d560fa"),
    "INT_CFG2W": ("ea491d2d19f73e5bc18ba4dd64c0c05365bae792f62dcd87fe5a294728bc5fbb",
                  "01fc5949a5aa5f36377035f11448b98d16d4c08a1ffd7ba9fcb61eb56717380e",
                  "5a7b404d689303c9ea35e71310a4978b88a606d31b813dd02d3e38c4f95c6132"),
    "BLOCK_CFG2W": ("ea491d2d19f73e5bc18ba4dd64c0c05365bae792f62dcd87fe5a294728bc5fbb",
                    "6456649f62853ada0b5ab9a481cfdd4a7fcd1b4ac6a56e55f31a6572363acd36",
                    "2ae97f5cbbee112f289cf9fb7bcff99e5c1c7c542e081d42b4aadc7322a73bd9"),
    "MIXED_S11": ("7f582b090943b53b64899971a96365f61b21022cc03ddaea984329cdfd11ab72",
                  "5359797aa849a31f8327f99d8217b31bf8e7c52e66b6519a75b6ecb90eda48ac",
                  "16fd95a6bafe9c167b7ec5f32f97eb37041f210a35d9058b497316b41ed64b0f"),
}
GOLDEN_RUNS = {
    "BTTS_CFG": lambda: run(BTTS_ROWS, market=BTTS, line=None),
    "INT_CFG2W": lambda: run(INT_ROWS, line="2.0", config=CFG2W),
    "BLOCK_CFG2W": lambda: run(BLOCK_ROWS, market=BTTS, line=None, config=CFG2W),
    "MIXED_S11": lambda: run(MIXED_ROWS, market=BTTS, line=None, config=UncertaintyConfig(seed=11, iterations=50, min_weeks=2)),
}


def hex_token(n):
    return ("-" if n < 0 else "+") + format(abs(n), "x")


def raises_bounded(error, fn):
    with pytest.raises(error) as info:
        fn()
    message = str(info.value)
    assert len(message) <= MAX_MESSAGE, len(message)
    return message


class Hostile:
    """Cualquier __repr__/__str__/__format__/comparación/hash/bool registra la llamada y falla."""
    calls: list = []

    def _boom(self, name):
        Hostile.calls.append(name)
        raise RuntimeError(f"hostile {name}")

    def __repr__(self):
        return self._boom("__repr__")

    def __str__(self):
        return self._boom("__str__")

    def __format__(self, spec):
        return self._boom("__format__")

    def __eq__(self, other):
        return self._boom("__eq__")

    def __ne__(self, other):
        return self._boom("__ne__")

    def __lt__(self, other):
        return self._boom("__lt__")

    def __le__(self, other):
        return self._boom("__le__")

    def __gt__(self, other):
        return self._boom("__gt__")

    def __ge__(self, other):
        return self._boom("__ge__")

    def __hash__(self):
        return self._boom("__hash__")

    def __bool__(self):
        return self._boom("__bool__")


class HostileKey(Hostile):
    __hash__ = object.__hash__  # hashable para poder construir el dict de prueba; el resto sigue siendo hostil


class SubStr(str):
    pass


class SubFloat(float):
    pass


@pytest.fixture(autouse=True)
def _reset_hostile_calls():
    Hostile.calls.clear()
    yield
    assert Hostile.calls == []  # el módulo nunca ejecutó un método del objeto hostil


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_r1_ordinary_identities_are_unchanged(name):
    report = GOLDEN_RUNS[name]()
    assert (report.config_identity, report.dataset_identity, report.report_identity) == GOLDEN[name]


def test_r1_huge_valid_fixture_ids_are_supported_and_distinct():
    small = run([btts("a", 1, "2026-W01", 0.25, "YES"), btts("b", 2, "2026-W02", 0.75, "NO")], market=BTTS, line=None, config=CFG2W)
    huge = run([btts("a", BIG, "2026-W01", 0.25, "YES"), btts("b", BIG + 1, "2026-W02", 0.75, "NO")], market=BTTS, line=None, config=CFG2W)
    again = run([btts("b", BIG + 1, "2026-W02", 0.75, "NO"), btts("a", BIG, "2026-W01", 0.25, "YES")], market=BTTS, line=None, config=CFG2W)
    other = run([btts("a", BIG + 2, "2026-W01", 0.25, "YES"), btts("b", BIG + 1, "2026-W02", 0.75, "NO")], market=BTTS, line=None, config=CFG2W)
    assert huge == again and huge.dataset_identity == again.dataset_identity  # repetible e independiente del orden
    assert other.dataset_identity != huge.dataset_identity and other.report_identity != huge.report_identity
    assert huge.dataset_identity != small.dataset_identity
    # la estadística no depende del identificador
    assert (huge.ece, huge.coverage, huge.classes, huge.uncertainty) == (small.ece, small.coverage, small.classes, small.uncertainty)
    for edge in (10 ** 640 - 1, 10 ** 640):  # última decimal y primera INT_HEX_V1: distintas y estables
        a = run([btts("a", edge, "2026-W01", 0.25, "YES")], market=BTTS, line=None)
        b = run([btts("a", edge, "2026-W01", 0.25, "YES")], market=BTTS, line=None)
        assert a.dataset_identity == b.dataset_identity
    assert run([btts("a", 10 ** 640 - 1, "2026-W01", 0.25, "YES")], market=BTTS, line=None).dataset_identity != \
        run([btts("a", 10 ** 640, "2026-W01", 0.25, "YES")], market=BTTS, line=None).dataset_identity


def test_r1_duplicate_huge_fixture_ids_fail_with_a_bounded_message():
    message = raises_bounded(DuplicateSampleError, lambda: run([btts("a", BIG, "2026-W01", 0.5, "YES"), btts("b", BIG, "2026-W02", 0.5, "NO")],
                                                               market=BTTS, line=None))
    assert "bits" in message


def test_r1_huge_valid_seed_canonical_encoding():
    cfg = UncertaintyConfig(seed=BIG, iterations=5)
    r = run(BTTS_ROWS, market=BTTS, line=None, config=cfg)
    payload = json.loads(canonical_report_json(r))
    assert payload["config"]["seed"] == payload["uncertainty"]["seed"] == {"INT_HEX_V1": hex_token(BIG)}
    assert int(payload["config"]["seed"]["INT_HEX_V1"], 16) == BIG
    assert r == run(BTTS_ROWS, market=BTTS, line=None, config=cfg)
    plus_one = run(BTTS_ROWS, market=BTTS, line=None, config=dataclasses.replace(cfg, seed=BIG + 1))
    assert plus_one.config_identity != r.config_identity and plus_one.report_identity != r.report_identity
    decimal_edge = run(BTTS_ROWS, market=BTTS, line=None, config=dataclasses.replace(cfg, seed=10 ** 640 - 1))
    assert json.loads(canonical_report_json(decimal_edge))["config"]["seed"] == 10 ** 640 - 1  # hasta 640 dígitos: número JSON
    hex_edge = run(BTTS_ROWS, market=BTTS, line=None, config=dataclasses.replace(cfg, seed=10 ** 640))
    assert json.loads(canonical_report_json(hex_edge))["config"]["seed"] == {"INT_HEX_V1": hex_token(10 ** 640)}


def huge_seed_draw(seed, replicate, draw, weeks):
    token = f"INT_HEX_V1:{hex_token(seed)}" if abs(seed) >= 10 ** 640 else str(seed)
    digest = hashlib.sha256(f"WEEK_BLOCK_BOOTSTRAP_SHA256_COUNTER_DRAWS_V1|{token}|{replicate}|{draw}".encode()).digest()
    return int.from_bytes(digest, "big") % weeks


def test_r1_huge_seed_bootstrap_is_deterministic_and_follows_the_encoding():
    cfg = UncertaintyConfig(seed=BIG, iterations=40, min_weeks=2)
    r = run(BLOCK_ROWS, market=BTTS, line=None, config=cfg)
    assert r.uncertainty.status == "EXPLORATORY_BOOTSTRAP" and r.uncertainty.replicates == 40
    expected = tuple(sorted(abs(1 - 2 * sum(1 for j in range(2) if huge_seed_draw(BIG, i, j, 2) == 0)) / 4 for i in range(40)))
    assert r.uncertainty.replicate_ece_sorted == expected
    assert r == run(BLOCK_ROWS, market=BTTS, line=None, config=cfg)
    for seed in (BIG, BIG + 1, 10 ** 640, 10 ** 640 - 1, 7):
        assert block_draw(seed, 3, 1, 13) == huge_seed_draw(seed, 3, 1, 13)


@pytest.mark.parametrize("field,value", [("seed", -BIG), ("iterations", BIG), ("bin_count", -BIG), ("min_weeks", -BIG),
                                         ("seed", Hostile()), ("iterations", 2.5)],
                         ids=["neg-huge-seed", "huge-iterations", "neg-huge-bins", "neg-huge-min-weeks", "hostile-seed", "float-iterations"])
def test_r1_invalid_huge_or_hostile_config_values_are_bounded_config_errors(field, value):
    raises_bounded(ConfigError, lambda: run([], config=dataclasses.replace(CFG, **{field: value})))


def test_r1_hostile_config_object():
    raises_bounded(ConfigError, lambda: run([], config=Hostile()))


def test_r1_huge_negative_fixture_id_is_a_short_row_error():
    message = raises_bounded(RowError, lambda: run([btts("a", -BIG, "2026-W01", 0.5, "YES")], market=BTTS, line=None))
    assert "fixture_id" in message and "negativo" in message and "bits" in message


def _bad_row(**change):
    return dict(btts("a", 1, "2026-W01", 0.5, "YES"), **change)


@pytest.mark.parametrize("change,error", [
    ({"label": Hostile()}, LabelError), ({"label": BIG}, LabelError), ({"label": "Y" * 5_000_000}, LabelError),
    ({"label": SubStr("YES")}, LabelError), ({"week": Hostile()}, WeekError), ({"week": BIG}, WeekError),
    ({"week": "2026-W" + "9" * 5_000_000}, WeekError), ({"fixture_id": Hostile()}, RowError), ({"fixture_id": 1.0}, RowError),
    ({"sample_id": Hostile()}, RowError), ({"sample_id": " " + "x" * 5_000_000}, RowError), ({"sample_id": SubStr("a")}, RowError),
    ({"market": Hostile()}, RowError), ({"market": BIG}, RowError), ({"line": Hostile()}, RowError), ({"line": BIG}, RowError),
    ({"estimate_status": Hostile()}, RowError), ({"estimate_status": SubStr("ESTIMATE")}, RowError),
    ({"probabilities": Hostile()}, ProbabilityError), ({"probabilities": {"YES": Hostile(), "NO": 0.5}}, ProbabilityError),
    ({"probabilities": {"YES": BIG, "NO": 0.5}}, ProbabilityError), ({"probabilities": {"YES": -BIG, "NO": 0.5}}, ProbabilityError),
    ({"probabilities": {"YES": SubFloat(0.5), "NO": 0.5}}, ProbabilityError),
    ({"probabilities": {"YES": 0.5, HostileKey(): 0.5}}, ProbabilityError),
    ({"probabilities": {"YES": 0.5, "NO": 0.5, "x" * 5_000_000: 0.0}}, ProbabilityError),
    ({"week": type("W" * 5000, (), {})()}, WeekError),
], ids=["hostile-label", "huge-int-label", "5MB-label", "substr-label", "hostile-week", "huge-int-week", "5MB-week",
        "hostile-fixture", "float-fixture", "hostile-sample", "5MB-sample", "substr-sample", "hostile-market", "huge-int-market",
        "hostile-line", "huge-int-line", "hostile-status", "substr-status", "hostile-probabilities", "hostile-prob-value",
        "huge-prob", "neg-huge-prob", "subfloat-prob", "hostile-prob-key", "5MB-prob-key", "long-type-name"])
def test_r1_hostile_and_oversized_row_values_give_bounded_typed_errors(change, error):
    message = raises_bounded(error, lambda: run([_bad_row(**change)], market=BTTS, line=None))
    assert "x" * 100 not in message and "Y" * 100 not in message and "9" * 100 not in message and "W" * 100 not in message


def test_r1_metaclass_name_is_read_without_running_caller_code():
    class HostileMeta(type):
        @property
        def __name__(cls):
            Hostile.calls.append("meta __name__")
            raise RuntimeError("hostile metaclass __name__")

    class MetaHostile(Hostile, metaclass=HostileMeta):
        pass

    message = raises_bounded(LabelError, lambda: run([_bad_row(label=MetaHostile())], market=BTTS, line=None))
    assert "<MetaHostile>" in message


@pytest.mark.parametrize("extra", [
    {HostileKey(): 1}, {BIG: 1}, {"z" * 5_000_000: 1}, {f"extra{i}" * 1000: i for i in range(1000)}, {("tuple", "key"): 1},
], ids=["hostile-key", "huge-int-key", "5MB-key", "1000-long-keys", "tuple-key"])
def test_r1_unexpected_field_keys_give_bounded_row_errors(extra):
    row = _bad_row()
    row.update(extra)
    message = raises_bounded(RowError, lambda: run([row], market=BTTS, line=None))
    assert "sobran" in message


def test_r1_missing_and_unexpected_keys_are_reported_deterministically():
    row = _bad_row()
    del row["week"]
    row["zeta"], row["alpha"] = 1, 2
    first = raises_bounded(RowError, lambda: run([row], market=BTTS, line=None))
    second = raises_bounded(RowError, lambda: run([dict(reversed(list(row.items())))], market=BTTS, line=None))
    assert first == second and "'week'" in first and "'alpha', 'zeta'" in first


@pytest.mark.parametrize("market,line,error", [
    (Hostile(), None, UnsupportedTargetError), (BIG, None, UnsupportedTargetError), ("M" * 5_000_000, None, UnsupportedTargetError),
    (SubStr(BTTS), None, UnsupportedTargetError), (OU, Hostile(), UnsupportedTargetError), (OU, BIG, UnsupportedTargetError),
    (OU, SubStr("2.5"), UnsupportedTargetError), (BTTS, Hostile(), UnsupportedTargetError),
], ids=["hostile-market", "huge-int-market", "5MB-market", "substr-market", "hostile-line", "huge-int-line", "substr-line",
        "btts-hostile-line"])
def test_r1_hostile_or_huge_targets_give_bounded_errors(market, line, error):
    raises_bounded(error, lambda: goals_reliability_uncertainty([], market=market, line=line, config=CFG))


def test_r1_oversized_duplicate_sample_ids_are_bounded():
    sid = "d" * 5_000_000
    message = raises_bounded(DuplicateSampleError, lambda: run([btts(sid, 1, "2026-W01", 0.5, "YES"), btts(sid, 2, "2026-W01", 0.5, "NO")],
                                                               market=BTTS, line=None))
    assert "5000000 caracteres" in message


def test_r1_hostile_containers_are_typed():
    raises_bounded(InputError, lambda: run(Hostile(), market=BTTS, line=None))
    raises_bounded(RowError, lambda: run([Hostile()], market=BTTS, line=None))


def test_r1_messages_are_deterministic():
    make = lambda: run([_bad_row(label=Hostile())], market=BTTS, line=None)  # noqa: E731
    assert raises_bounded(LabelError, make) == raises_bounded(LabelError, make)


def test_r1_functional_mapping_failures_are_not_swallowed():
    class CallerMappingBug(Exception):
        pass

    class ExplodingMapping(dict):  # Mapping funcional del llamador: su propio código se ejecuta y su error se propaga
        def __iter__(self):
            raise CallerMappingBug("caller __iter__")  # ni se convierte en InputError ni se oculta

    with pytest.raises(CallerMappingBug):
        run([ExplodingMapping(_bad_row())], market=BTTS, line=None)


def test_r1_preserves_statistics_with_huge_identities():
    rows = [ou3(f"s{i}", BIG + i, f"2026-W0{1 + i // 2}", *probs, label)
            for i, (probs, label) in enumerate([((0.5, 0.25, 0.25), "OVER"), ((0.5, 0.25, 0.25), "PUSH"),
                                                ((0.25, 0.5, 0.25), "UNDER"), ((0.25, 0.5, 0.25), "UNDER")])]
    r = run(rows, line="2.0", config=UncertaintyConfig(seed=BIG, iterations=30, min_weeks=2))
    assert r.ece == 1 / 6 and [c.ece for c in r.classes] == [0.125, 0.375, 0.0]  # mismos valores a mano que con ids pequeños
    assert (r.classes[2].mean_predicted, r.classes[2].observed_frequency) == (0.25, 0.25)
    assert (r.coverage.scored, r.sample_status, r.outcome_status, r.uncertainty.status) == (4, "INSUFFICIENT_SUPPORT",
                                                                                          "MULTIPLE_OBSERVED_CLASSES",
                                                                                          "EXPLORATORY_BOOTSTRAP")
    assert r.uncertainty.replicates == 30 and r.pit_status == "NOT_CERTIFIED"
