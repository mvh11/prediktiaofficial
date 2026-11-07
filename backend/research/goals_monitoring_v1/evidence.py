"""Evidencia de una cohorte del monitor V1: un informe de research.goals_consumer_v1 copiado como JSON nativo exacto,
comprobado en lo temporal, auditado por completo con la auditoría PUBLICADA del consumidor y reconciliado con un recuento
propio fila a fila antes de cualquier estadística (puro). Ver research.goals_monitoring_v1."""

import math
from collections import Counter
from typing import NamedTuple

from research.goals_calibration_uncertainty import INT_ENCODING
from research.goals_consumer_v1.aggregate import VERIFIED as CONSUMER_VERIFIED
from research.goals_consumer_v1.aggregate import aggregate_goals_consumer_reports
from research.goals_consumer_v1.consumer import REPORT_FIELDS, canonical, digest
from research.goals_consumer_v1.contract import CONSUMER_ID, CONSUMER_VERSION, ESTIMATE, FIXTURE_ID_LIMIT, GoalsConsumerError, describe
from research.goals_consumer_v1.contract import CONTRACT_VERSION as CONSUMER_CONTRACT_VERSION
from research.goals_markets.integer_evaluation import INTEGER_OUTCOMES
from research.goals_monitoring_v1.contract import (CONSUMER_VERSION_MISMATCH, EVIDENCE_NOT_AUDITABLE, NOT_RECONCILED, RECONCILED,
                                                   REJECTED, TEMPORAL_EVIDENCE_INCONSISTENT, VERIFIED, MonitoringInputError,
                                                   MonitoringIntegrityError, plain_json_copy, utc_instant)
from research.m58.metrics import EPS

_HEX_DIGITS = frozenset("0123456789abcdef")
_COUNT_FIELDS = ("input_total", "estimate_input", "no_estimate_input", "label_missing_input", "label_missing_estimate",
                 "label_missing_no_estimate", "settled_total", "scored_settled", "abstained_settled")


class Row(NamedTuple):
    """Una fila del libro mayor auditado, con su puntuación reconstruida si es una ESTIMATE liquidada."""
    fixture_id: int
    fold_index: int
    week: str
    estimate: bool
    label: str | None
    label_missing_reason: str | None
    brier: float | None
    log_loss: float | None


# ------------------------------------------------------------------------------------------- puntuación publicada

def row_scores(probabilities: dict, label: str, classes: tuple[str, ...]) -> tuple[float, float]:
    """(Brier, log loss) de UNA fila con las fórmulas publicadas que usa 109A, en su mismo orden de operaciones:
    binario (research.goals_markets.evaluation): sum((p_k - y_k)^2) de dos componentes (0-2) en el orden de las clases;
    tres clases (research.goals_markets.integer_evaluation): sum((p_c - y_c)^2) en OVER, UNDER, PUSH;
    log loss: -log(max(p_observada, EPS)) con EPS = research.m58.metrics.EPS (1e-15) solo dentro del log."""
    if len(classes) == 2:
        p = (probabilities[classes[0]], probabilities[classes[1]])
        observed = classes.index(label)
        return sum((p[k] - (k == observed)) ** 2 for k in range(2)), -math.log(max(p[observed], EPS))
    return (sum((probabilities[k] - (k == label)) ** 2 for k in INTEGER_OUTCOMES),
            -math.log(max(probabilities[label], EPS)))


# --------------------------------------------------------------------------------------------- comprobación previa

def _precheck(data: dict) -> str | None:
    """Lo que el monitor necesita antes de auditar: forma de informe completo (un resumen sin libro mayor no es evidencia),
    versión del consumidor e instantes UTC canónicos de ventanas y filas, coherentes entre sí. Motivo o None."""
    if set(data) != set(REPORT_FIELDS):
        return EVIDENCE_NOT_AUDITABLE
    if (data["consumer_id"], data["consumer_version"], data["contract_version"]) != (CONSUMER_ID, CONSUMER_VERSION,
                                                                                    CONSUMER_CONTRACT_VERSION):
        return CONSUMER_VERSION_MISMATCH
    folds, ledger = data["fold_groups"], data["ledger"]
    if type(folds) is not list or not folds or type(ledger) is not list:
        return EVIDENCE_NOT_AUDITABLE
    windows = []
    for k, group in enumerate(folds):
        if type(group) is not dict or type(group.get("fold_index")) is not int or group["fold_index"] != k:
            return EVIDENCE_NOT_AUDITABLE
        start, reason = utc_instant(group.get("holdout_start"))
        if reason:
            return reason
        end, reason = utc_instant(group.get("holdout_end"))
        if reason:
            return reason
        if not start < end or (windows and start < windows[-1][1]):
            return TEMPORAL_EVIDENCE_INCONSISTENT  # ventana vacía, invertida o pliegues solapados o desordenados
        windows.append((start, end))
    for entry in ledger:
        if type(entry) is not dict:
            return EVIDENCE_NOT_AUDITABLE
        k = entry.get("fold_index")
        if type(k) is not int or not 0 <= k < len(windows):
            return EVIDENCE_NOT_AUDITABLE
        kickoff, reason = utc_instant(entry.get("kickoff_at"))
        if reason:
            return reason
        if not windows[k][0] <= kickoff < windows[k][1]:
            return TEMPORAL_EVIDENCE_INCONSISTENT  # una fila fuera del holdout de su pliegue
    return None


