"""Consumidor de investigación V1 de goles FT90 (109A + 109B). Sin BD, red ni app.
    python -m pytest research/tests/test_goals_consumer_v1.py -q   (desde backend/)
Partidos, marcadores, instantes y probabilidades SINTÉTICOS: ni histórico real, ni procedencia acreditada, ni calibración."""

import copy
import hashlib
import json
import random
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from research.goals_calibration_uncertainty import UncertaintyConfig, canonical_report_json, goals_reliability_uncertainty
from research.goals_calibration_uncertainty import ConfigError as ReliabilityConfigError
from research.goals_consumer_v1 import (ALL_ABSTAINED, CALIBRATION_CLAIM, CONSUMER_VERSION_MISMATCH, CONTRACT_VERSION, DUPLICATE_REPORT,
                                        EVIDENCE_DIGEST_MISMATCH, EVIDENCE_NOT_AUDITABLE, FIXTURE_ID_LIMIT, LINE_MISMATCH, MARKET_MISMATCH,
                                        NO_HOLDOUT_ROWS, NO_SETTLED_OUTCOMES, OVERLAPPING_FIXTURES, PRODUCER_MISMATCH,
                                        PRODUCER_VERSION_MISMATCH, REJECTED, RELIABILITY_CONFIG_MISMATCH, RESULT_NO_ESTIMATE,
                                        RESULT_NON_COMPARABLE, RESULT_SCORED, SOURCE_IDENTITY_MISMATCH, SOURCE_VERSION_MISMATCH, VERIFIED,
                                        ConsumerConfigError, ConsumerDuplicateFixtureError, ConsumerIdentityError, ConsumerInputError,
                                        ConsumerObservationError, ConsumerProbabilityError, ConsumerSourceError, ConsumerTargetError,
                                        ConsumerTemporalError, GoalsConsumerError, aggregate_goals_consumer_reports,
                                        run_goals_research_consumer)
from research.goals_consumer_v1.contract import MESSAGE_LIMIT
from research.goals_oos_evaluation import ConfigError as OOSConfigError
from research.goals_oos_evaluation import LeakageError, TemporalError, evaluate_goals_oos
from research.m59.canonical import sha256_hex

UTC = timezone.utc
BTTS, OU = "BTTS_FT90", "OVER_UNDER_FT90"
O1, O2 = datetime(2025, 3, 1, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC)
HOLDOUT, CALIBRATION, EMBARGO = timedelta(days=28), timedelta(days=14), timedelta(days=3)
RELIABILITY = UncertaintyConfig(seed=7, iterations=20, min_weeks=2)
PROBS = {BTTS: {"YES": 0.55, "NO": 0.45}, "half": {"OVER": 0.52, "UNDER": 0.48}, "integer": {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.25}}


def day(month, d, hour=18):
    return datetime(2025, month, d, hour, tzinfo=UTC)


def obs(fixture_id, kickoff, *, market=OU, line="2.0", status="ESTIMATE", probs="default", score=(1, 1), status_short="FT",
        observed="default", cutoff=None, feature=None):
    cutoff = cutoff or kickoff - timedelta(hours=1)
    if probs == "default":
        probs = None if status == "NO_ESTIMATE" else copy.deepcopy(
            PROBS[BTTS] if market == BTTS else PROBS["integer" if line.endswith(".0") else "half"])
    return {"fixture_id": fixture_id, "kickoff_at": kickoff, "cutoff_at": cutoff, "feature_observed_at": feature or cutoff,
            "market": market, "line": line, "status": status, "probabilities": probs,
            "outcome": {"status_short": status_short, "fulltime_home": score[0], "fulltime_away": score[1],
                        "observed_at": kickoff + timedelta(hours=2) if observed == "default" else observed}}


def config(**over):
    base = {"contract_version": CONTRACT_VERSION, "producer_id": "SYNTHETIC_PRODUCER", "producer_version": "1.0", "market": OU,
            "line": "2.0", "origins": (O1,), "holdout_length": HOLDOUT, "calibration_length": CALIBRATION, "embargo": EMBARGO,
            "reliability": RELIABILITY}
    return {**base, **over}


def run(rows, **over):
    return run_goals_research_consumer(rows, config(**over))


def oos_config(cfg):
    return {"model_id": f"{cfg['producer_id']}@{cfg['producer_version']}", "origins": cfg["origins"], "holdout_length": cfg["holdout_length"],
            "calibration_length": cfg["calibration_length"], "embargo": cfg["embargo"]}


def consumer_digest(body):
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def redigest(report):
    body = {k: v for k, v in report.items() if k != "report_digest"}
    return {**body, "report_digest": consumer_digest(body)}


def b_rows(report):
    t = report["target"]
    return [{"sample_id": e["sample_id"], "fixture_id": e["fixture_id"], "week": e["week"], "market": t["market"], "line": t["line"],
             "estimate_status": e["estimate_status"], "probabilities": e["probabilities"], "label": e["label"]} for e in report["ledger"]]


# PRIMARY_GOLDEN: la fixture literal de PRIMARY. O/U 2.0, cinco filas, todas liquidadas; tres ESTIMATE y dos NO_ESTIMATE.
PRIMARY_GOLDEN = [obs(1, day(3, 2), score=(3, 0)),                          # f1 ESTIMATE     3-0 -> OVER
                  obs(2, day(3, 9), score=(2, 0)),                          # f2 ESTIMATE     2-0 -> PUSH
                  obs(3, day(3, 16), score=(1, 0)),                         # f3 ESTIMATE     1-0 -> UNDER
                  obs(4, day(3, 20), status="NO_ESTIMATE", score=(0, 0)),   # f4 NO_ESTIMATE  0-0 -> UNDER
                  obs(5, day(3, 25), status="NO_ESTIMATE", score=(2, 0))]   # f5 NO_ESTIMATE  2-0 -> PUSH
