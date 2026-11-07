"""Medidas descriptivas del monitor V1 y su incertidumbre exploratoria por bloques de semana (puro).

Cada medida es una media sobre las filas de UNA población de una cohorte (nunca una media de medias ni de informes):
una tasa es recuento / denominador con enteros exactos y un error medio es math.fsum de los valores por fila / filas.
La incertidumbre de una diferencia entre dos cohortes es un bootstrap por bloques de semana con los sorteos publicados
de 109B (block_draw) y su configuración compartida; sus percentiles P10/P50/P90 son exploratorios, NO un intervalo de
confianza ni un contraste. Ver research.goals_monitoring_v1.
"""

import math

from research.goals_calibration_uncertainty import BOOTSTRAP_METHOD, QUANTILE_METHOD, QUANTILES, block_draw
from research.goals_monitoring_v1.contract import (BELOW_MIN_SUPPORT, COMPLETE, EXPLORATORY_BOOTSTRAP, HOLDOUT_INPUT,
                                                   INSUFFICIENT_WEEKS, LABELS_INCOMPLETE, MEAN, MEETS_MIN_SUPPORT, NO_DATA,
                                                   NOT_APPLICABLE, NOT_RECONCILED, RATE, SCORED_SETTLED, SETTLED,
                                                   SHARED_WEEKS_NOT_INDEPENDENT, UNDEFINED_IN_REPLICATE)

DIFFERENCE_SEMANTICS = "CANDIDATE_MINUS_REFERENCE"
BOOTSTRAP_SEMANTICS = "EXPLORATORY_PERCENTILES_OF_INDEPENDENT_WEEK_BLOCK_RESAMPLES_PER_COHORT_NOT_A_CONFIDENCE_INTERVAL"
DRAW_LAYOUT = "REFERENCE_DRAWS_0_TO_WR_MINUS_1_CANDIDATE_DRAWS_WR_TO_WR_PLUS_WC_MINUS_1"

_POPULATIONS = {
    HOLDOUT_INPUT: lambda r: True,
    SETTLED: lambda r: r.label is not None,
    SCORED_SETTLED: lambda r: r.estimate and r.label is not None,
}


def measure_specs(classes: tuple[str, ...]) -> list[tuple]:
    """(nombre, tipo, población, depende_del_resultado, valor_por_fila, definición) en un orden fijo."""
    specs = [
        ("estimate_coverage", RATE, HOLDOUT_INPUT, False, lambda r: int(r.estimate),
         "filas ESTIMATE / filas de entrada del holdout (cobertura del productor)"),
        ("label_missing_rate", RATE, HOLDOUT_INPUT, False, lambda r: int(r.label is None),
         "filas sin etiqueta final (ESTIMATE o NO_ESTIMATE) / filas de entrada del holdout"),
        ("abstention_rate_settled", RATE, SETTLED, True, lambda r: int(not r.estimate),
         "NO_ESTIMATE liquidadas / todas las liquidadas (abstained_settled / settled_total)"),
    ]
    specs += [(f"settled_share.{c}", RATE, SETTLED, True, lambda r, c=c: int(r.label == c),
               f"liquidadas {c} / todas las liquidadas (semántica de outcome_counts de 109A)") for c in classes]
    specs += [(f"scored_share.{c}", RATE, SCORED_SETTLED, True, lambda r, c=c: int(r.label == c),
               f"puntuadas {c} / puntuadas (semántica de outcome_counts de 109B)") for c in classes]
    specs += [
        ("brier_mean", MEAN, SCORED_SETTLED, True, lambda r: r.brier,
         "media fila a fila del Brier publicado sobre las ESTIMATE liquidadas (0-2 en binario y en tres clases)"),
        ("log_loss_mean", MEAN, SCORED_SETTLED, True, lambda r: r.log_loss,
         "media fila a fila del log loss publicado (EPS 1e-15 solo dentro del log) sobre las ESTIMATE liquidadas"),
    ]
    return specs


def aggregate(cohort: dict, spec: tuple) -> dict:
    """Recuento, numerador o suma y valor de una medida en una cohorte, con sus agregados por semana (para el bootstrap)."""
    name, kind, population, _, value_of, _ = spec
    weeks = cohort["weeks"]
    position = {w: i for i, w in enumerate(weeks)}
    counts, parts = [0] * len(weeks), [[] for _ in weeks]
    keep = _POPULATIONS[population]
    for r in cohort["rows"]:
        if keep(r):
            i = position[r.week]
            counts[i] += 1
            parts[i].append(value_of(r))
    count = sum(counts)
    if kind == RATE:
        totals = [sum(p) for p in parts]
        numerator = sum(totals)
        value = numerator / count if count else None
    else:
        totals = [math.fsum(p) for p in parts]
        numerator = None
        value = math.fsum(v for p in parts for v in p) / count if count else None
    return {"kind": kind, "count": count, "numerator": numerator, "value": value, "week_counts": counts, "week_totals": totals}


