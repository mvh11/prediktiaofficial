"""Distribución de REFERENCIA de goles a 90': Poisson independientes en la partición 8x8 (puro).

Solo una línea base matemática (MOD-DEV-069): goles local ~ Poisson(lambda_home) y visitante ~
Poisson(lambda_away), independientes SOLO para construir esta referencia. No es el modelo de goles
de Prediktia, no ajusta ni elige lambdas y no afirma que los goles reales sean independientes.

Por equipo: P(K = k) = exp(−λ) λ^k / k! para k = 0..6 y P(K >= 7) para la categoría "7+" (siete o
más, nunca exactamente 7). Con λ = 0 toda la masa está en 0. Celda conjunta = producto de las dos
marginales; salida = las 64 entradas (local, visitante, p) en el orden canónico de
research.m58.goal_projection, que pasan su validated_cells (1 − 1e-9 <= M <= 1).

Representación conservadora (MOD-DEV-072-R2). Cada float devuelto es <= la probabilidad matemática
que representa, así que ninguna suma (marginal, conjunta o la de un resultado de mercado) supera 1;
no hay recortes, épsilons ad hoc, renormalización, restas de masa ni tope de λ:
1. Cotas racionales de exp(−λ): E = Decimal.exp(−λ) con DIGITS cifras (λ convertido a Decimal sin
   pérdida; Decimal.exp está documentado como correctamente redondeado, error <= 1/2 ulp, es decir
   |E − e^−λ| <= 5·10^−DIGITS·E). Se usa un margen 200 veces mayor, 10^(3−DIGITS):
   E·(1 − 10^(3−DIGITS)) <= e^−λ <= E·(1 + 10^(3−DIGITS)), válido aunque el error fuese de varios ulps.
   Para 0 < λ < 1024, e^−λ > 10^−445 es un Decimal normal (sin underflow; se atrapa por si acaso).
2. Todo lo demás es aritmética exacta con Fraction (λ es un float, un racional exacto):
   - k = 0..6: inferior_k = E_inf · λ^k / k!  (<= P(K = k));
   - "7+" con λ < 8: inferior = E_inf · Σ_{j=7..J} λ^j / j!, serie de términos positivos truncada (cota
     inferior); se corta en un término t_J <= 10^−DIGITS de la suma con λ/(J + 1) <= 1/2, así que lo omitido
     es <= 2·t_J (cola geométrica): pérdida relativa <= 2·10^−DIGITS. Una cola diminuta no sale de 1 − Σ;
   - "7+" con 8 <= λ < 1024: inferior = 1 − E_sup · Σ_{k<=6} λ^k / k!  (<= 1 − Σ P(K = k)); la cola
     supera 0,68, sin cancelación dañina;
   - λ >= 1024 (rama analítica, no un tope): P(K = k) <= e^−λ λ^6 <= e^−982,4 < 2^−1100 para k <= 6
     (λ^k <= λ^6 y k! >= 1 con λ >= 1; −λ + 6·ln λ decrece para λ > 6). Inferiores: 0 para k <= 6 y
     1 − 7·2^−1100 para "7+".
3. Conversión a float hacia abajo: el mayor float <= la cota racional (_round_down, exacto). Pierde
   menos de 2^−52 en relativo, o menos de 2^−1074 si el valor es subnormal: un valor por debajo del
   menor float positivo queda en 0 por redondeo, no se elimina masa representable. Con P(K = 0) < 1 y
   P(K = 1) > 0 (λ diminuta), P(K = 0) es 1 − 2^−53, no 1.0; igual que la cola "7+" para λ grande: la
   probabilidad verdadera es < 1.
4. Celda conjunta = el mayor float <= producto exacto de las dos marginales (Fraction): <= p_h·p_a.
Cotas de masa: suma exacta de cada marginal <= 1 (cada término <= su probabilidad) y déficit
< 2^−52 + 10^−45 (conversión + error matemático); conjunta: suma exacta <= (Σ marginal)^2 <= 1 y déficit
< 3·2^−52 + 10^−44 < 1e−15, muy dentro de TOLERANCE = 1e−9. Se comprueba (nunca se corrige) al final:
suma exacta con Fraction <= 1 y math.fsum en [1 − TOLERANCE, 1]; si no, ValueError.
Un int se usa como float; uno fuera del rango de float no es representable: ValueError.
"""

import math
from decimal import ROUND_HALF_EVEN, Context, Decimal, DivisionByZero, InvalidOperation, Overflow, Subnormal, Underflow
from fractions import Fraction