TRAIN_ONLY = obs(6, day(1, 10))                                             # anterior a O1: fuera de todo holdout

# ALTERNATIVE_SCENARIO (NO es PRIMARY_GOLDEN): la fixture comprometida antes, con f4 = 3-0 (OVER). Solo regresión aparte.
ALTERNATIVE_SCENARIO = [obs(1, day(3, 2), score=(2, 1)), obs(2, day(3, 9), score=(1, 0)), obs(3, day(3, 16), score=(1, 1)),
                        obs(4, day(3, 20), status="NO_ESTIMATE", score=(3, 0)), obs(5, day(3, 25), status="NO_ESTIMATE", score=(2, 0))]
OU_CLASSES = ("OVER", "UNDER", "PUSH")


def class_counts(ledger, status=None):
    """Cuentas por clase recontadas fila a fila del libro mayor (todas las liquidadas, o solo un estado de estimación)."""
    return {c: sum(1 for e in ledger if e["label"] == c and status in (None, e["estimate_status"])) for c in OU_CLASSES}


# ------------------------------------------------------------------------------------------- 1. escenario de PRIMARY

def test_01_primary_golden_five_rows_settled_scored_and_abstained_counts():
    report = run(PRIMARY_GOLDEN)
    ledger = report["ledger"]
    assert [(e["fixture_id"], e["estimate_status"], e["label"], e["label_missing_reason"]) for e in ledger] == [
        (1, "ESTIMATE", "OVER", None), (2, "ESTIMATE", "PUSH", None), (3, "ESTIMATE", "UNDER", None),
        (4, "NO_ESTIMATE", "UNDER", None), (5, "NO_ESTIMATE", "PUSH", None)]
    pooled = report["pooled"]["denominators"]
    assert (pooled["input_total"], pooled["settled_total"], pooled["scored_settled"], pooled["abstained_settled"],
            pooled["no_estimate_input"]) == (5, 5, 3, 2, 2)
    assert pooled["settled_by_class"] == {"OVER": 1, "UNDER": 2, "PUSH": 2}
    assert pooled["scored_by_class"] == {"OVER": 1, "UNDER": 1, "PUSH": 1}
    assert pooled["abstained_by_class"] == {"OVER": 0, "UNDER": 1, "PUSH": 1}
    assert pooled == {
        "input_total": 5, "estimate_input": 3, "no_estimate_input": 2, "label_missing_input": 0, "label_missing_estimate": 0,
        "label_missing_no_estimate": 0, "settled_total": 5, "scored_settled": 3, "abstained_settled": 2,
        "settled_by_class": {"OVER": 1, "UNDER": 2, "PUSH": 2}, "scored_by_class": {"OVER": 1, "UNDER": 1, "PUSH": 1},
        "abstained_by_class": {"OVER": 0, "UNDER": 1, "PUSH": 1}, "label_missing_by_reason": {}}
    assert report["pooled"]["result"] == {"status": RESULT_SCORED, "reasons": []}
    assert report["counts"] == {"input_rows": 5, "holdout_rows": 5, "outside_holdout_rows": 0, "folds": 1}

    # libro mayor, pliegue, conciliación y agregado dicen lo mismo
    assert (class_counts(ledger), class_counts(ledger, "ESTIMATE"), class_counts(ledger, "NO_ESTIMATE")) == (
        pooled["settled_by_class"], pooled["scored_by_class"], pooled["abstained_by_class"])
    assert all(pooled["settled_by_class"][c] == pooled["scored_by_class"][c] + pooled["abstained_by_class"][c] for c in OU_CLASSES)
    assert report["fold_groups"][0]["denominators"] == pooled
    agg = aggregate_goals_consumer_reports([report])
    assert agg["result"] == {"status": RESULT_SCORED, "reasons": []} and agg["denominators"] == pooled and agg["fixtures"] == 5
    assert agg["reliability_report"]["outcome_counts"] == pooled["scored_by_class"]

    # 109A cuenta TODAS las liquidadas (NO_ESTIMATE incluidas); 109B solo las puntuadas: no son equivalentes
    (group,) = report["oos_report"]["folds"][0]["groups"]
    assert (group["market"], group["line"]) == (OU, "2.0")
    assert group["outcome_counts"] == pooled["settled_by_class"] == {"OVER": 1, "UNDER": 2, "PUSH": 2}
    assert (group["holdout_rows"], group["holdout_estimate"], group["holdout_no_estimate"], group["holdout_settled"]) == (5, 3, 2, 5)
    assert (group["coverage"]["settled"], group["coverage"]["model_scored"], group["coverage"]["abstained_settled"]) == (5, 3, 2)
    assert group["model"]["scored"] == 3
    for b in (report["reliability_reports"]["pooled"], report["reliability_reports"]["folds"][0]):
        assert b["outcome_counts"] == pooled["scored_by_class"] == {"OVER": 1, "UNDER": 1, "PUSH": 1}
        coverage = b["coverage"]
        assert (coverage["total_input"], coverage["scored"], coverage["no_estimate"], coverage["label_missing"]) == (5, 3, 2, 0)
    # cada fuente ejecutada por sí sola sobre las filas literales da las mismas cuentas
    assert evaluate_goals_oos(copy.deepcopy(PRIMARY_GOLDEN), oos_config(config())) == report["oos_report"]
    standalone = goals_reliability_uncertainty(b_rows(report), market=OU, line="2.0", config=RELIABILITY)
    assert dict(standalone.outcome_counts) == {"OVER": 1, "UNDER": 1, "PUSH": 1}
    assert report["fold_groups"][0]["oos"]["metric_basis"] == "THREE_CLASS_OVER_UNDER_PUSH"
    semantics = report["compatibility"]["count_semantics"]
    assert semantics["oos_report.folds[].groups[].outcome_counts"] == "ALL_SETTLED_ESTIMATE_AND_NO_ESTIMATE"
    assert semantics["reliability_reports.outcome_counts"] == "SETTLED_ESTIMATE_ONLY"

    # una fila de entrenamiento fuera de todo holdout no cambia ni el libro mayor ni los denominadores
    with_train = run(PRIMARY_GOLDEN + [TRAIN_ONLY])
    assert with_train["counts"] == {"input_rows": 6, "holdout_rows": 5, "outside_holdout_rows": 1, "folds": 1}
    assert with_train["ledger"] == ledger and with_train["pooled"]["denominators"] == pooled


