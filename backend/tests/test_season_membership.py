"""Tests unitarios (sin BD ni red) de la membresía de la temporada (season_members) y de Q16.

Los calendarios son sintéticos: reproducen la estructura de los formatos reales (liga a doble
vuelta, promoción de Bundesliga/Ligue 1, playoffs de Eredivisie, Apertura/Clausura de Liga MX)
sin depender de respuestas del proveedor.
"""

from itertools import count

import pytest

from app.services import history_quality_checks as qc
from app.services.season_membership import (
    SeasonMembership,
    is_numbered_round,
    season_members,
)
from tests.conftest import make_fixture_data

_ids = count(1)


def match(home: int, away: int, round_name: str | None):
    return make_fixture_data(next(_ids), home=home, away=away, status="FT", round=round_name)


def round_robin(teams: list[int], prefix: str = "Regular Season", double: bool = True, start: int = 1) -> list:
    """Todos contra todos (método del círculo): len(teams)-1 jornadas por vuelta, "<prefix> - <n>"."""
    rotation = list(teams)
    n = len(rotation)
    fixtures = []
    for r in range(n - 1):
        for i in range(n // 2):
            fixtures.append(match(rotation[i], rotation[n - 1 - i], f"{prefix} - {start + r}"))
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    if double:
        fixtures += [match(f.away_team.external_id, f.home_team.external_id, f"{prefix} - {start + n - 1 + i // (n // 2)}") for i, f in enumerate(fixtures)]
    return fixtures


def two_legs(a: int, b: int, round_name: str) -> list:
    return [match(a, b, round_name), match(b, a, round_name)]


LEAGUE18 = list(range(1, 19))
LEAGUE20 = list(range(1, 21))


# --- Rounds numerados (señal de auditoría) ---------------------------------------------------


@pytest.mark.parametrize(
    ("name", "numbered"),
    [
        ("Regular Season - 1", True),
        ("Apertura - 17", True),
        ("2nd Phase - 27", True),
        ("Apertura - Quadrangular - 3", True),
        ("Relegation Round", False),
        ("Final", False),
        ("Round of 16", False),
        ("1st Phase - 8th Finals", False),
        ("Play-off 2nd leg", False),
        ("Regular Season - 01", False),
        ("Regular Season-1", False),
        ("- 3", False),
        ("", False),
        (None, False),
    ],
)
def test_numbered_round_parser_is_strict(name, numbered):
    assert is_numbered_round(name) is numbered


# --- Liga ---------------------------------------------------------------------------------------


def test_pure_league_all_participants_are_members():
    fixtures = round_robin(LEAGUE20)
    m = season_members(fixtures, "League")
    assert len(fixtures) == 380
    assert m.members == m.participants == frozenset(LEAGUE20)
    assert m.excluded == frozenset() and not m.ambiguous
    assert m.median_opponents == 19 and m.threshold == 9.5
    check = qc.q16_season_membership(m)
    assert check.passed and check.count == 0 and check.severity == "warning"


@pytest.mark.parametrize("playoff_round", ["Relegation Round", "Final", "Barrage", "Play-off 2nd leg", None])
def test_bundesliga_promotion_playoff_excludes_lower_division_club(playoff_round):
    """306 partidos de liga + 2 de promoción contra un club de 2. Bundesliga (99): 18 miembros."""
    fixtures = round_robin(LEAGUE18) + two_legs(16, 99, playoff_round)
    m = season_members(fixtures, "League")
    assert len(fixtures) == 308 and len(m.participants) == 19
    assert m.members == frozenset(LEAGUE18) and m.excluded == frozenset({99})
    assert not m.ambiguous  # el nombre del round no decide nada
    check = qc.q16_season_membership(m)
    assert check.passed and check.count == 1 and check.samples == [99]
    assert "no son miembros" in check.detail and not check.is_warning


def test_ligue1_external_club_playing_at_home_first_is_excluded():
    fixtures = round_robin(LEAGUE18) + [match(99, 16, "Relegation Round"), match(16, 99, "Relegation Round")]
    m = season_members(fixtures, "League")
    assert m.excluded == frozenset({99}) and len(m.members) == 18


def test_eredivisie_keeps_18_members_and_excludes_six_lower_division_clubs():
    """Liga + playoff de ascenso (6 clubes de Eerste Divisie y el 16.º) + playoff europeo interno."""
    externals = [101, 102, 103, 104, 105, 106]
    relegation = (
        two_legs(101, 102, "Relegation Round")
        + two_legs(103, 104, "Relegation Round")
        + two_legs(105, 106, "Relegation Round")
        + two_legs(101, 103, "Relegation Round")
        + two_legs(105, 16, "Relegation Round")  # el 16.º de la liga sí es miembro
        + two_legs(101, 16, "Relegation Round")
    )
    european = [match(5, 8, "Conference League Play-offs - Semi-finals"), match(6, 7, "Conference League Play-offs - Semi-finals"), match(5, 6, "Conference League Play-offs - Final")]
    fixtures = round_robin(LEAGUE18) + relegation + european
    m = season_members(fixtures, "League")
    assert len(m.participants) == 24
    assert m.members == frozenset(LEAGUE18)
    assert m.excluded == frozenset(externals)
    assert not m.ambiguous
    check = qc.q16_season_membership(m)
    assert check.passed and check.count == 6 and check.samples == externals[:5]


def test_internal_playoffs_between_members_exclude_nobody():
    """Cuadrangulares numerados y final entre miembros (formato de Colombia/Venezuela)."""
    fixtures = (
        round_robin(LEAGUE20, prefix="Apertura", double=False)
        + round_robin([1, 2, 3, 4], prefix="Apertura - Quadrangular")
        + round_robin([5, 6, 7, 8], prefix="Apertura - Quadrangular")
        + two_legs(1, 5, "Apertura - Final")
    )
    m = season_members(fixtures, "League")
    assert m.members == m.participants == frozenset(LEAGUE20)
    assert m.excluded == frozenset() and not m.ambiguous


def test_liga_mx_apertura_clausura_keeps_its_18_members():
    def tournament(prefix: str) -> list:
        return (
            round_robin(LEAGUE18, prefix=prefix, double=False)  # 153
            + [match(7, 10, f"{prefix} - Play-In Semi-finals"), match(8, 9, f"{prefix} - Play-In Semi-finals"), match(7, 8, f"{prefix} - Play-In Final")]
            + two_legs(1, 8, f"{prefix} - Quarter-finals") + two_legs(2, 7, f"{prefix} - Quarter-finals")
            + two_legs(3, 6, f"{prefix} - Quarter-finals") + two_legs(4, 5, f"{prefix} - Quarter-finals")
            + two_legs(1, 4, f"{prefix} - Semi-finals") + two_legs(2, 3, f"{prefix} - Semi-finals")
            + two_legs(1, 2, f"{prefix} - Final")
        )

    fixtures = tournament("Apertura") + tournament("Clausura")
    m = season_members(fixtures, "League")
    assert len(fixtures) == 2 * (153 + 3 + 14)
    assert m.members == m.participants == frozenset(LEAGUE18)
    assert not m.ambiguous


@pytest.mark.parametrize("competition_type", ["Cup", None, "league"])
def test_non_league_competitions_keep_every_participant(competition_type):
    """Copas (y tipo desconocido): quien juega una ronda previa también es de la edición."""
    fixtures = round_robin([1, 2, 3, 4]) + [match(50, 1, "Qualifying - 1"), match(51, 2, "Preliminary Round")]
    m = season_members(fixtures, competition_type)
    assert m.members == m.participants and m.excluded == frozenset() and not m.ambiguous
    assert m.threshold is None


def test_member_with_few_matches_disagrees_with_numbered_rounds():
    """Un equipo que se retira tras 2 jornadas: pocos rivales, pero aparece en rounds numerados."""
    teams = list(range(1, 11))
    fixtures = [f for f in round_robin(teams) if 10 not in (f.home_team.external_id, f.away_team.external_id)]
    fixtures += [match(10, 1, "Regular Season - 1"), match(2, 10, "Regular Season - 2")]
    m = season_members(fixtures, "League")
    assert 10 in m.excluded and 10 in m.numbered_members
    assert m.ambiguous
    check = qc.q16_season_membership(m)
    assert check.is_warning and not check.is_blocking_failure
    assert 10 in check.samples and "ambigua" in check.detail


def test_without_numbered_rounds_there_is_no_ambiguity():
    """Sin ningún round numerado la señal de auditoría no existe: no se inventa desacuerdo."""
    fixtures = [match(f.home_team.external_id, f.away_team.external_id, "Regular Season") for f in round_robin(LEAGUE18)]
    fixtures += two_legs(16, 99, "Relegation Round")
    m = season_members(fixtures, "League")
    assert m.numbered_members is None and not m.ambiguous
    assert m.excluded == frozenset({99})


def test_safeguard_blocks_when_members_are_fewer_than_half():
    """Con la regla de la mediana no puede darse (al menos la mitad está en o por encima de la
    mediana); la salvaguarda protege ante cualquier cambio futuro de la regla."""
    membership = SeasonMembership(
        competition_type="League",
        participants=frozenset(range(1, 11)),
        members=frozenset({1, 2, 3, 4}),
        excluded=frozenset(range(5, 11)),
        ambiguous=False,
        opponent_counts={},
        median_opponents=None,
        threshold=None,
        numbered_members=None,
    )
    check = qc.q16_season_membership(membership)
    assert check.is_blocking_failure and check.severity == "blocking" and check.count == 6


def test_self_match_adds_no_opponent():
    m = season_members(round_robin([1, 2, 3, 4]) + [match(3, 3, "Regular Season - 7")], "League")
    assert m.opponent_counts[3] == 3


def test_premier_and_liga_mx_shapes_keep_their_members():
    """Regresión M4.3: Premier 2025 (20 → 20) y Liga MX 2025 (18 → 18)."""
    premier = season_members(round_robin(LEAGUE20), "League")
    assert len(premier.participants) == len(premier.members) == 20
    liga_mx = season_members(
        round_robin(LEAGUE18, prefix="Apertura", double=False) + round_robin(LEAGUE18, prefix="Clausura", double=False),
        "League",
    )
    assert len(liga_mx.participants) == len(liga_mx.members) == 18
