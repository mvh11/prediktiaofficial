"""MOD-DATA-083: vectores de aceptación V1-V7 del CONSUMIDOR research.venue_goal_rates.baseline. Puro: sin BD, sin red.
    python -B -m pytest research/tests/test_venue_goal_rates_pit_acceptance.py -q -p no:cacheprovider   (desde backend/)

EVIDENCIA ARTIFICIAL: todos los partidos, instantes, ids y pertenencias son sintéticos y deterministas. No son
fixtures históricos certificados por Data Integrity, ni recibos autenticados, ni atestaciones de completitud.

Tres planos que estos tests mantienen separados:
1. Lo que el consumidor puro SÍ comprueba (estructura y cronología de la evidencia suministrada): se afirma aquí.
2. Lo que exige autoridad externa de Data Integrity (autenticidad de result_observed_at, pertenencia histórica,
   resolución STRICT_KNOWLEDGE de estados en conflicto, identidad portable, universo de partidos): el consumidor no
   puede verificarlo; se afirma que NO lo reclama y que lo declara en evidence_limitations.
3. Garantías históricas no soportadas: ningún resultado se presenta como TRUE_PIT ni como historia completa.

Clasificaciones externas: tal como las reporta DI-MOD-DATA-075-PIT-SOURCE-MAP-001-RESULT, cuyo informe no estaba
disponible para inspección local; NO se han verificado de forma independiente. Solo TEMPORAL_AMBIGUITY y el contrato
A6 STRICT_KNOWLEDGE (t* = max(observed_at <= T); estados distintos en t* = TEMPORAL_AMBIGUITY, sin ganador) se han
contrastado con código y documentación ya integrados (app/schemas/fixture_knowledge.py, docs/data-integrity-status.md).
"""

import dataclasses
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from research.m58.goal_labels import FULLTIME_INVALID
from research.m58.labels import FULLTIME_INCOMPLETE, NOT_FINISHED
from research.venue_goal_rates.baseline import (CROSS_SEASON_MEMBERSHIP_NOT_EVIDENCED, ESTIMATE, EVIDENCE_LIMITATIONS, HISTORY_FIELDS,
                                                KICKOFF_NOT_BEFORE_CUTOFF, NO_ESTIMATE, RESULT_OBSERVED_AFTER_CUTOFF,
                                                RESULT_OBSERVED_AT_MISSING, EvidenceContractError, VenueGoalRates,
                                                estimate_venue_goal_rates)

# Clasificación externa por vector (según el informe DI reportado; no verificada aquí). Nunca la emite el consumidor.
EXTERNAL_CLASSIFICATION = {
    "V1": ("TRUE_PIT_NOT_PROVEN_BY_CONSUMER",),
    "V2": ("RETROSPECTIVE_ONLY",),
    "V3": ("RETROSPECTIVE_ONLY",),
    "V4": ("CROSS_SEASON_MEMBERSHIP_UNPROVEN",),
    "V5": ("TEMPORAL_AMBIGUITY", "UPSTREAM_REQUIRED"),
    "V6": (),  # solo cronología
    "V7": ("FIXTURE_UNIVERSE_UNPROVEN",),
    "SCORE": ("RESULT_SCORE_UNPROVEN",),
    "IDENTITY": ("PORTABLE_IDENTITY_UNPROVEN",),
}
FORBIDDEN_CLAIMS = ("TRUE_PIT", "AUTHENTICATED", "CERTIFIED", "RETROSPECTIVE_ONLY", "TEMPORAL_AMBIGUITY", "UNPROVEN", "COMPLETE_HISTORY")

UTC = timezone.utc
T = datetime(2026, 4, 18, 19, 0, tzinfo=UTC)  # corte; el objetivo empieza a las 21:00 UTC del mismo día
HOME_TEAM, AWAY_TEAM, COMPETITION, SEASON, PREVIOUS_SEASON = 4101, 4202, 39, 2025, 2024
TARGET = {"fixture_id": 900000, "competition_id": COMPETITION, "season_id": SEASON, "home_team_id": HOME_TEAM,
          "away_team_id": AWAY_TEAM, "kickoff_at": T + timedelta(hours=2)}
RESULT_FIELDS = {f.name for f in dataclasses.fields(VenueGoalRates)}


