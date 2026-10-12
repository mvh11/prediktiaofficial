"""M5.10A: base sintética de replicación offline (filas, configuración, evaluación desde manifiestos
congelados e informe con huella).
    python -m pytest research/tests/test_m510a_replication.py -q   (desde backend/)
Sin BD, sin red, sin numpy y sin datos reales: filas y manifiestos sintéticos y deterministas."""

import copy
import hashlib
import json
import math
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.m58 import metrics
from research.m59.canonical import canonical_json, sha256_hex
from research.m59.manifest import FrozenModel, ManifestError, seal
from research.m510a import evaluate as evaluate_module
from research.m510a.config import CONFIG_SCHEMA, config_hash, current_environment, validate_config
from research.m510a.evaluate import evaluate, model_row
from research.m510a.report import (BOOTSTRAP_NOT_CONFIGURED, REPORT_SCHEMA, REQUIRES_FITTING_DEPENDENCIES, build_envelope, build_payload,
                                   payload_bytes, replicate, report_hash, verify_envelope)
from research.m510a.rows import ROW_SCHEMA, ReplicationError, dataset_bytes, dataset_fingerprint, dataset_payload, feature_names, validate_rows

UTC = timezone.utc
K0 = datetime(2027, 3, 1, 18, 0, tzinfo=UTC)
NUM = ["away_shots_total_for", "home_shots_total_for"]
FLAGS = [f"{c}_missing" for c in NUM]
ENVIRONMENT = {"python": "3.x-synthetic", "system": "synthetic"}


def _row(i=0, **overrides):
    kickoff = K0 + timedelta(hours=13 * i)
    return {"row_id": f"r{i:04d}", "label": "HDA"[i % 3], "kickoff_at": kickoff, "cutoff": kickoff - timedelta(hours=1), "competition_id": 1,
            "features": {"home_shots_total_for": 12.0, "away_shots_total_for": 9.5}, **overrides}


def rows(n=60, seed=7):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        features = {"home_shots_total_for": round(rng.gauss(12, 3), 3), "away_shots_total_for": round(rng.gauss(10, 3), 3)}
        if i % 9 == 4:
            features["away_shots_total_for"] = None  # ausente explícito
        out.append(_row(i, label=rng.choice("HHDA"), competition_id=99 if i % 17 == 5 else rng.choice((1, 2)), features=features))
    return out


def _meta(kind):
    return {"manifest_version": 1, "model_kind": kind, "model_version": f"{kind}-synthetic", "feature_contract": "team_recent_form_v1",
            "classes": ["H", "D", "A"], "training_id": "synthetic", "frozen_at": "2027-01-01T00:00:00+00:00", "code_version": "test",
            "dependencies": {}, "dataset_fingerprint": "synthetic"}


def manifests():
    b0 = seal({**_meta("B0"), "hyperparameters": {}, "params": {"probs": [0.5, 0.3, 0.2]}})
    b1 = seal({**_meta("B1"), "hyperparameters": {"alpha": 200.0}, "params": {"base": [0.5, 0.3, 0.2], "by_competition": {"1": [0.6, 0.25, 0.15], "2": [0.4, 0.3, 0.3]}}})
    b2 = seal({**_meta("B2"), "hyperparameters": {"C": 0.01}, "params": {
        "numeric_columns": NUM, "flag_columns": FLAGS, "competitions": [1, 2], "imputer_medians": [10.0, 12.0],
        "scaler_mean": [10.0, 12.0], "scaler_scale": [3.0, 3.0], "model_classes": ["A", "D", "H"],
        "coef": [[0.3, -0.3, 0.1, 0.0, 0.0, 0.1], [0.0, 0.0, 0.0, 0.0, 0.0, 0.0], [-0.3, 0.3, -0.1, 0.0, 0.1, 0.0]], "intercept": [-0.4, -0.5, 0.1]}})
    return {"B0": b0, "B1": b1, "B2": b2}


def config(**overrides):
    base = {"config_schema": CONFIG_SCHEMA, "models": ["B0", "B1", "B2"], "seed": 58, "environment": dict(ENVIRONMENT),
            "evaluation": {"metrics": ["log_loss", "brier", "rps", "ece_mean"], "calibration_bins": 10, "bootstrap": {"n_boot": 40, "alpha": 0.05}}}
    return {**base, **overrides}


