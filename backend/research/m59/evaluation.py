"""Contrato de evaluación prospectiva (puro). Preparado, NO ejecutado sobre partidos reales.

Las etiquetas viven fuera del registro (se unen solo aquí). Cada partido del universo elegible
recibe exactamente una clase:
- NO_RECORD: no hay predicción (emisor caído, no emitido a tiempo): cuenta en el denominador;
- NOT_EMITTED: solo decisiones no_emission (motivo conservado): cuenta en el denominador;
- KICKOFF_ADVANCED: el kickoff final es anterior a emitted_at + 1 h (la predicción ya no era
  prepartido con el margen pactado): fuera, contado;
- UNANCHORED: sin anclaje externo verificado anterior al kickoff final: fuera, contado;
- NO_LABEL: sin etiqueta verificable en el horizonte de etiquetas: fuera, contado;
- INFORMATIVE / DEGRADED: evaluables (B2 solo en INFORMATIVE); estratos POSTPONED (kickoff final
  posterior al conocido al emitir) y UNSEEN_COMPETITION se marcan aparte.

Estado del registro (registry_status), tres comprobaciones separadas:
- CHAIN_VALID: la cadena es íntegra (seq, prev_hash, record_hash, ids únicos);
- ANCHOR_VERIFIED: hay al menos un anclaje externo verificado sobre ESTA cadena y ningún anclaje
  presentado falla (uno que apunte a un seq inexistente delata una cadena truncada);
- EVALUATION_ELIGIBLE = CHAIN_VALID y ANCHOR_VERIFIED. Una cadena íntegra sin anclaje NO basta.
  Además, cada predicción tiene que estar cubierta por un anclaje anterior a su kickoff final.

Coberturas (coverage):
- operativa: denominador = TODOS los partidos elegibles para emisión (con o sin etiqueta);
  mide si el sistema emitió (cualquier predicción) y si emitió informativa;
- evaluable: denominador = partidos elegibles CON etiqueta; numerador = INFORMATIVE. Es la que
  hoy usa el umbral confirmatorio (informative_coverage_min = 0,60), pendiente de decisión
  gerencial antes del preregistro definitivo.
"""

from collections import Counter
from datetime import datetime, timedelta

from research.m58 import metrics
from research.m58.protocol import TestGate, config_hash
from research.m59.anchors import AnchorError, anchored_before, verify_anchor
from research.m59.registry import RegistryError, verify

LABEL_HORIZON = timedelta(hours=72)
MIN_LABELLED = 2000
PREREGISTRATION = {
    "primary_metric": "log_loss",
    "secondary": ["brier", "rps", "calibration_slope_by_class", "ece", "informative_coverage"],
    "comparisons": ["B2-B1", "B2-B0"],
    "ci": "95% block bootstrap by ISO week, 1000 resamples, seed 58",
    "thresholds": {"B2_minus_B1_ci_upper_below": 0.0, "calibration_slope_range": [0.8, 1.2],
                   "informative_coverage_min": 0.60, "leakage_violations": 0, "registry_chain": "intact"},
    "label": "fulltime 90' (FT/AET/PEN) from STRICT_KNOWLEDGE at kickoff + 72h",
    "evaluation_date_rule": f"first day with >= {MIN_LABELLED} labelled eligible predictions and >= 6 months after freeze",
    "single_confirmatory_evaluation": True,
}


