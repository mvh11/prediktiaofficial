"""MOD-DEV-069: distribución de referencia Poisson independiente en la partición 8x8 de goles a 90'.
Puro: sin BD, sin red, sin app.
    python -m pytest research/tests/test_goal_reference.py -q   (desde backend/)
Las referencias se calculan de forma independiente: fórmulas cerradas con math.exp y una Poisson en
Decimal con 60 dígitos (1 − Σ sin cancelación a esa precisión), nunca con el código probado.
MOD-DEV-072-R2: representación conservadora (cada float <= su probabilidad); referencia independiente en
Decimal con 80 dígitos y comparación exacta con Fraction."""

import copy
import math
import os
import subprocess
import sys
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research.m58.goal_labels import BTTS_NO, BTTS_YES, OVER, PUSH, SUPPORTED_LINE_TEXTS, UNDER
from research.m58.goal_projection import CELLS, goals_market_probabilities, validated_cells
from research.m58.goal_reference import independent_poisson_reference, poisson_categories

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATS = (0, 1, 2, 3, 4, 5, 6, "7+")


def ref_pmf(lam, k) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        rate = Decimal(lam)
        return (-rate).exp() * rate ** k / math.factorial(k)


def ref_marginal(lam) -> list[Decimal]:
    with localcontext() as ctx:
        ctx.prec = 60
        exact = [ref_pmf(lam, k) for k in range(7)]
        return exact + [1 - sum(exact)]


def ref_cdf(mu, upto) -> float:
    with localcontext() as ctx:
        ctx.prec = 60
        return float(sum(ref_pmf(mu, k) for k in range(upto + 1)))


def rel_close(got, expected, rel):
    expected = float(expected)
    return got == expected if expected == 0 else abs(got - expected) <= rel * abs(expected)


def cell(cells, home, away):
    return next(p for h, a, p in cells if (h, a) == (home, away))


# --- A/B. Tasas nulas -----------------------------------------------------------------------------

def test_zero_rates_put_all_mass_on_nil_nil():
    cells = independent_poisson_reference(0, 0.0)
    assert [(h, a) for h, a, _ in cells] == list(CELLS)
    assert [p for _, _, p in cells] == [1.0] + [0.0] * 63
    result = goals_market_probabilities(cells)
    assert result["btts"] == {BTTS_YES: 0.0, BTTS_NO: 1.0}
    assert all(result["over_under"][line] == {OVER: 0.0, UNDER: 1.0, PUSH: 0.0} for line in SUPPORTED_LINE_TEXTS)


@pytest.mark.parametrize("home, away", [(0, 1.3), (2.7, 0)])
def test_a_zero_rate_side_never_scores(home, away):
    cells = independent_poisson_reference(home, away)
    zero_side = 0 if home == 0 else 1
    assert all(p == 0.0 for h, a, p in cells if (h, a)[zero_side] != 0)
    assert goals_market_probabilities(cells)["btts"][BTTS_YES] == 0.0


# --- C/D. Marginales y cola -----------------------------------------------------------------------

@pytest.mark.parametrize("lam", [1, 1.0, 2.5])
def test_first_poisson_terms(lam):
    p = poisson_categories(lam)
    e = math.exp(-lam)
    assert rel_close(p[0], e, 1e-15) and rel_close(p[1], lam * e, 1e-15) and rel_close(p[2], lam ** 2 * e / 2, 1e-15)


@pytest.mark.parametrize("lam", [1e-300, 1e-6, 0.001, 0.05, 0.5, 1.0, 3.0, 7.0, 7.999, 8.0, 8.001, 12.0, 30.0])
def test_every_category_matches_a_60_digit_reference(lam):
    got, ref = poisson_categories(lam), ref_marginal(lam)
    for k in range(8):
        assert rel_close(got[k], ref[k], 1e-12), (lam, k, got[k], ref[k])


@pytest.mark.parametrize("lam", [1e-6, 0.001, 0.01])  # colas ~1e-46, ~1e-25 y ~1e-18: por debajo del redondeo de 1 − Σ
def test_a_tiny_tail_is_not_lost_to_cancellation(lam):
    got, ref = poisson_categories(lam)[7], float(ref_marginal(lam)[7])
    naive = 1.0 - math.fsum(poisson_categories(lam)[:7])
    assert 0 < got and rel_close(got, ref, 1e-12)
    assert not rel_close(naive, ref, 1e-3)  # 1 − Σ en float pierde la cola: por eso la serie