# --------------------------------------------------------------------------------------------------- recuento propio

def recount(rows: list[Row], classes: tuple[str, ...]) -> dict:
    """Los denominadores del consumidor, recontados de forma independiente con los mismos nombres y semánticas."""
    den = {name: 0 for name in _COUNT_FIELDS}
    by_class = {name: {c: 0 for c in classes} for name in ("settled_by_class", "scored_by_class", "abstained_by_class")}
    reasons = Counter()
    for r in rows:
        den["input_total"] += 1
        den["estimate_input" if r.estimate else "no_estimate_input"] += 1
        if r.label is None:
            den["label_missing_input"] += 1
            den["label_missing_estimate" if r.estimate else "label_missing_no_estimate"] += 1
            reasons[r.label_missing_reason] += 1
        else:
            den["settled_total"] += 1
            den["scored_settled" if r.estimate else "abstained_settled"] += 1
            by_class["settled_by_class"][r.label] += 1
            by_class["scored_by_class" if r.estimate else "abstained_by_class"][r.label] += 1
    den.update(by_class)
    den["label_missing_by_reason"] = dict(sorted(reasons.items()))
    return den


def _require(condition: bool, check: str) -> None:
    if not condition:
        raise MonitoringIntegrityError(f"informe auditado sin conciliar con el recuento del monitor: {check}")


def _reconcile(data: dict, rows: list[Row], classes: tuple[str, ...]) -> None:
    """Denominadores antes de cualquier estadística: recuento propio = informe = 109A (todas las liquidadas, por pliegue)
    = 109B (solo puntuadas). Un informe auditado que no cuadra es un fallo de integridad, nunca un resultado."""
    pooled = data["pooled"]["denominators"]
    _require(canonical(recount(rows, classes)) == canonical(pooled), "denominadores agregados")
    for k, group in enumerate(data["fold_groups"]):
        fold_rows = [r for r in rows if r.fold_index == k]
        den = recount(fold_rows, classes)
        _require(canonical(den) == canonical(group["denominators"]), f"denominadores del pliegue {k}")
        oos = data["oos_report"]["folds"][k]["groups"]
        found = [g for g in oos if g["market"] == data["target"]["market"] and g["line"] == data["target"]["line"]]
        if found:
            _require(canonical(found[0]["outcome_counts"]) == canonical(den["settled_by_class"]),
                     f"109A outcome_counts del pliegue {k} = todas las liquidadas")
        else:
            _require(not fold_rows, f"109A sin grupo del objetivo en el pliegue {k} con filas")
    payload = data["reliability_reports"]["pooled"]
    coverage = payload["coverage"]
    _require(canonical(payload["outcome_counts"]) == canonical(pooled["scored_by_class"]), "109B outcome_counts = solo puntuadas")
    _require((coverage["total_input"], coverage["scored"], coverage["no_estimate"], coverage["label_missing"])
             == (pooled["input_total"], pooled["scored_settled"], pooled["no_estimate_input"], pooled["label_missing_estimate"]),
             "109B coverage")


def _model_reconciliation(data: dict, rows: list[Row]) -> tuple[str, list[dict]]:
    """Brier y log loss reconstruidos fila a fila frente a los de 109A de cada pliegue (misma fórmula, mismo orden, misma
    división): igualdad EXACTA o NOT_RECONCILED, y entonces el monitor omite el error del modelo."""
    folds, status = [], RECONCILED
    for k, group in enumerate(data["fold_groups"]):
        scored = [r for r in rows if r.fold_index == k and r.brier is not None]
        model = None if group["oos"] is None else group["oos"]["model"]
        if scored:
            mine = {"scored": len(scored), "brier": sum(r.brier for r in scored) / len(scored),
                    "log_loss": sum(r.log_loss for r in scored) / len(scored)}
        else:
            mine = {"scored": 0, "brier": None, "log_loss": None}
        ok = (model is None and not scored) or (model is not None and canonical(model) == canonical(mine))
        status = status if ok else NOT_RECONCILED
        folds.append({"fold_index": k, "oos_model": model, "reconstructed": mine, "status": RECONCILED if ok else NOT_RECONCILED})
    return status, folds


