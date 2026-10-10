"""Anclajes externos del registro (puro): prueba de que una predicción existía antes del kickoff.

Un anclaje publica (seq, head_hash) del registro ante una AUTORIDAD EXTERNA que fija el instante;
la fecha de un commit local no vale (la pone quien escribe). Autoridades aceptadas:
- "rfc3161": sello de tiempo de una TSA sobre head_hash (el token se verifica con su certificado);
- "github_server": head_hash publicado en el repositorio remoto; vale la hora del SERVIDOR
  (evento de push o API), no la fecha del commit;
- "project_thread": head_hash publicado en el hilo del proyecto; vale la hora del mensaje.
La verificación criptográfica o de servidor de cada autoridad se inyecta (external_check): aquí se
comprueba el enlace con la cadena y la cobertura temporal.
"""

from collections.abc import Callable
from datetime import datetime

from research.m59.canonical import sha256_hex

ACCEPTED_AUTHORITIES = frozenset({"rfc3161", "github_server", "project_thread"})


class AnchorError(ValueError):
    pass


def make_statement(records: list[dict]) -> dict:
    """Lo que se publica: seq y hash de cabeza (más su propia huella)."""
    if not records:
        raise AnchorError("registro vacío")
    head = records[-1]
    statement = {"registry_seq": head["seq"], "head_hash": head["record_hash"]}
    return {**statement, "statement_sha256": sha256_hex(statement)}


def verify_anchor(records: list[dict], anchor: dict, external_check: Callable[[dict], bool]) -> datetime:
    """Devuelve el instante externo verificado del anclaje, o AnchorError."""
    if anchor.get("authority") not in ACCEPTED_AUTHORITIES:
        raise AnchorError(f"autoridad no aceptada: {anchor.get('authority')!r} (un commit local no prueba anterioridad)")
    seq = anchor.get("registry_seq")
    if not isinstance(seq, int) or not 0 <= seq < len(records) or records[seq]["record_hash"] != anchor.get("head_hash"):
        raise AnchorError("el anclaje no corresponde a esta cadena")
    if anchor.get("statement_sha256") != sha256_hex({"registry_seq": seq, "head_hash": anchor["head_hash"]}):
        raise AnchorError("declaración del anclaje alterada")
    if not external_check(anchor):
        raise AnchorError("la autoridad externa no confirma el anclaje")
    when = anchor.get("external_time")
    if not isinstance(when, datetime) or when.tzinfo is None:
        raise AnchorError("instante externo no válido")
    return when


def anchored_before(record: dict, records: list[dict], anchors: list[dict], deadline: datetime, external_check) -> bool:
    """El registro está cubierto por algún anclaje verificado con seq >= el suyo e instante
    externo estrictamente anterior a deadline (kickoff)."""
    for anchor in anchors:
        try:
            when = verify_anchor(records, anchor, external_check)
        except AnchorError:
            continue
        if anchor["registry_seq"] >= record["seq"] and when < deadline:
            return True
    return False