def fixture(fixture_id, home, away, kickoff, fulltime_home, fulltime_away, *, observed, status="FT", competition=COMPETITION,
            season=SEASON, **extra):
    """Un partido sintético; result_observed_at siempre explícito (nunca derivado del kickoff)."""
    return {"fixture_id": fixture_id, "competition_id": competition, "season_id": season, "home_team_id": home, "away_team_id": away,
            "kickoff_at": kickoff, "status_short": status, "fulltime_home": fulltime_home, "fulltime_away": fulltime_away,
            "result_observed_at": observed, **extra}


def home_block(goals, first_id, first_day=1, **kw):
    """Partidos EN CASA del local objetivo, uno por día hacia atrás; goals[0] es el más reciente."""
    rows = []
    for i, g in enumerate(goals):
        kickoff = T - timedelta(days=first_day + i, hours=1)
        rows.append(fixture(first_id + i, HOME_TEAM, 5000 + first_id + i, kickoff, g, 0, observed=kickoff + timedelta(hours=2), **kw))
    return rows


def away_block(goals, first_id, first_day=1, **kw):
    """Partidos FUERA del visitante objetivo, uno por día hacia atrás; goals[0] es el más reciente."""
    rows = []
    for i, g in enumerate(goals):
        kickoff = T - timedelta(days=first_day + i, hours=4)
        rows.append(fixture(first_id + i, 6000 + first_id + i, AWAY_TEAM, kickoff, 0, g, observed=kickoff + timedelta(hours=2), **kw))
    return rows


def membership(team_id=HOME_TEAM, season=PREVIOUS_SEASON, observed=T - timedelta(days=200), competition=COMPETITION):
    return {"team_id": team_id, "competition_id": competition, "season_id": season, "observed_at": observed}


def run(history, memberships=()):
    result = estimate_venue_goal_rates(TARGET, history, T, memberships)
    assert_no_external_authority_claimed(result)
    return result


def assert_no_external_authority_claimed(result):
    """Plano 3: solo ESTIMATE/NO_ESTIMATE, sin campos de certificación y con los límites de evidencia declarados."""
    assert result.status in (ESTIMATE, NO_ESTIMATE)
    assert RESULT_FIELDS == {"status", "no_estimate_reasons", "lambda_home", "lambda_away", "home", "away", "target_fixture_id", "cutoff",
                             "policy", "n_recent", "m_minimum", "history_records", "evidence_limitations"}
    assert result.evidence_limitations == EVIDENCE_LIMITATIONS
    assert {"HISTORY_COMPLETENESS_NOT_VERIFIED", "OBSERVED_AT_AUTHENTICITY_NOT_VERIFIED"} <= set(result.evidence_limitations)
    text = repr(result).upper()
    assert not any(claim in text for claim in FORBIDDEN_CLAIMS)


# --- Contrato de entrada: sin parámetros de autoridad --------------------------------------------------

def test_the_consumer_api_has_no_caller_controlled_authority_parameter():
    assert list(inspect.signature(estimate_venue_goal_rates).parameters) == ["target", "history", "cutoff", "memberships"]
    assert HISTORY_FIELDS == ("fixture_id", "competition_id", "season_id", "home_team_id", "away_team_id", "kickoff_at", "status_short",
                              "fulltime_home", "fulltime_away", "result_observed_at")  # ni knowledge_status, ni true_pit, ni complete


def test_caller_asserted_authority_flags_are_ignored_not_trusted():
    plain = home_block([1] * 8, 1100) + away_block([1] * 8, 1200)
    flagged = [dict(r, true_pit=True, di_certified=True, complete=True, knowledge_status="KNOWN") for r in plain]
    assert run(flagged) == run(plain)  # un booleano del llamador no certifica nada ni cambia el resultado


# --- V1: partido previo válido --------------------------------------------------------------------------

@pytest.mark.parametrize("observed_offset", [timedelta(hours=2), None], ids=["kickoff<observed<T", "observed==T"])
def test_v1_valid_previous_fixture_is_consumer_eligible_conditionally(observed_offset):
    kickoff = T - timedelta(days=3)
    observed = kickoff + observed_offset if observed_offset is not None else T
    previous = fixture(1001, HOME_TEAM, 5101, kickoff, 4, 1, observed=observed)
    assert kickoff < previous["result_observed_at"] <= T
    result = run([previous] + home_block([1] * 7, 1010, first_day=4) + away_block([2] * 8, 1020))
    assert result.status == ESTIMATE and 1001 in result.home.selected_fixture_ids
    assert result.lambda_home == (4 + 7) / 8 and result.lambda_away == 2.0  # usa los goles a 90' del local en su sede
    # CONSUMER_ELIGIBLE_CONDITIONALLY, no AUTHENTIC_TRUE_PIT_PROVEN: la autenticidad del instante y la completitud se declaran no verificadas
    assert "OBSERVED_AT_AUTHENTICITY_NOT_VERIFIED" in result.evidence_limitations
    assert "HISTORY_COMPLETENESS_NOT_VERIFIED" in result.evidence_limitations


