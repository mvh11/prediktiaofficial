"""Splits temporales del diseño M5.8A (puro). Por kickoff, globales entre competiciones.

- train:      kickoff < 2025-07-01
- validation: 2025-07-01 <= kickoff < 2026-01-01
- test:       kickoff >= 2026-01-01 (fuera de tiempo; se evalúa una sola vez)
Embargo de 7 días: los partidos con kickoff en los 7 días previos a cada frontera se purgan del
conjunto anterior (sus resultados podrían no conocerse aún en la frontera: supuesto A-1).
"""

from datetime import datetime, timedelta, timezone

TRAIN, VALIDATION, TEST, PURGED = "train", "validation", "test", "purged"
VALIDATION_START = datetime(2025, 7, 1, tzinfo=timezone.utc)
TEST_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
EMBARGO = timedelta(days=7)


def assign_split(kickoff_at: datetime) -> str:
    if kickoff_at.tzinfo is None:
        raise ValueError("kickoff sin zona horaria")
    if kickoff_at >= TEST_START:
        return TEST
    if kickoff_at >= VALIDATION_START:
        return PURGED if kickoff_at >= TEST_START - EMBARGO else VALIDATION
    return PURGED if kickoff_at >= VALIDATION_START - EMBARGO else TRAIN


def validation_halves(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Validación en dos mitades cronológicas: A elige la regularización, B ajusta la calibración
    (ninguna predicción se reutiliza para dos decisiones)."""
    ordered = sorted(rows, key=lambda r: (r["kickoff_at"], r["fixture_id"]))
    middle = len(ordered) // 2
    return ordered[:middle], ordered[middle:]
