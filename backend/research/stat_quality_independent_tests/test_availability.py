"""Resumen agregado de disponibilidad: datos sintéticos, sin BD ni app.*.
Se ejecuta por ruta explícita: python -m pytest research/stat_quality_independent_tests -q"""

import itertools
import math
import random

import pytest

from research.stat_quality_independent.availability import AVAILABLE, AvailabilitySummary, summarize_availability

MIXED = ["AVAILABLE", "EMPTY", "AVAILABLE", "PARTIAL", "AVAILABLE", "EMPTY", "NOT_REQUESTED", "AVAILABLE"]


def test_empty_input():
    for empty in ([], (), iter(())):
        s = summarize_availability(empty)
        assert s.total_observations == 0
        assert dict(s.counts_by_classification) == {}
        assert dict(s.relative_frequencies) == {}
        assert s.available_proportion is None


def test_single_observation():
    s = summarize_availability(["AVAILABLE"])
    assert s.total_observations == 1
    assert dict(s.counts_by_classification) == {"AVAILABLE": 1}
    assert dict(s.relative_frequencies) == {"AVAILABLE": 1.0}
    assert s.available_proportion == 1.0

    other = summarize_availability(["EMPTY"])
    assert dict(other.counts_by_classification) == {"EMPTY": 1}
    assert other.available_proportion == 0.0


def test_all_available():
    s = summarize_availability(["AVAILABLE"] * 7)
    assert s.total_observations == 7
    assert dict(s.counts_by_classification) == {"AVAILABLE": 7}
    assert dict(s.relative_frequencies) == {"AVAILABLE": 1.0}
    assert s.available_proportion == 1.0


def test_all_empty():
    s = summarize_availability(["EMPTY"] * 5)
    assert s.total_observations == 5
    assert dict(s.counts_by_classification) == {"EMPTY": 5}
    assert dict(s.relative_frequencies) == {"EMPTY": 1.0}
    assert s.available_proportion == 0.0  # muestra no vacía sin AVAILABLE: 0.0, no None


def test_mixed_classifications():
    s = summarize_availability(MIXED)
    assert s.total_observations == 8
    assert dict(s.counts_by_classification) == {"AVAILABLE": 4, "EMPTY": 2, "NOT_REQUESTED": 1, "PARTIAL": 1}
    assert dict(s.relative_frequencies) == {"AVAILABLE": 0.5, "EMPTY": 0.25, "NOT_REQUESTED": 0.125, "PARTIAL": 0.125}
    assert s.available_proportion == 0.5
    assert s.available_proportion == s.relative_frequencies[AVAILABLE]


def test_input_order_invariance():
    reference = summarize_availability(MIXED)
    rng = random.Random(20260101)
    for _ in range(25):
        shuffled = MIXED[:]
        rng.shuffle(shuffled)
        s = summarize_availability(shuffled)
        assert s == reference
        assert list(s.counts_by_classification) == list(reference.counts_by_classification)
    for permutation in itertools.permutations(["AVAILABLE", "EMPTY", "PARTIAL", "EMPTY"]):
        assert summarize_availability(permutation) == summarize_availability(["AVAILABLE", "EMPTY", "PARTIAL", "EMPTY"])


def test_repeated_observations_are_counted_repeatedly():
    once = summarize_availability(["AVAILABLE", "EMPTY"])
    thrice = summarize_availability(["AVAILABLE", "EMPTY"] * 3)
    assert once.total_observations == 2
    assert thrice.total_observations == 6  # sin deduplicar: cada elemento es una observación
    assert dict(thrice.counts_by_classification) == {"AVAILABLE": 3, "EMPTY": 3}
    assert thrice.relative_frequencies == once.relative_frequencies


def test_deterministic_output():
    first, second = summarize_availability(MIXED), summarize_availability(MIXED)
    assert first == second
    assert repr(first) == repr(second)
    # claves en orden ascendente, sea cual sea el orden de llegada
    assert list(first.counts_by_classification) == sorted(set(MIXED))
    assert list(first.relative_frequencies) == sorted(set(MIXED))
    assert repr(summarize_availability(reversed(MIXED))) == repr(first)


@pytest.mark.parametrize(
    "sample",
    [["AVAILABLE"], ["EMPTY"] * 3, MIXED, ["A", "B", "C"], ["AVAILABLE"] + ["EMPTY"] * 6, [str(i % 7) for i in range(1000)]],
)
def test_frequencies_sum_to_one(sample):
    s = summarize_availability(sample)
    assert math.isclose(sum(s.relative_frequencies.values()), 1.0, rel_tol=0, abs_tol=1e-12)
    assert sum(s.counts_by_classification.values()) == s.total_observations == len(sample)
    assert all(s.relative_frequencies[k] == n / len(sample) for k, n in s.counts_by_classification.items())


def test_input_is_not_mutated():
    sample = MIXED[:]
    summarize_availability(sample)
    assert sample == MIXED
    frozen = tuple(MIXED)
    assert summarize_availability(frozen) == summarize_availability(MIXED)


def test_accepts_one_shot_iterables():
    assert summarize_availability(label for label in MIXED) == summarize_availability(MIXED)


def test_custom_labels_are_counted_without_interpretation():
    s = summarize_availability(["", "desconocido", "X-1", "desconocido"])
    assert dict(s.counts_by_classification) == {"": 1, "X-1": 1, "desconocido": 2}
    assert s.available_proportion == 0.0


def test_labels_are_exact_no_normalization_or_aliasing():
    s = summarize_availability(["AVAILABLE", "available", " AVAILABLE", "Available", "AVAILABLE "])
    assert s.total_observations == 5
    assert all(n == 1 for n in s.counts_by_classification.values())
    assert len(s.counts_by_classification) == 5
    assert s.available_proportion == 0.2  # solo la etiqueta exacta


def test_result_is_immutable():
    s = summarize_availability(MIXED)
    assert isinstance(s, AvailabilitySummary)
    with pytest.raises(AttributeError):
        s.total_observations = 0
    with pytest.raises(TypeError):
        s.counts_by_classification["AVAILABLE"] = 0
    with pytest.raises(TypeError):
        s.relative_frequencies["AVAILABLE"] = 0.0


@pytest.mark.parametrize("bad", ["AVAILABLE", b"AVAILABLE"])
def test_bare_string_input_is_rejected(bad):
    with pytest.raises(TypeError):
        summarize_availability(bad)


@pytest.mark.parametrize("bad", [["AVAILABLE", None], ["AVAILABLE", 1], [b"AVAILABLE"]])
def test_non_str_labels_are_rejected(bad):
    with pytest.raises(TypeError):
        summarize_availability(bad)
