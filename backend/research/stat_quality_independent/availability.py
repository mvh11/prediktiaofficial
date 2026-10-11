"""Resumen agregado de una muestra de clasificaciones de disponibilidad.

Unidad de conteo: UN elemento de la entrada = UNA observación. No se deduplica ni se asume
unicidad por partido: un valor repetido cuenta tantas veces como aparezca.

Las clasificaciones son etiquetas categóricas opacas: se comparan de forma EXACTA, sin
normalizar mayúsculas, espacios ni alias. "AVAILABLE", "available" y " AVAILABLE" son tres
clasificaciones distintas.

Este módulo solo describe la muestra. No fija tamaño mínimo de muestra, umbrales de cobertura,
elegibilidad para modelar ni ninguna regla de decisión: `total_observations` es un hecho, no
una valoración.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

AVAILABLE = "AVAILABLE"


@dataclass(frozen=True)
class AvailabilitySummary:
    """Resultado agregado. Los mappings son de solo lectura y sus claves van en orden ascendente
    (orden de `str`), así que la representación no depende del orden de la entrada.

    - total_observations: número de elementos de la entrada.
    - counts_by_classification: veces que aparece cada clasificación observada.
    - relative_frequencies: count / total_observations de cada clasificación observada.
    - available_proportion: frecuencia relativa de la clasificación exacta "AVAILABLE"; 0.0 si
      la muestra no está vacía y no la contiene; None si la muestra está vacía (denominador
      cero: no se inventa una interpretación).
    """

    total_observations: int
    counts_by_classification: Mapping[str, int]
    relative_frequencies: Mapping[str, float]
    available_proportion: float | None


def summarize_availability(classifications: Iterable[str]) -> AvailabilitySummary:
    """Resume una secuencia finita de clasificaciones. Pura: no modifica la entrada.

    Lanza TypeError si la entrada es un único str/bytes (se recorrería carácter a carácter) o si
    algún elemento no es str: no se convierte ni se descarta nada en silencio.
    """
    if isinstance(classifications, (str, bytes)):
        raise TypeError("classifications debe ser un iterable de str, no un único str/bytes")
    counter: Counter[str] = Counter()
    for label in classifications:
        if not isinstance(label, str):
            raise TypeError(f"clasificación no str: {label!r}")
        counter[label] += 1

    total = sum(counter.values())
    counts = {label: counter[label] for label in sorted(counter)}
    frequencies = {label: n / total for label, n in counts.items()}
    return AvailabilitySummary(
        total_observations=total,
        counts_by_classification=MappingProxyType(counts),
        relative_frequencies=MappingProxyType(frequencies),
        available_proportion=frequencies.get(AVAILABLE, 0.0) if total else None,
    )
