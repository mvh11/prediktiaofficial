"""Reconciliador live de estadísticas (M5.6B) contra PostgreSQL con un proveedor FALSO: calendario
T1–T4, estados, baja cobertura, política temporal live, lock, presupuesto, reanudación natural,
separación de live_sync y equivalencia con el pipeline del backfill. Ninguna llamada real."""

import asyncio
import inspect
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text, update

from app.integrations.exceptions import ProviderResponseError
from app.jobs import statistics_backfill as backfill_cli
from app.jobs import statistics_reconcile as cli
from app.models import Fixture, FixtureStatisticsObservation, FixtureTeamStatistics, Season, StatisticsRun
from app.repositories import fixture_repository
from app.repositories import statistics_run_repository as runs
from app.services import statistics_reconcile_service as rs
from app.services import statistics_service as service
from app.services.statistics_reconcile_service import ReconcileOptions
from tests.conftest import make_competition, make_evidence, make_fixture_data
from tests.test_statistics_service import AWAY_FULL, HOME_FULL, FakeStatsProvider, entry, full_item, item, replace

pytestmark = pytest.mark.db

PROVIDER = "api-football"
TARGET = "ep-test.us-east-2.aws.neon.tech:5432/neondb"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
K = NOW - timedelta(days=1)  # kickoff de referencia; las pasadas simulan el reloj desde aquí
T1, T2, T3, T4 = (K + timedelta(hours=h, minutes=30) for h in (4, 8, 26, 50))
SEC = timedelta(seconds=1)
BLOCKED_HOME = replace(HOME_FULL, **{"Passes %": "130%"})


@pytest.fixture
def league(db_session):
    """make(ext, ...) crea una competición con su temporada (current por defecto) y devuelve un
    dict con add(n, kickoff, start, status) que crea partidos FT con equipos (2n+1, 2n+2)."""

    def make(external_id=39, name="Liga live", *, current=True, end_date=None, year=2026):
        cid, sid = make_competition(db_session, external_id, name=name, current_year=year)
        if not current or end_date is not None:
            db_session.execute(update(Season).where(Season.id == sid).values(is_current=current, end_date=end_date))
        db_session.commit()
        return {"competition_id": cid, "season_id": sid, "add": lambda *a, **k: add_fixtures(db_session, sid, *a, **k)}

    return make


def add_fixtures(db, season_id, n, kickoff=K, start=1001, status="FT"):
    data = [make_fixture_data(start + i, home=2 * (start + i) + 1, away=2 * (start + i) + 2, status=status, kickoff_at=kickoff) for i in range(n)]
    team_ids = fixture_repository.ensure_teams(db, [t for f in data for t in (f.home_team, f.away_team)], PROVIDER)
    fixture_repository.upsert_fixtures(db, season_id, data, team_ids, PROVIDER, make_evidence(provider=PROVIDER))
    db.commit()
    return [(f.external_id, f.home_team.external_id, f.away_team.external_id) for f in data]


def rec(db, provider, at, **options):
    opts = ReconcileOptions(**{"mode": "apply", **options})
    return asyncio.run(rs.run_live_reconcile(db, provider=provider, options=opts, clock=lambda: at))


def fixture_id(db, external_id):
    return db.scalar(select(Fixture.id).where(Fixture.external_id == external_id))


def observations(db, external_id=None):
    db.expire_all()
    stmt = select(FixtureStatisticsObservation).order_by(FixtureStatisticsObservation.id)
    if external_id is not None:
        stmt = stmt.where(FixtureStatisticsObservation.fixture_id == fixture_id(db, external_id))
    return list(db.scalars(stmt))


def rows(db, external_id=None):
    db.expire_all()
    stmt = select(FixtureTeamStatistics).order_by(FixtureTeamStatistics.side.desc())
    if external_id is not None:
        stmt = stmt.where(FixtureTeamStatistics.fixture_id == fixture_id(db, external_id))
    return list(db.scalars(stmt))


def the_run(db, run_id):
    db.expire_all()
    return db.get(StatisticsRun, run_id)


def selection(db, run_id):
    return next(d for d in the_run(db, run_id).details if d.get("kind") == "live_selection")


def called(provider):
    return sorted(i for call in provider.calls for i in call)


# --- Motor de checkpoints (puro) -------------------------------------------------------------


def test_checkpoint_offsets_and_first_fetch():
    assert rs.first_fetch_at(K) == T1 == K + timedelta(hours=4, minutes=30)
    end = rs.fixture_end_estimate(K)
    assert [end + rs.CHECKPOINTS[c] for c in ("T1", "T2", "T3", "T4")] == [T1, T2, T3, T4]


