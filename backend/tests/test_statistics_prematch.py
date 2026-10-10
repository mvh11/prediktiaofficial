"""Selección de partidos de contexto prepartido (M5.7A). Capa pura, separada de la lectura as-of:
sin BD ni estadísticas."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.statistics_prematch import ContextCandidate, context_fixture_ids, prematch_cutoff

KICKOFF = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)


def _c(fixture_id, hours):
    return ContextCandidate(fixture_id, KICKOFF + timedelta(hours=hours))


def test_cutoff_is_the_target_kickoff():
    assert prematch_cutoff(KICKOFF) == KICKOFF
    with pytest.raises(ValueError):
        prematch_cutoff(datetime(2026, 9, 14, 19, 0))


def test_own_fixture_is_never_context():
    assert context_fixture_ids(1, KICKOFF, [_c(1, -48), _c(2, -24)]) == [2]


def test_same_kickoff_and_later_fixtures_are_excluded():
    assert context_fixture_ids(1, KICKOFF, [_c(2, 0), _c(3, 1), _c(4, -0.001)]) == [4]


def test_order_is_deterministic_and_without_duplicates():
    candidates = [_c(9, -24), _c(3, -48), _c(5, -24), _c(3, -48)]
    assert context_fixture_ids(1, KICKOFF, candidates) == [3, 5, 9]
    assert context_fixture_ids(1, KICKOFF, list(reversed(candidates))) == [3, 5, 9]


def test_naive_kickoffs_are_rejected():
    with pytest.raises(ValueError):
        context_fixture_ids(1, KICKOFF, [ContextCandidate(2, datetime(2026, 9, 1))])
