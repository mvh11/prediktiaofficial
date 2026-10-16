"""Distribución de REFERENCIA de goles a 90': Poisson independientes en la partición 8x8 (puro).

Solo una línea base matemática (MOD-DEV-069): goles local ~ Poisson(lambda_home) y visitante ~
Poisson(lambda_away), independientes SOLO para construir esta referencia. No es el modelo de goles
de Prediktia, no ajusta ni elige lambdas y no afirma que los goles reales sean independientes.

Por equipo: P(K = k) = exp(−λ) λ^k / k! para k = 0..6 y P(K >= 7) para la categoría "7+" (siete o
más, nunca exactamente 7). Con λ = 0 toda la masa está en 0. Celda conjunta = producto de las dos
marginales; salida = las 64 entradas (local, visitante, p) en el orden canónico de
research.m58.goal_projection, que pasan su validated_cells sin cambios.

Cálculo estable, sin recortes, épsilons, renormalización ni tope de λ:
- P(K = k) en escala logarítmica: exp(−λ + k·log λ − lgamma(k + 1)); sin factoriales ni potencias
  que desborden, y una masa diminuta queda subnormal o 0 por redondeo, no por un tope;
- cola con λ < 8 (= 7 + 1): serie de la gamma incompleta inferior regularizada P(7, λ) =
  exp(−λ + 7·log λ − lgamma(8)) · Σ λ^n / (8·9···(7 + n)), sumada hasta que un término ya no cambia
  la suma en coma flotante; así una cola diminuta no sale de 1 − Σ (cancelación);
- cola con λ >= 8: 1 − math.fsum(P(K = 0..6)) (la cola supera 0,68: no hay cancelación dañina).
Un int se usa como float; uno fuera del rango de float no es representable: ValueError.
"""

import math

from research.m58.goal_projection import CELLS, GOAL_CATEGORIES, TOLERANCE, validated_cells

EXACT_GOALS = range(7)  # categorías exactas 0..6; la octava es "7+"
SERIES_BELOW = 8.0  # λ < 7 + 1: serie de P(7, λ); si no, complemento


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


def _tail_series(rate: float, log_rate: float) -> float:
    """P(K >= 7) = P(7, λ) por su serie (λ < 8): términos positivos y decrecientes, sumados con fsum."""
    terms, term, n = [1.0], 1.0, 0
    while True:
        n += 1
        term *= rate / (7 + n)
        partial = math.fsum(terms)
        if partial + term == partial:  # el término ya no cambia la suma en coma flotante
            break
        terms.append(term)
    return math.exp(-rate + 7 * log_rate - math.lgamma(8)) * math.fsum(terms)


def poisson_categories(rate, name: str = "lambda") -> tuple[float, ...]:
    """(P(K = 0), ..., P(K = 6), P(K >= 7)) de una Poisson(rate), en el orden de GOAL_CATEGORIES."""
    rate = _rate(rate, name)
    if rate == 0.0:
        return (1.0,) + (0.0,) * 7
    log_rate = math.log(rate)
    exact = tuple(math.exp(-rate + k * log_rate - math.lgamma(k + 1)) for k in EXACT_GOALS)
    tail = _tail_series(rate, log_rate) if rate < SERIES_BELOW else 1.0 - math.fsum(exact)
    marginal = exact + (tail,)
    if not abs(math.fsum(marginal) - 1.0) <= TOLERANCE:  # comprobación, nunca corrección
        raise ValueError(f"{name}: la marginal no conserva la masa: {math.fsum(marginal)!r}")
    return marginal


def independent_poisson_reference(lambda_home, lambda_away) -> list[tuple]:
    """Las 64 entradas (local, visitante, p) de la referencia Poisson independiente, en orden
    canónico y ya validadas con validated_cells (contrato MOD-DEV-066-R1); si no, ValueError."""
    home = dict(zip(GOAL_CATEGORIES, poisson_categories(lambda_home, "lambda_home")))
    away = dict(zip(GOAL_CATEGORIES, poisson_categories(lambda_away, "lambda_away")))
    cells = [(h, a, home[h] * away[a]) for h, a in CELLS]
    validated_cells(cells)
    return cells
