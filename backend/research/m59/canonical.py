"""Serialización canónica y huellas (puro). Una misma estructura da siempre los mismos bytes:
claves ordenadas, sin espacios, UTF-8, floats con repr exacto de Python e instantes en ISO-8601 UTC."""

import hashlib
import json
import math
from datetime import datetime, timezone


def _normalize(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("instante sin zona horaria")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("float no finito")
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def canonical_json(value) -> str:
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
