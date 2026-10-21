"""MOD-DEV-103A: puente sintético cuota elegible -> cuota justa y EV. Sin BD, sin red, sin app.
    python -m pytest research/tests/test_quote_value_bridge.py -q   (desde backend/)
Cuotas, recepciones, mapeos y probabilidades sintéticos: ninguno es una cotización auténtica, una
probabilidad calibrada ni un histórico PIT."""

import copy
import dataclasses
import itertools
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import research.quote_value_bridge as bridge
from research.market_value_math import value as market_value
from research.quote_temporal_eligibility import (BOOKMAKER_MISMATCH, BTTS, EVENT_MISMATCH, EVIDENCE_ID_INCONSISTENT, FIXTURE_MISMATCH,
                                                 IDENTITY_INVALID, LINE_MISMATCH, MAPPING_METHOD_NOT_APPROVED,
                                                 MAPPING_PROVENANCE_MISSING, MARKET_MISMATCH, ONE_X_TWO, OVER_UNDER, PRICE_INVALID,
                                                 PROVIDER_MISMATCH, QUOTE_NOT_KNOWN_AT_ISSUE, QUOTE_STALE, QUOTE_TEMPORAL_AMBIGUITY,
                                                 RECEIVED_AT_INVALID, SELECTION_MISMATCH, SETTLEMENT_CONVENTION_UNSUPPORTED,
                                                 EligibilityInputError, EvaluationContext, MappingProvenance, QualifiedQuote,
                                                 QuoteObservation, QuoteTarget)
from research.quote_value_bridge import (MULTIPLE_ELIGIBLE_QUOTES, NO_ELIGIBLE_QUOTE, NO_VALUE, NUMERIC_BASIS,
                                         ODDS_NOT_REPRESENTABLE_AS_DOUBLE, PROBABILITY_IDENTITY_MISMATCH, PROBABILITY_INVALID,
                                         PROBABILITY_MALFORMED, PROBABILITY_MISSING, PROBABILITY_SHAPE_INVALID, REASONS, UNSUPPORTED,
                                         UNSUPPORTED_LINE, UNSUPPORTED_MARKET, UNSUPPORTED_QUARTER_LINE, VALUE_EVALUATED,
                                         QuoteValueResult, SelectionProbabilities, evaluate_quote_value)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = timezone.utc


def at(hh, mm=0):
    return datetime(2026, 1, 10, hh, mm, tzinfo=UTC)


MAPPING = MappingProvenance(mapping_ref="map-syn-1", method="manual", provider="prov-syn", provider_event_id="ev-1", fixture_id=101)


def target(market=ONE_X_TWO, selection="HOME", line=None, **changes) -> QuoteTarget:
    base = QuoteTarget(provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1", fixture_id=101, market=market,
                       selection=selection, line=line)
    return dataclasses.replace(base, **changes)


def context(tgt=None, **changes) -> EvaluationContext:
    base = EvaluationContext(prediction_id="pred-syn-1", issued_at=at(12), kickoff_at=at(15), max_quote_age=timedelta(hours=3),
                             target=tgt or target())
    return dataclasses.replace(base, **changes)


def quote(**changes) -> QuoteObservation:
    base = QuoteObservation(evidence_id="evd-a", provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1", mapping=MAPPING,
                            market=ONE_X_TWO, selection="HOME", line=None, settlement_convention="FT90_V1", decimal_odds=Decimal("2.10"),
                            quote_status="OPEN", provider_quoted_at=at(11), provider_updated_at=at(11), received_at=at(11, 30))
    return dataclasses.replace(base, **changes)


def probs(ctx=None, p_win=0.5, p_lose=None, p_push=None, **changes) -> SelectionProbabilities:
    t = (ctx or context()).target
    base = SelectionProbabilities(prediction_id=(ctx or context()).prediction_id, fixture_id=t.fixture_id, market=t.market,
                                  selection=t.selection, line=t.line, p_win=p_win, p_lose=p_lose, p_push=p_push)
    return dataclasses.replace(base, **changes)


OU25 = context(target(OVER_UNDER, "OVER", "2.5"))
OU20 = context(target(OVER_UNDER, "OVER", "2.0"))


