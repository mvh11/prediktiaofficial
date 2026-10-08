"""Etiqueta 1X2 a 90' corregida (M5.8D, tras M5.8C). Puro, sin BD.

FT, AET y PEN se etiquetan con el marcador fulltime (90') del propio partido. En una eliminatoria a
doble partido la prórroga depende del global, así que un AET/PEN puede no ser empate a los 90':
no se supone nada por el estado. goals nunca decide (incluye la prórroga). La coherencia
goals = fulltime + extratime es solo un control de calidad: no excluye ni corrige.
"""

from research.m58.labels import AWAY, DRAW, FULLTIME_INCOMPLETE, HOME, LABELLED_STATUSES, NOT_FINISHED

EXTRA_TIME_STATUSES = frozenset({"AET", "PEN"})
GOALS_NOT_FULLTIME_PLUS_EXTRATIME = "goals_not_fulltime_plus_extratime"


def label_1x2_v2(status_short: str, fulltime_home: int | None, fulltime_away: int | None) -> tuple[str | None, str | None]:
    """(etiqueta, None) o (None, motivo). Misma firma que la v1 de M5.8B."""
    if status_short not in LABELLED_STATUSES:
        return None, NOT_FINISHED
    if fulltime_home is None or fulltime_away is None:
        return None, FULLTIME_INCOMPLETE
    if fulltime_home > fulltime_away:
        return HOME, None
    return (DRAW, None) if fulltime_home == fulltime_away else (AWAY, None)


def quality_flags(status_short: str, goals: tuple, fulltime: tuple, extratime: tuple) -> list[str]:
    """Avisos de coherencia (no cambian la etiqueta)."""
    if status_short not in EXTRA_TIME_STATUSES or None in fulltime or None in goals or None in extratime:
        return []
    expected = (fulltime[0] + extratime[0], fulltime[1] + extratime[1])
    return [] if tuple(goals) == expected else [GOALS_NOT_FULLTIME_PLUS_EXTRATIME]
