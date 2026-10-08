"""Protocolo de evaluación M5.8 (puro): configuración congelada y test evaluado una sola vez."""

import hashlib
import json


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()


class TestGate:
    """La evaluación de test se ejecuta UNA vez y solo con la configuración congelada antes."""

    __test__ = False  # no es una clase de tests de pytest

    def __init__(self, frozen_config: dict):
        self.frozen = config_hash(frozen_config)
        self.used = False

    def evaluate(self, config: dict, fn):
        if self.used:
            raise RuntimeError("test ya evaluado: no se reevalúa")
        if config_hash(config) != self.frozen:
            raise RuntimeError("configuración distinta de la congelada")
        self.used = True
        return fn()
