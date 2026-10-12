"""M5.9B: regresiones de contrato de la simulación prospectiva (serialización canónica, huellas,
identidad de predicción, registro append-only, manifiesto, anclajes y orden temporal del emisor).
    python -m pytest research/tests/test_m59_contracts.py -q   (desde backend/)
Sin BD, sin red y sin numpy: datos sintéticos, reloj inyectado y registros en tmp_path. Importar el
emisor carga app.* (crea el engine, sin conectarse): si no hay DATABASE_URL se pone una inalcanzable
y un fixture falla si algo de este módulo intenta abrir una conexión."""

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://tests:tests@127.0.0.1:9/tests_sin_bd")

from sqlalchemy import event  # noqa: E402

from app.schemas.prematch_features import FORM_METRICS, FeatureStatus, MetricSide, MetricValue, SampleStatus, TeamRecentForm, WindowMatch  # noqa: E402
from app.schemas.statistics_knowledge import KnowledgeRegime, StatisticsProvenance  # noqa: E402
from research.m59 import anchors, evaluation  # noqa: E402
from research.m59 import emitter as emitter_module  # noqa: E402
from research.m59.canonical import canonical_json, sha256_hex  # noqa: E402
from research.m59.emitter import (AMBIGUOUS_TEAM_HISTORY, FIXTURE_AMBIGUOUS, FIXTURE_UNKNOWN, IDENTITY_INVALID, LATE, Emitter,  # noqa: E402
                                  FixtureFact)
from research.m59.manifest import FrozenModel, ManifestError, load, seal, validate  # noqa: E402
from research.m59.manifest import verify as verify_manifest  # noqa: E402
from research.m59.registry import GENESIS, Registry, RegistryError, prediction_id, verify  # noqa: E402

UTC = timezone.utc
K = datetime(2027, 3, 6, 18, 0, tzinfo=UTC)  # partido futuro sintético
T = K - timedelta(hours=1)
MICRO = timedelta(microseconds=1)
NUM = ["home_shots_total_for", "away_shots_total_for"]
FLAGS = ["home_shots_total_for_missing", "away_shots_total_for_missing"]
ALWAYS = lambda a: True  # noqa: E731  (la verificación de la autoridad se inyecta)


@pytest.fixture(autouse=True)
def no_database():
    """Ningún test de este módulo puede abrir una conexión de BD."""
    from app.db.database import engine

    def refuse(*_args, **_kwargs):
        raise AssertionError("un test de contrato M5.9B intentó conectarse a la BD")

    event.listen(engine, "do_connect", refuse)
    try:
        yield
    finally:
        event.remove(engine, "do_connect", refuse)


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


def _body(manifest):
    return {k: v for k, v in json.loads(canonical_json(manifest)).items() if k != "manifest_sha256"}  # copia profunda sin huella


def _resealed(kind, change):
    """Manifiesto con un cambio y la huella recalculada: lo que falle es la forma, no la huella."""
    body = _body(manifests()[kind])
    change(body)
    return seal(body)


# --- 1. Serialización canónica ------------------------------------------------------------------

VALUE = {"b": 1, "a": [1.5, "x", None, True, (1, 2)], "ñ": "é", 3: {"z": 0, "y": -0.5}, "t": datetime(2027, 1, 1, tzinfo=UTC)}
VALUE_TEXT = '{"3":{"y":-0.5,"z":0},"a":[1.5,"x",null,true,[1,2]],"b":1,"t":"2027-01-01T00:00:00+00:00","ñ":"é"}'


def test_canonical_json_exact_form():
    # claves ordenadas (y convertidas a texto), sin espacios, tuplas como listas, sin escapar UTF-8
    assert canonical_json(VALUE) == VALUE_TEXT
    assert canonical_json(dict(reversed(list(VALUE.items())))) == VALUE_TEXT


def test_canonical_json_is_a_fixed_point_of_its_own_round_trip():
    assert canonical_json(json.loads(VALUE_TEXT)) == VALUE_TEXT
    assert sha256_hex(json.loads(VALUE_TEXT)) == sha256_hex(VALUE)


def test_canonical_json_uses_exact_float_repr():
    assert canonical_json([0.1, 1e-07, 1.0, 1e22, -0.0, 1]) == "[0.1,1e-07,1.0,1e+22,-0.0,1]"
    assert canonical_json(0.1 + 0.2) == "0.30000000000000004"  # sin redondeo


@pytest.mark.parametrize("instant, text", [
    (datetime(2027, 1, 1, tzinfo=UTC), "2027-01-01T00:00:00+00:00"),
    (datetime(2027, 1, 1, 1, 0, tzinfo=timezone(timedelta(hours=1))), "2027-01-01T00:00:00+00:00"),
    (datetime(2026, 12, 31, 19, 0, tzinfo=timezone(timedelta(hours=-5))), "2027-01-01T00:00:00+00:00"),
    (datetime(2027, 1, 1, 0, 0, 0, 5, tzinfo=UTC), "2027-01-01T00:00:00.000005+00:00"),
])
def test_canonical_instants_are_iso_utc(instant, text):
    assert canonical_json({"t": instant}) == f'{{"t":"{text}"}}'
    assert sha256_hex({"t": instant}) == sha256_hex({"t": text})  # el mismo instante, la misma huella


@pytest.mark.parametrize("wrap", [lambda v: v, lambda v: [v], lambda v: (v,), lambda v: {"a": {"b": [v]}}], ids=["top", "list", "tuple", "nested"])
@pytest.mark.parametrize("bad, message", [(datetime(2027, 1, 1), "zona horaria"), (float("nan"), "no finito"),
                                          (float("inf"), "no finito"), (float("-inf"), "no finito")], ids=["naive", "nan", "inf", "-inf"])
def test_canonical_rejects_naive_instants_and_non_finite_floats_at_any_depth(wrap, bad, message):
    with pytest.raises(ValueError, match=message):
        canonical_json(wrap(bad))
    with pytest.raises(ValueError, match=message):
        sha256_hex(wrap(bad))


@pytest.mark.parametrize("unsupported", [Decimal("1.5"), {1, 2}, b"raw", object()], ids=["decimal", "set", "bytes", "object"])
def test_canonical_never_stringifies_unsupported_types(unsupported):
    with pytest.raises(TypeError):
        canonical_json({"x": unsupported})


# --- 2. Huella SHA-256 --------------------------------------------------------------------------


def test_sha256_is_the_digest_of_the_canonical_utf8_bytes():
    assert sha256_hex({}) == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"  # sha256(b"{}")
    assert sha256_hex(VALUE) == hashlib.sha256(VALUE_TEXT.encode("utf-8")).hexdigest()
    assert sha256_hex({"ñ": "é"}) == hashlib.sha256('{"ñ":"é"}'.encode("utf-8")).hexdigest()
    assert sha256_hex({"ñ": "é"}) != hashlib.sha256(json.dumps({"ñ": "é"}, separators=(",", ":")).encode()).hexdigest()  # no la forma \uXXXX
    digest = sha256_hex(VALUE)
    assert len(digest) == 64 and digest == digest.lower() and int(digest, 16) >= 0


def test_sha256_changes_with_any_change_of_content_or_type():
    variants = [{"a": 1}, {"a": 1.0}, {"a": "1"}, {"a": True}, {"a": [1]}, {"a": None}, {"A": 1}, {"a": 1, "b": None}, {"a": 2}, {}]
    assert len({sha256_hex(v) for v in variants}) == len(variants)


# --- 3. Manifiesto: huella, verificación y entradas no válidas -----------------------------------


def test_seal_is_deterministic_idempotent_and_ignores_a_stale_fingerprint():
    for m in manifests().values():
        body = _body(m)
        assert m["manifest_sha256"] == sha256_hex(body)
        assert seal(m) == m and seal({**body, "manifest_sha256": "0" * 64}) == m
        assert canonical_json(seal(dict(reversed(list(body.items()))))) == canonical_json(m)
        verify_manifest(m)