@pytest.mark.parametrize("lam", [0.3, 4.0, 9.5, 50.0])
def test_each_marginal_conserves_its_mass(lam):
    p = poisson_categories(lam)
    assert len(p) == 8 and all(math.isfinite(x) and 0 <= x <= 1 for x in p) and abs(math.fsum(p) - 1) <= 1e-12


# --- E/F/G. Conjunta, soporte y conservación ------------------------------------------------------

def test_joint_cells_are_products_of_the_reference_marginals():
    lh, la = 1.4, 0.9
    cells = independent_poisson_reference(lh, la)
    mh, ma = ref_marginal(lh), ref_marginal(la)
    for i, h in enumerate(CATS):
        for j, a in enumerate(CATS):
            assert rel_close(cell(cells, h, a), mh[i] * ma[j], 1e-12), (h, a)


def test_complete_canonical_support_and_conservation():
    cells = independent_poisson_reference(1.4, 0.9)
    assert len(cells) == 64 and [(h, a) for h, a, _ in cells] == list(CELLS) and len({(h, a) for h, a, _ in cells}) == 64
    assert all(type(p) is float and math.isfinite(p) and 0 <= p <= 1 for _, _, p in cells)
    assert abs(math.fsum(p for _, _, p in cells) - 1) <= 1e-12
    assert len(validated_cells(cells)) == 64  # el validador congelado de MOD-DEV-066-R1, sin cambios


# --- H/I. Proyección ------------------------------------------------------------------------------

def test_over_under_projection_matches_the_poisson_total():
    lh, la = 1.4, 1.1
    mu = lh + la  # suma de Poisson independientes: Poisson(lh + la) sobre las celdas exactas
    result = goals_market_probabilities(independent_poisson_reference(lh, la))
    ou = result["over_under"]
    assert ou["2.5"][PUSH] == 0.0 and abs(ou["2.5"][UNDER] - ref_cdf(mu, 2)) <= 1e-13
    assert abs(ou["2.0"][PUSH] - (ref_cdf(mu, 2) - ref_cdf(mu, 1))) <= 1e-13 and ou["2.0"][PUSH] > 0
    assert abs(ou["2.0"][UNDER] - ref_cdf(mu, 1)) <= 1e-13
    assert abs(ou["6.5"][UNDER] - ref_cdf(mu, 6)) <= 1e-13 and abs(ou["6.0"][PUSH] - (ref_cdf(mu, 6) - ref_cdf(mu, 5))) <= 1e-13
    for line in SUPPORTED_LINE_TEXTS:
        probs = ou[line]
        assert abs(math.fsum(probs.values()) - 1) <= 1e-9 and (probs[PUSH] == 0.0) is line.endswith(".5")


@pytest.mark.parametrize("lh, la", [(1.4, 1.1), (0.2, 3.5), (6.0, 6.0), (0.001, 0.001)])
def test_btts_matches_the_independent_closed_form(lh, la):
    btts = goals_market_probabilities(independent_poisson_reference(lh, la))["btts"]
    expected = (1 - math.exp(-lh)) * (1 - math.exp(-la))
    assert abs(btts[BTTS_YES] - expected) <= 1e-13 and abs(math.fsum(btts.values()) - 1) <= 1e-9


# --- J. Parámetros no válidos ---------------------------------------------------------------------

@pytest.mark.parametrize("bad", [True, False, None, "1.2", Decimal("1.2"), Fraction(1, 2), 1 + 0j, [1.0], math.nan, math.inf, -math.inf,
                                 -1, -0.5, -1e-300], ids=repr)
def test_invalid_rates_are_rejected_on_either_side(bad):
    with pytest.raises(ValueError, match="lambda_home"):
        independent_poisson_reference(bad, 1.0)
    with pytest.raises(ValueError, match="lambda_away"):
        independent_poisson_reference(1.0, bad)


def test_an_int_beyond_float_range_is_not_representable():
    with pytest.raises(ValueError, match="no es representable"):
        independent_poisson_reference(10 ** 400, 1.0)


# --- K. Fronteras numéricas -----------------------------------------------------------------------

@pytest.mark.parametrize("lam", [5e-324, 1e-300, 1e-12, 700.0, 745.0, 800.0, 1e6, 1e300, sys.float_info.max])
def test_extreme_finite_rates_stay_valid_without_caps_or_clipping(lam):
    cells = independent_poisson_reference(lam, lam)
    result = goals_market_probabilities(cells)  # pasa el validador congelado
    p = poisson_categories(lam)
    assert all(math.isfinite(x) and 0 <= x <= 1 for x in p) and abs(math.fsum(p) - 1) <= 1e-12
    if lam >= 700:
        # P(K >= 7) = 1 − Σ P(K = k) < 1 exactamente: el mayor float que no la supera es 1 − 2^−53, no 1.0
        # (MOD-DEV-069 fijaba 1.0 − fsum(p[:7]) = 1.0, por encima de la probabilidad verdadera; MOD-DEV-072-R2)
        assert p[7] == math.nextafter(1.0, 0.0) and all(result["over_under"][line][OVER] > 1 - 1e-12 for line in SUPPORTED_LINE_TEXTS)
    else:
        assert rel_close(p[0], math.exp(-lam), 1e-15) and result["btts"][BTTS_NO] > 1 - 1e-11