def ou_quote(line, **changes):
    return quote(market=OVER_UNDER, selection="OVER", line=line, **changes)


@pytest.fixture
def engine(monkeypatch):
    """Espía sobre las funciones REALES del motor de valor: registra las llamadas y devuelve su resultado."""
    calls = {"binary": [], "push": []}
    real_binary, real_push = market_value.evaluate_binary_value, market_value.evaluate_push_value

    def binary(**kw):
        out = real_binary(**kw)
        calls["binary"].append((kw, out))
        return out

    def push(**kw):
        out = real_push(**kw)
        calls["push"].append((kw, out))
        return out

    monkeypatch.setattr(market_value, "evaluate_binary_value", binary)
    monkeypatch.setattr(market_value, "evaluate_push_value", push)
    return calls


def assert_no_value(result, reasons, eligibility_reasons=None):
    assert isinstance(result, QuoteValueResult)
    assert (result.status, result.reasons, result.valuation) == (NO_VALUE, reasons, None)
    if eligibility_reasons is not None:
        assert result.eligibility_reasons == eligibility_reasons


# --- K: una cuota elegible pasa por el motor EXISTENTE y conserva su identidad -------------------

def test_k_single_eligible_1x2_quote_flows_through_the_existing_binary_engine(engine):
    result = evaluate_quote_value(context(), [quote()], probs())
    assert (result.status, result.reasons, result.eligible_count, result.quotes_received) == (VALUE_EVALUATED, (), 1, 1)
    assert result.qualified == QualifiedQuote(evidence_id="evd-a", provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1",
                                              fixture_id=101, mapping_ref="map-syn-1", market=ONE_X_TWO, selection="HOME", line=None,
                                              decimal_odds=Decimal("2.10"), received_at=at(11, 30))
    assert str(result.offered_decimal_odds) == "2.10" and result.engine_decimal_odds == 2.1 and result.odds_conversion_exact is False
    (kw, out), = engine["binary"]
    assert kw == {"p_win": 0.5, "decimal_odds": 2.1} and engine["push"] == []
    assert result.valuation is out  # el objeto del motor, no una reimplementación
    assert result.valuation == market_value.evaluate_binary_value(p_win=0.5, decimal_odds=2.1)
    assert (result.valuation.evaluator_id, result.valuation.settlement, result.valuation.fair_odds) == ("MARKET_VALUE_MATH", "BINARY_WIN_LOSE", 2.0)
    assert abs(result.valuation.expected_net_return - 0.05) < 1e-15
    assert (result.numeric_basis, result.pit_status, result.settlement_convention) == (NUMERIC_BASIS, "NOT_CERTIFIED", "FT90_V1")
    assert "NOT_TRUE_PIT_CERTIFIED" in result.limitations and "DOUBLE_PRECISION_APPROXIMATION_NOT_DECIMAL_EXACT" in result.limitations


def test_k_btts_uses_the_binary_engine(engine):
    ctx = context(target(BTTS, "YES"))
    result = evaluate_quote_value(ctx, [quote(market=BTTS, selection="YES", decimal_odds=Decimal("1.80"))], probs(ctx, p_win=0.6))
    assert result.status == VALUE_EVALUATED and len(engine["binary"]) == 1 and engine["push"] == []
    assert result.valuation == market_value.evaluate_binary_value(p_win=0.6, decimal_odds=1.8)


def test_k_exactly_representable_price_is_flagged_exact():
    result = evaluate_quote_value(context(), [quote(decimal_odds=Decimal("2.5"))], probs())
    assert (result.status, result.engine_decimal_odds, result.odds_conversion_exact) == (VALUE_EVALUATED, 2.5, True)


# --- A/B/C/J: ninguna cuota no elegible o múltiple llega al motor ---------------------------------

def test_a_quote_received_after_issuance_never_reaches_the_engine_even_at_huge_odds(engine):
    late = quote(decimal_odds=Decimal("50.0"), provider_quoted_at=at(11, 55), provider_updated_at=at(11, 55), received_at=at(12, 5))
    result = evaluate_quote_value(context(), [late], probs(p_win=0.9))
    assert_no_value(result, (NO_ELIGIBLE_QUOTE,), (QUOTE_NOT_KNOWN_AT_ISSUE,))
    assert (result.qualified, result.offered_decimal_odds, result.engine_decimal_odds) == (None, None, None)
    assert engine == {"binary": [], "push": []}