@pytest.mark.parametrize("state, low, schedule", [
    ("none", False, ("T1", "T2", "T3", "T4")),
    ("available", False, ("T1", "T3")),
    ("available", True, ("T1", "T3")),
    ("partial", True, ("T1", "T2", "T3", "T4")),
    ("blocked", True, ("T1", "T2", "T3", "T4")),
    ("empty", False, ("T1", "T2", "T3", "T4")),
    ("empty", True, ("T1", "T3")),
])
def test_schedule_by_state(state, low, schedule):
    assert rs.schedule_for(state, low) == schedule


def test_due_checkpoint_rules():
    due = rs.due_checkpoint
    assert due(K, "none", None, T1 - SEC) is None
    assert due(K, "none", None, T1) == ("T1", T1)
    assert due(K, "available", T1, T2 + SEC) is None  # available no usa T2
    assert due(K, "available", T1, T3) == ("T3", T3)
    assert due(K, "available", T3, T4 + timedelta(days=30)) is None  # freeze tras T3
    assert due(K, "empty", T1, T2) == ("T2", T2)
    assert due(K, "empty", T1, T2, low_coverage=True) is None
    assert due(K, "empty", T4, T4 + timedelta(days=30)) is None  # freeze tras T4
    assert due(K, "empty", None, T3 + SEC) == ("T3", T3)  # vencidos T1–T3: una sola petición (el último)
    assert due(K, "none", T3, T4 + timedelta(days=5)) == ("T4", T4)  # primera adquisición pasada T4: una vez
    assert due(K, "none", T4, T4 + timedelta(days=5)) is None


def test_fixture_state_classification():
    assert rs.fixture_state(None, None, 0) == "none"
    assert rs.fixture_state("empty", 1, 0) == "empty"
    assert rs.fixture_state("available", 1, 2) == "available"
    assert rs.fixture_state("partial", 1, 1) == "partial"
    assert rs.fixture_state("available", 1, 0) == "blocked"  # raw guardada, normalización rechazada
    assert rs.fixture_state("partial", 1, 0) == "blocked"


def test_low_coverage_rule():
    mode = rs.low_coverage_mode
    assert mode(["empty"] * 20, None) == (True, "recent_empty")
    assert mode(["empty"] * 18 + ["available"] * 2, None) == (True, "recent_empty")  # 90 %
    assert mode(["empty"] * 17 + ["available"] * 3, None) == (False, "recent_available")  # salida
    assert mode(["empty"] * 19, 0.95) == (True, "previous_season_empty")
    assert mode(["empty"] * 19, 0.5) == (False, "normal")
    assert mode(["available"] * 3 + ["empty"] * 5, 1.0) == (False, "recent_available")
    assert mode(["empty"] * 10 + ["available"] * 15, None) == (False, "recent_available")


# --- Primer fetch y elegibilidad ----------------------------------------------------------------


