"""Informe comparativo determinista y su huella NO autorreferente (puro).

Carga útil `m510a.report.v1` (solo tipos JSON; claves en orden canónico al serializar):
- report_schema
- dataset       {row_schema, fingerprint, rows}
- config        {hash, config}  (la configuración entera: semilla y entorno incluidos)
- models        nombre -> {model_kind, model_version, manifest_sha256}
- model_set_sha256
- population    {n, row_ids (orden canónico), class_balance, excluded: {n, by_reason}}
- results       nombre -> {n, unseen_competition_rows, predictions_sha256, metrics, [log_loss_ci95]}
- paired_log_loss_diff   "A-B" -> {estimate, [ci95]} para los pares seleccionados
- unavailable   resultado -> motivo (código estable)

Métricas: las de research.m58.metrics, con su valor exacto (sin redondear aquí; `ece_mean` y
`class_balance` ya vienen redondeados a 4 decimales por esas funciones). Reglas de lo no disponible:
- las pendientes de calibración necesitan scikit-learn y quedan fuera del camino congelado:
  siempre en `unavailable` como REQUIRES_FITTING_DEPENDENCIES, nunca sustituidas por otra cosa;
- sin bootstrap en la configuración no hay intervalos: BOOTSTRAP_NOT_CONFIGURED.

Huella: report_hash = sha256(bytes UTF-8 del JSON canónico de M5.9 de la carga útil). La carga útil
no contiene report_hash. El sobre es {report_hash, payload, audit}: en `audit` va lo volátil
(generated_at, host, rutas) y no entra en la huella. Lo que se garantiza idéntico byte a byte entre
dos ejecuciones con las mismas entradas congeladas y el mismo entorno es la CARGA ÚTIL, no el sobre.
No se afirma equivalencia entre plataformas distintas (math.exp/log pueden diferir en el último bit;
por eso el entorno forma parte de la configuración).
"""

import hashlib
import json

from research.m58 import metrics
from research.m59.canonical import canonical_json, sha256_hex
from research.m510a.config import config_hash
from research.m510a.evaluate import Evaluation, evaluate
from research.m510a.rows import ROW_SCHEMA, ReplicationError, dataset_fingerprint

REPORT_SCHEMA = "m510a.report.v1"
PAIRS = (("B2", "B1"), ("B2", "B0"), ("B1", "B0"))
REQUIRES_FITTING_DEPENDENCIES, BOOTSTRAP_NOT_CONFIGURED = "REQUIRES_FITTING_DEPENDENCIES", "BOOTSTRAP_NOT_CONFIGURED"


def _metrics(names, probs, labels, bins: int) -> dict:
    values = {"log_loss": lambda: metrics.log_loss(probs, labels), "brier": lambda: metrics.brier(probs, labels),
              "rps": lambda: metrics.rps(probs, labels), "ece_mean": lambda: metrics.calibration(probs, labels, bins)["ece_mean"]}
    return {name: values[name]() for name in names}


def build_payload(evaluation: Evaluation) -> dict:
    rows, config, settings = evaluation.rows, evaluation.config, evaluation.config["evaluation"]
    labels = [r["label"] for r in rows]
    bootstrap = settings["bootstrap"]

    def interval(statistic, tagged):
        return list(metrics.block_bootstrap(tagged, statistic, n_boot=bootstrap["n_boot"], seed=config["seed"], alpha=bootstrap["alpha"]))

    results = {}
    for name, model in evaluation.models.items():
        probs = evaluation.predictions[name]
        entry = {"n": len(probs), "unseen_competition_rows": sum(not model.known_competition(r["competition_id"]) for r in rows),
                 "predictions_sha256": sha256_hex([[r["row_id"], p] for r, p in zip(rows, probs)]),
                 "metrics": _metrics(settings["metrics"], probs, labels, settings["calibration_bins"])}
        if bootstrap is not None:
            tagged = [{"kickoff_at": r["kickoff_at"], "label": r["label"], "p": p} for r, p in zip(rows, probs)]
            entry["log_loss_ci95"] = interval(lambda rs: metrics.log_loss([r["p"] for r in rs], [r["label"] for r in rs]), tagged)
        results[name] = entry

    paired = {}
    for a, b in PAIRS:
        if a in evaluation.models and b in evaluation.models:
            pa, pb = evaluation.predictions[a], evaluation.predictions[b]
            entry = {"estimate": metrics.log_loss(pa, labels) - metrics.log_loss(pb, labels)}
            if bootstrap is not None:
                tagged = [{"kickoff_at": r["kickoff_at"], "label": r["label"], "pa": x, "pb": y} for r, x, y in zip(rows, pa, pb)]
                entry["ci95"] = interval(lambda rs: metrics.log_loss([r["pa"] for r in rs], [r["label"] for r in rs])
                                         - metrics.log_loss([r["pb"] for r in rs], [r["label"] for r in rs]), tagged)
            paired[f"{a}-{b}"] = entry

    unavailable = {"calibration_slopes": REQUIRES_FITTING_DEPENDENCIES}
    if bootstrap is None:
        unavailable.update(log_loss_ci95=BOOTSTRAP_NOT_CONFIGURED, paired_log_loss_diff_ci95=BOOTSTRAP_NOT_CONFIGURED)
    payload = {
        "report_schema": REPORT_SCHEMA,
        "dataset": {"row_schema": ROW_SCHEMA, "fingerprint": dataset_fingerprint(list(rows)), "rows": len(rows)},
        "config": {"hash": config_hash(config), "config": config},
        "models": {name: {"model_kind": m.kind, "model_version": m.manifest["model_version"], "manifest_sha256": m.sha}
                   for name, m in evaluation.models.items()},
        "model_set_sha256": sha256_hex({name: m.sha for name, m in sorted(evaluation.models.items())}),
        "population": {"n": len(rows), "row_ids": [r["row_id"] for r in rows], "class_balance": metrics.class_balance(labels),
                       "excluded": {"n": 0, "by_reason": {}}},
        "results": results,
        "paired_log_loss_diff": paired,
        "unavailable": unavailable,
    }
    return json.loads(canonical_json(payload))  # solo tipos JSON, tal y como se serializa


def payload_bytes(payload: dict) -> bytes:
    if not isinstance(payload, dict) or "report_hash" in payload:
        raise ReplicationError("la carga útil del informe no puede contener report_hash")
    return canonical_json(payload).encode("utf-8")


def report_hash(payload: dict) -> str:
    return hashlib.sha256(payload_bytes(payload)).hexdigest()


def build_envelope(payload: dict, audit: dict | None = None) -> dict:
    """Sobre del informe: la huella y los metadatos de auditoría van FUERA de la carga útil."""
    return {"report_hash": report_hash(payload), "payload": payload, "audit": dict(audit or {})}


def verify_envelope(envelope: dict) -> None:
    """Recalcula la huella solo con la carga útil. Lanza ReplicationError si no coincide."""
    if not isinstance(envelope, dict) or set(envelope) != {"report_hash", "payload", "audit"}:
        raise ReplicationError("sobre de informe con forma inesperada")
    if envelope["report_hash"] != report_hash(envelope["payload"]):
        raise ReplicationError("la huella del informe no coincide con su carga útil")


def replicate(rows, manifests, config, audit: dict | None = None) -> dict:
    """Filas + manifiestos congelados + configuración -> sobre del informe."""
    return build_envelope(build_payload(evaluate(rows, manifests, config)), audit)