def _int_value(value) -> int:
    """Un int exacto o el {"INT_HEX_V1": "<signo><hex>"} canónico de 109B (seed enorme)."""
    if type(value) is int:
        return value
    text = value[INT_ENCODING]
    if type(text) is not str or len(text) < 2 or text[0] not in "+-" or text[1] == "0" or any(c not in _HEX_DIGITS for c in text[1:]):
        raise MonitoringIntegrityError("config 109B: INT_HEX_V1 no canónico en un informe auditado")
    number = int(text[1:], 16)
    number = -number if text[0] == "-" else number
    _require(not -FIXTURE_ID_LIMIT < number < FIXTURE_ID_LIMIT, "INT_HEX_V1 solo para |n| >= 10**640")
    return number


# --------------------------------------------------------------------------------------------------------- cohorte

def _rejected(input_digest: str, report_digest: str | None, reasons: list[str]) -> dict:
    return {"status": REJECTED, "reasons": sorted(set(reasons)), "input_digest": input_digest, "report_digest": report_digest}


def load_cohort(report, what: str) -> dict:
    """Una cohorte VERIFIED lista para medir, o REJECTED con sus motivos. Un valor que no es JSON nativo exacto es
    MonitoringInputError; una evidencia JSON que no supera la auditoría es REJECTED (nunca un resumen inventado)."""
    if type(report) is not dict:
        raise MonitoringInputError(f"{what}: se espera un dict exacto devuelto por run_goals_research_consumer, no {describe(report)}")
    data = plain_json_copy(report, what)
    input_digest = digest(data)
    stated = data.get("report_digest")
    report_digest = stated if type(stated) is str else None
    reason = _precheck(data)
    if reason:
        return _rejected(input_digest, report_digest, [reason])
    try:
        audit = aggregate_goals_consumer_reports([data])
    except GoalsConsumerError as exc:
        raise MonitoringInputError(f"{what}: el consumidor V1 rechazó la estructura: {exc.args[0]}") from exc
    (entry,) = audit["inputs"]
    _require(entry["input_digest"] == input_digest, "input_digest del consumidor")
    if entry["status"] != CONSUMER_VERIFIED:
        return _rejected(input_digest, report_digest, list(entry["reasons"]))

    target = data["target"]
    classes = tuple(target["classes"])
    rows = []
    for e in data["ledger"]:
        estimate = e["estimate_status"] == ESTIMATE
        scores = row_scores(e["probabilities"], e["label"], classes) if estimate and e["label"] is not None else (None, None)
        rows.append(Row(e["fixture_id"], e["fold_index"], e["week"], estimate, e["label"], e["label_missing_reason"], *scores))
    _reconcile(data, rows, classes)
    model_status, model_folds = _model_reconciliation(data, rows)
    payload = data["reliability_reports"]["pooled"]
    config = payload["config"]
    windows = [(utc_instant(g["holdout_start"])[0], utc_instant(g["holdout_end"])[0], g["holdout_start"], g["holdout_end"])
               for g in data["fold_groups"]]
    # por instante, no por texto: la fracción de microsegundos es opcional en el texto canónico
    kickoffs = [text for _, text in sorted((utc_instant(e["kickoff_at"])[0], e["kickoff_at"]) for e in data["ledger"])]
    return {
        "status": VERIFIED, "reasons": [], "input_digest": input_digest, "report_digest": data["report_digest"],
        "consumer": {"consumer_id": data["consumer_id"], "consumer_version": data["consumer_version"],
                     "contract_version": data["contract_version"]},
        "producer": data["producer"], "target": target, "classes": classes, "sources": data["sources"],
        "config_identity": payload["config_identity"],
        "bootstrap": {"seed": _int_value(config["seed"]), "iterations": config["iterations"], "min_weeks": config["min_weeks"]},
        "windows": windows, "first_kickoff_at": kickoffs[0] if kickoffs else None, "last_kickoff_at": kickoffs[-1] if kickoffs else None,
        "rows": rows, "fixtures": frozenset(r.fixture_id for r in rows), "weeks": sorted({r.week for r in rows}),
        "denominators": data["pooled"]["denominators"], "model_status": model_status, "model_folds": model_folds,
        "reliability": payload,
    }