@pytest.mark.parametrize("c_id, expected", [("evd-c", (QUOTE_TEMPORAL_AMBIGUITY,)), ("", (IDENTITY_INVALID, QUOTE_TEMPORAL_AMBIGUITY))],
                         ids=["valid_id", "invalid_id"])
def test_b_contradictory_prices_at_the_same_key_and_instant_give_no_value(engine, c_id, expected):
    a, c = quote(), quote(evidence_id=c_id, decimal_odds=Decimal("2.20"))
    for order in ([a, c], [c, a]):
        assert_no_value(evaluate_quote_value(context(), order, probs()), (NO_ELIGIBLE_QUOTE,), expected)
    assert engine == {"binary": [], "push": []}


def test_c_inconsistent_response_evidence_id_gives_no_value(engine):
    other = quote(selection="DRAW", received_at=at(11, 45))  # mismo evidence_id que el objetivo, otra recepción
    result = evaluate_quote_value(context(), [quote(), other], probs())
    assert_no_value(result, (NO_ELIGIBLE_QUOTE,), (SELECTION_MISMATCH, EVIDENCE_ID_INCONSISTENT))
    assert engine == {"binary": [], "push": []}


@pytest.mark.parametrize("quotes, count", [
    ([quote(), quote(evidence_id="evd-dup", decimal_odds=Decimal("2.1"))], 2),  # duplicados equivalentes
    ([quote(), quote(evidence_id="evd-b", decimal_odds=Decimal("3.00"), provider_quoted_at=at(10), provider_updated_at=at(10),
                     received_at=at(11))], 2),  # dos recepciones distintas
], ids=["equivalent_duplicates", "two_receipt_times"])
def test_j_multiple_eligible_quotes_are_never_reduced_to_one(engine, quotes, count):
    for order in itertools.permutations(quotes):
        result = evaluate_quote_value(context(), list(order), probs())
        assert_no_value(result, (MULTIPLE_ELIGIBLE_QUOTES,), ())
        assert (result.eligible_count, result.qualified, result.offered_decimal_odds) == (count, None, None)
    assert engine == {"binary": [], "push": []}


def test_j_zero_quotes_give_no_value(engine):
    result = evaluate_quote_value(context(), [], probs())
    assert_no_value(result, (NO_ELIGIBLE_QUOTE,), ())
    assert (result.quotes_received, result.eligible_count) == (0, 0) and engine == {"binary": [], "push": []}


def test_j_one_eligible_quote_among_rejected_ones_is_the_one_evaluated(engine):
    others = [quote(evidence_id="evd-b", bookmaker_id="book-b", decimal_odds=Decimal("9.0")),
              quote(evidence_id="evd-l", decimal_odds=Decimal("8.0"), provider_quoted_at=at(12), provider_updated_at=at(12), received_at=at(12, 1))]
    result = evaluate_quote_value(context(), [*others, quote()], probs())
    assert (result.status, result.qualified.evidence_id, result.eligible_count) == (VALUE_EVALUATED, "evd-a", 1)
    assert result.eligibility_reasons == (BOOKMAKER_MISMATCH, QUOTE_NOT_KNOWN_AT_ISSUE)
    assert [kw["decimal_odds"] for kw, _ in engine["binary"]] == [2.1]


# --- D/I: identidad, mapeo, convención, precio y tiempos ------------------------------------------