def pipeline(audit=None):
    """Ejecución completa con las entradas sintéticas fijas (también la usa el subproceso aislado)."""
    return replicate(rows(), manifests(), config(), audit)


# --- A. Contrato de fila -------------------------------------------------------------------------


def test_valid_rows_are_accepted_normalized_and_sorted_by_row_id():
    given = [_row(2), _row(0, features={"home_shots_total_for": 12, "away_shots_total_for": None}), _row(1)]
    snapshot = copy.deepcopy(given)
    valid = validate_rows(given)
    assert [r["row_id"] for r in valid] == ["r0000", "r0001", "r0002"] and given == snapshot  # no modifica la entrada
    assert valid[0]["features"] == {"home_shots_total_for": 12.0, "away_shots_total_for": None}
    assert type(valid[0]["features"]["home_shots_total_for"]) is float  # 12 y 12.0 son el mismo contenido
    assert validate_rows(tuple(given)) == valid and validate_rows(list(valid)) == valid  # idempotente
    assert feature_names(given) == ("away_shots_total_for", "home_shots_total_for")


@pytest.mark.parametrize("field", ["row_id", "label", "kickoff_at", "cutoff", "competition_id", "features"])
def test_missing_required_field_is_rejected(field):
    with pytest.raises(ReplicationError, match=f"faltan campos .*{field}"):
        validate_rows([{k: v for k, v in _row().items() if k != field}])


@pytest.mark.parametrize("row_id", ["", " ", "r 1 ", "partido-ñ", "r\n1", "r\t", 7, None, 1.5, b"r1", ["r1"]],
                         ids=["empty", "blank", "padded", "non-ascii", "newline", "tab", "int", "null", "float", "bytes", "list"])
def test_row_id_must_be_nonempty_ascii_text(row_id):
    with pytest.raises(ReplicationError, match="row_id"):
        validate_rows([_row(row_id=row_id)])


def test_duplicate_row_id_is_rejected():
    with pytest.raises(ReplicationError, match="row_id duplicado: 'r0001'"):
        validate_rows([_row(0), _row(1), _row(1, label="A")])


@pytest.mark.parametrize("field", ["kickoff_at", "cutoff"])
@pytest.mark.parametrize("value, message", [
    (datetime(2027, 3, 1, 17, 0), "instante sin zona horaria"), (datetime(2027, 3, 1, 19, 0, tzinfo=timezone(timedelta(hours=2))), "el instante tiene que estar en UTC"),
    ("2027-03-01T17:00:00+00:00", "se esperaba un instante"), (None, "se esperaba un instante"), (1803920400, "se esperaba un instante"),
], ids=["naive", "non-utc-offset", "text", "null", "epoch"])
def test_instants_must_be_utc_aware(field, value, message):
    with pytest.raises(ReplicationError, match=f"{field}: {message}"):
        validate_rows([_row(**{field: value})])


def test_cutoff_after_kickoff_is_rejected_and_cutoff_at_kickoff_is_coherent():
    with pytest.raises(ReplicationError, match="el corte es posterior al kickoff"):
        validate_rows([_row(cutoff=K0 + timedelta(microseconds=1))])
    assert validate_rows([_row(cutoff=K0)])[0]["cutoff"] == K0


@pytest.mark.parametrize("label", ["X", "h", "", "HD", None, 1, True, ["H"]], ids=["unknown", "case", "empty", "two", "null", "int", "bool", "list"])
def test_invalid_label_is_rejected(label):
    with pytest.raises(ReplicationError, match="etiqueta no válida"):
        validate_rows([_row(label=label)])


@pytest.mark.parametrize("competition_id", [None, "1", 1.0, True], ids=["null", "text", "float", "bool"])
def test_competition_id_must_be_an_integer(competition_id):
    with pytest.raises(ReplicationError, match="competition_id tiene que ser un entero"):
        validate_rows([_row(competition_id=competition_id)])


