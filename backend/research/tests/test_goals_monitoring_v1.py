"""Monitor de investigación V1 de goles FT90 sobre informes del consumidor V1. Sin BD, red, disco ni app.
    python -m pytest research/tests/test_goals_monitoring_v1.py -q   (desde backend/)
Partidos, marcadores, instantes y probabilidades SINTÉTICOS: ni histórico real, ni procedencia acreditada, ni calibración."""

import copy
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import research.goals_monitoring_v1.evidence as evidence
from research.goals_calibration_uncertainty import UncertaintyConfig
from research.goals_consumer_v1 import (ConsumerSourceError, GoalsConsumerError, aggregate_goals_consumer_reports,
                                        run_goals_research_consumer)
from research.goals_consumer_v1.contract import FIXTURE_ID_LIMIT
from research.goals_monitoring_v1 import (BELOW_MIN_SUPPORT, COHORT_COMPARISON, COHORT_SUMMARY, COMPARED, CONSUMER_VERSION_MISMATCH,
                                          CONTRACT_VERSION, DUPLICATE_REPORT, EMPTY_COHORT, EVIDENCE_NOT_AUDITABLE, EVIDENCE_REJECTED,
                                          EXPLORATORY_BOOTSTRAP, FEWER_THAN_TWO_COHORTS, INCOMPLETE_LABELS, INSUFFICIENT_SUPPORT,
                                          INSUFFICIENT_WEEKS, INTERPRETATION, LINE_MISMATCH, MARKET_MISMATCH, MEETS_MIN_SUPPORT,
                                          MODEL_ERROR_NOT_RECONCILED, MONITORING_SEQUENCE, NO_COMPARISON, NO_DATA,
                                          NO_MEASURE_WITH_MIN_SUPPORT, NOT_COMPARABLE, NOT_RECONCILED, OVERLAPPING_FIXTURES,
                                          PRODUCER_MISMATCH, PRODUCER_VERSION_MISMATCH, RECONCILED, REJECTED,
                                          RELIABILITY_CONFIG_MISMATCH, RELIABILITY_SAMPLE_INSUFFICIENT, SHARED_WEEKS_NOT_INDEPENDENT,
                                          TEMPORAL_EVIDENCE_INCONSISTENT, TEMPORAL_ORDER_INVALID, TEMPORAL_OVERLAP,
                                          TIMESTAMP_NOT_CANONICAL_UTC, TIMESTAMP_PRECISION_UNKNOWN, UNDEFINED_IN_REPLICATE, VERIFIED,
                                          GoalsMonitoringError, MonitoringConfigError, MonitoringInputError, MonitoringIntegrityError,
                                          compare_goals_cohorts, monitor_goals_cohorts, summarize_goals_cohort,
                                          verify_monitoring_document)
from research.goals_monitoring_v1.contract import MESSAGE_LIMIT
from research.m59.canonical import sha256_hex

UTC = timezone.utc
BTTS, OU = "BTTS_FT90", "OVER_UNDER_FT90"
O1 = datetime(2025, 3, 3, tzinfo=UTC)  # lunes: las ventanas de 56 días son semanas ISO enteras
O2 = O1 + timedelta(days=56)
O3 = O2 + timedelta(days=56)
HOLDOUT = timedelta(days=56)
RELIABILITY = UncertaintyConfig(seed=7, iterations=40, min_weeks=2, min_total_support=30, min_bin_support=5)
MONITOR = {"contract_version": CONTRACT_VERSION, "min_support": 30}
SMALL = {"contract_version": CONTRACT_VERSION, "min_support": 1}
OU_PROBS = ({"OVER": 0.5, "UNDER": 0.3, "PUSH": 0.2}, {"OVER": 0.3, "UNDER": 0.5, "PUSH": 0.2}, {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.25})
MEASURES_INTEGER = {"estimate_coverage", "label_missing_rate", "abstention_rate_settled", "settled_share.OVER", "settled_share.UNDER",
                    "settled_share.PUSH", "scored_share.OVER", "scored_share.UNDER", "scored_share.PUSH", "brier_mean",
                    "log_loss_mean", "ece"}


def obs(fixture_id, kickoff, *, market=OU, line="2.0", status="ESTIMATE", probs=None, score=(1, 1), status_short="FT",
        observed="default"):
    if status == "NO_ESTIMATE":
        probs = None
    elif probs is None:
        probs = ({"YES": 0.55, "NO": 0.45} if market == BTTS else
                 {"OVER": 0.52, "UNDER": 0.48} if line.endswith(".5") else dict(OU_PROBS[2]))
    cutoff = kickoff - timedelta(hours=1)
    return {"fixture_id": fixture_id, "kickoff_at": kickoff, "cutoff_at": cutoff, "feature_observed_at": cutoff, "market": market,
            "line": line, "status": status, "probabilities": probs,
            "outcome": {"status_short": status_short, "fulltime_home": score[0], "fulltime_away": score[1],
                        "observed_at": kickoff + timedelta(hours=2) if observed == "default" else observed}}


def consumer_config(origins, *, market=OU, line="2.0", producer_id="SYNTHETIC_PRODUCER", producer_version="1.0",
                    reliability=RELIABILITY, holdout=HOLDOUT):
    return {"contract_version": "GOALS_RESEARCH_CONSUMER_V1", "producer_id": producer_id, "producer_version": producer_version,
            "market": market, "line": line, "origins": origins, "holdout_length": holdout, "calibration_length": timedelta(days=14),
            "embargo": timedelta(days=3), "reliability": reliability}


