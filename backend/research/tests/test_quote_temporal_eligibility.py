"""MOD-DEV-102C: elegibilidad temporal de cuotas sintéticas. Sin BD, sin red, sin app.
    python -m pytest research/tests/test_quote_temporal_eligibility.py -q   (desde backend/)
Todas las cuotas, timestamps, mapeos y bookmakers son sintéticos: ninguno es una cotización auténtica,
una recepción real de Prediktia ni un histórico PIT."""

import copy
import dataclasses
import itertools
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from research.m58.goal_labels import SUPPORTED_LINE_TEXTS as M58_SUPPORTED_LINE_TEXTS
from research.quote_temporal_eligibility import (BOOKMAKER_MISMATCH, BTTS, CLOSED, ELIGIBLE, EVALUATOR_ID, EVENT_MISMATCH,
                                                 EVIDENCE_ID_INCONSISTENT, FIXTURE_MISMATCH, IDENTITY_INVALID, INELIGIBLE,
                                                 INPUT_BASIS, ISSUED_AT_NOT_BEFORE_KICKOFF, LIMITATIONS, LINE_INVALID,
                                                 LINE_MISMATCH, MAPPING_INCONSISTENT, MAPPING_METHOD_NOT_APPROVED,
                                                 MAPPING_PROVENANCE_INVALID, MAPPING_PROVENANCE_MISSING, MARKET_MISMATCH,
                                                 MARKET_UNSUPPORTED, ONE_X_TWO, OVER_UNDER, PIT_STATUS, PRICE_INVALID,
                                                 PROVIDER_MISMATCH, PROVIDER_TIMESTAMP_AFTER_RECEIPT, PROVIDER_TIMESTAMP_INVALID,
                                                 PROVIDER_TIMESTAMPS_INCONSISTENT, QUOTE_NOT_KNOWN_AT_ISSUE, QUOTE_STALE,
                                                 QUOTE_STATUS_NOT_ADMISSIBLE, QUOTE_STATUS_UNKNOWN, QUOTE_TEMPORAL_AMBIGUITY,
                                                 REASONS, RECEIVED_AT_INVALID, SELECTION_INVALID, SELECTION_MISMATCH,
                                                 SETTLEMENT_CONVENTION_UNSUPPORTED, SUPPORTED_LINE_TEXTS, SUSPENDED,
                                                 EligibilityInputError, EligibilityReport, EvaluationContext, MappingProvenance,
                                                 QualifiedQuote, QuoteEligibility, QuoteObservation, QuoteTarget,
                                                 evaluate_quote_eligibility)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UTC = timezone.utc
MINUS_3 = timezone(timedelta(hours=-3))


def at(hh, mm=0, ss=0, tz=UTC):
    """Instante sintético del 10/01/2026 (hora en la zona dada)."""
    return datetime(2026, 1, 10, hh, mm, ss, tzinfo=tz)


MAPPING = MappingProvenance(mapping_ref="map-syn-1", method="manual", provider="prov-syn", provider_event_id="ev-1", fixture_id=101)
TARGET = QuoteTarget(provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1", fixture_id=101, market=ONE_X_TWO,
                     selection="HOME", line=None)
CONTEXT = EvaluationContext(prediction_id="pred-syn-1", issued_at=at(12), kickoff_at=at(15), max_quote_age=timedelta(hours=3),
                            target=TARGET)
# A: cotización afirmada a las 11:00, recibida a las 11:30, cuota 2.10
Q_A = QuoteObservation(evidence_id="evd-a", provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1", mapping=MAPPING,
                       market=ONE_X_TWO, selection="HOME", line=None, settlement_convention="FT90_V1",
                       decimal_odds=Decimal("2.10"), quote_status="OPEN", provider_quoted_at=at(11),
                       provider_updated_at=at(11), received_at=at(11, 30))


def quote(**changes) -> QuoteObservation:
    return dataclasses.replace(Q_A, **changes)


def evaluate(*quotes, context=CONTEXT) -> EligibilityReport:
    return evaluate_quote_eligibility(context, list(quotes))


def reasons(q, context=CONTEXT) -> tuple[str, ...]:
    return evaluate(q, context=context).outcomes[0].reasons


def by_evidence(report, quotes) -> dict:
    return {quotes[o.position].evidence_id: (o.status, o.reasons, o.qualified) for o in report.outcomes}


# --- A/B/C: casos obligatorios ------------------------------------------------------------------