def test_01c_alternative_scenario_former_fixture_is_a_separate_regression():
    """ALTERNATIVE_SCENARIO, nunca PRIMARY_GOLDEN: con f4 = 3-0 la abstención liquidada es OVER, no UNDER."""
    report = run(ALTERNATIVE_SCENARIO)
    den = report["pooled"]["denominators"]
    assert (den["input_total"], den["settled_total"], den["scored_settled"], den["abstained_settled"],
            den["no_estimate_input"]) == (5, 5, 3, 2, 2)
    assert den["settled_by_class"] == {"OVER": 2, "UNDER": 1, "PUSH": 2}
    assert den["scored_by_class"] == {"OVER": 1, "UNDER": 1, "PUSH": 1}
    assert den["abstained_by_class"] == {"OVER": 1, "UNDER": 0, "PUSH": 1}
    (group,) = report["oos_report"]["folds"][0]["groups"]
    assert group["outcome_counts"] == den["settled_by_class"]
    assert report["reliability_reports"]["pooled"]["outcome_counts"] == den["scored_by_class"]
    assert den["settled_by_class"] != run(PRIMARY_GOLDEN)["pooled"]["denominators"]["settled_by_class"]


def test_01b_report_is_json_round_trippable_and_digest_recomputes():
    report = run(PRIMARY_GOLDEN)
    assert json.loads(json.dumps(report)) == report
    assert redigest(report)["report_digest"] == report["report_digest"]
    assert report["calibration_claim"] == CALIBRATION_CLAIM and "NOT_CERTIFIED" in report["classifications"]
    assert report["producer"] == {"producer_id": "SYNTHETIC_PRODUCER", "producer_version": "1.0", "model_id": "SYNTHETIC_PRODUCER@1.0"}
    assert report["consumer_version"] == "1" and report["contract_version"] == CONTRACT_VERSION


# ---------------------------------------------------------------------------------------------- 2. etiquetas faltantes

def test_02_missing_labels_are_counted_by_reason_and_never_scored():
    rows = [obs(1, day(3, 2), score=(2, 1)),
            obs(2, day(3, 3), observed=None),                                     # ESTIMATE sin evidencia del resultado
            obs(3, day(3, 4), status_short="NS", score=(None, None)),            # ESTIMATE no terminado
            obs(4, day(3, 5), status="NO_ESTIMATE", score=(1, None)),            # NO_ESTIMATE incompleto
            obs(5, day(3, 6), score=(-1, 2))]                                     # ESTIMATE marcador inválido
    den = run(rows)["pooled"]["denominators"]
    assert (den["input_total"], den["settled_total"], den["label_missing_input"]) == (5, 1, 4)
    assert (den["label_missing_estimate"], den["label_missing_no_estimate"]) == (3, 1)
    assert den["label_missing_by_reason"] == {"fulltime_incomplete": 1, "fulltime_invalid": 1, "outcome_observed_at_missing": 1,
                                              "status_not_finished": 1}
    report = run(rows)
    coverage = report["reliability_reports"]["pooled"]["coverage"]
    assert (coverage["label_missing"], coverage["no_estimate"], coverage["scored"]) == (3, 1, 1)  # 109B: solo ESTIMATE sin etiqueta
    labels = {e["fixture_id"]: (e["label"], e["label_missing_reason"]) for e in report["ledger"]}
    assert labels[2] == (None, "outcome_observed_at_missing") and labels[1] == ("OVER", None)


# ------------------------------------------------------------------------------------- 3. cohortes sin estimación

def test_03_all_abstain_cohort_is_an_explicit_no_estimate():
    rows = [obs(i, day(3, i + 1), status="NO_ESTIMATE", score=(i, 0)) for i in range(1, 5)]
    report = run(rows)
    assert report["pooled"]["result"] == {"status": RESULT_NO_ESTIMATE, "reasons": [ALL_ABSTAINED]}
    den = report["pooled"]["denominators"]
    assert (den["settled_total"], den["scored_settled"], den["abstained_settled"]) == (4, 0, 4)
    assert report["fold_groups"][0]["oos"]["group_status"] == "ALL_ABSTAINED"
    assert report["pooled"]["reliability"]["ece"] is None
    assert report["pooled"]["reliability"]["uncertainty_status"] == "NO_SCORED_SAMPLES"


def test_03b_no_holdout_rows_and_no_settled_outcomes():
    empty = run([obs(1, day(1, 10))])  # solo train
    assert empty["pooled"]["result"] == {"status": RESULT_NO_ESTIMATE, "reasons": [NO_HOLDOUT_ROWS]}
    assert empty["ledger"] == [] and empty["pooled"]["denominators"]["input_total"] == 0
    nothing = run([])
    assert nothing["pooled"]["result"]["reasons"] == [NO_HOLDOUT_ROWS] and nothing["fold_groups"][0]["oos"] is None
    unsettled = run([obs(1, day(3, 2), observed=None), obs(2, day(3, 3), status_short="CANC", score=(None, None))])
    assert unsettled["pooled"]["result"] == {"status": RESULT_NO_ESTIMATE, "reasons": [NO_SETTLED_OUTCOMES]}


