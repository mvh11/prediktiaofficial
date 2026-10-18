"""MOD-DEV-099: diagrama de fiabilidad de probabilidades binarias de goles. Sin BD, sin red, sin app.
    python -m pytest research/tests/test_goals_calibration_diagnostics.py -q   (desde backend/)
Predicciones y resultados sintéticos; los valores esperados están escritos a mano (no derivados de la
implementación). Ningún dato es un recibo auténtico de proveedor ni un histórico PIT."""

import copy
import dataclasses
import math
import os
import random
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.goals_calibration_diagnostics.reliability import (BIN_COUNT, BINNING_VERSION, DIAGNOSTIC_ID, DIAGNOSTIC_VERSION, LIMITATIONS,
                                                                LOWER_EDGES, MIN_BIN_SUPPORT, MIN_TOTAL_SUPPORT, DuplicateConflictError,
                                                                ProbabilityError, RecordError, ReliabilityDiagnostic, TemporalError,
                                                                bin_index, binary_reliability)
from research.goals_markets.evaluation import BTTS, OVER_UNDER

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = timezone.utc
KICKOFF = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
PREDICTED = KICKOFF - timedelta(hours=6)
OBSERVED = KICKOFF + timedelta(hours=3)
INTERVALS = ("[0.0, 0.1)", "[0.1, 0.2)", "[0.2, 0.3)", "[0.3, 0.4)", "[0.4, 0.5)", "[0.5, 0.6)", "[0.6, 0.7)", "[0.7, 0.8)",
             "[0.8, 0.9)", "[0.9, 1.0]")


def rec(fixture_id, probability, result, *, prediction_id="model-a", market=OVER_UNDER, line="2.5", event="OVER",
        predicted_at=PREDICTED, kickoff_at=KICKOFF, observed_at=OBSERVED, outcome=None, **extra):
    """Registro sintético; outcome sobrescribe campos del resultado (mismo partido y mercado por defecto)."""
    out = {"fixture_id": fixture_id, "market": market, "line": line, "result": result, "observed_at": observed_at, **(outcome or {})}
    return {"fixture_id": fixture_id, "prediction_id": prediction_id, "market": market, "line": line, "event": event,
            "probability": probability, "predicted_at": predicted_at, "kickoff_at": kickoff_at, "outcome": out, **extra}


def ou(records):
    return binary_reliability(records, market=OVER_UNDER, line="2.5", event="OVER")


def group(start, probability, positives, total):
    """total registros con la misma probabilidad, positives de ellos con OVER."""
    return [rec(start + i, probability, "OVER" if i < positives else "UNDER") for i in range(total)]


def table(result):
    return [(b.index, b.count, b.positives, b.mean_predicted, b.observed_fraction, b.support) for b in result.bins]


# --- A/B: frecuencias calibradas y mal calibradas ---------------------------------------------------

