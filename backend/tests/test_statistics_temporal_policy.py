"""Política temporal de las estadísticas (M5.4C): clasificación de temporadas histórica/operativa,
fail-closed del CLI, source/available_at por política y metadatos del run. Proveedor falso."""

import asyncio
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.jobs import statistics_backfill as cli
from app.models import Fixture, FixtureStatisticsObservation, Season, StatisticsRun
from app.services import statistics_service as service
from app.services.statistics_service import BackfillOptions, season_temporal_policy
from tests.test_statistics_service import HOME_FULL, TARGET, FakeStatsProvider, full_item, observations, replace, season  # noqa: F401

TODAY = date(2026, 10, 6)


# --- Clasificación (pura) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("is_current", "end_date", "classification", "reason", "source", "policy"),
    [
        (True, None, "operational", "is_current", "manual", "observed_at"),  # A + I
        (True, TODAY - timedelta(days=400), "operational", "is_current", "manual", "observed_at"),  # current manda
        (False, TODAY, "operational", "recently_ended", "manual", "observed_at"),
        (False, TODAY - timedelta(days=1), "operational", "recently_ended", "manual", "observed_at"),
        (False, TODAY - timedelta(days=7), "operational", "recently_ended", "manual", "observed_at"),  # B: límite incluido
        (False, TODAY - timedelta(days=8), "historical", "historical", "backfill", "kickoff_plus_6h"),  # C
        (False, TODAY + timedelta(days=30), "operational", "recently_ended", "manual", "observed_at"),  # termina en el futuro
    ],
)
def test_season_classification(is_current, end_date, classification, reason, source, policy):
    p = season_temporal_policy(is_current, end_date, TODAY)
    assert (p.classification, p.reason, p.source, p.availability_policy) == (classification, reason, source, policy)


def test_non_current_without_end_date_fails_closed():  # J
    with pytest.raises(service.UnknownSeasonEnd):
        season_temporal_policy(False, None, TODAY)


def test_grace_days_is_a_named_constant():
    assert service.OPERATIONAL_SEASON_GRACE_DAYS == 7
    assert season_temporal_policy(False, TODAY, TODAY).as_dict() == {
        "season_temporal_classification": "operational",
        "season_temporal_reason": "recently_ended",
        "observation_source": "manual",
        "availability_policy": "observed_at",
        "operational_grace_days": 7,
    }


def test_operational_available_at_is_the_observed_at_object():
    p = season_temporal_policy(True, None, TODAY)
    observed = datetime(2026, 10, 6, 6, 0, 0, 123456, tzinfo=timezone.utc)
    assert p.available_at(datetime(2026, 8, 1, tzinfo=timezone.utc), observed) is observed
    h = season_temporal_policy(False, TODAY - timedelta(days=60), TODAY)
    assert h.available_at(datetime(2026, 8, 1, tzinfo=timezone.utc), observed) == datetime(2026, 8, 1, 6, tzinfo=timezone.utc)


# --- Ejecución (BD de tests, proveedor falso) ---------------------------------------------------

def _set_season(db, season, *, is_current, end_date):
    db.execute(update(Season).where(Season.id == season["season_id"]).values(is_current=is_current, end_date=end_date))
    db.commit()


def _run(db, season, provider, clock, **options):
    opts = BackfillOptions(**{"mode": "apply", **options})
    return asyncio.run(service.run_season_backfill(
        db, competition_id=season["competition_id"], season_id=season["season_id"], provider=provider, options=opts, clock=lambda: clock))


T1 = datetime.now(timezone.utc).replace(microsecond=123456)
T2 = T1 + timedelta(hours=2)


@pytest.mark.db
def test_current_season_without_flag_is_rejected_before_run_and_provider(db_session, season):  # D
    (fid, h, a), = season["add"](1)
    _set_season(db_session, season, is_current=True, end_date=None)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    with pytest.raises(service.OperationalSeasonNotAllowed) as excinfo:
        _run(db_session, season, provider, T1)
    assert "is_current" in str(excinfo.value) and "--allow-operational-season" in str(excinfo.value)
    assert provider.calls == []
    assert db_session.scalar(select(func.count()).select_from(StatisticsRun)) == 0
    assert observations(db_session) == []


@pytest.mark.db
def test_operational_with_flag_uses_manual_and_observed_at(db_session, season):  # E + K
    (fid, h, a), (f2, h2, a2) = season["add"](2)
    _set_season(db_session, season, is_current=True, end_date=None)
    out = _run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a), f2: full_item(f2, h2, a2)}), T1, allow_operational_season=True)
    assert out.status == "completed"
    obs = observations(db_session)
    assert len(obs) == 2
    for o in obs:
        assert o.source == "manual"
        assert o.observed_at == o.available_at == o.last_observed_at == T1  # exactamente el mismo instante
    kickoffs = set(db_session.scalars(select(Fixture.kickoff_at)))
    assert all(o.available_at - timedelta(hours=6) not in kickoffs for o in obs)  # nunca kickoff + 6 h
    run = db_session.get(StatisticsRun, out.run_id)
    db_session.refresh(run)
    assert run.details[0] == {
        "kind": "temporal_policy",
        "season_temporal_classification": "operational",
        "season_temporal_reason": "is_current",
        "observation_source": "manual",
        "availability_policy": "observed_at",
        "operational_grace_days": 7,
    }