def test_first_fetch_boundary_and_live_temporal_policy(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    before = rec(db_session, provider, T1 - SEC)
    assert before.counters["fixtures_targeted"] == 0 and provider.calls == [] and before.status == "completed"
    out = rec(db_session, provider, T1)
    assert out.status == "completed" and provider.calls == [[fid]]
    (obs,) = observations(db_session)
    assert obs.source == "live" and obs.observed_at == T1 and obs.available_at == obs.observed_at  # nunca backdatada
    run = the_run(db_session, out.run_id)
    assert (run.scope, run.trigger, run.mode) == ("live", "cli", "apply")
    policy = run.details[0]
    assert policy == {"kind": "temporal_policy", **rs.LIVE_POLICY.as_dict()}
    assert (policy["observation_source"], policy["availability_policy"]) == ("live", "observed_at")
    assert len(rows(db_session, fid)) == 2


@pytest.mark.parametrize("status", ["NS", "1H", "HT", "PST", "CANC", "ABD", "SUSP", "AWD", "WO"])
def test_not_final_and_awarded_are_never_targets(db_session, league, status):
    lg = league()
    (fid, h, a), = lg["add"](1, status=status)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = rec(db_session, provider, T4 + SEC)
    assert out.counters["fixtures_targeted"] == 0 and provider.calls == []


@pytest.mark.parametrize("status", ["AET", "PEN"])
def test_extra_time_and_penalties_are_eligible(db_session, league, status):
    lg = league()
    (fid, h, a), = lg["add"](1, status=status)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    assert rec(db_session, provider, T1).counters["fixtures_available"] == 1


def test_rescheduled_kickoff_recomputes_schedule(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    later = K + timedelta(hours=3)
    db_session.execute(update(Fixture).where(Fixture.external_id == fid).values(kickoff_at=later))
    db_session.commit()
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    assert rec(db_session, provider, T1).counters["fixtures_targeted"] == 0  # el T1 viejo ya no vale
    assert rec(db_session, provider, later + timedelta(hours=4, minutes=30)).counters["fixtures_targeted"] == 1


# --- Available: T1 + T3 --------------------------------------------------------------------------


def test_available_is_reconciled_only_at_t1_and_t3_then_frozen(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    assert rec(db_session, provider, T1).counters["fixtures_available"] == 1
    assert rec(db_session, provider, T2 + SEC).counters["fixtures_targeted"] == 0  # sin T2
    out = rec(db_session, provider, T3)
    assert out.counters["fixtures_targeted"] == 1 and out.counters["observations_unchanged"] == 1  # mismo hash
    (obs,) = observations(db_session)
    assert obs.observed_at == T1 and obs.last_observed_at == T3 and obs.available_at == T1  # solo avanza last_observed_at
    for at in (T4 + SEC, T4 + timedelta(days=3)):
        assert rec(db_session, provider, at).counters["fixtures_targeted"] == 0  # freeze
    assert len(provider.calls) == 2


def test_available_changed_at_t3_creates_new_evidence_and_keeps_old(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    rec(db_session, FakeStatsProvider({fid: full_item(fid, h, a)}), T1)
    corrected = FakeStatsProvider({fid: full_item(fid, h, a, away_stats=replace(AWAY_FULL, Fouls=12, **{"Corner Kicks": None}))})
    out = rec(db_session, corrected, T3)
    assert out.counters["observations_created"] == 1
    first, second = observations(db_session)
    assert (first.is_latest, first.observed_at, first.available_at) == (False, T1, T1)  # A intacta
    assert (second.is_latest, second.source, second.observed_at, second.available_at) == (True, "live", T3, T3)
    home, away = rows(db_session, fid)
    assert away.fouls == 12 and away.corners == 4  # merge no destructivo: el NULL de B no borra A
    assert home.observation_id == away.observation_id == second.id


# --- Empty / partial / quality-blocked -------------------------------------------------------------


def test_empty_is_retried_t1_to_t4_then_frozen_without_new_observations(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    provider = FakeStatsProvider({fid: item(fid, entry(h, []), entry(a, []))})
    for at in (T1, T2, T3, T4):
        assert rec(db_session, provider, at).counters["fixtures_empty"] == 1
        assert rec(db_session, provider, at + timedelta(minutes=30)).counters["fixtures_targeted"] == 0  # servido
    assert rec(db_session, provider, T4 + timedelta(days=2)).counters["fixtures_targeted"] == 0  # freeze
    (obs,) = observations(db_session)  # mismo hash: una sola observación
    assert obs.availability == "empty" and obs.last_observed_at == T4 and rows(db_session) == []
    assert len(provider.calls) == 4


def test_empty_that_gets_statistics_later_is_normalized_as_new_evidence(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    rec(db_session, FakeStatsProvider({fid: item(fid)}), T1)
    out = rec(db_session, FakeStatsProvider({fid: full_item(fid, h, a)}), T2)
    assert out.counters["observations_created"] == 1 and out.counters["rows_created"] == 2
    empty, full = observations(db_session)
    assert (empty.availability, empty.is_latest, full.availability, full.available_at) == ("empty", False, "available", T2)


def test_partial_retry_upgrades_to_available_and_degradation_never_erases(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    rec(db_session, FakeStatsProvider({fid: item(fid, entry(h, HOME_FULL), entry(a, []))}), T1)
    assert len(rows(db_session, fid)) == 1
    out = rec(db_session, FakeStatsProvider({fid: full_item(fid, h, a)}), T2)  # partial → available
    assert out.counters["rows_created"] == 1 and len(rows(db_session, fid)) == 2
    assert [o.availability for o in observations(db_session)] == ["partial", "available"]
    out = rec(db_session, FakeStatsProvider({fid: item(fid, entry(h, []), entry(a, AWAY_FULL))}), T3)  # degradada
    assert out.counters["warning_count"] >= 1
    home, _away = rows(db_session, fid)
    assert home.shots_on_goal == 5  # el dato normalizado previo no se borra


def test_quality_blocked_is_retried_and_recovers_when_provider_corrects(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    bad = FakeStatsProvider({fid: full_item(fid, h, a, home_stats=BLOCKED_HOME)})
    out = rec(db_session, bad, T1)
    assert out.status == "completed_with_errors" and out.counters["blocking_count"] == 1 and rows(db_session) == []
    again = rec(db_session, bad, T2)  # sigue inválido: mismo hash, BLOCKING otra vez, 0 filas
    assert again.counters["observations_unchanged"] == 1 and again.counters["blocking_count"] == 1 and rows(db_session) == []
    assert [c["code"] for c in the_run(db_session, again.run_id).checks] == ["percentage_over_100"]
    fixed = rec(db_session, FakeStatsProvider({fid: full_item(fid, h, a)}), T3)
    assert fixed.status == "completed" and fixed.counters["observations_created"] == 1 and fixed.counters["rows_created"] == 2
    blocked, good = observations(db_session)
    assert (blocked.is_latest, good.is_latest, good.available_at) == (False, True, T3)  # la inválida se conserva
    assert {r.observation_id for r in rows(db_session, fid)} == {good.id}
    assert all(r.passes_pct is None or r.passes_pct <= 100 for r in rows(db_session))


def test_quality_blocked_stays_frozen_after_t4(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    bad = FakeStatsProvider({fid: full_item(fid, h, a, home_stats=BLOCKED_HOME)})
    for at in (T1, T2, T3, T4):
        rec(db_session, bad, at)
    assert rec(db_session, bad, T4 + timedelta(days=1)).counters["fixtures_targeted"] == 0
    assert len(bad.calls) == 4 and rows(db_session) == []


# --- Coalescencia, primera adquisición y ventana -----------------------------------------------------


def test_missed_checkpoints_coalesce_into_one_request(db_session, league):
    lg = league()
    (fid, _h, _a), = lg["add"](1)
    provider = FakeStatsProvider({fid: item(fid)})
    assert rec(db_session, provider, T3 + timedelta(hours=1)).counters["fixtures_targeted"] == 1  # T1–T3 vencidos
    assert rec(db_session, provider, T3 + timedelta(hours=2)).counters["fixtures_targeted"] == 0
    assert rec(db_session, provider, T4).counters["fixtures_targeted"] == 1
    assert len(provider.calls) == 2


def test_first_acquisition_after_t4_within_lookback_happens_once(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = rec(db_session, provider, K + timedelta(days=4))
    assert out.counters["fixtures_available"] == 1 and selection(db_session, out.run_id)["by_priority"]["first_acquisition"] == 1


def test_missing_in_response_is_not_retried_within_the_same_checkpoint(db_session, league):
    lg = league()
    (fid, _h, _a), = lg["add"](1)
    provider = FakeStatsProvider({})  # el proveedor no devuelve el partido
    out = rec(db_session, provider, T1)
    assert out.counters["fixtures_missing_in_response"] == 1 and observations(db_session) == []
    assert rec(db_session, provider, T1 + timedelta(hours=1)).counters["fixtures_targeted"] == 0  # intento ya registrado
    assert rec(db_session, provider, T2).counters["fixtures_targeted"] == 1
    for at in (T3, T4):
        rec(db_session, provider, at)
    assert rec(db_session, provider, T4 + timedelta(days=2)).counters["fixtures_targeted"] == 0
    assert len(provider.calls) == 4


def test_backlog_outside_lookback_is_left_for_explicit_catch_up(db_session, league):
    lg = league()
    (old, h, a), = lg["add"](1, kickoff=K - timedelta(days=10))
    provider = FakeStatsProvider({old: full_item(old, h, a)})
    out = rec(db_session, provider, T1)
    assert out.counters["fixtures_targeted"] == 0 and provider.calls == []
    assert selection(db_session, out.run_id)["backlog_outside_lookback"] == 1


# --- Baja cobertura ------------------------------------------------------------------------------


def test_low_coverage_from_recent_first_acquisitions(db_session, league):
    lg = league()
    batch = lg["add"](20)
    provider = FakeStatsProvider({fid: item(fid) for fid, _h, _a in batch})
    rec(db_session, provider, T1)  # 20 primeras adquisiciones, todas empty
    later = K + timedelta(hours=2)
    (fid, _h, _a), = lg["add"](1, kickoff=later, start=2001)
    provider.items[fid] = item(fid)
    t1, t2, t3 = (later + timedelta(hours=h, minutes=30) for h in (4, 8, 26))
    rec(db_session, provider, t1)
    out = rec(db_session, provider, t2)
    assert selection(db_session, out.run_id)["seasons"][0]["low_coverage"] is True
    assert fixture_id(db_session, fid) not in [t[0] for t in selection(db_session, out.run_id)["targets"]]  # sin T2
    out = rec(db_session, provider, t3)
    assert fixture_id(db_session, fid) in [t[0] for t in selection(db_session, out.run_id)["targets"]]  # T3 sí


def test_low_coverage_exit_with_three_available(db_session, league):
    lg = league()
    batch = lg["add"](20)
    provider = FakeStatsProvider({fid: item(fid) for fid, _h, _a in batch[:17]})
    provider.items.update({fid: full_item(fid, h, a) for fid, h, a in batch[17:]})
    out = rec(db_session, provider, T1)
    seasons = selection(db_session, rec(db_session, provider, T1 + timedelta(minutes=5)).run_id)["seasons"]
    assert out.counters["fixtures_available"] == 3 and seasons[0] == {**seasons[0], "low_coverage": False, "low_coverage_reason": "recent_available"}


def test_low_coverage_from_previous_season(db_session, league):
    lg = league(140, "Liga sin estadísticas")
    previous = Season(competition_id=lg["competition_id"], year=2025, is_current=False, end_date=date.today() - timedelta(days=200))
    db_session.add(previous)
    db_session.commit()
    old = add_fixtures(db_session, previous.id, 3, kickoff=K - timedelta(days=300), start=7001)
    backfill = asyncio.run(service.run_season_backfill(
        db_session, competition_id=lg["competition_id"], season_id=previous.id,
        provider=FakeStatsProvider({fid: item(fid) for fid, _h, _a in old}), options=service.BackfillOptions(mode="apply"),
    ))
    assert backfill.counters["fixtures_empty"] == 3
    (fid, _h, _a), = lg["add"](1)
    provider = FakeStatsProvider({fid: item(fid)})
    rec(db_session, provider, T1)
    out = rec(db_session, provider, T2)
    assert selection(db_session, out.run_id)["seasons"][0]["low_coverage_reason"] == "previous_season_empty"
    assert out.counters["fixtures_targeted"] == 0  # empty en baja cobertura: sin T2
    assert rec(db_session, provider, T3).counters["fixtures_targeted"] == 1


# --- Prioridad, presupuesto y lotes -----------------------------------------------------------------


def test_priority_order_first_acquisition_retry_reconciliation(db_session, league):
    lg = league()
    (avail, h1, a1), (empty, _h2, _a2) = lg["add"](2)
    rec(db_session, FakeStatsProvider({avail: full_item(avail, h1, a1), empty: item(empty)}), T1)
    later = T3 - timedelta(hours=4, minutes=30)  # su T1 coincide con el T3 de los anteriores
    (new, h3, a3), = lg["add"](1, kickoff=later, start=3001)
    sel = rs.select_live_targets(db_session, PROVIDER, T3)
    assert [(t.fixture_id, t.priority, t.checkpoint) for t in sel.targets] == [
        (fixture_id(db_session, new), 1, "T1"), (fixture_id(db_session, empty), 2, "T3"), (fixture_id(db_session, avail), 3, "T3"),
    ]


def test_budget_exhaustion_stops_and_next_pass_recomputes(db_session, league):
    lg = league()
    batch = lg["add"](25, kickoff=NOW - timedelta(hours=5))
    provider = FakeStatsProvider({fid: full_item(fid, h, a) for fid, h, a in batch})
    out = rec(db_session, provider, NOW, stats_daily_budget=3)  # 0+3 ≤ 3; 1+3 > 3
    assert (out.status, out.stop_reason) == ("aborted", "budget_exhausted") and cli.exit_code(out, "scheduler") == 1
    run = the_run(db_session, out.run_id)
    assert run.fixtures_attempted == 20 and "siguiente pasada" in run.error_message
    nxt = rec(db_session, provider, NOW)
    assert nxt.counters["fixtures_targeted"] == 5 and len(observations(db_session)) == 25


def test_budget_respects_combined_cap_with_live_sync(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1, kickoff=NOW - timedelta(hours=5))
    db_session.execute(text(
        "INSERT INTO live_sync_runs (job_type, trigger, status, lock_scope, finished_at, provider_requests) "
        "VALUES ('fixtures', 'cli', 'completed', 'live_sync', now(), 10)"
    ))
    db_session.commit()
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = rec(db_session, provider, NOW, provider_daily_cap=12, stats_daily_budget=12)
    assert out.stop_reason == "budget_exhausted" and provider.calls == []


def test_batches_of_at_most_twenty_mixing_competitions_and_states(db_session, league):
    one, two = league(39, "Liga A"), league(140, "Liga B")
    a = one["add"](12)
    b = two["add"](13, start=5001)
    items = {fid: full_item(fid, h, x) for fid, h, x in a[:6] + b[:6]}
    items.update({fid: item(fid) for fid, _h, _x in a[6:] + b[6:]})
    provider = FakeStatsProvider(items)
    out = rec(db_session, provider, T1)
    assert [len(c) for c in provider.calls] == [20, 5] and out.counters["fixtures_attempted"] == 25
    by_season = the_run(db_session, out.run_id).coverage["by_season"]
    assert set(by_season) == {f"{one['competition_id']}:{one['season_id']}", f"{two['competition_id']}:{two['season_id']}"}
    with pytest.raises(ValueError):
        rec(db_session, provider, T1, batch_size=21)


# --- Temporadas fuera ----------------------------------------------------------------------------


def test_only_operational_non_dormant_seasons_are_considered(db_session, league):
    hist = league(39, "Histórica", current=False, end_date=date.today() - timedelta(days=60), year=2025)
    unknown = league(140, "Sin fin", current=False, year=2024)
    dormant = league(135, "Dormida", end_date=date.today() - timedelta(days=60))
    recent = league(78, "Recién terminada", current=False, end_date=date.today() - timedelta(days=3), year=2025)
    items, by_league = {}, {}
    for lg, start in ((hist, 1001), (unknown, 4001), (dormant, 6001), (recent, 8001)):
        (fid, h, a), = lg["add"](1, start=start)
        items[fid], by_league[lg["season_id"]] = full_item(fid, h, a), fid
    provider = FakeStatsProvider(items)
    out = rec(db_session, provider, T1)
    assert provider.calls == [[by_league[recent["season_id"]]]]  # solo la operativa no dormant
    sel = selection(db_session, out.run_id)
    assert [s["season_id"] for s in sel["seasons"]] == [recent["season_id"]]
    reasons = {e["season_id"]: e["reason"] for e in sel["excluded_seasons"]}
    assert reasons == {unknown["season_id"]: "unknown_season_end", dormant["season_id"]: "dormant"}
    assert hist["season_id"] not in reasons  # una histórica ni siquiera es candidata


def test_historical_statistics_are_never_mutated(db_session, league):
    hist = league(39, "Histórica", current=False, end_date=date.today() - timedelta(days=60), year=2025)
    (hfid, hh, ha), = hist["add"](1)
    asyncio.run(service.run_season_backfill(
        db_session, competition_id=hist["competition_id"], season_id=hist["season_id"],
        provider=FakeStatsProvider({hfid: item(hfid)}), options=service.BackfillOptions(mode="apply"),
    ))
    snapshot = lambda: db_session.execute(text(  # noqa: E731
        "SELECT md5(string_agg(o::text, ',' ORDER BY o.id)) FROM fixture_statistics_observations o")).scalar()
    before = snapshot()
    live = league(140, "Actual")
    (fid, h, a), = live["add"](1, start=5001)
    provider = FakeStatsProvider({fid: full_item(fid, h, a), hfid: full_item(hfid, hh, ha)})
    for at in (T1, T2, T3, T4):
        rec(db_session, provider, at)
    assert called(provider) == [fid, fid]  # T1 y T3; el histórico nunca se pide
    hist_obs = [o for o in observations(db_session) if o.fixture_id == fixture_id(db_session, hfid)]
    assert len(hist_obs) == 1 and hist_obs[0].source == "backfill"
    db_session.execute(text("DELETE FROM fixture_team_statistics WHERE fixture_id = :f"), {"f": fixture_id(db_session, fid)})
    db_session.execute(text("DELETE FROM fixture_statistics_observations WHERE fixture_id = :f"), {"f": fixture_id(db_session, fid)})
    assert snapshot() == before


# --- Lock, stale, reanudación ------------------------------------------------------------------


def test_lock_shared_with_backfill_and_manual(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    runs.create_run(db_session, trigger="cli", mode="apply", scope="season", competition_id=lg["competition_id"], season_id=lg["season_id"])
    db_session.commit()
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = rec(db_session, provider, T1)
    assert (out.status, out.run_id) == ("locked", None) and provider.calls == []
    assert cli.exit_code(out, "scheduler") == 0 and cli.exit_code(out, "cli") == 2  # SKIPPED no alarmante


def test_live_run_blocks_backfill(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    runs.create_run(db_session, trigger="scheduler", mode="apply", scope="live")
    db_session.commit()
    out = asyncio.run(service.run_season_backfill(
        db_session, competition_id=lg["competition_id"], season_id=lg["season_id"], provider=FakeStatsProvider(),
        options=service.BackfillOptions(mode="apply", allow_operational_season=True),
    ))
    assert out.status == "locked"


def test_stale_run_recovery_then_reconcile(wired, db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1, kickoff=NOW - timedelta(hours=5))
    wired["provider"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    run_id = runs.create_run(db_session, trigger="scheduler", mode="apply", scope="live")
    db_session.commit()
    assert cli.main(["--fail-stale-run", "--confirm-target", TARGET]) == 1  # demasiado reciente
    db_session.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(started_at=func.now() - timedelta(hours=2)))
    db_session.commit()
    assert cli.main(["--fail-stale-run", "--confirm-target", TARGET]) == 0
    assert the_run(db_session, run_id).status == "aborted"
    assert cli.main(["--confirm-target", TARGET, "--trigger", "scheduler"]) == 0
    assert [o.source for o in observations(db_session)] == ["live"]


def test_crash_mid_run_keeps_committed_batches_and_next_pass_resumes(db_session, league, monkeypatch):
    lg = league()
    batch = lg["add"](25)
    provider = FakeStatsProvider({fid: full_item(fid, h, a) for fid, h, a in batch})
    original = service.plan_fixture
    seen = {"n": 0}

    def flaky(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] > 20:
            raise RuntimeError("bug")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "plan_fixture", flaky)
    with pytest.raises(RuntimeError):
        rec(db_session, provider, T1)
    failed = list(db_session.scalars(select(StatisticsRun).order_by(StatisticsRun.id)))[-1]
    db_session.refresh(failed)
    assert failed.status == "failed" and len(observations(db_session)) == 20
    monkeypatch.setattr(service, "plan_fixture", original)
    nxt = rec(db_session, provider, T1 + timedelta(minutes=5))
    assert nxt.counters["fixtures_targeted"] == 5 and len(observations(db_session)) == 25  # sin --resume ni cursor


def test_provider_failure_rolls_back_batch_and_it_stays_due(db_session, league):
    lg = league()
    batch = lg["add"](25)
    items = {fid: full_item(fid, h, a) for fid, h, a in batch}
    out = rec(db_session, FakeStatsProvider(items, errors=[None, ProviderResponseError(PROVIDER, "boom")]), T1)
    assert (out.status, out.stop_reason) == ("failed", "provider_error") and cli.exit_code(out, "scheduler") == 2
    assert len(observations(db_session)) == 20
    nxt = rec(db_session, FakeStatsProvider(items), T1 + timedelta(minutes=5))
    assert nxt.counters["fixtures_targeted"] == 5


def test_dry_run_writes_nothing_and_is_not_an_attempt(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = rec(db_session, provider, T1, mode="dry_run")
    assert out.status == "dry_run_completed" and out.counters["observations_created"] == 1 and observations(db_session) == []
    assert rec(db_session, provider, T1 + timedelta(minutes=5)).counters["fixtures_available"] == 1


# --- Carrera de estado e identidad --------------------------------------------------------------------


def test_fixture_that_stops_being_final_during_download_is_blocked(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1)

    def to_pst(_ids):
        db_session.execute(update(Fixture).where(Fixture.external_id == fid).values(status_short="PST"))
        db_session.commit()

    out = rec(db_session, FakeStatsProvider({fid: full_item(fid, h, a)}, hook=to_pst), T1)
    assert [c["code"] for c in the_run(db_session, out.run_id).checks] == ["fixture_not_final"]
    assert observations(db_session) == [] and rows(db_session) == []


def test_fixture_without_mapping_is_blocked_without_request(db_session, league):
    lg = league()
    (fid, _h, _a), = lg["add"](1)
    db_session.execute(text("UPDATE fixture_provider_mappings SET is_active = false WHERE external_id = :e"), {"e": str(fid)})
    db_session.commit()
    provider = FakeStatsProvider()
    out = rec(db_session, provider, T1)
    assert provider.calls == [] and out.counters["fixtures_blocked"] == 1
    assert rec(db_session, provider, T1 + timedelta(minutes=5)).counters["fixtures_targeted"] == 0  # intento registrado


# --- Manual frente a live, equivalencia y separación ---------------------------------------------------


def test_manual_operational_and_live_sources(db_session, league):
    lg = league()
    (fid, h, a), = lg["add"](1, kickoff=NOW - timedelta(hours=5))
    manual = asyncio.run(service.run_season_backfill(
        db_session, competition_id=lg["competition_id"], season_id=lg["season_id"],
        provider=FakeStatsProvider({fid: full_item(fid, h, a)}),
        options=service.BackfillOptions(mode="apply", allow_operational_season=True),
    ))
    assert manual.status == "completed"
    (obs,) = observations(db_session)
    assert obs.source == "manual" and obs.available_at == obs.observed_at
    provider = FakeStatsProvider({fid: full_item(fid, h, a, away_stats=replace(AWAY_FULL, Fouls=12))})
    assert rec(db_session, provider, NOW + timedelta(minutes=5)).counters["fixtures_targeted"] == 0  # manual sirve T1
    later = NOW - timedelta(hours=5) + timedelta(hours=26, minutes=30)
    rec(db_session, provider, later)
    assert [o.source for o in observations(db_session)] == ["manual", "live"]


def test_live_pipeline_is_equivalent_to_backfill_pipeline(db_session, league):
    a, b = league(39, "Live"), league(140, "Manual")
    (fa, h, x), = a["add"](1, start=1001)
    (fb,) = [m[0] for m in add_fixtures(db_session, b["season_id"], 1, kickoff=K, start=9001)]
    # Mismo par de equipos en las dos competiciones: el mismo payload
    db_session.execute(update(Fixture).where(Fixture.external_id == fb).values(
        home_team_id=select(Fixture.home_team_id).where(Fixture.external_id == fa).scalar_subquery(),
        away_team_id=select(Fixture.away_team_id).where(Fixture.external_id == fa).scalar_subquery(),
    ))
    db_session.commit()
    payload = lambda fid: full_item(fid, h, x, home_stats=replace(HOME_FULL, **{"Red Cards": 0}))  # noqa: E731
    rec(db_session, FakeStatsProvider({fa: payload(fa)}), T1)
    asyncio.run(service.run_season_backfill(
        db_session, competition_id=b["competition_id"], season_id=b["season_id"], provider=FakeStatsProvider({fb: payload(fb)}),
        options=service.BackfillOptions(mode="apply", allow_operational_season=True),
    ))
    live_obs, manual_obs = observations(db_session, fa)[0], observations(db_session, fb)[0]
    for col in ("payload_hash", "availability", "teams_returned", "fixture_status_at_fetch"):
        assert getattr(live_obs, col) == getattr(manual_obs, col)
    strip = lambda r: {k: v for k, v in r.__dict__.items() if k not in ("_sa_instance_state", "id", "fixture_id", "observation_id", "created_at", "updated_at")}  # noqa: E731
    assert [strip(r) for r in rows(db_session, fa)] == [strip(r) for r in rows(db_session, fb)]


def test_reconciler_is_separate_from_live_sync(db_session, league):
    source = inspect.getsource(rs) + inspect.getsource(cli)
    assert "fixture_sync_service" not in source and "app.jobs.live_sync" not in source
    assert "/fixtures/statistics" not in source
    assert cli.default_provider is backfill_cli.default_provider  # mismo adapter /fixtures?ids
    lg = league()
    (fid, h, a), = lg["add"](1)
    before = db_session.execute(text("SELECT md5(string_agg(f::text, ',' ORDER BY f.id)) FROM fixtures f")).scalar()
    rec(db_session, FakeStatsProvider({fid: full_item(fid, h, a)}), T1)
    after = db_session.execute(text("SELECT md5(string_agg(f::text, ',' ORDER BY f.id)) FROM fixtures f")).scalar()
    assert before == after and db_session.execute(text("SELECT count(*) FROM live_sync_runs")).scalar() == 0


def test_run_audit_selection_and_coverage(db_session, league):
    lg = league()
    (f1, h, a), (f2, _h2, _a2) = lg["add"](2)
    out = rec(db_session, FakeStatsProvider({f1: full_item(f1, h, a), f2: item(f2)}), T1)
    run = the_run(db_session, out.run_id)
    sel = selection(db_session, out.run_id)
    assert sel["due"] == 2 and sel["by_priority"]["first_acquisition"] == 2 and sel["now"] == T1.isoformat()
    cov = run.coverage
    assert cov["scope"] == "live" and cov["fixtures"]["available"] == 1 and cov["fixtures"]["empty"] == 1
    key = f"{lg['competition_id']}:{lg['season_id']}"
    assert cov["by_season"][key]["fixtures"] == cov["fixtures"] and cov["xg_pct"] == 100.0
    batches = [d for d in run.details if "fixture_ids" in d]
    assert len(batches) == 1 and run.cursor_fixture_id == batches[0]["fixture_ids"][-1]


# --- CLI ------------------------------------------------------------------------------------------


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


def test_cli_target_missing_or_mismatch_does_nothing(wired, db_session):
    assert cli.main(["--dry-run"]) == 2
    assert cli.main(["--dry-run", "--confirm-target", "otro:5432/x"]) == 2
    assert wired["built"] == 0 and db_session.scalar(select(func.count()).select_from(StatisticsRun)) == 0


def test_cli_end_to_end_and_locked_skip(wired, db_session, league, capsys):
    lg = league()
    (fid, h, a), = lg["add"](1, kickoff=NOW - timedelta(hours=5))
    wired["provider"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    assert cli.main(["--dry-run", "--confirm-target", TARGET]) == 0
    out = capsys.readouterr().out
    assert f"Destino BD:  {TARGET}" in out and "dry_run_completed" in out and "source=live" in out
    assert cli.main(["--confirm-target", TARGET, "--trigger", "scheduler"]) == 0
    assert [o.source for o in observations(db_session)] == ["live"]
    runs.create_run(db_session, trigger="cli", mode="apply", scope="fixtures")
    db_session.commit()
    assert cli.main(["--confirm-target", TARGET, "--trigger", "scheduler"]) == 0
    assert "SKIPPED" in capsys.readouterr().out
    assert cli.main(["--confirm-target", TARGET]) == 2


def test_cli_argument_validation():
    for argv in (["--budget", "0"], ["--budget", "7000", "--provider-daily-cap", "6000"], ["--stale-after-minutes", "5"],
                 ["--fail-stale-run", "--dry-run"], ["--resume"], ["--refresh"], ["--season", "2025"]):
        with pytest.raises(SystemExit):
            cli.parse_args(argv)
    args = cli.parse_args(["--dry-run"])
    assert (args.budget, args.provider_daily_cap, args.dry_run, args.trigger) == (1500, 6000, True, "cli")


def test_exit_codes():
    def outcome(status, stop=None, **counters):
        return service.BackfillOutcome(1, status, stop, {"blocking_count": 0, "fixtures_missing_in_response": 0, **counters})

    assert cli.exit_code(outcome("completed"), "scheduler") == 0
    assert cli.exit_code(outcome("dry_run_completed", blocking_count=1), "cli") == 1
    assert cli.exit_code(outcome("completed_with_errors"), "scheduler") == 1
    assert cli.exit_code(outcome("aborted", "budget_exhausted"), "scheduler") == 1
    assert cli.exit_code(outcome("aborted", "rate_limited"), "scheduler") == 2
    assert cli.exit_code(outcome("failed", "auth_failed"), "scheduler") == 2
    assert cli.exit_code(service.BackfillOutcome(None, "locked"), "scheduler") == 0
    assert cli.exit_code(service.BackfillOutcome(None, "locked"), "cli") == 2