def test_underflow_is_rounding_not_a_cap():
    assert rel_close(poisson_categories(700.0)[6], ref_pmf(700.0, 6), 1e-10)  # ~1e-290: aún representable
    assert poisson_categories(740.0)[0] > 0  # exp(−740) es subnormal, no cero (la recursión desde exp(−λ) lo perdería)
    assert poisson_categories(800.0)[:7] == (0.0,) * 7  # por debajo del menor float: redondeo legítimo a 0
    # P(K = 1) = λ·e^−λ < λ = 5e-324 (el menor subnormal): el mayor float que no la supera es 0. MOD-DEV-069
    # fijaba 5e-324, el más cercano pero por encima de la probabilidad verdadera (MOD-DEV-072-R2)
    assert poisson_categories(5e-324)[:2] == (math.nextafter(1.0, 0.0), 0.0)


# --- MOD-DEV-072-R2. Representación conservadora ---------------------------------------------------

REL = Fraction(1, 10 ** 40)  # holgura para el error de la referencia de 80 dígitos (< 1e-75)
ONE_BELOW = math.nextafter(1.0, 0.0)  # 1 − 2^−53


def hp_marginal(lam) -> list[Fraction]:
    """Referencia independiente con 80 dígitos: e^−λ λ^k / k! y la cola por su serie (λ < 8) o por 1 − Σ."""
    with localcontext() as ctx:
        ctx.prec = 80
        rate = Decimal(lam)
        e = (-rate).exp()  # 0 si e^−λ queda por debajo del rango de Decimal (λ >= ~2,3e6)
        exact = [e * rate ** k / math.factorial(k) for k in range(7)]
        tail = e * sum(rate ** j / math.factorial(j) for j in range(7, 300)) if lam < 8 else 1 - sum(exact)
        return [Fraction(x) for x in exact + [tail]]


@pytest.mark.parametrize("lam", [5e-324, 1e-300, 1e-12, 1e-6, 0.001, 0.01, 0.5, 1.1, 1.7, 2.5, 3.0, 3.5, 7.999, 8.0, 8.001, 50.0, 700.0,
                                 740.0, 1000.0, math.nextafter(1024.0, 0.0), 1024.0, 1e6, 1e300])
def test_each_category_is_the_largest_float_not_above_its_probability(lam):
    got, ref = poisson_categories(lam), hp_marginal(lam)
    for k in range(8):
        assert Fraction(got[k]) <= ref[k] * (1 + REL), (lam, k)  # nunca por encima de la probabilidad
        assert Fraction(math.nextafter(got[k], math.inf)) > ref[k] * (1 - REL), (lam, k)  # y a menos de un ulp
    exact_mass = sum(map(Fraction, got))
    assert 1 - (Fraction(2) ** -52 + Fraction(1, 10 ** 45)) < exact_mass <= 1  # cota de déficit documentada


JOINT_PAIRS = [(0, 0), (0, 1.1), (1.1, 0), (1.4, 0.9), (3.0, 3.0), (1.7, 3.0), (2.5, 50.0), (1.1, 700.0), (1e-12, 0.01),
               (5e-324, 1e-300), (1e300, 3.0), (sys.float_info.max, sys.float_info.max)]


@pytest.mark.parametrize("lh, la", JOINT_PAIRS)
def test_joint_cells_are_the_largest_floats_not_above_the_exact_products(lh, la):
    home, away = poisson_categories(lh), poisson_categories(la)
    cells = independent_poisson_reference(lh, la)
    for (h, a, p), (i, j) in zip(cells, [(i, j) for i in range(8) for j in range(8)]):
        product = Fraction(home[i]) * Fraction(away[j])
        assert Fraction(p) <= product < Fraction(math.nextafter(p, math.inf)), (h, a)
    total = sum(Fraction(p) for _, _, p in cells)
    assert 1 - (3 * Fraction(2) ** -52 + Fraction(1, 10 ** 44)) < total <= 1