# -------------------------------------------------------------------------------------------- 4-6. mercados y PUSH

def test_04_btts_binary():
    rows = [obs(1, day(3, 2), market=BTTS, line=None, score=(1, 1)), obs(2, day(3, 9), market=BTTS, line=None, score=(1, 0)),
            obs(3, day(3, 16), market=BTTS, line=None, status="NO_ESTIMATE", score=(2, 2))]
    report = run(rows, market=BTTS, line=None)
    den = report["pooled"]["denominators"]
    assert den["settled_by_class"] == {"YES": 2, "NO": 1} and den["scored_by_class"] == {"YES": 1, "NO": 1}
    assert report["target"]["classes"] == ["YES", "NO"] and report["target"]["line"] is None
    assert report["pooled"]["reliability"]["metric_basis"] == "BINARY_EVENT"
    assert report["fold_groups"][0]["oos"]["metric_basis"] == "BINARY_TWO_COMPONENT"


def test_05_half_line_over_under_has_no_push():
    rows = [obs(1, day(3, 2), line="2.5", score=(2, 1)), obs(2, day(3, 9), line="2.5", score=(1, 1))]
    report = run(rows, line="2.5")
    assert report["pooled"]["denominators"]["settled_by_class"] == {"OVER": 1, "UNDER": 1}
    assert report["target"]["line_type"] == "HALF"
    with pytest.raises(ConsumerProbabilityError):
        run([obs(1, day(3, 2), line="2.5", probs={"OVER": 0.5, "UNDER": 0.25, "PUSH": 0.25})], line="2.5")


def test_06_integer_line_push_is_settled_and_void_is_label_missing():
    rows = [obs(1, day(3, 2), score=(2, 0)),                                                # PUSH liquidado
            obs(2, day(3, 3), status_short="AET", score=(1, 1)),                            # PUSH con el marcador a 90'
            obs(3, day(3, 4), status_short="CANC", score=(None, None)),                     # "void": no terminado
            obs(4, day(3, 5), status_short="ABD", score=(1, 1))]                            # "void" con marcador parcial
    report = run(rows)
    den = report["pooled"]["denominators"]
    assert den["settled_by_class"] == {"OVER": 0, "UNDER": 0, "PUSH": 2} and den["scored_by_class"]["PUSH"] == 2
    assert den["label_missing_by_reason"] == {"status_not_finished": 2}
    assert report["reliability_reports"]["pooled"]["all_push"] is True
    labels = [(e["label"], e["label_missing_reason"]) for e in report["ledger"]]
    assert labels == [("PUSH", None), ("PUSH", None), (None, "status_not_finished"), (None, "status_not_finished")]


# ------------------------------------------------------------------------------------------- 7. objetivos no admitidos

@pytest.mark.parametrize("market, line", [("1X2_FT90", None), (OU, "2.25"), (OU, "2.75"), (OU, "2.50"), (OU, 2.5), (OU, Decimal("2.5")),
                                          (OU, None), (BTTS, "2.5"), ("TOTAL_GOALS", "2.5"), ("btts_ft90", None)])
def test_07_unsupported_targets_are_rejected(market, line):
    with pytest.raises(ConsumerTargetError):
        run([], market=market, line=line)


def test_07b_unsupported_observation_market_or_other_target_rejected():
    with pytest.raises(ConsumerTargetError, match="1X2"):
        run([obs(1, day(3, 2), market="1X2_FT90", line=None, probs={"H": 0.5, "D": 0.25, "A": 0.25})])
    with pytest.raises(ConsumerTargetError):
        run([obs(1, day(3, 2), line="2.25", probs={"OVER": 0.5, "UNDER": 0.5})])
    with pytest.raises(ConsumerObservationError, match="no es el objetivo"):
        run([obs(1, day(3, 2), line="2.5")])


# ---------------------------------------------------------------------------- 8-10. tipos exactos y objetos hostiles

class BenignFloat(float):
    pass


def test_08_benign_float_subclass_rejected_by_consumer_boundary():
    probs = {"OVER": BenignFloat(0.4), "UNDER": 0.35, "PUSH": 0.25}
    with pytest.raises(ConsumerProbabilityError, match="BenignFloat"):
        run([obs(1, day(3, 2), probs=probs)])
    # 109A por sí solo la admitiría: el rechazo es una regla de la frontera compartida, no de la fuente
    evaluate_goals_oos([obs(1, day(3, 2), probs=probs)], oos_config(config()))


@pytest.mark.parametrize("mutate", [
    lambda o: o.update(fixture_id=type("I", (int,), {})(5)),
    lambda o: o.update(market=type("S", (str,), {})(OU)),
    lambda o: o.update(status=type("S", (str,), {})("ESTIMATE")),
    lambda o: o.update(kickoff_at=type("D", (datetime,), {})(2025, 3, 2, 18, tzinfo=UTC)),
    lambda o: o.update(probabilities=type("M", (dict,), {})(PROBS["integer"])),
    lambda o: o["outcome"].update(status_short=type("S", (str,), {})("FT")),
])
def test_08b_other_subclasses_rejected(mutate):
    row = obs(1, day(3, 2))
    mutate(row)
    with pytest.raises(ConsumerInputError):
        run([row])
    with pytest.raises(ConsumerObservationError):
        run(type("L", (list,), {})([obs(1, day(3, 2))]))
    with pytest.raises(ConsumerObservationError):
        run([type("M", (dict,), {})(obs(1, day(3, 2)))])


CALLS = []


class HostileMeta(type):
    @property
    def __name__(cls):
        CALLS.append("metaclass __name__")
        raise RuntimeError("hostil")


