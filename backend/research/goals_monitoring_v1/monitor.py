"""Resúmenes por cohorte, comparaciones entre cohortes disjuntas y secuencias de monitorización V1 (puro).
Ver research.goals_monitoring_v1."""

from collections import Counter

from research.goals_calibration_uncertainty import DIAGNOSTIC_ID, MULTIPLE_OBSERVED_CLASSES
from research.goals_consumer_v1.consumer import canonical, digest
from research.goals_monitoring_v1.contract import (CALIBRATION_CLAIM, COMPARED, CONTRACT_VERSION, DUPLICATE_REPORT, ECE_REPLICATES_UNPAIRED,
                                                   EMPTY_COHORT, EVIDENCE_REJECTED, EVIDENCE_STATUS, EXPLORATORY_BOOTSTRAP,
                                                   FEWER_THAN_TWO_COHORTS, GOVERNANCE_TAGS, INCOMPLETE_LABELS, INSUFFICIENT_SUPPORT,
                                                   INTERPRETATION, LIMITATIONS, LINE_MISMATCH, MARKET_MISMATCH, MEAN, MEETS_MIN_SUPPORT,
                                                   MODEL_ERROR_NOT_RECONCILED, MONITORING_ID, MONITORING_VERSION, NO_COMPARISON,
                                                   NO_DATA, NO_MEASURE_WITH_MIN_SUPPORT, NOT_COMPARABLE, NOT_COMPUTED,
                                                   NOT_RECONCILED, OVERLAPPING_FIXTURES, PRODUCER_MISMATCH, PRODUCER_VERSION_MISMATCH,
                                                   RATE, RECONCILED, RELIABILITY_CONFIG_MISMATCH, RELIABILITY_SAMPLE_INSUFFICIENT,
                                                   SCORED_SETTLED, SINGLE_OBSERVED_CLASS, SOURCE_STATUS, TARGET_DEFINITION_MISMATCH,
                                                   TEMPORAL_ORDER_INVALID, TEMPORAL_OVERLAP, VERIFIED, BELOW_MIN_SUPPORT,
                                                   MonitoringInputError, plain_json_copy, sequence, validate_config)
from research.goals_monitoring_v1.evidence import load_cohort
from research.goals_monitoring_v1.measures import (DIFFERENCE_SEMANTICS, aggregate, bootstrap_differences, measure_specs,
                                                   summary_measures)

COHORT_SUMMARY, COHORT_COMPARISON, MONITORING_SEQUENCE = "COHORT_SUMMARY", "COHORT_COMPARISON", "MONITORING_SEQUENCE"
IDENTITY_FIELD = "document_digest"
COHORT_DIAGNOSTIC = "COHORT_DIAGNOSTIC"
DENOMINATOR_RECONCILIATION = "LEDGER_RECOUNT_EQUALS_REPORT_FOLDS_109A_ALL_SETTLED_AND_109B_SCORED_ONLY"
SHARED_ISO_WEEKS = "COHORTS_SHARE_ISO_WEEKS"
_STATUS_PRECEDENCE = (NOT_COMPARABLE, NO_COMPARISON, INSUFFICIENT_SUPPORT, COMPARED)


def _document(kind: str, cfg: dict, body: dict) -> dict:
    full = {"monitoring_id": MONITORING_ID, "monitoring_version": MONITORING_VERSION, "contract_version": CONTRACT_VERSION,
            "document": kind, "evidence_status": EVIDENCE_STATUS, "source_status": SOURCE_STATUS, "calibration_claim": CALIBRATION_CLAIM,
            "interpretation": INTERPRETATION, "governance_tags": list(GOVERNANCE_TAGS), "limitations": list(LIMITATIONS),
            "config": cfg, **body}
    return {**full, IDENTITY_FIELD: digest(full)}


# --------------------------------------------------------------------------------------------------------- cohorte

