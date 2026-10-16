"""MOD-DEV-064: etiquetas de goles a 90' (Over/Under y BTTS). Puro: sin BD, sin red, sin app.
    python -m pytest research/tests/test_goal_labels.py -q   (desde backend/)
Los resultados esperados están escritos a mano, no derivados de la implementación."""

import copy
import math
import os
import subprocess
import sys
from decimal import Decimal

import pytest

from research.m58.goal_labels import (BTTS_NO, BTTS_YES, FULLTIME_INVALID, OVER, PUSH, SUPPORTED_LINE_TEXTS, SUPPORTED_LINES, UNDER,
                                      goals_at_90, label_btts, label_over_under, over_under_line)
from research.m58.labels import FULLTIME_INCOMPLETE, NOT_FINISHED, label_1x2
from research.m58d.labels import label_1x2_v2

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = Decimal


# --- Líneas aprobadas -----------------------------------------------------------------------------

def test_exactly_the_thirteen_approved_lines():
    assert SUPPORTED_LINE_TEXTS == ("0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0", "5.5", "6.0", "6.5")
    assert SUPPORTED_LINES == tuple(D(n) / 2 for n in range(1, 14))
    for text in SUPPORTED_LINE_TEXTS:
        assert over_under_line(text) == D(text) and over_under_line(D(text)) == D(text)
    assert over_under_line(D("2.50")) == D("2.5") and over_under_line(D("3")) == D("3.0")  # mismo valor exacto: no es redondeo


@pytest.mark.parametrize("line", [D("0"), D("0.0"), D("7.0"), D("7.5"), D("-0.5"), D("2.25"), D("2.75"), D("2.49999"), D("2.500001"),
                                  "0", "7.0", "2.25", "2.50", " 2.5", "2.5 ", "+2.5", "2.5e0", "dos", "", "nan"],
                         ids=lambda v: repr(v))
def test_unapproved_lines_are_rejected_without_rounding(line):
    with pytest.raises(ValueError, match="no aprobada"):
        over_under_line(line)


@pytest.mark.parametrize("line", [2.5, 2.0, 3, True, None, [D("2.5")], b"2.5"], ids=["float", "float-whole", "int", "bool", "none", "list", "bytes"])
def test_lines_of_other_types_are_rejected(line):
    with pytest.raises(ValueError, match="se espera Decimal o texto"):
        over_under_line(line)