@pytest.mark.parametrize("features, message", [
    (None, "features tiene que ser un objeto"), ([1.0, 2.0], "features tiene que ser un objeto"),
    ({"home_shots_total_for": "12"}, "tipo no admitido: str"), ({"home_shots_total_for": True}, "tipo no admitido: bool"),
    ({"home_shots_total_for": [12.0]}, "tipo no admitido: list"), ({"home_shots_total_for": {"v": 1}}, "tipo no admitido: dict"),
    ({"home_shots_total_for": float("nan")}, "no finita"), ({"home_shots_total_for": float("inf")}, "no finita"),
    ({"home_shots_total_for": float("-inf")}, "no finita"), ({"home_shots_total_for": 10 ** 400}, "fuera de rango"),
    ({"": 1.0}, "nombre de feature"), ({1: 1.0}, "nombre de feature"), ({"tiros_ñ": 1.0}, "nombre de feature"),
    ({"home_shots_total_for_missing": 1.0}, "sufijo reservado"),
], ids=["null", "list", "text", "bool", "list-value", "mapping-value", "nan", "inf", "-inf", "overflow", "empty-name", "int-name", "non-ascii-name",
        "reserved-suffix"])
def test_invalid_features_are_rejected(features, message):
    with pytest.raises(ReplicationError, match=message):
        validate_rows([_row(features=features)])


def test_rows_must_declare_the_same_features_and_no_unknown_fields():
    with pytest.raises(ReplicationError, match="sus features no son las del resto"):
        validate_rows([_row(0), _row(1, features={"home_shots_total_for": 1.0})])  # una feature ausente no se rellena
    with pytest.raises(ReplicationError, match="campos no admitidos"):
        validate_rows([_row(fixture_id=123)])  # sin identidad implícita de BD
    for bad in (None, "rows", {"r": _row()}, (r for r in [_row()]), []):
        with pytest.raises(ReplicationError):
            validate_rows(bad)
    with pytest.raises(ReplicationError, match="no es un objeto"):
        validate_rows(["r0000"])


# --- B. Determinismo del dataset -----------------------------------------------------------------


def test_dataset_fingerprint_and_bytes_do_not_depend_on_input_order():
    base = rows()
    reference, reference_bytes = dataset_fingerprint(base), dataset_bytes(base)
    assert reference == hashlib.sha256(reference_bytes).hexdigest() == sha256_hex(dataset_payload(base)) and len(reference) == 64
    rng = random.Random(1)
    for _ in range(10):
        shuffled = base[:]
        rng.shuffle(shuffled)
        assert dataset_fingerprint(shuffled) == reference and dataset_bytes(shuffled) == reference_bytes
        assert [r["row_id"] for r in validate_rows(shuffled)] == sorted(r["row_id"] for r in base)
    assert dataset_bytes(rows()) == reference_bytes  # otra construcción de las mismas filas: los mismos bytes
    payload = json.loads(reference_bytes)
    assert payload["row_schema"] == ROW_SCHEMA and payload["rows"][0]["kickoff_at"] == "2027-03-01T18:00:00+00:00"


CHANGES = {
    "row-id": lambda r: r.update(row_id="r9999"), "label": lambda r: r.update(label="A" if r["label"] != "A" else "H"),
    "kickoff": lambda r: r.update(kickoff_at=r["kickoff_at"] + timedelta(microseconds=1)),
    "cutoff": lambda r: r.update(cutoff=r["cutoff"] - timedelta(seconds=1)), "competition": lambda r: r.update(competition_id=3),
    "feature-value": lambda r: r["features"].update(home_shots_total_for=r["features"]["home_shots_total_for"] + 1e-9),
    "value-to-null": lambda r: r["features"].update(home_shots_total_for=None),
    "zero-is-not-null": lambda r: r["features"].update(home_shots_total_for=0.0),
}


@pytest.mark.parametrize("name", CHANGES)
def test_meaningful_row_content_changes_the_fingerprint(name):
    base, changed = rows(), rows()
    CHANGES[name](changed[3])
    assert dataset_fingerprint(changed) != dataset_fingerprint(base)


