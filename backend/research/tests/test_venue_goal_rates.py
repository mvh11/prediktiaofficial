"""MOD-DEV-073: línea base de tasas de goles por sede con control PIT. Puro: sin BD, sin red, sin app.
    python -m pytest research/tests/test_venue_goal_rates.py -q   (desde backend/)
Todos los partidos son SINTÉTICOS (no son datos históricos) y las medias esperadas están calculadas a mano."""

import copy
import dataclasses
import math
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from research.m58.goal_labels import FULLTIME_INVALID
from research.m58.goal_projection import CELLS, validated_cells
from research.m58.goal_reference import independent_poisson_reference
from research.m58.labels import FULLTIME_INCOMPLETE, NOT_FINISHED
from research.venue_goal_rates.baseline import (CROSS_SEASON_MEMBERSHIP_NOT_EVIDENCED, ESTIMATE, EVIDENCE_LIMITATIONS, KICKOFF_NOT_BEFORE_CUTOFF,
                                                M_MINIMUM, N_RECENT, NO_ESTIMATE, POLICY, RESULT_OBSERVED_AFTER_CUTOFF,
                                                RESULT_OBSERVED_AT_MISSING, TARGET_FIXTURE, WRONG_COMPETITION, EvidenceContractError,
                                                estimate_venue_goal_rates)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = timezone.utc
T = datetime(2026, 3, 1, 18, 0, tzinfo=UTC)
HOME_TEAM, AWAY_TEAM, COMPETITION, SEASON = 11, 22, 7, 70
TARGET = {"fixture_id": 1000, "competition_id": COMPETITION, "season_id": SEASON, "home_team_id": HOME_TEAM, "away_team_id": AWAY_TEAM,
          "kickoff_at": T + timedelta(hours=2)}
AUTO = object()


def match(fixture_id, home, away, kickoff, fulltime_home, fulltime_away, status="FT", competition=COMPETITION, season=SEASON,
          observed=AUTO, **extra):
    return {"fixture_id": fixture_id, "competition_id": competition, "season_id": season, "home_team_id": home, "away_team_id": away,
            "kickoff_at": kickoff, "status_short": status, "fulltime_home": fulltime_home, "fulltime_away": fulltime_away,
            "result_observed_at": kickoff + timedelta(hours=2) if observed is AUTO else observed, **extra}


def home_games(goals, first_id=1, first_day=1, **kw):
    """Partidos EN CASA del local objetivo; goals[0] es el más reciente."""
    return [match(first_id + i, HOME_TEAM, 500 + i, T - timedelta(days=first_day + i), g, 1, **kw) for i, g in enumerate(goals)]


def away_games(goals, first_id=101, first_day=1, **kw):
    """Partidos FUERA del visitante objetivo; goals[0] es el más reciente."""
    return [match(first_id + i, 600 + i, AWAY_TEAM, T - timedelta(days=first_day + i, hours=3), 1, g, **kw) for i, g in enumerate(goals)]


def run(history, memberships=(), target=TARGET, cutoff=T):
    return estimate_venue_goal_rates(target, history, cutoff, memberships)


# --- Política N = 20, M = 8 y medias ---------------------------------------------------------------

def test_exactly_twenty_home_and_ten_away_matches():
    result = run(home_games([2, 1, 0, 3] * 5) + away_games([0, 1, 1, 2, 0, 3, 1, 0, 2, 0]))
    assert result.status == ESTIMATE and result.no_estimate_reasons == ()
    assert result.lambda_home == 1.5 and result.lambda_away == 1.0  # 30 / 20 y 10 / 10
    assert (result.home.eligible, result.home.selected, result.away.eligible, result.away.selected) == (20, 20, 10, 10)
    assert (result.policy, result.n_recent, result.m_minimum) == (POLICY, 20, 8) == (POLICY, N_RECENT, M_MINIMUM)
    assert result.cutoff == T and result.target_fixture_id == 1000 and result.history_records == 30
    assert result.evidence_limitations == EVIDENCE_LIMITATIONS and "HISTORY_COMPLETENESS_NOT_VERIFIED" in EVIDENCE_LIMITATIONS