def _spread(uncertainty: dict) -> float | None:
    if uncertainty["status"] != EXPLORATORY_BOOTSTRAP:
        return None
    q = dict(uncertainty["quantiles"])
    return q["P90"] - q["P10"]


def _calibration(c: dict) -> dict:
    """El diagnóstico 109B de la cohorte tal cual (sobre sus filas puntuadas): nunca se recalcula ni se promedia."""
    p = c["reliability"]
    u = p["uncertainty"]
    return {"source_id": DIAGNOSTIC_ID, "report_identity": p["report_identity"], "config_identity": p["config_identity"],
            "metric_basis": p["metric_basis"], "ece_method": p["ece_method"], "ece": p["ece"], "scored": p["coverage"]["scored"],
            "sample_status": p["sample_status"], "outcome_status": p["outcome_status"],
            "uncertainty": {"status": u["status"], "method": u["method"], "interval_semantics": u["interval_semantics"],
                            "weeks": u["weeks"], "replicates": u["replicates"], "quantiles": u["quantiles"]},
            "bootstrap_spread_p10_p90": _spread(u)}


def _warnings(c: dict, min_support: int) -> list[str]:
    den = c["denominators"]
    w = []
    if not den["input_total"]:
        w.append("NO_HOLDOUT_ROWS")
    elif den["input_total"] < min_support:
        w.append("SMALL_COHORT_BELOW_MIN_SUPPORT")
    if den["label_missing_input"]:
        w.append("LABELS_INCOMPLETE")
    if den["input_total"] and not den["settled_total"]:
        w.append("NO_SETTLED_OUTCOMES")
    if den["settled_total"] and not den["scored_settled"]:
        w.append("ALL_SETTLED_ABSTAINED")
    if len(c["weeks"]) < c["bootstrap"]["min_weeks"]:
        w.append("FEWER_WEEKS_THAN_109B_MIN_WEEKS")
    if c["model_status"] == NOT_RECONCILED:
        w.append(MODEL_ERROR_NOT_RECONCILED)
    if c["reliability"]["sample_status"] != MEETS_MIN_SUPPORT:
        w.append(RELIABILITY_SAMPLE_INSUFFICIENT)
    return sorted(w)


def _cohort_summary(c: dict, cfg: dict) -> dict:
    if c["status"] != VERIFIED:
        return {"status": c["status"], "reasons": c["reasons"], "input_digest": c["input_digest"], "report_digest": c["report_digest"]}
    per_week = Counter(r.week for r in c["rows"])
    windows = c["windows"]
    return {
        "status": VERIFIED, "reasons": [], "input_digest": c["input_digest"], "report_digest": c["report_digest"],
        "consumer": c["consumer"], "producer": c["producer"], "target": c["target"], "sources": c["sources"],
        "time": {"holdout_windows": [{"fold_index": k, "holdout_start": w[2], "holdout_end": w[3]} for k, w in enumerate(windows)],
                 "span_start": windows[0][2], "span_end": windows[-1][3], "first_kickoff_at": c["first_kickoff_at"],
                 "last_kickoff_at": c["last_kickoff_at"], "weeks": len(c["weeks"]),
                 "rows_per_week_min": min(per_week.values()) if per_week else None,
                 "rows_per_week_max": max(per_week.values()) if per_week else None},
        "denominators": c["denominators"],
        "labels_complete": c["denominators"]["label_missing_input"] == 0,
        "reconciliation": {"denominators": DENOMINATOR_RECONCILIATION, "model_error": c["model_status"],
                           "model_error_folds": c["model_folds"]},
        "measures": summary_measures(c, cfg["min_support"]),
        "calibration": _calibration(c),
        "warnings": _warnings(c, cfg["min_support"]),
    }


# ------------------------------------------------------------------------------------------------------ comparación

