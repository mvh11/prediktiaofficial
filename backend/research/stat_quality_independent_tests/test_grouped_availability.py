"""Resumen agregado de disponibilidad por grupo: datos sintéticos, sin BD ni app.*.
Se ejecuta por ruta explícita: python -m pytest research/stat_quality_independent_tests -q"""

import itertools
import random

import pytest

from research.stat_quality_independent.availability import AvailabilitySummary, summarize_availability
from research.stat_quality_independent.grouped_availability import summarize_availability_by_group

OBSERVATIONS = [
    ("liga-b", "AVAILABLE"),
    ("liga-a", "EMPTY"),
    ("liga-c", "AVAILABLE"),
    ("liga-a", "EMPTY"),
    ("liga-c", "EMPTY"),
    ("liga-b", "AVAILABLE"),
    ("liga-c", "PARTIAL"),
    ("liga-c", "AVAILABLE"),
    ("liga-b", "AVAILABLE"),
]


def test_no_observations():
    for empty in ([], (), iter(())):
        result = summarize_availability_by_group(empty)
        assert dict(result) == {}
        assert len(result) == 0


def test_single_group_single_observation():
    result = summarize_availability_by_group([("liga-a", "AVAILABLE")])
    assert list(result) == ["liga-a"]
    summary = result["liga-a"]
    assert isinstance(summary, AvailabilitySummary)
    assert summary.total_observations == 1
    assert dict(summary.counts_by_classification) == {"AVAILABLE": 1}
    assert summary.available_proportion == 1.0


def test_multiple_groups():
    result = summarize_availability_by_group(OBSERVATIONS)
    assert list(result) == ["liga-a", "liga-b", "liga-c"]
    assert [s.total_observations for s in result.values()] == [2, 3, 4]
    assert sum(s.total_observations for s in result.values()) == len(OBSERVATIONS)


def test_group_all_available():
    summary = summarize_availability_by_group(OBSERVATIONS)["liga-b"]
    assert dict(summary.counts_by_classification) == {"AVAILABLE": 3}
    assert dict(summary.relative_frequencies) == {"AVAILABLE": 1.0}
    assert summary.available_proportion == 1.0


def test_group_all_empty_has_zero_available_proportion():
    summary = summarize_availability_by_group(OBSERVATIONS)["liga-a"]
    assert summary.total_observations == 2
    assert dict(summary.counts_by_classification) == {"EMPTY": 2}
    assert summary.available_proportion == 0.0  # grupo no vacío sin AVAILABLE: 0.0, no None


def test_mixed_group():
    summary = summarize_availability_by_group(OBSERVATIONS)["liga-c"]
    assert summary.total_observations == 4
    assert dict(summary.counts_by_classification) == {"AVAILABLE": 2, "EMPTY": 1, "PARTIAL": 1}
    assert dict(summary.relative_frequencies) == {"AVAILABLE": 0.5, "EMPTY": 0.25, "PARTIAL": 0.25}
    assert summary.available_proportion == 0.5


def test_each_group_matches_the_ungrouped_summary_of_its_own_labels():
    result = summarize_availability_by_group(OBSERVATIONS)
    for key, summary in result.items():
        assert summary == summarize_availability([label for group, label in OBSERVATIONS if group == key])


def test_repeated_observations_within_a_group_are_counted_repeatedly():
    once = summarize_availability_by_group([("liga-a", "AVAILABLE"), ("liga-a", "EMPTY")])
    thrice = summarize_availability_by_group([("liga-a", "AVAILABLE"), ("liga-a", "EMPTY")] * 3)
    assert once["liga-a"].total_observations == 2
    assert thrice["liga-a"].total_observations == 6  # sin deduplicar pares idénticos
    assert dict(thrice["liga-a"].counts_by_classification) == {"AVAILABLE": 3, "EMPTY": 3}
    assert thrice["liga-a"].available_proportion == once["liga-a"].available_proportion == 0.5


def test_same_label_in_different_groups_is_counted_independently():
    result = summarize_availability_by_group(
        [("liga-a", "AVAILABLE"), ("liga-b", "AVAILABLE"), ("liga-b", "AVAILABLE"), ("liga-b", "EMPTY")]
    )
    assert dict(result["liga-a"].counts_by_classification) == {"AVAILABLE": 1}
    assert dict(result["liga-b"].counts_by_classification) == {"AVAILABLE": 2, "EMPTY": 1}
    assert result["liga-a"].available_proportion == 1.0
    assert result["liga-b"].available_proportion == 2 / 3