def test_perfectly_calibrated_synthetic_frequencies():
    records = [r for k in range(10) for r in group(1000 * (k + 1), (2 * k + 1) / 20, 2 * k + 1, 20)]
    result = ou(records)
    assert [b.count for b in result.bins] == [20] * 10
    assert [b.positives for b in result.bins] == [1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    expected = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    assert [b.observed_fraction for b in result.bins] == expected
    for b, p in zip(result.bins, expected):
        assert abs(b.mean_predicted - p) <= 1e-15 and abs(b.mean_predicted - b.observed_fraction) <= 1e-15
    assert (result.total_input, result.accepted, result.positives, result.distinct_fixtures) == (200, 200, 100, 200)


def test_deliberately_miscalibrated_synthetic_frequencies():
    result = ou(group(1, 0.8, 2, 10) + group(100, 0.15, 6, 10))
    assert table(result)[1] == (1, 10, 6, 0.15, 0.6, "BELOW_MIN_SUPPORT")
    assert table(result)[8] == (8, 10, 2, 0.8, 0.2, "BELOW_MIN_SUPPORT")
    assert all(b.count == 0 for b in result.bins if b.index not in (1, 8))


def test_equal_scalar_brier_but_different_reliability():
    """El diagnóstico no es un Brier: dos muestras con el mismo Brier binario (0.25) tienen tablas distintas."""
    calibrated = [rec(1, 0.5, "OVER"), rec(2, 0.5, "OVER"), rec(3, 0.5, "UNDER"), rec(4, 0.5, "UNDER")]
    overconfident = [rec(1, 0.0, "OVER"), rec(2, 0.0, "UNDER"), rec(3, 0.0, "UNDER"), rec(4, 0.0, "UNDER")]
    brier = [sum((r["probability"] - (r["outcome"]["result"] == "OVER")) ** 2 for r in rs) / 4 for rs in (calibrated, overconfident)]
    assert brier == [0.25, 0.25]
    assert table(ou(calibrated))[5][1:5] == (4, 2, 0.5, 0.5)
    assert table(ou(overconfident))[0][1:5] == (4, 1, 0.0, 0.25)


def test_btts_event_and_complementary_event_are_separate_tables():
    records = [rec(1, 0.7, "YES", market=BTTS, line=None, event="YES"), rec(2, 0.7, "NO", market=BTTS, line=None, event="YES")]
    result = binary_reliability(records, market=BTTS, event="YES")
    assert (result.market, result.line, result.event) == (BTTS, None, "YES")
    assert table(result)[7][1:5] == (2, 1, 0.7, 0.5)
    with pytest.raises(RecordError):  # P(NO) no se mezcla en la tabla de P(YES)
        binary_reliability(records + [rec(3, 0.3, "NO", market=BTTS, line=None, event="NO")], market=BTTS, event="YES")
    under = binary_reliability([rec(1, 0.3, "UNDER", event="UNDER")], market=OVER_UNDER, line="2.5", event="UNDER")
    assert table(under)[3][1:5] == (1, 1, 0.3, 1.0)


# --- C/D/E/F: bordes, p = 0, p = 1 y bins vacíos ---------------------------------------------------

def test_binning_policy_is_fixed_and_versioned():
    assert BIN_COUNT == 10 and BINNING_VERSION == "EQUAL_WIDTH_10_LEFT_CLOSED_LAST_BIN_CLOSED_V1"
    assert LOWER_EDGES == (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    result = ou([])
    assert [b.interval for b in result.bins] == list(INTERVALS)
    assert [b.index for b in result.bins] == list(range(10))
    assert [b.upper_closed for b in result.bins] == [False] * 9 + [True]
    assert [(b.lower, b.upper) for b in result.bins][9] == (0.9, 1.0)


def test_bin_boundaries_belong_to_the_bin_they_open():
    for k in range(1, 10):
        edge = k / 10
        assert bin_index(edge) == k
        assert bin_index(math.nextafter(edge, 0)) == k - 1
    assert bin_index(0.3) == 3 and bin_index(0.1 + 0.2) == 3 and bin_index(0.30000000000000004) == 3
    assert bin_index(0.29999999999999993) == 2
    result = ou([rec(1, 0.1, "OVER"), rec(2, math.nextafter(0.1, 0), "UNDER")])
    assert (result.bins[0].count, result.bins[1].count) == (1, 1)


def test_exact_zero_probability():
    result = ou([rec(1, 0.0, "UNDER"), rec(2, 0, "OVER"), rec(3, -0.0, "UNDER")])
    assert table(result)[0] == (0, 3, 1, 0.0, 1 / 3, "BELOW_MIN_SUPPORT")
    assert math.copysign(1, result.bins[0].mean_predicted) == 1


def test_exact_one_probability():
    result = ou([rec(1, 1.0, "OVER"), rec(2, 1, "OVER"), rec(3, 0.9, "UNDER")])
    assert table(result)[9][:3] == (9, 3, 2)
    assert abs(result.bins[9].mean_predicted - 2.9 / 3) <= 1e-15 and result.bins[9].observed_fraction == 2 / 3


def test_empty_bins_have_no_fabricated_values():
    result = ou([rec(1, 0.55, "OVER")])
    for b in result.bins:
        if b.index == 5:
            assert (b.count, b.positives, b.mean_predicted, b.observed_fraction) == (1, 1, 0.55, 1.0)
        else:
            assert (b.count, b.positives, b.mean_predicted, b.observed_fraction, b.support) == (0, 0, None, None, "EMPTY")


# --- G/H: sin datos y poco soporte -----------------------------------------------------------------

@pytest.mark.parametrize("empty", [[], ()])
def test_empty_input_is_no_data(empty):
    result = ou(empty)
    assert (result.total_input, result.accepted, result.duplicates_collapsed, result.positives, result.distinct_fixtures) == (0, 0, 0, 0, 0)
    assert result.sample_status == "NO_DATA"
    assert all(b.support == "EMPTY" and b.observed_fraction is None for b in result.bins) and len(result.bins) == 10


@pytest.mark.parametrize("bad", [None, iter([]), {"a": 1}, "records", rec(1, 0.5, "OVER")])
def test_records_must_be_a_list_or_tuple(bad):
    with pytest.raises(RecordError):
        ou(bad)


def test_low_support_values_are_reported_but_flagged():
    result = ou(group(1, 0.42, 2, 5))
    assert result.sample_status == "BELOW_MIN_TOTAL"
    index, count, positives, mean, observed, support = table(result)[4]
    assert (index, count, positives, observed, support) == (4, 5, 2, 0.4, "BELOW_MIN_SUPPORT") and abs(mean - 0.42) <= 1e-15


def test_support_thresholds():
    assert (MIN_BIN_SUPPORT, MIN_TOTAL_SUPPORT) == (30, 300)
    exactly = ou(group(1, 0.5, 15, 30))
    assert exactly.bins[5].support == "MEETS_MIN_SUPPORT" and exactly.sample_status == "BELOW_MIN_TOTAL"
    assert ou(group(1, 0.5, 15, 29)).bins[5].support == "BELOW_MIN_SUPPORT"
    full = ou([r for k in range(10) for r in group(1000 * (k + 1), k / 10 + 0.05, 15, 30)])
    assert full.accepted == 300 and full.sample_status == "MEETS_MIN_TOTAL" and (full.min_bin_support, full.min_total_support) == (30, 300)
    assert ou(group(1, 0.5, 150, 299)).sample_status == "BELOW_MIN_TOTAL"
    assert ou(group(1, 0.5, 150, 300)).sample_status == "MEETS_MIN_TOTAL"


# --- I/J/K: orden e identidad ----------------------------------------------------------------------

def test_shuffled_input_gives_identical_output():
    records = group(1, 0.13, 4, 9) + group(50, 0.61, 3, 7) + group(80, 0.97, 5, 6) + [rec(1, 0.13, "OVER", prediction_id="model-b")]
    records += [copy.deepcopy(records[3]), copy.deepcopy(records[10])]
    reference = ou(records)
    for seed in range(5):
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        assert ou(shuffled) == reference and repr(ou(tuple(shuffled))) == repr(reference)
    assert ou(list(reversed(records))) == reference


def test_identical_duplicates_count_once():
    original = rec(7, 0.5, "OVER")
    same_instant = rec(7, 0.5, "OVER", predicted_at=PREDICTED.astimezone(timezone(timedelta(hours=2))))
    result = ou([original, copy.deepcopy(original), same_instant])
    assert (result.total_input, result.duplicates_collapsed, result.accepted, result.bins[5].count) == (3, 2, 1, 1)
    integer = ou([rec(8, 1, "OVER"), rec(8, 1.0, "OVER")])
    assert (integer.accepted, integer.duplicates_collapsed) == (1, 1)


def test_distinct_predictions_on_the_same_fixture_are_not_merged():
    result = ou([rec(7, 0.5, "OVER", prediction_id="model-a"), rec(7, 0.5, "OVER", prediction_id="model-b")])
    assert (result.accepted, result.duplicates_collapsed, result.distinct_fixtures, result.bins[5].count) == (2, 0, 1, 2)
    assert "PREDICTIONS_MAY_NOT_BE_INDEPENDENT" in result.limitations


@pytest.mark.parametrize("other", [
    rec(7, 0.51, "OVER"),
    rec(7, 0.5, "UNDER"),
    rec(7, 0.5, "OVER", predicted_at=PREDICTED - timedelta(seconds=1)),
    rec(7, 0.5, "OVER", kickoff_at=KICKOFF + timedelta(minutes=1)),
    rec(7, 0.5, "OVER", observed_at=OBSERVED + timedelta(days=1)),
])
def test_conflicting_duplicate_identity_is_rejected(other):
    for records in ([rec(7, 0.5, "OVER"), other], [other, rec(7, 0.5, "OVER")]):
        with pytest.raises(DuplicateConflictError):
            ou(records)


# --- L/M/N/O: probabilidades -----------------------------------------------------------------------

@pytest.mark.parametrize("p", [-0.1, -1e-300, -1, 1.0000000000000002, 1.5, 2, math.nan, math.inf, -math.inf, True, False,
                               "0.5", Decimal("0.5"), Fraction(1, 2), None, [0.5], (0.5, 0.5)])
def test_invalid_probabilities_are_rejected_without_clamping(p):
    with pytest.raises(ProbabilityError):
        ou([rec(1, 0.5, "OVER"), rec(2, p, "OVER")])


# --- P/Q: resultado observado e identidad del evento ------------------------------------------------

@pytest.mark.parametrize("result", ["PUSH", "over", "YES", "", None, True, 1, 1.0])
def test_invalid_observed_labels_over_under(result):
    with pytest.raises(RecordError):
        ou([rec(1, 0.5, result)])


@pytest.mark.parametrize("result", ["OVER", "yes", None, False])
def test_invalid_observed_labels_btts(result):
    with pytest.raises(RecordError):
        binary_reliability([rec(1, 0.5, result, market=BTTS, line=None, event="YES")], market=BTTS, event="YES")


@pytest.mark.parametrize("record", [
    rec(1, 0.5, "OVER", event="UNDER"),
    rec(1, 0.5, "OVER", line="3.5"),
    rec(1, 0.5, "YES", market=BTTS, line=None, event="YES"),
    rec(1, 0.5, "OVER", outcome={"line": "3.5"}),
    rec(1, 0.5, "OVER", outcome={"market": BTTS}),
    rec(1, 0.5, "OVER", outcome={"fixture_id": 2}),
    rec(1, 0.5, "OVER", outcome={"fixture_id": True}),
    rec(1, 0.5, "OVER", outcome={"fixture_id": 1.0}),
])
def test_prediction_and_outcome_event_identity_must_match(record):
    with pytest.raises(RecordError):
        ou([record])


@pytest.mark.parametrize("target", [
    {"market": OVER_UNDER, "line": "2.0", "event": "OVER"},
    {"market": OVER_UNDER, "line": Decimal("2.5"), "event": "OVER"},
    {"market": OVER_UNDER, "line": 2.5, "event": "OVER"},
    {"market": OVER_UNDER, "line": None, "event": "OVER"},
    {"market": OVER_UNDER, "line": "7.5", "event": "OVER"},
    {"market": OVER_UNDER, "line": "2.5", "event": "PUSH"},
    {"market": BTTS, "line": "2.5", "event": "YES"},
    {"market": BTTS, "line": None, "event": "OVER"},
    {"market": "1X2", "line": None, "event": "H"},
])
def test_invalid_targets_fail_even_without_records(target):
    with pytest.raises(RecordError):
        binary_reliability([], **target)


@pytest.mark.parametrize("change", [
    lambda r: r.pop("probability"),
    lambda r: r.pop("outcome"),
    lambda r: r["outcome"].pop("result"),
    lambda r: r.update(home_goals=3, away_goals=1),  # nunca se liquida desde marcadores operativos
    lambda r: r["outcome"].update(fulltime_home=2),
    lambda r: r.update(fixture_id=0),
    lambda r: r.update(fixture_id=True),
    lambda r: r.update(fixture_id="1"),
    lambda r: r.update(prediction_id=""),
    lambda r: r.update(prediction_id=" model-a"),
    lambda r: r.update(prediction_id=1),
    lambda r: r.update(outcome=("OVER",)),
])
def test_missing_extra_or_invalid_fields_are_rejected(change):
    record = rec(1, 0.5, "OVER")
    change(record)
    with pytest.raises(RecordError):
        ou([record])


def test_non_mapping_record_is_rejected():
    with pytest.raises(RecordError):
        ou([[1, 0.5, "OVER"]])


# --- R: tiempo -------------------------------------------------------------------------------------

@pytest.mark.parametrize("fields", [
    {"predicted_at": PREDICTED.replace(tzinfo=None)},
    {"kickoff_at": KICKOFF.replace(tzinfo=None)},
    {"observed_at": OBSERVED.replace(tzinfo=None)},
    {"predicted_at": date(2026, 3, 1)},
    {"predicted_at": PREDICTED.isoformat()},
    {"observed_at": 1772377200},
    {"predicted_at": KICKOFF},
    {"predicted_at": KICKOFF + timedelta(minutes=5)},
    {"observed_at": KICKOFF},
    {"observed_at": KICKOFF - timedelta(minutes=1)},
    {"predicted_at": OBSERVED, "observed_at": PREDICTED},
    {"predicted_at": datetime(2026, 3, 1, 17, 30, tzinfo=timezone(timedelta(hours=2)))},  # 15:30Z, después del saque
])
def test_temporal_validation(fields):
    with pytest.raises(TemporalError):
        ou([rec(1, 0.5, "OVER", **fields)])


def test_temporal_order_is_compared_as_instants():
    local = datetime(2026, 3, 1, 16, 30, tzinfo=timezone(timedelta(hours=2)))  # 14:30Z, antes del saque
    assert ou([rec(1, 0.5, "OVER", predicted_at=local)]).accepted == 1


# --- S/T: procedencia e inmutabilidad --------------------------------------------------------------

def test_identity_and_provenance_limitations_are_explicit():
    result = ou([rec(1, 0.5, "OVER")])
    assert (result.diagnostic_id, result.diagnostic_version, DIAGNOSTIC_ID, DIAGNOSTIC_VERSION) == (
        "GOALS_BINARY_RELIABILITY", "1", "GOALS_BINARY_RELIABILITY", "1")
    assert result.binning_version == "EQUAL_WIDTH_10_LEFT_CLOSED_LAST_BIN_CLOSED_V1"
    assert result.evidence_basis == "CALLER_SUPPLIED_PREDICTIONS_AND_RETROSPECTIVE_OUTCOMES"
    assert result.pit_status == "NOT_CERTIFIED"
    assert result.limitations == LIMITATIONS == (
        "OUTCOMES_SUPPLIED_BY_CALLER_NOT_VERIFIED",
        "TIMESTAMPS_SUPPLIED_BY_CALLER_NOT_AUTHENTICATED",
        "NOT_TRUE_PIT_CERTIFIED",
        "NOT_A_COMPLETE_HISTORICAL_COHORT",
        "PREDICTIONS_MAY_NOT_BE_INDEPENDENT",
        "NO_CONFIDENCE_INTERVALS_OR_CALIBRATION_CERTIFICATION",
        "SUPPORT_THRESHOLDS_ARE_REPORTING_RULES_NOT_STATISTICAL_TESTS",
    )


def test_output_schema_has_no_scalar_score_or_certification():
    assert [f.name for f in dataclasses.fields(ReliabilityDiagnostic)] == [
        "diagnostic_id", "diagnostic_version", "binning_version", "market", "line", "event", "total_input", "duplicates_collapsed",
        "accepted", "distinct_fixtures", "positives", "min_bin_support", "min_total_support", "sample_status", "bins",
        "evidence_basis", "pit_status", "limitations"]
    assert [f.name for f in dataclasses.fields(type(ou([]).bins[0]))] == [
        "index", "lower", "upper", "upper_closed", "interval", "count", "positives", "mean_predicted", "observed_fraction", "support"]


def test_inputs_are_not_modified_and_output_is_frozen():
    records = group(1, 0.25, 1, 4) + [rec(1, 0.25, "OVER")]
    snapshot = copy.deepcopy(records)
    result = ou(records)
    assert records == snapshot
    assert isinstance(result.bins, tuple) and isinstance(result.limitations, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.accepted = 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.bins[0].count = 1


def test_module_imports_no_app_database_or_numeric_stack():
    code = ("import sys; import research.goals_calibration_diagnostics.reliability; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'requests', 'httpx')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"
