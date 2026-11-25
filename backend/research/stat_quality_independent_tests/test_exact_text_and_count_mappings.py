"""Regresión: etiquetas y claves cuentan por su texto exacto, y los mappings de conteos se rechazan.
Datos sintéticos, sin BD ni app.*.
Se ejecuta por ruta explícita: python -m pytest research/stat_quality_independent_tests -q"""

import itertools
from enum import StrEnum

from research.stat_quality_independent.availability import summarize_availability
from research.stat_quality_independent.grouped_availability import summarize_availability_by_group


class Status(StrEnum):
    """Misma forma que el productor real de estas etiquetas (StatisticsAsOfStatus, app/schemas/statistics_knowledge.py)."""

    AVAILABLE = "AVAILABLE"
    EMPTY = "EMPTY"


class League(StrEnum):
    A = "liga-a"
    B = "liga-b"


class CaseFolded(str):
    """Subclase de str cuya igualdad no es exacta: CaseFolded("AVAILABLE") == "available"."""

    def __eq__(self, other):
        return isinstance(other, str) and self.casefold() == other.casefold()

    def __hash__(self):
        return hash(self.casefold())


def _exact_str_keys(mapping):
    return all(type(key) is str for key in mapping)


def test_enum_and_plain_labels_give_one_result_whatever_the_arrival_order():
    reference = summarize_availability(["AVAILABLE", "AVAILABLE", "EMPTY", "EMPTY"])
    for permutation in itertools.permutations([Status.AVAILABLE, "AVAILABLE", Status.EMPTY, "EMPTY"]):
        summary = summarize_availability(permutation)
        assert repr(summary) == repr(reference)
        assert _exact_str_keys(summary.counts_by_classification) and _exact_str_keys(summary.relative_frequencies)

    grouped_reference = summarize_availability_by_group([("liga-a", "AVAILABLE"), ("liga-a", "AVAILABLE"), ("liga-b", "EMPTY"), ("liga-b", "EMPTY")])
    pairs = [(League.A, Status.AVAILABLE), ("liga-a", "AVAILABLE"), ("liga-b", Status.EMPTY), (League.B, "EMPTY")]
    for permutation in itertools.permutations(pairs):
        result = summarize_availability_by_group(permutation)
        assert repr(result) == repr(grouped_reference)
        assert _exact_str_keys(result)
        assert all(_exact_str_keys(summary.counts_by_classification) for summary in result.values())


def test_labels_and_group_keys_count_by_their_exact_text_not_by_a_subclass_equality():
    summary = summarize_availability([CaseFolded("AVAILABLE"), "available", "EMPTY"])
    assert dict(summary.counts_by_classification) == {"AVAILABLE": 1, "EMPTY": 1, "available": 1}
    assert summary.available_proportion == summary.relative_frequencies["AVAILABLE"] == 1 / 3

    result = summarize_availability_by_group([(CaseFolded("Liga"), "AVAILABLE"), ("liga", CaseFolded("available"))])
    assert list(result) == ["Liga", "liga"]
    assert result["Liga"].available_proportion == 1.0
    assert result["liga"].available_proportion == 0.0  # "available" no es la etiqueta exacta