def test_more_than_twenty_selects_the_twenty_most_recent():
    result = run(home_games([1] * 20 + [9] * 5) + away_games([2] * 8))
    assert result.lambda_home == 1.0 and result.home.eligible == 25 and result.home.selected == 20  # los cinco 9 son más antiguos
    assert result.home.selected_fixture_ids == tuple(range(1, 21))


def test_exactly_eight_estimates_and_seven_does_not():
    ok = run(home_games([1, 2, 3, 0, 1, 2, 3, 0]) + away_games([2] * 8))
    assert ok.status == ESTIMATE and ok.lambda_home == 1.5 and ok.lambda_away == 2.0  # 12 / 8 y 16 / 8
    short = run(home_games([1, 2, 3, 0, 1, 2, 3]) + away_games([2] * 8))
    assert short.status == NO_ESTIMATE and short.no_estimate_reasons == ("insufficient_home_sample",)
    assert short.lambda_home is None and short.lambda_away is None
    assert (short.home.eligible, short.home.selected, short.home.selected_fixture_ids) == (7, 0, ())


def test_one_venue_sufficient_is_not_an_estimate():
    result = run(home_games([1] * 10) + away_games([1] * 5))
    assert result.status == NO_ESTIMATE and result.no_estimate_reasons == ("insufficient_away_sample",)
    assert result.lambda_home is None and result.lambda_away is None and result.home.no_estimate_reason is None


def test_valid_zero_mean_is_an_estimate_not_a_missing_value():
    result = run(home_games([0] * 8) + away_games([0] * 9))
    assert result.status == ESTIMATE and result.lambda_home == 0.0 and result.lambda_away == 0.0


def test_empty_history_is_no_estimate_without_fallback_rates():
    result = run([])
    assert result.status == NO_ESTIMATE and result.no_estimate_reasons == ("insufficient_home_sample", "insufficient_away_sample")
    assert result.lambda_home is None and result.lambda_away is None and result.history_records == 0
    assert result.home.candidates == result.away.candidates == 0


# --- Sedes y goles a 90' ----------------------------------------------------------------------------

def test_home_and_away_roles_are_isolated():
    other_roles = ([match(300 + i, 700 + i, HOME_TEAM, T - timedelta(days=2 + i, hours=1), 0, 9) for i in range(10)]  # local objetivo fuera
                   + [match(320 + i, AWAY_TEAM, 800 + i, T - timedelta(days=2 + i, hours=2), 9, 0) for i in range(10)])  # visitante en casa
    result = run(home_games([1] * 8) + away_games([1] * 8) + other_roles)
    assert result.lambda_home == 1.0 and result.lambda_away == 1.0
    assert result.home.candidates == 8 and result.away.candidates == 8


def test_a_meeting_of_the_two_target_teams_counts_for_each_with_its_own_goals():
    meeting = match(340, HOME_TEAM, AWAY_TEAM, T - timedelta(days=30), 3, 0)
    result = run(home_games([1] * 7) + away_games([2] * 7) + [meeting])
    assert result.lambda_home == 1.25 and result.lambda_away == 1.75  # (7 + 3) / 8 y (14 + 0) / 8
    assert 340 in result.home.selected_fixture_ids and 340 in result.away.selected_fixture_ids


def test_ft_aet_and_pen_use_only_the_90_minute_score():
    home = [match(1, HOME_TEAM, 501, T - timedelta(days=1), 2, 0),
            match(2, HOME_TEAM, 502, T - timedelta(days=2), 1, 1, "AET", goals_home=3, goals_away=1, extratime_home=2, extratime_away=0),
            match(3, HOME_TEAM, 503, T - timedelta(days=3), 0, 0, "PEN", penalty_home=4, penalty_away=3)] + home_games([1] * 5, first_id=4, first_day=4)
    away = [match(101, 601, AWAY_TEAM, T - timedelta(days=1, hours=3), 1, 2, "AET", goals_home=1, goals_away=4)] + away_games([0] * 7, first_id=102, first_day=2)
    result = run(home + away)
    assert result.lambda_home == 1.0 and result.lambda_away == 0.25  # (2 + 1 + 0 + 5) / 8 y 2 / 8


