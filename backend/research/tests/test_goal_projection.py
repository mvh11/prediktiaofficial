"""MOD-DEV-066-R1: proyección de una distribución conjunta categórica 8x8 de goles a 90' en
probabilidades Over/Under y BTTS. Puro: sin BD, sin red, sin app.
    python -m pytest research/tests/test_goal_projection.py -q   (desde backend/)
Los valores esperados están calculados a mano (fracciones diádicas exactas o fórmulas cerradas),
nunca con la función que se prueba."""

import copy
import math
import os
import subprocess
import sys
from decimal import Decimal

import pytest

from research.m58.goal_labels import BTTS_NO, BTTS_YES, OVER, PUSH, SUPPORTED_LINE_TEXTS, UNDER, label_btts, label_over_under
from research.m58.goal_projection import (CELLS, GOAL_CATEGORIES, TAIL, TOLERANCE, btts_probabilities, goals_market_probabilities,
                                          over_under_probabilities, validated_cells)
from research.m58.labels import label_1x2
from research.m58d.labels import label_1x2_v2

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATS = (0, 1, 2, 3, 4, 5, 6, "7+")  # escrito a mano: el contrato aprobado


def dist(weights: dict) -> list:
    """Las 64 entradas explícitas (local, visitante, p) en orden canónico; las celdas no nombradas, con 0."""
    assert set(weights) <= {(h, a) for h in CATS for a in CATS}
    return [(h, a, weights.get((h, a), 0.0)) for h in CATS for a in CATS]


def ou(result, line):
    return result["over_under"][line]


# --- A. Contrato de la partición -------------------------------------------------------------------

def test_the_closed_8x8_partition():
    assert GOAL_CATEGORIES == CATS and TAIL == "7+" and TOLERANCE == 1e-9
    assert len(CELLS) == 64 and len(set(CELLS)) == 64 and CELLS[0] == (0, 0) and CELLS[-1] == ("7+", "7+") and CELLS[8] == (1, 0)
    ordered = validated_cells(dist({(0, 0): 1.0}))
    assert len(ordered) == 64 and [cell for cell, _ in ordered] == list(CELLS)


def test_zero_probability_cells_are_required_and_never_filled_in():
    full = dist({(0, 0): 1.0})
    assert validated_cells(full)  # 63 ceros explícitos: válido
    with pytest.raises(ValueError, match="faltan 63 celdas"):
        validated_cells([(0, 0, 1.0)])  # la masa es 1, pero faltan celdas: no se rellenan


@pytest.mark.parametrize("drop", [[(0, 0)], [("7+", "7+")], [(3, 2), (6, "7+"), ("7+", 0)]], ids=["first", "tail", "several"])
def test_missing_cells_are_rejected(drop):
    weights = {(1, 1): 0.5, (2, 2): 0.5}
    entries = [e for e in dist(weights) if (e[0], e[1]) not in drop]
    with pytest.raises(ValueError, match=f"faltan {len(drop)} celdas"):
        validated_cells(entries)


def test_a_duplicate_cell_is_rejected_even_when_it_adds_no_mass():
    entries = dist({(1, 1): 1.0}) + [(4, 4, 0.0)]
    with pytest.raises(ValueError, match="celda repetida"):
        validated_cells(entries)
    with pytest.raises(ValueError, match="celda repetida"):
        validated_cells(dist({(1, 1): 0.5}) + [(1, 1, 0.5)])  # nunca se suman probabilidades repetidas


@pytest.mark.parametrize("bad", [7, 8, -1, "7", "7 +", "7+ ", "+7", True, False, 1.0, None, "0", Decimal("1")], ids=repr)
def test_invalid_goal_categories_are_rejected_in_either_side(bad):
    for index in (0, 1):
        entries = dist({(0, 0): 1.0})
        row = list(entries[5])
        row[index] = bad
        entries[5] = tuple(row)
        with pytest.raises(ValueError, match="categoría de goles"):
            validated_cells(entries)


@pytest.mark.parametrize("bad", [(0, 0), (0, 0, 0.5, 0.5), {"home": 0, "away": 0, "p": 1.0}, "0,0,1", None, 0.0], ids=repr)
def test_malformed_entries_are_rejected(bad):
    entries = dist({(1, 0): 1.0})
    entries[0] = bad
    with pytest.raises(ValueError, match="entrada no válida"):
        validated_cells(entries)


@pytest.mark.parametrize("container", [lambda e: {(h, a): p for h, a, p in e}, lambda e: iter(e), lambda e: None], ids=["dict", "iterator", "none"])
def test_only_a_list_or_tuple_of_entries_is_accepted(container):
    with pytest.raises(ValueError, match="lista o tupla"):
        validated_cells(container(dist({(0, 0): 1.0})))


# --- B. Probabilidades ----------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, True, False, "0.5", Decimal("0.5"), math.nan, math.inf, -math.inf, -0.1, -1e-300, 1.5, 1 + 1e-12],
                         ids=repr)
