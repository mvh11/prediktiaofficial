"""Membresía de una temporada a partir de los partidos que el proveedor asigna a la league/season.

Dos conceptos distintos:
- participante: juega algún partido de la league/season (todos se guardan como teams y fixtures).
- miembro: pertenece a la competición principal esa temporada (solo estos van a season_teams).

Un club de una división inferior que solo aparece en un playoff de promoción/descenso contra
un club de la competición es participante, pero no miembro.

Regla (competiciones "League"): miembro si se enfrentó a al menos la mitad de los rivales
distintos que enfrenta el equipo mediano (opponent_count >= 0.5 * mediana). Un miembro juega la
fase principal contra gran parte de la liga; un invitado de un playoff interdivisional, contra
1–3 rivales. No depende de los nombres de los rounds, que el proveedor no usa de forma estable.

Cualquier otro tipo (copas, o tipo desconocido): todos los participantes son miembros, como
hasta ahora (en una copa, quien juega una fase previa también es participante de la edición).

Señal de auditoría (nunca decide la membresía): participantes de rounds numerados con la forma
"<texto> - <entero>" (p. ej. "Regular Season - 12", "Apertura - 3"). Si no hay ningún round
numerado, la señal no está disponible (numbered_members = None) y no puede haber ambigüedad.
Si está disponible y no coincide con los miembros inferidos, la membresía es ambigua.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from app.schemas.fixture import FixtureData

LEAGUE_TYPE = "League"
MEMBER_SHARE_OF_MEDIAN = 0.5
# "<texto> - <entero>": texto no vacío, separador " - " y un entero positivo sin ceros a la
# izquierda al final. "Round of 16", "1st Phase - 8th Finals" o "Play-off 2nd leg" no lo son.
_NUMBERED_ROUND = re.compile(r"^\S.*? - [1-9][0-9]*$")


@dataclass(frozen=True)
class SeasonMembership:
    competition_type: str | None
    participants: frozenset[int]  # external_id de todos los equipos de los partidos
    members: frozenset[int]  # external_id de los miembros (los que van a season_teams)
    excluded: frozenset[int]  # participants - members
    ambiguous: bool
    # Auditoría
    opponent_counts: dict[int, int]  # external_id -> número de rivales distintos
    median_opponents: float | None
    threshold: float | None  # MEMBER_SHARE_OF_MEDIAN * mediana (None si no aplica la regla)
    numbered_members: frozenset[int] | None  # participantes de rounds numerados; None = sin señal


def is_numbered_round(round_name: str | None) -> bool:
    return bool(round_name) and _NUMBERED_ROUND.match(round_name) is not None


def season_members(fixtures: Iterable[FixtureData], competition_type: str | None) -> SeasonMembership:
    fixtures = list(fixtures)
    opponents: dict[int, set[int]] = {}
    numbered: set[int] = set()
    has_numbered = False
    for f in fixtures:
        home, away = f.home_team.external_id, f.away_team.external_id
        opponents.setdefault(home, set())
        opponents.setdefault(away, set())
        if home != away:  # un "partido" contra sí mismo no aporta rivales (Q15 ya lo bloquea)
            opponents[home].add(away)
            opponents[away].add(home)
        if is_numbered_round(f.round):
            has_numbered = True
            numbered.update((home, away))

    participants = frozenset(opponents)
    counts = {team: len(rivals) for team, rivals in opponents.items()}
    numbered_members = frozenset(numbered) if has_numbered else None

    if competition_type != LEAGUE_TYPE or not counts:
        return SeasonMembership(
            competition_type=competition_type,
            participants=participants,
            members=participants,
            excluded=frozenset(),
            ambiguous=False,
            opponent_counts=counts,
            median_opponents=None,
            threshold=None,
            numbered_members=numbered_members,
        )

    median_opponents = median(counts.values())
    threshold = MEMBER_SHARE_OF_MEDIAN * median_opponents
    members = frozenset(team for team, n in counts.items() if n >= threshold)
    return SeasonMembership(
        competition_type=competition_type,
        participants=participants,
        members=members,
        excluded=participants - members,
        ambiguous=numbered_members is not None and numbered_members != members,
        opponent_counts=counts,
        median_opponents=median_opponents,
        threshold=threshold,
        numbered_members=numbered_members,
    )
