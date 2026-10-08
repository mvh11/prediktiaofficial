"""Modelos exploratorios M5.8D (requirements-research.txt). Reutiliza B0/B1/B2 de M5.8B sin
cambiarlos y añade calibración por clase y un logit ordinal (H < D < A)."""

import numpy as np
from scipy.optimize import minimize
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from research.m58 import metrics
from research.m58.labels import CLASSES
from research.m58.models import FLAGS, NUMERIC, SEED

LAMBDA_GRID = (0.1, 1.0, 10.0, 100.0)


class Preprocessor:
    """Igual que el de B2: imputación (mediana) y escalado ajustados con las filas de ajuste,
    indicadores de ausencia y one-hot de competición (categorías del ajuste)."""

    def fit(self, rows):
        self.competitions = sorted({r["competition_id"] for r in rows})
        x = self._numeric(rows)
        self.imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(x)
        self.scaler = StandardScaler().fit(self.imputer.transform(x))
        return self

    @staticmethod
    def _numeric(rows):
        return np.array([[np.nan if r[c] is None else float(r[c]) for c in NUMERIC] for r in rows], dtype=float)

    def transform(self, rows):
        flags = np.array([[1.0 if r[c] else 0.0 for c in FLAGS] for r in rows], dtype=float)
        onehot = np.array([[1.0 if r["competition_id"] == c else 0.0 for c in self.competitions] for r in rows], dtype=float)
        return np.hstack([self.scaler.transform(self.imputer.transform(self._numeric(rows))), flags, onehot])


class VectorCalibrator:
    """Calibración por clase: logística multinomial sobre log p del modelo base, ajustada con
    predicciones que el modelo base NO usó para entrenar."""

    def fit(self, probs, labels):
        self.model = LogisticRegression(C=10.0, max_iter=2000, random_state=SEED).fit(np.log(np.clip(probs, 1e-6, 1)), labels)
        return self

    def transform(self, probs):
        p = self.model.predict_proba(np.log(np.clip(probs, 1e-6, 1)))
        p = p[:, [list(self.model.classes_).index(c) for c in CLASSES]]
        return [list(map(float, q / q.sum())) for q in p]


class OrdinalLogit:
    """Logit ordinal acumulado con penalización L2: P(Y <= k) = sigma(theta_k − x·beta), H < D < A."""

    def __init__(self, lam: float):
        self.lam = lam

    def fit(self, rows):
        self.pre = Preprocessor().fit(rows)
        x = self.pre.transform(rows)
        y = np.array([CLASSES.index(r["label"]) for r in rows])
        n, d = x.shape

        def unpack(w):
            return w[:d], w[d], w[d] + np.exp(w[d + 1])

        def nll(w):
            beta, t1, t2 = unpack(w)
            eta = x @ beta
            c1, c2 = 1 / (1 + np.exp(-(t1 - eta))), 1 / (1 + np.exp(-(t2 - eta)))
            p = np.stack([c1, c2 - c1, 1 - c2], axis=1)
            ll = np.log(np.clip(p[np.arange(n), y], 1e-12, 1)).sum()
            return -ll / n + self.lam * (beta @ beta) / n

        w0 = np.zeros(d + 2)
        w0[d], w0[d + 1] = -0.2, np.log(1.2)
        self.result = minimize(nll, w0, method="L-BFGS-B", options={"maxiter": 500})
        self.beta, self.t1, self.t2 = unpack(self.result.x)
        return self

    def predict(self, rows):
        eta = self.pre.transform(rows) @ self.beta
        c1, c2 = 1 / (1 + np.exp(-(self.t1 - eta))), 1 / (1 + np.exp(-(self.t2 - eta)))
        p = np.clip(np.stack([c1, c2 - c1, 1 - c2], axis=1), 1e-9, 1)
        return [list(map(float, q / q.sum())) for q in p]


def select_lambda(early, calibration):
    labels = [r["label"] for r in calibration]
    scores = {lam: metrics.log_loss(OrdinalLogit(lam).fit(early).predict(calibration), labels) for lam in LAMBDA_GRID}
    return min(scores, key=lambda lam: (scores[lam], lam)), scores
