"""Etiquetas de goles a 90' (Over/Under y BTTS) desde el par fulltime (puro, sin BD).

Mismo origen y estados que la etiqueta 1X2 de M5.8D (research.m58d.labels.label_1x2_v2): FT, AET y
PEN se liquidan con el marcador fulltime (90') del propio partido; nunca con goals (incluye la
prórroga), extratime, penalty ni halftime. Mismo formato: (etiqueta, None) o (None, motivo).

Exclusiones del partido (no son resultados): estado no FT/AET/PEN (NS, PST, CANC, AWD, WO... ->
NOT_FINISHED), par fulltime incompleto (FULLTIME_INCOMPLETE) y par fulltime que no son dos enteros
>= 0 (FULLTIME_INVALID; bool no cuenta como entero). Un dato que falta nunca se convierte en 0.

Over/Under: solo las 13 líneas aprobadas, de 0,5 a 6,5 en pasos de 0,5. La línea se pasa como
Decimal (por su valor exacto) o como su texto canónico ("2.5"); un float, otro tipo, un valor no
finito o una línea no aprobada lanzan ValueError (error del llamador, no del partido), sin
redondear. Total de goles = fulltime_home + fulltime_away: OVER si supera la línea, UNDER si
queda por debajo y PUSH (anulada, se devuelve la apuesta) si la iguala, lo que solo pasa en
líneas enteras. PUSH es un resultado válido, no una exclusión.

BTTS: YES si los dos equipos marcan al menos un gol a los 90', NO si alguno no marca.
"""

from decimal import Decimal

from research.m58.labels import FULLTIME_INCOMPLETE, LABELLED_STATUSES, NOT_FINISHED

FULLTIME_INVALID = "fulltime_invalid"  # el par fulltime existe pero no son dos enteros >= 0

OVER, UNDER, PUSH = "OVER", "UNDER", "PUSH"
BTTS_YES, BTTS_NO = "YES", "NO"
SUPPORTED_LINE_TEXTS = ("0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0", "5.5", "6.0", "6.5")
SUPPORTED_LINES = tuple(Decimal(text) for text in SUPPORTED_LINE_TEXTS)


def over_under_line(line) -> Decimal:
    """La línea aprobada que representa `line`, o ValueError. Decimal: por valor exacto (2.50 es
    2.5); texto: solo la forma canónica de SUPPORTED_LINE_TEXTS. Nada se redondea."""
    if isinstance(line, str):
        if line not in SUPPORTED_LINE_TEXTS:
            raise ValueError(f"línea Over/Under no aprobada: {line!r}")
        return SUPPORTED_LINES[SUPPORTED_LINE_TEXTS.index(line)]
    if not isinstance(line, Decimal):
        raise ValueError(f"línea Over/Under: se espera Decimal o texto, no {type(line).__name__}")
    if not line.is_finite():
        raise ValueError(f"línea Over/Under no finita: {line!r}")
    for supported in SUPPORTED_LINES:
        if line == supported:
            return supported
    raise ValueError(f"línea Over/Under no aprobada: {line!r}")


def goals_at_90(status_short, fulltime_home, fulltime_away) -> tuple[tuple[int, int] | None, str | None]:
    """(par de goles a 90', None) o (None, motivo), con los estados y motivos de la etiqueta 1X2."""
    if not isinstance(status_short, str) or status_short not in LABELLED_STATUSES:
        return None, NOT_FINISHED
    if fulltime_home is None or fulltime_away is None:
        return None, FULLTIME_INCOMPLETE
    if any(type(goals) is not int or goals < 0 for goals in (fulltime_home, fulltime_away)):
        return None, FULLTIME_INVALID
    return (fulltime_home, fulltime_away), None


def label_over_under(status_short, fulltime_home, fulltime_away, line) -> tuple[str | None, str | None]:
    """(OVER | UNDER | PUSH, None) o (None, motivo de exclusión). La línea se valida primero:
    una línea no válida es ValueError aunque el partido esté excluido."""
    approved = over_under_line(line)
    goals, reason = goals_at_90(status_short, fulltime_home, fulltime_away)
    if goals is None:
        return None, reason
    total = goals[0] + goals[1]
    if total > approved:
        return OVER, None
    return (UNDER, None) if total < approved else (PUSH, None)


def label_btts(status_short, fulltime_home, fulltime_away) -> tuple[str | None, str | None]:
    """(YES | NO, None) o (None, motivo de exclusión)."""
    goals, reason = goals_at_90(status_short, fulltime_home, fulltime_away)
    if goals is None:
        return None, reason
    return (BTTS_YES, None) if goals[0] >= 1 and goals[1] >= 1 else (BTTS_NO, None)
