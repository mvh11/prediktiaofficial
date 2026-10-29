"""MOD-DEV-108A: demostración sintética tasas de gol -> Poisson -> BTTS/Over-Under -> valor de cuota. Sin BD, sin red, sin app.
    python -m pytest research/tests/test_prematch_value_smoke.py -q   (desde backend/)
Partidos, cuotas, recepciones y mapeos SINTÉTICOS: ni histórico real, ni cotizaciones auténticas, ni probabilidades
calibradas. Solo se comprueba la conectividad de los módulos publicados."""

import copy
import dataclasses
import inspect
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

import research.prematch_value_smoke as smoke
from research import quote_value_bridge
from research.m58 import goal_projection, goal_reference
from research.market_value_math import value as market_value
from research.quote_temporal_eligibility import (BTTS, FIXTURE_MISMATCH, MAPPING_METHOD_NOT_APPROVED, ONE_X_TWO, OVER_UNDER,
                                                 QUOTE_NOT_KNOWN_AT_ISSUE, QUOTE_STALE, EvaluationContext, MappingProvenance,
                                                 QuoteObservation, QuoteTarget)
from research.quote_value_bridge import (MULTIPLE_ELIGIBLE_QUOTES, NO_ELIGIBLE_QUOTE, NO_VALUE, UNSUPPORTED, UNSUPPORTED_LINE,
                                         UNSUPPORTED_QUARTER_LINE, VALUE_EVALUATED, SelectionProbabilities)
from research.venue_goal_rates import baseline
from research.venue_goal_rates.baseline import ESTIMATE, NO_ESTIMATE, RESULT_OBSERVED_AFTER_CUTOFF, EvidenceContractError
from research.prematch_value_smoke import (DESIGN_LIMITS, LIMITATIONS, ONE_X_TWO_PROJECTION_NOT_AUTHORIZED, PrematchSmokeInputError,
                                           PrematchValueSmoke, evaluate_prematch_value_smoke)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = timezone.utc
T = datetime(2026, 3, 1, 18, 0, tzinfo=UTC)  # corte
KICKOFF = T + timedelta(hours=2)
HOME_TEAM, AWAY_TEAM, COMPETITION, SEASON, FIXTURE = 11, 22, 7, 70, 1000
TARGET = {"fixture_id": FIXTURE, "competition_id": COMPETITION, "season_id": SEASON, "home_team_id": HOME_TEAM, "away_team_id": AWAY_TEAM,
          "kickoff_at": KICKOFF}


def match(fixture_id, home, away, kickoff, fulltime_home, fulltime_away, observed=None):
    return {"fixture_id": fixture_id, "competition_id": COMPETITION, "season_id": SEASON, "home_team_id": home, "away_team_id": away,
            "kickoff_at": kickoff, "status_short": "FT", "fulltime_home": fulltime_home, "fulltime_away": fulltime_away,
            "result_observed_at": observed or kickoff + timedelta(hours=2)}


def home_games(goals, first_id=1):
    """Partidos EN CASA del local objetivo; goals[0] es el más reciente."""
    return [match(first_id + i, HOME_TEAM, 500 + i, T - timedelta(days=1 + i), g, 1) for i, g in enumerate(goals)]


def away_games(goals, first_id=101):
    """Partidos FUERA del visitante objetivo; goals[0] es el más reciente."""
    return [match(first_id + i, 600 + i, AWAY_TEAM, T - timedelta(days=1 + i, hours=3), 1, g) for i, g in enumerate(goals)]


HISTORY = home_games([2, 1, 0, 3] * 5) + away_games([0, 1, 1, 2, 0, 3, 1, 0, 2, 0])  # lambda_home = 1.5, lambda_away = 1.0
MAPPING = MappingProvenance(mapping_ref="map-syn-1", method="manual", provider="prov-syn", provider_event_id="ev-1", fixture_id=FIXTURE)


def qtarget(market=BTTS, selection="YES", line=None, **changes) -> QuoteTarget:
    base = QuoteTarget(provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1", fixture_id=FIXTURE, market=market,
                       selection=selection, line=line)
    return dataclasses.replace(base, **changes)


def context(tgt=None, **changes) -> EvaluationContext:
    base = EvaluationContext(prediction_id="pred-syn-108a", issued_at=T + timedelta(minutes=30), kickoff_at=KICKOFF,
                             max_quote_age=timedelta(hours=3), target=tgt or qtarget())
    return dataclasses.replace(base, **changes)


def quote(ctx=None, **changes) -> QuoteObservation:
    t = (ctx or context()).target
    base = QuoteObservation(evidence_id="evd-a", provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1", mapping=MAPPING,
                            market=t.market, selection=t.selection, line=t.line, settlement_convention="FT90_V1",
                            decimal_odds=Decimal("2.10"), quote_status="OPEN", provider_quoted_at=T - timedelta(minutes=40),
                            provider_updated_at=T - timedelta(minutes=40), received_at=T - timedelta(minutes=30))
    return dataclasses.replace(base, **changes)


def run(ctx=None, quotes=None, history=HISTORY, target=TARGET, cutoff=T, memberships=()):
    ctx = ctx or context()
    return evaluate_prematch_value_smoke(target=target, history=history, cutoff=cutoff, memberships=memberships, context=ctx,
                                         quotes=[quote(ctx)] if quotes is None else quotes)


OU25_UNDER = context(qtarget(OVER_UNDER, "UNDER", "2.5"))
OU20_OVER = context(qtarget(OVER_UNDER, "OVER", "2.0"))
OU20_UNDER = context(qtarget(OVER_UNDER, "UNDER", "2.0"))


