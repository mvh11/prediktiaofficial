"""Lectura as-of de estadísticas (M5.7A) contra PostgreSQL: selección temporal, reconstrucción
desde el raw, calidad recalculada y contrastada con el run, procedencia sintética/operativa,
ausencia de escrituras y de fugas de observaciones posteriores al corte."""

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, select, update

from app.models import Fixture, FixtureStatisticsObservation, FixtureTeamStatistics, StatisticsRun
from app.repositories import fixture_repository
from app.repositories import statistics_repository as repo
from app.schemas.statistics import TeamStatisticValues
from app.schemas.statistics_knowledge import StatisticsAsOfStatus as S
from app.schemas.statistics_knowledge import StatisticsProvenance as P
from app.schemas.statistics_knowledge import provenance_of
from app.services import statistics_as_of as as_of
from app.services import statistics_quality_checks as qc
from app.services.statistics_prematch import prematch_cutoff
from tests.conftest import load_json, make_competition, make_evidence, make_fixture_data

pytestmark = pytest.mark.db

PROVIDER = "api-football"
KICKOFF = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
SYNTHETIC = KICKOFF + timedelta(hours=6)
T1 = datetime(2026, 9, 14, 21, 30, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 15, 3, 30, tzinfo=timezone.utc)
REAL = next(i for i in load_json("api_football/statistics/fixtures_ids_recorded.json")["response"] if i["fixture"]["id"] == 1557402)
PAYLOAD = REAL["statistics"]  # home 63, away 34


@pytest.fixture
def match(db_session):
    _, sid = make_competition(db_session, 39, name="Premier League")
    data = [
        make_fixture_data(1557402, home=63, away=34, status="FT", kickoff_at=KICKOFF, home_goals=2, away_goals=1, fulltime_home=2, fulltime_away=1),
        make_fixture_data(1557403, home=40, away=50, status="FT", kickoff_at=KICKOFF, home_goals=0, away_goals=0, fulltime_home=0, fulltime_away=0),
    ]
    teams = [t for d in data for t in (d.home_team, d.away_team)]
    team_ids = fixture_repository.ensure_teams(db_session, teams, PROVIDER)
    fixture_repository.upsert_fixtures(db_session, sid, data, team_ids, PROVIDER, make_evidence(provider=PROVIDER))
    rows = db_session.execute(select(Fixture.external_id, Fixture.id, Fixture.home_team_id, Fixture.away_team_id)).all()
    return {int(ext): (fid, home, away) for ext, fid, home, away in rows}


def _payload(fouls_home=8, home=True, away=True, passes_pct_home="84%", home_team=63):
    payload = copy.deepcopy(PAYLOAD)
    payload[0]["team"]["id"] = home_team
    for stat in payload[0]["statistics"]:
        if stat["type"] == "Fouls":
            stat["value"] = fouls_home
        if stat["type"] == "Passes %":
            stat["value"] = passes_pct_home
    if not home:
        payload[0]["statistics"] = []
    if not away:
        payload[1]["statistics"] = []
    return payload


def _observe(db, fixture_id, payload, *, observed_at, source="live", available_at=None, run_id=None, pid="1557402"):
    with_stats = sum(1 for e in payload if e["statistics"])
    if available_at is None:
        assert source != "backfill"
        available_at = observed_at
    return repo.record_observation(
        db, fixture_id=fixture_id, provider=PROVIDER, provider_fixture_id=pid, payload=payload, source=source,
        availability=("empty", "partial", "available")[with_stats], teams_returned=with_stats,
        observed_at=observed_at, available_at=available_at, run_id=run_id,
    ).observation_id


def _read(db, fixture_id, cutoff):
    return as_of.statistics_as_of_fixture(db, fixture_id, PROVIDER, cutoff)


def _fouls(result, side="home"):
    return result.team(side).values.fouls


# --- Selección temporal -----------------------------------------------------------------------


