"""Contrato versionado de configuración y su identidad (puro).

Esquema `m510a.config.v1`. La configuración es un dict con EXACTAMENTE estos campos:
- config_schema  "m510a.config.v1".
- models         lista sin repetidos de "B0", "B1", "B2" (al menos uno).
- evaluation     {"metrics": lista sin repetidos de SUPPORTED_METRICS (al menos una),
                  "calibration_bins": int >= 1,
                  "bootstrap": None o {"n_boot": int >= 2, "alpha": float en (0, 1)}}.
                 El bootstrap es el de bloques por semana de research.m58.metrics, con `seed`.
- seed           int >= 0 (no bool).
- environment    dict texto -> texto con lo que identifica el entorno de ejecución (versión de
                 Python, plataforma...). Forma parte de la identidad: otro entorno es otra
                 configuración. No admite barras de ruta ("/" o "\\").

No hay más campos: un sello de tiempo, un host o una ruta local no caben en la configuración (van en
el sobre del informe, fuera de lo que se huella). No se define ningún umbral de rendimiento.

La identidad es el sha256 del JSON canónico de M5.9 de la configuración normalizada (modelos y
métricas en orden canónico): no depende del orden de las claves ni del de esas listas.
"""

import math
import platform

from research.m59.canonical import sha256_hex
from research.m510a.rows import ReplicationError

CONFIG_SCHEMA = "m510a.config.v1"
CONFIG_FIELDS = ("config_schema", "models", "evaluation", "seed", "environment")
EVALUATION_FIELDS = ("metrics", "calibration_bins", "bootstrap")
BOOTSTRAP_FIELDS = ("n_boot", "alpha")
MODEL_KINDS = ("B0", "B1", "B2")
SUPPORTED_METRICS = ("log_loss", "brier", "rps", "ece_mean")


def _exact_fields(value, fields, what: str) -> dict:
    if not isinstance(value, dict):
        raise ReplicationError(f"{what}: se esperaba un objeto")
    missing = [f for f in fields if f not in value]
    extra = [repr(k) for k in value if k not in fields]
    if missing or extra:
        raise ReplicationError(f"{what}: faltan {missing}; no admitidos [{', '.join(extra)}]")
    return value


def _choices(value, allowed, what: str) -> list:
    if not isinstance(value, list) or not value:
        raise ReplicationError(f"{what}: se esperaba una lista no vacía")
    if any(not isinstance(v, str) or v not in allowed for v in value) or len(set(value)) != len(value):
        raise ReplicationError(f"{what}: valores no admitidos o repetidos: {value!r}")
    return [v for v in allowed if v in value]


def _integer(value, minimum: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReplicationError(f"{what}: se esperaba un entero >= {minimum}")
    return value


def validate_config(config) -> dict:
    """Configuración validada y normalizada (copia). Lanza ReplicationError."""
    _exact_fields(config, CONFIG_FIELDS, "configuración")
    if config["config_schema"] != CONFIG_SCHEMA:
        raise ReplicationError(f"esquema de configuración no soportado: {config['config_schema']!r}")
    evaluation = _exact_fields(config["evaluation"], EVALUATION_FIELDS, "evaluation")
    bootstrap = evaluation["bootstrap"]
    if bootstrap is not None:
        _exact_fields(bootstrap, BOOTSTRAP_FIELDS, "bootstrap")
        alpha = bootstrap["alpha"]
        if isinstance(alpha, bool) or not isinstance(alpha, float) or not math.isfinite(alpha) or not 0 < alpha < 1:
            raise ReplicationError("bootstrap: alpha tiene que ser un float finito en (0, 1)")
        bootstrap = {"n_boot": _integer(bootstrap["n_boot"], 2, "bootstrap: n_boot"), "alpha": alpha}
    environment = config["environment"]
    if not isinstance(environment, dict) or not environment:
        raise ReplicationError("environment: se esperaba un objeto no vacío")
    for key, value in environment.items():
        for text in (key, value):
            if not isinstance(text, str) or not text or not text.isascii() or not text.isprintable() or "/" in text or "\\" in text:
                raise ReplicationError(f"environment: {key!r} tiene que ser texto ASCII sin rutas: {text!r}")
    return {
        "config_schema": CONFIG_SCHEMA,
        "models": _choices(config["models"], MODEL_KINDS, "models"),
        "evaluation": {"metrics": _choices(evaluation["metrics"], SUPPORTED_METRICS, "metrics"),
                       "calibration_bins": _integer(evaluation["calibration_bins"], 1, "calibration_bins"), "bootstrap": bootstrap},
        "seed": _integer(config["seed"], 0, "seed"),
        "environment": dict(sorted(environment.items())),
    }


def config_hash(config) -> str:
    return sha256_hex(validate_config(config))


def current_environment() -> dict:
    """Entorno de ESTA ejecución, sin nada volátil (ni host, ni rutas, ni instantes)."""
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "system": platform.system(), "machine": platform.machine()}
