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
"""

from collections import Counter
from datetime import datetime, timedelta

from research.m58 import metrics
from research.m58.protocol import TestGate, config_hash
from research.m59.anchors import anchored_before
from research.m59.registry import verify

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
            entry.update({"prediction": p, "postponed": final > _dt(p["kickoff_known"]), "unseen_competition": p["unseen_competition"]})
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
    """Denominador = partidos elegibles CON etiqueta, se emitiera o no (más los que no se emitieron)."""
    counts = Counter(e["class"] for e in classified)
    denominator = sum(1 for e in classified if e["label"] is not None)
    informative = counts["INFORMATIVE"]
    return {"counts": dict(counts), "denominator_labelled": denominator,
            "informative_coverage": informative / denominator if denominator else None,
            "postponed": sum(1 for e in classified if e.get("postponed")),
            "unseen_competition": sum(1 for e in classified if e.get("unseen_competition"))}


class ConfirmatoryEvaluation:
    """Una sola evaluación, con el preregistro congelado: TestGate de M5.8 sobre su huella."""

    def __init__(self, preregistration: dict = PREREGISTRATION):
        self.preregistration_sha = config_hash(preregistration)
        self.gate = TestGate(preregistration)
        self.preregistration = preregistration

    def run(self, classified: list[dict]) -> dict:
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