def published_projection():
    """La cadena publicada ejecutada directamente, para comparar (no es una reimplementación)."""
    rates = baseline.estimate_venue_goal_rates(TARGET, HISTORY, T, ())
    return goal_projection.goals_market_probabilities(goal_reference.independent_poisson_reference(rates.lambda_home, rates.lambda_away))


@pytest.fixture
def calls(monkeypatch):
    """Espías sobre las funciones REALES de cada componente: registran las llamadas y devuelven su resultado."""
    log = {name: [] for name in ("estimate", "reference", "projection", "bridge", "binary", "push")}

    def spy(module, attr, name):
        real = getattr(module, attr)

        def wrapper(*args, **kwargs):
            out = real(*args, **kwargs)
            log[name].append((args, kwargs, out))
            return out

        monkeypatch.setattr(module, attr, wrapper)

    spy(baseline, "estimate_venue_goal_rates", "estimate")
    spy(goal_reference, "independent_poisson_reference", "reference")
    spy(goal_projection, "goals_market_probabilities", "projection")
    spy(quote_value_bridge, "evaluate_quote_value", "bridge")
    spy(market_value, "evaluate_binary_value", "binary")
    spy(market_value, "evaluate_push_value", "push")
    return log


def assert_chain(result, calls):
    """Cada componente se llamó una vez y su salida llega intacta al resultado."""
    assert [len(calls[n]) for n in ("estimate", "reference", "projection", "bridge")] == [1, 1, 1, 1]
    (_, _, rates), = calls["estimate"]
    (ref_args, _, cells), = calls["reference"]
    (proj_args, _, _), = calls["projection"]
    (bridge_args, _, quote_value), = calls["bridge"]
    assert result.venue_rates is rates and rates.status == ESTIMATE
    assert ref_args == (rates.lambda_home, rates.lambda_away) and proj_args == (cells,)
    assert result.reference_cells == tuple(cells) and len(cells) == 64
    assert result.quote_value is quote_value and bridge_args[2] is result.selection_probabilities


# --- 1-3: BTTS, Over/Under de media línea y de línea entera, delegados al motor existente ----------

def test_01_btts_with_one_eligible_quote_flows_through_every_published_component(calls):
    ctx = context()
    result = run(ctx)
    assert_chain(result, calls)
    assert (result.status, result.reasons) == (VALUE_EVALUATED, ())
    assert (result.venue_rates.lambda_home, result.venue_rates.lambda_away) == (1.5, 1.0)
    p = result.selection_probabilities
    assert p == SelectionProbabilities(prediction_id="pred-syn-108a", fixture_id=FIXTURE, market=BTTS, selection="YES", line=None,
                                       p_win=published_projection()["btts"]["YES"], p_lose=None, p_push=None)
    (_, kw, out), = calls["binary"]
    assert kw == {"p_win": p.p_win, "decimal_odds": 2.1} and calls["push"] == []
    assert result.quote_value.valuation is out and result.quote_value.status == VALUE_EVALUATED
    assert result.quote_value.qualified.fixture_id == FIXTURE and result.quote_value.eligible_count == 1


def test_02_over_under_half_line_is_binary_without_push_mass(calls):
    result = run(OU25_UNDER)
    assert_chain(result, calls)
    assert (result.status, result.reasons) == (VALUE_EVALUATED, ())
    p = result.selection_probabilities
    assert (p.line, p.selection, p.p_lose, p.p_push) == ("2.5", "UNDER", None, None)
    assert p.p_win == published_projection()["over_under"]["2.5"]["UNDER"]
    assert result.market_probabilities["over_under"]["2.5"]["PUSH"] == 0.0  # sin PUSH fabricado
    assert calls["push"] == [] and len(calls["binary"]) == 1
    assert result.quote_value.valuation.settlement == market_value.BINARY_WIN_LOSE and result.quote_value.valuation.p_push is None


def test_03_over_under_integer_line_passes_explicit_push_mass_to_the_push_engine(calls):
    result = run(OU20_OVER)
    assert_chain(result, calls)
    assert (result.status, result.reasons) == (VALUE_EVALUATED, ())
    line = published_projection()["over_under"]["2.0"]
    p = result.selection_probabilities
    assert (p.p_win, p.p_lose, p.p_push) == (line["OVER"], line["UNDER"], line["PUSH"])
    assert p.p_push == pytest.approx(0.25651562069968376, rel=1e-12)  # P(total = 2), total ~ Poisson(2.5): e^-2.5 * 2.5^2 / 2
    (_, kw, out), = calls["push"]
    assert kw == {"p_win": p.p_win, "p_lose": p.p_lose, "p_push": p.p_push, "decimal_odds": 2.1} and calls["binary"] == []
    assert result.quote_value.valuation is out
    assert out == market_value.evaluate_push_value(p_win=p.p_win, p_lose=p.p_lose, p_push=p.p_push, decimal_odds=2.1)
    assert (out.settlement, out.p_push) == (market_value.WIN_LOSE_PUSH, line["PUSH"])


# --- 4-5: NO_ESTIMATE y exclusión por observación tardía (comportamiento publicado, no fixed-N) ----

def test_04_fewer_than_eight_admissible_results_is_no_estimate_without_value(calls):
    result = run(history=home_games([2] * 7) + away_games([1] * 10))
    assert (result.status, result.reasons) == (NO_ESTIMATE, ("insufficient_home_sample",))
    assert result.venue_rates is calls["estimate"][0][2]
    assert (result.venue_rates.lambda_home, result.venue_rates.lambda_away) == (None, None)  # nunca 0 ni relleno
    assert (result.reference_cells, result.market_probabilities, result.selection_probabilities, result.quote_value) == (None,) * 4
    assert [len(calls[n]) for n in ("estimate", "reference", "projection", "bridge", "binary", "push")] == [1, 0, 0, 0, 0, 0]