def test_manifest_fingerprint_is_the_same_for_an_instant_and_its_iso_text():
    as_text = manifests()["B0"]
    as_instant = seal({**_body(as_text), "frozen_at": datetime(2027, 1, 1, 1, 0, tzinfo=timezone(timedelta(hours=1)))})
    assert as_instant["manifest_sha256"] == as_text["manifest_sha256"]
    validate(as_instant)
    assert load(canonical_json(as_instant)) == as_text  # ida y vuelta por texto: misma huella


@pytest.mark.parametrize("kind", ["B0", "B1", "B2"])
def test_manifest_text_round_trip_keeps_content_and_fingerprint(kind):
    m = manifests()[kind]
    text = canonical_json(m)
    assert load(text) == m and canonical_json(load(text)) == text
    assert FrozenModel(load(text)).sha == m["manifest_sha256"] and FrozenModel(m).kind == kind


@pytest.mark.parametrize("key", sorted(_body(manifests()["B2"])))
def test_manifest_verify_detects_a_change_in_any_field(key):
    m = manifests()["B2"]
    for tampered in ({**m, key: {"tampered": True}}, {k: v for k, v in m.items() if k != key}):
        with pytest.raises(ManifestError, match="huella"):
            verify_manifest(tampered)
        with pytest.raises(ManifestError, match="huella"):
            validate(tampered)
        with pytest.raises(ManifestError, match="huella"):
            FrozenModel(tampered)


def _set(path, value):
    def change(m):
        node = m
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value
    return change


@pytest.mark.parametrize("change", [
    _set(("params", "coef", 2, 4), 0.1000000001), _set(("params", "intercept", 0), -0.41), _set(("params", "competitions"), [1, 2]),
    _set(("params", "numeric_columns", 0), "away_shots_total_for"), _set(("params", "scaler_scale", 1), 3), _set(("hyperparameters", "C"), 1.0),
    _set(("dependencies", "numpy"), "2.5.3"), _set(("extra",), 1), _set(("manifest_sha256",), "f" * 64),
    lambda m: m.pop("manifest_sha256"), lambda m: m.__setitem__("manifest_sha256", m["manifest_sha256"].upper()),
    lambda m: m.__setitem__("manifest_sha256", manifests()["B0"]["manifest_sha256"]),
], ids=["coef", "intercept", "competitions", "column", "int-for-float", "hyperparameter", "dependency", "extra-key", "wrong-sha", "no-sha",
        "upper-sha", "other-manifest-sha"])
def test_manifest_fingerprint_covers_nested_content(change):
    tampered = json.loads(canonical_json(manifests()["B2"]))
    change(tampered)
    with pytest.raises(ManifestError, match="huella"):
        validate(tampered)
    with pytest.raises(ManifestError, match="huella"):
        load(json.dumps(tampered))


@pytest.mark.parametrize("key", ["manifest_version", "model_kind", "model_version", "feature_contract", "classes", "training_id",
                                 "frozen_at", "code_version", "dependencies", "dataset_fingerprint", "hyperparameters", "params"])
def test_validate_requires_every_field_even_with_a_valid_fingerprint(key):
    incomplete = _resealed("B2", lambda m: m.pop(key))
    verify_manifest(incomplete)  # la huella es correcta: lo que falta es el campo
    with pytest.raises(ManifestError, match=f"falta {key}"):
        validate(incomplete)


@pytest.mark.parametrize("change, message", [
    (_set(("manifest_version",), 2), "versión o tipo"), (_set(("manifest_version",), "1"), "versión o tipo"),
    (_set(("model_kind",), "B3"), "versión o tipo"), (_set(("model_kind",), "b0"), "versión o tipo"),
    (_set(("feature_contract",), "team_recent_form_v2"), "contrato de features o clases"),
    (_set(("classes",), ["H", "A", "D"]), "contrato de features o clases"), (_set(("classes",), ["H", "D"]), "contrato de features o clases"),
], ids=["version-2", "version-text", "kind-unknown", "kind-case", "feature-contract", "classes-order", "classes-missing"])
def test_validate_rejects_unsupported_headers(change, message):
    with pytest.raises(ManifestError, match=message):
        validate(_resealed("B0", change))


BAD_PROBS = [[0.5, 0.5], [0.25, 0.25, 0.25, 0.25], [0.5, 0.3, 0.3], [0.5, 0.25, 0.25 + 5e-9], ["0.5", 0.25, 0.25], [True, 0, 0],
             [None, 0.5, 0.5], "0.5,0.25,0.25", {"H": 1.0}, None, []]
BAD_PROBS_IDS = ["two", "four", "sum-high", "sum-off-tolerance", "text-number", "bool", "null", "text", "mapping", "missing", "empty"]


@pytest.mark.parametrize("probs", BAD_PROBS, ids=BAD_PROBS_IDS)
@pytest.mark.parametrize("kind, path", [("B0", ("params", "probs")), ("B1", ("params", "base")), ("B1", ("params", "by_competition", "1"))],
                         ids=["B0", "B1-base", "B1-competition"])
def test_validate_rejects_invalid_probability_vectors(kind, path, probs):
    bad = _resealed(kind, _set(path, probs))
    with pytest.raises(ManifestError):
        validate(bad)
    with pytest.raises(ManifestError):
        FrozenModel(bad)


def test_probability_vectors_within_tolerance_are_accepted():
    for probs in ([0.5, 0.25, 0.25], [1, 0, 0], [0.5, 0.25, 0.25 + 5e-10], [1 / 3, 1 / 3, 1 / 3]):
        validate(_resealed("B0", _set(("params", "probs"), probs)))


PROB_PATHS = [("B0", ("params", "probs")), ("B1", ("params", "base")), ("B1", ("params", "by_competition", "1"))]
PROB_PATH_IDS = ["B0", "B1-base", "B1-competition"]
# cada componente debe estar en [0, 1]: todos estos suman 1 (dentro de la tolerancia) y aun así no son probabilidades
OUT_OF_RANGE_PROBS = [[1.5, -0.25, -0.25], [2, -1, 0], [-0.5, 0.75, 0.75], [0.5, 0.5 + 1e-12, -1e-12], [1 + 5e-10, 0, 0], [1 + 5e-10, -5e-10, 0]]
OUT_OF_RANGE_IDS = ["above-and-negative", "integers", "negative", "tiny-negative", "above-one-within-sum-tolerance", "both-within-sum-tolerance"]


@pytest.mark.parametrize("probs", OUT_OF_RANGE_PROBS, ids=OUT_OF_RANGE_IDS)
@pytest.mark.parametrize("kind, path", PROB_PATHS, ids=PROB_PATH_IDS)
def test_validate_rejects_probability_components_outside_the_unit_interval(kind, path, probs):
    bad = _resealed(kind, _set(path, probs))
    verify_manifest(bad)  # la huella es correcta: lo que falla es el vector
    with pytest.raises(ManifestError, match="vector de probabilidades"):
        validate(bad)
    with pytest.raises(ManifestError, match="vector de probabilidades"):
        FrozenModel(bad)
    with pytest.raises(ManifestError, match="vector de probabilidades"):
        load(canonical_json(bad))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("kind, path", PROB_PATHS, ids=PROB_PATH_IDS)
def test_non_finite_probability_components_are_never_sealed_nor_accepted(kind, path, bad):
    body = _body(manifests()[kind])
    _set(path, [bad, 0.5, 0.5])(body)
    with pytest.raises(ValueError):  # no tienen forma canónica: ni siquiera se pueden sellar
        seal(body)
    unsealed = {**body, "manifest_sha256": manifests()[kind]["manifest_sha256"]}
    with pytest.raises(ValueError):
        validate(unsealed)
    with pytest.raises(ValueError):
        FrozenModel(unsealed)