@pytest.mark.parametrize("changes, expected", [
    ({"bookmaker_id": "book-b"}, (BOOKMAKER_MISMATCH,)),
    ({"provider": "prov-other", "mapping": dataclasses.replace(MAPPING, provider="prov-other")}, (PROVIDER_MISMATCH,)),
    ({"provider_event_id": "ev-2", "mapping": dataclasses.replace(MAPPING, provider_event_id="ev-2")}, (EVENT_MISMATCH,)),
    ({"mapping": dataclasses.replace(MAPPING, fixture_id=102)}, (FIXTURE_MISMATCH,)),
    ({"mapping": None}, (MAPPING_PROVENANCE_MISSING,)),
    ({"mapping": dataclasses.replace(MAPPING, method="auto_fuzzy")}, (MAPPING_METHOD_NOT_APPROVED,)),
    ({"market": BTTS, "selection": "YES"}, (MARKET_MISMATCH, SELECTION_MISMATCH)),
    ({"selection": "AWAY"}, (SELECTION_MISMATCH,)),
    ({"settlement_convention": "AET_INCLUSIVE_V1"}, (SETTLEMENT_CONVENTION_UNSUPPORTED,)),
    ({"decimal_odds": Decimal("NaN")}, (PRICE_INVALID,)),
    ({"decimal_odds": 2.1}, (PRICE_INVALID,)),
    ({"received_at": datetime(2026, 1, 10, 11, 30)}, (RECEIVED_AT_INVALID,)),
    ({"evidence_id": ""}, (IDENTITY_INVALID,)),
    ({"received_at": at(8, 59), "provider_quoted_at": at(8), "provider_updated_at": at(8)}, (QUOTE_STALE,)),
], ids=["bookmaker", "provider", "event", "fixture", "no_mapping", "fuzzy_mapping", "market", "selection", "convention", "nan_price",
        "float_price", "naive_receipt", "empty_evidence_id", "stale"])
def test_d_i_rejected_quotes_never_reach_the_engine(engine, changes, expected):
    assert_no_value(evaluate_quote_value(context(), [quote(**changes)], probs()), (NO_ELIGIBLE_QUOTE,), expected)
    assert engine == {"binary": [], "push": []}


def test_d_wrong_over_under_line_is_rejected(engine):
    assert_no_value(evaluate_quote_value(OU25, [ou_quote("3.5")], probs(OU25, p_win=0.5)), (NO_ELIGIBLE_QUOTE,), (LINE_MISMATCH,))
    assert engine == {"binary": [], "push": []}


@pytest.mark.parametrize("price", [Decimal("1e400"), Decimal("1.00000000000000000001")], ids=["overflows_double", "rounds_to_one"])
def test_i_eligible_price_without_a_valid_double_gives_no_value(engine, price):
    result = evaluate_quote_value(context(), [quote(decimal_odds=price)], probs())
    assert_no_value(result, (ODDS_NOT_REPRESENTABLE_AS_DOUBLE,), ())
    assert result.offered_decimal_odds == price and result.qualified is not None and result.engine_decimal_odds is None
    assert engine == {"binary": [], "push": []}


# --- E/F/G: reglas por mercado y línea -------------------------------------------------------------

def test_e_half_line_uses_binary_value_with_p_win_only(engine):
    result = evaluate_quote_value(OU25, [ou_quote("2.5", decimal_odds=Decimal("1.95"))], probs(OU25, p_win=0.55))
    assert result.status == VALUE_EVALUATED and result.valuation.settlement == "BINARY_WIN_LOSE" and engine["push"] == []
    assert result.valuation == market_value.evaluate_binary_value(p_win=0.55, decimal_odds=1.95)


@pytest.mark.parametrize("changes, expected", [({"p_lose": 0.45}, PROBABILITY_SHAPE_INVALID), ({"p_push": 0.0}, PROBABILITY_SHAPE_INVALID),
                                               ({"p_win": None}, PROBABILITY_MISSING)], ids=["p_lose_given", "p_push_given", "no_p_win"])
def test_e_half_line_rejects_any_other_probability_shape(engine, changes, expected):
    result = evaluate_quote_value(OU25, [ou_quote("2.5")], probs(OU25, **{"p_win": 0.55, **changes}))
    assert_no_value(result, (expected,), ())
    assert result.qualified is not None and engine == {"binary": [], "push": []}


def test_f_integer_line_uses_push_value_with_three_explicit_masses(engine):
    result = evaluate_quote_value(OU20, [ou_quote("2.0", decimal_odds=Decimal("2.20"))], probs(OU20, p_win=0.4, p_lose=0.35, p_push=0.25))
    assert result.status == VALUE_EVALUATED and result.valuation.settlement == "WIN_LOSE_PUSH" and engine["binary"] == []
    (kw, out), = engine["push"]
    assert kw == {"p_win": 0.4, "p_lose": 0.35, "p_push": 0.25, "decimal_odds": 2.2} and result.valuation is out
    assert result.valuation == market_value.evaluate_push_value(p_win=0.4, p_lose=0.35, p_push=0.25, decimal_odds=2.2)


