"""Agregación V1 de informes del consumidor sobre poblaciones de holdout DISJUNTAS y comparables (puro).

Cada informe se audita por completo antes de agregarlo: JSON nativo exacto, versión del consumidor, report_digest,
identidades de 109A y 109B recalculadas, libro mayor revalidado fila a fila, 109B re-ejecutado sobre ese libro (por pliegue
y agregado) con la misma identidad, y el cuerpo entero reconstruido con consumer.assemble idéntico al recibido. Después se
exige el mismo productor y versión, mercado, línea, versiones de fuente y configuración de 109B, y fixture_id disjuntos.
Lo que no cumple da NON_COMPARABLE con sus motivos (nunca un agregado inventado); las sumas son de cuentas, nunca de tasas.
"""

import math

from research.goals_calibration_uncertainty import (BINNING_VERSION, DIAGNOSTIC_VERSION, INT_ENCODING, UncertaintyConfig)
from research.goals_consumer_v1.consumer import (CALIBRATION_CLAIM, CLASSIFICATIONS, LABEL_MISSING_REASONS, LEDGER_FIELDS, LIMITATIONS,
                                                 REPORT_FIELDS, RESULT_NON_COMPARABLE, SAMPLE_ID_PREFIX, assemble, canonical, denominators,
                                                 digest, iso_week, oos_identity_ok, parse_utc, reliability_identity,
                                                 reliability_summary, result_status, run_reliability, target_info)
from research.goals_consumer_v1.contract import (CONSUMER_ID, CONSUMER_VERSION, CONTRACT_VERSION, ESTIMATE, FIXTURE_ID_LIMIT,
                                                 NO_ESTIMATE, ConsumerInputError, GoalsConsumerError, describe, exact_keys, fixture_id,
                                                 identifier, probabilities, sequence, target_classes)
from research.goals_oos_evaluation import EVALUATOR_VERSION, UNVERIFIED_RESEARCH_ONLY, WAIT_REAL_SOURCE

AGGREGATE_ID = "GOALS_RESEARCH_CONSUMER_AGGREGATE"
VERIFIED, REJECTED = "VERIFIED", "REJECTED"
# motivos de NON_COMPARABLE
EVIDENCE_NOT_AUDITABLE = "EVIDENCE_NOT_AUDITABLE"
EVIDENCE_DIGEST_MISMATCH = "EVIDENCE_DIGEST_MISMATCH"
SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
CONSUMER_VERSION_MISMATCH = "CONSUMER_VERSION_MISMATCH"
SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
PRODUCER_MISMATCH = "PRODUCER_MISMATCH"
PRODUCER_VERSION_MISMATCH = "PRODUCER_VERSION_MISMATCH"
MARKET_MISMATCH = "MARKET_MISMATCH"
LINE_MISMATCH = "LINE_MISMATCH"
RELIABILITY_CONFIG_MISMATCH = "RELIABILITY_CONFIG_MISMATCH"
DUPLICATE_REPORT = "DUPLICATE_REPORT"
OVERLAPPING_FIXTURES = "OVERLAPPING_FIXTURES"

MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 20_000_000
_UNCERTAINTY_FIELDS = ("seed", "iterations", "bin_count", "min_bin_support", "min_total_support", "min_weeks")
_HEX_DIGITS = frozenset("0123456789abcdef")
# errores esperables al auditar un JSON nativo que no es un informe íntegro (nunca código del llamador: ya se comprobó el tipo)
_NOT_AUDITABLE = (GoalsConsumerError, ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError)


class _Rejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _plain_json(value, what: str) -> None:
    """Solo dict (claves str exactas), list, tuple, str, int (|n| < 10**640), float finito, bool y None EXACTOS."""
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ConsumerInputError(f"{what}: estructura demasiado profunda o grande para un informe del consumidor")
        kind = type(item)
        if item is None or kind is bool or kind is str:
            continue
        if kind is int:
            if not -FIXTURE_ID_LIMIT < item < FIXTURE_ID_LIMIT:
                raise ConsumerInputError(f"{what}: int fuera de rango en un informe: {describe(item)}")
        elif kind is float:
            if not math.isfinite(item):
                raise ConsumerInputError(f"{what}: float no finito en un informe: {describe(item)}")
        elif kind is list or kind is tuple:
            stack.extend((x, depth + 1) for x in item)
        elif kind is dict:
            for k, x in item.items():
                if type(k) is not str:
                    raise ConsumerInputError(f"{what}: clave no str exacta en un informe: {describe(k)}")
                stack.append((x, depth + 1))
        else:
            raise ConsumerInputError(f"{what}: valor que no es JSON nativo exacto: {describe(item)}")