def cohort_rows(origin, first_id, n, *, market=OU, line="2.0", phase=0, abstain_every=9, step_hours=6, start_hours=1):
    """Filas sintéticas deterministas: un partido cada `step_hours` dentro del holdout que empieza en `origin`."""
    rows = []
    for i in range(n):
        kickoff = origin + timedelta(hours=start_hours + step_hours * i)
        s = (i * 7 + phase) % 5
        score = (s, (i + phase) % 2) if market == BTTS else (s, 0)
        if market == BTTS:
            p = 0.6 if i % 3 else 0.35
            probs = {"YES": p, "NO": 1 - p}
        elif line.endswith(".5"):
            p = 0.55 if i % 3 else 0.4
            probs = {"OVER": p, "UNDER": 1 - p}
        else:
            probs = dict(OU_PROBS[i % 2])
        status = "NO_ESTIMATE" if abstain_every and i % abstain_every == 0 else "ESTIMATE"
        rows.append(obs(first_id + i, kickoff, market=market, line=line, status=status, probs=probs, score=score))
    return rows


def cohort(origin=O1, first_id=1, n=200, *, market=OU, line="2.0", phase=0, abstain_every=9, **over):
    rows = cohort_rows(origin, first_id, n, market=market, line=line, phase=phase, abstain_every=abstain_every)
    return run_goals_research_consumer(rows, consumer_config((origin,), market=market, line=line, **over))