@pytest.mark.parametrize("probs", [[1, 0, 0], [0, 1, 0], [0.0, 0.0, 1.0], [0.5, 0.5, 0], [0.2, 0.3, 0.5], [1 - 5e-10, 0, 0], [0.5, 0.25, 0.25 - 5e-10]],
                         ids=["one-first", "one-middle", "one-last-float", "zero-component", "interior", "sum-low-in-tolerance", "interior-low-in-tolerance"])
@pytest.mark.parametrize("kind, path", PROB_PATHS, ids=PROB_PATH_IDS)
def test_valid_probability_vectors_are_served_unchanged(kind, path, probs):
    ok = _resealed(kind, _set(path, probs))
    validate(ok)
    assert load(canonical_json(ok)) == ok
    served = FrozenModel(ok).predict({"competition_id": 1 if "by_competition" in path else 99})
    assert served == probs and [type(v) for v in served] == [type(v) for v in probs]  # sin normalizar ni convertir


def test_valid_manifest_fingerprints_and_canonical_hashes_are_unchanged():
    # huellas fijadas antes de endurecer la validación: los manifiestos válidos conservan su identidad
    assert {k: m["manifest_sha256"] for k, m in manifests().items()} == {
        "B0": "251e6232cd15ca6f0641681f9920a2d5c7da340e1753f9dac58e31272ecef48d",
        "B1": "ade1fc7678a23e348952b40eef3362d0325e18f855e41a9713ac7f8c8a24082a",
        "B2": "91f7d1cbbe962d5fa469641e356e42345b7f1dba7a2e492d1162207870be57a2"}
    assert sha256_hex([0.45, 0.26, 0.29]) == "d2086057c6bc760bb8acb7ad6a4a4447e91c89356b09c901fdbf893c1476d3c6"
    assert sha256_hex([1, 0, 0]) == "7ff52993d5d3cb149089942de7fc7a6511db2671cf2125976c081ad6fff89772"
    assert sha256_hex([0.0, 0.0, 1.0]) == "e4aea449125555e5faa564f372700a986bbcdaa343ca808bd9166d50fa51c2bb"
    for m in manifests().values():
        validate(m)


@pytest.mark.parametrize("change, message", [
    (_set(("params", "coef"), [[0.0] * 5] * 2), "coeficientes"), (_set(("params", "coef", 1), [0.0] * 4), "coeficientes"),
    (_set(("params", "coef", 1), [0.0] * 6), "coeficientes"), (_set(("params", "intercept"), [0.1, 0.2]), "coeficientes"),
    (_set(("params", "model_classes"), ["H", "D", "A"]), "coeficientes"), (_set(("params", "competitions"), [1, 2]), "coeficientes"),
    (_set(("params", "flag_columns"), FLAGS[:1]), "coeficientes"),
    (_set(("params", "imputer_medians"), [10.0]), "imputador o escalador"), (_set(("params", "scaler_mean"), [10.0, 10.0, 10.0]), "imputador o escalador"),
    (_set(("params", "scaler_scale"), []), "imputador o escalador"), (_set(("params", "numeric_columns"), NUM[:1]), "imputador o escalador"),
    (_set(("params", "coef", 0, 0), "0.3"), "número no válido"), (_set(("params", "intercept", 2), True), "número no válido"),
    (_set(("params", "scaler_scale", 0), None), "número no válido"), (_set(("params", "imputer_medians", 1), [10.0]), "número no válido"),
], ids=["coef-rows", "coef-row-short", "coef-row-long", "intercept-len", "class-order", "competitions-width", "flags-width", "medians-len",
        "mean-len", "scale-len", "numeric-len", "coef-text", "intercept-bool", "scale-null", "median-list"])
def test_validate_rejects_b2_parameters_with_unexpected_shape(change, message):
    bad = _resealed("B2", change)
    with pytest.raises(ManifestError, match=message):
        validate(bad)
    with pytest.raises(ManifestError, match=message):
        load(canonical_json(bad))


@pytest.mark.parametrize("text", ["{}", '{"manifest_sha256": null}', '{"model_kind": "B0"}'])
def test_load_rejects_objects_without_a_matching_fingerprint(text):
    with pytest.raises(ManifestError, match="huella"):
        load(text)


@pytest.mark.parametrize("text", ["", "{not json", '{"params": ', '{"params": {"probs": [NaN, 0.5, 0.5]}}', '{"x": Infinity}'],
                         ids=["empty", "garbage", "truncated", "nan", "infinity"])
def test_load_rejects_malformed_or_non_finite_text(text):
    with pytest.raises(ValueError):  # JSONDecodeError o "float no finito": nunca se carga
        load(text)


def _unsealed(kind, change):
    """Manifiesto alterado SIN recalcular la huella (un valor sin forma canónica no se puede sellar)."""
    body = _body(manifests()[kind])
    change(body)
    return {**body, "manifest_sha256": manifests()[kind]["manifest_sha256"]}


@pytest.mark.parametrize("not_an_object", [[], None, "{}", 1, [manifests()["B0"]]], ids=["list", "null", "text", "number", "wrapped"])
def test_a_manifest_that_is_not_an_object_is_a_manifest_error(not_an_object):
    for check in (verify_manifest, validate, FrozenModel):
        with pytest.raises(ManifestError, match="no es un objeto"):
            check(not_an_object)
    with pytest.raises(ManifestError, match="no es un objeto"):
        load(json.dumps(not_an_object))


@pytest.mark.parametrize("change, message", [
    (_set(("params", "probs"), [float("nan"), 0.5, 0.5]), "float no finito"), (_set(("params", "probs"), [float("inf"), 0, 0]), "float no finito"),
    (_set(("params", "probs"), [float("-inf"), 1, 1]), "float no finito"), (_set(("hyperparameters", "x"), float("nan")), "float no finito"),
    (_set(("frozen_at",), datetime(2027, 1, 1)), "instante sin zona horaria"), (_set(("dependencies",), {1, 2}), "forma canónica"),
], ids=["nan", "inf", "-inf", "nan-outside-params", "naive-instant", "unsupported-type"])
def test_manifest_content_without_canonical_form_is_a_manifest_error(change, message):
    bad = _unsealed("B0", change)
    for check in (verify_manifest, validate, FrozenModel):
        with pytest.raises(ManifestError, match=message) as caught:
            check(bad)
        assert "forma canónica" in str(caught.value)


@pytest.mark.parametrize("text", ['{"params": {"probs": [NaN, 0.5, 0.5]}}', '{"x": Infinity}', '{"x": -Infinity}'], ids=["nan", "infinity", "-infinity"])
def test_load_reports_non_finite_text_as_a_manifest_error(text):
    with pytest.raises(ManifestError, match="float no finito"):
        load(text)


B2_PARAMS = ["numeric_columns", "flag_columns", "competitions", "imputer_medians", "scaler_mean", "scaler_scale", "model_classes", "coef", "intercept"]


@pytest.mark.parametrize("kind, key", [("B0", "probs"), ("B1", "base"), ("B1", "by_competition"), *(("B2", key) for key in B2_PARAMS)],
                         ids=lambda v: v)
def test_validate_reports_each_missing_parameter(kind, key):
    incomplete = _resealed(kind, lambda m: m["params"].pop(key))
    verify_manifest(incomplete)  # la huella es correcta: lo que falta es el parámetro
    with pytest.raises(ManifestError, match=f"falta params.{key}$"):
        validate(incomplete)
    with pytest.raises(ManifestError, match=f"falta params.{key}$"):
        load(canonical_json(incomplete))


