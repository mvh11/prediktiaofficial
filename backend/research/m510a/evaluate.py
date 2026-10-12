"""Evaluación offline desde manifiestos CONGELADOS (puro: sin BD, sin numpy; aquí no se ajusta nada).

Los manifiestos se validan y se usan con las APIs de M5.9 (research.m59.manifest): `load` para texto
JSON y `FrozenModel` para la validación y la inferencia. Aquí no hay ninguna fórmula de predicción.

Población de evaluación (contrato v1): TODAS las filas del dataset, en orden de row_id, las mismas
para todos los modelos. El contrato no define ninguna exclusión: una fila que un modelo no pueda
predecir es un error, nunca una omisión silenciosa.

Fila que ve el modelo: competition_id, cada feature con su valor (None = ausente) y, por cada
feature, `<nombre>_missing` = (valor is None). Un manifiesto B2 solo puede pedir columnas que el
dataset declare; si pide otra, se rechaza.
"""

import math
from dataclasses import dataclass

from research.m59.manifest import FrozenModel, load
from research.m510a.config import validate_config
from research.m510a.rows import MISSING_SUFFIX, ReplicationError, validate_rows


@dataclass(frozen=True)
class Evaluation:
    rows: tuple  # filas validadas, por row_id
    config: dict  # configuración validada
    models: dict  # nombre -> FrozenModel, en el orden de config["models"]
    predictions: dict  # nombre -> [[pH, pD, pA]] alineado con rows


def model_row(row: dict) -> dict:
    out = {"competition_id": row["competition_id"]}
    for name, value in row["features"].items():
        out[name] = value
        out[name + MISSING_SUFFIX] = value is None
    return out


def load_models(manifests, selected) -> dict:
    """nombre -> FrozenModel. Cada manifiesto es un dict o su texto JSON; lo valida M5.9 (ManifestError)."""
    if not isinstance(manifests, dict) or set(manifests) != set(selected):
        raise ReplicationError(f"hacen falta exactamente los manifiestos de {list(selected)}")
    models = {}
    for name in selected:
        manifest = manifests[name]
        model = FrozenModel(load(manifest) if isinstance(manifest, str) else manifest)
        if model.kind != name:
            raise ReplicationError(f"el manifiesto presentado como {name} es de tipo {model.kind}")
        models[name] = model
    return models


def _require_columns(name: str, model: FrozenModel, rows) -> None:
    if model.kind != "B2":
        return
    available = set(model_row(rows[0])) - {"competition_id"}
    unknown = [c for c in (*model.p["numeric_columns"], *model.p["flag_columns"]) if c not in available]
    if unknown:
        raise ReplicationError(f"{name}: el manifiesto pide columnas que el dataset no declara: {unknown}")


def _probabilities(name: str, row_id: str, probs) -> list[float]:
    if (not isinstance(probs, list) or len(probs) != 3 or any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) for p in probs)
            or any(not 0 <= p <= 1 for p in probs) or abs(sum(probs) - 1) > 1e-9):
        raise ReplicationError(f"{name}: predicción no válida para la fila {row_id!r}: {probs!r}")
    return probs


def evaluate(rows, manifests, config) -> Evaluation:
    """Predicciones de cada modelo seleccionado para cada fila, alineadas por row_id."""
    config = validate_config(config)
    rows = validate_rows(rows)
    models = load_models(manifests, config["models"])
    predictions = {}
    for name, model in models.items():
        _require_columns(name, model, rows)
        predictions[name] = [_probabilities(name, row["row_id"], model.predict(model_row(row))) for row in rows]
    return Evaluation(rows, config, models, predictions)