def test_a_eligible_synthetic_home_quote():
    report = evaluate(Q_A)
    (outcome,) = report.outcomes
    assert (outcome.position, outcome.status, outcome.reasons) == (0, ELIGIBLE, ())
    assert outcome.qualified == QualifiedQuote(evidence_id="evd-a", provider="prov-syn", bookmaker_id="book-a", provider_event_id="ev-1",
                                               fixture_id=101, mapping_ref="map-syn-1", market=ONE_X_TWO, selection="HOME", line=None,
                                               decimal_odds=Decimal("2.10"), received_at=at(11, 30))
    assert report.eligible == (outcome.qualified,)
    assert (report.evaluator_id, report.input_basis, report.pit_status) == (EVALUATOR_ID, INPUT_BASIS, "NOT_CERTIFIED")


def test_b_late_known_quote_is_rejected_although_provider_time_precedes_issuance():
    late = quote(evidence_id="evd-b", decimal_odds=Decimal("2.40"), provider_quoted_at=at(11, 55), provider_updated_at=at(11, 55),
                 received_at=at(12, 5))
    (outcome,) = evaluate(late).outcomes
    assert (outcome.status, outcome.reasons, outcome.qualified) == (INELIGIBLE, (QUOTE_NOT_KNOWN_AT_ISSUE,), None)
    # Regresión dirigida: una implementación que tomara provider_quoted_at como conocimiento la daría por elegible
    assert late.provider_quoted_at <= CONTEXT.issued_at < late.received_at


def test_b_provider_time_never_rescues_a_later_receipt_even_without_provider_update():
    late = quote(provider_quoted_at=at(9), provider_updated_at=None, received_at=at(12, 0, 1))
    assert reasons(late) == (QUOTE_NOT_KNOWN_AT_ISSUE,)


def test_c_conflicting_prices_at_same_identity_and_receipt_reject_all_in_any_order():
    q1 = Q_A
    q3 = quote(evidence_id="evd-c", decimal_odds=Decimal("2.20"))
    earlier = quote(evidence_id="evd-0", decimal_odds=Decimal("2.05"), provider_quoted_at=at(10), provider_updated_at=at(10),
                    received_at=at(10, 30))
    for order in itertools.permutations([q1, q3, earlier]):
        result = by_evidence(evaluate(*order), order)
        assert result["evd-a"] == (INELIGIBLE, (QUOTE_TEMPORAL_AMBIGUITY,), None)
        assert result["evd-c"] == (INELIGIBLE, (QUOTE_TEMPORAL_AMBIGUITY,), None)
        assert result["evd-0"][0] == ELIGIBLE  # otro instante: no es la misma evidencia temporal


def test_conflict_is_detected_across_utc_offsets_of_the_same_instant():
    other = quote(evidence_id="evd-c", decimal_odds=Decimal("2.20"), received_at=at(8, 30, tz=MINUS_3))  # = 11:30Z
    assert [o.reasons for o in evaluate(Q_A, other).outcomes] == [(QUOTE_TEMPORAL_AMBIGUITY,)] * 2


@pytest.mark.parametrize("field, value", [("quote_status", SUSPENDED), ("provider_quoted_at", at(11, 1)),
                                          ("mapping", dataclasses.replace(MAPPING, mapping_ref="map-syn-2"))])
def test_any_content_conflict_at_the_same_instant_is_ambiguous(field, value):
    other = quote(evidence_id="evd-c", **{field: value})
    for outcome in evaluate(Q_A, other).outcomes:
        assert outcome.status == INELIGIBLE and QUOTE_TEMPORAL_AMBIGUITY in outcome.reasons


def test_invalid_price_at_the_same_instant_makes_the_valid_quote_ambiguous_too():
    first, second = evaluate(Q_A, quote(evidence_id="evd-c", decimal_odds=Decimal("NaN"))).outcomes
    assert first.reasons == (QUOTE_TEMPORAL_AMBIGUITY,) and second.reasons == (PRICE_INVALID, QUOTE_TEMPORAL_AMBIGUITY)


def test_duplicate_equivalent_evidence_is_not_ambiguous():
    duplicate = quote(evidence_id="evd-dup", decimal_odds=Decimal("2.1"))  # mismo valor exacto que 2.10
    outcomes = evaluate(Q_A, duplicate).outcomes
    assert [o.status for o in outcomes] == [ELIGIBLE, ELIGIBLE]
    assert [o.qualified.evidence_id for o in outcomes] == ["evd-a", "evd-dup"]


def test_distinct_selections_markets_and_bookmakers_never_conflict_with_the_target():
    draw = quote(evidence_id="evd-d", selection="DRAW", decimal_odds=Decimal("3.30"))
    other_book = quote(evidence_id="evd-o", bookmaker_id="book-b", decimal_odds=Decimal("2.30"))
    result = by_evidence(evaluate(Q_A, draw, other_book), [Q_A, draw, other_book])
    assert result["evd-a"][0] == ELIGIBLE
    assert result["evd-d"][1] == (SELECTION_MISMATCH,) and result["evd-o"][1] == (BOOKMAKER_MISMATCH,)