# Casos de MOD-DEV-072/R1: masa 1 ulp por encima de 1, 2 ulps, y toda la masa en un resultado (> 1)
@pytest.mark.parametrize("lh, la", [(0, 1.1), (1.4, 0.9), (3.0, 3.0), (2.5, 50.0), (1.1, 700.0)])
def test_previously_failing_rates_project_within_physical_bounds(lh, la):
    cells = independent_poisson_reference(lh, la)
    mass = math.fsum(p for _, _, p in cells)
    assert 1 - 1e-9 <= mass <= 1.0 and len(validated_cells(cells)) == 64
    result = goals_market_probabilities(cells)
    assert result["mass"] == mass
    for line in SUPPORTED_LINE_TEXTS:
        probs = result["over_under"][line]
        assert all(0.0 <= p <= 1.0 for p in probs.values()) and (probs[PUSH] == 0.0) is line.endswith(".5")
        assert abs(math.fsum(probs.values()) - 1) <= 1e-9 and abs(math.fsum(probs.values()) - mass) <= 1e-9
    assert all(0.0 <= p <= 1.0 for p in result["btts"].values()) and abs(math.fsum(result["btts"].values()) - mass) <= 1e-9
    if lh == 0:
        assert result["btts"] == {BTTS_YES: 0.0, BTTS_NO: mass}  # toda la masa en NO: <= 1, sin recorte


def test_an_ordinary_and_extreme_grid_never_exceeds_unit_mass():
    rates = [0, 5e-324, 1e-300, 1e-6, 0.01] + [round(0.15 * i, 2) for i in range(1, 28)] + [7.999, 8.0, 50.0, 745.0, 1e6, sys.float_info.max]
    for lh in rates:
        for la in rates:
            cells = independent_poisson_reference(lh, la)
            assert sum(Fraction(p) for _, _, p in cells) <= 1, (lh, la)
            result = goals_market_probabilities(cells)
            outputs = [p for probs in result["over_under"].values() for p in probs.values()] + list(result["btts"].values())
            assert all(0.0 <= p <= 1.0 for p in outputs), (lh, la)


def test_a_tiny_rate_keeps_p0_below_one_and_p1_positive():
    p = poisson_categories(1e-300)
    # e^−λ < 1 y λ·e^−λ < λ: el float 1e-300 ya supera P(K = 1), así que se usa el anterior
    assert p[0] == ONE_BELOW and p[1] == math.nextafter(1e-300, 0.0) and p[2:] == (0.0,) * 6
    assert sum(map(Fraction, p)) <= 1  # MOD-DEV-069 daba (1.0, 1e-300, ...): suma exacta > 1


def test_seven_plus_is_a_tail_not_an_exact_seven():
    for lam in (0.5, 3.0, 7.999, 8.0, 12.0):
        p, ref = poisson_categories(lam), hp_marginal(lam)
        with localcontext() as ctx:
            ctx.prec = 80
            exactly_seven = Fraction((-Decimal(lam)).exp() * Decimal(lam) ** 7 / 5040)
        assert Fraction(p[7]) > exactly_seven and abs(Fraction(p[7]) - ref[7]) <= ref[7] * Fraction(2) ** -52


def test_the_analytic_branch_agrees_with_the_decimal_branch_at_its_threshold():
    assert poisson_categories(math.nextafter(1024.0, 0.0)) == poisson_categories(1024.0) == (0.0,) * 7 + (ONE_BELOW,)
    with localcontext() as ctx:
        ctx.prec = 60
        bound = (-Decimal(1024)).exp() * Decimal(1024) ** 6  # cota de P(K = k), k <= 6, en λ = 1024 (decrece después)
    assert Fraction(bound) < Fraction(1, 2 ** 1100)


def test_the_mass_postcondition_fails_closed(monkeypatch):
    from research.m58 import goal_reference
    monkeypatch.setattr(goal_reference, "_round_down", float)  # redondeo al más cercano: la representación anterior
    with pytest.raises(ValueError, match="no conserva la masa"):
        goal_reference.independent_poisson_reference(3.0, 3.0)


# --- L/M. Determinismo y compatibilidad -----------------------------------------------------------

def test_repeated_calls_are_identical_and_independent():
    first = independent_poisson_reference(1.7, 0.8)
    snapshot = copy.deepcopy(first)
    first[0] = ("x", "y", 9.0)  # la salida es una lista nueva en cada llamada
    assert independent_poisson_reference(1.7, 0.8) == snapshot == independent_poisson_reference(1.7, 0.8)
    assert poisson_categories(3) == poisson_categories(3.0)


def test_the_module_imports_without_the_app_or_a_database():
    code = ("import sys; import research.m58.goal_reference; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'scipy')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"
