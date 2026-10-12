"""Contrato versionado de filas SINTÉTICAS y huella del dataset (puro: sin BD, sin numpy).

Esquema `m510a.synthetic_row.v1`. Una fila es un dict con EXACTAMENTE estos campos:
- row_id          str ASCII imprimible, no vacío y sin espacios alrededor. Es la identidad de la fila:
                  explícita, nunca una clave primaria de BD. Único en el dataset.
- label           "H", "D" o "A": etiqueta SINTÉTICA. No se le atribuye ninguna semántica de
                  resultado histórico (90', prórroga...).
- kickoff_at      datetime con zona y desplazamiento UTC exacto.
- cutoff          datetime con zona y desplazamiento UTC exacto, no posterior a kickoff_at.
- competition_id  int (no bool).
- features        dict nombre -> número finito (int o float, no bool) o None. El nombre es str ASCII
                  no vacío y no termina en "_missing" (sufijo reservado al indicador derivado).
                  Todas las filas de un dataset declaran los mismos nombres.

Ausente, nulo y número son cosas distintas:
- un campo o una feature AUSENTE es un error (no se rellena);
- None es un valor ausente explícito: el modelo lo trata como tal (imputación + indicador);
- un número es un valor. Se guarda como float: 1 y 1.0 son el mismo contenido.

`cutoff <= kickoff_at` solo comprueba coherencia interna de la fila sintética: NO demuestra que unas
features reales estuvieran disponibles antes del corte (eso es de la capa temporal de datos).

La huella es el sha256 del JSON canónico de M5.9 (research.m59.canonical) de
{"row_schema", "rows"} con las filas ordenadas por row_id: no depende del orden de entrada.
"""

import math
from datetime import datetime, timedelta

from research.m58.labels import CLASSES
from research.m59.canonical import canonical_json, sha256_hex

ROW_SCHEMA = "m510a.synthetic_row.v1"
ROW_FIELDS = ("row_id", "label", "kickoff_at", "cutoff", "competition_id", "features")
MISSING_SUFFIX = "_missing"


class ReplicationError(ValueError):
    pass


def _ascii_name(value, what: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii() or not value.isprintable() or value != value.strip():
        raise ReplicationError(f"{what}: se esperaba texto ASCII no vacío: {value!r}")
    return value


def _utc(value, what: str) -> datetime:
    if not isinstance(value, datetime):
        raise ReplicationError(f"{what}: se esperaba un instante")
    if value.utcoffset() is None:
        raise ReplicationError(f"{what}: instante sin zona horaria")
    if value.utcoffset() != timedelta(0):
        raise ReplicationError(f"{what}: el instante tiene que estar en UTC")
    return value


def _features(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise ReplicationError(f"{what}: features tiene que ser un objeto")
    out = {}
    for name, v in value.items():
        _ascii_name(name, f"{what}: nombre de feature")
        if name.endswith(MISSING_SUFFIX):
            raise ReplicationError(f"{what}: {name!r} usa el sufijo reservado {MISSING_SUFFIX!r}")
        if v is None:
            out[name] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ReplicationError(f"{what}: feature {name!r} con tipo no admitido: {type(v).__name__}")
        try:
            number = float(v)
        except OverflowError:
            raise ReplicationError(f"{what}: feature {name!r} fuera de rango") from None
        if not math.isfinite(number):
            raise ReplicationError(f"{what}: feature {name!r} no finita")
        out[name] = number
    return out


def _row(raw, position: int) -> dict:
    what = f"fila {position}"
    if not isinstance(raw, dict):
        raise ReplicationError(f"{what}: no es un objeto")
    missing = [f for f in ROW_FIELDS if f not in raw]
    if missing:
        raise ReplicationError(f"{what}: faltan campos {missing}")
    extra = [repr(k) for k in raw if k not in ROW_FIELDS]
    if extra:
        raise ReplicationError(f"{what}: campos no admitidos [{', '.join(extra)}]")
    row_id = _ascii_name(raw["row_id"], f"{what}: row_id")
    what = f"fila {row_id!r}"
    if not isinstance(raw["label"], str) or raw["label"] not in CLASSES:
        raise ReplicationError(f"{what}: etiqueta no válida: {raw['label']!r}")
    kickoff, cutoff = _utc(raw["kickoff_at"], f"{what}: kickoff_at"), _utc(raw["cutoff"], f"{what}: cutoff")
    if cutoff > kickoff:
        raise ReplicationError(f"{what}: el corte es posterior al kickoff")
    competition_id = raw["competition_id"]
    if isinstance(competition_id, bool) or not isinstance(competition_id, int):
        raise ReplicationError(f"{what}: competition_id tiene que ser un entero")
    return {"row_id": row_id, "label": raw["label"], "kickoff_at": kickoff, "cutoff": cutoff,
            "competition_id": competition_id, "features": _features(raw["features"], what)}


def validate_rows(rows) -> tuple[dict, ...]:
    """Filas validadas y normalizadas (copias), ordenadas por row_id. Lanza ReplicationError."""
    if not isinstance(rows, (list, tuple)):
        raise ReplicationError("las filas tienen que ser una lista")
    if not rows:
        raise ReplicationError("dataset vacío")
    out, seen, names = [], set(), None
    for position, raw in enumerate(rows):
        row = _row(raw, position)
        if row["row_id"] in seen:
            raise ReplicationError(f"row_id duplicado: {row['row_id']!r}")
        seen.add(row["row_id"])
        if names is None:
            names = set(row["features"])
        elif set(row["features"]) != names:
            raise ReplicationError(f"fila {row['row_id']!r}: sus features no son las del resto del dataset")
        out.append(row)
    return tuple(sorted(out, key=lambda r: r["row_id"]))


def feature_names(rows) -> tuple[str, ...]:
    return tuple(sorted(validate_rows(rows)[0]["features"]))


def dataset_payload(rows) -> dict:
    """Lo que se serializa y se huella: esquema + filas en orden canónico."""
    return {"row_schema": ROW_SCHEMA, "rows": list(validate_rows(rows))}


def dataset_bytes(rows) -> bytes:
    return canonical_json(dataset_payload(rows)).encode("utf-8")


def dataset_fingerprint(rows) -> str:
    return sha256_hex(dataset_payload(rows))