def test_evidence_id_with_two_receipt_times_is_inconsistent_everywhere():
    other = quote(selection="DRAW", received_at=at(11, 45))  # mismo evidence_id, otra recepción
    first, second = evaluate(Q_A, other).outcomes
    assert first.reasons == (EVIDENCE_ID_INCONSISTENT,) and second.reasons == (SELECTION_MISMATCH, EVIDENCE_ID_INCONSISTENT)


# --- identidad, mapeo, convención y forma FT90 ---------------------------------------------------

@pytest.mark.parametrize("mapping, expected", [
    (None, (MAPPING_PROVENANCE_MISSING,)),
    ({"fixture_id": 101}, (MAPPING_PROVENANCE_INVALID,)),
    (dataclasses.replace(MAPPING, mapping_ref=""), (MAPPING_PROVENANCE_INVALID,)),
    (dataclasses.replace(MAPPING, fixture_id=True), (MAPPING_PROVENANCE_INVALID,)),
    (dataclasses.replace(MAPPING, provider="prov-other"), (MAPPING_INCONSISTENT,)),
    (dataclasses.replace(MAPPING, provider_event_id="ev-2"), (MAPPING_INCONSISTENT,)),
    (dataclasses.replace(MAPPING, method="auto_fuzzy"), (MAPPING_METHOD_NOT_APPROVED,)),
    (dataclasses.replace(MAPPING, fixture_id=102), (FIXTURE_MISMATCH,)),
], ids=["missing", "not_provenance", "empty_ref", "bool_fixture", "other_provider", "other_event", "fuzzy", "other_fixture"])
def test_mapping_provenance_is_required_consistent_and_approved(mapping, expected):
    assert reasons(quote(mapping=mapping)) == expected


@pytest.mark.parametrize("changes, expected", [
    ({"bookmaker_id": "book-b"}, (BOOKMAKER_MISMATCH,)),
    ({"provider": "prov-other", "mapping": dataclasses.replace(MAPPING, provider="prov-other")}, (PROVIDER_MISMATCH,)),
    ({"provider_event_id": "ev-2", "mapping": dataclasses.replace(MAPPING, provider_event_id="ev-2")}, (EVENT_MISMATCH,)),
    ({"market": BTTS, "selection": "YES"}, (MARKET_MISMATCH, SELECTION_MISMATCH)),
    ({"selection": "AWAY"}, (SELECTION_MISMATCH,)),
    ({"bookmaker_id": ""}, (IDENTITY_INVALID,)),
    ({"bookmaker_id": " book-a"}, (IDENTITY_INVALID,)),
    ({"evidence_id": None}, (IDENTITY_INVALID,)),
], ids=["bookmaker", "provider", "event", "market", "selection", "empty_bookmaker", "padded_bookmaker", "no_evidence"])
def test_identity_must_match_the_single_target(changes, expected):
    assert reasons(quote(**changes)) == expected


@pytest.mark.parametrize("convention", ["AET_INCLUSIVE_V1", "FT90_V2", "ft90_v1", None])
def test_unsupported_settlement_convention(convention):
    assert reasons(quote(settlement_convention=convention)) == (SETTLEMENT_CONVENTION_UNSUPPORTED,)


@pytest.mark.parametrize("market, selection, line, expected", [
    ("1X2_AET", "HOME", None, (MARKET_UNSUPPORTED,)),
    (ONE_X_TWO, "OVER", None, (SELECTION_INVALID,)),
    (ONE_X_TWO, "HOME", "2.5", (LINE_INVALID,)),
    (OVER_UNDER, "OVER", None, (LINE_INVALID,)),
    (OVER_UNDER, "OVER", "2.25", (LINE_INVALID,)),
    (OVER_UNDER, "OVER", "2.50", (LINE_INVALID,)),
    (OVER_UNDER, "OVER", Decimal("2.5"), (LINE_INVALID,)),
    (OVER_UNDER, "OVER", 2.5, (LINE_INVALID,)),
    (BTTS, "HOME", None, (SELECTION_INVALID,)),
])
def test_invalid_ft90_market_selection_or_line(market, selection, line, expected):
    assert reasons(quote(market=market, selection=selection, line=line)) == expected


def test_over_under_with_exact_line():
    target = dataclasses.replace(TARGET, market=OVER_UNDER, selection="OVER", line="2.5")
    context = dataclasses.replace(CONTEXT, target=target)
    assert reasons(quote(market=OVER_UNDER, selection="OVER", line="2.5"), context) == ()
    assert reasons(quote(market=OVER_UNDER, selection="OVER", line="3.5"), context) == (LINE_MISMATCH,)
    assert SUPPORTED_LINE_TEXTS == M58_SUPPORTED_LINE_TEXTS  # las líneas aprobadas de la liquidación