def _cohort_ref(c: dict) -> dict:
    if c["status"] != VERIFIED:
        return {"status": c["status"], "reasons": c["reasons"], "input_digest": c["input_digest"], "report_digest": c["report_digest"],
                "span_start": None, "span_end": None, "weeks": None, "input_total": None}
    return {"status": VERIFIED, "reasons": [], "input_digest": c["input_digest"], "report_digest": c["report_digest"],
            "span_start": c["windows"][0][2], "span_end": c["windows"][-1][3], "weeks": len(c["weeks"]),
            "input_total": c["denominators"]["input_total"]}


def _gates(a: dict, b: dict) -> list[str]:
    """Motivos de NOT_COMPARABLE: evidencia rechazada, la misma evidencia, fixtures compartidos, otro productor, versión,
    mercado, línea, definición del objetivo o configuración de 109B, y ventanas que se solapan o no van en orden."""
    if a["status"] != VERIFIED or b["status"] != VERIFIED:
        return [EVIDENCE_REJECTED]
    reasons = set()
    if a["input_digest"] == b["input_digest"] or a["report_digest"] == b["report_digest"]:
        reasons.add(DUPLICATE_REPORT)
    if a["fixtures"] & b["fixtures"]:
        reasons.add(OVERLAPPING_FIXTURES)
    if a["producer"]["producer_id"] != b["producer"]["producer_id"]:
        reasons.add(PRODUCER_MISMATCH)
    elif a["producer"]["producer_version"] != b["producer"]["producer_version"]:
        reasons.add(PRODUCER_VERSION_MISMATCH)
    if a["target"]["market"] != b["target"]["market"]:
        reasons.add(MARKET_MISMATCH)
    elif a["target"]["line"] != b["target"]["line"]:
        reasons.add(LINE_MISMATCH)
    elif canonical(a["target"]) != canonical(b["target"]):
        reasons.add(TARGET_DEFINITION_MISMATCH)
    if a["config_identity"] != b["config_identity"]:
        reasons.add(RELIABILITY_CONFIG_MISMATCH)
    # ventanas semiabiertas [inicio, fin): la referencia termina antes de que empiece la candidata
    if any(sa < eb and sb < ea for sa, ea, *_ in a["windows"] for sb, eb, *_ in b["windows"]):
        reasons.add(TEMPORAL_OVERLAP)
    elif not a["windows"][-1][1] <= b["windows"][0][0]:
        reasons.add(TEMPORAL_ORDER_INVALID)
    return sorted(reasons)


def _side(agg: dict, kind: str, hide_value: bool) -> dict:
    side = {"count": agg["count"], "value": None if hide_value else agg["value"]}
    if kind == RATE:
        side["numerator"] = agg["numerator"]
    return side


def _measure_status(kind: str, dependent: bool, complete: bool, reconciled: bool, a: dict, b: dict, min_support: int):
    if dependent and not complete:
        return NOT_COMPARABLE, [INCOMPLETE_LABELS]
    if kind == MEAN and not reconciled:
        return NOT_COMPARABLE, [MODEL_ERROR_NOT_RECONCILED]
    if not a["count"] or not b["count"]:
        return NO_COMPARISON, [NO_DATA]
    if a["count"] < min_support or b["count"] < min_support:
        return INSUFFICIENT_SUPPORT, [BELOW_MIN_SUPPORT]
    return COMPARED, []


def _ece_side(c: dict) -> dict:
    cal = _calibration(c)
    return {k: cal[k] for k in ("report_identity", "ece", "scored", "sample_status", "outcome_status", "bootstrap_spread_p10_p90")} | {
        "uncertainty_status": cal["uncertainty"]["status"], "quantiles": cal["uncertainty"]["quantiles"]}