def test_no_observation_before_the_cutoff_is_unknown(db_session, match):
    fid = match[1557402][0]
    assert _read(db_session, fid, T2).status is S.UNKNOWN_AT_T  # ninguna observación
    _observe(db_session, fid, _payload(), observed_at=T2)
    result = _read(db_session, fid, T1)
    assert result.status is S.UNKNOWN_AT_T and result.observation is None and result.teams == () and not result.is_usable


def test_observation_available_exactly_at_the_cutoff_is_included(db_session, match):
    fid = match[1557402][0]
    oid = _observe(db_session, fid, _payload(), observed_at=T1)
    assert _read(db_session, fid, T1).observation.observation_id == oid
    assert _read(db_session, fid, T1 - timedelta(microseconds=1)).status is S.UNKNOWN_AT_T


def test_later_observation_is_excluded_and_versions_are_counted(db_session, match):
    fid = match[1557402][0]
    a = _observe(db_session, fid, _payload(fouls_home=8), observed_at=T1)
    b = _observe(db_session, fid, _payload(fouls_home=9), observed_at=T2)
    before = _read(db_session, fid, T2 - timedelta(seconds=1))
    assert before.observation.observation_id == a and _fouls(before) == 8 and before.eligible_versions == 1
    after = _read(db_session, fid, T2)
    assert after.observation.observation_id == b and _fouls(after) == 9 and after.eligible_versions == 2


def test_correction_is_not_visible_before_its_availability(db_session, match):
    """A → B → A': cada corte ve exactamente la versión disponible entonces."""
    fid = match[1557402][0]
    t3 = T2 + timedelta(hours=10)
    _observe(db_session, fid, _payload(fouls_home=8), observed_at=T1)
    _observe(db_session, fid, _payload(fouls_home=11), observed_at=T2)
    _observe(db_session, fid, _payload(fouls_home=8), observed_at=t3)  # el proveedor revierte
    assert [_fouls(_read(db_session, fid, t)) for t in (T1, T2, t3 - timedelta(seconds=1), t3)] == [8, 11, 11, 8]
    assert _read(db_session, fid, t3).eligible_versions == 3


def test_ties_on_observed_at_are_resolved_by_id_deterministically(db_session, match):
    fid = match[1557402][0]
    _observe(db_session, fid, _payload(fouls_home=8), observed_at=T1)
    second = _observe(db_session, fid, _payload(fouls_home=12), observed_at=T1)  # misma marca de tiempo
    results = [_read(db_session, fid, T2) for _ in range(3)]
    assert {r.observation.observation_id for r in results} == {second} and _fouls(results[0]) == 12
    assert results[0] == results[1] == results[2]  # misma respuesta cada vez


# --- Procedencia ------------------------------------------------------------------------------


def test_historical_synthetic_provenance_is_kept_and_flags_late_observation(db_session, match):
    fid = match[1557402][0]
    observed = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)  # backfill real, semanas después
    _observe(db_session, fid, _payload(), observed_at=observed, source="backfill", available_at=SYNTHETIC)
    assert _read(db_session, fid, SYNTHETIC - timedelta(seconds=1)).status is S.UNKNOWN_AT_T
    result = _read(db_session, fid, SYNTHETIC)
    assert result.status is S.AVAILABLE and result.provenance is P.HISTORICAL_SYNTHETIC
    assert result.observation.available_at == SYNTHETIC and result.observation.observed_at == observed
    assert result.observation.observed_after(result.cutoff)  # disponible por política, no conocido entonces


def test_operational_provenance(db_session, match):
    fid = match[1557402][0]
    _observe(db_session, fid, _payload(), observed_at=T1, source="live")
    result = _read(db_session, fid, T1)
    assert result.provenance is P.OPERATIONAL and not result.observation.observed_after(T1)
    assert result.observation.available_at == result.observation.observed_at


def test_provenance_rules():
    assert provenance_of("manual", T1, T1) is P.OPERATIONAL
    assert provenance_of("backfill", T2, SYNTHETIC) is P.HISTORICAL_SYNTHETIC
    with pytest.raises(ValueError):
        provenance_of("live", T2, T1)  # una operativa nunca se retrodata
    with pytest.raises(ValueError):
        provenance_of("scraped", T1, T1)


