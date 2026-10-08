"""Construye manifiestos sellados desde modelos ajustados de M5.8 (B0, B1, B2/LogisticB2).

No importa numpy: lee los atributos ajustados y los convierte a listas de float (repr exacto).
El entrenamiento definitivo y la fecha de congelación real son de M5.9D; aquí solo la herramienta.
"""

from datetime import datetime

from research.m58.labels import CLASSES
from research.m59.manifest import FEATURE_CONTRACT, MANIFEST_VERSION, seal


def _base(kind, *, model_version, training_id, frozen_at: datetime, code_version, dependencies, dataset_fingerprint, hyperparameters, params):
    if frozen_at.tzinfo is None:
        raise ValueError("frozen_at sin zona horaria")
    return seal({
        "manifest_version": MANIFEST_VERSION, "model_kind": kind, "model_version": model_version,
        "feature_contract": FEATURE_CONTRACT, "classes": list(CLASSES), "training_id": training_id,
        "frozen_at": frozen_at, "code_version": code_version, "dependencies": dependencies,
        "dataset_fingerprint": dataset_fingerprint, "hyperparameters": hyperparameters, "params": params,
    })


def _floats(values):
    return [float(v) for v in values]


def freeze_b0(model, **meta) -> dict:
    return _base("B0", hyperparameters={}, params={"probs": _floats(model.p)}, **meta)


def freeze_b1(model, **meta) -> dict:
    return _base("B1", hyperparameters={"alpha": float(model.alpha)},
                 params={"base": _floats(model.base), "by_competition": {str(c): _floats(p) for c, p in sorted(model.p.items())}}, **meta)


def freeze_b2(model, numeric_columns, flag_columns, **meta) -> dict:
    lr = model.model
    params = {
        "numeric_columns": list(numeric_columns), "flag_columns": list(flag_columns),
        "competitions": [int(c) for c in model.competitions],
        "imputer_medians": _floats(model.imputer.statistics_),
        "scaler_mean": _floats(model.scaler.mean_), "scaler_scale": _floats(model.scaler.scale_),
        "model_classes": [str(c) for c in lr.classes_],
        "coef": [_floats(row) for row in lr.coef_], "intercept": _floats(lr.intercept_),
    }
    return _base("B2", hyperparameters={"C": float(model.C), "penalty": "l2", "solver": lr.solver}, params=params, **meta)