# --- V2: resultado recibido después de T ----------------------------------------------------------------

def test_v2_result_observed_after_cutoff_is_excluded_and_contributes_to_neither_mean():
    kickoff = T - timedelta(hours=6)
    late = fixture(2001, HOME_TEAM, AWAY_TEAM, kickoff, 9, 9, observed=T + timedelta(hours=1))  # candidato en las dos sedes
    assert kickoff + timedelta(hours=2) <= T  # un instante derivado del kickoff sería elegible: no se deriva
    before = dict(late)
    result = run([late] + home_block([1] * 8, 2010) + away_block([1] * 8, 2030))
    assert late == before  # el instante suministrado no se reemplaza
    assert result.lambda_home == 1.0 and result.lambda_away == 1.0
    assert dict(result.home.excluded_by_reason) == {RESULT_OBSERVED_AFTER_CUTOFF: 1} == dict(result.away.excluded_by_reason)
    assert 2001 not in result.home.selected_fixture_ids + result.away.selected_fixture_ids


def test_v2_selection_of_the_twenty_happens_after_eligibility_filtering():
    late = fixture(2101, HOME_TEAM, 5201, T - timedelta(hours=3), 9, 0, observed=T + timedelta(minutes=1))  # el más reciente
    eligible = home_block([1] * 20, 2110)
    result = run([late] + eligible + away_block([1] * 8, 2140))
    assert result.status == ESTIMATE and result.home.eligible == 20 and result.home.selected == 20
    assert result.home.selected_fixture_ids == tuple(r["fixture_id"] for r in eligible) and result.lambda_home == 1.0


# --- V3: arranque retrospectivo -------------------------------------------------------------------------

HISTORIC_KICKOFF = T - timedelta(days=400)  # completado hace más de un año


def bootstrap_case(observed):
    old = fixture(3001, HOME_TEAM, 5301, HISTORIC_KICKOFF, 5, 0, observed=observed, season=SEASON)
    return run([old] + home_block([1] * 7, 3010) + away_block([1] * 8, 3030))


def test_v3_retrospective_bootstrap_observation_after_cutoff_is_excluded():
    result = bootstrap_case(T + timedelta(days=30))  # única observación suministrada: la carga retrospectiva, después de T
    assert dict(result.home.excluded_by_reason) == {RESULT_OBSERVED_AFTER_CUTOFF: 1}
    assert result.status == NO_ESTIMATE and result.no_estimate_reasons == ("insufficient_home_sample",)
    assert result.lambda_home is None and result.lambda_away is None  # sin relleno ni instante sustituto


def test_v3_missing_observation_is_not_derived_from_the_historic_kickoff():
    result = bootstrap_case(None)
    assert dict(result.home.excluded_by_reason) == {RESULT_OBSERVED_AT_MISSING: 1} and result.status == NO_ESTIMATE


def test_v3_a_fabricated_earlier_timestamp_passes_shape_validation_external_authenticity_limit():
    # Instante estructuralmente válido (kickoff < observed <= T) pero inventado: el consumidor puro NO puede distinguirlo de
    # una observación histórica auténtica. Es un límite de autenticidad externo (DI), no una detección del consumidor.
    forged = bootstrap_case(HISTORIC_KICKOFF + timedelta(hours=2))
    assert forged.status == ESTIMATE and 3001 in forged.home.selected_fixture_ids
    assert "OBSERVED_AT_AUTHENTICITY_NOT_VERIFIED" in forged.evidence_limitations
    implausible = bootstrap_case(HISTORIC_KICKOFF + timedelta(minutes=1))  # "FT" un minuto después del kickoff: tampoco detectable
    assert implausible.status == ESTIMATE


def test_v3_only_a_final_state_observed_before_its_own_kickoff_is_structurally_rejected():
    with pytest.raises(EvidenceContractError, match="antes de su propio kickoff"):
        bootstrap_case(HISTORIC_KICKOFF - timedelta(minutes=1))