@pytest.mark.parametrize("status, expected", [(SUSPENDED, QUOTE_STATUS_NOT_ADMISSIBLE), (CLOSED, QUOTE_STATUS_NOT_ADMISSIBLE),
                                              ("open", QUOTE_STATUS_UNKNOWN), ("", QUOTE_STATUS_UNKNOWN), (None, QUOTE_STATUS_UNKNOWN)])
def test_quote_status(status, expected):
    assert reasons(quote(quote_status=status)) == (expected,)


# --- precio --------------------------------------------------------------------------------------

@pytest.mark.parametrize("price", [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("-2.10"),
                                   Decimal("0"), Decimal("1"), Decimal("1.00"), 2.1, 2, "2.10", True, None, float("nan")],
                         ids=lambda p: f"{type(p).__name__}:{p}")
def test_invalid_decimal_prices(price):
    assert reasons(quote(decimal_odds=price)) == (PRICE_INVALID,)


def test_price_just_above_one_is_kept_exactly():
    (outcome,) = evaluate(quote(decimal_odds=Decimal("1.000001"))).outcomes
    assert outcome.status == ELIGIBLE and str(outcome.qualified.decimal_odds) == "1.000001"


# --- tiempos -------------------------------------------------------------------------------------

def test_receipt_exactly_at_issuance_and_exactly_max_age_are_eligible():
    assert reasons(quote(provider_quoted_at=None, provider_updated_at=None, received_at=at(12))) == ()
    assert reasons(quote(provider_quoted_at=at(8), provider_updated_at=at(8), received_at=at(9))) == ()


def test_stale_quote():
    assert reasons(quote(provider_quoted_at=at(8), provider_updated_at=at(8), received_at=at(8, 59, 59))) == (QUOTE_STALE,)


def test_staleness_is_measured_from_receipt_not_provider_time():
    # El proveedor afirma 08:00 (4 h antes), pero se recibió a las 11:30: no está vencida por la política de 3 h
    assert reasons(quote(provider_quoted_at=at(8), provider_updated_at=at(8))) == ()


@pytest.mark.parametrize("issued", [at(15), at(15, 0, 1), at(16)], ids=["at_kickoff", "after_by_1s", "after_by_1h"])
def test_issuance_at_or_after_kickoff_rejects_every_quote(issued):
    context = dataclasses.replace(CONTEXT, issued_at=issued)
    report = evaluate(Q_A, quote(evidence_id="evd-x", selection="DRAW"), context=context)
    assert all(o.status == INELIGIBLE and ISSUED_AT_NOT_BEFORE_KICKOFF in o.reasons for o in report.outcomes)


@pytest.mark.parametrize("received", [datetime(2026, 1, 10, 11, 30), "2026-01-10T11:30:00Z", None, at(11, 30).date()],
                         ids=["naive", "text", "none", "date"])
def test_received_at_must_be_an_aware_datetime(received):
    assert reasons(quote(received_at=received)) == (RECEIVED_AT_INVALID,)


@pytest.mark.parametrize("changes, expected", [
    ({"provider_quoted_at": datetime(2026, 1, 10, 11)}, (PROVIDER_TIMESTAMP_INVALID,)),
    ({"provider_updated_at": "11:00"}, (PROVIDER_TIMESTAMP_INVALID,)),
    ({"provider_quoted_at": at(11, 31), "provider_updated_at": at(11, 31)}, (PROVIDER_TIMESTAMP_AFTER_RECEIPT,)),
    ({"provider_updated_at": at(11, 45)}, (PROVIDER_TIMESTAMP_AFTER_RECEIPT,)),
    ({"provider_updated_at": at(10, 59)}, (PROVIDER_TIMESTAMPS_INCONSISTENT,)),
    ({"provider_quoted_at": None, "provider_updated_at": None}, ()),
], ids=["naive_claim", "text_claim", "claim_after_receipt", "update_after_receipt", "update_before_quote", "no_claims"])
def test_provider_timestamps_are_checked_but_never_establish_knowledge(changes, expected):
    assert reasons(quote(**changes)) == expected


def test_utc_offsets_are_normalized():
    context = dataclasses.replace(CONTEXT, issued_at=at(14, tz=timezone(timedelta(hours=2))), kickoff_at=at(12, tz=MINUS_3))
    report = evaluate(quote(provider_quoted_at=at(8, tz=MINUS_3), provider_updated_at=None, received_at=at(8, 30, tz=MINUS_3)),
                      context=context)
    (outcome,) = report.outcomes
    assert outcome.status == ELIGIBLE and outcome.qualified.received_at == at(11, 30) and outcome.qualified.received_at.tzinfo is UTC
    assert (report.issued_at, report.kickoff_at) == (at(12), at(15)) and report.issued_at.tzinfo is UTC


