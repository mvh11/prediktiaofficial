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
    if not isinstance(manifest, dict):
        raise ManifestError("el manifiesto no es un objeto")
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    try:
        expected = sha256_hex(body)
    except (TypeError, ValueError) as exc:  # float no finito, instante sin zona o tipo sin forma canónica
        raise ManifestError(f"el manifiesto no tiene forma canónica: {exc}") from exc
    if manifest.get("manifest_sha256") != expected:
        raise ManifestError("la huella del manifiesto no coincide")


def _num(x, what):
    if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x):
        raise ManifestError(f"{what}: número no válido")
    return float(x)


def _probs(p, what):
    if not isinstance(p, list) or len(p) != 3 or abs(sum(_num(v, what) for v in p) - 1) > 1e-9 or not all(0 <= v <= 1 for v in p):
        raise ManifestError(f"{what}: vector de probabilidades no válido")
    return [float(v) for v in p]


def _param(p, key):
    if key not in p:
        raise ManifestError(f"falta params.{key}")
    return p[key]


def _len(v, what):
    try:
        return len(v)
    except TypeError:
        raise ManifestError(f"{what}: se esperaba una lista") from None


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
    if not isinstance(p, dict):
        raise ManifestError("params: se esperaba un objeto")
    kind = manifest["model_kind"]
    if kind == "B0":
        _probs(_param(p, "probs"), "B0")
    elif kind == "B1":
        _probs(_param(p, "base"), "B1 base")
        by_competition = _param(p, "by_competition")
        if not isinstance(by_competition, dict):
            raise ManifestError("B1 by_competition: se esperaba un objeto")
        for comp, probs in by_competition.items():
            try:
                int(comp)
            except (TypeError, ValueError):
                raise ManifestError(f"B1 {comp!r}: competición no válida") from None
            _probs(probs, f"B1 {comp}")
    else:
        numeric, flags, comps, medians, mean, scale, model_classes, coef, intercept = (_param(p, key) for key in (
            "numeric_columns", "flag_columns", "competitions", "imputer_medians", "scaler_mean", "scaler_scale", "model_classes", "coef", "intercept"))
        n = _len(numeric, "numeric_columns")
        if _len(medians, "imputer_medians") != n or _len(mean, "scaler_mean") != n or _len(scale, "scaler_scale") != n:
            raise ManifestError("imputador o escalador no cuadran con las columnas")
        width = n + _len(flags, "flag_columns") + _len(comps, "competitions")
        if model_classes != sorted(CLASSES) or _len(coef, "coef") != 3 or any(_len(row, "coef") != width for row in coef) or _len(intercept, "intercept") != 3:
            raise ManifestError("coeficientes con forma inesperada")
        for v in (*medians, *mean, *scale, *intercept, *(c for row in coef for c in row)):
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