def test_input_order_invariance():
    reference = summarize_availability_by_group(OBSERVATIONS)
    rng = random.Random(20260101)
    for _ in range(25):
        shuffled = OBSERVATIONS[:]
        rng.shuffle(shuffled)
        result = summarize_availability_by_group(shuffled)
        assert result == reference
        assert list(result) == list(reference)
        assert repr(result) == repr(reference)
    small = [("b", "AVAILABLE"), ("a", "EMPTY"), ("b", "EMPTY"), ("a", "EMPTY")]
    for permutation in itertools.permutations(small):
        assert summarize_availability_by_group(permutation) == summarize_availability_by_group(small)


def test_group_order_is_canonical_and_deterministic():
    arrival = [("zeta", "EMPTY"), ("Alfa", "EMPTY"), ("10", "EMPTY"), ("alfa", "EMPTY"), ("9", "EMPTY"), ("", "EMPTY")]
    result = summarize_availability_by_group(arrival)
    assert list(result) == sorted(key for key, _ in arrival) == ["", "10", "9", "Alfa", "alfa", "zeta"]
    assert list(summarize_availability_by_group(reversed(arrival))) == list(result)
    assert repr(summarize_availability_by_group(arrival)) == repr(result)


def test_custom_availability_labels_are_counted_without_interpretation():
    result = summarize_availability_by_group([("g", ""), ("g", "desconocido"), ("g", "X-1"), ("g", "desconocido"), ("g", "available")])
    assert dict(result["g"].counts_by_classification) == {"": 1, "X-1": 1, "available": 1, "desconocido": 2}
    assert result["g"].available_proportion == 0.0  # "available" no es la etiqueta exacta


def test_group_keys_are_exact_no_normalization_or_aliasing():
    keys = ["Liga", "liga", " Liga", "Liga ", "LIGA", "39", "039"]
    result = summarize_availability_by_group([(key, "AVAILABLE") for key in keys])
    assert len(result) == len(keys)
    assert set(result) == set(keys)
    assert all(summary.total_observations == 1 for summary in result.values())


def test_composite_groups_are_whatever_key_the_caller_supplies():
    result = summarize_availability_by_group(
        [("liga-a|2025", "AVAILABLE"), ("liga-a|2026", "EMPTY"), ("liga-a|2025", "EMPTY"), ("liga-b|2025", "AVAILABLE")]
    )
    assert list(result) == ["liga-a|2025", "liga-a|2026", "liga-b|2025"]
    assert result["liga-a|2025"].available_proportion == 0.5
    assert result["liga-a|2026"].available_proportion == 0.0


def test_input_is_not_mutated():
    sample = OBSERVATIONS[:]
    summarize_availability_by_group(sample)
    assert sample == OBSERVATIONS
    assert summarize_availability_by_group(tuple(OBSERVATIONS)) == summarize_availability_by_group(OBSERVATIONS)


def test_accepts_one_shot_iterables():
    reference = summarize_availability_by_group(OBSERVATIONS)
    assert summarize_availability_by_group(pair for pair in OBSERVATIONS) == reference
    assert summarize_availability_by_group(zip([g for g, _ in OBSERVATIONS], [c for _, c in OBSERVATIONS])) == reference


def test_only_observed_groups_exist_and_none_is_empty():
    result = summarize_availability_by_group(OBSERVATIONS)
    assert "liga-d" not in result
    assert result.get("liga-d") is None
    assert all(s.total_observations > 0 and s.available_proportion is not None for s in result.values())


def test_result_is_read_only():
    result = summarize_availability_by_group(OBSERVATIONS)
    with pytest.raises(TypeError):
        result["liga-a"] = summarize_availability([])
    with pytest.raises(TypeError):
        del result["liga-a"]


@pytest.mark.parametrize("bad", ["ab", b"ab"])
def test_bare_string_input_is_rejected(bad):
    with pytest.raises(TypeError):
        summarize_availability_by_group(bad)


@pytest.mark.parametrize(
    "bad",
    [
        [("liga-a", "AVAILABLE"), "ab"],
        [["liga-a", "AVAILABLE"]],
        [("liga-a",)],
        [("liga-a", "AVAILABLE", "extra")],
        [{"liga-a", "AVAILABLE"}],
        [None],
    ],
)
def test_observations_that_are_not_pairs_are_rejected(bad):
    with pytest.raises(TypeError):
        summarize_availability_by_group(bad)


@pytest.mark.parametrize("bad", [[(1, "AVAILABLE")], [(None, "AVAILABLE")], [(("liga-a", "2025"), "AVAILABLE")], [(b"liga-a", "AVAILABLE")]])
def test_non_str_group_keys_are_rejected(bad):
    with pytest.raises(TypeError):
        summarize_availability_by_group(bad)


@pytest.mark.parametrize("bad", [[("liga-a", None)], [("liga-a", 1)], [("liga-a", b"AVAILABLE")]])
def test_non_str_classifications_are_rejected(bad):
    with pytest.raises(TypeError):
        summarize_availability_by_group(bad)
