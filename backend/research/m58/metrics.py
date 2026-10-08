"""Métricas de probabilidades 1X2 (puro, sin numpy). Las probabilidades van en el orden CLASSES."""

import math
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime

from research.m58.labels import CLASSES

EPS = 1e-15


def _check(probs: Sequence[Sequence[float]], labels: Sequence[str]) -> None:
    if len(probs) != len(labels) or not labels:
        raise ValueError("probabilidades y etiquetas no cuadran")
    for p in probs:
        if len(p) != 3 or abs(sum(p) - 1) > 1e-6 or min(p) < 0:
            raise ValueError(f"vector de probabilidades no válido: {p}")


def log_loss(probs, labels) -> float:
    _check(probs, labels)
    return -sum(math.log(max(p[CLASSES.index(y)], EPS)) for p, y in zip(probs, labels)) / len(labels)


def brier(probs, labels) -> float:
    """Brier multiclase: suma de cuadrados sobre las 3 clases, media por partido (0–2)."""
    _check(probs, labels)
    return sum(sum((p[k] - (CLASSES[k] == y)) ** 2 for k in range(3)) for p, y in zip(probs, labels)) / len(labels)


def rps(probs, labels) -> float:
    """Ranked probability score (H < D < A ordinal), media por partido (0–1)."""
    _check(probs, labels)
    total = 0.0
    for p, y in zip(probs, labels):
        observed = [1.0 if CLASSES.index(y) <= k else 0.0 for k in range(3)]
        cumulative = [sum(p[: k + 1]) for k in range(3)]
        total += sum((cumulative[k] - observed[k]) ** 2 for k in range(2)) / 2
    return total / len(labels)


def calibration(probs, labels, bins: int = 10) -> dict:
    """Fiabilidad por clase (bins de igual anchura) y ECE medio de las 3 clases."""
    _check(probs, labels)
    out, eces = {}, []
    for k, cls in enumerate(CLASSES):
        buckets = defaultdict(list)
        for p, y in zip(probs, labels):
            buckets[min(int(p[k] * bins), bins - 1)].append((p[k], 1.0 if y == cls else 0.0))
        rows, ece = [], 0.0
        for b in sorted(buckets):
            items = buckets[b]
            mean_p = sum(i[0] for i in items) / len(items)
            freq = sum(i[1] for i in items) / len(items)
            ece += len(items) / len(labels) * abs(mean_p - freq)
            rows.append({"bin": b, "n": len(items), "mean_prob": round(mean_p, 4), "freq": round(freq, 4)})
        out[cls] = {"ece": round(ece, 4), "bins": rows}
        eces.append(ece)
    out["ece_mean"] = round(sum(eces) / 3, 4)
    return out


def class_balance(labels) -> dict:
    return {c: round(sum(1 for y in labels if y == c) / len(labels), 4) for c in CLASSES} if labels else {}


def week_block(kickoff_at: datetime) -> tuple[int, int]:
    iso = kickoff_at.isocalendar()
    return iso[0], iso[1]


def block_bootstrap(
    rows: Sequence[dict], statistic: Callable[[Sequence[dict]], float], *, n_boot: int = 1000, seed: int = 58, alpha: float = 0.05
) -> tuple[float, float, float]:
    """(estimación, IC inferior, IC superior) remuestreando SEMANAS de kickoff completas (la
    dependencia dentro de una jornada se conserva). Determinista por semilla."""
    blocks = defaultdict(list)
    for r in rows:
        blocks[week_block(r["kickoff_at"])].append(r)
    keys = sorted(blocks)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        sample = [r for _ in keys for r in blocks[keys[rng.randrange(len(keys))]]]
        stats.append(statistic(sample))
    stats.sort()
    low, high = stats[int(alpha / 2 * n_boot)], stats[int((1 - alpha / 2) * n_boot) - 1]
    return statistic(list(rows)), low, high