@pytest.mark.db
def test_recently_ended_season_is_operational(db_session, season):  # B en ejecución
    (fid, h, a), = season["add"](1)
    _set_season(db_session, season, is_current=False, end_date=T1.date() - timedelta(days=7))
    with pytest.raises(service.OperationalSeasonNotAllowed) as excinfo:
        _run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}), T1)
    assert excinfo.value.policy.reason == "recently_ended"
    _run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}), T1, allow_operational_season=True)
    (o,) = observations(db_session)
    assert (o.source, o.available_at) == ("manual", T1)


@pytest.mark.db
@pytest.mark.parametrize("flag", [False, True])  # F (sin flag) y G (flag sobre histórica)
def test_historical_season_keeps_backfill_policy(db_session, season, flag):
    (fid, h, a), = season["add"](1)
    _set_season(db_session, season, is_current=False, end_date=T1.date() - timedelta(days=8))
    out = _run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}), T1, allow_operational_season=flag)
    (o,) = observations(db_session)
    kickoff = db_session.scalar(select(Fixture.kickoff_at).where(Fixture.external_id == fid))
    assert (o.source, o.available_at, o.observed_at) == ("backfill", kickoff + timedelta(hours=6), T1)
    run = db_session.get(StatisticsRun, out.run_id)
    db_session.refresh(run)
    assert run.details[0]["season_temporal_classification"] == "historical"
    assert run.details[0]["observation_source"] == "backfill" and run.details[0]["availability_policy"] == "kickoff_plus_6h"


@pytest.mark.db
def test_operational_refresh_never_backdates_a_revision(db_session, season):  # H
    (fid, h, a), = season["add"](1)
    _set_season(db_session, season, is_current=True, end_date=None)
    _run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}), T1, allow_operational_season=True)
    changed = full_item(fid, h, a, home_stats=replace(HOME_FULL, **{"Corner Kicks": 9}))
    out = _run(db_session, season, FakeStatsProvider({fid: changed}), T2, allow_operational_season=True, refresh=True)
    assert out.counters["observations_created"] == 1 and out.counters["rows_updated"] == 1
    old, new = observations(db_session)
    assert (old.is_latest, old.observed_at, old.available_at) == (False, T1, T1)  # la antigua no cambia
    assert (new.is_latest, new.source, new.observed_at, new.available_at) == (True, "manual", T2, T2)
    # Mismo contenido otra vez: no hay versión nueva y se conserva el available_at original
    _run(db_session, season, FakeStatsProvider({fid: changed}), T2 + timedelta(hours=1), allow_operational_season=True, refresh=True)
    latest = observations(db_session)[-1]
    assert (latest.id, latest.available_at, latest.last_observed_at) == (new.id, T2, T2 + timedelta(hours=1))


@pytest.mark.db
def test_non_current_season_without_end_date_fails_closed(db_session, season):  # J en ejecución
    (fid, h, a), = season["add"](1)
    _set_season(db_session, season, is_current=False, end_date=None)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    with pytest.raises(service.UnknownSeasonEnd):
        _run(db_session, season, provider, T1, allow_operational_season=True)
    assert provider.calls == [] and db_session.scalar(select(func.count()).select_from(StatisticsRun)) == 0


# --- CLI ----------------------------------------------------------------------------------------


@pytest.fixture
def wired(db_session, monkeypatch):
    state = {"provider": FakeStatsProvider(), "built": 0}

    def build():
        state["built"] += 1
        return state["provider"]

    monkeypatch.setattr(cli, "_configured_target", lambda: TARGET)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(cli, "default_provider", build)
    monkeypatch.delenv(cli.TARGET_ENV, raising=False)
    return state


@pytest.mark.db
def test_cli_rejects_operational_season_without_flag(wired, db_session, season, capsys):
    (fid, h, a), = season["add"](1)
    _set_season(db_session, season, is_current=True, end_date=None)
    wired["provider"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    code = cli.main(["--competition-id", str(season["competition_id"]), "--season", "2025", "--dry-run", "--confirm-target", TARGET])
    assert code == 2 and wired["built"] == 0 and wired["provider"].calls == []
    err = capsys.readouterr().err
    assert "OPERATIVA" in err and "is_current" in err and "source=manual" in err and "available_at=observed_at" in err
    assert "--allow-operational-season" in err
    assert db_session.scalar(select(func.count()).select_from(StatisticsRun)) == 0 and observations(db_session) == []


@pytest.mark.db
def test_cli_operational_season_with_flag(wired, db_session, season, capsys):
    (fid, h, a), = season["add"](1)
    _set_season(db_session, season, is_current=True, end_date=None)
    wired["provider"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    code = cli.main(["--competition-id", str(season["competition_id"]), "--season", "2025", "--allow-operational-season", "--confirm-target", TARGET])
    assert code == 0
    assert "Política temporal: operational (is_current) -> source=manual, available_at=observed_at" in capsys.readouterr().out
    (o,) = observations(db_session)
    assert o.source == "manual" and o.available_at == o.observed_at


@pytest.mark.db
def test_cli_fails_closed_without_end_date(wired, db_session, season):
    season["add"](1)
    _set_season(db_session, season, is_current=False, end_date=None)
    code = cli.main(["--competition-id", str(season["competition_id"]), "--season", "2025", "--allow-operational-season", "--confirm-target", TARGET])
    assert code == 2 and wired["built"] == 0


def test_cli_flag_parsing():
    assert cli.parse_args(["--competition-id", "1", "--season", "2025"]).allow_operational_season is False
    assert cli.parse_args(["--competition-id", "1", "--season", "2025", "--allow-operational-season"]).allow_operational_season is True
