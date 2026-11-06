"""Integración V1 de 109A (evaluación OOS walk-forward) y 109B (fiabilidad e incertidumbre) sobre la MISMA población de
holdout, con denominadores conciliados contra las dos fuentes (puro). Ver research.goals_consumer_v1."""

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from research.goals_calibration_uncertainty import (BINNING_VERSION, DIAGNOSTIC_ID, DIAGNOSTIC_VERSION, INT_ENCODING,
                                                     canonical_report_json, goals_reliability_uncertainty)
from research.goals_calibration_uncertainty import InputError as ReliabilityInputError
from research.goals_consumer_v1.contract import (CONSUMER_ID, CONSUMER_VERSION, CONTRACT_VERSION, ESTIMATE,
                                                 FIXTURE_ID_DECIMAL_DIGITS, MODEL_ID_SEPARATOR, NO_ESTIMATE, ConsumerIntegrityError,
                                                 ConsumerSourceError, target_classes, validate_config, validate_observations)
from research.goals_oos_evaluation import (EVALUATOR_ID, EVALUATOR_VERSION, OUTCOME_OBSERVED_AT_MISSING, UNVERIFIED_RESEARCH_ONLY,
                                           WAIT_REAL_SOURCE, OOSInputError, evaluate_goals_oos)
from research.m58.goal_labels import FULLTIME_INVALID, label_btts, label_over_under
from research.m58.labels import FULLTIME_INCOMPLETE, NOT_FINISHED
from research.m59.canonical import sha256_hex
from research.quote_temporal_eligibility import BTTS, SETTLEMENT_CONVENTION_FT90

SAMPLE_ID_PREFIX = "fixture:"
WEEK_DERIVATION = "ISO_WEEK_OF_KICKOFF_AT_UTC"
LABEL_MISSING_REASONS = tuple(sorted((OUTCOME_OBSERVED_AT_MISSING, NOT_FINISHED, FULLTIME_INCOMPLETE, FULLTIME_INVALID)))

RESULT_SCORED, RESULT_NO_ESTIMATE, RESULT_NON_COMPARABLE = "SCORED", "NO_ESTIMATE", "NON_COMPARABLE"
NO_HOLDOUT_ROWS, NO_SETTLED_OUTCOMES, ALL_ABSTAINED = "NO_HOLDOUT_ROWS", "NO_SETTLED_OUTCOMES", "ALL_ABSTAINED"
VERIFIED_DISJOINT = "VERIFIED_DISJOINT"

CLASSIFICATIONS = ("RESEARCH_ONLY", "SYNTHETIC_CALLER_SUPPLIED", "NOT_CERTIFIED", WAIT_REAL_SOURCE)
CALIBRATION_CLAIM = "NONE_RELIABILITY_AND_ECE_ARE_DESCRIPTIVE_NOT_PROOF_OF_MODEL_CALIBRATION"
LIMITATIONS = (
    "SYNTHETIC_CALLER_SUPPLIED_OBSERVATIONS_NOT_DI_ADMITTED",
    "SOURCE_IDENTITIES_ARE_INTEGRITY_DIGESTS_NOT_AUTHENTICITY",
    "WEEK_BLOCKS_DERIVED_FROM_CALLER_KICKOFF_AT",
    "NOT_TRUE_PIT_CERTIFIED",
    "NO_CALIBRATION_CERTIFICATE_OR_PASS_THRESHOLD",
    "OOS_BRIER_LOG_LOSS_AND_BASE_RATE_COMPARISONS_PER_FOLD_ONLY_NEVER_POOLED_OR_AVERAGED",
    "LEDGER_LABELS_RECONCILED_BY_COUNTS_NOT_RESETTLED_FROM_SCORES_ON_AGGREGATION",
    "QUARTER_LINES_AND_1X2_UNSUPPORTED",
    "NO_BETTING_VALUE_OR_PRODUCTION_ELIGIBILITY",
)
COUNT_SEMANTICS = {
    "denominators.settled_by_class": "ALL_SETTLED_ESTIMATE_AND_NO_ESTIMATE",
    "denominators.scored_by_class": "SETTLED_ESTIMATE_ONLY",
    "denominators.abstained_by_class": "SETTLED_NO_ESTIMATE_ONLY",
    "oos_report.folds[].groups[].outcome_counts": "ALL_SETTLED_ESTIMATE_AND_NO_ESTIMATE",
    "reliability_reports.outcome_counts": "SETTLED_ESTIMATE_ONLY",
    "reliability_reports.coverage.label_missing": "ESTIMATE_WITHOUT_LABEL_ONLY",
    "reliability_reports.coverage.no_estimate": "ALL_NO_ESTIMATE_WITH_OR_WITHOUT_LABEL",
}
REPORT_FIELDS = ("consumer_id", "consumer_version", "contract_version", "evidence_status", "source_status", "calibration_claim",
                 "classifications", "limitations", "producer", "target", "compatibility", "sources", "counts", "fold_groups", "pooled",
                 "ledger", "oos_report", "reliability_reports", "report_digest")
