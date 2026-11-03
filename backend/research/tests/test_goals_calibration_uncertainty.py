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