from research.m58.goal_projection import CELLS, GOAL_CATEGORIES, TOLERANCE, validated_cells

EXACT_GOALS = range(7)  # categorías exactas 0..6; la octava es "7+"
SERIES_BELOW = 8.0  # λ < 7 + 1: serie de la cola; si no, complemento
NEGLIGIBLE_FROM = 1024.0  # λ >= 1024: P(K = k) < 2^−1100 para k <= 6 (cota analítica, ver arriba)
DIGITS = 50  # cifras de Decimal.exp
_EXP_CONTEXT = Context(prec=DIGITS, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                       traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, Subnormal])
_EXP_MARGIN = Fraction(1, 10 ** (DIGITS - 3))  # error relativo admitido para E (documentado: <= 5·10^−DIGITS)
_NEGLIGIBLE = Fraction(1, 2 ** 1100)


def _rate(value, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name}: se espera int o float, no {type(value).__name__}")
    try:
        rate = float(value)
    except OverflowError:
        raise ValueError(f"{name}: {value!r} no es representable como float") from None
    if not math.isfinite(rate) or rate < 0:
        raise ValueError(f"{name}: tasa no válida: {value!r} (finita y >= 0)")
    return rate


def _round_down(value: Fraction) -> float:
    """El mayor float <= value (0 <= value <= 1): float() redondea al más cercano; si queda por encima, se baja."""
    result = float(value)
    while Fraction(result) > value:
        result = math.nextafter(result, -math.inf)
    return result


def _exp_bounds(rate: float) -> tuple[Fraction, Fraction]:
    """(inferior, superior) racionales de exp(−rate) para 0 < rate < NEGLIGIBLE_FROM."""
    approx = Fraction(_EXP_CONTEXT.exp(Decimal(rate).copy_negate()))  # Decimal(float) y copy_negate son exactos
    return approx * (1 - _EXP_MARGIN), approx * (1 + _EXP_MARGIN)


def _tail_series(lam: Fraction) -> Fraction:
    """Σ_{j>=7} λ^j / j! truncada (λ < 8): cota inferior; lo omitido es <= 2·10^−DIGITS de la suma."""
    j, term = 7, lam ** 7 / math.factorial(7)
    total = term
    while True:
        j += 1
        term = term * lam / j  # λ^j / j!
        if 2 * lam <= j + 1 and term * 10 ** DIGITS <= total:  # resto desde j <= 2·term (razón <= 1/2)
            return total
        total += term


def _checked(probabilities, what: str):
    """Postcondición, nunca corrección: suma exacta <= 1 y math.fsum dentro del contrato de masa."""
    mass = math.fsum(probabilities)
    if sum(map(Fraction, probabilities), Fraction(0)) > 1 or not 1.0 - TOLERANCE <= mass <= 1.0:
        raise ValueError(f"{what}: la representación no conserva la masa: {mass!r}")
    return probabilities


def poisson_categories(rate, name: str = "lambda") -> tuple[float, ...]:
    """(P(K = 0), ..., P(K = 6), P(K >= 7)) de una Poisson(rate), en el orden de GOAL_CATEGORIES; cada
    valor es el mayor float que no supera una cota inferior rigurosa de la probabilidad."""
    rate = _rate(rate, name)
    if rate == 0.0:
        return (1.0,) + (0.0,) * 7
    if rate >= NEGLIGIBLE_FROM:
        lower = [Fraction(0)] * 7 + [1 - 7 * _NEGLIGIBLE]
    else:
        lam = Fraction(rate)
        exp_low, exp_high = _exp_bounds(rate)
        exact = [lam ** k / math.factorial(k) for k in EXACT_GOALS]
        tail = exp_low * _tail_series(lam) if rate < SERIES_BELOW else 1 - exp_high * sum(exact)
        lower = [exp_low * x for x in exact] + [tail]
    return _checked(tuple(_round_down(p) for p in lower), name)


def independent_poisson_reference(lambda_home, lambda_away) -> list[tuple]:
    """Las 64 entradas (local, visitante, p) de la referencia Poisson independiente, en orden
    canónico y ya validadas con validated_cells (contrato MOD-DEV-066-R1); si no, ValueError."""
    home = dict(zip(GOAL_CATEGORIES, poisson_categories(lambda_home, "lambda_home")))
    away = dict(zip(GOAL_CATEGORIES, poisson_categories(lambda_away, "lambda_away")))
    cells = [(h, a, _round_down(Fraction(home[h]) * Fraction(away[a]))) for h, a in CELLS]
    _checked([p for _, _, p in cells], "conjunta")
    validated_cells(cells)
    return cells