@pytest.mark.parametrize("status, home_goals, away_goals, reason", [("NS", None, None, NOT_FINISHED), ("PST", None, None, NOT_FINISHED),
                                                                      ("FT", None, 1, FULLTIME_INCOMPLETE), ("FT", -1, 0, FULLTIME_INVALID),
                                                                      ("AET", 1, True, FULLTIME_INVALID)])
def test_unsettled_matches_keep_canonical_settlement_reasons(status, home_goals, away_goals, reason):
    bad = match(200, HOME_TEAM, 900, T - timedelta(hours=30), home_goals, away_goals, status)
    result = run([bad] + home_games([1] * 8) + away_games([1] * 8))
    assert dict(result.home.excluded_by_reason) == {reason: 1} and result.lambda_home == 1.0


def test_wrong_competition_is_excluded_and_never_borrowed():
    other = [match(210 + i, HOME_TEAM, 910 + i, T - timedelta(hours=10 + i), 9, 0, competition=8) for i in range(3)]
    result = run(other + home_games([1] * 8) + away_games([1] * 8))
    assert dict(result.home.excluded_by_reason) == {WRONG_COMPETITION: 3} and result.lambda_home == 1.0
    assert result.home.candidates == result.home.eligible + sum(result.home.excluded_by_reason.values()) == 11


def test_cross_season_needs_membership_evidence_observed_by_the_cutoff():
    history = home_games([1] * 6) + home_games([3] * 4, first_id=50, first_day=200, season=69) + away_games([1] * 8)
    without = run(history)
    assert without.status == NO_ESTIMATE and without.no_estimate_reasons == ("insufficient_home_sample",)
    assert dict(without.home.excluded_by_reason) == {CROSS_SEASON_MEMBERSHIP_NOT_EVIDENCED: 4}
    proof = {"team_id": HOME_TEAM, "competition_id": COMPETITION, "season_id": 69, "observed_at": T - timedelta(days=150)}
    assert run(history, [proof]).lambda_home == 1.8  # (6 * 1 + 4 * 3) / 10
    for useless in (dict(proof, observed_at=T + timedelta(seconds=1)), dict(proof, team_id=AWAY_TEAM), dict(proof, competition_id=8),
                    dict(proof, season_id=68)):
        assert run(history, [useless]).status == NO_ESTIMATE


def test_the_target_fixture_is_excluded_even_if_supplied():
    leaked = match(1000, HOME_TEAM, AWAY_TEAM, T - timedelta(days=3), 5, 5)
    result = run([leaked] + home_games([1] * 8) + away_games([1] * 8))
    assert dict(result.home.excluded_by_reason) == {TARGET_FIXTURE: 1} == dict(result.away.excluded_by_reason)
    assert result.lambda_home == 1.0 and result.lambda_away == 1.0


# --- PIT -------------------------------------------------------------------------------------------

def test_future_kickoff_late_result_and_missing_observed_at_are_excluded():
    late = [match(220, HOME_TEAM, 920, T, 4, 0, observed=T + timedelta(hours=3)),  # kickoff = T: no es anterior a T
            match(221, HOME_TEAM, 921, T - timedelta(hours=1), 4, 0, observed=T + timedelta(hours=1)),  # conocido después de T
            match(222, HOME_TEAM, 922, T - timedelta(days=2), 4, 0, observed=None)]  # sin evidencia de cuándo se observó
    result = run(late + home_games([1] * 8) + away_games([1] * 8))
    assert dict(result.home.excluded_by_reason) == {KICKOFF_NOT_BEFORE_CUTOFF: 1, RESULT_OBSERVED_AFTER_CUTOFF: 1, RESULT_OBSERVED_AT_MISSING: 1}
    assert result.lambda_home == 1.0


def test_observed_exactly_at_the_cutoff_is_eligible():
    edge = match(230, HOME_TEAM, 930, T - timedelta(hours=3), 9, 0, observed=T)
    result = run([edge] + home_games([1] * 7) + away_games([1] * 8))
    assert result.status == ESTIMATE and result.lambda_home == 2.0  # (9 + 7) / 8