# --- V4: pertenencia de otra temporada ------------------------------------------------------------------

def cross_season_history():
    previous = fixture(4001, HOME_TEAM, 5401, T - timedelta(days=240), 5, 0, observed=T - timedelta(days=240) + timedelta(hours=2),
                       season=PREVIOUS_SEASON)
    return [previous] + home_block([1] * 7, 4010) + away_block([1] * 8, 4030)


def test_v4a_membership_observed_by_the_cutoff_makes_the_fixture_structurally_eligible():
    for observed in (T - timedelta(days=200), T):  # inclusivo: observed_at <= T
        result = run(cross_season_history(), [membership(observed=observed)])
        assert result.status == ESTIMATE and 4001 in result.home.selected_fixture_ids and result.lambda_home == (5 + 7) / 8
        assert "COMPETITION_AND_MEMBERSHIP_LIMITED_TO_SUPPLIED_EVIDENCE" in result.evidence_limitations


@pytest.mark.parametrize("memberships", [
    (),  # B: sin pertenencia
    (membership(observed=T + timedelta(seconds=1)),),  # C: observada después de T
    (membership(team_id=5401),),  # del rival, no del equipo objetivo
    (membership(competition=40),), (membership(season=2023),),
], ids=["B-missing", "C-after-cutoff", "opponent-only", "other-competition", "other-season"])
def test_v4bc_membership_not_evidenced_by_the_cutoff_excludes_the_fixture(memberships):
    result = run(cross_season_history(), list(memberships))
    assert dict(result.home.excluded_by_reason) == {CROSS_SEASON_MEMBERSHIP_NOT_EVIDENCED: 1}
    assert result.status == NO_ESTIMATE and result.no_estimate_reasons == ("insufficient_home_sample",)


def test_v4_team_names_are_never_identity_proof():
    named_target = dict(TARGET, home_team_name="Synthetic Home FC")
    homonym = fixture(4101, 4999, 5402, T - timedelta(days=2), 9, 0, observed=T - timedelta(days=2) + timedelta(hours=2),
                      home_team_name="Synthetic Home FC")  # mismo nombre, otro id estable
    named_membership = dict(membership(team_id=4999), team_name="Synthetic Home FC")
    result = estimate_venue_goal_rates(named_target, cross_season_history() + [homonym], T, [named_membership])
    assert_no_external_authority_claimed(result)
    assert result.home.candidates == 8 and 4101 not in result.home.selected_fixture_ids
    assert dict(result.home.excluded_by_reason) == {CROSS_SEASON_MEMBERSHIP_NOT_EVIDENCED: 1}


# --- V5: evidencia en conflicto en el mismo instante ----------------------------------------------------

def conflicting_pair():
    kickoff = T - timedelta(days=2)
    same_instant = kickoff + timedelta(hours=2)
    return (fixture(5001, HOME_TEAM, 5501, kickoff, 2, 1, observed=same_instant),
            fixture(5001, HOME_TEAM, 5501, kickoff, 3, 1, observed=same_instant))  # mismo fixture y observed_at, otro estado


@pytest.mark.parametrize("order", [0, 1])
def test_v5_two_conflicting_states_for_one_fixture_fail_closed_without_choosing(order):
    a, b = conflicting_pair()
    pair = [a, b] if order == 0 else [b, a]
    with pytest.raises(EvidenceContractError, match="fixture_id repetido 5001"):
        estimate_venue_goal_rates(TARGET, pair + home_block([1] * 7, 5010) + away_block([1] * 8, 5030), T)


def test_v5_identical_repeated_state_is_also_rejected_fail_closed():
    a, _ = conflicting_pair()
    with pytest.raises(EvidenceContractError, match="repetido"):
        estimate_venue_goal_rates(TARGET, [a, dict(a)] + home_block([1] * 7, 5010) + away_block([1] * 8, 5030), T)


def test_v5_a_single_row_cannot_represent_upstream_temporal_ambiguity_upstream_required():
    # El consumidor recibe historia ya cualificada, no conjuntos de observaciones A6. Con una sola fila no hay nada que
    # resolver y no tiene campo de estado de conocimiento: un marcador no se lee. Excluir TEMPORAL_AMBIGUITY es
    # responsabilidad de STRICT_KNOWLEDGE aguas arriba (UPSTREAM_REQUIRED); aquí no se inventa ninguna selección temporal.
    a, _ = conflicting_pair()
    rest = home_block([1] * 7, 5010) + away_block([1] * 8, 5030)
    marked = dict(a, knowledge_status="TEMPORAL_AMBIGUITY")
    assert "knowledge_status" not in HISTORY_FIELDS
    assert run([marked] + rest) == run([a] + rest)