def test_equivalent_numbers_and_added_or_removed_rows():
    base = rows()
    as_int = copy.deepcopy(base)
    as_int[0]["features"]["home_shots_total_for"] = 12
    as_float = copy.deepcopy(base)
    as_float[0]["features"]["home_shots_total_for"] = 12.0
    assert dataset_fingerprint(as_int) == dataset_fingerprint(as_float)  # el mismo contenido
    assert len({dataset_fingerprint(base), dataset_fingerprint(base[:-1]), dataset_fingerprint(base + [_row(999)])}) == 3
    null_first, zero_first = copy.deepcopy(base), copy.deepcopy(base)
    null_first[0]["features"]["home_shots_total_for"], zero_first[0]["features"]["home_shots_total_for"] = None, 0
    assert dataset_fingerprint(null_first) != dataset_fingerprint(zero_first)  # nulo y cero no se confunden


# --- C. Identidad de la configuración ------------------------------------------------------------


def test_config_hash_is_stable_across_key_and_list_order():
    reference = config_hash(config())
    reordered = {"environment": dict(reversed(list(ENVIRONMENT.items()))), "seed": 58, "models": ["B2", "B0", "B1"],
                 "evaluation": {"bootstrap": {"alpha": 0.05, "n_boot": 40}, "calibration_bins": 10, "metrics": ["ece_mean", "rps", "brier", "log_loss"]},
                 "config_schema": CONFIG_SCHEMA}
    assert config_hash(reordered) == reference == sha256_hex(validate_config(config())) and len(reference) == 64
    assert validate_config(reordered) == validate_config(config()) and validate_config(reordered)["models"] == ["B0", "B1", "B2"]
    assert validate_config(validate_config(config())) == validate_config(config())


CONFIG_CHANGES = {
    "models": lambda c: c.update(models=["B0", "B1"]), "seed": lambda c: c.update(seed=59),
    "metrics": lambda c: c["evaluation"].update(metrics=["log_loss"]), "bins": lambda c: c["evaluation"].update(calibration_bins=5),
    "n-boot": lambda c: c["evaluation"]["bootstrap"].update(n_boot=41), "alpha": lambda c: c["evaluation"]["bootstrap"].update(alpha=0.1),
    "no-bootstrap": lambda c: c["evaluation"].update(bootstrap=None), "environment": lambda c: c["environment"].update(python="3.y-synthetic"),
    "environment-key": lambda c: c["environment"].update(numpy="2.5.3"),
}


@pytest.mark.parametrize("name", CONFIG_CHANGES)
def test_meaningful_config_change_changes_the_hash(name):
    changed = copy.deepcopy(config())
    CONFIG_CHANGES[name](changed)
    assert config_hash(changed) != config_hash(config())


BAD_CONFIGS = {
    "not-an-object": lambda c: "config", "missing-seed": lambda c: c.pop("seed") and None, "unknown-field": lambda c: c.update(generated_at="2027-01-01T00:00:00Z"),
    "schema": lambda c: c.update(config_schema="m510a.config.v2"), "models-empty": lambda c: c.update(models=[]),
    "models-unknown": lambda c: c.update(models=["B0", "B3"]), "models-repeated": lambda c: c.update(models=["B0", "B0"]),
    "models-text": lambda c: c.update(models="B0"), "metric-unknown": lambda c: c["evaluation"].update(metrics=["accuracy"]),
    "metrics-empty": lambda c: c["evaluation"].update(metrics=[]), "bins-zero": lambda c: c["evaluation"].update(calibration_bins=0),
    "bins-float": lambda c: c["evaluation"].update(calibration_bins=10.0), "evaluation-extra": lambda c: c["evaluation"].update(threshold=0.6),
    "seed-negative": lambda c: c.update(seed=-1), "seed-bool": lambda c: c.update(seed=True), "seed-float": lambda c: c.update(seed=58.0),
    "seed-nan": lambda c: c.update(seed=float("nan")), "alpha-nan": lambda c: c["evaluation"]["bootstrap"].update(alpha=float("nan")),
    "alpha-inf": lambda c: c["evaluation"]["bootstrap"].update(alpha=float("inf")), "alpha-zero": lambda c: c["evaluation"]["bootstrap"].update(alpha=0.0),
    "alpha-one": lambda c: c["evaluation"]["bootstrap"].update(alpha=1.0), "alpha-int": lambda c: c["evaluation"]["bootstrap"].update(alpha=0),
    "n-boot-one": lambda c: c["evaluation"]["bootstrap"].update(n_boot=1), "bootstrap-extra": lambda c: c["evaluation"]["bootstrap"].update(started_at="now"),
    "environment-empty": lambda c: c.update(environment={}), "environment-number": lambda c: c["environment"].update(python=3.12),
    "environment-windows-path": lambda c: c["environment"].update(executable="C:\\venv\\python.exe"),
    "environment-posix-path": lambda c: c["environment"].update(executable="/usr/bin/python"),
    "environment-non-ascii": lambda c: c["environment"].update(host="máquina"),
}