class Hostile(metaclass=HostileMeta):
    def _hit(self, *args):
        CALLS.append("dunder")
        raise RuntimeError("hostil")

    __repr__ = __str__ = __format__ = __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _hit
    __bool__ = __float__ = __int__ = __index__ = __len__ = __iter__ = _hit


class HostileKey:
    def __hash__(self):
        return 0

    def _hit(self, *args):
        CALLS.append("key dunder")
        raise RuntimeError("hostil")

    __repr__ = __str__ = __format__ = __eq__ = __lt__ = _hit


def _hostile_cases():
    h = Hostile()
    yield "obs.fixture_id", lambda o, c: o.update(fixture_id=h)
    yield "obs.market", lambda o, c: o.update(market=h)
    yield "obs.line", lambda o, c: o.update(line=h)
    yield "obs.status", lambda o, c: o.update(status=h)
    yield "obs.kickoff_at", lambda o, c: o.update(kickoff_at=h)
    yield "obs.probabilities", lambda o, c: o.update(probabilities=h)
    yield "obs.probability", lambda o, c: o["probabilities"].update(OVER=h)
    yield "obs.probability_key", lambda o, c: o["probabilities"].update({HostileKey(): 0.0})
    yield "obs.extra_key", lambda o, c: o.update({HostileKey(): 1})
    yield "obs.outcome", lambda o, c: o.update(outcome=h)
    yield "obs.status_short", lambda o, c: o["outcome"].update(status_short=h)
    yield "obs.fulltime_home", lambda o, c: o["outcome"].update(fulltime_home=h)
    yield "obs.observed_at", lambda o, c: o["outcome"].update(observed_at=h)
    yield "cfg.contract_version", lambda o, c: c.update(contract_version=h)
    yield "cfg.producer_id", lambda o, c: c.update(producer_id=h)
    yield "cfg.market", lambda o, c: c.update(market=h)
    yield "cfg.origins", lambda o, c: c.update(origins=h)
    yield "cfg.origin", lambda o, c: c.update(origins=(h,))
    yield "cfg.holdout_length", lambda o, c: c.update(holdout_length=h)
    yield "cfg.reliability", lambda o, c: c.update(reliability=h)
    yield "cfg.extra_key", lambda o, c: c.update({HostileKey(): 1})


@pytest.mark.parametrize("name, mutate", list(_hostile_cases()), ids=[n for n, _ in _hostile_cases()])
def test_09_hostile_objects_never_execute_and_messages_are_bounded(name, mutate):
    row, cfg = obs(1, day(3, 2)), config()
    mutate(row, cfg)
    CALLS.clear()
    with pytest.raises(ConsumerInputError) as info:
        run_goals_research_consumer([row], cfg)
    assert CALLS == []
    assert len(str(info.value)) <= MESSAGE_LIMIT + 3


def test_09b_hostile_collection_and_long_texts_are_bounded():
    CALLS.clear()
    with pytest.raises(ConsumerObservationError):
        run_goals_research_consumer(Hostile(), config())
    with pytest.raises(ConsumerObservationError):
        run_goals_research_consumer([Hostile()], config())
    with pytest.raises(ConsumerConfigError):
        run_goals_research_consumer([], Hostile())
    assert CALLS == []
    with pytest.raises(ConsumerTargetError) as info:
        run([], market="X" * 100_000)
    assert len(str(info.value)) <= MESSAGE_LIMIT + 3


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), float("-inf"), -0.1, 1.5, 10 ** 400, "0.4", None, Decimal("0.4")])
def test_10_bool_nonfinite_and_out_of_range_probabilities_rejected(value):
    with pytest.raises(ConsumerProbabilityError):
        run([obs(1, day(3, 2), probs={"OVER": value, "UNDER": 0.35, "PUSH": 0.25})])


@pytest.mark.parametrize("field, value", [("fixture_id", True), ("fixture_id", 5.0), ("fixture_id", "5")])
def test_10b_bool_and_non_int_identities_rejected(field, value):
    with pytest.raises(ConsumerIdentityError):
        run([dict(obs(1, day(3, 2)), **{field: value})])


@pytest.mark.parametrize("goals", [True, 1.0, 10 ** 6, -(10 ** 6), float("nan")])
def test_10c_goals_must_be_bounded_exact_ints(goals):
    with pytest.raises(ConsumerObservationError):
        run([obs(1, day(3, 2), score=(goals, 0))])


# ---------------------------------------------------------------------------------- 11. símplex y clases ausentes

@pytest.mark.parametrize("line, probs", [
    ("2.5", {"OVER": 0.5, "UNDER": 0.4}),
    ("2.5", {"OVER": 0.6, "UNDER": 0.6}),
    ("2.0", {"OVER": 0.5, "UNDER": 0.5}),
    ("2.0", {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.2}),
    ("2.0", {"OVER": 0.4, "UNDER": 0.35, "PUSH": 0.25, "VOID": 0.0}),
    ("2.0", {"over": 0.4, "UNDER": 0.35, "PUSH": 0.25}),
    ("2.0", {}),
])
def test_11_invalid_sums_and_missing_or_extra_classes(line, probs):
    with pytest.raises(ConsumerProbabilityError):
        run([obs(1, day(3, 2), line=line, probs=probs)], line=line)


def test_11b_estimate_needs_probabilities_and_abstention_carries_none():
    with pytest.raises(ConsumerProbabilityError, match="sin probabilidades"):
        run([obs(1, day(3, 2), probs=None)])
    with pytest.raises(ConsumerProbabilityError, match="abstención"):
        run([obs(1, day(3, 2), status="NO_ESTIMATE", probs=PROBS["integer"])])
    # tolerancia publicada 1e-9: 0.1 + 0.2 + 0.7 no suma exactamente 1 en double y se admite sin renormalizar
    report = run([obs(1, day(3, 2), probs={"OVER": 0.1, "UNDER": 0.2, "PUSH": 0.7})])
    assert report["ledger"][0]["probabilities"] == {"OVER": 0.1, "UNDER": 0.2, "PUSH": 0.7}


