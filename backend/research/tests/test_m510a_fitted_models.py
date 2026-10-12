"""M5.10A con artefactos creados por las APIs de investigación existentes: modelos de M5.8 ajustados
sobre filas sintéticas, congelados con research.m59.freeze y evaluados solo desde sus manifiestos.
    python -m pytest research/tests/test_m510a_fitted_models.py -q   (venv de investigación: numpy / scikit-learn)
Sin BD ni datos reales. El ajuste ocurre aquí, en el test: el evaluador de M5.10A no ajusta nada."""

import random
from datetime import datetime, timedelta, timezone

from research.m58 import models as m58
from research.m59.canonical import canonical_json
from research.m59.freeze import freeze_b0, freeze_b1, freeze_b2
from research.m510a.config import CONFIG_SCHEMA
from research.m510a.evaluate import evaluate, model_row
from research.m510a.report import payload_bytes, replicate, verify_envelope
from research.m510a.rows import dataset_fingerprint, validate_rows

UTC = timezone.utc
META = dict(model_version="synthetic-1", training_id="synthetic", frozen_at=datetime(2027, 1, 1, tzinfo=UTC), code_version="test",
            dependencies={"scikit-learn": "synthetic"})
CONFIG = {"config_schema": CONFIG_SCHEMA, "models": ["B0", "B1", "B2"], "seed": 58, "environment": {"python": "synthetic"},
          "evaluation": {"metrics": ["log_loss", "brier", "rps", "ece_mean"], "calibration_bins": 10, "bootstrap": {"n_boot": 20, "alpha": 0.05}}}


def _rows(n, seed, prefix, start):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        features = {c: round(rng.gauss(10, 3), 4) for c in m58.NUMERIC}
        if rng.random() < 0.2:
            features["home_expected_goals_for"] = None
        edge = (features["home_shots_total_for"] - features["away_shots_total_for"]) / 3
        u, p_home = rng.random(), 0.75 / (1 + 2.718281828 ** -edge)
        kickoff = start + timedelta(hours=7 * i)
        out.append({"row_id": f"{prefix}-{i:05d}", "label": "H" if u < p_home else ("D" if u < p_home + 0.25 else "A"), "kickoff_at": kickoff,
                    "cutoff": kickoff - timedelta(hours=1), "competition_id": 999 if i == 3 else rng.choice((1, 2, 3)), "features": features})
    return out


def _fit_rows(rows):
    """Filas con la forma que ajustan los modelos de M5.8: la fila del modelo más su etiqueta."""
    return [{**model_row(r), "label": r["label"]} for r in validate_rows(rows)]


def _frozen(train):
    fingerprint = dataset_fingerprint(train)
    fit = _fit_rows(train)
    b0, b1, b2 = m58.ClassFrequency().fit(fit), m58.CompetitionFrequency(200).fit(fit), m58.LogisticB2(0.01).fit(fit)
    manifests = {"B0": freeze_b0(b0, dataset_fingerprint=fingerprint, **META), "B1": freeze_b1(b1, dataset_fingerprint=fingerprint, **META),
                 "B2": freeze_b2(b2, m58.NUMERIC, m58.FLAGS, dataset_fingerprint=fingerprint, **META)}
    return {"B0": b0, "B1": b1, "B2": b2}, {k: canonical_json(m) for k, m in manifests.items()}  # manifiestos como texto JSON


def test_frozen_manifests_reproduce_the_fitted_models_without_refitting():
    train = _rows(900, 11, "train", datetime(2026, 1, 1, tzinfo=UTC))
    evaluation_rows = _rows(240, 12, "eval", datetime(2027, 1, 1, tzinfo=UTC))
    fitted, manifests = _frozen(train)
    result = evaluate(evaluation_rows, manifests, CONFIG)
    for name, model in fitted.items():
        expected = model.predict(_fit_rows(evaluation_rows))  # el modelo ajustado, sobre las mismas filas y en el mismo orden
        assert max(abs(a - b) for pe, pg in zip(expected, result.predictions[name]) for a, b in zip(pe, pg)) < 1e-12
    assert any(r["features"]["home_expected_goals_for"] is None for r in result.rows)  # con valores ausentes
    assert result.rows[3]["competition_id"] == 999  # y una competición no vista en el ajuste


def test_report_from_frozen_manifests_is_byte_identical_across_executions_and_refits():
    train = _rows(600, 21, "train", datetime(2026, 1, 1, tzinfo=UTC))
    evaluation_rows = _rows(180, 22, "eval", datetime(2027, 1, 1, tzinfo=UTC))
    _, manifests = _frozen(train)
    first, second = replicate(evaluation_rows, manifests, CONFIG), replicate(evaluation_rows, manifests, CONFIG)
    assert payload_bytes(first["payload"]) == payload_bytes(second["payload"]) and first["report_hash"] == second["report_hash"]
    verify_envelope(first)
    assert first["payload"]["dataset"]["fingerprint"] == dataset_fingerprint(evaluation_rows) != dataset_fingerprint(train)
    _, again = _frozen(train)  # volver a ajustar y congelar las mismas filas da los mismos manifiestos
    assert again == manifests and replicate(evaluation_rows, again, CONFIG)["report_hash"] == first["report_hash"]