def test_05_result_observed_after_cutoff_is_excluded_by_the_published_baseline_filter(calls):
    late = T + timedelta(minutes=1)
    # Ocho partidos en casa, uno observado después del corte: quedan siete -> NO_ESTIMATE
    history = home_games([2] * 8) + away_games([1] * 10)
    history[0] = {**history[0], "result_observed_at": late}
    result = run(history=history)
    assert (result.status, result.reasons, result.quote_value) == (NO_ESTIMATE, ("insufficient_home_sample",), None)
    assert dict(result.venue_rates.home.excluded_by_reason) == {RESULT_OBSERVED_AFTER_CUTOFF: 1}
    # Veintiuno en casa con el más reciente tardío: el baseline publicado filtra ANTES de elegir los 20, así que el 21.º
    # entra en la muestra. Es el comportamiento publicado (DESIGN_LIMITS), no la población fija DI N3/G2.
    history = home_games([2, 1, 0, 3] * 5 + [4]) + away_games([1] * 10)
    history[0] = {**history[0], "result_observed_at": late}
    result = run(history=history)
    assert result.status == VALUE_EVALUATED and result.venue_rates.status == ESTIMATE
    assert 1 not in result.venue_rates.home.selected_fixture_ids and 21 in result.venue_rates.home.selected_fixture_ids
    assert result.venue_rates.lambda_home == sum(([1, 0, 3] + [2, 1, 0, 3] * 4 + [4])) / 20
    assert DESIGN_LIMITS == ("PUBLISHED_BASELINE_RESULT_VALID_PRE_FILTERING_NOT_DI_N3_FIXED_COHORT",)
    assert result.design_limits == DESIGN_LIMITS and "G2_N3_ACCEPTANCE_WAIT_CONTRACT" in result.limitations


# --- 6-7: identidad y cronología -> error tipado, sin llamar a ningún componente -----------------

@pytest.mark.parametrize("case", [
    {"ctx": context(qtarget(fixture_id=1001))},
    {"target": {**TARGET, "fixture_id": True}, "ctx": context(qtarget(fixture_id=1))},
    {"target": {**TARGET, "fixture_id": 1}, "ctx": context(qtarget(fixture_id=True))},
    {"target": {**TARGET, "fixture_id": 1000.0}},
    {"target": {k: v for k, v in TARGET.items() if k != "fixture_id"}},
    {"target": [("fixture_id", FIXTURE)]},
    {"ctx": "not-a-context"},
], ids=["different", "bool-target", "bool-context", "float", "missing", "not-mapping", "context-type"])
def test_06_fixture_identity_mismatch_or_malformed_identity_is_a_typed_error(calls, case):
    with pytest.raises(PrematchSmokeInputError):
        evaluate_prematch_value_smoke(target=case.get("target", TARGET), history=HISTORY, cutoff=T, memberships=(),
                                      context=case.get("ctx", context()), quotes=[])
    assert all(log == [] for log in calls.values())


@pytest.mark.parametrize("case", [
    {"ctx": context(kickoff_at=KICKOFF + timedelta(minutes=1))},
    {"ctx": context(issued_at=T - timedelta(seconds=1))},
    {"ctx": context(issued_at=KICKOFF)},
    {"ctx": context(issued_at=KICKOFF + timedelta(hours=1))},
    {"cutoff": KICKOFF},
    {"cutoff": T.replace(tzinfo=None)},
    {"ctx": context(issued_at=(T + timedelta(minutes=30)).replace(tzinfo=None))},
    {"ctx": context(kickoff_at=KICKOFF.replace(tzinfo=None))},
    {"target": {**TARGET, "kickoff_at": KICKOFF.replace(tzinfo=None)}},
    {"target": {**TARGET, "kickoff_at": "2026-03-01T20:00:00Z"}},
    {"cutoff": None},
], ids=["kickoff-differs", "issued-before-cutoff", "issued-at-kickoff", "issued-after-kickoff", "cutoff-after-issued",
        "naive-cutoff", "naive-issued", "naive-context-kickoff", "naive-target-kickoff", "str-kickoff", "no-cutoff"])
def test_07_invalid_temporal_identity_or_sequence_is_a_typed_error(calls, case):
    with pytest.raises(PrematchSmokeInputError):
        run(case.get("ctx"), target=case.get("target", TARGET), cutoff=case.get("cutoff", T))
    assert all(log == [] for log in calls.values())


def test_07b_same_instant_in_another_zone_and_cutoff_equal_to_issued_are_accepted():
    plus2 = timezone(timedelta(hours=2))
    ctx = context(issued_at=T.astimezone(plus2), kickoff_at=KICKOFF.astimezone(plus2))
    assert run(ctx).status == VALUE_EVALUATED


def test_07c_errors_of_the_published_components_are_propagated_typed():
    with pytest.raises(EvidenceContractError):  # el estimador valida su evidencia
        run(history=HISTORY + [{**HISTORY[0], "fixture_id": 999, "home_team_id": True}])
    with pytest.raises(PrematchSmokeInputError):
        run(quotes=({"not": "a quote"},))
    with pytest.raises(PrematchSmokeInputError):
        run(context(qtarget(selection="OVER")))  # selección de otro mercado
    with pytest.raises(PrematchSmokeInputError):
        run(context(qtarget(line="2.5")))  # BTTS con línea