# ------------------------------------------------------------------------------------------ 12. identidad duplicada

def test_12_duplicate_fixture_identities_rejected_even_if_identical():
    row = obs(1, day(3, 2))
    with pytest.raises(ConsumerDuplicateFixtureError, match="1 valor"):
        run([row, copy.deepcopy(row)])
    with pytest.raises(ConsumerDuplicateFixtureError):
        run([obs(1, day(3, 2)), obs(1, day(3, 9), status="NO_ESTIMATE")])


# --------------------------------------------------------------------------- 13-14, 19. agregación y no comparables

def second_cohort(fixture_ids=(11, 12, 13), *, line="2.0", market=OU, **over):
    scores = [(3, 1), (0, 0), (1, 1)]
    rows = [obs(fid, day(4, 2 + 7 * i), market=market, line=line, score=scores[i % 3]) for i, fid in enumerate(fixture_ids)]
    return run(rows, origins=(O2,), market=market, line=line, **over)


def test_13_overlapping_holdout_configuration_is_a_preserved_source_error():
    with pytest.raises(ConsumerSourceError) as info:
        run(PRIMARY_GOLDEN, origins=(O1, O1 + timedelta(days=10)))
    err = info.value
    assert isinstance(err.source_error, OOSConfigError) and err.__cause__ is err.source_error
    assert (err.source_id, err.source_version) == ("GOALS_OOS_WALK_FORWARD", "1")
    assert err.source_error_type == "research.goals_oos_evaluation.ConfigError"


def test_13b_overlapping_fixture_cohorts_are_non_comparable():
    first, overlapping = run(PRIMARY_GOLDEN), second_cohort((11, 3, 13))
    agg = aggregate_goals_consumer_reports([first, overlapping])
    assert agg["result"] == {"status": RESULT_NON_COMPARABLE, "reasons": [OVERLAPPING_FIXTURES]}
    assert agg["denominators"] is None and agg["reliability"] is None
    assert all(e["status"] == VERIFIED for e in agg["inputs"])
    same = aggregate_goals_consumer_reports([first, first])
    assert same["result"]["reasons"] == [DUPLICATE_REPORT, OVERLAPPING_FIXTURES]


def test_13c_disjoint_cohorts_pool_counts_and_rerun_reliability_on_the_union():
    first, second = run(PRIMARY_GOLDEN), second_cohort()
    agg = aggregate_goals_consumer_reports([first, second])
    assert agg["result"] == {"status": RESULT_SCORED, "reasons": []}
    a, b = first["pooled"]["denominators"], second["pooled"]["denominators"]
    den = agg["denominators"]
    assert den["input_total"] == a["input_total"] + b["input_total"] == 8
    assert den["settled_by_class"] == {c: a["settled_by_class"][c] + b["settled_by_class"][c] for c in ("OVER", "UNDER", "PUSH")}
    assert den["scored_by_class"] == {"OVER": 2, "UNDER": 2, "PUSH": 2}  # 1/1/1 de PRIMARY_GOLDEN + 3-1, 0-0, 1-1
    assert den["settled_by_class"] == {"OVER": 2, "UNDER": 3, "PUSH": 3}  # 1/2/2 de PRIMARY_GOLDEN + 3-1, 0-0, 1-1
    # la fiabilidad agregada es 109B sobre la unión de filas, nunca una media de ECE
    rows = sorted(b_rows(first) + b_rows(second), key=lambda r: r["fixture_id"])
    union = goals_reliability_uncertainty(rows, market=OU, line="2.0", config=RELIABILITY)
    assert agg["reliability"]["report_identity"] == union.report_identity and agg["reliability"]["ece"] == union.ece
    assert agg["fixtures"] == 8 and agg["producer"] == first["producer"]


@pytest.mark.parametrize("other, reason", [
    (lambda: second_cohort(producer_version="2.0"), PRODUCER_VERSION_MISMATCH),
    (lambda: second_cohort(producer_id="OTHER_PRODUCER"), PRODUCER_MISMATCH),
    (lambda: second_cohort(line="2.5"), LINE_MISMATCH),
    (lambda: second_cohort(market=BTTS, line=None), MARKET_MISMATCH),
    (lambda: second_cohort(reliability=UncertaintyConfig(seed=8, iterations=20, min_weeks=2)), RELIABILITY_CONFIG_MISMATCH),
])
def test_14_incompatible_producer_market_line_or_reliability_config(other, reason):
    agg = aggregate_goals_consumer_reports([run(PRIMARY_GOLDEN), other()])
    assert agg["result"] == {"status": RESULT_NON_COMPARABLE, "reasons": [reason]}
    assert agg["denominators"] is None


def _tamper_ledger_label(report):
    r = copy.deepcopy(report)
    r["ledger"][0]["label"] = "UNDER"
    return redigest(r)


def _tamper_status(report):
    r = copy.deepcopy(report)
    r["pooled"]["result"] = {"status": RESULT_SCORED, "reasons": ["X"]}
    return redigest(r)


def _tamper_oos_content(report):
    r = copy.deepcopy(report)
    r["oos_report"]["counts"]["rows"] += 1
    return redigest(r)


def _tamper_oos_version(report):
    r = copy.deepcopy(report)
    oos = r["oos_report"]
    oos["evaluator_version"] = "2"
    oos["report_digest"] = sha256_hex({k: v for k, v in oos.items() if k != "report_digest"})
    return redigest(r)


def _tamper_reliability(report):
    r = copy.deepcopy(report)
    r["reliability_reports"]["pooled"]["ece"] = 0.0
    return redigest(r)