@pytest.mark.parametrize("name", BAD_CONFIGS)
def test_invalid_config_is_rejected(name):
    bad = copy.deepcopy(config())
    replaced = BAD_CONFIGS[name](bad)
    with pytest.raises(ReplicationError):
        config_hash(bad if replaced is None else replaced)


def test_current_environment_has_no_volatile_values():
    env = current_environment()
    assert set(env) == {"python", "implementation", "system", "machine"} and env == current_environment()
    assert validate_config(config(environment=env))["environment"] == dict(sorted(env.items()))


# --- D. Evaluación desde manifiestos congelados --------------------------------------------------


def test_predictions_come_from_frozen_models_and_are_aligned_by_row_id(monkeypatch):
    calls = []

    class Spy(FrozenModel):
        def predict(self, row):
            calls.append((self.kind, dict(row)))
            return super().predict(row)

    monkeypatch.setattr(evaluate_module, "FrozenModel", Spy)
    base = rows()
    shuffled = base[:]
    random.Random(3).shuffle(shuffled)
    result = evaluate(shuffled, manifests(), config())
    ordered = validate_rows(base)
    assert result.rows == ordered and list(result.models) == ["B0", "B1", "B2"] == list(result.predictions)
    assert len(calls) == 3 * len(base) and [kind for kind, _ in calls] == ["B0"] * 60 + ["B1"] * 60 + ["B2"] * 60
    for name, manifest in manifests().items():  # lo mismo que da FrozenModel fila a fila, en orden de row_id
        assert result.predictions[name] == [FrozenModel(manifest).predict(model_row(r)) for r in ordered]
        assert result.models[name].sha == manifest["manifest_sha256"]
    assert all(len(p) == len(base) for p in result.predictions.values())  # ninguna fila omitida
    assert evaluate(base, manifests(), config()).predictions == result.predictions  # el orden de entrada no importa


def test_model_row_derives_the_missing_indicator_from_explicit_nulls():
    row = validate_rows([_row(features={"home_shots_total_for": 14.0, "away_shots_total_for": None})])[0]
    assert model_row(row) == {"competition_id": 1, "home_shots_total_for": 14.0, "home_shots_total_for_missing": False,
                              "away_shots_total_for": None, "away_shots_total_for_missing": True}
    b2 = FrozenModel(manifests()["B2"])
    imputed = model_row(validate_rows([_row(features={"home_shots_total_for": 14.0, "away_shots_total_for": 10.0})])[0])
    assert b2.predict(model_row(row)) != b2.predict(imputed)  # el indicador distingue ausente de "igual a la mediana"
    assert b2.predict({**model_row(row), "away_shots_total_for_missing": False}) == b2.predict(imputed)  # y el valor ausente se imputa (10.0)


def test_manifests_are_accepted_as_objects_or_canonical_text_and_models_can_be_selected():
    as_text = {k: canonical_json(m) for k, m in manifests().items()}
    assert evaluate(rows(), as_text, config()).predictions == evaluate(rows(), manifests(), config()).predictions
    only_b0 = evaluate(rows(), {"B0": manifests()["B0"]}, config(models=["B0"]))
    assert list(only_b0.predictions) == ["B0"] and only_b0.predictions["B0"] == [[0.5, 0.3, 0.2]] * 60


def _tampered(m):
    return {**m, "hyperparameters": {"C": 1.0}}