@pytest.mark.parametrize("kind, change, message", [
    ("B0", _set(("params",), []), "params: se esperaba un objeto"), ("B1", _set(("params",), None), "params: se esperaba un objeto"),
    ("B2", _set(("params",), "coef"), "params: se esperaba un objeto"),
    ("B1", _set(("params", "by_competition"), [[0.5, 0.25, 0.25]]), "by_competition: se esperaba un objeto"),
    ("B1", _set(("params", "by_competition"), None), "by_competition: se esperaba un objeto"),
    ("B1", _set(("params", "by_competition", "abc"), [0.5, 0.25, 0.25]), "competición no válida"),
    ("B1", _set(("params", "by_competition", "1.5"), [0.5, 0.25, 0.25]), "competición no válida"),
    ("B2", _set(("params", "numeric_columns"), 2), "numeric_columns: se esperaba una lista"),
    ("B2", _set(("params", "flag_columns"), None), "flag_columns: se esperaba una lista"),
    ("B2", _set(("params", "competitions"), None), "competitions: se esperaba una lista"),
    ("B2", _set(("params", "imputer_medians"), None), "imputer_medians: se esperaba una lista"),
    ("B2", _set(("params", "scaler_mean"), 10.0), "scaler_mean: se esperaba una lista"),
    ("B2", _set(("params", "scaler_scale"), None), "scaler_scale: se esperaba una lista"),
    ("B2", _set(("params", "coef"), None), "coef: se esperaba una lista"), ("B2", _set(("params", "coef", 1), None), "coef: se esperaba una lista"),
    ("B2", _set(("params", "coef", 1), 5), "coef: se esperaba una lista"), ("B2", _set(("params", "intercept"), None), "intercept: se esperaba una lista"),
], ids=["params-list", "params-null", "params-text", "by-competition-list", "by-competition-null", "competition-text", "competition-decimal",
        "numeric-number", "flags-null", "competitions-null", "medians-null", "mean-number", "scale-null", "coef-null", "coef-row-null",
        "coef-row-number", "intercept-null"])
def test_validate_reports_malformed_parameter_structures(kind, change, message):
    bad = _resealed(kind, change)
    with pytest.raises(ManifestError, match=message):
        validate(bad)
    with pytest.raises(ManifestError, match=message):
        FrozenModel(bad)


def test_frozen_model_predictions_are_deterministic_copies():
    ms = models()
    row = {"competition_id": 1, "home_shots_total_for": 14.0, "away_shots_total_for": 9.0,
           "home_shots_total_for_missing": False, "away_shots_total_for_missing": False}
    for name, m in ms.items():
        first = m.predict(row)
        assert m.sha == manifests()[name]["manifest_sha256"] and m.predict(row) == first
        first[0] = 9.0  # mutar lo devuelto no altera el modelo congelado
        assert m.predict(row)[0] != 9.0 and FrozenModel(manifests()[name]).predict(row) == m.predict(row)
    assert ms["B1"].predict({"competition_id": 99}) == ms["B0"].predict({}) == [0.45, 0.26, 0.29]
    assert ms["B0"].known_competition(99) and ms["B1"].known_competition(1) and not ms["B1"].known_competition(99)


def test_b2_imputes_missing_values_with_the_frozen_median():
    b2 = models()["B2"]
    row = {"competition_id": 1, "home_shots_total_for": 14.0, "away_shots_total_for": None,
           "home_shots_total_for_missing": False, "away_shots_total_for_missing": True}
    assert b2.predict(row) == b2.predict({**row, "away_shots_total_for": 10.0})  # 10.0 = imputer_medians[1]
    assert b2.predict(row) != b2.predict({**row, "away_shots_total_for": 0.0})  # ausente no es cero


def test_b2_returns_probabilities_in_class_order_not_in_model_order():
    # model_classes = [A, D, H]; con coeficientes nulos la salida es softmax(intercept), reordenada a [H, D, A]
    zero = _resealed("B2", lambda m: m["params"].update(coef=[[0.0] * 5] * 3, intercept=[math.log(0.2), math.log(0.3), math.log(0.5)]))
    probs = FrozenModel(zero).predict({"competition_id": 77, **dict.fromkeys(NUM, 1.0), **dict.fromkeys(FLAGS, False)})
    assert probs == pytest.approx([0.5, 0.3, 0.2], abs=1e-12)


# --- 4. Identidad de predicción y registro append-only -------------------------------------------


def _rec(i, **kw):
    return {"kind": "prediction", "prediction_id": f"p{i}", "fixture_id": i, "emitted_at": datetime(2027, 1, 1, tzinfo=UTC), **kw}


def _chain(tmp_path, n=4):
    reg = Registry(tmp_path / "r.jsonl")
    for i in range(n):
        reg.append(_rec(i))
    return reg


def _rehash(record):
    record["record_hash"] = sha256_hex({k: v for k, v in record.items() if k != "record_hash"})
    return record


def _rechain(records):
    """Reescribe seq, prev_hash y record_hash de toda la lista (lo que haría quien falsifica la cadena)."""
    prev = GENESIS
    for i, rec in enumerate(records):
        rec.update(seq=i, prev_hash=prev)
        prev = _rehash(rec)["record_hash"]
    return records


def test_prediction_id_is_the_fingerprint_of_model_set_fixture_and_cutoff():
    pid = prediction_id("a" * 64, 7, T.isoformat())
    assert pid == sha256_hex({"model_set": "a" * 64, "fixture_id": 7, "T": "2027-03-06T17:00:00+00:00"})
    assert pid == prediction_id("a" * 64, 7, T.isoformat()) and len(pid) == 64


@pytest.mark.parametrize("other", [("b" * 64, 7, T.isoformat()), ("a" * 64, 8, T.isoformat()), ("a" * 64, 7, (T + MICRO).isoformat()),
                                   ("a" * 64, 7, (T + timedelta(days=1)).isoformat())], ids=["model-set", "fixture", "cutoff-microsecond", "cutoff-day"])
def test_prediction_id_changes_with_each_component(other):
    assert prediction_id(*other) != prediction_id("a" * 64, 7, T.isoformat())


def test_empty_registry_and_reopening(tmp_path):
    path = tmp_path / "nested" / "dir" / "r.jsonl"
    reg = Registry(path)  # crea las carpetas y un archivo vacío
    assert path.read_bytes() == b"" and reg.records() == [] and reg.head() == (-1, GENESIS) and verify([]) == []
    first = reg.append(_rec(1))
    reopened = Registry(path)  # reabrir no trunca
    assert reopened.records() == [first] and reopened.head() == (0, first["record_hash"])
    assert reopened.append(_rec(2))["seq"] == 1


