"""Proyección de una distribución conjunta de goles a 90' en probabilidades Over/Under y BTTS (puro).

NO es un modelo: no genera ni estima la distribución, solo la reparte según las reglas de
liquidación de research.m58.goal_labels (OVER/UNDER/PUSH y BTTS YES/NO).

Entrada (contrato MOD-DEV-066-R1): una lista o tupla con exactamente 64 entradas (local, visitante,
probabilidad), una por celda de la partición cerrada 8x8. Categorías de cada equipo: los enteros
0..6 (exactos) y el texto "7+" (siete o más goles; el entero 7 no es una categoría válida). Cada
celda aparece exactamente una vez, también las de probabilidad 0; nada se rellena, se funde ni se
descarta. Probabilidad: int o float (no bool), finita y en [0, 1]. La masa total M (math.fsum en
orden canónico: local 0..6, "7+" y, dentro, visitante en el mismo orden) tiene que cumplir
|M − 1| <= 1e-9; no se renormaliza. Cualquier entrada no válida: ValueError.

Salida: probabilidades incondicionales. Over/Under para cada una de las 13 líneas aprobadas (PUSH
aparte: positivo solo en líneas enteras, nunca repartido) y BTTS YES/NO. Una celda con "7+" suma 7
goles o más: es OVER en todas las líneas aprobadas (todas < 7) y no da un marcador exacto. Cada
partición suma 1 y reconstruye M, ambas dentro de 1e-9. Solo sirve para estas líneas y BTTS.
"""

import math
from decimal import Decimal

from research.m58.goal_labels import (BTTS_NO, BTTS_YES, OVER, PUSH, SUPPORTED_LINE_TEXTS, SUPPORTED_LINES, UNDER,
                                      over_under_line)

TAIL = "7+"  # siete o más goles
GOAL_CATEGORIES = (0, 1, 2, 3, 4, 5, 6, TAIL)
CELLS = tuple((home, away) for home in GOAL_CATEGORIES for away in GOAL_CATEGORIES)  # orden canónico, 64 celdas
TOLERANCE = 1e-9  # normalización de la entrada y conservación de la salida (MOD-DEV-066-R1)
if not max(SUPPORTED_LINES) < 7:  # "7+" solo es OVER seguro si todas las líneas quedan por debajo de 7
    raise RuntimeError("una línea aprobada >= 7 no se puede liquidar con la categoría 7+")


def _category(value, side: str):
    if (type(value) is int and 0 <= value <= 6) or (isinstance(value, str) and value == TAIL):
        return value
    raise ValueError(f"categoría de goles {side} no válida: {value!r} (0..6 o {TAIL!r})")


def _probability(value, cell) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"celda {cell}: probabilidad no válida: {value!r}")
    return value


def validated_cells(distribution) -> tuple[tuple[tuple, float], ...]:
    """((celda, probabilidad), ...) en orden canónico, tras validar la partición y la masa; si no, ValueError."""
    if not isinstance(distribution, (list, tuple)):
        raise ValueError("la distribución tiene que ser una lista o tupla de entradas (local, visitante, probabilidad)")
    seen = {}
    for entry in distribution:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise ValueError(f"entrada no válida (se espera local, visitante, probabilidad): {entry!r}")
        cell = (_category(entry[0], "local"), _category(entry[1], "visitante"))
        if cell in seen:
            raise ValueError(f"celda repetida: {cell}")
        seen[cell] = _probability(entry[2], cell)
    missing = [cell for cell in CELLS if cell not in seen]
    if missing:
        raise ValueError(f"faltan {len(missing)} celdas de las 64, p. ej. {missing[0]}")
    ordered = tuple((cell, seen[cell]) for cell in CELLS)
    mass = math.fsum(p for _, p in ordered)
    if not abs(mass - 1.0) <= TOLERANCE:
        raise ValueError(f"masa total {mass!r} fuera de 1 ± {TOLERANCE}")
    return ordered


def _over_under_outcome(home, away, line: Decimal) -> str:
    if TAIL in (home, away):
        return OVER
    total = home + away
    if total > line:
        return OVER
    return UNDER if total < line else PUSH


def _partition(ordered, outcome_of, outcomes) -> dict[str, float]:
    """Suma incondicional por resultado (math.fsum, orden canónico) y conservación dentro de TOLERANCE."""
    probs = {name: math.fsum(p for cell, p in ordered if outcome_of(*cell) == name) for name in outcomes}
    total, mass = math.fsum(probs.values()), math.fsum(p for _, p in ordered)
    if not (abs(total - 1.0) <= TOLERANCE and abs(total - mass) <= TOLERANCE):
        raise ValueError(f"la partición no conserva la masa: {total!r} frente a {mass!r}")
    return probs


def over_under_probabilities(distribution, line) -> dict[str, float]:
    """{OVER, UNDER, PUSH} para una línea aprobada (validada antes que la distribución, como en las etiquetas)."""
    approved = over_under_line(line)
    ordered = validated_cells(distribution)
    return _partition(ordered, lambda home, away: _over_under_outcome(home, away, approved), (OVER, UNDER, PUSH))


def btts_probabilities(distribution) -> dict[str, float]:
    """{YES, NO}: YES si ninguno de los dos equipos está en la categoría 0 ("7+" implica gol)."""
    return _partition(validated_cells(distribution), lambda home, away: BTTS_YES if home != 0 and away != 0 else BTTS_NO, (BTTS_YES, BTTS_NO))


def goals_market_probabilities(distribution) -> dict:
    """{"mass": M, "over_under": {texto de línea: {OVER, UNDER, PUSH}} para las 13 líneas, "btts": {YES, NO}}."""
    ordered = validated_cells(distribution)
    over_under = {text: _partition(ordered, lambda home, away, line=line: _over_under_outcome(home, away, line), (OVER, UNDER, PUSH))
                  for text, line in zip(SUPPORTED_LINE_TEXTS, SUPPORTED_LINES)}
    btts = _partition(ordered, lambda home, away: BTTS_YES if home != 0 and away != 0 else BTTS_NO, (BTTS_YES, BTTS_NO))
    return {"mass": math.fsum(p for _, p in ordered), "over_under": over_under, "btts": btts}