def _int_value(value) -> int:
    """int exacto o {"INT_HEX_V1": "<signo><hex>"} canónico de 109B (solo para |n| >= 10**640)."""
    if type(value) is int:
        return value
    exact_keys(value, (INT_ENCODING,), "config 109B", ConsumerInputError)
    text = value[INT_ENCODING]
    if (type(text) is not str or len(text) < 2 or text[0] not in "+-" or text[1] == "0"
            or any(ch not in _HEX_DIGITS for ch in text[1:])):
        raise ConsumerInputError("config 109B: INT_HEX_V1 no canónico")
    number = int(text[1:], 16)  # base potencia de 2: sin límite de cifras decimales
    number = -number if text[0] == "-" else number
    if -FIXTURE_ID_LIMIT < number < FIXTURE_ID_LIMIT:
        raise ConsumerInputError("config 109B: INT_HEX_V1 para un int que se escribe en decimal")
    return number


def _uncertainty_config(payload: dict) -> UncertaintyConfig:
    config = payload["config"]
    exact_keys(config, _UNCERTAINTY_FIELDS, "config 109B", ConsumerInputError)
    return UncertaintyConfig(**{name: _int_value(config[name]) for name in _UNCERTAINTY_FIELDS})


def _ledger_row(entry, classes: tuple[str, ...]) -> dict:
    exact_keys(entry, LEDGER_FIELDS, "libro mayor", ConsumerInputError)
    fid = fixture_id(entry["fixture_id"], "libro mayor")
    kickoff = parse_utc(entry["kickoff_at"])
    status, probs, label, reason = entry["estimate_status"], entry["probabilities"], entry["label"], entry["label_missing_reason"]
    ok = (entry["sample_id"] == f"{SAMPLE_ID_PREFIX}{fid}" and type(entry["fold_index"]) is int and entry["week"] == iso_week(kickoff)
          and type(status) is str and status in (ESTIMATE, NO_ESTIMATE)
          and ((label is None and type(reason) is str and reason in LABEL_MISSING_REASONS)
               or (reason is None and type(label) is str and label in classes)))
    if not ok:
        raise ConsumerInputError("libro mayor: fila incoherente")
    if status == ESTIMATE:
        probabilities(probs, classes, "libro mayor")
        if any(type(p) is not float for p in probs.values()):
            raise ConsumerInputError("libro mayor: probabilidades no normalizadas a float")
    elif probs is not None:
        raise ConsumerInputError("libro mayor: NO_ESTIMATE con probabilidades")
    return entry


def _audit(report) -> dict:
    """Las piezas comparables de un informe íntegro, o _Rejected(motivo)."""
    if set(report) != set(REPORT_FIELDS):
        raise _Rejected(EVIDENCE_NOT_AUDITABLE)
    if (report["consumer_id"], report["consumer_version"], report["contract_version"]) != (CONSUMER_ID, CONSUMER_VERSION, CONTRACT_VERSION):
        raise _Rejected(CONSUMER_VERSION_MISMATCH)
    if digest({k: v for k, v in report.items() if k != "report_digest"}) != report["report_digest"]:
        raise _Rejected(EVIDENCE_DIGEST_MISMATCH)
    oos, reliability = report["oos_report"], report["reliability_reports"]
    fold_payloads, pooled_payload = reliability["folds"], reliability["pooled"]
    payloads = list(fold_payloads) + [pooled_payload]
    if not oos_identity_ok(oos) or any(reliability_identity(p) != p["report_identity"] for p in payloads):
        raise _Rejected(SOURCE_IDENTITY_MISMATCH)
    if oos["evaluator_version"] != EVALUATOR_VERSION or any(
            (p["diagnostic_version"], p["binning_version"]) != (DIAGNOSTIC_VERSION, BINNING_VERSION) for p in payloads):
        raise _Rejected(SOURCE_VERSION_MISMATCH)

    producer, target = report["producer"], report["target"]
    producer_id = identifier(producer["producer_id"], "informe.producer")
    producer_version = identifier(producer["producer_version"], "informe.producer")
    market, line = target["market"], target["line"]
    _, classes = target_classes(market, line, "informe.target")
    ledger = [_ledger_row(e, classes) for e in sequence(report["ledger"], "libro mayor", ConsumerInputError)]
    config = _uncertainty_config(pooled_payload)
    # 109B re-ejecutado sobre el libro mayor: la misma identidad o la evidencia no es auditable
    rerun = [run_reliability([e for e in ledger if e["fold_index"] == k], market, line, config) for k in range(len(fold_payloads))]
    rerun.append(run_reliability(ledger, market, line, config))
    if [p["report_identity"] for p in rerun] != [p["report_identity"] for p in payloads]:
        raise _Rejected(EVIDENCE_NOT_AUDITABLE)
    rebuilt = assemble(producer_id, producer_version, market, line, oos["counts"]["rows"], oos, ledger, list(fold_payloads), pooled_payload)
    if canonical({**rebuilt, "report_digest": report["report_digest"]}) != canonical(report):
        raise _Rejected(EVIDENCE_NOT_AUDITABLE)
    return {"producer": rebuilt["producer"], "target": rebuilt["target"], "config": config,
            "config_identity": pooled_payload["config_identity"], "ledger": ledger, "classes": classes}