# --- Reconstrucción y calidad -----------------------------------------------------------------


def test_values_are_rebuilt_from_raw_with_null_and_presence(db_session, match):
    fid, home_id, away_id = match[1557402]
    _observe(db_session, fid, _payload(), observed_at=T1)
    result = _read(db_session, fid, T1)
    home, away = result.teams
    assert (home.side, home.team_id, home.provider_team_id) == ("home", home_id, 63)
    assert (away.side, away.team_id, away.provider_team_id) == ("away", away_id, 34)
    assert home.values.possession_pct == Decimal("50") and home.values.expected_goals == Decimal("2.08")
    assert home.values.red_cards is None and "red_cards" in home.present  # null del proveedor, no 0
    assert away.values.shots_blocked == 0
    assert result.normalizer_version == as_of.NORMALIZER_VERSION and result.issues == ()


def test_current_normalized_state_is_never_used(db_session, match):
    fid, home_id, _ = match[1557402]
    oid = _observe(db_session, fid, _payload(fouls_home=8), observed_at=T1)
    repo.upsert_team_statistics(db_session, fixture_id=fid, team_id=home_id, provider=PROVIDER, side="home",
                                observation_id=oid, values=TeamStatisticValues(fouls=99), normalizer_version=1)
    assert _fouls(_read(db_session, fid, T1)) == 8


def test_empty_observation_is_valid_evidence_without_values(db_session, match):
    fid = match[1557402][0]
    _observe(db_session, fid, _payload(home=False, away=False), observed_at=T1)
    result = _read(db_session, fid, T1)
    assert result.status is S.EMPTY and result.teams == () and result.observation.availability == "empty"


def test_partial_observation_exposes_only_the_team_with_statistics(db_session, match):
    fid = match[1557402][0]
    _observe(db_session, fid, _payload(home=False), observed_at=T1)
    result = _read(db_session, fid, T1)
    assert result.status is S.PARTIAL and [t.side for t in result.teams] == ["away"] and result.team("home") is None


def test_value_blocking_observation_is_blocked_without_values(db_session, match):
    fid = match[1557402][0]
    _observe(db_session, fid, _payload(passes_pct_home="130%"), observed_at=T1)
    result = _read(db_session, fid, T1)
    assert result.status is S.BLOCKED and result.teams == () and not result.is_usable
    assert (qc.BLOCKING, qc.PERCENTAGE_OVER_100, 63, "passes_pct") in {(i.severity, i.code, i.provider_team_id, i.field) for i in result.issues}


def test_identity_blocking_observation_is_blocked(db_session, match):
    fid = match[1557402][0]
    _observe(db_session, fid, _payload(home_team=99999), observed_at=T1)  # equipo sin mapping
    result = _read(db_session, fid, T1)
    assert result.status is S.BLOCKED and qc.TEAM_WITHOUT_MAPPING in {i.code for i in result.issues}


def _run(db, checks):
    run = StatisticsRun(trigger="cli", mode="apply", scope="fixtures", status="completed_with_errors",
                        started_at=T1, finished_at=T1, checks=checks)
    db.add(run)
    db.flush()
    return run.id


def test_recorded_quality_agrees_with_reconstruction(db_session, match):
    fid = match[1557402][0]
    run_id = _run(db_session, [{"severity": "BLOCKING", "code": qc.PERCENTAGE_OVER_100, "fixture_id": fid},
                               {"severity": "BLOCKING", "code": qc.NEGATIVE_VALUE, "fixture_id": fid + 1}])
    _observe(db_session, fid, _payload(passes_pct_home="130%"), observed_at=T1, run_id=run_id)
    result = _read(db_session, fid, T1)
    assert result.status is S.BLOCKED and result.recorded_blocking == (qc.PERCENTAGE_OVER_100,)