# --- 8-10: estados negativos del puente, conservados ---------------------------------------------

def test_08_no_eligible_quote_preserves_no_value_and_its_reason(calls):
    result = run(quotes=[])
    assert (result.status, result.reasons) == (NO_VALUE, (NO_ELIGIBLE_QUOTE,))
    assert result.quote_value is calls["bridge"][0][2] and result.quote_value.reasons == (NO_ELIGIBLE_QUOTE,)
    assert (result.quote_value.valuation, result.quote_value.eligible_count) == (None, 0)
    assert result.selection_probabilities is not None and calls["binary"] == calls["push"] == []
    old = T - timedelta(hours=4)
    stale = run(quotes=[quote(received_at=old, provider_quoted_at=old, provider_updated_at=old)])
    assert (stale.status, stale.reasons, stale.quote_value.eligibility_reasons) == (NO_VALUE, (NO_ELIGIBLE_QUOTE,), (QUOTE_STALE,))


def test_09_two_eligible_quotes_keep_the_bridge_rejection(calls):
    quotes = [quote(), quote(evidence_id="evd-b", received_at=T - timedelta(minutes=20))]
    result = run(quotes=quotes)
    assert (result.status, result.reasons) == (NO_VALUE, (MULTIPLE_ELIGIBLE_QUOTES,))
    assert (result.quote_value.eligible_count, result.quote_value.qualified, result.quote_value.valuation) == (2, None, None)
    assert calls["binary"] == calls["push"] == []
    assert run(quotes=quotes[::-1]) == result  # sin elegir por orden


@pytest.mark.parametrize("changes, reason", [
    ({"received_at": T + timedelta(minutes=31)}, QUOTE_NOT_KNOWN_AT_ISSUE),
    ({"mapping": dataclasses.replace(MAPPING, fixture_id=FIXTURE + 1)}, FIXTURE_MISMATCH),
    ({"mapping": dataclasses.replace(MAPPING, method="auto_fuzzy")}, MAPPING_METHOD_NOT_APPROVED),
], ids=["late", "wrong-fixture-mapping", "fuzzy-mapping"])
def test_10_late_quote_or_wrong_mapping_is_no_value(changes, reason):
    result = run(quotes=[quote(**changes)])
    assert (result.status, result.reasons, result.quote_value.eligibility_reasons) == (NO_VALUE, (NO_ELIGIBLE_QUOTE,), (reason,))
    assert result.quote_value.valuation is None


# --- 11: 1X2 y líneas de cuarto -> UNSUPPORTED sin probabilidades inventadas -------------------

def test_11_one_x_two_is_unsupported_without_any_probability(calls):
    ctx = context(qtarget(ONE_X_TWO, "HOME"))
    result = run(ctx)
    assert (result.status, result.reasons) == (UNSUPPORTED, (ONE_X_TWO_PROJECTION_NOT_AUTHORIZED,))
    assert (result.venue_rates, result.reference_cells, result.market_probabilities, result.selection_probabilities,
            result.quote_value) == (None,) * 5
    assert all(log == [] for log in calls.values())


@pytest.mark.parametrize("line, reason", [("2.25", UNSUPPORTED_QUARTER_LINE), ("2.75", UNSUPPORTED_QUARTER_LINE), ("7.5", UNSUPPORTED_LINE),
                                          ("2.50", UNSUPPORTED_LINE), (None, UNSUPPORTED_LINE)])
def test_11b_quarter_or_non_canonical_lines_are_unsupported_by_the_bridge(calls, line, reason):
    ctx = context(qtarget(OVER_UNDER, "OVER", line))
    result = run(ctx)
    assert (result.status, result.reasons) == (UNSUPPORTED, (reason,))
    (args, _, quote_value), = calls["bridge"]
    assert args == (ctx, [quote(ctx)], None) and result.quote_value is quote_value  # sin probabilidades para el puente
    assert (quote_value.status, quote_value.reasons, quote_value.valuation, quote_value.quotes_received) == (UNSUPPORTED, (reason,), None, 0)
    assert (result.venue_rates, result.selection_probabilities, result.market_probabilities) == (None, None, None)
    assert [len(calls[n]) for n in ("estimate", "reference", "projection", "binary", "push")] == [0] * 5


# --- 12: masas, identidades y valores del motor original -----------------------------------------

@pytest.mark.parametrize("ctx, win, lose", [(OU20_OVER, "OVER", "UNDER"), (OU20_UNDER, "UNDER", "OVER")], ids=["over", "under"])
def test_12_integer_line_masses_identities_and_engine_values_are_preserved(ctx, win, lose):
    result = run(ctx, quotes=[quote(ctx, decimal_odds=Decimal("1.95"))])
    line = result.market_probabilities["over_under"]["2.0"]
    assert dict(line) == published_projection()["over_under"]["2.0"]
    p = result.selection_probabilities
    assert (p.prediction_id, p.fixture_id, p.market, p.selection, p.line) == (ctx.prediction_id, FIXTURE, OVER_UNDER, win, "2.0")
    assert (p.p_win, p.p_lose, p.p_push) == (line[win], line[lose], line["PUSH"]) and line["PUSH"] > 0
    assert abs(sum((p.p_win, p.p_lose, p.p_push)) - 1) <= market_value.SUM_TOLERANCE  # masa de la proyección, sin renormalizar
    v = result.quote_value.valuation
    assert (v.p_win, v.p_lose, v.p_push, v.decimal_odds) == (p.p_win, p.p_lose, p.p_push, 1.95)
    assert v == market_value.evaluate_push_value(p_win=p.p_win, p_lose=p.p_lose, p_push=p.p_push, decimal_odds=1.95)
    assert (result.prediction_id, result.fixture_id, result.market, result.selection, result.line) == (
        ctx.prediction_id, FIXTURE, OVER_UNDER, win, "2.0")
    assert result.quote_value.target == ctx.target and result.quote_value.prediction_id == ctx.prediction_id
    assert result.market_probabilities["mass"] == published_projection()["mass"] <= 1.0