# --- V6: partido previo del mismo día -------------------------------------------------------------------

def test_v6_same_day_prior_fixture_is_eligible_without_a_day_embargo():
    same_day = fixture(6001, HOME_TEAM, 5601, datetime(2026, 4, 18, 12, 30, tzinfo=UTC), 6, 0,
                       observed=datetime(2026, 4, 18, 14, 45, tzinfo=UTC))
    assert same_day["kickoff_at"].date() == T.date() and same_day["result_observed_at"] < T
    result = run([same_day] + home_block([1] * 7, 6010) + away_block([1] * 8, 6030))
    assert result.status == ESTIMATE and result.home.selected_fixture_ids[0] == 6001 and result.lambda_home == (6 + 7) / 8


def test_v6_same_day_chronology_edges():
    utc_minus_3 = timezone(timedelta(hours=-3))
    rows = [fixture(6101, HOME_TEAM, 5701, datetime(2026, 4, 18, 16, 0, tzinfo=UTC), 9, 0,
                    observed=T + timedelta(seconds=1)),  # mismo día, observado después de T
            fixture(6102, HOME_TEAM, 5702, T, 9, 0, observed=T + timedelta(hours=2)),  # kickoff = T
            fixture(6103, HOME_TEAM, 5703, datetime(2026, 4, 18, 10, 0, tzinfo=utc_minus_3), 3, 0,
                    observed=datetime(2026, 4, 18, 15, 0, tzinfo=utc_minus_3))]  # 13:00Z y 18:00Z en otra zona: elegible
    result = run(rows + home_block([1] * 7, 6110) + away_block([1] * 8, 6130))
    assert dict(result.home.excluded_by_reason) == {KICKOFF_NOT_BEFORE_CUTOFF: 1, RESULT_OBSERVED_AFTER_CUTOFF: 1}
    assert result.home.selected_fixture_ids[0] == 6103 and result.lambda_home == (3 + 7) / 8


# --- V7: ventana de muestra y completitud ---------------------------------------------------------------

def test_v7a_exactly_eight_per_venue_estimates_with_exact_means():
    result = run(home_block([0, 1, 2, 3, 4, 1, 2, 3], 7001) + away_block([1, 1, 0, 2, 0, 1, 3, 0], 7020))
    assert result.status == ESTIMATE and result.lambda_home == 2.0 and result.lambda_away == 1.0  # 16 / 8 y 8 / 8
    assert result.home.eligible == result.home.selected == result.away.eligible == result.away.selected == 8


@pytest.mark.parametrize("home_n, away_n, reason", [(7, 8, "insufficient_home_sample"), (8, 7, "insufficient_away_sample")])
def test_v7b_seven_eligible_at_either_venue_is_no_estimate(home_n, away_n, reason):
    result = run(home_block([2] * home_n, 7101) + away_block([2] * away_n, 7120))
    assert result.status == NO_ESTIMATE and result.no_estimate_reasons == (reason,)
    assert result.lambda_home is None and result.lambda_away is None


def test_v7c_more_than_twenty_selects_only_the_twenty_most_recent():
    home, away = home_block([1] * 20 + [9] * 5, 7201), away_block([2] * 20 + [9] * 3, 7240)
    result = run(home + away)
    assert result.status == ESTIMATE and result.lambda_home == 1.0 and result.lambda_away == 2.0
    assert result.home.selected_fixture_ids == tuple(range(7201, 7221)) and result.away.selected_fixture_ids == tuple(range(7240, 7260))
    assert (result.home.eligible, result.away.eligible) == (25, 23)


@pytest.mark.parametrize("venue", ["home", "away"])
def test_v7d_tie_across_the_twenty_boundary_is_ambiguous(venue):
    home, away = home_block([1] * 21, 7301), away_block([1] * 21, 7340)
    rows = home if venue == "home" else away
    rows[20] = dict(rows[20], kickoff_at=rows[19]["kickoff_at"], result_observed_at=rows[19]["result_observed_at"])
    result = run(home + away)
    assert result.status == NO_ESTIMATE and result.no_estimate_reasons == (f"ambiguous_{venue}_selection",)
    assert result.lambda_home is None and result.lambda_away is None