def _drop_ledger_field(report):
    r = copy.deepcopy(report)
    del r["ledger"][0]["week"]
    return redigest(r)


def _drop_ledger_row(report):
    r = copy.deepcopy(report)
    r["ledger"].pop()
    return redigest(r)


def _consumer_version(report):
    return redigest(dict(copy.deepcopy(report), consumer_version="0"))


@pytest.mark.parametrize("tamper, reason", [
    (lambda r: dict(copy.deepcopy(r), report_digest="0" * 64), EVIDENCE_DIGEST_MISMATCH),
    (lambda r: {k: v for k, v in r.items() if k != "ledger"}, EVIDENCE_NOT_AUDITABLE),
    (_tamper_ledger_label, EVIDENCE_NOT_AUDITABLE),
    (_tamper_status, EVIDENCE_NOT_AUDITABLE),
    (_drop_ledger_field, EVIDENCE_NOT_AUDITABLE),
    (_drop_ledger_row, EVIDENCE_NOT_AUDITABLE),
    (_tamper_oos_content, SOURCE_IDENTITY_MISMATCH),
    (_tamper_reliability, SOURCE_IDENTITY_MISMATCH),
    (_tamper_oos_version, SOURCE_VERSION_MISMATCH),
    (_consumer_version, CONSUMER_VERSION_MISMATCH),
])
def test_19_non_auditable_evidence_is_non_comparable(tamper, reason):
    report = run(PRIMARY_GOLDEN)
    agg = aggregate_goals_consumer_reports([tamper(report)])
    assert agg["result"] == {"status": RESULT_NON_COMPARABLE, "reasons": [reason]}
    assert agg["inputs"][0]["status"] == REJECTED and agg["denominators"] is None and agg["reliability_report"] is None
    # un informe íntegro acompañado de uno rechazado tampoco produce un agregado
    assert aggregate_goals_consumer_reports([report, tamper(second_cohort())])["result"]["status"] == RESULT_NON_COMPARABLE


def test_19b_untrusted_structures_are_input_errors():
    report = run(PRIMARY_GOLDEN)
    CALLS.clear()
    for bad in ([], (), [Hostile()], [dict(report, extra=Hostile())], [dict(report, extra=BenignFloat(1.0))],
                [dict(report, extra=float("nan"))], [dict(report, extra=10 ** 640)], [{1: 2}], "x", Hostile()):
        with pytest.raises(ConsumerInputError):
            aggregate_goals_consumer_reports(bad)
    assert CALLS == []
    deep = []
    for _ in range(100):
        deep = [deep]
    with pytest.raises(ConsumerInputError, match="profunda"):
        aggregate_goals_consumer_reports([dict(report, extra=deep)])


def test_19c_single_verified_report_aggregates_to_its_own_pool():
    report = run(PRIMARY_GOLDEN)
    agg = aggregate_goals_consumer_reports([json.loads(json.dumps(report))])
    assert agg["inputs"][0]["status"] == VERIFIED and agg["inputs"][0]["report_digest"] == report["report_digest"]
    assert agg["denominators"] == report["pooled"]["denominators"]
    assert agg["reliability"]["report_identity"] == report["pooled"]["reliability"]["report_identity"]


# ----------------------------------------------------------------------------------------- 15. fechas y tiempo

class FixedOffset(tzinfo):
    def utcoffset(self, dt):
        return timedelta(hours=1)

    def dst(self, dt):
        return timedelta(0)


@pytest.mark.parametrize("value", [datetime(2025, 3, 2, 18), date(2025, 3, 2), "2025-03-02T18:00:00+00:00",
                                   datetime(2025, 3, 2, 18, tzinfo=FixedOffset()), 1740938400])
def test_15_invalid_instants_rejected_at_boundary(value):
    with pytest.raises(ConsumerTemporalError):
        run([dict(obs(1, day(3, 2)), kickoff_at=value)])
    with pytest.raises(ConsumerConfigError):
        run([], origins=(value,))


def test_15b_temporal_inconsistencies_are_preserved_source_errors():
    kickoff = day(3, 2)
    cases = [(dict(cutoff=kickoff), TemporalError), (dict(cutoff=kickoff + timedelta(hours=1)), TemporalError),
             (dict(feature=kickoff - timedelta(minutes=30)), LeakageError), (dict(observed=kickoff), TemporalError),
             (dict(observed=kickoff - timedelta(days=1)), TemporalError)]
    for kwargs, error in cases:
        with pytest.raises(ConsumerSourceError) as info:
            run([obs(1, kickoff, **kwargs)])
        assert type(info.value.source_error) is error
    with pytest.raises(ConsumerSourceError) as info:
        run([], origins=(O2, O1))
    assert isinstance(info.value.source_error, OOSConfigError)
    with pytest.raises(ConsumerSourceError):
        run([], holdout_length=timedelta(0))
    with pytest.raises(ConsumerConfigError):
        run([], holdout_length=28)
    # un desfase distinto de UTC es el mismo instante: misma semana, mismo informe
    plus2 = timezone(timedelta(hours=2))
    shifted = [dict(o, kickoff_at=o["kickoff_at"].astimezone(plus2)) for o in PRIMARY_GOLDEN]
    assert run(shifted)["ledger"] == run(PRIMARY_GOLDEN)["ledger"]