@pytest.mark.parametrize("line", [D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity")])
def test_non_finite_lines_are_rejected(line):
    with pytest.raises(ValueError, match="no finita"):
        over_under_line(line)


def test_an_invalid_line_is_an_error_even_for_an_excluded_match():
    with pytest.raises(ValueError):
        label_over_under("NS", None, None, "2.25")  # error del llamador, no una exclusión del partido


# --- Over/Under -----------------------------------------------------------------------------------

# (total de goles a 90', línea) -> resultado, escrito a mano
OU_CASES = [
    (0, "0.5", UNDER), (1, "0.5", OVER),
    (0, "1.0", UNDER), (1, "1.0", PUSH), (2, "1.0", OVER),
    (1, "1.5", UNDER), (2, "1.5", OVER),
    (1, "2.0", UNDER), (2, "2.0", PUSH), (3, "2.0", OVER),
    (2, "2.5", UNDER), (3, "2.5", OVER),
    (2, "3.0", UNDER), (3, "3.0", PUSH), (4, "3.0", OVER),
    (3, "3.5", UNDER), (4, "3.5", OVER),
    (3, "4.0", UNDER), (4, "4.0", PUSH), (5, "4.0", OVER),
    (4, "4.5", UNDER), (5, "4.5", OVER),
    (4, "5.0", UNDER), (5, "5.0", PUSH), (6, "5.0", OVER),
    (5, "5.5", UNDER), (6, "5.5", OVER),
    (5, "6.0", UNDER), (6, "6.0", PUSH), (7, "6.0", OVER),
    (6, "6.5", UNDER), (7, "6.5", OVER), (11, "6.5", OVER),
]


@pytest.mark.parametrize("total, line, expected", OU_CASES, ids=[f"{t}-goals-{l}" for t, l, _ in OU_CASES])
def test_over_under_settlement_on_every_line(total, line, expected):
    home = total // 2
    for status in ("FT", "AET", "PEN"):
        assert label_over_under(status, home, total - home, line) == (expected, None)
        assert label_over_under(status, total - home, home, D(line)) == (expected, None)


def test_every_line_is_covered_by_the_table():
    assert {line for _, line, _ in OU_CASES} == set(SUPPORTED_LINE_TEXTS)
    assert {(line, result) for _, line, result in OU_CASES if result == PUSH} == {(l, PUSH) for l in SUPPORTED_LINE_TEXTS if l.endswith(".0")}


def test_a_push_is_a_valid_result_not_an_exclusion():
    result = label_over_under("FT", 1, 1, "2.0")
    assert result == (PUSH, None) and result[1] is None
    assert all(label_over_under("FT", h, a, line)[0] != PUSH for h in range(6) for a in range(6) for line in SUPPORTED_LINE_TEXTS if line.endswith(".5"))


# --- BTTS -----------------------------------------------------------------------------------------

BTTS_CASES = [((0, 0), BTTS_NO), ((1, 0), BTTS_NO), ((0, 1), BTTS_NO), ((1, 1), BTTS_YES), ((2, 1), BTTS_YES), ((0, 4), BTTS_NO),
              ((4, 0), BTTS_NO), ((1, 3), BTTS_YES), ((5, 2), BTTS_YES), ((0, 7), BTTS_NO)]


@pytest.mark.parametrize("score, expected", BTTS_CASES, ids=[f"{h}-{a}" for (h, a), _ in BTTS_CASES])
def test_btts_at_90_minutes(score, expected):
    for status in ("FT", "AET", "PEN"):
        assert label_btts(status, *score) == (expected, None)


# --- Solo el marcador a 90' -----------------------------------------------------------------------

def test_valid_ft_scores_including_zero_zero_are_settled():
    assert goals_at_90("FT", 2, 0) == ((2, 0), None)  # local gana
    assert goals_at_90("FT", 0, 3) == ((0, 3), None)  # visitante gana
    assert goals_at_90("FT", 2, 2) == ((2, 2), None)  # empate
    assert goals_at_90("FT", 0, 0) == ((0, 0), None)  # 0-0 es un resultado, no una exclusión
    assert goals_at_90("FT", 4, 3) == ((4, 3), None)
    assert label_over_under("FT", 0, 0, "0.5") == (UNDER, None) and label_btts("FT", 0, 0) == (BTTS_NO, None)


def test_aet_and_pen_settle_with_the_90_minute_pair_only():
    # AET 1-1 a los 90' y 3-1 tras la prórroga; PEN 0-0 a los 90' y 4-3 en penaltis: solo cuenta el 90'
    assert label_over_under("AET", 1, 1, "2.5") == (UNDER, None) and label_over_under("AET", 1, 1, "2.0") == (PUSH, None)
    assert label_btts("AET", 1, 1) == (BTTS_YES, None)
    assert label_over_under("PEN", 0, 0, "0.5") == (UNDER, None) and label_btts("PEN", 0, 0) == (BTTS_NO, None)
    # un AET que no es empate a los 90' (eliminatoria a doble partido) se liquida igual, como en M5.8D
    assert label_over_under("AET", 2, 1, "2.5") == (OVER, None) and label_btts("AET", 2, 1) == (BTTS_YES, None)
    for status in ("AET", "PEN"):
        assert label_over_under(status, None, None, "2.5") == (None, FULLTIME_INCOMPLETE)
        assert label_btts(status, 1, None) == (None, FULLTIME_INCOMPLETE)


# --- Exclusiones del partido ----------------------------------------------------------------------

EXCLUSIONS = [
    ("NS", None, None, NOT_FINISHED), ("1H", 1, 0, NOT_FINISHED), ("HT", 1, 0, NOT_FINISHED), ("PST", None, None, NOT_FINISHED),
    ("CANC", None, None, NOT_FINISHED), ("ABD", 1, 0, NOT_FINISHED), ("AWD", 3, 0, NOT_FINISHED), ("WO", 3, 0, NOT_FINISHED),
    ("ft", 1, 0, NOT_FINISHED), ("FT ", 1, 0, NOT_FINISHED), ("", 1, 0, NOT_FINISHED), (None, 1, 0, NOT_FINISHED),
    (["FT"], 1, 0, NOT_FINISHED),
    ("FT", None, 1, FULLTIME_INCOMPLETE), ("FT", 1, None, FULLTIME_INCOMPLETE), ("FT", None, None, FULLTIME_INCOMPLETE),
    ("FT", -1, 0, FULLTIME_INVALID), ("FT", 0, -2, FULLTIME_INVALID), ("FT", 1.0, 0, FULLTIME_INVALID), ("FT", 1, 0.5, FULLTIME_INVALID),
    ("FT", True, 0, FULLTIME_INVALID), ("FT", 1, False, FULLTIME_INVALID), ("FT", "1", 0, FULLTIME_INVALID),
    ("FT", D("1"), 0, FULLTIME_INVALID), ("FT", 1, math.nan, FULLTIME_INVALID), ("PEN", -1, 1, FULLTIME_INVALID),
]


@pytest.mark.parametrize("status, home, away, reason", EXCLUSIONS, ids=[f"{s!r}-{h!r}-{a!r}" for s, h, a, _ in EXCLUSIONS])
def test_invalid_matches_are_excluded_never_settled(status, home, away, reason):
    assert goals_at_90(status, home, away) == (None, reason)
    assert label_btts(status, home, away) == (None, reason)  # nunca un NO inventado
    for line in SUPPORTED_LINE_TEXTS:
        assert label_over_under(status, home, away, line) == (None, reason)  # nunca un UNDER inventado


# --- Compatibilidad -------------------------------------------------------------------------------

def test_the_1x2_labels_are_unchanged_and_share_the_status_and_missing_score_rules():
    assert label_1x2("AET", 2, 1) == (None, "extra_time_not_draw_at_90") and label_1x2_v2("AET", 2, 1) == ("H", None)
    for status, home, away in [("NS", None, None), ("AWD", 3, 0), ("WO", 3, 0), ("FT", None, 1), ("AET", 1, None)]:
        assert label_1x2_v2(status, home, away)[1] == goals_at_90(status, home, away)[1] == label_btts(status, home, away)[1]


def test_inputs_are_not_mutated_and_calls_are_deterministic():
    line, home, away = D("2.5"), 2, 1
    args = copy.deepcopy((line, home, away))
    first = (label_over_under("FT", home, away, line), label_btts("FT", home, away))
    assert all(first == (label_over_under("FT", home, away, line), label_btts("FT", home, away)) for _ in range(5))
    assert (line, home, away) == args and str(line) == "2.5"


def test_the_module_imports_without_the_app_or_a_database():
    code = ("import sys; import research.m58.goal_labels; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"