# --- contrato de entrada -------------------------------------------------------------------------

@pytest.mark.parametrize("context", [
    dataclasses.replace(CONTEXT, issued_at=datetime(2026, 1, 10, 12)),
    dataclasses.replace(CONTEXT, kickoff_at=None),
    dataclasses.replace(CONTEXT, max_quote_age=timedelta(0)),
    dataclasses.replace(CONTEXT, max_quote_age=timedelta(hours=-1)),
    dataclasses.replace(CONTEXT, max_quote_age=10_800),
    dataclasses.replace(CONTEXT, max_quote_age=None),
    dataclasses.replace(CONTEXT, prediction_id=""),
    dataclasses.replace(CONTEXT, target=dataclasses.replace(TARGET, market="1X2_AET")),
    dataclasses.replace(CONTEXT, target=dataclasses.replace(TARGET, fixture_id=0)),
    dataclasses.replace(CONTEXT, target=dataclasses.replace(TARGET, bookmaker_id="")),
    dataclasses.replace(CONTEXT, target=None),
    None,
], ids=["naive_issued", "no_kickoff", "zero_age", "negative_age", "age_seconds_int", "no_age", "no_prediction", "target_market",
        "target_fixture", "target_bookmaker", "no_target", "no_context"])
def test_invalid_context_fails_closed(context):
    with pytest.raises(EligibilityInputError):
        evaluate_quote_eligibility(context, [Q_A])


@pytest.mark.parametrize("quotes", [iter([Q_A]), (q for q in [Q_A]), {Q_A}, Q_A, [Q_A, dataclasses.asdict(Q_A)], None],
                         ids=["iterator", "generator", "set", "single", "dict_element", "none"])
def test_invalid_quote_collection_fails_closed(quotes):
    with pytest.raises(EligibilityInputError):
        evaluate_quote_eligibility(CONTEXT, quotes)


def test_empty_collection_gives_an_empty_report():
    report = evaluate_quote_eligibility(CONTEXT, ())
    assert report.outcomes == () and report.eligible == ()


def test_several_reasons_are_reported_in_the_fixed_order():
    bad = quote(bookmaker_id="book-b", mapping=None, quote_status=CLOSED, decimal_odds=Decimal("0.5"), received_at=at(12, 30))
    assert reasons(bad) == (BOOKMAKER_MISMATCH, MAPPING_PROVENANCE_MISSING, QUOTE_STATUS_NOT_ADMISSIBLE, PRICE_INVALID,
                            QUOTE_NOT_KNOWN_AT_ISSUE)
    assert len(set(REASONS)) == len(REASONS)


# --- MOD-DEV-102C-R1: ambigüedad y evidencia, fail-closed ---------------------------------------

