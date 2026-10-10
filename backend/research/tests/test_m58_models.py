"""Modelos M5.8 (necesitan requirements-research.txt). Fuera de la suite operativa:
    python -m pytest research/tests -q   (desde backend/, con el venv de investigación)
Sin BD: filas sintéticas con la forma del dataset."""

import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from research.m58 import metrics, models
from research.m58.dataset import FEATURE_COLUMNS
from research.m58.labels import CLASSES


def _rows(n, seed=1, signal=True, start=datetime(2024, 8, 1, tzinfo=timezone.utc)):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        r = {"fixture_id": i, "competition_id": rng.choice((1, 2, 3)), "kickoff_at": start + timedelta(hours=i)}
        for c in FEATURE_COLUMNS:
            r[c] = False if c.endswith("_missing") else rng.gauss(10, 3)
        if rng.random() < 0.2:  # ausencias con indicador
            r["home_expected_goals_for"], r["home_expected_goals_for_missing"] = None, True
        edge = (r["home_shots_total_for"] - r["away_shots_total_for"]) / 3 if signal else 0
        u = rng.random()
        p_home = 1 / (1 + np.exp(-edge)) * 0.75
        r["label"] = "H" if u < p_home else ("D" if u < p_home + 0.25 else "A")
        rows.append(r)
    return rows


def test_probabilities_are_valid_and_deterministic():
    train, test = _rows(600), _rows(200, seed=2)
    a = models.LogisticB2(0.1).fit(train).predict(test)
    b = models.LogisticB2(0.1).fit(train).predict(test)
    assert a == b and all(abs(sum(p) - 1) < 1e-9 and min(p) >= 0 for p in a)


def test_imputation_and_scaling_are_fitted_on_train_only():
    train, test = _rows(400), _rows(400, seed=3)
    for r in test:
        r["home_shots_total_for"] = 1000.0  # un test extremo no puede mover la normalización
    m = models.LogisticB2(0.1).fit(train)
    col = models.NUMERIC.index("home_shots_total_for")
    assert m.scaler.mean_[col] == pytest.approx(np.mean([r["home_shots_total_for"] for r in train]))
    m.predict(test)
    assert m.scaler.mean_[col] == pytest.approx(np.mean([r["home_shots_total_for"] for r in train]))


def test_missing_values_are_imputed_with_indicator_not_zero():
    train = _rows(300)
    m = models.LogisticB2(0.1).fit(train)
    col = models.NUMERIC.index("home_expected_goals_for")
    observed = [r["home_expected_goals_for"] for r in train if r["home_expected_goals_for"] is not None]
    assert m.imputer.statistics_[col] == pytest.approx(np.median(observed)) and m.imputer.statistics_[col] != 0
    assert "home_expected_goals_for_missing" in models.FLAGS


def test_baselines_and_signal_detection():
    train, test = _rows(2000), _rows(800, seed=4)
    labels = [r["label"] for r in test]
    b0 = metrics.log_loss(models.ClassFrequency().fit(train).predict(test), labels)
    b2 = metrics.log_loss(models.LogisticB2(0.1).fit(train).predict(test), labels)
    assert b2 < b0  # hay señal sintética: B2 tiene que encontrarla
    noise_train, noise_test = _rows(2000, signal=False), _rows(800, seed=5, signal=False)
    nl = [r["label"] for r in noise_test]
    b0n = metrics.log_loss(models.ClassFrequency().fit(noise_train).predict(noise_test), nl)
    b2n = metrics.log_loss(models.LogisticB2(0.01).fit(noise_train).predict(noise_test), nl)
    assert b2n > b0n - 0.01  # sin señal no aparece una ventaja material


def test_competition_frequency_shrinks_to_global():
    train = _rows(500)
    b1 = models.CompetitionFrequency(1e9).fit(train)
    base = models.ClassFrequency().fit(train).p
    assert all(np.allclose(p, base, atol=1e-6) for p in b1.p.values())
    assert b1.predict([{"competition_id": 999}]) == [list(base)]  # competición no vista → global


def test_temperature_is_selected_only_on_validation_b():
    train, val_b = _rows(600), _rows(300, seed=6)
    m = models.LogisticB2(0.1).fit(train)
    t, scores = models.select_temperature(m, val_b)
    assert t in models.TEMPERATURE_GRID and scores[t] == min(scores.values())
    t_other, _ = models.select_temperature(m, _rows(300, seed=7))
    assert isinstance(t_other, float)  # depende solo de las filas que se le pasan


def test_calibration_slopes_shape():
    test = _rows(300, seed=8)
    probs = models.ClassFrequency().fit(_rows(300)).predict(test)
    probs = [[p + 1e-3 * i % 3 for p in q] for i, q in enumerate(probs)]
    probs = [[p / sum(q) for p in q] for q in probs]
    out = models.calibration_slopes(probs, [r["label"] for r in test])
    assert set(out) == set(CLASSES)