def redigest(report):
    body = {k: v for k, v in report.items() if k != "report_digest"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return {**body, "report_digest": hashlib.sha256(text.encode("ascii")).hexdigest()}


def tampered(report, edit):
    r = copy.deepcopy(report)
    edit(r)
    return redigest(r)


def summary(report, config=MONITOR):
    return summarize_goals_cohort(report, config)["cohort"]


# Fixture dorada de PRIMARY (la del consumidor V1): O/U 2.0, cinco filas liquidadas, tres ESTIMATE y dos NO_ESTIMATE.
GOLDEN_ORIGIN = datetime(2025, 3, 1, tzinfo=UTC)


def _golden_day(d):
    return datetime(2025, 3, d, 18, tzinfo=UTC)


PRIMARY_GOLDEN = [obs(1, _golden_day(2), score=(3, 0)),                          # f1 ESTIMATE     3-0 -> OVER
                  obs(2, _golden_day(9), score=(2, 0)),                          # f2 ESTIMATE     2-0 -> PUSH
                  obs(3, _golden_day(16), score=(1, 0)),                         # f3 ESTIMATE     1-0 -> UNDER
                  obs(4, _golden_day(20), status="NO_ESTIMATE", score=(0, 0)),   # f4 NO_ESTIMATE  0-0 -> UNDER
                  obs(5, _golden_day(25), status="NO_ESTIMATE", score=(2, 0))]   # f5 NO_ESTIMATE  2-0 -> PUSH


GOLDEN_RELIABILITY = UncertaintyConfig(seed=7, iterations=20, min_weeks=2)  # la del consumidor V1 (soporte total de 109B: 300)


def golden_report():
    return run_goals_research_consumer(copy.deepcopy(PRIMARY_GOLDEN), consumer_config(
        (GOLDEN_ORIGIN,), holdout=timedelta(days=28), reliability=GOLDEN_RELIABILITY))


@pytest.fixture(scope="module")
def base():
    """Cohortes verificables reutilizadas (los informes son JSON nativo; cada prueba trabaja con copias)."""
    return {"a": cohort(O1, 1, 200), "b": cohort(O2, 1001, 200, phase=1), "c": cohort(O3, 2001, 200, phase=2),
            "golden": golden_report()}


# ------------------------------------------------------------------------------------------- escenario dorado

def test_01_golden_five_rows_denominators_and_measures(base):
    report = base["golden"]
    c = summary(report)
    assert c["status"] == VERIFIED and c["reasons"] == []
    den = c["denominators"]
    assert (den["input_total"], den["settled_total"], den["scored_settled"], den["abstained_settled"], den["no_estimate_input"],
            den["label_missing_input"]) == (5, 5, 3, 2, 2, 0)
    assert den["settled_by_class"] == {"OVER": 1, "UNDER": 2, "PUSH": 2}
    assert den["scored_by_class"] == {"OVER": 1, "UNDER": 1, "PUSH": 1}
    assert den["abstained_by_class"] == {"OVER": 0, "UNDER": 1, "PUSH": 1}
    m = c["measures"]
    assert (m["estimate_coverage"]["numerator"], m["estimate_coverage"]["count"], m["estimate_coverage"]["value"]) == (3, 5, 3 / 5)
    assert m["abstention_rate_settled"]["value"] == 2 / 5 and m["abstention_rate_settled"]["count"] == 5
    assert [m[f"settled_share.{k}"]["value"] for k in ("OVER", "UNDER", "PUSH")] == [1 / 5, 2 / 5, 2 / 5]   # 109A: todas
    assert [m[f"scored_share.{k}"]["value"] for k in ("OVER", "UNDER", "PUSH")] == [1 / 3, 1 / 3, 1 / 3]   # 109B: puntuadas
    assert m["label_missing_rate"]["value"] == 0.0 and m["settled_share.PUSH"]["population"] == "SETTLED"
    # Brier de tres clases fila a fila: 0.545 (OVER), 0.845 (PUSH), 0.645 (UNDER); la media es exactamente la de 109A
    (group,) = report["oos_report"]["folds"][0]["groups"]
    assert m["brier_mean"]["value"] == pytest.approx(2.035 / 3, abs=1e-15)
    assert c["reconciliation"]["model_error"] == RECONCILED
    assert c["reconciliation"]["model_error_folds"][0]["reconstructed"] == group["model"]
    assert m["brier_mean"]["count"] == group["model"]["scored"] == 3
    assert all(m[k]["support"] == BELOW_MIN_SUPPORT for k in m)  # cinco filas con min_support 30
    assert "SMALL_COHORT_BELOW_MIN_SUPPORT" in c["warnings"] and c["labels_complete"] is True
    assert c["calibration"]["scored"] == 3 and c["calibration"]["sample_status"] == "INSUFFICIENT_SUPPORT"


# ------------------------------------------------------------------------------------- mercados y denominadores

def test_02_btts_binary_summary_and_comparison():
    a, b = cohort(O1, 1, 120, market=BTTS, line=None), cohort(O2, 501, 120, market=BTTS, line=None, phase=1)
    c = summary(a)
    assert set(c["measures"]) == {"estimate_coverage", "label_missing_rate", "abstention_rate_settled", "settled_share.YES",
                                  "settled_share.NO", "scored_share.YES", "scored_share.NO", "brier_mean", "log_loss_mean"}
    assert c["reconciliation"]["model_error"] == RECONCILED and c["calibration"]["metric_basis"] == "BINARY_EVENT"
    cmp = compare_goals_cohorts(a, b, MONITOR)
    assert cmp["result"]["status"] == COMPARED and cmp["measures"]["brier_mean"]["status"] == COMPARED


def test_03_half_line_is_binary_without_push():
    a = cohort(O1, 1, 120, line="2.5")
    m = summary(a)["measures"]
    assert "settled_share.PUSH" not in m and "scored_share.PUSH" not in m
    assert m["settled_share.OVER"]["value"] + m["settled_share.UNDER"]["value"] == pytest.approx(1.0)
    assert summary(a)["reconciliation"]["model_error"] == RECONCILED


def test_04_integer_line_push_is_a_settled_class_never_void(base):
    c = summary(base["a"])
    den = c["denominators"]
    assert den["settled_by_class"]["PUSH"] > 0 and den["label_missing_by_reason"] == {}
    assert c["measures"]["settled_share.PUSH"]["numerator"] == den["settled_by_class"]["PUSH"]
    assert set(c["measures"]) | {"ece"} == MEASURES_INTEGER
    # un partido no terminado (CANC) no es PUSH: es etiqueta ausente
    void = run_goals_research_consumer([obs(1, O1 + timedelta(hours=1), score=(2, 0)),
                                        obs(2, O1 + timedelta(hours=7), status_short="CANC", score=(None, None))],
                                       consumer_config((O1,)))
    v = summary(void, SMALL)
    assert v["denominators"]["settled_by_class"]["PUSH"] == 1 and v["denominators"]["label_missing_by_reason"] == {"status_not_finished": 1}


def test_05_all_no_estimate_has_no_model_error():
    a = cohort(O1, 1, 60, abstain_every=1)
    b = cohort(O2, 501, 60, abstain_every=1, phase=1)
    c = summary(a, SMALL)
    assert c["denominators"]["scored_settled"] == 0 and c["measures"]["abstention_rate_settled"]["value"] == 1.0
    assert c["measures"]["brier_mean"]["support"] == NO_DATA and c["measures"]["brier_mean"]["value"] is None
    assert "ALL_SETTLED_ABSTAINED" in c["warnings"]
    cmp = compare_goals_cohorts(a, b, SMALL)
    assert cmp["measures"]["brier_mean"]["status"] == NO_COMPARISON and cmp["measures"]["brier_mean"]["reasons"] == [NO_DATA]
    assert cmp["measures"]["scored_share.OVER"]["status"] == NO_COMPARISON
    assert cmp["measures"]["abstention_rate_settled"]["status"] == COMPARED and cmp["result"]["status"] == COMPARED


def test_06_no_estimate_without_final_outcome_is_neither_settled_nor_scored(base):
    rows = cohort_rows(O2, 1001, 60, phase=1)
    rows[1] = obs(1002, rows[1]["kickoff_at"], status="NO_ESTIMATE", observed=None)
    b = run_goals_research_consumer(rows, consumer_config((O2,)))
    c = summary(b, SMALL)
    den = c["denominators"]
    assert (den["label_missing_no_estimate"], den["label_missing_estimate"], den["settled_total"]) == (1, 0, 59)
    assert den["label_missing_by_reason"] == {"outcome_observed_at_missing": 1}
    assert c["labels_complete"] is False and "LABELS_INCOMPLETE" in c["warnings"]
    assert c["measures"]["abstention_rate_settled"]["population_status"] == "LABELS_INCOMPLETE"
    cmp = compare_goals_cohorts(cohort(O1, 1, 60), b, SMALL)
    assert cmp["measures"]["settled_share.UNDER"]["status"] == NOT_COMPARABLE
    assert cmp["measures"]["settled_share.UNDER"]["reasons"] == [INCOMPLETE_LABELS]
    assert cmp["measures"]["ece"]["status"] == NOT_COMPARABLE and cmp["measures"]["brier_mean"]["status"] == NOT_COMPARABLE
    # la completitud de etiquetas sí se compara: no depende del resultado
    assert cmp["measures"]["label_missing_rate"]["status"] == COMPARED
    assert cmp["measures"]["label_missing_rate"]["difference"] == pytest.approx(1 / 60)


def test_07_missing_labels_among_estimates_are_not_model_errors():
    rows = cohort_rows(O1, 1, 60)
    rows[1] = obs(2, rows[1]["kickoff_at"], status_short="NS", score=(None, None))       # ESTIMATE sin terminar
    rows[2] = obs(3, rows[2]["kickoff_at"], observed=None)                               # ESTIMATE sin evidencia
    report = run_goals_research_consumer(rows, consumer_config((O1,)))
    c = summary(report, SMALL)
    den = c["denominators"]
    assert (den["label_missing_estimate"], den["scored_settled"]) == (2, den["estimate_input"] - 2)
    assert c["measures"]["brier_mean"]["count"] == den["scored_settled"]
    assert c["calibration"]["scored"] == den["scored_settled"]
    assert report["reliability_reports"]["pooled"]["coverage"]["label_missing"] == 2


def test_08_denominators_reconcile_with_both_sources(base):
    report = base["a"]
    c = summary(report)
    den = c["denominators"]
    assert den == report["pooled"]["denominators"]
    assert den["settled_total"] == den["scored_settled"] + den["abstained_settled"]
    assert den["input_total"] == den["settled_total"] + den["label_missing_input"]
    oos = report["oos_report"]["folds"][0]["groups"][0]["outcome_counts"]
    assert oos == den["settled_by_class"] != den["scored_by_class"]
    assert report["reliability_reports"]["pooled"]["outcome_counts"] == den["scored_by_class"]
    m = c["measures"]
    assert m["scored_share.OVER"]["count"] == den["scored_settled"] and m["settled_share.OVER"]["count"] == den["settled_total"]
    assert m["abstention_rate_settled"]["numerator"] == den["abstained_settled"]


# --------------------------------------------------------------------------------------------- comparaciones

def test_09_clean_comparison_with_meaningful_support(base):
    a, b = base["a"], base["b"]
    cmp = compare_goals_cohorts(a, b, MONITOR)
    assert cmp["document"] == COHORT_COMPARISON and cmp["result"] == {"status": COMPARED, "reasons": []}
    assert set(cmp["measures"]) == MEASURES_INTEGER and cmp["interpretation"] == INTERPRETATION
    sa, sb = summary(a)["measures"], summary(b)["measures"]
    for name, m in cmp["measures"].items():
        assert m["status"] == COMPARED, name
        if name == "ece":
            assert m["uncertainty"]["status"] == "NOT_COMPUTED" and m["difference"] == m["candidate"]["ece"] - m["reference"]["ece"]
            continue
        assert m["difference"] == sb[name]["value"] - sa[name]["value"]
        u = m["uncertainty"]
        assert u["status"] == EXPLORATORY_BOOTSTRAP and u["replicates"] == RELIABILITY.iterations
        q = [v for _, v in u["quantiles"]]
        assert [k for k, _ in u["quantiles"]] == ["P10", "P50", "P90"] and q == sorted(q)
        assert "NOT_A_CONFIDENCE_INTERVAL" in u["interval_semantics"]
    assert cmp["warnings"] == []


def test_09b_shifted_abstention_is_described_not_diagnosed(base):
    shifted = cohort(O2, 1001, 200, phase=1, abstain_every=2)
    cmp = compare_goals_cohorts(base["a"], shifted, MONITOR)
    m = cmp["measures"]["abstention_rate_settled"]
    assert m["status"] == COMPARED and m["difference"] > 0.3
    assert m["uncertainty"]["p10_p90_excludes_zero"] is True
    assert cmp["interpretation"] == INTERPRETATION and "drift" not in json.dumps(m).lower()


def test_10_duplicate_and_overlapping_fixtures(base):
    a = base["a"]
    same = compare_goals_cohorts(a, copy.deepcopy(a), MONITOR)
    assert same["result"] == {"status": NOT_COMPARABLE, "reasons": [DUPLICATE_REPORT, OVERLAPPING_FIXTURES, TEMPORAL_OVERLAP]}
    assert same["measures"] is None
    shared = cohort(O2, 150, 200, phase=1)  # fixture_id 150..199 también están en a
    cmp = compare_goals_cohorts(a, shared, MONITOR)
    assert cmp["result"] == {"status": NOT_COMPARABLE, "reasons": [OVERLAPPING_FIXTURES]}
    # en una secuencia, también entre cohortes NO consecutivas
    c_overlap = cohort(O3, 1, 200, phase=2)
    seq = monitor_goals_cohorts([a, base["b"], c_overlap], MONITOR)
    assert [x["result"]["status"] for x in seq["comparisons"]] == [COMPARED, COMPARED]
    assert seq["result"] == {"status": NOT_COMPARABLE, "reasons": [OVERLAPPING_FIXTURES]}


@pytest.mark.parametrize("other, reason", [
    (lambda: cohort(O2, 1001, 200, producer_id="OTHER_PRODUCER"), PRODUCER_MISMATCH),
    (lambda: cohort(O2, 1001, 200, producer_version="2.0"), PRODUCER_VERSION_MISMATCH),
    (lambda: cohort(O2, 1001, 200, market=BTTS, line=None), MARKET_MISMATCH),
    (lambda: cohort(O2, 1001, 200, line="2.5"), LINE_MISMATCH),
    (lambda: cohort(O2, 1001, 200, line="3.0"), LINE_MISMATCH),
    (lambda: cohort(O2, 1001, 200, reliability=UncertaintyConfig(seed=8, iterations=40, min_weeks=2, min_total_support=30,
                                                                 min_bin_support=5)), RELIABILITY_CONFIG_MISMATCH),
])
def test_11_different_producer_version_market_line_or_reliability_config(base, other, reason):
    cmp = compare_goals_cohorts(base["a"], other(), MONITOR)
    assert cmp["result"] == {"status": NOT_COMPARABLE, "reasons": [reason]} and cmp["measures"] is None


# ------------------------------------------------------------------------------------------------- tiempo

def test_12_overlapping_or_reversed_temporal_ranges(base):
    a, b = base["a"], base["b"]
    overlapping = cohort(O1 + timedelta(days=28), 5001, 100, phase=1)  # holdout dentro del de a, fixtures disjuntos
    assert compare_goals_cohorts(a, overlapping, MONITOR)["result"] == {"status": NOT_COMPARABLE, "reasons": [TEMPORAL_OVERLAP]}
    # orden imposible: la "candidata" termina antes de que empiece la referencia
    assert compare_goals_cohorts(b, a, MONITOR)["result"] == {"status": NOT_COMPARABLE, "reasons": [TEMPORAL_ORDER_INVALID]}
    # entrelazadas: pliegues [O1] y [O3] frente a [O2]
    two_folds = run_goals_research_consumer(cohort_rows(O1, 7001, 100) + cohort_rows(O3, 7101, 100),
                                            consumer_config((O1, O3)))
    assert compare_goals_cohorts(two_folds, b, MONITOR)["result"]["reasons"] == [TEMPORAL_ORDER_INVALID]
    # contiguas ([.., O2) y [O2, ..)) sí son comparables
    assert compare_goals_cohorts(a, b, MONITOR)["result"]["status"] == COMPARED


@pytest.mark.parametrize("text, reason", [
    ("2025-03-03", TIMESTAMP_PRECISION_UNKNOWN),
    ("2025-03-03T01:00+00:00", TIMESTAMP_PRECISION_UNKNOWN),
    ("2025-03-03T01:00:00", TIMESTAMP_PRECISION_UNKNOWN),
    ("2025-03-03T01:00:00Z", TIMESTAMP_PRECISION_UNKNOWN),
    ("2025-03-03T01:00:00.123+00:00", TIMESTAMP_PRECISION_UNKNOWN),
    ("2025-03-03T01:00:00.000000+00:00", TIMESTAMP_NOT_CANONICAL_UTC),
    ("2025-03-03T03:00:00+02:00", TIMESTAMP_NOT_CANONICAL_UTC),
    ("2025-03-03T01:00:00-00:00", TIMESTAMP_NOT_CANONICAL_UTC),
])
def test_13_unknown_or_non_canonical_timestamp_precision_rejected(base, text, reason):
    def kickoff(r):
        r["ledger"][0]["kickoff_at"] = text

    def boundary(r):
        r["fold_groups"][0]["holdout_start"] = text

    for edit in (kickoff, boundary):
        c = summary(tampered(base["a"], edit))
        assert c["status"] == REJECTED and c["reasons"] == [reason]


def test_14_temporal_leakage_counterexamples(base):
    a = base["a"]
    # una fila movida fuera del holdout de su pliegue (después de su fin): orden imposible, aunque se recalcule el digest
    late = tampered(a, lambda r: r["ledger"][0].update(kickoff_at="2026-01-01T00:00:00+00:00"))
    assert summary(late)["reasons"] == [TEMPORAL_EVIDENCE_INCONSISTENT]
    inverted = tampered(a, lambda r: r["fold_groups"][0].update(holdout_start=r["fold_groups"][0]["holdout_end"]))
    assert summary(inverted)["reasons"] == [TEMPORAL_EVIDENCE_INCONSISTENT]
    # una línea base que incluye el periodo de la candidata es una fuga del futuro: no se compara
    future_baseline = cohort(O2 - timedelta(days=7), 9001, 100, phase=1)
    assert compare_goals_cohorts(future_baseline, base["b"], MONITOR)["result"]["reasons"] == [TEMPORAL_OVERLAP]
    # la fuga dentro de una fila (feature_observed_at > cutoff_at) ni siquiera produce evidencia del consumidor
    rows = cohort_rows(O1, 1, 3)
    rows[0]["feature_observed_at"] = rows[0]["kickoff_at"] - timedelta(minutes=30)
    with pytest.raises(ConsumerSourceError):
        run_goals_research_consumer(rows, consumer_config((O1,)))


def test_15_shared_iso_week_and_insufficient_weeks_have_no_bootstrap():
    # referencia hasta el jueves 2025-03-13 y candidata desde ese jueves: misma semana ISO, filas disjuntas
    ref = run_goals_research_consumer(cohort_rows(O1, 1, 40), consumer_config((O1,), holdout=timedelta(days=10)))
    thursday = O1 + timedelta(days=10)
    cand = run_goals_research_consumer(cohort_rows(thursday, 501, 40, phase=1), consumer_config((thursday,), holdout=timedelta(days=10)))
    cmp = compare_goals_cohorts(ref, cand, SMALL)
    assert cmp["result"]["status"] == COMPARED and "COHORTS_SHARE_ISO_WEEKS" in cmp["warnings"]
    assert cmp["measures"]["estimate_coverage"]["uncertainty"]["status"] == SHARED_WEEKS_NOT_INDEPENDENT
    assert cmp["measures"]["estimate_coverage"]["uncertainty"]["quantiles"] == []
    # una sola semana por cohorte: menos que min_weeks de 109B
    one = run_goals_research_consumer(cohort_rows(O1, 1, 20), consumer_config((O1,), holdout=timedelta(days=6)))
    other = run_goals_research_consumer(cohort_rows(O2, 501, 20, phase=1), consumer_config((O2,), holdout=timedelta(days=6)))
    cmp = compare_goals_cohorts(one, other, SMALL)
    assert cmp["measures"]["brier_mean"]["status"] == COMPARED
    assert cmp["measures"]["brier_mean"]["uncertainty"]["status"] == INSUFFICIENT_WEEKS


def test_15b_replicate_without_population_is_never_dropped_silently():
    # las ESTIMATE solo en la primera semana: alguna réplica no la sortea y su error medio no está definido
    rows = cohort_rows(O1, 1, 200)
    rows = [r if r["kickoff_at"] < O1 + timedelta(days=7) else dict(r, status="NO_ESTIMATE", probabilities=None) for r in rows]
    a = run_goals_research_consumer(rows, consumer_config((O1,)))
    cmp = compare_goals_cohorts(a, cohort(O2, 1001, 200, phase=1), SMALL)
    assert cmp["measures"]["brier_mean"]["status"] == COMPARED
    assert cmp["measures"]["brier_mean"]["uncertainty"]["status"] == UNDEFINED_IN_REPLICATE
    assert cmp["measures"]["estimate_coverage"]["uncertainty"]["status"] == EXPLORATORY_BOOTSTRAP


# ------------------------------------------------------------------------------------------------- soporte

def test_16_small_samples_give_insufficient_support(base):
    other = run_goals_research_consumer(cohort_rows(O2, 501, 6, phase=1), consumer_config((O2,), reliability=GOLDEN_RELIABILITY))
    cmp = compare_goals_cohorts(base["golden"], other, MONITOR)
    assert cmp["result"] == {"status": INSUFFICIENT_SUPPORT, "reasons": [NO_MEASURE_WITH_MIN_SUPPORT]}
    assert all(m["status"] == INSUFFICIENT_SUPPORT for m in cmp["measures"].values())
    assert cmp["measures"]["brier_mean"]["reasons"] == [BELOW_MIN_SUPPORT] and cmp["measures"]["brier_mean"]["difference"] is None
    assert cmp["measures"]["ece"]["reasons"] == [RELIABILITY_SAMPLE_INSUFFICIENT]
    assert "reference.SMALL_COHORT_BELOW_MIN_SUPPORT" in cmp["warnings"]
    # el mismo par con min_support 1 sí compara las tasas (y el ECE sigue exigiendo el soporte de 109B)
    small = compare_goals_cohorts(base["golden"], other, SMALL)
    assert small["result"]["status"] == COMPARED and small["measures"]["ece"]["status"] == INSUFFICIENT_SUPPORT


def test_16b_empty_cohort_and_single_cohort_sequences(base):
    empty = run_goals_research_consumer([obs(1, O2 - timedelta(days=60))], consumer_config((O2,)))  # solo train
    cmp = compare_goals_cohorts(base["a"], empty, MONITOR)
    assert cmp["result"] == {"status": NO_COMPARISON, "reasons": [EMPTY_COHORT]} and cmp["measures"] is None
    seq = monitor_goals_cohorts([base["a"]], MONITOR)
    assert seq["result"] == {"status": NO_COMPARISON, "reasons": [FEWER_THAN_TWO_COHORTS]} and seq["comparisons"] == []
    assert summary(empty)["measures"]["estimate_coverage"]["support"] == NO_DATA


# --------------------------------------------------------------------------------------- evidencia e identidad

def _oos_version_two(report):
    """109A con otra versión y su report_digest recalculado: identidad coherente, versión no admitida."""
    oos = report["oos_report"]
    oos["evaluator_version"] = "2"
    oos["report_digest"] = sha256_hex({k: v for k, v in oos.items() if k != "report_digest"})


@pytest.mark.parametrize("edit, reason", [
    (lambda r: dict(copy.deepcopy(r), report_digest="0" * 64), "EVIDENCE_DIGEST_MISMATCH"),
    (lambda r: tampered(r, lambda x: x["ledger"][0].update(label="PUSH" if x["ledger"][0]["label"] != "PUSH" else "OVER")),
     EVIDENCE_NOT_AUDITABLE),
    (lambda r: tampered(r, lambda x: x["reliability_reports"]["pooled"].update(ece=0.0)), "SOURCE_IDENTITY_MISMATCH"),
    (lambda r: tampered(r, lambda x: x["pooled"]["denominators"].update(scored_settled=1)), EVIDENCE_NOT_AUDITABLE),
    (lambda r: tampered(r, lambda x: x.update(consumer_version="2")), CONSUMER_VERSION_MISMATCH),
    (lambda r: tampered(r, _oos_version_two), "SOURCE_VERSION_MISMATCH"),
    (lambda r: {k: v for k, v in r.items() if k != "ledger"}, EVIDENCE_NOT_AUDITABLE),
    (lambda r: aggregate_goals_consumer_reports([r]), EVIDENCE_NOT_AUDITABLE),                    # agregado: solo resumen
    (lambda r: summarize_goals_cohort(r, MONITOR), EVIDENCE_NOT_AUDITABLE),                      # documento del monitor
    (lambda r: tampered(r, lambda x: x["ledger"][1].update(probabilities={"OVER": 0.9, "UNDER": 0.3, "PUSH": 0.2})),
     EVIDENCE_NOT_AUDITABLE),
    (lambda r: tampered(r, lambda x: x["ledger"][0].update(fixture_id=True)), EVIDENCE_NOT_AUDITABLE),
])
def test_17_tampered_or_summary_only_evidence_is_rejected(base, edit, reason):
    report = base["a"]
    bad = edit(report)
    c = summary(bad)
    assert c["status"] == REJECTED and c["reasons"] == [reason]
    cmp = compare_goals_cohorts(report, bad, MONITOR)
    assert cmp["result"] == {"status": NOT_COMPARABLE, "reasons": [EVIDENCE_REJECTED]}
    assert cmp["candidate"]["status"] == REJECTED and cmp["candidate"]["reasons"] == [reason]
    seq = monitor_goals_cohorts([report, bad], MONITOR)
    assert seq["result"]["status"] == NOT_COMPARABLE and EVIDENCE_REJECTED in seq["result"]["reasons"]
    assert [c["status"] for c in seq["cohorts"]] == [VERIFIED, REJECTED]


def test_18_monitoring_document_identity_detects_tampering(base):
    for doc in (summarize_goals_cohort(base["a"], MONITOR), compare_goals_cohorts(base["a"], base["b"], MONITOR),
                monitor_goals_cohorts([base["a"], base["b"]], MONITOR)):
        assert verify_monitoring_document(doc) is True
        assert verify_monitoring_document(json.loads(json.dumps(doc))) is True
        forged = copy.deepcopy(doc)
        forged["config"]["min_support"] = 1
        assert verify_monitoring_document(forged) is False
        assert verify_monitoring_document(dict(doc, document_digest="0" * 64)) is False
        assert verify_monitoring_document(dict(doc, monitoring_version="2")) is False
    with pytest.raises(MonitoringInputError):
        verify_monitoring_document(dict(summarize_goals_cohort(base["a"], MONITOR), extra=float("nan")))


def test_19_documents_are_deterministic_json_with_recomputable_identity(base):
    doc = monitor_goals_cohorts([base["a"], base["b"], base["c"]], MONITOR)
    assert doc["document"] == MONITORING_SEQUENCE and json.loads(json.dumps(doc)) == doc
    body = {k: v for k, v in doc.items() if k != "document_digest"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    assert doc["document_digest"] == hashlib.sha256(text.encode("ascii")).hexdigest()
    assert monitor_goals_cohorts([base["a"], base["b"], base["c"]], MONITOR) == doc
    assert summarize_goals_cohort(base["a"], MONITOR)["document"] == COHORT_SUMMARY
    assert doc["result"] == {"status": COMPARED, "reasons": []} and len(doc["comparisons"]) == 2
    assert [c["report_digest"] for c in doc["cohorts"]] == [base[k]["report_digest"] for k in "abc"]


def test_20_input_order_permutations_do_not_change_the_sequence(base):
    reports = [base["a"], base["b"], base["c"]]
    reference = monitor_goals_cohorts(reports, MONITOR)
    rng = random.Random(110)
    for _ in range(4):
        shuffled = [json.loads(json.dumps(r)) for r in reports]
        rng.shuffle(shuffled)
        shuffled = [dict(reversed(list(r.items()))) for r in shuffled]  # también otro orden de claves
        doc = monitor_goals_cohorts(tuple(shuffled) if rng.random() < 0.5 else shuffled, MONITOR)
        assert doc == reference


def test_20b_inputs_are_never_mutated(base):
    reports = [copy.deepcopy(base["a"]), copy.deepcopy(base["b"])]
    config = dict(MONITOR)
    before = copy.deepcopy((reports, config))
    monitor_goals_cohorts(reports, config)
    compare_goals_cohorts(reports[0], reports[1], config)
    assert (reports, config) == before


# ------------------------------------------------------------------------------- tipos exactos y objetos hostiles

CALLS = []


class HostileMeta(type):
    @property
    def __name__(cls):
        CALLS.append("metaclass __name__")
        raise RuntimeError("hostil")


class Hostile(metaclass=HostileMeta):
    def _hit(self, *args):
        CALLS.append("dunder")
        raise RuntimeError("hostil")

    __repr__ = __str__ = __format__ = __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _hit
    __bool__ = __float__ = __int__ = __index__ = __len__ = __iter__ = __deepcopy__ = __reduce_ex__ = _hit


class HostileKey:
    def __hash__(self):
        return 0

    def _hit(self, *args):
        CALLS.append("key dunder")
        raise RuntimeError("hostil")

    __repr__ = __str__ = __format__ = __eq__ = __lt__ = _hit


def test_21_hostile_objects_never_execute_and_messages_are_bounded(base):
    report = base["golden"]
    h = Hostile()
    cases = [
        lambda: summarize_goals_cohort(h, MONITOR),
        lambda: summarize_goals_cohort(dict(report, extra=h), MONITOR),
        lambda: summarize_goals_cohort(dict(report, ledger=[h]), MONITOR),
        lambda: summarize_goals_cohort({**report, HostileKey(): 1}, MONITOR),
        lambda: summarize_goals_cohort(report, h),
        lambda: summarize_goals_cohort(report, {**MONITOR, HostileKey(): 1}),
        lambda: summarize_goals_cohort(report, dict(MONITOR, min_support=h)),
        lambda: compare_goals_cohorts(report, h, MONITOR),
        lambda: monitor_goals_cohorts(h, MONITOR),
        lambda: monitor_goals_cohorts([report, h], MONITOR),
        lambda: verify_monitoring_document(h),
        lambda: summarize_goals_cohort(dict(report, extra="X" * 2_000_000), MONITOR),
    ]
    for case in cases:
        CALLS.clear()
        with pytest.raises(MonitoringInputError) as info:
            case()
        assert CALLS == []
        assert len(str(info.value)) <= MESSAGE_LIMIT + 3


@pytest.mark.parametrize("make", [
    lambda r: type("D", (dict,), {})(r),
    lambda r: dict(r, extra=type("S", (str,), {})("x")),
    lambda r: dict(r, extra=type("F", (float,), {})(1.0)),
    lambda r: dict(r, extra=type("I", (int,), {})(1)),
    lambda r: dict(r, ledger=type("L", (list,), {})(r["ledger"])),
    lambda r: dict(r, extra={1, 2}),
    lambda r: dict(r, extra=b"bytes"),
    lambda r: dict(r, extra=float("nan")),
    lambda r: dict(r, extra=float("inf")),
    lambda r: dict(r, extra=-float("inf")),
])
def test_22_subclasses_and_non_finite_numbers_rejected_at_boundary(base, make):
    with pytest.raises(MonitoringInputError):
        summarize_goals_cohort(make(base["golden"]), MONITOR)


@pytest.mark.parametrize("config", [
    None, [], {}, {"contract_version": CONTRACT_VERSION}, {**MONITOR, "extra": 1},
    {**MONITOR, "contract_version": "GOALS_RESEARCH_MONITORING_V2"}, {**MONITOR, "min_support": True},
    {**MONITOR, "min_support": 1.0}, {**MONITOR, "min_support": 0}, {**MONITOR, "min_support": 10 ** 6 + 1},
    {**MONITOR, "min_support": type("I", (int,), {})(30)}, type("D", (dict,), {})(MONITOR),
])
def test_23_config_contract_is_exact(base, config):
    with pytest.raises(MonitoringConfigError):
        summarize_goals_cohort(base["golden"], config)


def test_24_oversized_ids_deep_nesting_and_giant_seed(base):
    report = base["golden"]
    for bad in (FIXTURE_ID_LIMIT, -FIXTURE_ID_LIMIT, 10 ** 5000):  # sin redigest: la frontera lo rechaza antes de cualquier digest
        ledger = [dict(report["ledger"][0], fixture_id=bad)] + report["ledger"][1:]
        with pytest.raises(MonitoringInputError, match="10\\*\\*640"):
            summarize_goals_cohort(dict(report, ledger=ledger), MONITOR)
    deep = []
    for _ in range(100):
        deep = [deep]
    with pytest.raises(MonitoringInputError, match="profunda"):
        summarize_goals_cohort(dict(report, extra=deep), MONITOR)
    loop = []
    loop.append(loop)
    with pytest.raises(MonitoringInputError, match="profunda"):
        summarize_goals_cohort(dict(report, extra=loop), MONITOR)
    # el mayor fixture_id del consumidor y una semilla enorme de 109B (INT_HEX_V1) son evidencia válida
    giant = UncertaintyConfig(seed=10 ** 5000, iterations=20, min_weeks=2, min_total_support=30, min_bin_support=5)
    rows_a, rows_b = cohort_rows(O1, 1, 60), cohort_rows(O2, 501, 60, phase=1)
    rows_a[0]["fixture_id"] = FIXTURE_ID_LIMIT - 1
    a = run_goals_research_consumer(rows_a, consumer_config((O1,), reliability=giant))
    b = run_goals_research_consumer(rows_b, consumer_config((O2,), reliability=giant))
    assert a["reliability_reports"]["pooled"]["config"]["seed"] == {"INT_HEX_V1": "+" + format(10 ** 5000, "x")}
    cmp = compare_goals_cohorts(a, b, SMALL)
    assert cmp["result"]["status"] == COMPARED
    assert cmp["measures"]["brier_mean"]["uncertainty"]["status"] == EXPLORATORY_BOOTSTRAP
    assert summary(a, SMALL)["status"] == VERIFIED


# ------------------------------------------------------------------------------------------ fallos de integridad

def test_25_unreconciled_model_error_is_omitted_not_invented(base, monkeypatch):
    real = evidence.row_scores
    monkeypatch.setattr(evidence, "row_scores", lambda p, label, classes: tuple(x * 1.5 for x in real(p, label, classes)))
    c = summary(base["a"])
    assert c["reconciliation"]["model_error"] == NOT_RECONCILED and MODEL_ERROR_NOT_RECONCILED in c["warnings"]
    assert c["measures"]["brier_mean"]["value"] is None and c["measures"]["log_loss_mean"]["value"] is None
    cmp = compare_goals_cohorts(base["a"], base["b"], MONITOR)
    assert cmp["measures"]["brier_mean"]["status"] == NOT_COMPARABLE
    assert cmp["measures"]["brier_mean"]["reasons"] == [MODEL_ERROR_NOT_RECONCILED]
    assert cmp["measures"]["brier_mean"]["reference"]["value"] is None and cmp["result"]["status"] == COMPARED


def test_26_audited_report_that_does_not_reconcile_is_an_integrity_error(base, monkeypatch):
    real = evidence.recount

    def wrong(rows, classes):
        den = real(rows, classes)
        den["settled_total"] += 1
        return den

    monkeypatch.setattr(evidence, "recount", wrong)
    with pytest.raises(MonitoringIntegrityError):
        summarize_goals_cohort(base["golden"], MONITOR)


def test_27_error_namespace_is_separate_and_versioned():
    assert issubclass(MonitoringConfigError, MonitoringInputError) and issubclass(MonitoringInputError, GoalsMonitoringError)
    assert issubclass(MonitoringIntegrityError, GoalsMonitoringError) and not issubclass(MonitoringIntegrityError, MonitoringInputError)
    assert issubclass(GoalsMonitoringError, ValueError) and not issubclass(GoalsMonitoringError, GoalsConsumerError)
    with pytest.raises(MonitoringInputError, match="al menos un informe"):
        monitor_goals_cohorts([], MONITOR)


# ---------------------------------------------------------------------------------- rendimiento y aislamiento

def test_28_performance_bound_on_a_reasonable_synthetic_workload():
    reliability = UncertaintyConfig(seed=11, iterations=200, min_weeks=2, min_total_support=30, min_bin_support=5)
    origins = [O1 + timedelta(days=56 * k) for k in range(4)]
    reports = [run_goals_research_consumer(cohort_rows(o, 1 + 1000 * k, 220, phase=k), consumer_config((o,), reliability=reliability))
               for k, o in enumerate(origins)]
    start = time.perf_counter()
    doc = monitor_goals_cohorts(reports, MONITOR)
    elapsed = time.perf_counter() - start
    assert doc["result"]["status"] == COMPARED and len(doc["comparisons"]) == 3
    assert elapsed < 30, f"monitor de 4 cohortes x 220 filas x 200 réplicas: {elapsed:.1f} s"


_FORBIDDEN_EVENTS = ("open", "socket.", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.system",
                     "os.exec", "os.spawn", "os.startfile", "os.putenv", "os.unsetenv", "os.chdir", "subprocess.", "urllib.", "http.",
                     "sqlite3.", "ctypes.", "shutil.", "glob.", "tempfile.", "pathlib.")
_SEEN, _ARMED = [], [False]


def _audit_hook(event, args):
    if _ARMED[0] and event.startswith(_FORBIDDEN_EVENTS):
        _SEEN.append(event)


sys.addaudithook(_audit_hook)  # solo registra mientras _ARMED; inofensivo para el resto del proceso


def test_29_no_network_database_provider_or_filesystem_access(base):
    bad = dict(copy.deepcopy(base["a"]), report_digest="0" * 64)
    _SEEN.clear()
    _ARMED[0] = True
    try:  # control positivo: el gancho sí ve una apertura de fichero
        open(__file__, "rb").close()
    finally:
        _ARMED[0] = False
    assert _SEEN == ["open"]
    _SEEN.clear()
    _ARMED[0] = True
    try:
        summarize_goals_cohort(base["golden"], MONITOR)
        doc = compare_goals_cohorts(base["a"], base["b"], MONITOR)
        monitor_goals_cohorts([base["a"], base["b"], bad], MONITOR)
        verify_monitoring_document(doc)
    finally:
        _ARMED[0] = False
    assert _SEEN == []


def test_30_package_import_pulls_no_app_db_network_or_provider_module():
    backend = Path(__file__).resolve().parents[2]
    probe = ("import sys; import research.goals_monitoring_v1; "
             "bad = {'app', 'sqlalchemy', 'psycopg', 'psycopg2', 'asyncpg', 'alembic', 'fastapi', 'httpx', 'requests', "
             "'urllib3', 'aiohttp', 'socket', 'ssl', 'sqlite3'}; "
             "print(sorted(m for m in sys.modules if m.split('.')[0] in bad))")
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
    env.update(PYTHONPATH=str(backend), PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-B", "-c", probe], cwd=str(backend), env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"


def test_31_measure_values_are_finite_and_rates_bounded(base):
    doc = monitor_goals_cohorts([base["a"], base["b"], base["c"]], MONITOR)
    for c in doc["cohorts"]:
        for name, m in c["measures"].items():
            assert m["value"] is None or math.isfinite(m["value"])
            if m["kind"] == "RATE" and m["value"] is not None:
                assert 0.0 <= m["value"] <= 1.0 and m["value"] == m["numerator"] / m["count"]
            assert m["support"] in (NO_DATA, BELOW_MIN_SUPPORT, MEETS_MIN_SUPPORT)