def support(count: int, min_support: int) -> str:
    if not count:
        return NO_DATA
    return MEETS_MIN_SUPPORT if count >= min_support else BELOW_MIN_SUPPORT


def summary_measures(cohort: dict, min_support: int) -> dict:
    """Las medidas de una cohorte verificada, con su soporte y el estado de su población."""
    complete = cohort["denominators"]["label_missing_input"] == 0
    out = {}
    for spec in measure_specs(cohort["classes"]):
        name, kind, population, dependent, _, definition = spec
        agg = aggregate(cohort, spec)
        entry = {"definition": definition, "kind": kind, "population": population, "outcome_dependent": dependent,
                 "population_status": (COMPLETE if complete else LABELS_INCOMPLETE) if dependent else NOT_APPLICABLE,
                 "count": agg["count"], "support": support(agg["count"], min_support), "value": agg["value"]}
        if kind == RATE:
            entry["numerator"] = agg["numerator"]
        else:
            entry["reconciliation"] = cohort["model_status"]
            if cohort["model_status"] == NOT_RECONCILED:
                entry["value"] = None  # sin conciliar con 109A no se informa: se omite, no se inventa
        out[name] = entry
    return out


# --------------------------------------------------------------------------------------------------------- bootstrap

def _replicate(agg: dict, draws: list[int]) -> float | None:
    count = sum(agg["week_counts"][d] for d in draws)
    if not count:
        return None
    if agg["kind"] == RATE:
        return sum(agg["week_totals"][d] for d in draws) / count
    return math.fsum(agg["week_totals"][d] for d in draws) / count


def nearest_rank(values: list[float]) -> list[list]:
    """Percentiles por rango más cercano de 109B: el valor ordenado en la posición ceil(q * n) (q exacto con Fraction)."""
    ordered = sorted(values)
    return [[label, ordered[math.ceil(q * len(ordered)) - 1]] for label, q in QUANTILES]


def bootstrap_differences(reference: dict, candidate: dict, aggs: dict) -> dict:
    """{medida: incertidumbre} de candidate - reference. Cada réplica r remuestrea con reemplazo las semanas de cada cohorte
    por separado con block_draw(seed, r, j, W) de 109B: la referencia usa los sorteos j = 0..Wr-1 y la candidata
    j = Wr..Wr+Wc-1, así que las dos cohortes nunca comparten sorteo. aggs = {medida: (agregado ref, agregado cand)}."""
    setup = reference["bootstrap"]
    seed, iterations, min_weeks = setup["seed"], setup["iterations"], setup["min_weeks"]
    wr, wc = len(reference["weeks"]), len(candidate["weeks"])
    base = {"method": BOOTSTRAP_METHOD, "draw_layout": DRAW_LAYOUT, "quantile_method": QUANTILE_METHOD,
            "interval_semantics": BOOTSTRAP_SEMANTICS, "iterations": iterations, "min_weeks": min_weeks,
            "reference_weeks": wr, "candidate_weeks": wc}
    if set(reference["weeks"]) & set(candidate["weeks"]):
        status = SHARED_WEEKS_NOT_INDEPENDENT  # filas disjuntas en una misma semana ISO: los bloques no son independientes
    elif wr < min_weeks or wc < min_weeks:
        status = INSUFFICIENT_WEEKS
    else:
        status = None
    if status:
        return {name: {**base, "status": status, "replicates": 0, "quantiles": [], "p10_p90_excludes_zero": None} for name in aggs}

    replicates = {name: [] for name in aggs}
    undefined = set()
    for r in range(iterations):
        ref_draws = [block_draw(seed, r, j, wr) for j in range(wr)]
        cand_draws = [block_draw(seed, r, wr + j, wc) for j in range(wc)]
        for name, (ref_agg, cand_agg) in aggs.items():
            if name in undefined:
                continue
            a, b = _replicate(ref_agg, ref_draws), _replicate(cand_agg, cand_draws)
            if a is None or b is None:
                undefined.add(name)  # una réplica sin población: no se descarta en silencio ni se rellena
            else:
                replicates[name].append(b - a)
    out = {}
    for name in aggs:
        if name in undefined:
            out[name] = {**base, "status": UNDEFINED_IN_REPLICATE, "replicates": 0, "quantiles": [], "p10_p90_excludes_zero": None}
            continue
        quantiles = nearest_rank(replicates[name])
        p10, p90 = quantiles[0][1], quantiles[-1][1]
        out[name] = {**base, "status": EXPLORATORY_BOOTSTRAP, "replicates": iterations, "quantiles": quantiles,
                     "p10_p90_excludes_zero": p10 > 0 or p90 < 0}
    return out