def test_12b_binary_selection_masses_come_from_the_projection_and_the_engine_computes_the_complement():
    for ctx, key in ((context(qtarget(selection="NO")), ("btts", "NO")), (OU25_UNDER, ("over_under", "2.5", "UNDER"))):
        result = run(ctx)
        expected = published_projection()
        for k in key:
            expected = expected[k]
        assert result.selection_probabilities.p_win == expected
        v = result.quote_value.valuation
        assert (v.p_win, v.p_lose, v.p_push) == (expected, 1.0 - expected, None)
        assert v == market_value.evaluate_binary_value(p_win=expected, decimal_odds=2.1)


# --- 13-15: inmutabilidad, determinismo, ausencia de afirmaciones y de efectos externos ---------

def test_13_inputs_are_not_mutated_results_are_immutable_and_deterministic():
    ctx, quotes, history = OU20_OVER, [quote(OU20_OVER)], copy.deepcopy(HISTORY)
    before = copy.deepcopy((TARGET, history, quotes, ctx))
    result = run(ctx, quotes, history)
    assert (TARGET, history, quotes, ctx) == before
    assert all(run(ctx, list(quotes), copy.deepcopy(history)) == result for _ in range(3))
    assert isinstance(result, PrematchValueSmoke)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = NO_VALUE
    with pytest.raises(TypeError):
        result.market_probabilities["mass"] = 1.0
    with pytest.raises(TypeError):
        result.market_probabilities["over_under"]["2.0"]["PUSH"] = 0.0
    assert isinstance(result.reference_cells, tuple) and all(isinstance(c, tuple) for c in result.reference_cells)


def test_14_no_calibration_authenticity_certification_or_recommendation_claims():
    result = run()
    texts = (result.input_basis, result.probability_basis, result.pit_status) + result.limitations + result.design_limits
    for text in texts:
        for claim in ("CALIBRATED", "CERTIFIED", "AUTHENTICATED", "ACCREDITED", "VERIFIED"):
            if claim in text:
                assert "NOT_" + claim in text or "NOT_INDEPENDENTLY_" + claim in text, text
    for required in ("RESEARCH_ONLY", "POISSON_REFERENCE_NOT_CALIBRATED", "CALLER_SUPPLIED_HISTORY_CONTEXT_AND_QUOTES", "NOT_CERTIFIED",
                     "NOT_A_REAL_BETTING_OPPORTUNITY"):
        assert required in LIMITATIONS
    assert result.pit_status == result.quote_value.pit_status == result.quote_value.valuation.pit_status == "NOT_CERTIFIED"
    names = {f.name for f in dataclasses.fields(PrematchValueSmoke)}
    banned = ("stake", "kelly", "bankroll", "recommend", "recommendation", "pick", "edge", "bet", "profit", "calibrated", "certified")
    assert not [n for n in names if any(b == part for part in n.split("_") for b in banned)]


def test_15_no_clock_environment_disk_or_network_access(monkeypatch):
    source = inspect.getsource(smoke)
    for forbidden in ("now(", "utcnow", "time.time", "os.environ", "getenv", "open(", "socket", "requests", "httpx", "sqlalchemy", "psycopg"):
        assert forbidden not in source, forbidden

    def blocked(*args, **kwargs):
        raise AssertionError("acceso de red durante la evaluación")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    assert run(OU20_OVER, [quote(OU20_OVER)]).status == VALUE_EVALUATED


def test_15b_module_imports_without_app_database_provider_or_numeric_stack():
    code = ("import sys; import research.prematch_value_smoke; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'psycopg2', 'numpy', 'sklearn',"
            " 'requests', 'httpx', 'urllib3', 'dotenv')))")
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ADMIN_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE", "PYTHONPATH")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# --- R1 (MOD-DEV108A-R1, D-108A-1): diagnósticos seguros, acotados y tipados -----------------------
# Variantes H de la auditoría de SECONDARY (H1-H6) más su observación O5 (desplazamiento de 30 h). Solo API pública,
# así que este archivo también se ejecuta contra la versión original como control negativo.

TOUCHED = []  # métodos del llamador que llegó a ejecutar un diagnóstico (tiene que quedar vacío)
MESSAGE_MAX = 400


class Halt(BaseException):
    """No es una Exception: nunca se convierte en PrematchSmokeInputError."""


def _boom(name):
    def method(self, *args, **kwargs):
        TOUCHED.append(name)
        raise RuntimeError(f"hostile {name}")
    return method


_TEXT = {n: _boom(n) for n in ("__repr__", "__str__", "__format__")}
_DT_METHODS = {n: _boom(n) for n in ("utcoffset", "astimezone", "isoformat", "__sub__")}
HostileRepr = type("HostileRepr", (), dict(_TEXT))
HostileInt = type("HostileInt", (int,), dict(_TEXT))
HostileStr = type("HostileStr", (str,), {**_TEXT, **{n: _boom(n) for n in ("__len__", "__getitem__")}})
HostileNaive = type("HostileNaive", (datetime,), {**_TEXT, **_DT_METHODS})
HostileAware = type("HostileAware", (datetime,), {**_TEXT, **_DT_METHODS})