def _ece_comparison(a: dict, b: dict, complete: bool) -> dict:
    """ECE de 109B de cada cohorte, cada uno sobre sus propias filas: la diferencia es descriptiva y no tiene incertidumbre
    emparejada (las réplicas de 109B son de cada cohorte por separado y se publican ordenadas)."""
    ra, rb = a["reliability"], b["reliability"]
    if not complete:
        status, reasons = NOT_COMPARABLE, [INCOMPLETE_LABELS]
    elif ra["ece"] is None or rb["ece"] is None:
        status, reasons = NO_COMPARISON, [NO_DATA]
    elif ra["sample_status"] != MEETS_MIN_SUPPORT or rb["sample_status"] != MEETS_MIN_SUPPORT:
        status, reasons = INSUFFICIENT_SUPPORT, [RELIABILITY_SAMPLE_INSUFFICIENT]
    elif ra["outcome_status"] != MULTIPLE_OBSERVED_CLASSES or rb["outcome_status"] != MULTIPLE_OBSERVED_CLASSES:
        status, reasons = INSUFFICIENT_SUPPORT, [SINGLE_OBSERVED_CLASS]
    else:
        status, reasons = COMPARED, []
    sa, sb = _ece_side(a), _ece_side(b)
    compared = status == COMPARED
    spreads = sa["bootstrap_spread_p10_p90"], sb["bootstrap_spread_p10_p90"]
    return {"definition": "ece de 109B de cada cohorte sobre sus propias filas puntuadas (nunca promediado ni agrupado)",
            "kind": COHORT_DIAGNOSTIC, "population": SCORED_SETTLED, "outcome_dependent": True, "status": status, "reasons": reasons,
            "reference": sa, "candidate": sb, "difference": sb["ece"] - sa["ece"] if compared else None,
            "bootstrap_spread_difference": spreads[1] - spreads[0] if compared and None not in spreads else None,
            "uncertainty": {"status": NOT_COMPUTED, "reasons": [ECE_REPLICATES_UNPAIRED]}}


def _comparison(a: dict, b: dict, cfg: dict) -> dict:
    body = {"difference_semantics": DIFFERENCE_SEMANTICS, "reference": _cohort_ref(a), "candidate": _cohort_ref(b)}
    reasons = _gates(a, b)
    if reasons:
        return {**body, "result": {"status": NOT_COMPARABLE, "reasons": reasons}, "measures": None, "warnings": []}
    if not a["denominators"]["input_total"] or not b["denominators"]["input_total"]:
        return {**body, "result": {"status": NO_COMPARISON, "reasons": [EMPTY_COHORT]}, "measures": None, "warnings": []}

    min_support = cfg["min_support"]
    complete = a["denominators"]["label_missing_input"] == 0 and b["denominators"]["label_missing_input"] == 0
    reconciled = a["model_status"] == RECONCILED and b["model_status"] == RECONCILED
    measures, compared = {}, {}
    for spec in measure_specs(a["classes"]):
        name, kind, population, dependent, _, definition = spec
        ra, rb = aggregate(a, spec), aggregate(b, spec)
        status, why = _measure_status(kind, dependent, complete, reconciled, ra, rb, min_support)
        hide = kind == MEAN and not reconciled
        measures[name] = {"definition": definition, "kind": kind, "population": population, "outcome_dependent": dependent,
                          "status": status, "reasons": why, "reference": _side(ra, kind, hide), "candidate": _side(rb, kind, hide),
                          "difference": rb["value"] - ra["value"] if status == COMPARED else None, "uncertainty": None}
        if status == COMPARED:
            compared[name] = (ra, rb)
    if compared:
        for name, uncertainty in bootstrap_differences(a, b, compared).items():
            measures[name]["uncertainty"] = uncertainty
    measures["ece"] = _ece_comparison(a, b, complete)

    statuses = {m["status"] for m in measures.values()}
    if COMPARED in statuses:
        result = {"status": COMPARED, "reasons": []}
    else:
        result = {"status": INSUFFICIENT_SUPPORT if INSUFFICIENT_SUPPORT in statuses else NO_COMPARISON,
                  "reasons": [NO_MEASURE_WITH_MIN_SUPPORT]}
    warnings = [f"reference.{w}" for w in _warnings(a, min_support)] + [f"candidate.{w}" for w in _warnings(b, min_support)]
    if set(a["weeks"]) & set(b["weeks"]):
        warnings.append(SHARED_ISO_WEEKS)
    return {**body, "result": result, "measures": measures, "warnings": sorted(warnings)}