def _dt(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def registry_status(records: list[dict], anchors: list[dict], external_check) -> dict:
    """CHAIN_VALID, ANCHOR_VERIFIED y EVALUATION_ELIGIBLE por separado (fail-closed)."""
    status = {"CHAIN_VALID": True, "ANCHOR_VERIFIED": False, "EVALUATION_ELIGIBLE": False, "problems": [], "verified_anchors": 0}
    try:
        verify(records)
    except RegistryError as exc:
        status["CHAIN_VALID"] = False
        status["problems"].append(f"cadena: {exc}")
    failures = 0
    for anchor in anchors:
        try:
            verify_anchor(records, anchor, external_check)
            status["verified_anchors"] += 1
        except AnchorError as exc:
            failures += 1
            status["problems"].append(f"anclaje seq {anchor.get('registry_seq')}: {exc}")
    if not anchors:
        status["problems"].append("sin anclaje externo")
    status["ANCHOR_VERIFIED"] = status["CHAIN_VALID"] and status["verified_anchors"] > 0 and failures == 0
    status["EVALUATION_ELIGIBLE"] = status["CHAIN_VALID"] and status["ANCHOR_VERIFIED"]
    return status


def classify(universe: list[dict], records: list[dict], anchors: list[dict], labels: dict[int, str], external_check) -> list[dict]:
    """universe: [{fixture_id, competition_id, final_kickoff}] (kickoff > congelación y competición
    del manifiesto). Verifica la cadena entera antes de nada."""
    verify(records)
    by_fixture: dict[int, list[dict]] = {}
    for rec in records:
        by_fixture.setdefault(rec["fixture_id"], []).append(rec)
    out = []
    for fx in universe:
        recs = by_fixture.get(fx["fixture_id"], [])
        preds = [r for r in recs if r["kind"] == "prediction"]
        entry = {"fixture_id": fx["fixture_id"], "competition_id": fx["competition_id"], "label": labels.get(fx["fixture_id"])}
        if not recs:
            entry["class"] = "NO_RECORD"
        elif not preds:
            entry.update({"class": "NOT_EMITTED", "reasons": sorted({r["reason"] for r in recs})})
        else:
            p = preds[0]  # la primera emisión es la vinculante
            final = _dt(fx["final_kickoff"])
            entry.update({"prediction": p, "emission_status": p["status"], "postponed": final > _dt(p["kickoff_known"]),
                          "unseen_competition": p["unseen_competition"]})
            if _dt(p["emitted_at"]) > final - timedelta(hours=1):
                entry["class"] = "KICKOFF_ADVANCED"
            elif not anchored_before(p, records, anchors, final, external_check):
                entry["class"] = "UNANCHORED"
            elif entry["label"] is None:
                entry["class"] = "NO_LABEL"
            else:
                entry["class"] = p["status"]
        out.append(entry)
    return out


def coverage(classified: list[dict]) -> dict:
    """Cobertura operativa (todos los elegibles) y evaluable (elegibles con etiqueta), por separado."""
    counts = Counter(e["class"] for e in classified)
    emitted = [e for e in classified if "emission_status" in e]
    operational_den = len(classified)
    labelled = [e for e in classified if e["label"] is not None]
    evaluable_num = counts["INFORMATIVE"]

    def ratio(num, den):
        return num / den if den else None

    return {
        "counts": {k: counts.get(k, 0) for k in ("INFORMATIVE", "DEGRADED", "NO_RECORD", "NOT_EMITTED", "NO_LABEL", "KICKOFF_ADVANCED", "UNANCHORED")},
        "operational": {
            "denominator": operational_den, "denominator_definition": "partidos elegibles para emisión (con o sin etiqueta)",
            "emitted": len(emitted), "emitted_informative": sum(e["emission_status"] == "INFORMATIVE" for e in emitted),
            "emitted_degraded": sum(e["emission_status"] == "DEGRADED" for e in emitted),
            "no_record": counts["NO_RECORD"], "not_emitted": counts["NOT_EMITTED"],
            "emission_coverage": ratio(len(emitted), operational_den),
            "informative_emission_coverage": ratio(sum(e["emission_status"] == "INFORMATIVE" for e in emitted), operational_den),
        },
        "evaluable": {
            "denominator": len(labelled), "denominator_definition": "partidos elegibles CON etiqueta verificable",
            "informative": evaluable_num, "no_label_excluded": counts["NO_LABEL"],
            "informative_coverage": ratio(evaluable_num, len(labelled)),
            "used_by_threshold": "informative_coverage_min (pendiente de decisión gerencial)",
        },
        "postponed": sum(1 for e in classified if e.get("postponed")),
        "unseen_competition": sum(1 for e in classified if e.get("unseen_competition")),
    }


class ConfirmatoryEvaluation:
    """Una sola evaluación, con el preregistro congelado: TestGate de M5.8 sobre su huella."""

    def __init__(self, preregistration: dict = PREREGISTRATION):
        self.preregistration_sha = config_hash(preregistration)
        self.gate = TestGate(preregistration)
        self.preregistration = preregistration

    def run(self, classified: list[dict], registry: dict) -> dict:
        """registry = registry_status(...). Sin EVALUATION_ELIGIBLE no se evalúa (ni se gasta la puerta)."""
        if not registry.get("EVALUATION_ELIGIBLE"):
            return {"status": "REGISTRY_NOT_ELIGIBLE", "registry": registry}

        def evaluate():
            rows = [e for e in classified if e["class"] == "INFORMATIVE"]
            labels = [e["label"] for e in rows]
            result = {"preregistration_sha": self.preregistration_sha, "n": len(rows), "coverage": coverage(classified)}
            if len(rows) < MIN_LABELLED:
                result["status"] = "INSUFFICIENT_VOLUME"
                return result
            for model in ("B0", "B1", "B2"):
                probs = [e["prediction"]["probs"][model] for e in rows]
                result[model] = {"log_loss": metrics.log_loss(probs, labels), "brier": metrics.brier(probs, labels), "rps": metrics.rps(probs, labels)}
            result["status"] = "EVALUATED"
            return result

        return self.gate.evaluate(self.preregistration, evaluate)