class BigRepr:
    def __repr__(self):
        TOUCHED.append("__repr__")
        return "x" * 5_000_000


class FixedOffset(tzinfo):
    """tzinfo del llamador: devuelve `offset` o, si es una clase de excepción, la lanza."""

    def __init__(self, offset):
        self.offset = offset

    def utcoffset(self, dt):
        if isinstance(self.offset, type) and issubclass(self.offset, BaseException):
            raise self.offset("tzinfo del llamador")
        return self.offset


@pytest.fixture
def touched():
    TOUCHED.clear()
    yield TOUCHED
    TOUCHED.clear()


def assert_typed_and_safe(fn, calls, touched):
    with pytest.raises(PrematchSmokeInputError) as info:
        fn()
    message = info.value.args[0]
    assert type(message) is str and len(message) <= MESSAGE_MAX, len(message)
    assert touched == [] and all(log == [] for log in calls.values())
    return message


H_CASES = {
    "H1-fixture-object-hostile-repr": lambda: run(target={**TARGET, "fixture_id": HostileRepr()}, quotes=[]),
    "H2-fixture-int-subclass-hostile-repr": lambda: run(target={**TARGET, "fixture_id": HostileInt(FIXTURE)}, quotes=[]),
    "H3-fixture-huge-int-mismatch": lambda: run(target={**TARGET, "fixture_id": 10 ** 5000}, quotes=[]),
    "H4-cutoff-object-hostile-repr": lambda: run(cutoff=HostileRepr(), quotes=[]),
    "H5-cutoff-naive-datetime-subclass-hostile": lambda: run(cutoff=HostileNaive(2026, 3, 1, 18), quotes=[]),
    "H6-btts-selection-str-subclass-hostile-repr": lambda: run(context(qtarget(selection=HostileStr("MAYBE"))), quotes=[]),
    "O5-cutoff-utcoffset-30h": lambda: run(cutoff=T.replace(tzinfo=FixedOffset(timedelta(hours=30))), quotes=[]),
}


@pytest.mark.parametrize("case", list(H_CASES), ids=list(H_CASES))
def test_r1_audit_variants_raise_typed_bounded_errors_without_running_caller_code(calls, touched, case):
    assert_typed_and_safe(H_CASES[case], calls, touched)


@pytest.mark.parametrize("build", [
    lambda: {"target": {**TARGET, "fixture_id": HostileInt(1)}, "ctx": context(qtarget(fixture_id=HostileInt(1)))},
    lambda: {"target": {**TARGET, "fixture_id": HostileStr("1000")}},
    lambda: {"ctx": context(issued_at=HostileNaive(2026, 3, 1, 18, 30))},
    lambda: {"ctx": context(kickoff_at=HostileNaive(2026, 3, 1, 20))},
    lambda: {"target": {**TARGET, "kickoff_at": HostileNaive(2026, 3, 1, 20)}},
    # aware con utcoffset/astimezone/__sub__ hostiles: se lee con los métodos base y la cronología inválida se tipa
    lambda: {"ctx": context(issued_at=HostileAware(2026, 3, 1, 17, tzinfo=UTC))},
    lambda: {"ctx": context(qtarget(market=HostileStr("BTTS_FT90"), selection="MAYBE"))},
    lambda: {"ctx": context(qtarget(OVER_UNDER, HostileStr("HOME"), "2.5"))},
], ids=["int-both-sides", "str-fixture", "issued-naive", "ctx-kickoff-naive", "target-kickoff-naive", "aware-hostile-methods",
        "market-hostile-format", "ou-selection-hostile"])
def test_r1_hostile_int_str_and_datetime_subclasses_are_typed(calls, touched, build):
    case = build()
    assert_typed_and_safe(lambda: run(case.get("ctx"), quotes=[], target=case.get("target", TARGET)), calls, touched)


def test_r1_class_name_that_is_a_hostile_str_subclass_is_never_executed(calls, touched):
    evil = type("Evil", (), {})
    evil.__name__ = HostileStr("EvilName")
    weird = type(HostileStr("WeirdName"), (), {})
    for value, name in ((evil(), "EvilName"), (weird(), "WeirdName")):
        message = assert_typed_and_safe(lambda: run(cutoff=value, quotes=[]), calls, touched)
        assert message == f"cutoff: se espera un datetime con zona horaria, no <{name}>"


@pytest.mark.parametrize("build", [
    lambda: {"target": {**TARGET, "fixture_id": BigRepr()}},
    lambda: {"cutoff": BigRepr()},
    lambda: {"ctx": context(qtarget(selection="Z" * 5_000_000))},
    lambda: {"ctx": context(qtarget(selection=HostileStr("Z" * 5_000_000)))},
    lambda: {"target": {**TARGET, "kickoff_at": "9" * 5_000_000}},
], ids=["fixture-big-repr", "cutoff-big-repr", "selection-5MB", "selection-5MB-hostile", "kickoff-5MB-str"])
def test_r1_multi_megabyte_representations_give_bounded_messages(calls, touched, build):
    case = build()
    message = assert_typed_and_safe(lambda: run(case.get("ctx"), quotes=[], target=case.get("target", TARGET), cutoff=case.get("cutoff", T)),
                                    calls, touched)
    assert "(5000000 caracteres)" in message or "<BigRepr>" in message