BAD_MANIFESTS = {
    "tampered": (lambda ms: ms.update(B2=_tampered(ms["B2"])), ManifestError, "huella"),
    "invalid-probabilities": (lambda ms: ms.update(B0=seal({**_meta("B0"), "hyperparameters": {}, "params": {"probs": [1.5, -0.25, -0.25]}})), ManifestError, "vector de probabilidades"),
    "zero-scale": (lambda ms: ms.update(B2=seal({**{k: v for k, v in ms["B2"].items() if k != "manifest_sha256"},
                                                 "params": {**ms["B2"]["params"], "scaler_scale": [3.0, 0.0]}})), ManifestError, "escala nula"),
    "not-an-object": (lambda ms: ms.update(B1=["B1"]), ManifestError, "no es un objeto"),
    "text-without-fingerprint": (lambda ms: ms.update(B1="{}"), ManifestError, "huella"),
    "garbage-text": (lambda ms: ms.update(B1="{not json"), json.JSONDecodeError, None),  # como manifest.load: el texto roto no se carga
    "missing-model": (lambda ms: ms.pop("B1"), ReplicationError, "exactamente los manifiestos"),
    "extra-model": (lambda ms: ms.update(B3=ms["B0"]), ReplicationError, "exactamente los manifiestos"),
    "wrong-kind": (lambda ms: ms.update(B1=ms["B0"]), ReplicationError, "presentado como B1 es de tipo B0"),
    "unknown-columns": (lambda ms: ms.update(B2=seal({**{k: v for k, v in ms["B2"].items() if k != "manifest_sha256"}, "params": {
        **ms["B2"]["params"], "numeric_columns": ["home_shots_total_for", "home_expected_goals_for"]}})), ReplicationError, "columnas que el dataset no declara"),
}


@pytest.mark.parametrize("name", BAD_MANIFESTS)
def test_malformed_or_mismatched_manifests_are_rejected(name):
    change, error, message = BAD_MANIFESTS[name]
    ms = manifests()
    change(ms)
    with pytest.raises(error, match=message):
        evaluate(rows(), ms, config())
    with pytest.raises(error, match=message):
        replicate(rows(), ms, config())  # sin informe parcial


def test_manifests_container_must_be_a_mapping():
    for bad in (None, list(manifests().values()), "B0"):
        with pytest.raises(ReplicationError, match="exactamente los manifiestos"):
            evaluate(rows(), bad, config())


def test_a_prediction_that_is_not_a_probability_vector_is_an_error_not_an_omission(monkeypatch):
    class Broken(FrozenModel):
        def predict(self, row):
            return [0.5, 0.5] if self.kind == "B1" and row["competition_id"] == 99 else super().predict(row)

    monkeypatch.setattr(evaluate_module, "FrozenModel", Broken)
    with pytest.raises(ReplicationError, match="B1: predicción no válida para la fila 'r0005'"):
        evaluate(rows(), manifests(), config())


# --- E. Informe ----------------------------------------------------------------------------------


def test_report_identities_population_and_schema():
    base = rows()
    payload = build_payload(evaluate(base, manifests(), config()))
    assert sorted(payload) == ["config", "dataset", "model_set_sha256", "models", "paired_log_loss_diff", "population", "report_schema",
                               "results", "unavailable"] and "report_hash" not in payload
    assert payload["report_schema"] == REPORT_SCHEMA
    assert payload["dataset"] == {"row_schema": ROW_SCHEMA, "fingerprint": dataset_fingerprint(base), "rows": 60}
    assert payload["config"] == {"hash": config_hash(config()), "config": validate_config(config())}
    assert payload["config"]["config"]["seed"] == 58 and payload["config"]["config"]["environment"] == ENVIRONMENT
    assert payload["models"] == {k: {"model_kind": k, "model_version": f"{k}-synthetic", "manifest_sha256": m["manifest_sha256"]} for k, m in manifests().items()}
    assert payload["model_set_sha256"] == sha256_hex({k: m["manifest_sha256"] for k, m in manifests().items()})
    labels = [r["label"] for r in validate_rows(base)]
    assert payload["population"] == {"n": 60, "row_ids": [f"r{i:04d}" for i in range(60)], "class_balance": metrics.class_balance(labels),
                                     "excluded": {"n": 0, "by_reason": {}}}
    assert {k: v["n"] for k, v in payload["results"].items()} == {"B0": 60, "B1": 60, "B2": 60}
    assert {k: v["unseen_competition_rows"] for k, v in payload["results"].items()} == {"B0": 0, "B1": 4, "B2": 4}
    assert sorted(payload["paired_log_loss_diff"]) == ["B1-B0", "B2-B0", "B2-B1"]
    assert payload["unavailable"] == {"calibration_slopes": REQUIRES_FITTING_DEPENDENCIES}