def test_same_day_prior_match_observed_before_the_cutoff_counts():
    same_day = [match(240, HOME_TEAM, 940, T - timedelta(hours=5), 5, 0, observed=T - timedelta(hours=3)),
                match(241, HOME_TEAM, 941, T - timedelta(hours=2), 9, 0, observed=T + timedelta(minutes=5))]
    result = run(same_day + home_games([1] * 7) + away_games([1] * 8))
    assert result.status == ESTIMATE and result.lambda_home == 1.5  # (5 + 7) / 8; el de después de T no cuenta
    assert result.home.selected_fixture_ids[0] == 240 and dict(result.home.excluded_by_reason) == {RESULT_OBSERVED_AFTER_CUTOFF: 1}


def test_eligibility_is_applied_before_the_twenty_most_recent_are_taken():
    unknown_recent = home_games([9] * 20, first_id=250, first_day=1, observed=None)  # los 20 más recientes no son elegibles
    result = run(unknown_recent + home_games([2] * 8, first_id=1, first_day=40) + away_games([1] * 8))
    assert result.status == ESTIMATE and result.lambda_home == 2.0 and result.home.selected == 8


# --- Cronología ------------------------------------------------------------------------------------

def _with_kickoff(rows, index, kickoff):
    return [dict(r, kickoff_at=kickoff, result_observed_at=kickoff + timedelta(hours=2)) if i == index else r for i, r in enumerate(rows)]


def test_a_tie_across_the_twentieth_position_is_ambiguous():
    rows = home_games([1] * 21)
    tied = _with_kickoff(rows, 20, rows[19]["kickoff_at"])  # el 20.º y el 21.º comparten instante
    result = run(tied + away_games([1] * 8))
    assert result.status == NO_ESTIMATE and result.no_estimate_reasons == ("ambiguous_home_selection",)
    assert result.lambda_home is None and result.lambda_away is None and result.home.selected_fixture_ids == ()


def test_a_tie_inside_the_selection_or_without_a_21st_match_is_harmless():
    rows = home_games([1, 2, 3, 4] + [1] * 17)
    inside = _with_kickoff(rows, 3, rows[2]["kickoff_at"])  # 3.º y 4.º empatados, los dos dentro de los 20
    result = run(inside + away_games([1] * 8))
    assert result.status == ESTIMATE and result.lambda_home == 1.3  # (1 + 2 + 3 + 4 + 16 * 1) / 20 = 26 / 20
    assert result.home.selected_fixture_ids[2:4] == (3, 4)  # dentro del empate, fixture_id solo fija el orden
    twenty = home_games([1] * 20)
    boundary = _with_kickoff(twenty, 19, twenty[18]["kickoff_at"])
    assert run(boundary + away_games([1] * 8)).status == ESTIMATE


def test_equal_instants_in_different_zones_are_a_tie():
    rows = home_games([1] * 21)
    same_instant = rows[19]["kickoff_at"].astimezone(timezone(timedelta(hours=-3)))
    assert run(_with_kickoff(rows, 20, same_instant) + away_games([1] * 8)).no_estimate_reasons == ("ambiguous_home_selection",)


def test_result_does_not_depend_on_input_order():
    history = home_games([2, 1, 0, 3] * 6) + away_games([0, 1, 2] * 4) + [match(340, HOME_TEAM, AWAY_TEAM, T - timedelta(days=60), 3, 1)]
    first = run(history)
    assert run(list(reversed(history))) == first and run(history[1::2] + history[0::2]) == first


# --- Evidencia inválida (EvidenceContractError) -----------------------------------------------------

def test_duplicate_fixture_ids_are_rejected():
    rows = home_games([1] * 8) + away_games([1] * 8)
    with pytest.raises(EvidenceContractError, match="repetido"):
        run(rows + [dict(rows[0])])


@pytest.mark.parametrize("case", ["cutoff", "kickoff", "observed", "membership", "target", "cutoff_date"])
def test_naive_or_non_datetime_timestamps_are_rejected(case):
    rows = home_games([1] * 8) + away_games([1] * 8)
    naive = T.replace(tzinfo=None)
    kwargs = {"history": rows, "memberships": (), "target": TARGET, "cutoff": T}
    if case == "cutoff":
        kwargs["cutoff"] = naive
    elif case == "cutoff_date":
        kwargs["cutoff"] = date(2026, 3, 1)
    elif case == "kickoff":
        kwargs["history"] = [dict(rows[0], kickoff_at=naive - timedelta(days=1))] + rows[1:]
    elif case == "observed":
        kwargs["history"] = [dict(rows[0], result_observed_at=naive - timedelta(hours=1))] + rows[1:]
    elif case == "membership":
        kwargs["memberships"] = [{"team_id": HOME_TEAM, "competition_id": COMPETITION, "season_id": 69, "observed_at": naive}]
    else:
        kwargs["target"] = dict(TARGET, kickoff_at=naive + timedelta(hours=2))
    with pytest.raises(EvidenceContractError, match="zona horaria"):
        run(**kwargs)