def test_15c_config_contract_and_identifiers():
    with pytest.raises(ConsumerConfigError, match="contract_version"):
        run([], contract_version="GOALS_RESEARCH_CONSUMER_V2")
    for bad in ("", " SYN", "SYN@1", "ñ", "x" * 129, 1):
        with pytest.raises(ConsumerConfigError):
            run([], producer_id=bad)
    with pytest.raises(ConsumerConfigError):
        run_goals_research_consumer([], {k: v for k, v in config().items() if k != "embargo"})
    with pytest.raises(ConsumerSourceError) as info:
        run(PRIMARY_GOLDEN, reliability=UncertaintyConfig(seed=7, iterations=0))
    assert isinstance(info.value.source_error, ReliabilityConfigError) and info.value.source_id == "GOALS_RELIABILITY_UNCERTAINTY"


# ------------------------------------------------------------------------------------- 16-17. enteros grandes

def test_16_fixture_id_boundary_at_10_pow_640():
    largest = FIXTURE_ID_LIMIT - 1
    report = run([obs(largest, day(3, 2)), obs(1, day(3, 9))])
    assert [e["fixture_id"] for e in report["ledger"]] == [1, largest]
    assert aggregate_goals_consumer_reports([report])["result"]["status"] == RESULT_SCORED
    for bad in (FIXTURE_ID_LIMIT, 10 ** 5000, 0, -1):
        with pytest.raises(ConsumerIdentityError):
            run([obs(bad, day(3, 2))])
    # el tope es solo del consumidor: 109B por sí solo sigue admitiendo cualquier int
    rows = [{"sample_id": "s", "fixture_id": 10 ** 5000, "week": "2025-W10", "market": OU, "line": "2.0", "estimate_status": "ESTIMATE",
             "probabilities": PROBS["integer"], "label": "PUSH"}]
    assert goals_reliability_uncertainty(rows, market=OU, line="2.0", config=RELIABILITY).coverage.scored == 1


def test_17_b_only_giant_seed_passes_through_with_int_hex_v1():
    giant = UncertaintyConfig(seed=10 ** 5000, iterations=20, min_weeks=2)
    report = run(PRIMARY_GOLDEN, reliability=giant)
    pooled = report["reliability_reports"]["pooled"]
    assert pooled["config"]["seed"] == {"INT_HEX_V1": "+" + format(10 ** 5000, "x")}
    standalone = goals_reliability_uncertainty(b_rows(report), market=OU, line="2.0", config=giant)
    assert pooled["config_identity"] == standalone.config_identity == report["sources"]["reliability"]["config_identity"]
    assert pooled["report_identity"] == standalone.report_identity
    assert aggregate_goals_consumer_reports([report])["result"]["status"] == RESULT_SCORED


# ---------------------------------------------------------------------------------------- 18. identidad de fuentes

def test_18_source_identities_and_reports_pass_through_unchanged():
    cfg = config(origins=(O1, O2))
    rows = PRIMARY_GOLDEN + [obs(21, day(4, 3), score=(0, 0)), obs(22, day(4, 10), status="NO_ESTIMATE", score=(2, 2))]
    report = run_goals_research_consumer(rows, cfg)
    oos = evaluate_goals_oos(copy.deepcopy(rows), oos_config(cfg))
    assert report["oos_report"] == oos
    assert report["sources"]["oos"] == {"source_id": "GOALS_OOS_WALK_FORWARD", "source_version": "1", "model_id": "SYNTHETIC_PRODUCER@1.0",
                                        "report_digest": oos["report_digest"], "config_digest": oos["config_digest"],
                                        "dataset_fingerprint": oos["dataset_fingerprint"]}
    standalone = goals_reliability_uncertainty(b_rows(report), market=OU, line="2.0", config=RELIABILITY)
    assert report["reliability_reports"]["pooled"] == json.loads(canonical_report_json(standalone))
    for k, fold in enumerate(report["fold_groups"]):
        part = [r for r, e in zip(b_rows(report), report["ledger"]) if e["fold_index"] == k]
        fold_b = goals_reliability_uncertainty(part, market=OU, line="2.0", config=RELIABILITY)
        assert fold["reliability"]["report_identity"] == fold_b.report_identity == report["sources"]["reliability"]["fold_report_identities"][k]
    # dos pliegues disjuntos: el agregado es la suma de cuentas
    f0, f1 = (g["denominators"] for g in report["fold_groups"])
    assert (f0["input_total"], f1["input_total"], report["pooled"]["denominators"]["input_total"]) == (5, 2, 7)
    assert report["pooled"]["fixture_disjointness"] == "VERIFIED_DISJOINT"


def test_18b_consumer_never_mutates_caller_inputs():
    rows, cfg = copy.deepcopy(PRIMARY_GOLDEN), config()
    before = (copy.deepcopy(rows), dict(cfg))
    run_goals_research_consumer(rows, cfg)
    assert (rows, cfg) == before


# --------------------------------------------------------------------------------------------- 20. determinismo

def test_20_deterministic_across_valid_permutations():
    reference = run(PRIMARY_GOLDEN)
    rng = random.Random(109)
    for _ in range(5):
        rows = [dict(reversed(list(copy.deepcopy(o).items()))) for o in PRIMARY_GOLDEN]
        rng.shuffle(rows)
        report = run(tuple(rows) if rng.random() < 0.5 else rows)
        assert report == reference and report["report_digest"] == reference["report_digest"]
    first, second = reference, second_cohort()
    third = run([obs(31, day(5, 5), score=(0, 3))], origins=(datetime(2025, 5, 1, tzinfo=UTC),))
    a = aggregate_goals_consumer_reports([first, second, third])
    b = aggregate_goals_consumer_reports((third, first, second))
    assert a == b and a["result"]["status"] == RESULT_SCORED and a["fixtures"] == 9


def test_20b_error_namespace_is_separate_and_versioned():
    assert issubclass(ConsumerSourceError, GoalsConsumerError) and not issubclass(ConsumerSourceError, ConsumerInputError)
    assert issubclass(ConsumerInputError, ValueError)
    assert not issubclass(GoalsConsumerError, (OOSConfigError, ReliabilityConfigError))
