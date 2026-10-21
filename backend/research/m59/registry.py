"""Registro local append-only de decisiones de emisión con cadena de hashes (puro).

Cada línea es un registro JSON canónico con seq (0, 1, 2…), prev_hash (record_hash del anterior;
GENESIS en el primero) y record_hash = sha256 del resto. Dos tipos:
- "prediction": probabilidades emitidas (nunca resultados);
- "no_emission": decisión de NO emitir con su motivo (cuenta en los denominadores de cobertura).
prediction_id es determinista (modelo + partido + T): reemitir tras un reinicio no duplica nada.
verify() detecta registros modificados, eliminados, duplicados o reordenados.
records() solo lee; verified_records() lee el archivo UNA vez y devuelve exactamente esa lectura ya
verificada con verify() (una línea ilegible también es RegistryError). Es lo que usan append() y la
reutilización del emisor (MOD-DEV-104A). No hay bloqueo entre procesos: la verificación de una lectura no
hace atómica una escritura posterior, y una cadena reescrita entera con hashes válidos sigue pasando.
"""

import json
from pathlib import Path

from research.m59.canonical import canonical_json, sha256_hex

GENESIS = "0" * 64
KINDS = ("prediction", "no_emission")


class RegistryError(ValueError):
    pass


def prediction_id(model_set_sha: str, fixture_id: int, cutoff_iso: str) -> str:
    return sha256_hex({"model_set": model_set_sha, "fixture_id": fixture_id, "T": cutoff_iso})


class Registry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def records(self) -> list[dict]:
        return _parse(self.path.read_text(encoding="utf-8"))

    def verified_records(self) -> list[dict]:
        """Una sola lectura, verificada con verify(): se devuelven esos mismos registros, sin releer el archivo."""
        try:
            snapshot = _parse(self.path.read_text(encoding="utf-8"))
        except (ValueError, RecursionError):  # UTF-8 o JSON no válidos, un int demasiado largo o un anidamiento patológico
            raise RegistryError("el registro tiene una línea ilegible") from None
        return verify(snapshot)

    def head(self) -> tuple[int, str]:
        recs = self.records()
        return (len(recs) - 1, recs[-1]["record_hash"]) if recs else (-1, GENESIS)

    def append(self, record: dict) -> dict:
        """Añade un registro (verificando antes toda la cadena). Idempotente por prediction_id:
        si ya existe uno idéntico en contenido se devuelve ese; si difiere, error (no se reescribe)."""
        if record.get("kind") not in KINDS or "prediction_id" not in record:
            raise RegistryError("registro sin tipo o sin prediction_id")
        existing = self.verified_records()
        for rec in existing:
            if rec["prediction_id"] == record["prediction_id"]:
                body = {k: v for k, v in rec.items() if k not in ("seq", "prev_hash", "record_hash", "emitted_at")}
                new = {k: v for k, v in json.loads(canonical_json(record)).items() if k != "emitted_at"}
                if body != new:
                    raise RegistryError("prediction_id ya registrado con otro contenido")
                return rec
        seq, prev = (len(existing) - 1, existing[-1]["record_hash"]) if existing else (-1, GENESIS)
        full = {**json.loads(canonical_json(record)), "seq": seq + 1, "prev_hash": prev}
        full["record_hash"] = sha256_hex(full)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(canonical_json(full) + "\n")
        return full


def _parse(text: str) -> list:
    """Cada línea no vacía es un JSON (lo escribe append con canonical_json)."""
    return [json.loads(line) for line in text.splitlines() if line]


def verify(records: list[dict]) -> list[dict]:
    """Cadena completa: seq contiguos desde 0, prev_hash enlazado, record_hash correcto y
    prediction_id únicos. Devuelve los registros si todo está bien; si no, RegistryError."""
    if not isinstance(records, (list, tuple)):  # ni se convierte ni se recorre: un generador quedaría agotado
        raise RegistryError("los registros no son una lista")
    prev, seen = GENESIS, set()
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise RegistryError(f"posición {i}: el registro no es un objeto")
        if rec.get("seq") != i:
            raise RegistryError(f"seq {rec.get('seq')} en la posición {i}: eliminado o reordenado")
        if rec.get("prev_hash") != prev:
            raise RegistryError(f"seq {i}: prev_hash no enlaza")
        body = {k: v for k, v in rec.items() if k != "record_hash"}
        try:
            expected = sha256_hex(body)
        except (TypeError, ValueError):  # contenido sin forma canónica: no lo escribió append
            raise RegistryError(f"seq {i}: registro modificado") from None
        if rec.get("record_hash") != expected:
            raise RegistryError(f"seq {i}: registro modificado")
        if "prediction_id" not in rec:
            raise RegistryError(f"seq {i}: registro sin prediction_id")
        try:
            duplicate = rec["prediction_id"] in seen
        except TypeError:
            raise RegistryError(f"seq {i}: prediction_id no válido") from None
        if duplicate:
            raise RegistryError(f"seq {i}: prediction_id duplicado")
        seen.add(rec["prediction_id"])
        prev = rec["record_hash"]
    return records
