"""M5.8D (exploratorio): etiquetas a 90' corregidas, causas de cobertura, pliegues de origen móvil
sin el periodo de test de M5.8B y el constructor del dataset contra PostgreSQL. Sin numpy."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models import Fixture, TeamProviderMapping
from app.repositories import fixture_repository
from app.repositories import statistics_repository as repo
from app.schemas.prematch_features import SampleStatus
from research.m58.labels import FULLTIME_INCOMPLETE, NOT_FINISHED, label_1x2
from research.m58.splits import EMBARGO, TEST_START
from research.m58d.dataset import (EMPTY, EXTRA_TIME, NEVER_INGESTED, NOT_AVAILABLE_AT_T, QUALITY, TEAM_MISSING, USED,
                                   build_rows, coverage_cause)
from research.m58d.folds import ORIGINS, fold
from research.m58d.labels import GOALS_NOT_FULLTIME_PLUS_EXTRATIME, label_1x2_v2, quality_flags
from tests.conftest import make_competition, make_evidence, make_fixture_data

UTC = timezone.utc


@pytest.mark.parametrize(
    "status, home, away, expected",
    [
        ("AET", 2, 1, ("H", None)), ("PEN", 1, 0, ("H", None)),  # vuelta de eliminatoria: victoria local a 90'
        ("AET", 0, 2, ("A", None)), ("PEN", 1, 2, ("A", None)),  # victoria visitante a 90'
        ("AET", 1, 1, ("D", None)), ("PEN", 0, 0, ("D", None)),  # empate a 90'
        ("FT", 3, 1, ("H", None)), ("FT", 2, 2, ("D", None)),
        ("AET", None, 1, (None, FULLTIME_INCOMPLETE)), ("PEN", 1, None, (None, FULLTIME_INCOMPLETE)),
        ("NS", None, None, (None, NOT_FINISHED)), ("AWD", 3, 0, (None, NOT_FINISHED)),
    ],
)
def test_label_at_90_minutes_never_assumes_a_draw(status, home, away, expected):
    assert label_1x2_v2(status, home, away) == expected


def test_v1_of_m58b_is_preserved():
    assert label_1x2("AET", 2, 1) == (None, "extra_time_not_draw_at_90")  # M5.8B queda intacto


def test_two_legged_second_leg_is_labelled_by_its_own_90_minutes():
    # ida 1-0; vuelta 0-1 a los 90' (global 1-1), prórroga 1-0 y AET: la vuelta es A a los 90'
    assert label_1x2_v2("AET", 0, 1)[0] == "A"
    assert quality_flags("AET", (1, 1), (0, 1), (1, 0)) == []  # goals = fulltime + extratime


def test_quality_flags_never_change_the_label():
    assert quality_flags("AET", (5, 1), (1, 1), (1, 0)) == [GOALS_NOT_FULLTIME_PLUS_EXTRATIME]
    assert quality_flags("PEN", (1, 0), (1, 0), (None, None)) == []  # penaltis directos: sin extratime
    assert quality_flags("FT", (2, 0), (1, 0), (None, None)) == []
    assert label_1x2_v2("AET", 1, 1) == ("D", None)


def test_coverage_causes():
    assert coverage_cause(SampleStatus.USED, True) == USED
    assert coverage_cause(SampleStatus.EXCLUDED_EXTRA_TIME, True) == EXTRA_TIME
    assert coverage_cause(SampleStatus.TEAM_STATISTICS_MISSING, True) == TEAM_MISSING
    assert coverage_cause(SampleStatus.EMPTY, True) == EMPTY
    assert coverage_cause(SampleStatus.UNKNOWN_AT_T, False) == NEVER_INGESTED
    assert coverage_cause(SampleStatus.UNKNOWN_AT_T, True) == NOT_AVAILABLE_AT_T
    for s in (SampleStatus.BLOCKED, SampleStatus.RECONSTRUCTION_MISMATCH, SampleStatus.IDENTITY_UNVERIFIED):
        assert coverage_cause(s, True) == QUALITY


def _rows(start, days):
    return [{"fixture_id": i, "kickoff_at": start + timedelta(days=i)} for i in range(days)]


def test_rolling_folds_are_disjoint_embargoed_and_before_the_m58b_test():
    rows = _rows(datetime(2024, 1, 1, tzinfo=UTC), 730)  # hasta 2025-12-30
    for origin in ORIGINS:
        f = fold(rows, origin)
        assert max(r["kickoff_at"] for r in f["fit"]) < origin - EMBARGO
        assert max(r["kickoff_at"] for r in f["early"]) < min(r["kickoff_at"] for r in f["calibration"]) - EMBARGO + timedelta(days=1)
        assert {r["fixture_id"] for r in f["calibration"]} <= {r["fixture_id"] for r in f["fit"]}
        assert not {r["fixture_id"] for r in f["early"]} & {r["fixture_id"] for r in f["calibration"]}
        assert not {r["fixture_id"] for r in f["fit"]} & {r["fixture_id"] for r in f["evaluation"]}
        assert all(origin <= r["kickoff_at"] < TEST_START for r in f["evaluation"])
    with pytest.raises(ValueError):
        fold(rows + [{"fixture_id": -1, "kickoff_at": TEST_START}], ORIGINS[0])


# --- Constructor contra PostgreSQL --------------------------------------------------------------

PROVIDER = "api-football"
EARLY = datetime(2025, 1, 1, tzinfo=UTC)
H = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def season(db_session):
    _, sid = make_competition(db_session, 39, name="Liga")
    base = datetime(2025, 3, 1, 19, tzinfo=UTC)
    spec = [(8000, 63, 69, -7, "FT", (0, 0)), (8006, 63, 74, 28, "FT", (3, 0)), (8001, 63, 70, 0, "FT", (1, 0)), (8002, 70, 63, 7, "AET", (0, 1)), (8003, 63, 71, 14, "PEN", (2, 2)),
            (8004, 72, 63, 21, "FT", (1, 1)), (8005, 63, 73, 320, "FT", (2, 0))]  # 8005: periodo de test M5.8B
    data = []
    for ext, h, a, days, status, (fh, fa) in spec:
        g = dict(home_goals=fh, away_goals=fa, fulltime_home=fh, fulltime_away=fa)
        if status == "AET":
            g.update(home_goals=fh + 1, extratime_home=1, extratime_away=0)
        if status == "PEN":
            g.update(extratime_home=0, extratime_away=0, penalty_home=4, penalty_away=3)
        data.append(make_fixture_data(ext, home=h, away=a, status=status, kickoff_at=base + timedelta(days=days), **g))
    teams = fixture_repository.ensure_teams(db_session, [t for d in data for t in (d.home_team, d.away_team)], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, sid, data, teams, PROVIDER, make_evidence(EARLY, provider=PROVIDER))
    db_session.execute(update(TeamProviderMapping).values(created_at=EARLY))
    ids = {int(e): i for e, i in db_session.execute(select(Fixture.external_id, Fixture.id))}
    stats = [{"team": {"id": 63}, "statistics": [{"type": "Total Shots", "value": 9}]}, {"team": {"id": 70}, "statistics": [{"type": "Total Shots", "value": 4}]}]
    # 8001: stats disponibles; 8004: stats recibidas tarde (available_at después de T); 8000/8002/8003/8006: nunca ingeridas
    repo.record_observation(db_session, fixture_id=ids[8001], provider=PROVIDER, provider_fixture_id="8001", payload=stats, source="backfill",
                            availability="available", teams_returned=2, observed_at=H - timedelta(days=1), available_at=base + timedelta(hours=6))
    repo.record_observation(db_session, fixture_id=ids[8004], provider=PROVIDER, provider_fixture_id="8004", payload=[], source="live",
                            availability="empty", teams_returned=0, observed_at=H - timedelta(days=2), available_at=H - timedelta(days=2))
    return ids


def test_dataset_labels_causes_and_test_period_exclusion(db_session, season):
    rows, excluded, coverage = build_rows(db_session, H)
    labels = {next(e for e, i in season.items() if i == r["fixture_id"]): r["label"] for r in rows}
    assert labels == {8000: "D", 8001: "H", 8002: "A", 8003: "D", 8004: "D", 8006: "H"}  # AET 0-1 a los 90' es A
    assert excluded["m58b_test_period_not_used"] == 1 and all(r["kickoff_at"] < TEST_START for r in rows)
    r8002 = next(r for r in rows if r["fixture_id"] == season[8002])
    assert r8002["extra_time_non_draw_at_90"] and r8002["quality_flags"] == []
    target = next(r for r in rows if r["fixture_id"] == season[8004])
    causes = {c["window_fixture_id"]: c["cause"] for c in coverage if c["target_fixture_id"] == target["fixture_id"] and c["side"] == "away"}
    assert causes == {season[8000]: NEVER_INGESTED, season[8001]: USED, season[8002]: EXTRA_TIME, season[8003]: EXTRA_TIME}
    last = {c["window_fixture_id"]: c["cause"] for c in coverage if c["target_fixture_id"] == season[8006] and c["side"] == "home"}
    assert last == {season[8004]: NOT_AVAILABLE_AT_T, season[8003]: EXTRA_TIME, season[8002]: EXTRA_TIME,
                    season[8001]: USED, season[8000]: NEVER_INGESTED}
