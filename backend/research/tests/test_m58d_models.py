"""Modelos exploratorios M5.8D (venv de investigación): logit ordinal y calibración por clase."""

import numpy as np

from research.m58 import metrics
from research.m58.labels import CLASSES
from research.m58d import models
from research.tests.test_m58_models import _rows


def test_ordinal_logit_probabilities_and_ordering():
    train, test = _rows(1500), _rows(400, seed=11)
    m = models.OrdinalLogit(1.0).fit(train)
    probs = m.predict(test)
    assert all(abs(sum(p) - 1) < 1e-9 and min(p) > 0 for p in probs)
    assert m.t2 > m.t1  # umbrales ordenados
    b0 = metrics.log_loss([[sum(r["label"] == c for r in train) / len(train) for c in CLASSES]] * len(test), [r["label"] for r in test])
    assert metrics.log_loss(probs, [r["label"] for r in test]) < b0  # encuentra la señal sintética


def test_ordinal_logit_is_deterministic_and_regularized():
    train, test = _rows(800), _rows(200, seed=12)
    a = models.OrdinalLogit(10.0).fit(train)
    assert a.predict(test) == models.OrdinalLogit(10.0).fit(train).predict(test)
    strong = models.OrdinalLogit(1e6).fit(train)
    assert np.abs(strong.beta).max() < np.abs(a.beta).max()


def test_vector_calibrator_is_fitted_only_on_its_own_predictions():
    cal = _rows(600, seed=13)
    base = [[0.6, 0.2, 0.2]] * len(cal)
    calibrator = models.VectorCalibrator().fit(np.array(base), [r["label"] for r in cal])
    out = calibrator.transform(np.array(base[:5]))
    observed = [sum(r["label"] == c for r in cal) / len(cal) for c in CLASSES]
    assert np.allclose(out[0], observed, atol=0.02)  # con una entrada constante aprende la frecuencia
    assert all(abs(sum(p) - 1) < 1e-9 for p in out)