def test_v7e_twenty_supplied_candidates_do_not_prove_the_fixture_universe_complete():
    supplied = home_block([1] * 20, 7401, first_day=2) + away_block([1] * 8, 7440)
    result = run(supplied)
    assert result.status == ESTIMATE and result.home.candidates == result.home.selected == 20 and result.lambda_home == 1.0
    assert "HISTORY_COMPLETENESS_NOT_VERIFIED" in result.evidence_limitations  # ESTIMATE no es AUTHENTICATED_N20
    # Un partido elegible omitido por el llamador cambiaría la estimación; el consumidor no puede saber si existe fuera de la entrada.
    omitted = home_block([9], 7499, first_day=1)
    assert run(supplied + omitted).lambda_home == (9 + 19) / 20 != result.lambda_home
    assert run([dict(r, complete=True) for r in supplied]) == result  # complete=True del llamador no certifica nada


# --- Límites adicionales: marcador a 90' e identidad ----------------------------------------------------

def test_only_the_90_minute_fulltime_pair_is_used_never_extra_time_goals():
    aet_kickoff, pen_kickoff = T - timedelta(days=1), T - timedelta(days=2)
    rows = [fixture(8001, HOME_TEAM, 5801, aet_kickoff, 1, 1, status="AET", observed=aet_kickoff + timedelta(hours=3),
                    goals_home=4, goals_away=2, extratime_home=3, extratime_away=1),
            fixture(8002, HOME_TEAM, 5802, pen_kickoff, 0, 0, status="PEN", observed=pen_kickoff + timedelta(hours=3),
                    goals_home=0, goals_away=0, penalty_home=5, penalty_away=4)]
    result = run(rows + home_block([2] * 6, 8010, first_day=3) + away_block([1] * 8, 8030))
    assert result.lambda_home == (1 + 0 + 12) / 8  # nunca 4 (con prórroga) ni penaltis


@pytest.mark.parametrize("status, fulltime_home, fulltime_away, reason", [
    ("FT", None, 0, FULLTIME_INCOMPLETE), ("AET", 1, None, FULLTIME_INCOMPLETE), ("FT", -1, 0, FULLTIME_INVALID),
    ("FT", 1.0, 0, FULLTIME_INVALID), ("PEN", True, 0, FULLTIME_INVALID), ("FT", "2", 0, FULLTIME_INVALID),
    ("2H", 3, 0, NOT_FINISHED), ("NS", None, None, NOT_FINISHED), ("AWD", 3, 0, NOT_FINISHED),
], ids=repr)
def test_missing_or_invalid_90_minute_score_is_excluded_with_its_canonical_reason(status, fulltime_home, fulltime_away, reason):
    kickoff = T - timedelta(hours=30)
    bad = fixture(8101, HOME_TEAM, 5901, kickoff, fulltime_home, fulltime_away, status=status, observed=kickoff + timedelta(hours=2),
                  goals_home=5, goals_away=0)
    result = run([bad] + home_block([1] * 8, 8110, first_day=2) + away_block([1] * 8, 8130))
    assert dict(result.home.excluded_by_reason) == {reason: 1} and result.lambda_home == 1.0  # nunca goals_home como sustituto


def test_identity_is_local_integer_equality_only():
    # Verificable localmente: igualdad de ids enteros estables (no bool, no texto).
    with pytest.raises(EvidenceContractError, match="id entero"):
        run([dict(home_block([1], 8201)[0], home_team_id=str(HOME_TEAM))] + home_block([1] * 8, 8210) + away_block([1] * 8, 8230))
    # No verificable: que dos ids locales distintos sean o no el mismo partido real (PORTABLE_IDENTITY_UNPROVEN).
    kickoff = T - timedelta(days=1)
    twin_a = fixture(8301, HOME_TEAM, 5951, kickoff, 2, 0, observed=kickoff + timedelta(hours=2))
    twin_b = fixture(8302, HOME_TEAM, 5951, kickoff, 2, 0, observed=kickoff + timedelta(hours=2))
    result = run([twin_a, twin_b] + home_block([1] * 6, 8310, first_day=2) + away_block([1] * 8, 8330))
    assert {8301, 8302} <= set(result.home.selected_fixture_ids)  # el consumidor no puede fusionarlos sin identidad portable
    assert "TEAM_IDENTITY_CONTINUITY_LIMITED_TO_STABLE_IDS" in result.evidence_limitations