def test_contradictory_or_malformed_evidence_is_rejected():
    rows = home_games([1] * 8) + away_games([1] * 8)
    first = rows[0]
    bad_inputs = [
        {"history": [dict(first, result_observed_at=first["kickoff_at"] - timedelta(minutes=1))] + rows[1:]},  # final antes de su kickoff
        {"history": [dict(first, away_team_id=HOME_TEAM)] + rows[1:]},  # el mismo equipo en las dos sedes
        {"history": [dict(first, fixture_id=True)] + rows[1:]},
        {"history": [dict(first, season_id="70")] + rows[1:]},
        {"history": [{k: v for k, v in first.items() if k != "result_observed_at"}] + rows[1:]},  # falta el campo (no es None)
        {"history": tuple(rows)[0]},  # un Mapping suelto, no una lista
        {"history": (r for r in rows)},
        {"memberships": {"team_id": HOME_TEAM}},
        {"memberships": [{"team_id": HOME_TEAM, "competition_id": COMPETITION, "season_id": 69}]},
        {"target": dict(TARGET, kickoff_at=T - timedelta(minutes=1))},  # T posterior al kickoff objetivo
        {"target": {k: v for k, v in TARGET.items() if k != "season_id"}},
        {"target": dict(TARGET, away_team_id=HOME_TEAM)},
    ]
    for bad in bad_inputs:
        kwargs = {"history": rows, "memberships": (), "target": TARGET, "cutoff": T, **bad}
        with pytest.raises(EvidenceContractError):
            run(**kwargs)


# --- Pureza ----------------------------------------------------------------------------------------

def test_inputs_are_not_mutated_and_results_are_immutable():
    history = home_games([1, 2] * 6) + away_games([3] * 9)
    memberships = [{"team_id": HOME_TEAM, "competition_id": COMPETITION, "season_id": 69, "observed_at": T - timedelta(days=1)}]
    target, before = dict(TARGET), copy.deepcopy((history, memberships, TARGET))
    result = run(history, memberships, target)
    assert (history, memberships, target) == before and all(run(history, memberships, target) == result for _ in range(3))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.lambda_home = 0.0
    with pytest.raises(TypeError):
        result.home.excluded_by_reason["x"] = 1


def test_the_module_imports_without_the_app_or_a_database():
    code = ("import sys; import research.venue_goal_rates.baseline; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"


# --- Compatibilidad con independent_poisson_reference (sin cambiarla) ------------------------------

def test_estimated_rates_feed_the_independent_poisson_reference():
    result = run(home_games([2, 1, 0, 3] * 5) + away_games([0, 1, 1, 2, 0, 3, 1, 0, 2, 0]))
    cells = independent_poisson_reference(result.lambda_home, result.lambda_away)
    assert len(cells) == 64 and [(h, a) for h, a, _ in cells] == list(CELLS)
    assert validated_cells(cells)  # contrato MOD-DEV-066-R1 sin cambios
    p00 = dict(((h, a), p) for h, a, p in cells)[(0, 0)]
    assert p00 == pytest.approx(math.exp(-1.5) * math.exp(-1.0), rel=1e-12)


def test_large_valid_rates_are_accepted_by_the_reference():
    result = run(home_games([30] * 8) + away_games([25] * 8))
    assert result.lambda_home == 30.0 and result.lambda_away == 25.0
    cells = dict(((h, a), p) for h, a, p in independent_poisson_reference(result.lambda_home, result.lambda_away))
    assert cells[("7+", "7+")] > 0.99 and math.fsum(cells.values()) == pytest.approx(1.0, abs=1e-9)
