"""Manifiesto JSON de modelos congelados y su inferencia en Python puro (sin numpy ni pickle).

Cargar un manifiesto solo hace json.loads + validación de forma: nunca ejecuta código. La huella
manifest_sha256 cubre todo el contenido (menos ella misma); cualquier cambio la rompe.
Modelos: B0 (frecuencias), B1 (frecuencias por competición) y B2 (logística multinomial con
imputación por mediana, escalado, indicadores y one-hot de competición).
"""

import json
import math

from research.m58.labels import CLASSES
from research.m59.canonical import sha256_hex

MANIFEST_VERSION = 1
FEATURE_CONTRACT = "team_recent_form_v1"
KINDS = ("B0", "B1", "B2")


class ManifestError(ValueError):
    pass


def seal(manifest: dict) -> dict:
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return {**body, "manifest_sha256": sha256_hex(body)}


def verify(manifest: dict) -> None:
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if manifest.get("manifest_sha256") != sha256_hex(body):
        raise ManifestError("la huella del manifiesto no coincide")


def _num(x, what):
    if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x):
        raise ManifestError(f"{what}: número no válido")
    return float(x)


def _probs(p, what):
    if not isinstance(p, list) or len(p) != 3 or abs(sum(_num(v, what) for v in p) - 1) > 1e-9:
        raise ManifestError(f"{what}: vector de probabilidades no válido")
    return [float(v) for v in p]


def validate(manifest: dict) -> None:
    """Forma completa (tipos, longitudes, clases) y huella. Lanza ManifestError."""
    verify(manifest)
    for key in ("manifest_version", "model_kind", "model_version", "feature_contract", "classes", "training_id",
                "frozen_at", "code_version", "dependencies", "dataset_fingerprint", "hyperparameters", "params"):
        if key not in manifest:
            raise ManifestError(f"falta {key}")
    if manifest["manifest_version"] != MANIFEST_VERSION or manifest["model_kind"] not in KINDS:
        raise ManifestError("versión o tipo de modelo no soportado")
    if manifest["feature_contract"] != FEATURE_CONTRACT or manifest["classes"] != list(CLASSES):
        raise ManifestError("contrato de features o clases inesperados")
    p = manifest["params"]
    kind = manifest["model_kind"]
    if kind == "B0":
        _probs(p["probs"], "B0")
    elif kind == "B1":
        _probs(p["base"], "B1 base")
        for comp, probs in p["by_competition"].items():
            int(comp)
            _probs(probs, f"B1 {comp}")
    else:
        numeric, flags, comps = p["numeric_columns"], p["flag_columns"], p["competitions"]
        if len(p["imputer_medians"]) != len(numeric) or len(p["scaler_mean"]) != len(numeric) or len(p["scaler_scale"]) != len(numeric):
            raise ManifestError("imputador o escalador no cuadran con las columnas")
        width = len(numeric) + len(flags) + len(comps)
        if p["model_classes"] != sorted(CLASSES) or len(p["coef"]) != 3 or any(len(row) != width for row in p["coef"]) or len(p["intercept"]) != 3:
            raise ManifestError("coeficientes con forma inesperada")
        for v in (*p["imputer_medians"], *p["scaler_mean"], *p["scaler_scale"], *p["intercept"], *(c for row in p["coef"] for c in row)):
            _num(v, "parámetro")


def load(text: str) -> dict:
    manifest = json.loads(text)
    validate(manifest)
    return manifest


def _softmax(z):
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


class FrozenModel:
    """Inferencia desde el manifiesto. predict(row) -> [pH, pD, pA] en el orden de CLASSES."""

    def __init__(self, manifest: dict):
        validate(manifest)
        self.manifest = manifest
        self.kind = manifest["model_kind"]
        self.sha = manifest["manifest_sha256"]
        self.p = manifest["params"]

    def known_competition(self, competition_id: int) -> bool:
        if self.kind == "B1":
            return str(competition_id) in self.p["by_competition"]
        if self.kind == "B2":
            return competition_id in self.p["competitions"]
        return True

    def predict(self, row: dict) -> list[float]:
        if self.kind == "B0":
            return list(self.p["probs"])
        if self.kind == "B1":
            return list(self.p["by_competition"].get(str(row["competition_id"]), self.p["base"]))
        p = self.p
        x = []
        for col, med, mean, scale in zip(p["numeric_columns"], p["imputer_medians"], p["scaler_mean"], p["scaler_scale"]):
            v = row[col]
            v = med if v is None else float(v)
            x.append((v - mean) / scale)
        x += [1.0 if row[c] else 0.0 for c in p["flag_columns"]]
        x += [1.0 if row["competition_id"] == c else 0.0 for c in p["competitions"]]
        z = [b + sum(w * xi for w, xi in zip(ws, x)) for ws, b in zip(p["coef"], p["intercept"])]
        probs = dict(zip(p["model_classes"], _softmax(z)))
        return [probs[c] for c in CLASSES]
