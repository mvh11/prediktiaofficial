"""Origen móvil por trimestres de M5.8D (puro). Solo kickoff < 2026-01-01 (el test de M5.8B no se toca)."""

from datetime import datetime, timedelta, timezone

from research.m58.splits import EMBARGO, TEST_START

ORIGINS = tuple(datetime(2025, m, 1, tzinfo=timezone.utc) for m in (1, 4, 7, 10))
CALIBRATION_WINDOW = timedelta(days=90)


def _quarter_end(origin: datetime) -> datetime:
    month = origin.month + 3
    return min(origin.replace(year=origin.year + (month > 12), month=(month - 1) % 12 + 1), TEST_START)


def fold(rows: list[dict], origin: datetime) -> dict[str, list[dict]]:
    """Conjuntos de un pliegue (disjuntos y con embargo de 7 días en cada corte):
    - fit:        kickoff < O − 7 d                        (modelo final del pliegue)
    - early:      kickoff < C0 − 7 d, C0 = O − 7 d − 90 d (modelo cuyas predicciones calibran/eligen)
    - calibration: C0 <= kickoff < O − 7 d
    - evaluation: O <= kickoff < fin del trimestre (y < 2026-01-01)"""
    fit_end = origin - EMBARGO
    cal_start = fit_end - CALIBRATION_WINDOW
    end = _quarter_end(origin)
    if any(r["kickoff_at"] >= TEST_START for r in rows):
        raise ValueError("el periodo de test de M5.8B no puede entrar en M5.8D")
    return {
        "fit": [r for r in rows if r["kickoff_at"] < fit_end],
        "early": [r for r in rows if r["kickoff_at"] < cal_start - EMBARGO],
        "calibration": [r for r in rows if cal_start <= r["kickoff_at"] < fit_end],
        "evaluation": [r for r in rows if origin <= r["kickoff_at"] < end],
    }