# ---------------------------------------------------------------------------------------------------------- interfaz

def summarize_goals_cohort(report, config) -> dict:
    """Resumen V1 de una cohorte (un informe de run_goals_research_consumer), con document_digest."""
    cfg = validate_config(config)
    cohort = load_cohort(report, "informe")
    return _document(COHORT_SUMMARY, cfg, {"cohort": _cohort_summary(cohort, cfg)})


def compare_goals_cohorts(reference, candidate, config) -> dict:
    """Comparación V1 de dos cohortes disjuntas; la candidata empieza cuando termina la referencia o después."""
    cfg = validate_config(config)
    a, b = load_cohort(reference, "reference"), load_cohort(candidate, "candidate")
    return _document(COHORT_COMPARISON, cfg, _comparison(a, b, cfg))


def monitor_goals_cohorts(reports, config) -> dict:
    """Secuencia V1: resúmenes de todas las cohortes en orden temporal y comparación de cada cohorte con la anterior.
    El resultado no depende del orden de entrada."""
    cfg = validate_config(config)
    seq = sequence(reports, "informes")
    if not seq:
        raise MonitoringInputError("informes: se espera al menos un informe del consumidor")
    cohorts = [load_cohort(r, f"informe {i}") for i, r in enumerate(seq)]
    verified = sorted((c for c in cohorts if c["status"] == VERIFIED), key=lambda c: (c["windows"][0][0], c["report_digest"]))
    rejected = sorted((c for c in cohorts if c["status"] != VERIFIED), key=lambda c: c["input_digest"])
    comparisons = [_comparison(verified[k - 1], verified[k], cfg) for k in range(1, len(verified))]

    reasons = {EVIDENCE_REJECTED} if rejected else set()
    digests = [c["input_digest"] for c in verified]
    if len(set(digests)) != len(digests):
        reasons.add(DUPLICATE_REPORT)
    seen: set[int] = set()
    for c in verified:
        if c["fixtures"] & seen:
            reasons.add(OVERLAPPING_FIXTURES)  # también entre cohortes no consecutivas
        seen |= c["fixtures"]
    statuses = [cmp["result"]["status"] for cmp in comparisons]
    if reasons or NOT_COMPARABLE in statuses:
        status = NOT_COMPARABLE
        reasons |= {r for cmp in comparisons if cmp["result"]["status"] == NOT_COMPARABLE for r in cmp["result"]["reasons"]}
    elif len(verified) < 2:
        status, reasons = NO_COMPARISON, {FEWER_THAN_TWO_COHORTS}
    else:
        status = next(s for s in _STATUS_PRECEDENCE if s in statuses)
        reasons = {r for cmp in comparisons if cmp["result"]["status"] == status for r in cmp["result"]["reasons"]}
    return _document(MONITORING_SEQUENCE, cfg, {
        "result": {"status": status, "reasons": sorted(reasons)},
        "cohorts": [_cohort_summary(c, cfg) for c in verified + rejected],
        "comparisons": comparisons,
    })


def verify_monitoring_document(document) -> bool:
    """True si un documento del monitor V1 (resumen, comparación o secuencia) conserva su document_digest. Prueba
    consistencia interna, no autenticidad: cualquiera puede recalcular un digest."""
    if type(document) is not dict:
        raise MonitoringInputError("documento: se espera un dict exacto")
    data = plain_json_copy(document, "documento")
    stated = data.get(IDENTITY_FIELD)
    if (type(stated) is not str or data.get("monitoring_id") != MONITORING_ID or data.get("monitoring_version") != MONITORING_VERSION
            or data.get("contract_version") != CONTRACT_VERSION):
        return False
    return digest({k: v for k, v in data.items() if k != IDENTITY_FIELD}) == stated