@pytest.mark.parametrize("masses", [{"p_win": 0.4, "p_lose": 0.6}, {"p_win": 0.4, "p_push": 0.6}, {"p_win": None, "p_lose": 0.5, "p_push": 0.5},
                                    {"p_win": 0.4}], ids=["no_push", "no_lose", "no_win", "only_win"])
def test_f_integer_line_never_infers_a_missing_mass(engine, masses):
    result = evaluate_quote_value(OU20, [ou_quote("2.0")], probs(OU20, **masses))
    assert_no_value(result, (PROBABILITY_MISSING,), ())
    assert engine == {"binary": [], "push": []}


def test_f_incoherent_integer_line_masses_are_rejected_by_the_engine(engine):
    result = evaluate_quote_value(OU20, [ou_quote("2.0")], probs(OU20, p_win=0.4, p_lose=0.35, p_push=0.24))
    assert_no_value(result, (PROBABILITY_INVALID,), ())
    assert "no suman 1" in result.engine_error and len(engine["push"]) == 0  # el motor lanzó: no hay resultado


@pytest.mark.parametrize("line", ["2.25", "2.75", "0.25", "6.75", "10.25"])
def test_g_quarter_lines_are_unsupported_without_evaluating_anything(engine, monkeypatch, line):
    monkeypatch.setattr(bridge, "evaluate_quote_eligibility", lambda *a: pytest.fail("no se evalúa la elegibilidad"))
    result = evaluate_quote_value(context(target(OVER_UNDER, "OVER", line)), [ou_quote(line)], probs(p_win=0.5))
    assert (result.status, result.reasons, result.eligible_count, result.quotes_received) == (UNSUPPORTED, (UNSUPPORTED_QUARTER_LINE,), None, 0)
    assert result.valuation is None and engine == {"binary": [], "push": []}


@pytest.mark.parametrize("tgt, reason", [
    (target(OVER_UNDER, "OVER", "7.5"), UNSUPPORTED_LINE), (target(OVER_UNDER, "OVER", "2.4"), UNSUPPORTED_LINE),
    (target(OVER_UNDER, "OVER", "dos"), UNSUPPORTED_LINE), (target(OVER_UNDER, "OVER", None), UNSUPPORTED_LINE),
    (target(OVER_UNDER, "OVER", "-0.25"), UNSUPPORTED_LINE),
    (target("ASIAN_HANDICAP_FT90", "HOME", "-0.5"), UNSUPPORTED_MARKET), (target("1X2_AET", "HOME"), UNSUPPORTED_MARKET),
], ids=["line_7.5", "line_2.4", "line_text", "no_line", "negative_quarter", "asian_handicap", "1x2_aet"])
def test_g_other_unsupported_targets(engine, tgt, reason):
    result = evaluate_quote_value(context(tgt), [quote()], probs())
    assert (result.status, result.reasons) == (UNSUPPORTED, (reason,)) and engine == {"binary": [], "push": []}


# --- H: probabilidades -----------------------------------------------------------------------------

@pytest.mark.parametrize("p, expected", [
    (None, PROBABILITY_MISSING), ({"p_win": 0.5}, PROBABILITY_MALFORMED), (0.5, PROBABILITY_MALFORMED),
    (probs(prediction_id="pred-other"), PROBABILITY_IDENTITY_MISMATCH), (probs(fixture_id=102), PROBABILITY_IDENTITY_MISMATCH),
    (probs(fixture_id=101.0), PROBABILITY_IDENTITY_MISMATCH), (probs(market=BTTS), PROBABILITY_IDENTITY_MISMATCH),
    (probs(selection="DRAW"), PROBABILITY_IDENTITY_MISMATCH), (probs(line="2.5"), PROBABILITY_IDENTITY_MISMATCH),
    (probs(p_win=None), PROBABILITY_MISSING), (probs(p_lose=0.5), PROBABILITY_SHAPE_INVALID),
], ids=["none", "dict", "number", "other_prediction", "other_fixture", "float_fixture_equal_by_value", "other_market", "other_selection", "other_line",
        "no_p_win", "binary_with_p_lose"])