def test_r1_huge_ints_compare_exactly_and_are_described_by_sign_and_bits(calls, touched):
    huge = 10 ** 5000
    message = assert_typed_and_safe(lambda: run(context(qtarget(fixture_id=huge + 1)), quotes=[], target={**TARGET, "fixture_id": huge}),
                                    calls, touched)
    assert message == "fixture_id distinto entre target (<int de 16610 bits>) y context (<int de 16610 bits>)"
    message = assert_typed_and_safe(lambda: run(target={**TARGET, "fixture_id": -huge}, quotes=[]), calls, touched)
    assert message == "fixture_id distinto entre target (<int negativo de 16610 bits>) y context (1000)"
    # el mismo int enorme en los dos lados no es un error de identidad: comparación exacta, sin texto ni float
    result = run(context(qtarget(fixture_id=huge)), quotes=[], target={**TARGET, "fixture_id": huge}, history=[])
    assert result.status == NO_ESTIMATE and result.fixture_id == huge


@pytest.mark.parametrize("offset", [timedelta(hours=30), timedelta(hours=-30), "not-a-timedelta", RuntimeError, ArithmeticError],
                         ids=["+30h", "-30h", "not-timedelta", "raises-RuntimeError", "raises-ArithmeticError"])
@pytest.mark.parametrize("where", ["cutoff", "issued_at", "ctx_kickoff", "target_kickoff"])
def test_r1_invalid_tzinfo_offsets_are_typed(calls, touched, offset, where):
    base = {"cutoff": T, "issued_at": T + timedelta(minutes=30), "ctx_kickoff": KICKOFF, "target_kickoff": KICKOFF}[where]
    bad = base.replace(tzinfo=FixedOffset(offset))
    ctx = context(issued_at=bad) if where == "issued_at" else context(kickoff_at=bad) if where == "ctx_kickoff" else None
    target = {**TARGET, "kickoff_at": bad} if where == "target_kickoff" else TARGET
    message = assert_typed_and_safe(lambda: run(ctx, quotes=[], target=target, cutoff=bad if where == "cutoff" else T), calls, touched)
    assert "zona horaria o instante no válidos" in message


@pytest.mark.parametrize("where", ["cutoff", "issued_at", "target_kickoff"])
def test_r1_base_exceptions_are_never_converted(calls, where):
    base = {"cutoff": T, "issued_at": T + timedelta(minutes=30), "target_kickoff": KICKOFF}[where]
    bad = base.replace(tzinfo=FixedOffset(Halt))
    with pytest.raises(Halt):
        run(context(issued_at=bad) if where == "issued_at" else None, quotes=[],
            target={**TARGET, "kickoff_at": bad} if where == "target_kickoff" else TARGET, cutoff=bad if where == "cutoff" else T)
    assert all(log == [] for log in calls.values())


def test_r1_ordinary_messages_and_valid_inputs_are_preserved(touched):
    with pytest.raises(PrematchSmokeInputError, match=r"^fixture_id distinto entre target \(1000\) y context \(1001\)$"):
        run(context(qtarget(fixture_id=1001)), quotes=[])
    with pytest.raises(PrematchSmokeInputError, match=r"^fixture_id: se espera un int \(no bool\): True / 1$"):
        run(context(qtarget(fixture_id=1)), quotes=[], target={**TARGET, "fixture_id": True})
    with pytest.raises(PrematchSmokeInputError, match=r"^selección 'OVER' no válida para BTTS_FT90$"):
        run(context(qtarget(selection="OVER")), quotes=[])
    with pytest.raises(PrematchSmokeInputError, match=r"^target\.kickoff_at: se espera un datetime con zona horaria, no '2026-03-01T20:00:00Z'$"):
        run(quotes=[], target={**TARGET, "kickoff_at": "2026-03-01T20:00:00Z"})
    with pytest.raises(PrematchSmokeInputError, match=r"^se exige cutoff <= issued_at < kickoff_at$"):
        run(context(issued_at=KICKOFF), quotes=[])
    # datetime subclases válidas (solo texto hostil) en otra zona: el recorrido válido y su resultado no cambian
    plus3 = timezone(timedelta(hours=3))
    aware = type("TextHostileAware", (datetime,), dict(_TEXT))
    ctx = context(issued_at=aware(2026, 3, 1, 21, 30, tzinfo=plus3), kickoff_at=aware(2026, 3, 1, 23, tzinfo=plus3))
    result = run(ctx, cutoff=aware(2026, 3, 1, 21, tzinfo=plus3), target={**TARGET, "kickoff_at": aware(2026, 3, 1, 20, tzinfo=UTC)})
    assert (result.status, result.reasons) == (VALUE_EVALUATED, ())
    assert result.selection_probabilities == run().selection_probabilities and touched == []


def test_r1_rejected_naive_datetime_is_described_by_its_base_fields():
    """Formato R1 del diagnóstico (el original usaba repr; no es un defecto): campos base, sin zona."""
    with pytest.raises(PrematchSmokeInputError, match=r"^cutoff: se espera un datetime con zona horaria, no datetime 2026-03-01T18:00:00\.000000$"):
        run(cutoff=T.replace(tzinfo=None), quotes=[])
    with pytest.raises(PrematchSmokeInputError, match=r"^cutoff: se espera un datetime con zona horaria, no <HostileNaive 2026-03-01T18:00:00\.000000>$"):
        run(cutoff=HostileNaive(2026, 3, 1, 18), quotes=[])