class _NoOffset(tzinfo):
    """Zona que no sabe su desplazamiento: el instante no se puede normalizar."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None


class _OutOfRangeOffset(tzinfo):
    """Zona con un desplazamiento imposible: utcoffset() lanza ValueError."""

    def utcoffset(self, dt):
        return timedelta(hours=30)

    def dst(self, dt):
        return None


UNNORMALIZABLE = [None, datetime(2026, 1, 10, 11, 30), "2026-01-10T11:30:00Z", at(11, 30).date(),
                  datetime(2026, 1, 10, 11, 30, tzinfo=_NoOffset()), datetime(2026, 1, 10, 11, 30, tzinfo=_OutOfRangeOffset()),
                  datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))]
UNNORMALIZABLE_IDS = ["none", "naive", "text", "date", "tz_without_offset", "offset_out_of_range", "overflow_to_utc"]


def by_label(report, order, labels) -> dict:
    """Resultado por etiqueta del objeto de entrada (varios comparten evidence_id)."""
    return {labels[id(q)]: (o.status, o.reasons, o.qualified) for q, o in zip(order, report.outcomes)}


@pytest.mark.parametrize("c_id, c_reasons", [
    ("", (IDENTITY_INVALID, QUOTE_TEMPORAL_AMBIGUITY)),
    (None, (IDENTITY_INVALID, QUOTE_TEMPORAL_AMBIGUITY)),
    ("   ", (IDENTITY_INVALID, QUOTE_TEMPORAL_AMBIGUITY)),
    ("evd-c", (QUOTE_TEMPORAL_AMBIGUITY,)),  # control con evidence_id válido
], ids=["empty", "none", "whitespace", "valid_control"])
def test_r1_d1_invalid_evidence_id_does_not_mask_a_price_conflict(c_id, c_reasons):
    c = quote(evidence_id=c_id, decimal_odds=Decimal("2.20"))
    labels = {id(Q_A): "A", id(c): "C"}
    for order in ([Q_A, c], [c, Q_A]):
        report = evaluate(*order)
        assert by_label(report, order, labels) == {"A": (INELIGIBLE, (QUOTE_TEMPORAL_AMBIGUITY,), None), "C": (INELIGIBLE, c_reasons, None)}
        assert report.eligible == ()


def test_r1_d1_conflict_with_invalid_id_does_not_poison_other_books_events_markets_or_times():
    c = quote(evidence_id="", decimal_odds=Decimal("2.20"))
    other_book = quote(evidence_id="evd-b", bookmaker_id="book-b", decimal_odds=Decimal("2.30"))
    other_event = quote(evidence_id="evd-e", provider_event_id="ev-2", mapping=dataclasses.replace(MAPPING, provider_event_id="ev-2"),
                        decimal_odds=Decimal("2.40"))
    other_market = quote(evidence_id="evd-m", market=BTTS, selection="YES", decimal_odds=Decimal("1.80"))
    other_time = quote(evidence_id="evd-t", provider_quoted_at=at(10, 30), provider_updated_at=at(10, 30), received_at=at(11),
                       decimal_odds=Decimal("2.05"))
    quotes = [Q_A, c, other_book, other_event, other_market, other_time]
    labels = dict(zip(map(id, quotes), ["A", "C", "book", "event", "market", "time"]))
    result = by_label(evaluate(*quotes), quotes, labels)
    assert result["A"][:2] == (INELIGIBLE, (QUOTE_TEMPORAL_AMBIGUITY,))
    assert result["C"][:2] == (INELIGIBLE, (IDENTITY_INVALID, QUOTE_TEMPORAL_AMBIGUITY))
    assert result["book"][1] == (BOOKMAKER_MISMATCH,) and result["event"][1] == (EVENT_MISMATCH,)
    assert result["market"][1] == (MARKET_MISMATCH, SELECTION_MISMATCH)
    assert result["time"][0] == ELIGIBLE and result["time"][2].evidence_id == "evd-t"


def test_r1_d1_equivalent_duplicate_with_invalid_id_does_not_reject_the_valid_quote():
    c = quote(evidence_id="", decimal_odds=Decimal("2.1"))  # mismo contenido que A (2.10 == 2.1)
    a, dup = evaluate(Q_A, c).outcomes
    assert (a.status, a.reasons, a.qualified.evidence_id) == (ELIGIBLE, (), "evd-a")
    assert (dup.status, dup.reasons, dup.qualified) == (INELIGIBLE, (IDENTITY_INVALID,), None)


def test_r1_d1_invalid_evidence_ids_are_never_linked_to_each_other():
    first = quote(evidence_id="", selection="DRAW", provider_quoted_at=at(10), provider_updated_at=at(10), received_at=at(11))
    second = quote(evidence_id=None, selection="AWAY", received_at=at(11, 45))
    assert [o.reasons for o in evaluate(first, second).outcomes] == [(IDENTITY_INVALID, SELECTION_MISMATCH)] * 2


@pytest.mark.parametrize("bad_received", UNNORMALIZABLE, ids=UNNORMALIZABLE_IDS)
def test_r1_d2_reused_evidence_id_with_unproven_receipt_rejects_every_occurrence(bad_received):
    other = quote(selection="DRAW", decimal_odds=Decimal("3.30"), received_at=bad_received)  # mismo evidence_id que A
    unrelated = quote(evidence_id="evd-u", provider_quoted_at=at(11, 10), provider_updated_at=at(11, 10), received_at=at(11, 20),
                      decimal_odds=Decimal("2.05"))
    labels = {id(Q_A): "A", id(other): "O", id(unrelated): "U"}
    for order in itertools.permutations([Q_A, other, unrelated]):
        result = by_label(evaluate(*order), order, labels)
        assert result["A"] == (INELIGIBLE, (EVIDENCE_ID_INCONSISTENT,), None)
        assert result["O"] == (INELIGIBLE, (SELECTION_MISMATCH, RECEIVED_AT_INVALID, EVIDENCE_ID_INCONSISTENT), None)
        assert result["U"][0] == ELIGIBLE and result["U"][2].evidence_id == "evd-u"


@pytest.mark.parametrize("bad_received", UNNORMALIZABLE, ids=UNNORMALIZABLE_IDS)
def test_r1_unnormalizable_timestamps_fail_per_observation_without_aborting(bad_received):
    report = evaluate(quote(evidence_id="evd-x", received_at=bad_received),
                      quote(evidence_id="evd-y", provider_quoted_at=bad_received, received_at=at(11, 35)), Q_A)
    assert [o.reasons for o in report.outcomes[:2]] == [(RECEIVED_AT_INVALID,), (PROVIDER_TIMESTAMP_INVALID,) if bad_received is not None else ()]
    assert report.outcomes[2].status == ELIGIBLE


@pytest.mark.parametrize("field", ["issued_at", "kickoff_at"])
@pytest.mark.parametrize("bad", UNNORMALIZABLE[4:], ids=UNNORMALIZABLE_IDS[4:])
def test_r1_unnormalizable_context_instants_raise_the_documented_error(field, bad):
    with pytest.raises(EligibilityInputError):
        evaluate_quote_eligibility(dataclasses.replace(CONTEXT, **{field: bad}), [Q_A])


@pytest.mark.parametrize("changes, a_reasons", [
    ({"selection": "DRAW", "provider": "prov-other", "mapping": dataclasses.replace(MAPPING, provider="prov-other")},
     (EVIDENCE_ID_INCONSISTENT,)),
    ({"selection": "DRAW", "provider": ""}, (EVIDENCE_ID_INCONSISTENT,)),
    ({"selection": "DRAW", "provider_quoted_at": at(11, 40), "provider_updated_at": at(11, 40), "received_at": at(11, 45)},
     (EVIDENCE_ID_INCONSISTENT,)),
    ({"quote_status": SUSPENDED}, (EVIDENCE_ID_INCONSISTENT, QUOTE_TEMPORAL_AMBIGUITY)),
    ({"decimal_odds": Decimal("2.20")}, (EVIDENCE_ID_INCONSISTENT, QUOTE_TEMPORAL_AMBIGUITY)),
    ({"selection": "DRAW", "mapping": dataclasses.replace(MAPPING, mapping_ref="map-syn-2")}, (EVIDENCE_ID_INCONSISTENT,)),
    ({"selection": "DRAW", "mapping": None}, (EVIDENCE_ID_INCONSISTENT,)),
], ids=["other_provider", "invalid_provider", "other_receipt_time", "same_key_other_status", "same_key_other_price",
        "same_event_other_mapping", "same_event_without_mapping"])
def test_r1_d2_reused_evidence_id_with_material_inconsistency_rejects_all(changes, a_reasons):
    other = quote(**changes)  # mismo evidence_id que A
    labels = {id(Q_A): "A", id(other): "O"}
    for order in ([Q_A, other], [other, Q_A]):
        result = by_label(evaluate(*order), order, labels)
        assert result["A"] == (INELIGIBLE, a_reasons, None)
        assert result["O"][0] == INELIGIBLE and EVIDENCE_ID_INCONSISTENT in result["O"][1] and result["O"][2] is None


@pytest.mark.parametrize("changes, other_reasons", [
    ({"selection": "DRAW", "decimal_odds": Decimal("3.30")}, (SELECTION_MISMATCH,)),
    ({"bookmaker_id": "book-b", "decimal_odds": Decimal("2.30")}, (BOOKMAKER_MISMATCH,)),
    ({"provider_event_id": "ev-2", "mapping": dataclasses.replace(MAPPING, provider_event_id="ev-2")}, (EVENT_MISMATCH,)),
    ({"market": BTTS, "selection": "YES"}, (MARKET_MISMATCH, SELECTION_MISMATCH)),
    ({}, ()),  # la misma observación repetida
    ({"decimal_odds": Decimal("2.1")}, ()),  # equivalente: mismo valor exacto
], ids=["other_selection", "other_bookmaker", "other_event", "other_market", "exact_repeat", "equivalent_price"])
def test_r1_d2_one_response_may_carry_several_quotes_and_equivalent_repeats(changes, other_reasons):
    """Un evidence_id es una respuesta lógica: otras cuotas de la misma respuesta (mismo proveedor y
    received_at) no son incoherencias; se rechazan solo por no ser la cuota objetivo."""
    first, other = evaluate(Q_A, quote(**changes)).outcomes
    assert (first.status, first.reasons) == (ELIGIBLE, ())
    assert other.reasons == other_reasons and (other.qualified is None) == bool(other_reasons)


R1_MIXED = [
    Q_A,
    quote(evidence_id="", decimal_odds=Decimal("2.20")),  # conflicto con A pese al id no válido
    quote(evidence_id="evd-r", selection="DRAW", received_at=at(11, 40), provider_quoted_at=at(11, 35), provider_updated_at=at(11, 35)),
    quote(evidence_id="evd-r", selection="AWAY", received_at=None),  # reutiliza evd-r sin recepción válida
    quote(evidence_id="evd-u", provider_quoted_at=at(11, 10), provider_updated_at=at(11, 10), received_at=at(11, 20)),
    quote(evidence_id="evd-u2", provider_quoted_at=at(11, 10), provider_updated_at=at(11, 10), received_at=at(11, 20),
          decimal_odds=Decimal("2.1")),  # equivalente a evd-u
]


def test_r1_outcomes_are_independent_of_input_order():
    labels = dict(zip(map(id, R1_MIXED), ["A", "C", "R1", "R2", "U", "U2"]))
    reference = by_label(evaluate(*R1_MIXED), R1_MIXED, labels)
    assert {k: v[:2] for k, v in reference.items()} == {
        "A": (INELIGIBLE, (QUOTE_TEMPORAL_AMBIGUITY,)),
        "C": (INELIGIBLE, (IDENTITY_INVALID, QUOTE_TEMPORAL_AMBIGUITY)),
        "R1": (INELIGIBLE, (SELECTION_MISMATCH, EVIDENCE_ID_INCONSISTENT)),
        "R2": (INELIGIBLE, (SELECTION_MISMATCH, RECEIVED_AT_INVALID, EVIDENCE_ID_INCONSISTENT)),
        "U": (ELIGIBLE, ()),
        "U2": (ELIGIBLE, ()),
    }
    for order in itertools.permutations(R1_MIXED):
        assert by_label(evaluate(*order), order, labels) == reference


# --- determinismo, inmutabilidad e independencia del orden ---------------------------------------

MIXED = [
    Q_A,
    quote(evidence_id="evd-c", decimal_odds=Decimal("2.20"), received_at=at(11, 45)),
    quote(evidence_id="evd-c2", decimal_odds=Decimal("2.25"), received_at=at(11, 45)),
    quote(evidence_id="evd-b", decimal_odds=Decimal("2.40"), provider_quoted_at=at(11, 55), provider_updated_at=at(11, 55),
          received_at=at(12, 5)),
    quote(evidence_id="evd-dup", decimal_odds=Decimal("2.1")),
    quote(evidence_id="evd-s", provider_quoted_at=at(7), provider_updated_at=at(7), received_at=at(8)),
]


def test_outcomes_are_independent_of_input_order():
    reference = by_evidence(evaluate(*MIXED), MIXED)
    assert reference == {
        "evd-a": (ELIGIBLE, (), reference["evd-a"][2]),
        "evd-c": (INELIGIBLE, (QUOTE_TEMPORAL_AMBIGUITY,), None),
        "evd-c2": (INELIGIBLE, (QUOTE_TEMPORAL_AMBIGUITY,), None),
        "evd-b": (INELIGIBLE, (QUOTE_NOT_KNOWN_AT_ISSUE,), None),
        "evd-dup": (ELIGIBLE, (), reference["evd-dup"][2]),
        "evd-s": (INELIGIBLE, (QUOTE_STALE,), None),
    }
    permutations = list(itertools.permutations(MIXED))
    assert len(permutations) == 720
    for order in permutations:
        report = evaluate(*order)
        assert [o.position for o in report.outcomes] == list(range(len(order)))
        assert by_evidence(report, order) == reference


def test_evaluation_is_reproducible_and_does_not_modify_inputs():
    quotes, snapshot = list(MIXED), copy.deepcopy(MIXED)
    first, second = evaluate_quote_eligibility(CONTEXT, quotes), evaluate_quote_eligibility(CONTEXT, tuple(quotes))
    assert first == second and quotes == snapshot and CONTEXT == copy.deepcopy(CONTEXT)


def test_inputs_and_outputs_are_immutable():
    report = evaluate(Q_A)
    for obj, field in ((Q_A, "received_at"), (MAPPING, "fixture_id"), (TARGET, "bookmaker_id"), (CONTEXT, "issued_at"),
                       (report, "outcomes"), (report.outcomes[0], "status"), (report.outcomes[0].qualified, "decimal_odds")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, None)
    assert isinstance(report.outcomes, tuple) and isinstance(report.limitations, tuple)


def test_output_schema_has_no_value_recommendation_or_certification_fields():
    names = {f.name for cls in (EligibilityReport, QuoteEligibility, QualifiedQuote) for f in dataclasses.fields(cls)}
    banned = ("ev", "expected", "fair", "stake", "kelly", "recommend", "pick", "rank", "edge", "clv", "roi", "profit", "bet", "true_pit")
    assert not [n for n in names if any(b == part for part in n.split("_") for b in banned)]
    assert PIT_STATUS == "NOT_CERTIFIED" and "NOT_TRUE_PIT_CERTIFIED" in LIMITATIONS


def test_module_imports_no_app_database_provider_or_value_math():
    code = ("import sys; import research.quote_temporal_eligibility; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'requests', 'httpx')"
            " or m.startswith('research.') and m != 'research.quote_temporal_eligibility'))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]"