def test_recorded_quality_disagreement_is_a_reconstruction_mismatch(db_session, match):
    """El run lo bloqueó y hoy los checks no: no se elige ganador ni se exponen valores."""
    fid = match[1557402][0]
    run_id = _run(db_session, [{"severity": "BLOCKING", "code": qc.TEAM_WITHOUT_MAPPING, "fixture_id": fid}])
    _observe(db_session, fid, _payload(), observed_at=T1, run_id=run_id)
    result = _read(db_session, fid, T1)
    assert result.status is S.RECONSTRUCTION_MISMATCH and result.teams == ()
    assert as_of.QUALITY_RECORD_MISMATCH in {i.code for i in result.issues}


def test_altered_evidence_is_a_reconstruction_mismatch(db_session, match):
    fid = match[1557402][0]
    oid = _observe(db_session, fid, _payload(), observed_at=T1)
    db_session.execute(update(FixtureStatisticsObservation).where(FixtureStatisticsObservation.id == oid).values(payload=_payload(fouls_home=1)))
    result = _read(db_session, fid, T1)
    assert result.status is S.RECONSTRUCTION_MISMATCH and result.issues[0].code == as_of.PAYLOAD_HASH_MISMATCH


# --- Contexto prepartido y fugas ---------------------------------------------------------------


def test_same_kickoff_and_own_fixture_statistics_are_not_visible_before_kickoff(db_session, match):
    own, other = match[1557402][0], match[1557403][0]
    _observe(db_session, own, _payload(), observed_at=T1)
    _observe(db_session, other, _payload(home_team=40), observed_at=T1, pid="1557403")
    report = as_of.statistics_as_of(db_session, [own, other], PROVIDER, prematch_cutoff(KICKOFF))
    assert report.counts[S.UNKNOWN_AT_T] == 2  # ni el propio partido ni el simultáneo filtran sus stats


def test_batch_report_covers_every_requested_fixture(db_session, match):
    own, other = match[1557402][0], match[1557403][0]
    _observe(db_session, own, _payload(), observed_at=T1)
    report = as_of.statistics_as_of(db_session, [other, own, own], PROVIDER, T2)
    assert sorted(report.results) == sorted([own, other])
    assert report.with_status(S.AVAILABLE) == [own] and report.with_status(S.UNKNOWN_AT_T) == [other]


def test_invalid_inputs(db_session, match):
    fid = match[1557402][0]
    with pytest.raises(ValueError):
        _read(db_session, fid, datetime(2026, 9, 15))  # corte sin zona horaria
    with pytest.raises(ValueError):
        as_of.statistics_as_of(db_session, [fid], "5dollarfootballapi", T1)
    assert as_of.statistics_as_of(db_session, [], PROVIDER, T1).results == {}


# --- Solo lectura ------------------------------------------------------------------------------


def _evidence(db):
    db.expire_all()
    observations = db.execute(select(FixtureStatisticsObservation.__table__)).all()
    rows = db.execute(select(FixtureTeamStatistics.__table__)).all()
    return sorted(map(tuple, observations)), sorted(map(tuple, rows))


def test_reads_never_write_and_preserve_the_original_evidence(db_session, match):
    fid, home_id, _ = match[1557402]
    oid = _observe(db_session, fid, _payload(), observed_at=T1)
    repo.upsert_team_statistics(db_session, fixture_id=fid, team_id=home_id, provider=PROVIDER, side="home",
                                observation_id=oid, values=TeamStatisticValues(fouls=8), normalizer_version=1)
    _observe(db_session, fid, _payload(passes_pct_home="130%"), observed_at=T2)
    db_session.flush()
    before = _evidence(db_session)
    statements = []
    connection = db_session.connection()

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(connection, "before_cursor_execute", capture)
    try:
        for cutoff in (T1, T2, T2 + timedelta(days=1)):
            as_of.statistics_as_of(db_session, [fid, match[1557403][0]], PROVIDER, cutoff)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert statements and set(statements) == {"SELECT"}
    assert _evidence(db_session) == before