def test_registry_record_layout_and_hash(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    a = reg.append(_rec(1, emitted_at=datetime(2027, 1, 1, 1, 0, tzinfo=timezone(timedelta(hours=1))), nested={"z": (1, 2), "a": 0.1}))
    b = reg.append(_rec(2))
    assert (a["seq"], a["prev_hash"]) == (0, GENESIS) and GENESIS == "0" * 64
    assert a["emitted_at"] == "2027-01-01T00:00:00+00:00" and a["nested"] == {"a": 0.1, "z": [1, 2]}  # se guarda la forma canónica
    for rec, prev in ((a, GENESIS), (b, a["record_hash"])):
        assert rec["prev_hash"] == prev and rec["record_hash"] == sha256_hex({k: v for k, v in rec.items() if k != "record_hash"})
    assert reg.records() == [a, b]  # lo devuelto es exactamente lo persistido


def test_registry_file_is_canonical_jsonl_and_only_grows(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    before = b""
    for i in range(4):
        rec = reg.append(_rec(i, note="ñ"))
        now = reg.path.read_bytes()
        assert now.startswith(before) and now[len(before):] == (canonical_json(rec) + "\n").encode("utf-8")  # solo se añade una línea
        before = now
    assert b"\r" not in before and before.count(b"\n") == 4 and "ñ".encode("utf-8") in before
    reg.append(_rec(0, note="ñ"))  # reemisión idéntica: ni un byte
    assert reg.path.read_bytes() == before


@pytest.mark.parametrize("record", [{}, {"kind": "prediction"}, {"prediction_id": "p"}, {"kind": "result", "prediction_id": "p"},
                                    {"kind": None, "prediction_id": "p"}, {"kind": "PREDICTION", "prediction_id": "p"}],
                         ids=["empty", "no-id", "no-kind", "unknown-kind", "null-kind", "kind-case"])
def test_registry_rejects_records_without_kind_or_identity(tmp_path, record):
    reg = _chain(tmp_path, 1)
    before = reg.path.read_bytes()
    with pytest.raises(RegistryError, match="sin tipo o sin prediction_id"):
        reg.append(record)
    assert reg.path.read_bytes() == before


def test_registry_idempotency_ignores_only_the_emission_instant(tmp_path):
    reg = Registry(tmp_path / "r.jsonl")
    first = reg.append(_rec(1, probs=[0.5, 0.25, 0.25]))
    again = reg.append(_rec(1, probs=(0.5, 0.25, 0.25), emitted_at=datetime(2027, 1, 2, tzinfo=UTC)))  # reintento posterior
    assert again == first and again["emitted_at"] == "2027-01-01T00:00:00+00:00" and len(reg.records()) == 1  # manda la primera


@pytest.mark.parametrize("conflict", [
    _rec(1, probs=[0.5, 0.25, 0.25 + 1e-12]), _rec(1, probs=[0.5, 0.25, 0.25], extra=None), _rec(1), _rec(1, probs=[0.5, 0.25, 0.25], fixture_id=2),
    {**_rec(1, probs=[0.5, 0.25, 0.25]), "kind": "no_emission"},
], ids=["value", "extra-key", "missing-key", "fixture", "kind"])
def test_registry_never_rewrites_an_existing_prediction_id(tmp_path, conflict):
    reg = Registry(tmp_path / "r.jsonl")
    reg.append(_rec(1, probs=[0.5, 0.25, 0.25]))
    before = reg.path.read_bytes()
    with pytest.raises(RegistryError, match="otro contenido"):
        reg.append(conflict)
    assert reg.path.read_bytes() == before


def test_verify_accepts_every_prefix_of_a_valid_chain(tmp_path):
    recs = _chain(tmp_path, 5).records()
    assert verify(recs) is recs
    for n in range(len(recs) + 1):  # un prefijo es una cadena válida: la truncación solo la delata un anclaje
        assert verify(recs[:n]) == recs[:n]


def _forged(seq, prev_hash, pid, **kw):
    return _rehash({"kind": "prediction", "prediction_id": pid, "fixture_id": 99, "emitted_at": "2027-01-01T00:00:00+00:00",
                    "seq": seq, "prev_hash": prev_hash, **kw})


CORRUPTIONS = {
    "payload": (lambda r: r[1].update(fixture_id=7), "seq 1: registro modificado"),
    "payload-type": (lambda r: r[1].update(fixture_id="1"), "seq 1: registro modificado"),
    "added-field": (lambda r: r[3].update(label="H"), "seq 3: registro modificado"),
    "removed-field": (lambda r: r[2].pop("fixture_id"), "seq 2: registro modificado"),
    "record-hash": (lambda r: r[0].update(record_hash="f" * 64), "seq 0: registro modificado"),
    "no-record-hash": (lambda r: r[2].pop("record_hash"), "seq 2: registro modificado"),
    "payload-rehashed": (lambda r: _rehash(r[1].update(fixture_id=7) or r[1]), "seq 2: prev_hash no enlaza"),
    "prev-hash-rehashed": (lambda r: _rehash(r[2].update(prev_hash=r[0]["record_hash"]) or r[2]), "seq 2: prev_hash no enlaza"),
    "genesis-rehashed": (lambda r: _rehash(r[0].update(prev_hash="1" * 64) or r[0]), "seq 0: prev_hash no enlaza"),
    "no-prev-hash": (lambda r: r[1].pop("prev_hash"), "seq 1: prev_hash no enlaza"),
    "delete-first": (lambda r: r.pop(0), "seq 1 en la posición 0"),
    "delete-middle": (lambda r: r.pop(2), "seq 3 en la posición 2"),
    "swap": (lambda r: r.__setitem__(slice(1, 3), [r[2], r[1]]), "seq 2 en la posición 1"),
    "duplicate-line": (lambda r: r.append(dict(r[3])), "seq 3 en la posición 4"),
    "repeat-in-place": (lambda r: r.insert(2, dict(r[1])), "seq 1 en la posición 2"),
    "seq-renumbered": (lambda r: _rehash(r[2].update(seq=5) or r[2]), "seq 5 en la posición 2"),
    "seq-text": (lambda r: _rehash(r[1].update(seq="1") or r[1]), "seq 1 en la posición 1"),
    "no-seq": (lambda r: r[1].pop("seq"), "seq None en la posición 1"),
    "inserted-forgery": (lambda r: r.insert(2, _forged(2, r[1]["record_hash"], "forged")), "seq 2 en la posición 3"),
    "appended-duplicate-id": (lambda r: r.append(_forged(4, r[3]["record_hash"], "p1")), "seq 4: prediction_id duplicado"),
    "replaced-tail-duplicate-id": (lambda r: r.__setitem__(3, _forged(3, r[2]["record_hash"], "p0")), "seq 3: prediction_id duplicado"),
}


@pytest.mark.parametrize("name", CORRUPTIONS)
def test_verify_detects_each_kind_of_corruption(tmp_path, name):
    corrupt, message = CORRUPTIONS[name]
    reg = _chain(tmp_path, 4)
    recs = reg.records()
    corrupt(recs)
    with pytest.raises(RegistryError, match=message):
        verify(recs)
    # el mismo daño en el archivo bloquea cualquier escritura posterior, y no se escribe nada
    damaged = "".join(canonical_json(r) + "\n" for r in recs).encode("utf-8")
    reg.path.write_bytes(damaged)
    with pytest.raises(RegistryError, match=message):
        reg.append(_rec(50))
    assert reg.path.read_bytes() == damaged


@pytest.mark.parametrize("tail", ['{"kind":"pred', "not json\n", "[1, 2\n"], ids=["torn-write", "garbage", "torn-array"])
def test_registry_with_an_unreadable_line_cannot_be_read_or_extended(tmp_path, tail):
    reg = _chain(tmp_path, 2)
    damaged = reg.path.read_bytes() + tail.encode("utf-8")
    reg.path.write_bytes(damaged)
    with pytest.raises(ValueError):  # json.JSONDecodeError: la línea rota no se ignora
        reg.records()
    with pytest.raises(ValueError):
        reg.append(_rec(50))
    assert reg.path.read_bytes() == damaged


def _rechained(records, change):
    """Copia de la cadena con un cambio en el registro 1 y todos los hashes recalculados."""
    copy = json.loads(json.dumps(records))
    change(copy[1])
    return _rechain(copy)


MALFORMED_CHAINS = {
    "record-is-text": (lambda r: r[:1] + ["x"] + r[2:], "posición 1: el registro no es un objeto"),
    "record-is-null": (lambda r: [None], "posición 0: el registro no es un objeto"),
    "record-is-list": (lambda r: r[:2] + [list(r[2].items())], "posición 2: el registro no es un objeto"),
    "no-prediction-id": (lambda r: _rechained(r, lambda rec: rec.pop("prediction_id")), "seq 1: registro sin prediction_id"),
    "unhashable-prediction-id": (lambda r: _rechained(r, lambda rec: rec.update(prediction_id=["p1"])), "seq 1: prediction_id no válido"),
    "non-finite-value": (lambda r: r[:1] + [{**r[1], "x": float("nan")}] + r[2:], "seq 1: registro modificado"),
    "non-finite-value-without-hash": (lambda r: r[:1] + [{k: v for k, v in {**r[1], "x": float("inf")}.items() if k != "record_hash"}], "seq 1: registro modificado"),
}


@pytest.mark.parametrize("name", MALFORMED_CHAINS)
def test_verify_reports_malformed_records_as_registry_errors(tmp_path, name):
    damage, message = MALFORMED_CHAINS[name]
    recs = damage(_chain(tmp_path, 3).records())
    with pytest.raises(RegistryError, match=message):
        verify(recs)
    # y la evaluación falla cerrada en vez de romperse: una cadena malformada no es válida ni evaluable
    status = evaluation.registry_status(recs, [], ALWAYS)
    assert (status["CHAIN_VALID"], status["EVALUATION_ELIGIBLE"]) == (False, False) and message in status["problems"][0]
    with pytest.raises(RegistryError, match=message):
        evaluation.classify([], recs, [], {}, ALWAYS)


def test_registry_file_with_a_non_finite_value_cannot_be_extended(tmp_path):
    reg = _chain(tmp_path, 2)
    damaged = reg.path.read_bytes() + b'{"kind":"prediction","prediction_id":"p9","prev_hash":"x","seq":2,"x":NaN}\n'  # json.loads lo admite
    reg.path.write_bytes(damaged)
    with pytest.raises(RegistryError, match="seq 2: prev_hash no enlaza"):
        reg.append(_rec(50))
    assert reg.path.read_bytes() == damaged


def test_fully_rewritten_chain_is_internally_valid_but_breaks_its_anchor(tmp_path):
    original = _chain(tmp_path, 4).records()
    anchor = {**anchors.make_statement(original), "authority": "rfc3161", "external_time": K - timedelta(days=1)}
    forged = json.loads(json.dumps(original))
    forged[1]["fixture_id"] = 7
    _rechain(forged)
    assert verify(forged) == forged and forged[0] == original[0] and forged[-1]["record_hash"] != original[-1]["record_hash"]
    with pytest.raises(anchors.AnchorError, match="no corresponde a esta cadena"):
        anchors.verify_anchor(forged, anchor, ALWAYS)
    assert anchors.verify_anchor(original, anchor, ALWAYS) == K - timedelta(days=1)
    status = evaluation.registry_status(forged, [anchor], ALWAYS)
    assert (status["CHAIN_VALID"], status["ANCHOR_VERIFIED"], status["EVALUATION_ELIGIBLE"]) == (True, False, False)


# --- 5. Anclajes: declaración, verificación y frontera temporal ----------------------------------


@pytest.fixture
def chain(tmp_path):
    return _chain(tmp_path, 3).records()


def _anchor(records, **kw):
    return {**anchors.make_statement(records), "authority": "project_thread", "external_time": K - timedelta(days=1), **kw}


def test_statement_binds_sequence_and_head_hash(chain):
    for n in (1, 2, 3):
        st = anchors.make_statement(chain[:n])
        assert st == {"registry_seq": n - 1, "head_hash": chain[n - 1]["record_hash"],
                      "statement_sha256": sha256_hex({"registry_seq": n - 1, "head_hash": chain[n - 1]["record_hash"]})}
        assert st == anchors.make_statement(chain[:n])
    with pytest.raises(anchors.AnchorError, match="registro vacío"):
        anchors.make_statement([])


@pytest.mark.parametrize("authority", sorted(anchors.ACCEPTED_AUTHORITIES))
def test_each_accepted_authority_returns_its_external_instant(chain, authority):
    seen = []
    when = anchors.verify_anchor(chain, _anchor(chain[:2], authority=authority), lambda a: seen.append(a) or True)
    assert when == K - timedelta(days=1) and len(seen) == 1 and seen[0]["authority"] == authority
    assert anchors.ACCEPTED_AUTHORITIES == {"rfc3161", "github_server", "project_thread"}


_DROP = object()
BAD_ANCHORS = {
    "local-commit": ({"authority": "git_local_commit"}, "autoridad no aceptada"),
    "authority-case": ({"authority": "RFC3161"}, "autoridad no aceptada"),
    "authority-null": ({"authority": None}, "autoridad no aceptada"),
    "no-authority": ({"authority": _DROP}, "autoridad no aceptada"),
    "seq-negative": ({"registry_seq": -1}, "no corresponde a esta cadena"),
    "seq-beyond-head": ({"registry_seq": 3}, "no corresponde a esta cadena"),
    "seq-text": ({"registry_seq": "1"}, "no corresponde a esta cadena"),
    "seq-float": ({"registry_seq": 1.0}, "no corresponde a esta cadena"),
    "no-seq": ({"registry_seq": _DROP}, "no corresponde a esta cadena"),
    "seq-of-another-record": ({"registry_seq": 0}, "no corresponde a esta cadena"),
    "head-hash": ({"head_hash": "f" * 64}, "no corresponde a esta cadena"),
    "no-head-hash": ({"head_hash": _DROP}, "no corresponde a esta cadena"),
    "statement-sha": ({"statement_sha256": "f" * 64}, "declaración del anclaje alterada"),
    "no-statement-sha": ({"statement_sha256": _DROP}, "declaración del anclaje alterada"),
    "naive-instant": ({"external_time": datetime(2027, 3, 5, 18, 0)}, "instante externo no válido"),
    "text-instant": ({"external_time": "2027-03-05T18:00:00+00:00"}, "instante externo no válido"),
    "no-instant": ({"external_time": _DROP}, "instante externo no válido"),
}


@pytest.mark.parametrize("name", BAD_ANCHORS)
def test_verify_anchor_rejects_each_invalid_anchor(chain, name):
    change, message = BAD_ANCHORS[name]
    anchor = {k: v for k, v in {**_anchor(chain[:2]), **change}.items() if v is not _DROP}
    with pytest.raises(anchors.AnchorError, match=message):
        anchors.verify_anchor(chain, anchor, ALWAYS)
    assert not anchors.anchored_before(chain[0], chain, [anchor], K, ALWAYS)  # un anclaje no válido no cubre nada
    assert evaluation.registry_status(chain, [anchor], ALWAYS)["EVALUATION_ELIGIBLE"] is False


def test_external_authority_has_the_last_word(chain):
    with pytest.raises(anchors.AnchorError, match="no confirma"):
        anchors.verify_anchor(chain, _anchor(chain), lambda a: False)
    assert not anchors.anchored_before(chain[0], chain, [_anchor(chain)], K, lambda a: False)


@pytest.mark.parametrize("external_time, covered", [(K - timedelta(days=30), True), (K - MICRO, True), (K, False), (K + MICRO, False)],
                         ids=["long-before", "one-microsecond-before", "at-kickoff", "after-kickoff"])
def test_anchor_must_be_strictly_before_the_deadline(chain, external_time, covered):
    assert anchors.anchored_before(chain[0], chain, [_anchor(chain, external_time=external_time)], K, ALWAYS) is covered


def test_anchor_covers_records_up_to_its_sequence_only(chain):
    at_one = _anchor(chain[:2])  # registry_seq = 1
    assert [anchors.anchored_before(r, chain, [at_one], K, ALWAYS) for r in chain] == [True, True, False]
    assert not anchors.anchored_before(chain[0], chain, [], K, ALWAYS)
    late, broken = _anchor(chain, external_time=K), {**_anchor(chain), "head_hash": "f" * 64}
    assert not anchors.anchored_before(chain[2], chain, [late, broken, at_one], K, ALWAYS)
    assert anchors.anchored_before(chain[2], chain, [late, broken, at_one, _anchor(chain)], K, ALWAYS)  # basta uno válido y a tiempo


# --- 6. Emisor simulado: orden temporal estricto y condiciones de no emisión ---------------------


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
        self.models = models()

    def know(self, fixture_id, since, **kw):
        self.facts.setdefault(fixture_id, []).append((since, FixtureFact(status="KNOWN", known_at=since, **kw)))

    def knowledge(self, fixture_id, at):
        known = [f for since, f in self.facts.get(fixture_id, []) if since <= at]
        return known[-1] if known else FixtureFact(status="UNKNOWN_AT_T")

    def form(self, team_id, fixture_id, cutoff, horizon):
        self.form_calls.append((cutoff, horizon))
        return _form(team_id, fixture_id, cutoff, horizon, status=self.form_status, used=self.used)

    def emitter(self, registry, clock=None):
        return Emitter(clock=clock or (lambda: self.now), knowledge=self.knowledge, form=self.form, models=self.models, registry=registry)


@pytest.fixture
def world():
    w = World()
    w.know(1, K - timedelta(days=30), kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=1)
    return w


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "r.jsonl")


@pytest.mark.parametrize("offset, emitted", [
    (-timedelta(days=7), True), (-timedelta(hours=1, microseconds=1), True), (-timedelta(hours=1), True),  # H <= T
    (-timedelta(hours=1) + MICRO, False), (-timedelta(minutes=1), False), (timedelta(0), False), (timedelta(hours=2), False),  # H > T
], ids=["week-before", "T-1us", "exactly-T", "T+1us", "K-1min", "at-kickoff", "after-kickoff"])
def test_emission_boundary_is_h_not_later_than_t(reg, world, offset, emitted):
    world.now = K + offset
    rec = world.emitter(reg).emit(1)
    assert rec["T"] == T.isoformat() and rec["H"] == rec["emitted_at"] == world.now.isoformat() and rec["kickoff_known"] == K.isoformat()
    if emitted:
        assert rec["kind"] == "prediction" and world.now <= T
        assert world.form_calls == [(world.now, world.now)] * 2  # corte = horizonte = H para local y visitante
    else:
        assert (rec["kind"], rec["reason"]) == ("no_emission", LATE) and "probs" not in rec and world.form_calls == []
    assert verify(reg.records()) == [rec]


def test_computation_that_crosses_t_is_refused_not_backdated(reg, world):
    ticks = iter([T - timedelta(seconds=1), T + timedelta(seconds=1)])  # el reloj pasa de T mientras se calcula
    rec = world.emitter(reg, clock=lambda: next(ticks)).emit(1)
    assert (rec["kind"], rec["reason"]) == ("no_emission", LATE)
    assert rec["emitted_at"] == rec["H"] == (T + timedelta(seconds=1)).isoformat() and rec["T"] == T.isoformat()
    assert [r["kind"] for r in reg.records()] == ["no_emission"]  # no queda ninguna predicción con hora anterior


def test_computation_that_ends_exactly_at_t_is_emitted(reg, world):
    ticks = iter([T - timedelta(seconds=1), T])
    rec = world.emitter(reg, clock=lambda: next(ticks)).emit(1)
    assert rec["kind"] == "prediction" and rec["H"] == (T - timedelta(seconds=1)).isoformat() and rec["emitted_at"] == T.isoformat()
    assert world.form_calls == [(T - timedelta(seconds=1),) * 2] * 2  # las features son las de H, no las del final del cálculo


def test_naive_clock_is_rejected_before_anything_is_written(reg, world):
    with pytest.raises(ValueError, match="reloj sin zona horaria"):
        world.emitter(reg, clock=lambda: datetime(2027, 3, 6, 15, 0)).emit(1)
    assert reg.records() == [] and world.form_calls == []


@pytest.mark.parametrize("names", [("B0", "B1"), ("B0", "B1", "B2", "B3"), ("B0", "B1", "b2"), ()], ids=["missing", "extra", "case", "none"])
def test_emitter_requires_exactly_the_three_frozen_models(reg, world, names):
    world.models = {name: models()["B0"] for name in names}
    with pytest.raises(ValueError, match="B0, B1 y B2"):
        world.emitter(reg)


def test_emitted_prediction_identity_is_model_set_fixture_and_t(reg, world):
    emitter = world.emitter(reg)
    assert emitter.model_set_sha == sha256_hex({k: m["manifest_sha256"] for k, m in manifests().items()})
    world.models = dict(reversed(list(models().items())))
    assert world.emitter(reg).model_set_sha == emitter.model_set_sha  # no depende del orden del diccionario
    rec = emitter.emit(1)
    assert rec["prediction_id"] == prediction_id(emitter.model_set_sha, 1, "2027-03-06T17:00:00+00:00") and rec["model_set_sha"] == emitter.model_set_sha
    assert rec["models"] == {k: {"sha": m["manifest_sha256"], "version": f"{k}-sim"} for k, m in manifests().items()}
    assert rec["registry_version"] == 1 and rec["fixture_known_at"] == (K - timedelta(days=30)).isoformat()


def test_a_different_frozen_model_is_a_different_prediction_identity(tmp_path, world):
    first = world.emitter(Registry(tmp_path / "a.jsonl")).emit(1)
    other_b0 = seal({**_meta("B0"), "hyperparameters": {}, "params": {"probs": [0.44, 0.27, 0.29]}})
    world.models = {**models(), "B0": FrozenModel(other_b0)}
    second = world.emitter(Registry(tmp_path / "b.jsonl")).emit(1)
    assert second["model_set_sha"] != first["model_set_sha"] and second["prediction_id"] != first["prediction_id"]
    assert second["probs"]["B0"] == [0.44, 0.27, 0.29] and second["probs"]["B2"] == first["probs"]["B2"]


def test_same_inputs_produce_byte_identical_registries(tmp_path):
    files = []
    for name in ("a.jsonl", "b.jsonl"):
        w, registry = World(), Registry(tmp_path / name)
        w.know(1, K - timedelta(days=30), kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=1)
        w.know(2, K - timedelta(days=30), kickoff_at=K, home_team_id=30, away_team_id=40, competition_id=77)
        for fixture_id in (1, 2, 3, 1):
            w.emitter(registry).emit(fixture_id)
        files.append(registry.path.read_bytes())
    assert files[0] == files[1] and files[0].count(b"\n") == 3  # 2 predicciones + 1 no emisión; la repetida no añade nada


def test_repeated_emission_keeps_the_first_record_and_skips_recomputation(reg, world):
    first = world.emitter(reg).emit(1)
    calls = len(world.form_calls)
    world.now += timedelta(minutes=30)  # sigue siendo H <= T
    world.used, world.form_status = 0, FeatureStatus.AMBIGUOUS_HISTORY  # el mundo cambió: no importa
    assert world.emitter(reg).emit(1) == first and len(world.form_calls) == calls and reg.records() == [first]


def test_refusals_are_idempotent_per_instant_and_do_not_block_a_later_emission(reg, world):
    world.know(2, K - timedelta(hours=2, minutes=30), kickoff_at=K, home_team_id=10, away_team_id=30, competition_id=1)  # se sabrá más tarde
    emitter = world.emitter(reg)
    refused = emitter.emit(2)
    assert emitter.emit(2) == refused and len(reg.records()) == 1  # misma decisión en el mismo instante: un solo registro
    assert refused["prediction_id"] == sha256_hex({"model_set": emitter.model_set_sha, "fixture_id": 2, "no_emission_at": world.now})
    assert (refused["reason"], refused["T"], refused["kickoff_known"]) == (FIXTURE_UNKNOWN, None, None)
    world.now += timedelta(minutes=10)
    again = emitter.emit(2)  # otro instante, otra decisión registrada
    assert again["reason"] == FIXTURE_UNKNOWN and again["prediction_id"] != refused["prediction_id"]
    world.now = K - timedelta(hours=2)
    rec = emitter.emit(2)  # ya se conoce, y a tiempo
    assert rec["kind"] == "prediction" and [r["kind"] for r in verify(reg.records())] == ["no_emission", "no_emission", "prediction"]


@pytest.mark.parametrize("status, reason", [("UNKNOWN_AT_T", FIXTURE_UNKNOWN), ("TEMPORAL_AMBIGUITY", FIXTURE_AMBIGUOUS)])
def test_unknown_or_ambiguous_fixture_is_refused_without_computing_features(reg, world, status, reason):
    world.facts[9] = [(K - timedelta(days=1), FixtureFact(status=status, kickoff_at=K, home_team_id=10, away_team_id=20, competition_id=1))]
    rec = world.emitter(reg).emit(9)
    assert (rec["kind"], rec["reason"], rec["T"]) == ("no_emission", reason, None) and world.form_calls == []
    assert "probs" not in rec and "status" not in rec


@pytest.mark.parametrize("missing", ["home_team_id", "away_team_id", "competition_id"])
def test_fixture_without_full_identity_is_refused(reg, world, missing):
    identity = {"home_team_id": 10, "away_team_id": 20, "competition_id": 1, missing: None}
    world.know(4, K - timedelta(days=2), kickoff_at=K, **identity)
    rec = world.emitter(reg).emit(4)
    assert (rec["kind"], rec["reason"], rec["T"]) == ("no_emission", IDENTITY_INVALID, T.isoformat()) and world.form_calls == []


@pytest.mark.parametrize("status", [FeatureStatus.TARGET_UNKNOWN, FeatureStatus.TARGET_AMBIGUOUS, FeatureStatus.TARGET_TEAM_MISMATCH,
                                    FeatureStatus.CUTOFF_AFTER_KICKOFF], ids=lambda s: s.value)
def test_feature_target_failure_is_refused(reg, world, status):
    world.form_status = status
    rec = world.emitter(reg).emit(1)
    assert (rec["kind"], rec["reason"]) == ("no_emission", IDENTITY_INVALID) and "probs" not in rec


@pytest.mark.parametrize("status", [FeatureStatus.AMBIGUOUS_HISTORY, FeatureStatus.AMBIGUOUS_ORDER], ids=lambda s: s.value)
def test_ambiguous_team_history_is_degraded_without_b2(reg, world, status):
    world.form_status = status
    rec = world.emitter(reg).emit(1)
    assert (rec["kind"], rec["status"], rec["degraded_reasons"]) == ("prediction", "DEGRADED", [AMBIGUOUS_TEAM_HISTORY])
    assert rec["probs"] == {"B0": [0.45, 0.26, 0.29], "B1": [0.5, 0.25, 0.25], "B2": None}  # B2 no se inventa
    assert {side: f["status"] for side, f in rec["features"].items()} == {"home": status.value, "away": status.value}


def test_changed_kickoff_is_a_new_identity_and_the_first_emission_stays_binding(reg, world):
    first = world.emitter(reg).emit(1)
    world.know(1, K - timedelta(hours=2, minutes=30), kickoff_at=K + timedelta(days=1), home_team_id=10, away_team_id=20, competition_id=1)
    world.now = K - timedelta(hours=2)
    second = world.emitter(reg).emit(1)  # otro T -> otro prediction_id: se registra, no sustituye
    assert second["prediction_id"] != first["prediction_id"] and second["T"] == (T + timedelta(days=1)).isoformat()
    records = verify(reg.records())
    assert records == [first, second]
    anchor = _anchor(records, external_time=world.now + timedelta(minutes=5))
    universe = [{"fixture_id": 1, "competition_id": 1, "final_kickoff": K + timedelta(days=1)}]
    entry = evaluation.classify(universe, records, [anchor], {1: "H"}, ALWAYS)[0]
    assert entry["prediction"] == first and entry["postponed"] and entry["class"] == "INFORMATIVE"


# --- 7. Identidad de predicción: T en UTC sea cual sea el desplazamiento del kickoff --------------

# valores fijados con el kickoff en UTC antes de normalizar: las identidades UTC existentes no cambian
PINNED_MODEL_SET = "70e0b727f10eb2542114c259073ac58b3f351771a708e854bbd15300ebb1c858"
PINNED_PREDICTION_ID = "a29dcfc2542979a191847bb3f6e214bd8ca7166d60fefef2b26e3e8ac6fc8ece"
PINNED_RECORD_HASH = "ddbb63c9d46faa901a96e41580560ca51c6536c260869a6d2ae24778a1cd68a1"
PINNED_STATEMENT = "22d07d4e1da4f500b9c6f5d31f821a56fa9093f0d82f1371f27390cf2c9cd146"
OFFSETS = [UTC, timezone(timedelta(hours=2)), timezone(timedelta(hours=-5)), timezone(timedelta(hours=5, minutes=30)), timezone(timedelta(hours=14))]
OFFSET_IDS = ["utc", "+02:00", "-05:00", "+05:30", "+14:00-next-day"]


def _world_with_kickoff(kickoff_at):
    w = World()
    w.know(1, K - timedelta(days=30), kickoff_at=kickoff_at, home_team_id=10, away_team_id=20, competition_id=1)
    return w


@pytest.mark.parametrize("tz", OFFSETS, ids=OFFSET_IDS)
def test_prediction_identity_does_not_depend_on_the_kickoff_offset(reg, tz):
    emitter = _world_with_kickoff(K.astimezone(tz)).emitter(reg)
    rec = emitter.emit(1)
    assert emitter.model_set_sha == PINNED_MODEL_SET
    assert rec["prediction_id"] == PINNED_PREDICTION_ID == prediction_id(PINNED_MODEL_SET, 1, "2027-03-06T17:00:00+00:00")
    assert (rec["T"], rec["kickoff_known"]) == (T.isoformat(), K.isoformat())
    # el mismo instante da el mismo registro, la misma cadena y la misma declaración que con el kickoff en UTC
    assert rec["record_hash"] == PINNED_RECORD_HASH and verify(reg.records()) == [rec]
    assert anchors.make_statement(reg.records())["statement_sha256"] == PINNED_STATEMENT


@pytest.mark.parametrize("tz", OFFSETS[1:], ids=OFFSET_IDS[1:])
def test_restating_the_same_kickoff_in_another_offset_is_the_same_prediction(reg, world, tz):
    first = world.emitter(reg).emit(1)
    before = reg.path.read_bytes()
    calls = len(world.form_calls)
    world.know(1, K - timedelta(hours=2, minutes=45), kickoff_at=K.astimezone(tz), home_team_id=10, away_team_id=20, competition_id=1)
    world.now += timedelta(minutes=30)  # el mismo K, reexpresado: no es una identidad nueva
    assert world.emitter(reg).emit(1) == first and reg.path.read_bytes() == before and len(world.form_calls) == calls


def test_different_instants_keep_different_identities_whatever_their_offset(tmp_path):
    same_wall_clock = K.replace(tzinfo=timezone(timedelta(hours=2)))  # 18:00+02:00 = 16:00 UTC: otro instante
    rec = _world_with_kickoff(same_wall_clock).emitter(Registry(tmp_path / "r.jsonl")).emit(1)
    assert rec["prediction_id"] == prediction_id(PINNED_MODEL_SET, 1, "2027-03-06T15:00:00+00:00") != PINNED_PREDICTION_ID
    assert rec["T"] == "2027-03-06T15:00:00+00:00"


def test_naive_kickoff_is_still_refused_and_never_read_as_local_time(reg):
    w = _world_with_kickoff(datetime(2027, 3, 6, 18, 0))
    with pytest.raises(TypeError):  # como antes: un instante sin zona no se puede comparar con el reloj
        w.emitter(reg).emit(1)
    assert reg.records() == [] and w.form_calls == []
    # y la identidad no le supone zona alguna: ni la local de la máquina ni UTC
    assert emitter_module._cutoff_iso(datetime(2027, 3, 6, 17, 0)) == "2027-03-06T17:00:00"


@pytest.mark.parametrize("tz", OFFSETS, ids=OFFSET_IDS)
def test_no_emission_records_do_not_depend_on_the_kickoff_offset(reg, tz):
    w = _world_with_kickoff(K.astimezone(tz))
    w.now = K - timedelta(minutes=30)
    late = w.emitter(reg).emit(1)
    assert (late["kind"], late["reason"], late["T"], late["kickoff_known"]) == ("no_emission", LATE, T.isoformat(), K.isoformat())
    assert late["prediction_id"] == "ed917567d9dc080b7b7fd30fb7f25cd126728bf610c59b08cbb88a7eab7e0dd4"
    assert late["record_hash"] == "b77467d8e9f72329403104be33faacf17cc6cf4fe081f0dcc91a26105a559a26"
    unknown = w.emitter(reg).emit(2)
    assert (unknown["reason"], unknown["T"], unknown["prev_hash"]) == (FIXTURE_UNKNOWN, None, late["record_hash"])
    assert verify(reg.records()) == [late, unknown]