def test_h_missing_malformed_or_mismatched_probabilities_never_reach_the_engine(engine, p, expected):
    result = evaluate_quote_value(context(), [quote()], p)
    assert_no_value(result, (expected,), ())
    assert result.qualified is not None and engine == {"binary": [], "push": []}


@pytest.mark.parametrize("p_win", [True, 1.5, -0.1, float("nan"), float("inf"), "0.5", Decimal("0.5")],
                         ids=["bool", "above_one", "negative", "nan", "inf", "text", "decimal"])
def test_h_probabilities_are_validated_by_the_existing_engine_contract(p_win):
    result = evaluate_quote_value(context(), [quote()], probs(p_win=p_win))
    assert_no_value(result, (PROBABILITY_INVALID,), ())
    assert result.engine_error.startswith("p_win ") and result.engine_decimal_odds == 2.1


# --- L/M: determinismo, inmutabilidad, contrato de entrada y aislamiento ---------------------------

MIXED = [quote(), quote(evidence_id="evd-b", bookmaker_id="book-b", decimal_odds=Decimal("9.0")),
         quote(evidence_id="evd-l", decimal_odds=Decimal("50.0"), provider_quoted_at=at(12), provider_updated_at=at(12), received_at=at(12, 1)),
         quote(evidence_id="evd-s", provider_quoted_at=at(8), provider_updated_at=at(8), received_at=at(8, 30))]


def test_l_result_is_independent_of_quote_order_and_reproducible():
    reference = evaluate_quote_value(context(), MIXED, probs())
    assert reference.status == VALUE_EVALUATED and reference.qualified.evidence_id == "evd-a"
    for order in itertools.permutations(MIXED):
        assert evaluate_quote_value(context(), list(order), probs()) == reference
    assert evaluate_quote_value(context(), tuple(MIXED), probs()) == reference


def test_l_inputs_are_not_modified_and_outputs_are_immutable():
    quotes, ctx, p = list(MIXED), context(), probs()
    snapshot = copy.deepcopy((quotes, ctx, p))
    result = evaluate_quote_value(ctx, quotes, p)
    assert (quotes, ctx, p) == snapshot
    for obj, field in ((result, "status"), (result, "valuation"), (p, "p_win"), (result.valuation, "expected_net_return")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, None)
    assert isinstance(result.reasons, tuple) and isinstance(result.limitations, tuple)


@pytest.mark.parametrize("bad", [dict(ctx=context(issued_at=datetime(2026, 1, 10, 12))), dict(quotes=(q for q in [quote()])),
                                 dict(quotes=[QualifiedQuote("evd-a", "prov-syn", "book-a", "ev-1", 101, "map-syn-1", ONE_X_TWO, "HOME",
                                                             None, Decimal("2.10"), at(11, 30))])],
                         ids=["naive_issuance", "generator", "injected_qualified_quote"])
def test_m_malformed_context_or_collection_raise_the_eligibility_error(bad):
    with pytest.raises(EligibilityInputError):
        evaluate_quote_value(bad.get("ctx", context()), bad.get("quotes", [quote()]), probs())


def test_m_output_schema_has_no_stake_bankroll_or_recommendation_fields():
    names = {f.name for cls in (QuoteValueResult, SelectionProbabilities) for f in dataclasses.fields(cls)}
    banned = ("stake", "kelly", "bankroll", "recommend", "pick", "rank", "edge", "clv", "roi", "profit", "bet", "yield", "true_pit")
    assert not [n for n in names if any(b == part for part in n.split("_") for b in banned)]
    assert len(set(REASONS)) == len(REASONS)


def test_m_module_imports_no_app_database_provider_or_numeric_stack():
    code = ("import sys; import research.quote_value_bridge; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'requests', 'httpx')"
            " or m.startswith('research.')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0
    assert out.stdout.strip() == str(sorted(["research.market_value_math", "research.market_value_math.value", "research.quote_temporal_eligibility",
                                             "research.quote_value_bridge"]))