# --- R2 (MOD-DEV108A-R2, D-108A-R1-1): el diagnóstico del mercado no vuelve a comparar el objeto del llamador --
# Comparaciones funcionales (las de 95a766a, sin cambios): BTTS = 2 __eq__ (tupla + clave del dict) y 1 __hash__;
# Over/Under = 3 __eq__ (BTTS y OVER_UNDER en la tupla + clave) y 1 __hash__. R1 añadía una más en el mensaje.

R2_OPS = []  # operaciones personalizadas del mercado ejecutadas


def market_str(text, *, fail_on_eq=None, hostile_text=False, liar=False, base=str):
    """Mercado del llamador con __eq__ / __hash__ que se cuentan (y delegan en str salvo liar); opcionalmente lanza en
    la comparación número fail_on_eq o tiene __repr__ / __str__ / __format__ hostiles."""
    def __eq__(self, other):
        R2_OPS.append("__eq__")
        if fail_on_eq is not None and R2_OPS.count("__eq__") >= fail_on_eq:
            raise RuntimeError(f"hostile __eq__ en la comparación {R2_OPS.count('__eq__')}")
        return True if liar else str.__eq__(self, other)

    def __hash__(self):
        R2_OPS.append("__hash__")
        return str.__hash__(BTTS) if liar else str.__hash__(self)

    ns = {"__eq__": __eq__, "__hash__": __hash__}
    if hostile_text:
        for name in ("__repr__", "__str__", "__format__"):
            def text_method(self, *args, _name=name):
                R2_OPS.append(_name)
                raise RuntimeError(f"hostile {_name}")
            ns[name] = text_method
    cls = type("LiarMarket" if liar else "CallerMarket", (base,), ns)
    return cls(text) if base is str else cls()


@pytest.fixture
def ops():
    R2_OPS.clear()
    yield R2_OPS
    R2_OPS.clear()


R2_CASES = {
    # A: el caso exacto de SECONDARY (BTTS válido, selección MAYBE, __eq__ falla en la 3.ª comparación)
    "A-btts-eq-fails-on-3rd": (lambda: qtarget(market=market_str(BTTS, fail_on_eq=3), selection="MAYBE"),
                               "selección 'MAYBE' no válida para BTTS_FT90", 2),
    "A-ou-eq-fails-on-4th": (lambda: qtarget(market=market_str(OVER_UNDER, fail_on_eq=4), selection="HOME", line="2.5"),
                             "selección 'HOME' no válida para OVER_UNDER_FT90", 3),
    # E/H: texto hostil más comparación contada: ni texto ni comparaciones extra en el diagnóstico
    "E-btts-hostile-text": (lambda: qtarget(market=market_str(BTTS, hostile_text=True), selection="MAYBE"),
                            "selección 'MAYBE' no válida para BTTS_FT90", 2),
    "E-ou-hostile-text": (lambda: qtarget(market=market_str(OVER_UNDER, hostile_text=True), selection="HOME", line="2.5"),
                          "selección 'HOME' no válida para OVER_UNDER_FT90", 3),
    # E: un mercado que "iguala" a todo: la etiqueta sale de su contenido base, nunca de volver a preguntarle
    "E-liar-str": (lambda: qtarget(market=market_str("XYZ", liar=True), selection="MAYBE"),
                   "selección 'MAYBE' no válida para <LiarMarket 'XYZ'>", 2),
    "E-liar-object": (lambda: qtarget(market=market_str(None, liar=True, base=object), selection="MAYBE"),
                      "selección 'MAYBE' no válida para <LiarMarket>", 2),
}


@pytest.mark.parametrize("case", list(R2_CASES), ids=list(R2_CASES))
def test_r2_market_diagnostic_runs_only_the_functional_comparisons(calls, touched, ops, case):
    build, expected, functional_eq = R2_CASES[case]
    message = assert_typed_and_safe(lambda: run(context(build()), quotes=[]), calls, touched)  # F: ningún componente
    assert message == expected
    assert ops == ["__eq__"] * (functional_eq - 1) + ["__hash__", "__eq__"]  # H: tupla, hash y clave; nada más


@pytest.mark.parametrize("tgt, expected", [
    (qtarget(selection="MAYBE"), "selección 'MAYBE' no válida para BTTS_FT90"),
    (qtarget(selection="OVER"), "selección 'OVER' no válida para BTTS_FT90"),
    (qtarget(OVER_UNDER, "HOME", "2.5"), "selección 'HOME' no válida para OVER_UNDER_FT90"),
    (qtarget(OVER_UNDER, "YES", "2.0"), "selección 'YES' no válida para OVER_UNDER_FT90"),
], ids=["btts-maybe", "btts-over", "ou-home", "ou-yes"])
def test_r2_ordinary_btts_and_over_under_messages_are_unchanged(calls, tgt, expected):
    with pytest.raises(PrematchSmokeInputError) as info:
        run(context(tgt), quotes=[])
    assert info.value.args[0] == expected and all(log == [] for log in calls.values())


@pytest.mark.parametrize("tgt", [qtarget(), qtarget(OVER_UNDER, "OVER", "2.0")], ids=["btts", "ou-2.0-push"])
def test_r2_valid_btts_and_integer_over_under_results_are_unchanged(ops, tgt):
    plain = run(context(tgt))
    counted = run(context(dataclasses.replace(tgt, market=market_str(tgt.market))))
    assert (plain.status, counted.status) == (VALUE_EVALUATED, VALUE_EVALUATED)
    assert counted.selection_probabilities == plain.selection_probabilities
    assert counted.quote_value.valuation == plain.quote_value.valuation
    if tgt.line == "2.0":
        assert plain.selection_probabilities.p_push == plain.market_probabilities["over_under"]["2.0"]["PUSH"] > 0