def test_invalid_probabilities_are_rejected(bad):
    entries = dist({(1, 1): 1.0})
    entries[3] = (entries[3][0], entries[3][1], bad)
    with pytest.raises(ValueError, match="probabilidad no válida"):
        validated_cells(entries)


def test_integer_probabilities_are_accepted():
    assert goals_market_probabilities(dist({(2, 1): 1}))["btts"] == {BTTS_YES: 1.0, BTTS_NO: 0.0}


@pytest.mark.parametrize("second, accepted", [(0.5 + 5e-10, True), (0.5 - 5e-10, True), (0.5 + 2e-9, False), (0.5 - 2e-9, False),
                                              (0.4, False), (0.25, False)], ids=["over-inside", "under-inside", "over-outside",
                                                                                 "under-outside", "under-normalized", "far-under"])
def test_mass_tolerance_without_renormalization(second, accepted):
    entries = dist({(0, 0): 0.5, (1, 1): second})
    if not accepted:
        with pytest.raises(ValueError, match="masa total"):
            goals_market_probabilities(entries)
        return
    result = goals_market_probabilities(entries)
    assert result["mass"] == 0.5 + second  # la masa aceptada, no 1
    assert result["btts"] == {BTTS_YES: second, BTTS_NO: 0.5}  # sin reescalar
    assert ou(result, "2.0") == {OVER: 0.0, UNDER: 0.5, PUSH: second}


def test_over_normalized_mass_is_rejected():
    with pytest.raises(ValueError, match="masa total"):
        goals_market_probabilities(dist({(0, 0): 0.75, (1, 1): 0.75}))


# --- C/D. Toda la masa en una celda ---------------------------------------------------------------

def test_all_mass_on_nil_nil():
    result = goals_market_probabilities(dist({(0, 0): 1.0}))
    assert result["btts"] == {BTTS_YES: 0.0, BTTS_NO: 1.0}
    for line in SUPPORTED_LINE_TEXTS:
        assert ou(result, line) == {OVER: 0.0, UNDER: 1.0, PUSH: 0.0}


def test_all_mass_on_the_tail_cell():
    result = goals_market_probabilities(dist({("7+", "7+"): 1.0}))
    assert result["btts"] == {BTTS_YES: 1.0, BTTS_NO: 0.0}
    for line in SUPPORTED_LINE_TEXTS:
        assert ou(result, line) == {OVER: 1.0, UNDER: 0.0, PUSH: 0.0}


@pytest.mark.parametrize("cell", [("7+", 0), (0, "7+"), ("7+", 3)])
def test_a_seven_plus_side_is_over_on_every_line_and_never_an_exact_seven(cell):
    result = goals_market_probabilities(dist({cell: 1.0}))
    for line in SUPPORTED_LINE_TEXTS:
        assert ou(result, line)[OVER] == 1.0
    assert result["btts"][BTTS_YES] == (1.0 if 0 not in cell else 0.0)


# --- E/F. Distribuciones diádicas con resultado exacto --------------------------------------------

PUSH_DIST = {(0, 0): 0.25, (1, 1): 0.25, (2, 1): 0.5}  # totales 0, 2 y 3
PUSH_EXPECTED = {  # a mano: total < línea -> UNDER, = -> PUSH, > -> OVER
    "0.5": (0.75, 0.25, 0.0), "1.0": (0.75, 0.25, 0.0), "1.5": (0.75, 0.25, 0.0), "2.0": (0.5, 0.25, 0.25),
    "2.5": (0.5, 0.5, 0.0), "3.0": (0.0, 0.5, 0.5), "3.5": (0.0, 1.0, 0.0), "4.0": (0.0, 1.0, 0.0), "4.5": (0.0, 1.0, 0.0),
    "5.0": (0.0, 1.0, 0.0), "5.5": (0.0, 1.0, 0.0), "6.0": (0.0, 1.0, 0.0), "6.5": (0.0, 1.0, 0.0),
}


def test_over_under_and_push_partitions_on_every_line():
    result = goals_market_probabilities(dist(PUSH_DIST))
    assert set(result["over_under"]) == set(SUPPORTED_LINE_TEXTS) == set(PUSH_EXPECTED)
    for line, (over, under, push) in PUSH_EXPECTED.items():
        assert ou(result, line) == {OVER: over, UNDER: under, PUSH: push}
        if line.endswith(".5"):
            assert ou(result, line)[PUSH] == 0.0
    assert result["btts"] == {BTTS_YES: 0.75, BTTS_NO: 0.25}


BTTS_DIST = {(0, 0): 0.125, (1, 0): 0.125, (0, 1): 0.125, (1, 1): 0.125, ("7+", 0): 0.125, (0, "7+"): 0.125, ("7+", "7+"): 0.125,
             (3, 2): 0.125}


def test_btts_and_tail_cells():
    result = goals_market_probabilities(dist(BTTS_DIST))
    assert result["btts"] == {BTTS_YES: 0.375, BTTS_NO: 0.625}  # 1-1, 7+/7+ y 3-2
    assert ou(result, "6.5") == {OVER: 0.375, UNDER: 0.625, PUSH: 0.0}  # las tres celdas con 7+
    assert ou(result, "5.0") == {OVER: 0.375, UNDER: 0.5, PUSH: 0.125}  # 3-2 es push
    assert ou(result, "1.0") == {OVER: 0.625, UNDER: 0.125, PUSH: 0.25}  # 1-0 y 0-1 son push; 0-0 under


