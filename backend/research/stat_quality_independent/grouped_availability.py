"""Resumen agregado de disponibilidad POR GRUPO.

Cada observación es un par (group_key, classification) que entrega el caller. La clave de grupo
es una etiqueta categórica opaca (competición, temporada, competición-temporada o cualquier otra
que el caller componga): aquí no se busca, no se resuelve ni se infiere ninguna entidad.

Semántica:
- una observación pertenece al grupo cuya clave entrega el caller, y solo a ese;
- las claves se comparan de forma EXACTA, sin normalizar mayúsculas, espacios, alias ni ids:
  "Liga", "liga" y " Liga" son tres grupos distintos;
- no se deduplica: la multiplicidad importa y un par repetido cuenta tantas veces como aparezca;
- cada grupo se resume de forma independiente con `summarize_availability`, así que hereda su
  contrato (etiquetas exactas, "AVAILABLE" exacto, 0.0 si el grupo no la contiene);
- el orden de la entrada no afecta al resultado;
- solo existen los grupos observados: un grupo que no aparece en la entrada no se materializa,
  de modo que ningún grupo del resultado está vacío. No se define un universo de grupos.

Como en `availability`, esto solo describe la muestra: sin tamaños mínimos, umbrales ni reglas
de decisión por grupo.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from research.stat_quality_independent.availability import AvailabilitySummary, summarize_availability


def summarize_availability_by_group(observations: Iterable[tuple[str, str]]) -> Mapping[str, AvailabilitySummary]:
    """Resume una secuencia finita de pares (group_key, classification). Pura: no modifica la entrada.

    Devuelve un mapping de solo lectura group_key -> AvailabilitySummary con las claves en orden
    ascendente (orden de `str`). Sin observaciones, el mapping está vacío.

    Lanza TypeError si la entrada es un único str/bytes, si una observación no es una tupla de
    exactamente dos elementos o si la clave de grupo o la clasificación no son str: no se convierte
    ni se descarta nada en silencio.
    """
    if isinstance(observations, (str, bytes)):
        raise TypeError("observations debe ser un iterable de pares (group_key, classification)")
    labels_by_group: dict[str, list[str]] = {}
    for observation in observations:
        if not isinstance(observation, tuple) or len(observation) != 2:
            raise TypeError(f"observación que no es un par (group_key, classification): {observation!r}")
        group_key, label = observation
        if not isinstance(group_key, str):
            raise TypeError(f"clave de grupo no str: {group_key!r}")
        if not isinstance(label, str):
            raise TypeError(f"clasificación no str: {label!r}")
        labels_by_group.setdefault(group_key, []).append(label)

    return MappingProxyType({key: summarize_availability(labels_by_group[key]) for key in sorted(labels_by_group)})
