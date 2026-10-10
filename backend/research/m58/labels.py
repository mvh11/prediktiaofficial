"""Etiqueta 1X2 a 90 minutos desde la evidencia de DI-A6 en el horizonte H (puro, sin BD).

El par fulltime ya es el marcador a 90' (adapter, contrato DI): en FT es score.fulltime (con
fallback a goals si faltaba); en AET/PEN solo score.fulltime, nunca goals. Por eso un AET/PEN se
liquida con su 90', que tiene que ser empate: si no lo es, la evidencia es incoherente y la fila
se excluye en vez de adivinar. Solo es la variable objetivo: nunca entra como feature.
"""

HOME, DRAW, AWAY = "H", "D", "A"
CLASSES = (HOME, DRAW, AWAY)  # orden ordinal (RPS)
LABELLED_STATUSES = frozenset({"FT", "AET", "PEN"})
EXTRA_TIME_STATUSES = frozenset({"AET", "PEN"})

# Motivos de etiqueta no verificable
NOT_FINISHED = "status_not_finished"  # NS, PST, CANC, AWD, WO...
FULLTIME_INCOMPLETE = "fulltime_incomplete"
EXTRA_TIME_NOT_DRAW_AT_90 = "extra_time_not_draw_at_90"


def label_1x2(status_short: str, fulltime_home: int | None, fulltime_away: int | None) -> tuple[str | None, str | None]:
    """(etiqueta, None) o (None, motivo)."""
    if status_short not in LABELLED_STATUSES:
        return None, NOT_FINISHED
    if fulltime_home is None or fulltime_away is None:
        return None, FULLTIME_INCOMPLETE
    if status_short in EXTRA_TIME_STATUSES and fulltime_home != fulltime_away:
        return None, EXTRA_TIME_NOT_DRAW_AT_90
    if fulltime_home > fulltime_away:
        return HOME, None
    return (DRAW, None) if fulltime_home == fulltime_away else (AWAY, None)