def test_projection_agrees_with_the_settlement_labels_on_every_exact_cell():
    for home in range(7):
        for away in range(7):
            result = goals_market_probabilities(dist({(home, away): 1.0}))
            assert result["btts"][label_btts("FT", home, away)[0]] == 1.0
            for line in SUPPORTED_LINE_TEXTS:
                assert ou(result, line)[label_over_under("FT", home, away, line)[0]] == 1.0


# --- G. Distribución completa no uniforme ---------------------------------------------------------

# p(h, a) = (i + 1)(j + 1) / 1296 con i, j la posición de la categoría (0..7): las 64 celdas son > 0 y
# suman (1 + ... + 8)^2 / 1296 = 1. Para celdas exactas con total s <= 6, la suma de (h + 1)(s − h + 1) es
# C(s + 3, 3): 1, 4, 10, 20, 35, 56, 84 para s = 0..6; todo lo demás (totales >= 7 y celdas con 7+) es
# OVER en esas líneas. BTTS NO = fila 0 + columna 0 − (0, 0) = 36 + 36 − 1 = 71.
PRODUCT = {(h, a): (i + 1) * (j + 1) / 1296 for i, h in enumerate(CATS) for j, a in enumerate(CATS)}
PRODUCT_EXPECTED = {  # (OVER, UNDER, PUSH) en unidades de 1/1296
    "0.5": (1295, 1, 0), "1.0": (1291, 1, 4), "2.0": (1281, 5, 10), "2.5": (1281, 15, 0),
    "3.0": (1261, 15, 20), "6.0": (1086, 126, 84), "6.5": (1086, 210, 0),
}


def test_a_complete_non_uniform_distribution():
    result = goals_market_probabilities(dist(PRODUCT))
    assert abs(result["mass"] - 1.0) <= 1e-12
    for line, (over, under, push) in PRODUCT_EXPECTED.items():
        got = ou(result, line)
        assert all(abs(got[k] - n / 1296) <= 1e-12 for k, n in ((OVER, over), (UNDER, under), (PUSH, push))), line
    assert abs(result["btts"][BTTS_NO] - 71 / 1296) <= 1e-12 and abs(result["btts"][BTTS_YES] - 1225 / 1296) <= 1e-12


# --- H. Líneas ------------------------------------------------------------------------------------

def test_every_approved_line_alone_matches_the_full_projection():
    entries = dist(PUSH_DIST)
    full = goals_market_probabilities(entries)
    for line in SUPPORTED_LINE_TEXTS:
        assert over_under_probabilities(entries, line) == ou(full, line) == over_under_probabilities(entries, Decimal(line))
    assert btts_probabilities(entries) == full["btts"]


@pytest.mark.parametrize("line", ["0.0", "0", "7.0", "7.5", "2.25", "2.50", " 2.5", Decimal("7"), Decimal("2.49"), 2.5, 2, True, None,
                                  Decimal("NaN"), Decimal("Infinity")], ids=repr)
def test_unapproved_or_invalid_lines_raise_before_reading_the_distribution(line):
    with pytest.raises(ValueError, match="línea Over/Under"):
        over_under_probabilities("ni siquiera una distribución", line)


# --- D/I. Conservación, determinismo e inmutabilidad -----------------------------------------------

@pytest.mark.parametrize("weights", [PUSH_DIST, BTTS_DIST, PRODUCT, {(0, 0): 1.0}, {("7+", "7+"): 1.0}], ids=["push", "btts", "product", "nil", "tail"])
def test_every_partition_is_finite_non_negative_bounded_and_conserved(weights):
    result = goals_market_probabilities(dist(weights))
    for probs in list(result["over_under"].values()) + [result["btts"]]:
        assert all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in probs.values())
        total = math.fsum(probs.values())
        assert abs(total - 1.0) <= 1e-9 and abs(total - result["mass"]) <= 1e-9


def test_order_of_entries_does_not_matter_and_input_is_not_mutated():
    entries = dist(PRODUCT)
    snapshot = copy.deepcopy(entries)
    first = goals_market_probabilities(entries)
    assert goals_market_probabilities(list(reversed(entries))) == first == goals_market_probabilities(tuple(entries))
    assert goals_market_probabilities(entries[1::2] + entries[0::2]) == first
    assert entries == snapshot


# --- J. Compatibilidad ----------------------------------------------------------------------------

def test_labels_are_unchanged():
    assert label_over_under("AET", 1, 1, "2.0") == (PUSH, None) and label_btts("PEN", 0, 0) == (BTTS_NO, None)
    assert label_1x2("AET", 2, 1) == (None, "extra_time_not_draw_at_90") and label_1x2_v2("AET", 2, 1) == ("H", None)


def test_the_module_imports_without_the_app_or_a_database():
    code = ("import sys; import research.m58.goal_projection; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'scipy')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"
