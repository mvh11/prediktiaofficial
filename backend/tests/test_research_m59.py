"""M5.9B (local, simulado): serialización canónica, manifiesto JSON, registro append-only con
cadena de hashes, anclajes externos, emisor con reloj inyectable y contrato de evaluación.
Ninguna predicción real: partidos y features sintéticos (y un escenario en la BD de tests)."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.models import Fixture, TeamProviderMapping
from app.repositories import fixture_repository
from app.repositories import fixture_knowledge_repository as fk
from app.repositories import statistics_repository as repo
from app.schemas.fixture_knowledge import KnowledgeStatus
from app.schemas.prematch_features import FORM_METRICS, FeatureStatus, MetricSide, MetricValue, SampleStatus, TeamRecentForm, WindowMatch
from app.schemas.statistics_knowledge import KnowledgeRegime, StatisticsProvenance
from app.services.prematch_features import team_recent_form_v1
from research.m59 import anchors, evaluation
from research.m59.canonical import canonical_json, sha256_hex
from research.m59.emitter import (FIXTURE_AMBIGUOUS, FIXTURE_UNKNOWN, IDENTITY_INVALID, LATE, NO_FEATURE_SAMPLES, Emitter,
                                  FixtureFact)
from research.m59.manifest import FrozenModel, ManifestError, load, seal, validate
from research.m59.registry import Registry, RegistryError, verify
from tests.conftest import make_competition, make_evidence, make_fixture_data

UTC = timezone.utc
K = datetime(2027, 3, 6, 18, 0, tzinfo=UTC)  # partido futuro sintético
NUM = ["home_shots_total_for", "away_shots_total_for"]
FLAGS = ["home_shots_total_for_missing", "away_shots_total_for_missing"]


def _meta(kind):
    return {"manifest_version": 1, "model_kind": kind, "model_version": f"{kind}-sim", "feature_contract": "team_recent_form_v1",
            "classes": ["H", "D", "A"], "training_id": "sim", "frozen_at": "2027-01-01T00:00:00+00:00", "code_version": "test",
            "dependencies": {}, "dataset_fingerprint": "none"}


def manifests():
    b0 = seal({**_meta("B0"), "hyperparameters": {}, "params": {"probs": [0.45, 0.26, 0.29]}})
    b1 = seal({**_meta("B1"), "hyperparameters": {"alpha": 200.0}, "params": {"base": [0.45, 0.26, 0.29], "by_competition": {"1": [0.5, 0.25, 0.25]}}})
    b2 = seal({**_meta("B2"), "hyperparameters": {"C": 0.01}, "params": {
        "numeric_columns": NUM, "flag_columns": FLAGS, "competitions": [1], "imputer_medians": [10.0, 10.0],
        "scaler_mean": [10.0, 10.0], "scaler_scale": [3.0, 3.0], "model_classes": ["A", "D", "H"],
        "coef": [[-0.3, 0.3, 0.1, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0, 0.0], [0.3, -0.3, -0.1, 0.0, 0.1]], "intercept": [-0.4, -0.5, 0.1]}})
    return {"B0": b0, "B1": b1, "B2": b2}


def models():
    return {k: FrozenModel(m) for k, m in manifests().items()}


# --- Canónico y manifiesto ----------------------------------------------------------------------


def test_canonical_serialization_is_deterministic_and_strict():
    a = {"b": 1, "a": [datetime(2027, 1, 1, tzinfo=UTC), 0.1]}
    assert canonical_json(a) == canonical_json({"a": [datetime(2027, 1, 1, tzinfo=UTC), 0.1], "b": 1})
    assert sha256_hex(a) == sha256_hex(json.loads(json.dumps(a, default=str)) | {"a": ["2027-01-01T00:00:00+00:00", 0.1]})
    with pytest.raises(ValueError):
        canonical_json({"t": datetime(2027, 1, 1)})
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_manifest_round_trip_tamper_and_shape():
    m = manifests()["B2"]
    text = canonical_json(m)
    assert load(text) == json.loads(text)  # solo json.loads + validación: no ejecuta nada
    tampered = json.loads(text)
    tampered["params"]["coef"][0][0] = 0.31
    with pytest.raises(ManifestError):
        validate(tampered)  # la huella ya no cuadra
    bad = seal({**{k: v for k, v in m.items() if k != "manifest_sha256"}, "params": {**m["params"], "coef": [[0.0]]}})
    with pytest.raises(ManifestError):
        validate(bad)


def test_frozen_models_predict_valid_probabilities():
    ms = models()
    row = {"competition_id": 1, "home_shots_total_for": 14.0, "away_shots_total_for": None,
           "home_shots_total_for_missing": False, "away_shots_total_for_missing": True}
    for m in ms.values():
        p = m.predict(row)
        assert len(p) == 3 and abs(sum(p) - 1) < 1e-12 and min(p) > 0
    assert ms["B1"].predict({"competition_id": 99}) == [0.45, 0.26, 0.29]  # competición no vista -> base
    assert not ms["B2"].known_competition(99) and ms["B2"].known_competition(1)


# --- Registro -----------------------------------------------------------------------------------


def _rec(i, **kw):
    return {"kind": "prediction", "prediction_id": f"p{i}", "fixture_id": i, "emitted_at": datetime(2027, 1, 1, tzinfo=UTC), **kw}


def test_registry_chain_and_idempotency(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    a, b = reg.append(_rec(1)), reg.append(_rec(2))
    assert (a["seq"], b["seq"], b["prev_hash"]) == (0, 1, a["record_hash"])
    assert reg.append(_rec(1)) == a and len(reg.records()) == 2  # mismo contenido: no duplica
    with pytest.raises(RegistryError):
        reg.append(_rec(1, extra="otra cosa"))  # mismo id, otro contenido: no se reescribe
    assert verify(reg.records()) and reg.head() == (1, b["record_hash"])


@pytest.mark.parametrize("attack", ["modify", "delete", "reorder", "duplicate"])
def test_registry_detects_tampering(tmp_path, attack):
    reg = Registry(tmp_path / "r.jsonl")
    for i in range(4):
        reg.append(_rec(i))
    lines = reg.path.read_text(encoding="utf-8").splitlines()
    if attack == "modify":
        lines[1] = lines[1].replace('"fixture_id":1', '"fixture_id":7')
    elif attack == "delete":
        del lines[2]
    elif attack == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    else:
        lines.append(lines[3])
    reg.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(RegistryError):
        verify(reg.records())


# --- Anclajes -----------------------------------------------------------------------------------


def test_external_anchor_covers_only_earlier_records_and_rejects_local_commits(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    for i in range(3):
        reg.append(_rec(i))
    recs = reg.records()
    st = anchors.make_statement(recs[:2])
    ok = {**st, "authority": "project_thread", "external_time": K - timedelta(days=1)}
    check = lambda a: True  # noqa: E731  (la verificación de la autoridad se inyecta)
    assert anchors.anchored_before(recs[1], recs, [ok], K, check)
    assert not anchors.anchored_before(recs[2], recs, [ok], K, check)  # seq 2 no estaba anclado
    assert not anchors.anchored_before(recs[1], recs, [{**ok, "external_time": K}], K, check)  # no anterior
    with pytest.raises(anchors.AnchorError):
        anchors.verify_anchor(recs, {**ok, "authority": "git_local_commit"}, check)
    with pytest.raises(anchors.AnchorError):
        anchors.verify_anchor(recs, {**ok, "head_hash": "f" * 64}, check)
    with pytest.raises(anchors.AnchorError):
        anchors.verify_anchor(recs, ok, lambda a: False)


# --- Emisor simulado ----------------------------------------------------------------------------


def _form(team_id, fixture_id, cutoff, horizon, *, status=FeatureStatus.OK, used=4, shots=Decimal("12")):
    window = tuple(WindowMatch(1000 + i, cutoff - timedelta(days=7 * (i + 1)), "home", "FT", cutoff - timedelta(days=60),
                               SampleStatus.USED if i < used else SampleStatus.UNKNOWN_AT_T,
                               i, cutoff - timedelta(days=7 * (i + 1) - 1), cutoff - timedelta(days=7 * (i + 1) - 1), "a" * 64,
                               StatisticsProvenance.OPERATIONAL) for i in range(5)) if status is FeatureStatus.OK else ()
    metrics = tuple(MetricValue(m, s, shots if used >= 3 else None, used) for m in FORM_METRICS for s in MetricSide) if status is FeatureStatus.OK else ()
    return TeamRecentForm(team_id, fixture_id, "api-football", cutoff, horizon, KnowledgeRegime.OPERATIONAL_STRICT, status, 5, 3, 1,
                          K if status is FeatureStatus.OK else None, cutoff - timedelta(days=90) if status is FeatureStatus.OK else None,
                          window, metrics)


class World:
    """Partidos y reloj sintéticos; facts[fixture] = lista de (desde_instante, FixtureFact)."""

    def __init__(self):
        self.now = K - timedelta(hours=3)
        self.facts = {}
        self.form_status = FeatureStatus.OK
        self.used = 4
        self.form_calls = []

    def know(self, fixture_id, since, **kw):
        self.facts.setdefault(fixture_id, []).append((since, FixtureFact(status="KNOWN", known_at=since, **kw)))

    def knowledge(self, fixture_id, at):
        known = [f for since, f in self.facts.get(fixture_id, []) if since <= at]
        return known[-1] if known else FixtureFact(status="UNKNOWN_AT_T")

    def form(self, team_id, fixture_id, cutoff, horizon):
        self.form_calls.append((cutoff, horizon))
        return _form(team_id, fixture_id, cutoff, horizon, status=self.form_status, used=self.used)

    def emitter(self, registry):
        return Emitter(clock=lambda: self.now, knowledge=self.knowledge, form=self.form, models=models(), registry=registry)


@pytest.fixture
def world():
    w = World()
    w.know(1, K - timedelta(days=30), kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=1)
    return w


def test_normal_emission_respects_t_and_h(tmp_path, world):
    rec = world.emitter(Registry(tmp_path / "r.jsonl")).emit(1)
    assert rec["kind"] == "prediction" and rec["status"] == "INFORMATIVE"
    t, h, emitted = (datetime.fromisoformat(rec[k]) for k in ("T", "H", "emitted_at"))
    assert t == K - timedelta(hours=1) and h == emitted == world.now and h <= t
    assert all(c == (world.now, world.now) for c in world.form_calls)  # features con corte = horizonte = H
    assert rec["probs"]["B2"] is not None and abs(sum(rec["probs"]["B2"]) - 1) < 1e-12
    assert not ({"label", "result", "fulltime_home", "home_goals"} & set(rec))  # sin resultados en el registro


def test_late_emission_is_refused(tmp_path, world):
    world.now = K - timedelta(minutes=59)
    rec = world.emitter(Registry(tmp_path / "r.jsonl")).emit(1)
    assert (rec["kind"], rec["reason"]) == ("no_emission", LATE)


def test_unknown_or_ambiguous_fixture_is_refused_without_fallback(tmp_path, world):
    reg = Registry(tmp_path / "r.jsonl")
    world.know(2, K + timedelta(hours=1), kickoff_at=K + timedelta(days=1), home_team_id=10, away_team_id=30, competition_id=1)  # se sabrá DESPUÉS de H
    assert world.emitter(reg).emit(2)["reason"] == FIXTURE_UNKNOWN
    world.facts[3] = [(K - timedelta(days=1), FixtureFact(status="TEMPORAL_AMBIGUITY"))]
    assert world.emitter(reg).emit(3)["reason"] == FIXTURE_AMBIGUOUS


def test_invalid_identity_is_refused(tmp_path, world):
    reg = Registry(tmp_path / "r.jsonl")
    world.know(4, K - timedelta(days=2), kickoff_at=K, home_team_id=None, away_team_id=20, competition_id=1)
    assert world.emitter(reg).emit(4)["reason"] == IDENTITY_INVALID
    world.form_status = FeatureStatus.TARGET_TEAM_MISMATCH
    assert world.emitter(reg).emit(1)["reason"] == IDENTITY_INVALID


def test_degraded_emission_has_no_b2(tmp_path, world):
    world.used = 0
    rec = world.emitter(Registry(tmp_path / "r.jsonl")).emit(1)
    assert rec["status"] == "DEGRADED" and NO_FEATURE_SAMPLES in rec["degraded_reasons"]
    assert rec["probs"]["B2"] is None and rec["probs"]["B0"] and rec["probs"]["B1"]


def test_unseen_competition_is_flagged(tmp_path, world):
    world.know(5, K - timedelta(days=2), kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=77)
    rec = world.emitter(Registry(tmp_path / "r.jsonl")).emit(5)
    assert rec["unseen_competition"] and rec["probs"]["B1"] == [0.45, 0.26, 0.29]


def test_restart_and_duplicate_emission_are_idempotent(tmp_path, world):
    reg = Registry(tmp_path / "r.jsonl")
    first = world.emitter(reg).emit(1)
    world.now += timedelta(minutes=30)
    world.used = 5  # llegaron más stats: la primera emisión sigue siendo la vinculante
    again = world.emitter(Registry(tmp_path / "r.jsonl")).emit(1)  # proceso reiniciado
    assert again == first and len(reg.records()) == 1


# --- Contrato de evaluación (simulado) ----------------------------------------------------------


def _anchor_all(reg, when):
    return [{**anchors.make_statement(reg.records()), "authority": "project_thread", "external_time": when}]


def test_evaluation_classes_coverage_and_kickoff_changes(tmp_path, world):
    reg = Registry(tmp_path / "r.jsonl")
    for fid in (11, 12, 13, 14, 15):
        world.know(fid, K - timedelta(days=3), kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=1)
    for fid in (11, 12, 13, 14):
        world.emitter(reg).emit(fid)
    world.used = 0
    world.know(16, K - timedelta(days=3), kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=1)
    world.emitter(reg).emit(16)  # DEGRADED
    world.facts[17] = []
    world.emitter(reg).emit(17)  # NOT_EMITTED (desconocido)
    anchors_ = _anchor_all(reg, world.now + timedelta(minutes=5))
    world.used = 4
    world.emitter(reg).emit(15)  # emitida a tiempo, pero ningún anclaje externo la cubre
    universe = [
        {"fixture_id": 11, "competition_id": 1, "final_kickoff": K},                       # normal
        {"fixture_id": 12, "competition_id": 1, "final_kickoff": K + timedelta(days=2)},   # aplazado: válido
        {"fixture_id": 13, "competition_id": 1, "final_kickoff": K - timedelta(hours=3)},  # adelantado: fuera
        {"fixture_id": 14, "competition_id": 1, "final_kickoff": K},                       # sin etiqueta
        {"fixture_id": 15, "competition_id": 1, "final_kickoff": K},                       # sin anclaje
        {"fixture_id": 16, "competition_id": 1, "final_kickoff": K},
        {"fixture_id": 17, "competition_id": 1, "final_kickoff": K},
        {"fixture_id": 18, "competition_id": 1, "final_kickoff": K},                       # emisor caído
    ]
    labels = {11: "H", 12: "D", 13: "A", 15: "H", 16: "A", 17: "H", 18: "D"}
    classes = {e["fixture_id"]: e["class"] for e in evaluation.classify(universe, reg.records(), anchors_, labels, lambda a: True)}
    assert classes == {11: "INFORMATIVE", 12: "INFORMATIVE", 13: "KICKOFF_ADVANCED", 14: "NO_LABEL", 15: "UNANCHORED",
                       16: "DEGRADED", 17: "NOT_EMITTED", 18: "NO_RECORD"}
    cov = evaluation.coverage(evaluation.classify(universe, reg.records(), anchors_, labels, lambda a: True))
    # operativa: los 8 elegibles; emitidas 11-16 (6), informativas 11-15 (5), degradada 16, sin registro 18, no emitida 17
    op = cov["operational"]
    assert (op["denominator"], op["emitted"], op["emitted_informative"], op["emitted_degraded"], op["no_record"], op["not_emitted"]) == (8, 6, 5, 1, 1, 1)
    assert op["emission_coverage"] == pytest.approx(6 / 8) and op["informative_emission_coverage"] == pytest.approx(5 / 8)
    # evaluable: solo los 7 con etiqueta (14 no tiene); INFORMATIVE evaluables = 11 y 12
    ev = cov["evaluable"]
    assert (ev["denominator"], ev["informative"], ev["no_label_excluded"]) == (7, 2, 1) and ev["informative_coverage"] == pytest.approx(2 / 7)
    assert cov["counts"]["DEGRADED"] == 1 and cov["counts"]["NO_RECORD"] == 1 and cov["counts"]["NO_LABEL"] == 1 and cov["postponed"] == 1


def test_evaluation_refuses_a_tampered_registry_and_runs_once(tmp_path, world):
    reg = Registry(tmp_path / "r.jsonl")
    world.emitter(reg).emit(1)
    recs = reg.records()
    recs[0]["probs"]["B2"][0] = 0.99
    with pytest.raises(RegistryError):
        evaluation.classify([{"fixture_id": 1, "competition_id": 1, "final_kickoff": K}], recs, [], {1: "H"}, lambda a: True)
    ev = evaluation.ConfirmatoryEvaluation()
    eligible = {"EVALUATION_ELIGIBLE": True}
    assert ev.run([], {"EVALUATION_ELIGIBLE": False})["status"] == "REGISTRY_NOT_ELIGIBLE"  # no gasta la puerta
    assert ev.run([], eligible)["status"] == "INSUFFICIENT_VOLUME"
    with pytest.raises(RuntimeError):
        ev.run([], eligible)  # una sola evaluación confirmatoria


# --- Estado del registro: CHAIN_VALID / ANCHOR_VERIFIED / EVALUATION_ELIGIBLE --------------------

ALWAYS = lambda a: True  # noqa: E731


def _emitted(tmp_path, world, n=3):
    reg = Registry(tmp_path / "r.jsonl")
    for fid in range(21, 21 + n):
        world.know(fid, K - timedelta(days=3), kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=1)
        world.emitter(reg).emit(fid)
    return reg


def _status(recs, anchors_):
    s = evaluation.registry_status(recs, anchors_, ALWAYS)
    return s["CHAIN_VALID"], s["ANCHOR_VERIFIED"], s["EVALUATION_ELIGIBLE"]


def test_intact_chain_without_anchor_is_not_eligible(tmp_path, world):
    recs = _emitted(tmp_path, world).records()
    assert _status(recs, []) == (True, False, False)


def test_truncated_chain_is_detected_by_its_anchor(tmp_path, world):
    reg = _emitted(tmp_path, world)
    recs = reg.records()
    anchor = {**anchors.make_statement(recs), "authority": "project_thread", "external_time": world.now}
    truncated = recs[:-1]  # se pierde el último registro: el prefijo sigue siendo una cadena válida
    assert evaluation.registry_status(truncated, [], ALWAYS)["CHAIN_VALID"]
    assert _status(truncated, [anchor]) == (True, False, False)  # el anclaje apunta a un seq que ya no existe


def test_invalid_anchor_makes_the_registry_ineligible(tmp_path, world):
    recs = _emitted(tmp_path, world).records()
    good = {**anchors.make_statement(recs), "authority": "project_thread", "external_time": world.now}
    assert _status(recs, [good, {**good, "head_hash": "e" * 64}]) == (True, False, False)
    assert _status(recs, [{**good, "authority": "git_local_commit"}]) == (True, False, False)
    assert evaluation.registry_status(recs, [good], lambda a: False)["EVALUATION_ELIGIBLE"] is False
    broken = [dict(r) for r in recs]
    broken[1]["fixture_id"] = 999
    assert _status(broken, [good]) == (False, False, False)


def test_anchor_after_kickoff_leaves_predictions_unanchored(tmp_path, world):
    recs = _emitted(tmp_path, world, n=1).records()
    late = {**anchors.make_statement(recs), "authority": "project_thread", "external_time": K + timedelta(minutes=1)}
    assert _status(recs, [late]) == (True, True, True)  # el registro es íntegro y está anclado…
    classes = evaluation.classify([{"fixture_id": 21, "competition_id": 1, "final_kickoff": K}], recs, [late], {21: "H"}, ALWAYS)
    assert classes[0]["class"] == "UNANCHORED"  # …pero el anclaje llegó después del kickoff


def test_valid_anchor_before_kickoff_makes_predictions_evaluable(tmp_path, world):
    recs = _emitted(tmp_path, world, n=2).records()
    anchor = {**anchors.make_statement(recs), "authority": "rfc3161", "external_time": K - timedelta(minutes=50)}
    assert _status(recs, [anchor]) == (True, True, True)
    universe = [{"fixture_id": f, "competition_id": 1, "final_kickoff": K} for f in (21, 22)]
    assert [e["class"] for e in evaluation.classify(universe, recs, [anchor], {21: "H", 22: "A"}, ALWAYS)] == ["INFORMATIVE", "INFORMATIVE"]


# --- Emisor contra la BD de tests (conocimiento y features reales) -------------------------------

PROVIDER = "api-football"
EARLY = datetime(2027, 1, 1, tzinfo=UTC)


@pytest.fixture
def scene(db_session):
    _, sid = make_competition(db_session, 39, name="Liga")
    data = [make_fixture_data(9500, home=63, away=70, status="NS", kickoff_at=K),
            make_fixture_data(9501, home=63, away=71, status="FT", kickoff_at=K - timedelta(days=4), home_goals=1, away_goals=0, fulltime_home=1, fulltime_away=0)]
    teams = fixture_repository.ensure_teams(db_session, [t for d in data for t in (d.home_team, d.away_team)], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, sid, data, teams, PROVIDER, make_evidence(EARLY, provider=PROVIDER))
    db_session.execute(update(TeamProviderMapping).values(created_at=EARLY))
    ids = {int(e): i for e, i in db_session.execute(select(Fixture.external_id, Fixture.id))}
    return ids, sid, teams


def test_emitter_with_real_knowledge_never_uses_evidence_after_h(tmp_path, db_session, scene):
    ids, sid, teams = scene
    emit_at = K - timedelta(hours=2)
    stats = [{"team": {"id": 63}, "statistics": [{"type": "Total Shots", "value": 9}]}, {"team": {"id": 71}, "statistics": [{"type": "Total Shots", "value": 4}]}]
    repo.record_observation(db_session, fixture_id=ids[9501], provider=PROVIDER, provider_fixture_id="9501", payload=stats, source="live",
                            availability="available", teams_returned=2, observed_at=emit_at + timedelta(minutes=10), available_at=emit_at + timedelta(minutes=10))
    fixture_repository.upsert_fixtures(db_session, sid, [make_fixture_data(9500, home=63, away=70, status="NS", kickoff_at=K - timedelta(hours=1, minutes=30))],
                                       teams, PROVIDER, make_evidence(emit_at + timedelta(minutes=20), provider=PROVIDER))  # kickoff adelantado, conocido después

    def knowledge(fixture_id, at):
        k = fk.strict_knowledge_at(db_session, fixture_id, at)
        if k.status is not KnowledgeStatus.KNOWN:
            return FixtureFact(status=k.status.value)
        s = k.state
        return FixtureFact("KNOWN", k.known_at, s.kickoff_at, s.home_team_id, s.away_team_id, 1)

    def form(team_id, fixture_id, cutoff, horizon):
        return team_recent_form_v1(db_session, team_id=team_id, target_fixture_id=fixture_id, provider=PROVIDER, cutoff=cutoff, horizon=horizon)

    reg = Registry(tmp_path / "r.jsonl")
    rec = Emitter(clock=lambda: emit_at, knowledge=knowledge, form=form, models=models(), registry=reg).emit(ids[9500])
    assert rec["kind"] == "prediction" and datetime.fromisoformat(rec["kickoff_known"]) == K  # el adelanto aún no se conocía
    assert rec["features"]["home"]["n_used"] == 0 and rec["status"] == "DEGRADED"  # stats recibidas después de H: no cuentan