def test_report_metric_values_are_the_existing_metrics_on_the_frozen_predictions():
    result = evaluate(rows(), manifests(), config())
    payload = build_payload(result)
    labels = [r["label"] for r in result.rows]
    b0 = payload["results"]["B0"]["metrics"]
    constant = {"H": 0.5, "D": 0.3, "A": 0.2}  # B0 es constante: sus métricas se pueden calcular a mano
    assert b0["log_loss"] == pytest.approx(-sum(math.log(constant[y]) for y in labels) / 60, abs=1e-15)
    assert b0["brier"] == pytest.approx(sum(sum((constant[c] - (c == y)) ** 2 for c in "HDA") for y in labels) / 60, abs=1e-15)
    for name, probs in result.predictions.items():
        entry = payload["results"][name]
        assert entry["metrics"] == {"log_loss": metrics.log_loss(probs, labels), "brier": metrics.brier(probs, labels), "rps": metrics.rps(probs, labels),
                                    "ece_mean": metrics.calibration(probs, labels, 10)["ece_mean"]}
        assert entry["predictions_sha256"] == sha256_hex([[r["row_id"], p] for r, p in zip(result.rows, probs)])
        estimate, low, high = entry["log_loss_ci95"]
        assert estimate == entry["metrics"]["log_loss"] and low <= high
    diff = payload["paired_log_loss_diff"]["B2-B1"]
    assert diff["estimate"] == payload["results"]["B2"]["metrics"]["log_loss"] - payload["results"]["B1"]["metrics"]["log_loss"]
    assert diff["ci95"][0] == diff["estimate"] and diff["ci95"][1] <= diff["ci95"][2]


def test_unavailable_results_are_declared_not_substituted():
    selected = {k: m for k, m in manifests().items() if k != "B1"}
    payload = build_payload(evaluate(rows(), selected, config(models=["B0", "B2"], evaluation={"metrics": ["log_loss"], "calibration_bins": 10, "bootstrap": None})))
    assert payload["unavailable"] == {"calibration_slopes": REQUIRES_FITTING_DEPENDENCIES, "log_loss_ci95": BOOTSTRAP_NOT_CONFIGURED,
                                      "paired_log_loss_diff_ci95": BOOTSTRAP_NOT_CONFIGURED}
    assert all(sorted(v) == ["metrics", "n", "predictions_sha256", "unseen_competition_rows"] and sorted(v["metrics"]) == ["log_loss"]
               for v in payload["results"].values())
    assert payload["paired_log_loss_diff"] == {"B2-B0": {"estimate": payload["results"]["B2"]["metrics"]["log_loss"] - payload["results"]["B0"]["metrics"]["log_loss"]}}


def test_two_executions_give_byte_identical_payloads_and_the_same_hash():
    first, second = pipeline(), pipeline()
    assert payload_bytes(first["payload"]) == payload_bytes(second["payload"]) and first["report_hash"] == second["report_hash"]
    shuffled = rows()
    random.Random(9).shuffle(shuffled)
    reordered_inputs = replicate(shuffled, {k: canonical_json(m) for k, m in reversed(list(manifests().items()))},
                                 {k: v for k, v in reversed(list(config(models=["B2", "B1", "B0"]).items()))})
    assert payload_bytes(reordered_inputs["payload"]) == payload_bytes(first["payload"])  # mismas entradas, otro orden de presentación
    raw = payload_bytes(first["payload"])
    assert raw == canonical_json(first["payload"]).encode("utf-8") and json.loads(raw) == first["payload"]  # carga útil: solo tipos JSON
    assert raw.decode("utf-8") == json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False)  # claves ordenadas, sin espacios