LEDGER_FIELDS = ("sample_id", "fixture_id", "fold_index", "kickoff_at", "week", "estimate_status", "probabilities", "label",
                 "label_missing_reason")
_COUNT_FIELDS = ("input_total", "estimate_input", "no_estimate_input", "label_missing_input", "label_missing_estimate",
                 "label_missing_no_estimate", "settled_total", "scored_settled", "abstained_settled")
_CLASS_FIELDS = ("settled_by_class", "scored_by_class", "abstained_by_class")


# ------------------------------------------------------------------------------------------------ JSON e identidades

def canonical(value) -> str:
    """JSON canónico del consumidor: claves ordenadas, sin espacios, ASCII, floats por repr; NaN/inf prohibidos."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def oos_identity_ok(report: dict) -> bool:
    """El report_digest y el config_digest de 109A se recalculan con su propia canonización (research.m59.canonical)."""
    body = {k: v for k, v in report.items() if k != "report_digest"}
    return sha256_hex(body) == report["report_digest"] and sha256_hex(report["config"]) == report["config_digest"]


def reliability_identity(payload: dict) -> str:
    """report_identity de 109B según su contrato: sha256 de canonical_report_json sin ese campo (INT_HEX_V1 intacto)."""
    return digest({k: v for k, v in payload.items() if k != "report_identity"})


def _require(condition: bool, check: str) -> None:
    if not condition:
        raise ConsumerIntegrityError(f"conciliación o identidad fallida: {check}")


def _same(a, b) -> bool:
    """Igualdad por JSON canónico: 2 y 2.0, o una lista y una tupla con otro contenido, no son lo mismo."""
    return canonical(a) == canonical(b)


def iso(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).isoformat()


def iso_week(instant: datetime) -> str:
    year, week, _ = instant.astimezone(timezone.utc).isocalendar()
    return f"{year:04d}-W{week:02d}"


def parse_utc(text: str) -> datetime:
    """Un instante UTC escrito por iso(); cualquier otra forma es ConsumerIntegrityError."""
    _require(type(text) is str, "instante ISO")
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise ConsumerIntegrityError("conciliación o identidad fallida: instante ISO no válido") from None
    _require(value.tzinfo is not None and value.utcoffset().total_seconds() == 0 and iso(value) == text, "instante ISO UTC canónico")
    return value


# ------------------------------------------------------------------------------------------- liquidación y libro mayor

def settle(outcome: dict, market: str, line) -> tuple[str | None, str | None]:
    """La liquidación de 109A con las funciones publicadas: sin observed_at no hay etiqueta auditable. PUSH es un resultado
    liquidado (línea entera igualada); un partido no terminado (CANC, ABD, PST... "void") es status_not_finished, nunca PUSH."""
    if outcome["observed_at"] is None:
        return None, OUTCOME_OBSERVED_AT_MISSING
    if market == BTTS:
        return label_btts(outcome["status_short"], outcome["fulltime_home"], outcome["fulltime_away"])
    return label_over_under(outcome["status_short"], outcome["fulltime_home"], outcome["fulltime_away"], line)


def ledger_entry(row: dict, fold_index: int, market: str, line) -> dict:
    label, reason = settle(row["outcome"], market, line)
    probs = row["probabilities"]
    return {"sample_id": f"{SAMPLE_ID_PREFIX}{row['fixture_id']}", "fixture_id": row["fixture_id"], "fold_index": fold_index,
            "kickoff_at": iso(row["kickoff_at"]), "week": iso_week(row["kickoff_at"]), "estimate_status": row["status"],
            "probabilities": None if probs is None else {k: float(p) + 0.0 for k, p in probs.items()},  # -0.0 -> 0.0; 1 -> 1.0
            "label": label, "label_missing_reason": reason}


# ---------------------------------------------------------------------------------------------------- denominadores

def denominators(entries: list[dict], classes: tuple[str, ...]) -> dict:
    """Cuentas auditables fila a fila (nunca deducidas de agregados)."""
    den = {name: 0 for name in _COUNT_FIELDS}
    by_class = {name: {c: 0 for c in classes} for name in _CLASS_FIELDS}
    reasons = Counter()
    for e in entries:
        estimate = e["estimate_status"] == ESTIMATE
        den["input_total"] += 1
        den["estimate_input" if estimate else "no_estimate_input"] += 1
        if e["label"] is None:
            den["label_missing_input"] += 1
            den["label_missing_estimate" if estimate else "label_missing_no_estimate"] += 1
            reasons[e["label_missing_reason"]] += 1
        else:
            den["settled_total"] += 1
            den["scored_settled" if estimate else "abstained_settled"] += 1
            by_class["settled_by_class"][e["label"]] += 1
            by_class["scored_by_class" if estimate else "abstained_by_class"][e["label"]] += 1
    den.update(by_class)
    den["label_missing_by_reason"] = dict(sorted(reasons.items()))
    check_denominators(den, classes)
    return den


def sum_denominators(dens: list[dict], classes: tuple[str, ...]) -> dict:
    """Suma de poblaciones disjuntas ya verificadas: cuentas, nunca tasas."""
    total = {name: sum(d[name] for d in dens) for name in _COUNT_FIELDS}
    for name in _CLASS_FIELDS:
        total[name] = {c: sum(d[name][c] for d in dens) for c in classes}
    reasons = Counter()
    for d in dens:
        reasons.update(d["label_missing_by_reason"])
    total["label_missing_by_reason"] = dict(sorted(reasons.items()))
    check_denominators(total, classes)
    return total


def check_denominators(den: dict, classes: tuple[str, ...]) -> None:
    s, sc, ab = den["settled_by_class"], den["scored_by_class"], den["abstained_by_class"]
    _require(den["input_total"] == den["estimate_input"] + den["no_estimate_input"], "input_total = estimate + no_estimate")
    _require(den["input_total"] == den["settled_total"] + den["label_missing_input"], "input_total = settled + label_missing")
    _require(den["label_missing_input"] == den["label_missing_estimate"] + den["label_missing_no_estimate"]
             == sum(den["label_missing_by_reason"].values()), "label_missing = estimate + no_estimate = suma por motivo")
    _require(den["settled_total"] == den["scored_settled"] + den["abstained_settled"], "settled = scored + abstained")
    _require(den["estimate_input"] == den["scored_settled"] + den["label_missing_estimate"], "estimate = scored + label_missing_estimate")
    _require(den["no_estimate_input"] == den["abstained_settled"] + den["label_missing_no_estimate"],
             "no_estimate = abstained + label_missing_no_estimate")
    _require(all(s[c] == sc[c] + ab[c] for c in classes), "settled_by_class = scored_by_class + abstained_by_class")
    _require(sum(s.values()) == den["settled_total"] and sum(sc.values()) == den["scored_settled"]
             and sum(ab.values()) == den["abstained_settled"], "sumas por clase")


def _int_is(actual, expected: int) -> bool:
    return type(actual) is int and actual == expected


def _counts_are(actual, expected: dict) -> bool:
    return (type(actual) is dict and len(actual) == len(expected)
            and all(type(k) is str and k in expected and _int_is(v, expected[k]) for k, v in actual.items()))


def reconcile_oos(den: dict, group, classes: tuple[str, ...]) -> None:
    """109A: outcome_counts son TODAS las liquidadas (NO_ESTIMATE incluidas): se comparan con settled_by_class."""
    if group is None:
        _require(den["input_total"] == 0, "109A sin grupo del objetivo con filas de holdout")
        return
    for source, name in (("holdout_rows", "input_total"), ("holdout_estimate", "estimate_input"),
                         ("holdout_no_estimate", "no_estimate_input"), ("holdout_settled", "settled_total")):
        _require(_int_is(group[source], den[name]), f"109A {source} = {name}")
    _require(_counts_are(group["holdout_excluded_by_reason"], den["label_missing_by_reason"]),
             "109A holdout_excluded_by_reason = label_missing_by_reason")
    _require(_counts_are(group["outcome_counts"], den["settled_by_class"]), "109A outcome_counts (todas las liquidadas) = settled_by_class")
    _require(_int_is(group["model"]["scored"], den["scored_settled"]), "109A model.scored = scored_settled")
    coverage = group["coverage"]
    _require(_int_is(coverage["settled"], den["settled_total"]) and _int_is(coverage["model_scored"], den["scored_settled"])
             and _int_is(coverage["abstained_settled"], den["abstained_settled"]), "109A coverage = settled / scored / abstained")


def reconcile_reliability(den: dict, payload: dict, classes: tuple[str, ...]) -> None:
    """109B: outcome_counts son SOLO las puntuadas y label_missing solo las ESTIMATE sin etiqueta."""
    coverage = payload["coverage"]
    _require(_int_is(coverage["total_input"], den["input_total"]), "109B total_input = input_total")
    _require(_int_is(coverage["no_estimate"], den["no_estimate_input"]), "109B no_estimate = no_estimate_input")
    _require(_int_is(coverage["label_missing"], den["label_missing_estimate"]), "109B label_missing = label_missing_estimate")
    _require(_int_is(coverage["scored"], den["scored_settled"]), "109B scored = scored_settled")
    _require(_counts_are(payload["outcome_counts"], den["scored_by_class"]), "109B outcome_counts (solo puntuadas) = scored_by_class")


def result_status(den: dict) -> dict:
    if not den["input_total"]:
        return {"status": RESULT_NO_ESTIMATE, "reasons": [NO_HOLDOUT_ROWS]}
    if not den["settled_total"]:
        return {"status": RESULT_NO_ESTIMATE, "reasons": [NO_SETTLED_OUTCOMES]}
    if not den["scored_settled"]:
        return {"status": RESULT_NO_ESTIMATE, "reasons": [ALL_ABSTAINED]}
    return {"status": RESULT_SCORED, "reasons": []}


# ---------------------------------------------------------------------------------------------------------- fuentes

def run_oos(rows: list[dict], cfg: dict) -> dict:
    observations = [dict(r, probabilities=None if r["probabilities"] is None else dict(r["probabilities"]), outcome=dict(r["outcome"]))
                    for r in rows]
    config = {"model_id": cfg["model_id"], "origins": cfg["origins"], "holdout_length": cfg["holdout_length"],
              "calibration_length": cfg["calibration_length"], "embargo": cfg["embargo"]}
    try:
        return evaluate_goals_oos(observations, config)
    except OOSInputError as exc:
        raise ConsumerSourceError(EVALUATOR_ID, EVALUATOR_VERSION, exc) from exc


def run_reliability(entries: list[dict], market: str, line, config) -> dict:
    """109B sobre las filas del libro mayor; el informe se devuelve como su JSON canónico publicado, sin reescribirlo."""
    rows = [{"sample_id": e["sample_id"], "fixture_id": e["fixture_id"], "week": e["week"], "market": market, "line": line,
             "estimate_status": e["estimate_status"], "probabilities": None if e["probabilities"] is None else dict(e["probabilities"]),
             "label": e["label"]} for e in entries]
    try:
        report = goals_reliability_uncertainty(rows, market=market, line=line, config=config)
    except ReliabilityInputError as exc:
        raise ConsumerSourceError(DIAGNOSTIC_ID, DIAGNOSTIC_VERSION, exc) from exc
    payload = json.loads(canonical_report_json(report))
    _require(payload["report_identity"] == report.report_identity == reliability_identity(payload), "109B report_identity")
    return payload


def oos_group(fold: dict, market: str, line):
    found = [g for g in fold["groups"] if g["market"] == market and g["line"] == line]
    _require(len(found) <= 1, "109A grupo del objetivo único por pliegue")
    return found[0] if found else None


def reliability_summary(payload: dict) -> dict:
    return {"report_identity": payload["report_identity"], "dataset_identity": payload["dataset_identity"],
            "config_identity": payload["config_identity"], "metric_basis": payload["metric_basis"], "ece_method": payload["ece_method"],
            "ece": payload["ece"], "sample_status": payload["sample_status"], "outcome_status": payload["outcome_status"],
            "uncertainty_status": payload["uncertainty"]["status"], "calibration_status": payload["calibration_status"]}


def target_info(market: str, line) -> dict:
    line_type, classes = target_classes(market, line, "objetivo")
    return {"market": market, "line": line, "line_type": line_type, "classes": list(classes),
            "settlement_convention": SETTLEMENT_CONVENTION_FT90, "week_derivation": WEEK_DERIVATION, "sample_id_prefix": SAMPLE_ID_PREFIX}


# --------------------------------------------------------------------------------------------------------- ensamblado

def assemble(producer_id: str, producer_version: str, market: str, line, input_rows: int, oos: dict, ledger: list[dict],
             fold_payloads: list[dict], pooled_payload: dict) -> dict:
    """El cuerpo del informe a partir de las fuentes y el libro mayor, verificando cada identidad y cada conciliación.
    Lo usan la ejecución y la auditoría de un informe recibido: los dos caminos producen exactamente el mismo cuerpo."""
    target = target_info(market, line)
    classes = tuple(target["classes"])
    model_id = f"{producer_id}{MODEL_ID_SEPARATOR}{producer_version}"
    _require(oos["evaluator_id"] == EVALUATOR_ID and oos["evaluator_version"] == EVALUATOR_VERSION, "109A evaluador y versión")
    _require(oos["model_id"] == model_id == oos["config"]["model_id"], "109A model_id = productor@versión")
    _require(oos_identity_ok(oos), "109A report_digest y config_digest")
    counts = oos["counts"]
    _require(_int_is(counts["rows"], input_rows) and _int_is(counts["supported"], input_rows) and _int_is(counts["unsupported"], 0),
             "109A filas = entrada, todas admitidas")
    _require(_int_is(counts["supported_in_no_holdout"], input_rows - len(ledger)), "109A filas fuera de holdout = entrada - libro mayor")

    folds = oos["folds"]
    _require(len(folds) == len(fold_payloads) >= 1, "un informe 109B por pliegue")
    ids = [e["fixture_id"] for e in ledger]
    _require(ids == sorted(set(ids)), "libro mayor ordenado y sin fixture_id repetido")
    holdouts = [(parse_utc(f["boundaries"]["holdout_start"]), parse_utc(f["boundaries"]["holdout_end"])) for f in folds]
    for e in ledger:
        k = e["fold_index"]
        _require(type(k) is int and 0 <= k < len(folds) and holdouts[k][0] <= parse_utc(e["kickoff_at"]) < holdouts[k][1],
                 "fila del libro mayor dentro del holdout de su pliegue")

    payloads = fold_payloads + [pooled_payload]
    for p in payloads:
        _require(p["diagnostic_id"] == DIAGNOSTIC_ID and p["diagnostic_version"] == DIAGNOSTIC_VERSION
                 and p["binning_version"] == BINNING_VERSION, "109B diagnóstico, versión y binning")
        _require(p["market"] == market and p["line"] == line, "109B objetivo")
        _require(reliability_identity(p) == p["report_identity"], "109B report_identity")
        _require(p["config_identity"] == pooled_payload["config_identity"], "109B misma configuración en todos los informes")

    fold_groups, dens = [], []
    for k, (fold, payload, (start, end)) in enumerate(zip(folds, fold_payloads, holdouts)):
        _require(fold["fold_index"] == k, "109A orden de pliegues")
        den = denominators([e for e in ledger if e["fold_index"] == k], classes)
        group = oos_group(fold, market, line)
        reconcile_oos(den, group, classes)
        reconcile_reliability(den, payload, classes)
        dens.append(den)
        fold_groups.append({
            "fold_index": k, "fold_id": fold["fold_id"], "holdout_start": iso(start), "holdout_end": iso(end),
            "result": result_status(den), "denominators": den,
            "oos": None if group is None else copy.deepcopy({
                "group_status": group["status"], "metric_basis": group["metric_basis"], "base_rate_status": group["base_rate"]["status"],
                "model": group["model"], "base_rate_on_model_set": group["base_rate_on_model_set"],
                "base_rate_on_all_settled": group["base_rate_on_all_settled"]}),
            "reliability": reliability_summary(payload),
        })
    pooled_den = denominators(ledger, classes)
    _require(_same(pooled_den, sum_denominators(dens, classes)), "agregado = suma de pliegues disjuntos")
    reconcile_reliability(pooled_den, pooled_payload, classes)

    return {
        "consumer_id": CONSUMER_ID, "consumer_version": CONSUMER_VERSION, "contract_version": CONTRACT_VERSION,
        "evidence_status": UNVERIFIED_RESEARCH_ONLY, "source_status": WAIT_REAL_SOURCE, "calibration_claim": CALIBRATION_CLAIM,
        "classifications": list(CLASSIFICATIONS), "limitations": list(LIMITATIONS),
        "producer": {"producer_id": producer_id, "producer_version": producer_version, "model_id": model_id},
        "target": target,
        "compatibility": {"fixture_id_range": f"[1, 10**{FIXTURE_ID_DECIMAL_DIGITS})", "reliability_int_encoding": INT_ENCODING,
                          "count_semantics": dict(COUNT_SEMANTICS)},
        "sources": {
            "oos": {"source_id": EVALUATOR_ID, "source_version": EVALUATOR_VERSION, "model_id": oos["model_id"],
                    "report_digest": oos["report_digest"], "config_digest": oos["config_digest"],
                    "dataset_fingerprint": oos["dataset_fingerprint"]},
            "reliability": {"source_id": DIAGNOSTIC_ID, "source_version": DIAGNOSTIC_VERSION, "binning_version": BINNING_VERSION,
                            "config_identity": pooled_payload["config_identity"],
                            "fold_report_identities": [p["report_identity"] for p in fold_payloads],
                            "pooled_report_identity": pooled_payload["report_identity"]},
        },
        "counts": {"input_rows": input_rows, "holdout_rows": len(ledger), "outside_holdout_rows": input_rows - len(ledger),
                   "folds": len(folds)},
        "fold_groups": fold_groups,
        "pooled": {"result": result_status(pooled_den), "denominators": pooled_den, "folds_pooled": len(folds),
                   "fixture_disjointness": VERIFIED_DISJOINT, "reliability": reliability_summary(pooled_payload)},
        "ledger": ledger,
        "oos_report": oos,
        "reliability_reports": {"folds": fold_payloads, "pooled": pooled_payload},
    }


def run_goals_research_consumer(observations, config) -> dict:
    """Informe V1 integrado (JSON-serializable, con report_digest). Ver research.goals_consumer_v1."""
    cfg = validate_config(config)
    rows = validate_observations(observations, cfg)
    market, line = cfg["market"], cfg["line"]
    oos = run_oos(rows, cfg)  # 109A valida el tiempo, la fuga y la forma de los pliegues antes de que aquí se calcule nada
    holdouts = [(o.astimezone(timezone.utc), o.astimezone(timezone.utc) + cfg["holdout_length"]) for o in cfg["origins"]]
    ledger = []
    for r in rows:
        inside = [k for k, (start, end) in enumerate(holdouts) if start <= r["kickoff_at"] < end]
        _require(len(inside) <= 1, "holdouts disjuntos")
        if inside:
            ledger.append(ledger_entry(r, inside[0], market, line))
    ledger.sort(key=lambda e: e["fixture_id"])
    fold_payloads = [run_reliability([e for e in ledger if e["fold_index"] == k], market, line, cfg["reliability"])
                     for k in range(len(holdouts))]
    pooled_payload = run_reliability(ledger, market, line, cfg["reliability"])
    body = assemble(cfg["producer_id"], cfg["producer_version"], market, line, len(rows), oos, ledger, fold_payloads, pooled_payload)
    return {**body, "report_digest": digest(body)}