def _input(report, i: int) -> dict:
    what = f"informe {i}"
    if type(report) is not dict:
        raise ConsumerInputError(f"{what}: se espera un dict exacto devuelto por run_goals_research_consumer, no {describe(report)}")
    _plain_json(report, what)
    stated = report.get("report_digest")
    entry = {"input_digest": digest(report), "report_digest": stated if type(stated) is str else None}
    try:
        parsed = _audit(report)
    except _Rejected as rejected:
        return {**entry, "status": REJECTED, "reasons": [rejected.reason], "parsed": None}
    except _NOT_AUDITABLE:
        return {**entry, "status": REJECTED, "reasons": [EVIDENCE_NOT_AUDITABLE], "parsed": None}
    return {**entry, "status": VERIFIED, "reasons": [], "parsed": parsed}


def _cross_reasons(verified: list[dict]) -> set[str]:
    reasons = set()
    first = verified[0]["parsed"]
    for item in verified[1:]:
        p = item["parsed"]
        if p["producer"]["producer_id"] != first["producer"]["producer_id"]:
            reasons.add(PRODUCER_MISMATCH)
        elif p["producer"]["producer_version"] != first["producer"]["producer_version"]:
            reasons.add(PRODUCER_VERSION_MISMATCH)
        if p["target"]["market"] != first["target"]["market"]:
            reasons.add(MARKET_MISMATCH)
        elif p["target"]["line"] != first["target"]["line"]:
            reasons.add(LINE_MISMATCH)
        if p["config_identity"] != first["config_identity"]:
            reasons.add(RELIABILITY_CONFIG_MISMATCH)
    digests = [item["input_digest"] for item in verified]
    if len(set(digests)) != len(digests):
        reasons.add(DUPLICATE_REPORT)
    seen: set[int] = set()
    for item in verified:
        ids = {e["fixture_id"] for e in item["parsed"]["ledger"]}
        if ids & seen:
            reasons.add(OVERLAPPING_FIXTURES)
        seen |= ids
    return reasons


def aggregate_goals_consumer_reports(reports) -> dict:
    """Agregado V1 de informes del consumidor (JSON-serializable, con aggregate_digest). Ver research.goals_consumer_v1."""
    seq = sequence(reports, "informes", ConsumerInputError)
    if not seq:
        raise ConsumerInputError("informes: se espera al menos un informe del consumidor")
    inputs = sorted((_input(r, i) for i, r in enumerate(seq)), key=lambda e: e["input_digest"])
    reasons = {reason for e in inputs for reason in e["reasons"]}
    verified = [e for e in inputs if e["status"] == VERIFIED]
    if not reasons:
        reasons = _cross_reasons(verified)

    body = {
        "aggregate_id": AGGREGATE_ID, "consumer_id": CONSUMER_ID, "consumer_version": CONSUMER_VERSION,
        "contract_version": CONTRACT_VERSION, "evidence_status": UNVERIFIED_RESEARCH_ONLY, "source_status": WAIT_REAL_SOURCE,
        "calibration_claim": CALIBRATION_CLAIM, "classifications": list(CLASSIFICATIONS), "limitations": list(LIMITATIONS),
        "inputs": [{k: e[k] for k in ("input_digest", "report_digest", "status", "reasons")} for e in inputs],
        "result": {"status": RESULT_NON_COMPARABLE, "reasons": sorted(reasons)},
        "producer": None, "target": None, "fixtures": None, "denominators": None, "reliability": None, "reliability_report": None,
    }
    if not reasons:
        first = verified[0]["parsed"]
        market, line = first["target"]["market"], first["target"]["line"]
        ledger = sorted((e for item in verified for e in item["parsed"]["ledger"]), key=lambda e: e["fixture_id"])
        den = denominators(ledger, first["classes"])
        payload = run_reliability(ledger, market, line, first["config"])
        body.update(result=result_status(den), producer=first["producer"], target=target_info(market, line), fixtures=len(ledger),
                    denominators=den, reliability=reliability_summary(payload), reliability_report=payload)
    return {**body, "aggregate_digest": digest(body)}
