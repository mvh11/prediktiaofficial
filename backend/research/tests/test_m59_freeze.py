"""Equivalencia numérica: modelo ajustado (sklearn) frente al reconstruido desde su manifiesto JSON."""

import json
from datetime import datetime, timezone

import pytest

from research.m58 import models as m58
from research.m59.canonical import canonical_json
from research.m59.freeze import freeze_b0, freeze_b1, freeze_b2
from research.m59.manifest import FrozenModel, ManifestError, load
from research.tests.test_m58_models import _rows

META = dict(model_version="sim-1", training_id="synthetic", frozen_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            code_version="test", dependencies={"scikit-learn": "1.9.1"}, dataset_fingerprint="synthetic")


def _reloaded(manifest):
    return FrozenModel(load(canonical_json(manifest)))  # ida y vuelta por texto JSON


def test_b2_manifest_reproduces_sklearn_probabilities():
    train, test = _rows(1200), _rows(300, seed=21)
    for r in test[::7]:
        r["home_expected_goals_for"], r["home_expected_goals_for_missing"] = None, True
    test[0]["competition_id"] = 999  # competición no vista
    fitted = m58.LogisticB2(0.01).fit(train)
    frozen = _reloaded(freeze_b2(fitted, m58.NUMERIC, m58.FLAGS, **META))
    expected, got = fitted.predict(test), [frozen.predict(r) for r in test]
    assert max(abs(a - b) for pe, pg in zip(expected, got) for a, b in zip(pe, pg)) < 1e-12


def test_b0_b1_manifests_reproduce_predictions():
    train, test = _rows(500), _rows(100, seed=22)
    b0, b1 = m58.ClassFrequency().fit(train), m58.CompetitionFrequency(200).fit(train)
    f0, f1 = _reloaded(freeze_b0(b0, **META)), _reloaded(freeze_b1(b1, **META))
    assert [f0.predict(r) for r in test] == b0.predict(test)
    assert all(max(abs(a - b) for a, b in zip(f1.predict(r), e)) < 1e-15 for r, e in zip(test, b1.predict(test)))


def test_manifest_is_deterministic_and_sealed():
    fitted = m58.LogisticB2(0.01).fit(_rows(400))
    a, b = freeze_b2(fitted, m58.NUMERIC, m58.FLAGS, **META), freeze_b2(fitted, m58.NUMERIC, m58.FLAGS, **META)
    assert canonical_json(a) == canonical_json(b) and len(a["manifest_sha256"]) == 64
    tampered = json.loads(canonical_json(a))
    tampered["hyperparameters"]["C"] = 1.0
    with pytest.raises(ManifestError):
        FrozenModel(tampered)