def test_report_hash_is_computed_over_the_payload_only():
    envelope = pipeline()
    assert sorted(envelope) == ["audit", "payload", "report_hash"] and envelope["audit"] == {}
    assert envelope["report_hash"] == hashlib.sha256(payload_bytes(envelope["payload"])).hexdigest() == report_hash(envelope["payload"])
    assert "report_hash" not in canonical_json(envelope["payload"])  # la huella no está dentro de lo que huella
    verify_envelope(envelope)
    with pytest.raises(ReplicationError, match="no puede contener report_hash"):
        report_hash({**envelope["payload"], "report_hash": envelope["report_hash"]})
    tampered = copy.deepcopy(envelope)
    tampered["payload"]["results"]["B2"]["metrics"]["log_loss"] += 1e-12
    with pytest.raises(ReplicationError, match="no coincide con su carga útil"):
        verify_envelope(tampered)
    for bad in (None, {"payload": envelope["payload"]}, {**envelope, "extra": 1}):
        with pytest.raises(ReplicationError, match="forma inesperada"):
            verify_envelope(bad)


def test_volatile_audit_metadata_stays_outside_the_hashed_payload():
    a = pipeline({"generated_at": "2027-05-01T10:00:00+00:00", "host": "builder-a", "output_path": "C:\\reports\\a.json"})
    b = pipeline({"generated_at": "2027-06-30T23:59:59+00:00", "host": "builder-b", "output_path": "/srv/reports/b.json"})
    assert a["report_hash"] == b["report_hash"] and payload_bytes(a["payload"]) == payload_bytes(b["payload"])
    assert a != b and canonical_json(a) != canonical_json(b)  # el sobre NO es idéntico: lleva la auditoría
    text = canonical_json(a["payload"])
    assert not any(s in text for s in ("generated_at", "builder-a", "reports", "2027-05-01", str(Path.cwd()), Path.cwd().as_posix()))
    verify_envelope(a)
    verify_envelope({**a, "audit": {"generated_at": "otro instante"}})  # cambiar la auditoría no toca la huella
    assert build_envelope(a["payload"], {"host": "x"})["report_hash"] == a["report_hash"]


def test_a_change_in_any_frozen_input_changes_the_report_hash():
    reference = pipeline()["report_hash"]
    changed_rows = rows()
    changed_rows[3]["label"] = "A" if changed_rows[3]["label"] != "A" else "H"
    other_b0 = {**manifests(), "B0": seal({**_meta("B0"), "hyperparameters": {}, "params": {"probs": [0.5, 0.25, 0.25]}})}
    hashes = {reference, replicate(changed_rows, manifests(), config())["report_hash"], replicate(rows(), other_b0, config())["report_hash"],
              replicate(rows(), manifests(), config(seed=59))["report_hash"],
              replicate(rows(), manifests(), config(environment={**ENVIRONMENT, "python": "3.y-synthetic"}))["report_hash"]}
    assert len(hashes) == 5


# --- F. Aislamiento ------------------------------------------------------------------------------

ISOLATED = """
import socket, sys

def refuse(*args, **kwargs):
    raise AssertionError("intento de conexión de red")

socket.socket.connect = socket.socket.connect_ex = socket.create_connection = refuse
from research.tests.test_m510a_replication import pipeline
envelope = pipeline()
loaded = [m for m in sys.modules if m.split(".")[0] in ("app", "sqlalchemy", "psycopg", "numpy", "sklearn", "scipy", "alembic")]
print(envelope["report_hash"], ",".join(loaded))
"""


def test_end_to_end_run_in_a_clean_process_needs_no_database_no_network_and_gives_the_same_hash():
    backend = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(backend))
    out = subprocess.run([sys.executable, "-c", ISOLATED], cwd=backend, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    report_sha, loaded = out.stdout.strip().split(" ") if " " in out.stdout.strip() else (out.stdout.strip(), "")
    assert loaded == ""  # ni la app, ni el driver de BD, ni numpy/scikit-learn
    assert report_sha == pipeline()["report_hash"]  # otro proceso, mismo entorno: la misma huella
