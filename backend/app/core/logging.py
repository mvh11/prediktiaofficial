"""Configuración de logs y utilidades para no exponer secretos."""

import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def mask_secret(value: str, visible: int = 4) -> str:
    """Devuelve el secreto oculto, mostrando solo los últimos caracteres.

    Ejemplo: "abcdef123456" -> "****3456"
    """
    if not value:
        return "(vacío)"
    if len(value) <= visible:
        return "****"
    return "****" + value[-visible:]
