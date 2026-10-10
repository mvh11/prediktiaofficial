"""Modelos del experimento M5.8 (requiere requirements-research.txt: numpy, scikit-learn).

B0: frecuencias globales de clase del train.
B1: frecuencias por competición, suavizadas hacia B0 (alpha elegido en validación A).
B2: logística multinomial L2: imputación (mediana) y escalado ajustados SOLO con train, one-hot de
    competición (categorías del train), C elegido en validación A; calibración opcional por
    temperatura ajustada en validación B. Semillas fijas.
"""

import math
from collections import Counter

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from research.m58 import metrics
from research.m58.dataset import FEATURE_COLUMNS
from research.m58.labels import CLASSES

SEED = 58
C_GRID = (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
ALPHA_GRID = (10, 25, 50, 100, 200, 400)
TEMPERATURE_GRID = tuple(round(0.5 + 0.05 * i, 2) for i in range(31))  # 0.5 .. 2.0
NUMERIC = tuple(c for c in FEATURE_COLUMNS if not c.endswith("_missing"))
FLAGS = tuple(c for c in FEATURE_COLUMNS if c.endswith("_missing"))


class ClassFrequency:
    """B0."""

    def fit(self, rows):
        counts = Counter(r["label"] for r in rows)
        self.p = tuple((counts[c] + 1) / (len(rows) + 3) for c in CLASSES)
        return self

    def predict(self, rows):
        return [list(self.p) for _ in rows]


class CompetitionFrequency:
    """B1: (n_c(k) + alpha * p0(k)) / (n_c + alpha)."""

    def __init__(self, alpha: float):
        self.alpha = alpha

    def fit(self, rows):
        self.base = ClassFrequency().fit(rows).p
        by = {}
        for r in rows:
            by.setdefault(r["competition_id"], Counter())[r["label"]] += 1
        self.p = {c: tuple((cnt[k] + self.alpha * self.base[i]) / (sum(cnt.values()) + self.alpha) for i, k in enumerate(CLASSES)) for c, cnt in by.items()}
        return self

    def predict(self, rows):
        return [list(self.p.get(r["competition_id"], self.base)) for r in rows]


class LogisticB2:
    def __init__(self, C: float):
        self.C = C

    def _raw(self, rows):
        x = np.array([[np.nan if r[c] is None else float(r[c]) for c in NUMERIC] for r in rows], dtype=float)
        flags = np.array([[1.0 if r[c] else 0.0 for c in FLAGS] for r in rows], dtype=float)
        onehot = np.array([[1.0 if r["competition_id"] == c else 0.0 for c in self.competitions] for r in rows], dtype=float)
        return x, flags, onehot

    def fit(self, rows):
        self.competitions = sorted({r["competition_id"] for r in rows})
        x, flags, onehot = self._raw(rows)
        self.imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(x)
        self.scaler = StandardScaler().fit(self.imputer.transform(x))
        self.model = LogisticRegression(C=self.C, max_iter=5000, random_state=SEED).fit(self._matrix(x, flags, onehot), [r["label"] for r in rows])
        assert tuple(self.model.classes_) == tuple(sorted(CLASSES))
        return self

    def _matrix(self, x, flags, onehot):
        return np.hstack([self.scaler.transform(self.imputer.transform(x)), flags, onehot])

    def predict(self, rows, temperature: float = 1.0):
        proba = self.model.predict_proba(self._matrix(*self._raw(rows)))
        order = [list(self.model.classes_).index(c) for c in CLASSES]
        proba = proba[:, order]
        if temperature != 1.0:
            logits = np.log(np.clip(proba, metrics.EPS, 1)) / temperature
            logits -= logits.max(axis=1, keepdims=True)
            proba = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
        return [list(map(float, p / p.sum())) for p in proba]


def select_alpha(train, val_a):
    labels = [r["label"] for r in val_a]
    scores = {a: metrics.log_loss(CompetitionFrequency(a).fit(train).predict(val_a), labels) for a in ALPHA_GRID}
    return min(scores, key=lambda a: (scores[a], a)), scores


def select_c(train, val_a):
    labels = [r["label"] for r in val_a]
    scores = {c: metrics.log_loss(LogisticB2(c).fit(train).predict(val_a), labels) for c in C_GRID}
    return min(scores, key=lambda c: (scores[c], c)), scores


def select_temperature(model: LogisticB2, val_b):
    labels = [r["label"] for r in val_b]
    scores = {t: metrics.log_loss(model.predict(val_b, t), labels) for t in TEMPERATURE_GRID}
    return min(scores, key=lambda t: (scores[t], abs(t - 1))), scores


def calibration_slopes(probs, labels) -> dict:
    """Por clase: pendiente e intercepto de logit(y) ~ logit(p) (ideal 1 y 0)."""
    out = {}
    for k, cls in enumerate(CLASSES):
        p = np.clip(np.array([q[k] for q in probs]), 1e-6, 1 - 1e-6)
        z = np.log(p / (1 - p)).reshape(-1, 1)
        y = np.array([1 if l == cls else 0 for l in labels])
        fit = LogisticRegression(C=1e6, max_iter=1000).fit(z, y)
        out[cls] = {"slope": round(float(fit.coef_[0][0]), 3), "intercept": round(float(fit.intercept_[0]), 3)}
    return out


def safe_mean(values):
    values = list(values)
    return sum(values) / len(values) if values else math.nan
