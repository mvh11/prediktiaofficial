"""Backfill de estadísticas (M5.3) contra PostgreSQL con un proveedor FALSO: identidad, checks,
disponibilidad, merge no destructivo, lotes y transacciones, presupuesto, cursor/reanudación,
dry-run, runs y CLI. Ninguna llamada real al proveedor."""

import asyncio
from contextlib import nullcontext
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text, update

from app.integrations.exceptions import ProviderAuthError, ProviderRateLimitError, ProviderResponseError
from app.integrations.football.api_football_statistics import normalize_fixture_ids, parse_fixture_statistics
from app.jobs import statistics_backfill as cli
from app.models import Fixture, FixtureStatisticsObservation, FixtureTeamStatistics, Season, StatisticsRun
from app.repositories import fixture_repository
from app.repositories import statistics_run_repository as runs
from app.services import statistics_service as service
from app.services.statistics_service import BackfillOptions
from tests.conftest import make_competition, make_fixture_data

pytestmark = pytest.mark.db

PROVIDER = "api-football"
TARGET = "ep-test.us-east-2.aws.neon.tech:5432/neondb"

HOME_FULL = [
    ("Shots on Goal", 5), ("Shots off Goal", 3), ("Total Shots", 10), ("Blocked Shots", 2), ("Shots insidebox", 6),
    ("Shots outsidebox", 4), ("Fouls", 11), ("Corner Kicks", 4), ("Offsides", 1), ("Ball Possession", "55%"),
    ("Yellow Cards", 2), ("Red Cards", None), ("Goalkeeper Saves", 3), ("Total passes", 400), ("Passes accurate", 330),
    ("Passes %", "83%"), ("expected_goals", "1.45"),
]
AWAY_FULL = [(t, v) for t, v in HOME_FULL if t not in ("Ball Possession", "expected_goals")] + [("Ball Possession", "45%"), ("expected_goals", "0.70")]


def entry(team_id, stats):
    return {"team": {"id": team_id, "name": f"T{team_id}"}, "statistics": [{"type": t, "value": v} for t, v in stats]}


def item(provider_fixture_id, *entries, status="FT"):
    return {"fixture": {"id": provider_fixture_id, "status": {"short": status}}, "statistics": list(entries)}


def full_item(fid, home, away, home_stats=HOME_FULL, away_stats=AWAY_FULL):
    return item(fid, entry(home, home_stats), entry(away, away_stats))


def replace(stats, **changes):
    """Copia de una lista de estadísticas cambiando valores por tipo (None = null)."""
    out = [(t, changes.pop(t, v)) for t, v in stats]
    return out + list(changes.items())


class FakeStatsProvider:
    name = PROVIDER

    def __init__(self, items=None, *, hook=None, errors=None, extra=None):
        self.items = dict(items or {})  # provider_fixture_id → item raw
        self.hook = hook
        self.errors = list(errors or [])  # una excepción (o None) por llamada
        self.extra = extra or []  # items devueltos aunque no se pidan
        self.calls: list[list[int]] = []

    async def get_fixture_statistics(self, provider_fixture_ids):
        ids = normalize_fixture_ids(provider_fixture_ids)
        self.calls.append(ids)
        if self.hook:
            self.hook(ids)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        raw = [self.items[i] for i in ids if i in self.items] + self.extra
        return [parse_fixture_statistics(r) for r in raw]


@pytest.fixture
def season(db_session):
    """Temporada con partidos FT: external_id 1001.. con equipos (2n+1, 2n+2). Devuelve un dict."""
    cid, sid = make_competition(db_session, 39, name="Premier League", current_year=2025)
    # Temporada HISTÓRICA (no current y terminada hace más de 7 días): la de los pilotos M5.4A/B
    db_session.execute(update(Season).where(Season.id == sid).values(is_current=False, end_date=date.today() - timedelta(days=60)))
    db_session.commit()  # el código hace rollback de sus lecturas: lo preparado no debe perderse

    def add(n, start=1001, status="FT"):
        data = [make_fixture_data(start + i, home=2 * (start + i) + 1, away=2 * (start + i) + 2, status=status) for i in range(n)]
        team_ids = fixture_repository.ensure_teams(db_session, [t for f in data for t in (f.home_team, f.away_team)], PROVIDER)
        fixture_repository.upsert_fixtures(db_session, sid, data, team_ids, PROVIDER)
        db_session.commit()
        return [(f.external_id, f.home_team.external_id, f.away_team.external_id) for f in data]

    return {"competition_id": cid, "season_id": sid, "add": add}


def run(db, season, provider, **options):
    opts = BackfillOptions(**{"mode": "apply", **options})
    return asyncio.run(service.run_season_backfill(db, competition_id=season["competition_id"], season_id=season["season_id"], provider=provider, options=opts))


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


def batches(db, run_id):
    return [d for d in the_run(db, run_id).details if "fixture_ids" in d]


def codes(db, run_id):
    return [c["code"] for c in the_run(db, run_id).checks]


# --- Casos básicos ----------------------------------------------------------------------


def test_available_fixtures_are_normalized_with_synthetic_availability(db_session, season):
    matches = season["add"](2)
    provider = FakeStatsProvider({fid: full_item(fid, h, a) for fid, h, a in matches})
    out = run(db_session, season, provider)
    assert (out.status, out.stop_reason) == ("completed", None)
    c = out.counters
    assert (c["fixtures_targeted"], c["fixtures_attempted"], c["fixtures_available"]) == (2, 2, 2)
    assert (c["observations_created"], c["rows_created"], c["provider_requests"], c["blocking_count"]) == (2, 4, 1, 0)
    assert provider.calls == [[1001, 1002]]
    obs = observations(db_session, 1001)[0]
    kickoff = db_session.scalar(select(Fixture.kickoff_at).where(Fixture.external_id == 1001))
    assert obs.source == "backfill" and obs.available_at == kickoff + timedelta(hours=6)  # sintético
    assert obs.observed_at > kickoff + timedelta(days=30)  # momento real del backfill, no +6 h
    assert (obs.availability, obs.teams_returned, obs.fixture_status_at_fetch, obs.provider_fixture_id) == ("available", 2, "FT", "1001")
    home, away = rows(db_session, 1001)
    assert (home.side, away.side) == ("home", "away")
    assert (home.possession_pct, away.possession_pct, home.expected_goals) == (Decimal("55.00"), Decimal("45.00"), Decimal("1.450"))
    assert home.red_cards is None  # null del proveedor: nunca 0
    run_row = the_run(db_session, out.run_id)
    assert run_row.cursor_fixture_id == fixture_id(db_session, 1002)
    assert run_row.coverage["xg_pct"] == 100.0 and run_row.coverage["core_pct"] == 100.0
    assert batches(db_session, out.run_id)[0]["requested"] == [1001, 1002] and batches(db_session, out.run_id)[0]["available"] == 2


def test_partial_fixture(db_session, season):
    (fid, h, _a), = season["add"](1)
    out = run(db_session, season, FakeStatsProvider({fid: item(fid, entry(h, HOME_FULL), entry(_a, []))}))
    assert out.status == "completed" and out.counters["fixtures_partial"] == 1 and out.counters["warning_count"] == 1
    (obs,) = observations(db_session, fid)
    assert (obs.availability, obs.teams_returned) == ("partial", 1)
    assert [r.side for r in rows(db_session, fid)] == ["home"]  # solo el equipo válido
    assert "partial_statistics" in codes(db_session, out.run_id)


def test_empty_final_fixture(db_session, season):
    (fid, h, a), = season["add"](1)
    out = run(db_session, season, FakeStatsProvider({fid: item(fid, entry(h, []), entry(a, []))}))
    assert out.status == "completed" and out.counters["fixtures_empty"] == 1 and out.counters["warning_count"] == 0
    (obs,) = observations(db_session, fid)
    assert (obs.availability, obs.teams_returned) == ("empty", 0)
    assert rows(db_session, fid) == []  # no se inventan ceros
    assert batches(db_session, out.run_id)[0]["issues"]["INFO"] == {"final_fixture_empty": 1}


# --- BLOCKING --------------------------------------------------------------------------------


def test_more_than_two_teams_is_blocked_without_observation(db_session, season):
    (fid, h, a), = season["add"](1)
    out = run(db_session, season, FakeStatsProvider({fid: item(fid, entry(h, HOME_FULL), entry(a, AWAY_FULL), entry(77, HOME_FULL))}))
    assert out.status == "completed_with_errors" and out.counters["fixtures_blocked"] == 1
    assert observations(db_session) == [] and rows(db_session) == []
    check = the_run(db_session, out.run_id).checks[0]
    assert check["code"] == "too_many_teams" and check["detail"]["provider_team_ids"] == [h, a, 77]  # evidencia


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (lambda fid, h, a: item(fid, entry(None, HOME_FULL), entry(a, AWAY_FULL)), "team_without_id"),
        (lambda fid, h, a: item(fid, entry(h, HOME_FULL), entry(99999, AWAY_FULL)), "team_without_mapping"),
        (lambda fid, h, a: item(fid, entry(h, HOME_FULL), entry(h, AWAY_FULL)), "duplicate_team"),
        (lambda fid, h, a: full_item(fid, h, a, home_stats=replace(HOME_FULL, **{"Corner Kicks": -2})), "negative_value"),
        (lambda fid, h, a: full_item(fid, h, a, home_stats=replace(HOME_FULL, **{"Ball Possession": "120%"})), "percentage_over_100"),
    ],
)
def test_team_and_value_blocking_keeps_raw_but_does_not_normalize(db_session, season, make, code):
    (fid, h, a), = season["add"](1)
    out = run(db_session, season, FakeStatsProvider({fid: make(fid, h, a)}))
    assert out.status == "completed_with_errors" and out.counters["fixtures_blocked"] == 1
    assert code in codes(db_session, out.run_id)
    (obs,) = observations(db_session, fid)  # la observación raw se conserva como evidencia
    assert obs.is_latest and rows(db_session, fid) == []


def test_foreign_team_is_blocked(db_session, season):
    (fid, h, _a), (_f2, h2, _a2) = season["add"](2)
    provider = FakeStatsProvider({fid: item(fid, entry(h, HOME_FULL), entry(h2, AWAY_FULL)), _f2: full_item(_f2, h2, _a2)})
    out = run(db_session, season, provider)
    assert "team_not_in_fixture" in codes(db_session, out.run_id)
    assert rows(db_session, fid) == [] and len(rows(db_session, _f2)) == 2  # el otro partido sigue


def test_fixture_that_stops_being_final_during_download_is_blocked(db_session, season):
    (fid, h, a), = season["add"](1)

    def to_ns(_ids):
        db_session.execute(update(Fixture).where(Fixture.external_id == fid).values(status_short="NS"))
        db_session.commit()

    out = run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}, hook=to_ns))
    check = the_run(db_session, out.run_id).checks[0]
    assert check["code"] == "fixture_not_final" and check["detail"]["has_content"] is True
    assert observations(db_session) == [] and rows(db_session) == []
    assert db_session.scalar(select(Fixture.status_short).where(Fixture.external_id == fid)) == "NS"  # M5 no lo toca


def test_fixture_without_mapping_is_blocked_without_request(db_session, season):
    (fid, h, a), (f2, h2, a2) = season["add"](2)
    db_session.execute(text("UPDATE fixture_provider_mappings SET is_active = false WHERE external_id = :e"), {"e": str(fid)})
    db_session.commit()
    provider = FakeStatsProvider({f2: full_item(f2, h2, a2)})
    out = run(db_session, season, provider)
    assert provider.calls == [[f2]]
    assert out.counters["fixtures_blocked"] == 1 and "fixture_without_mapping" in codes(db_session, out.run_id)


def test_missing_and_unexpected_fixtures_in_response(db_session, season):
    (f1, h, a), (f2, _h2, _a2) = season["add"](2)
    provider = FakeStatsProvider({f1: full_item(f1, h, a)}, extra=[full_item(5555, h, a)])  # f2 no viene; 5555 no se pidió
    out = run(db_session, season, provider)
    c = out.counters
    assert (c["fixtures_available"], c["fixtures_missing_in_response"], c["fixtures_blocked"]) == (1, 1, 0)
    assert c["blocking_count"] == 1 and out.status == "completed_with_errors"
    assert "unexpected_fixture_in_response" in codes(db_session, out.run_id)
    assert {o.provider_fixture_id for o in observations(db_session)} == {str(f1)}  # ni f2 (no inventado) ni 5555


# --- WARNING / INFO ------------------------------------------------------------------------


def test_unparseable_unknown_and_consistency(db_session, season):
    (fid, h, a), = season["add"](1)
    home = replace(HOME_FULL, **{"Fouls": "-", "Total Shots": 30, "Passes accurate": 900, "Expected Assists": "0.4", "goals_prevented": "0.1"})
    away = replace(AWAY_FULL, **{"Ball Possession": "30%"})
    out = run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a, home, away)}))
    assert out.status == "completed" and out.counters["blocking_count"] == 0
    found = codes(db_session, out.run_id)
    for code in ("unparseable_value", "shot_components_mismatch", "shot_zones_mismatch", "passes_accurate_over_total", "possession_sum_out_of_range"):
        assert code in found
    issues = batches(db_session, out.run_id)[0]["issues"]
    assert issues["INFO"] == {"raw_only_stat_type": 1, "unknown_stat_type": 1}  # INFO no cuenta como warning
    home_row = rows(db_session, fid)[0]
    assert home_row.fouls is None and home_row.passes_accurate == 900  # el dato raro se conserva


def test_missing_xg_is_not_a_warning(db_session, season):
    (f1, h, a), (f2, h2, a2) = season["add"](2)
    no_xg = [(t, v) for t, v in HOME_FULL if t != "expected_goals"]
    provider = FakeStatsProvider({f1: full_item(f1, h, a), f2: full_item(f2, h2, a2, no_xg, [(t, v) for t, v in AWAY_FULL if t != "expected_goals"])})
    out = run(db_session, season, provider)
    assert out.counters["warning_count"] == 0
    coverage = the_run(db_session, out.run_id).coverage
    assert coverage["xg_pct"] == 50.0 and coverage["core_pct"] == 100.0 and coverage["fields_pct"]["expected_goals"] == 50.0
    assert coverage["fixtures"]["available"] == 2


def test_awarded_fixtures_are_not_requested(db_session, season):
    season["add"](1, start=1001, status="AWD")
    (fid, h, a), = season["add"](1, start=1002)
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = run(db_session, season, provider)
    assert provider.calls == [[fid]] and out.counters["fixtures_targeted"] == 1
    assert the_run(db_session, out.run_id).coverage["excluded_awarded"] == 1
    assert "awarded_without_statistics" in codes(db_session, out.run_id)


# --- Merge no destructivo ------------------------------------------------------------------


def _first_then(db, season, second_item_fn, **options):
    (fid, h, a), = season["add"](1)
    run(db, season, FakeStatsProvider({fid: full_item(fid, h, a)}))
    out = run(db, season, FakeStatsProvider({fid: second_item_fn(fid, h, a)}), refresh=True, **options)
    return fid, out


def test_available_then_partial_keeps_absent_team(db_session, season):
    fid, out = _first_then(db_session, season, lambda f, h, a: item(f, entry(h, HOME_FULL), entry(a, [])))
    old, new = observations(db_session, fid)
    assert (old.is_latest, new.is_latest, new.availability) == (False, True, "partial")
    home, away = rows(db_session, fid)
    assert away.observation_id == old.id and away.shots_total == 10  # se conserva, no se borra
    assert home.observation_id == new.id  # re-enlazado a la última que lo aporta
    assert out.counters["rows_unchanged"] == 1 and out.counters["rows_updated"] == 0
    check = next(c for c in the_run(db_session, out.run_id).checks if c["code"] == "degraded_observation")
    assert check["detail"]["teams_without_statistics_now"] == [away.team_id]


def test_available_then_empty_keeps_all_rows(db_session, season):
    fid, out = _first_then(db_session, season, lambda f, h, a: item(f, entry(h, []), entry(a, [])))
    assert observations(db_session, fid)[-1].availability == "empty"
    assert len(rows(db_session, fid)) == 2 and all(r.shots_total == 10 for r in rows(db_session, fid))
    assert "degraded_observation" in codes(db_session, out.run_id)


def test_null_does_not_erase_value_but_value_enriches_null_and_changes_update(db_session, season):
    second = lambda f, h, a: full_item(f, h, a, home_stats=replace(HOME_FULL, **{"Fouls": None, "Red Cards": 1, "Corner Kicks": 9}))  # noqa: E731
    fid, out = _first_then(db_session, season, second)
    home = rows(db_session, fid)[0]
    assert home.fouls == 11  # null nuevo: se conserva el valor (no destructivo)
    assert home.red_cards == 1  # null previo → valor: se enriquece
    assert home.corners == 9  # valor nuevo: se actualiza
    assert out.counters["rows_updated"] == 1 and out.counters["rows_unchanged"] == 1
    check = next(c for c in the_run(db_session, out.run_id).checks if c["code"] == "degraded_observation")
    assert check["detail"]["fields_lost"] == {str(home.team_id): ["fouls"]}
    # La observación raw vigente sí refleja el null del proveedor
    latest = observations(db_session, fid)[-1]
    assert {"type": "Fouls", "value": None} in latest.payload[0]["statistics"]


def test_same_payload_again_is_unchanged(db_session, season):
    fid, out = _first_then(db_session, season, lambda f, h, a: full_item(f, h, a))
    c = out.counters
    assert (c["observations_created"], c["observations_unchanged"], c["rows_unchanged"], c["rows_updated"]) == (0, 1, 2, 0)
    assert len(observations(db_session, fid)) == 1 and out.counters["warning_count"] == 0


def test_existing_observations_are_skipped_without_refresh(db_session, season):
    (fid, h, a), = season["add"](1)
    run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}))
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = run(db_session, season, provider)
    assert provider.calls == [] and out.counters["fixtures_targeted"] == 0 and out.status == "completed"
    assert the_run(db_session, out.run_id).coverage["skipped_existing"] == 1


# --- Lotes, transacciones y paradas --------------------------------------------------------------


def test_batches_of_twenty_without_open_transaction_during_http(db_session, season):
    matches = season["add"](25)
    seen = []
    provider = FakeStatsProvider({fid: full_item(fid, h, a) for fid, h, a in matches}, hook=lambda _ids: seen.append(db_session.in_transaction()))
    out = run(db_session, season, provider)
    assert [len(c) for c in provider.calls] == [20, 5] and seen == [False, False]
    assert out.counters["provider_requests"] == 2 and out.counters["fixtures_available"] == 25
    assert [len(d["fixture_ids"]) for d in batches(db_session, out.run_id)] == [20, 5]


def test_provider_error_rolls_back_only_the_failing_batch_and_resume_finishes(db_session, season):
    matches = season["add"](25)
    items = {fid: full_item(fid, h, a) for fid, h, a in matches}
    out = run(db_session, season, FakeStatsProvider(items, errors=[None, ProviderResponseError(PROVIDER, "502")]))
    assert (out.status, out.stop_reason) == ("failed", "provider_error")
    assert len(observations(db_session)) == 20  # el primer lote quedó; el segundo no escribió nada
    first = the_run(db_session, out.run_id)
    assert first.cursor_fixture_id == fixture_id(db_session, matches[19][0]) and first.provider_requests == 2
    assert cli.exit_code(out) == 2
    # Reanudación: solo los 5 restantes, sin duplicados
    provider = FakeStatsProvider(items)
    resumed = run(db_session, season, provider, resume=True)
    assert provider.calls == [[m[0] for m in matches[20:]]]
    assert resumed.status == "completed" and len(observations(db_session)) == 25
    assert db_session.scalar(select(func.count()).select_from(FixtureTeamStatistics)) == 50


def test_auth_failure(db_session, season):
    (fid, h, a), = season["add"](1)
    out = run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}, errors=[ProviderAuthError(PROVIDER, "token")]))
    run_row = the_run(db_session, out.run_id)
    assert (out.status, run_row.auth_failed, run_row.finished_at is not None) == ("failed", True, True)
    assert cli.exit_code(out) == 2 and observations(db_session) == []


def test_rate_limit_stops_resumably(db_session, season):
    (fid, h, a), = season["add"](1)
    out = run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}, errors=[ProviderRateLimitError(PROVIDER, "429")]))
    assert (out.status, out.stop_reason, the_run(db_session, out.run_id).rate_limited) == ("aborted", "rate_limited", True)
    assert cli.exit_code(out) == 2


def test_daily_budget_stops_after_a_batch_and_resume_continues(db_session, season):
    matches = season["add"](25)
    items = {fid: full_item(fid, h, a) for fid, h, a in matches}
    out = run(db_session, season, FakeStatsProvider(items), stats_daily_budget=3)  # 0+3 ≤ 3; 1+3 > 3
    assert (out.status, out.stop_reason) == ("aborted", "budget_exhausted") and cli.exit_code(out) == 1
    run_row = the_run(db_session, out.run_id)
    assert run_row.fixtures_attempted == 20 and run_row.details[-1]["stopped"] == "budget_exhausted"
    assert "--resume" in run_row.error_message
    resumed = run(db_session, season, FakeStatsProvider(items), resume=True)
    assert resumed.counters["fixtures_targeted"] == 5 and len(observations(db_session)) == 25


def test_live_sync_requests_count_towards_the_provider_cap(db_session, season):
    (fid, h, a), = season["add"](1)
    db_session.execute(text(
        "INSERT INTO live_sync_runs (job_type, trigger, status, lock_scope, finished_at, provider_requests) "
        "VALUES ('fixtures', 'cli', 'completed', 'live_sync', now(), 10)"
    ))
    db_session.commit()
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = run(db_session, season, provider, provider_daily_cap=12, stats_daily_budget=12)
    assert out.stop_reason == "budget_exhausted" and provider.calls == []


def test_lock_prevents_a_second_run(db_session, season):
    (fid, h, a), = season["add"](1)
    runs.create_run(db_session, trigger="cli", mode="apply", scope="fixtures")
    db_session.commit()
    provider = FakeStatsProvider({fid: full_item(fid, h, a)})
    out = run(db_session, season, provider)
    assert (out.status, out.run_id) == ("locked", None) and provider.calls == [] and cli.exit_code(out) == 2


def test_resume_without_interrupted_run(db_session, season):
    season["add"](1)
    with pytest.raises(service.NothingToResume):
        run(db_session, season, FakeStatsProvider(), resume=True)


def test_season_must_belong_to_competition(db_session, season):
    other, _ = make_competition(db_session, 140, name="Otra")
    with pytest.raises(service.SeasonMismatch):
        asyncio.run(service.run_season_backfill(db_session, competition_id=other, season_id=season["season_id"],
                                                provider=FakeStatsProvider(), options=BackfillOptions()))


def test_unexpected_exception_marks_run_failed(db_session, season, monkeypatch):
    (fid, h, a), = season["add"](1)

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(service, "evaluate_fixture", boom)
    with pytest.raises(RuntimeError):
        run(db_session, season, FakeStatsProvider({fid: full_item(fid, h, a)}))
    (run_row,) = list(db_session.scalars(select(StatisticsRun)))
    db_session.refresh(run_row)
    assert run_row.status == "failed" and run_row.error_message == "Error inesperado (RuntimeError)" and run_row.provider_requests == 1


# --- Dry-run ----------------------------------------------------------------------------------


def test_dry_run_evaluates_but_never_writes_statistics(db_session, season):
    (fid, h, a), (f2, h2, a2) = season["add"](2)
    provider = FakeStatsProvider({fid: full_item(fid, h, a), f2: item(f2, entry(h2, [("Fouls", -1)]), entry(a2, AWAY_FULL))})
    out = run(db_session, season, provider, mode="dry_run")
    assert out.status == "dry_run_completed" and cli.exit_code(out) == 1  # hay un BLOCKING
    c = out.counters
    assert (c["fixtures_available"], c["fixtures_blocked"], c["observations_created"], c["rows_created"]) == (1, 1, 2, 2)  # previstos
    assert observations(db_session) == [] and rows(db_session) == []  # nada escrito
    run_row = the_run(db_session, out.run_id)
    assert run_row.mode == "dry_run" and run_row.details and run_row.coverage["fixtures"]["available"] == 1


def test_dry_run_predicts_against_existing_state(db_session, season):
    fid, _ = _first_then(db_session, season, lambda f, h, a: full_item(f, h, a))  # ya hay estado
    provider = FakeStatsProvider({fid: full_item(fid, *db_session.execute(text(
        "SELECT ht.external_id::int, at.external_id::int FROM fixtures f JOIN team_provider_mappings ht ON ht.team_id = f.home_team_id "
        "JOIN team_provider_mappings at ON at.team_id = f.away_team_id WHERE f.external_id = :e"), {"e": fid}).one(),
        home_stats=replace(HOME_FULL, **{"Corner Kicks": 7}))})
    out = run(db_session, season, provider, mode="dry_run", refresh=True)
    c = out.counters
    assert (c["observations_created"], c["rows_updated"], c["rows_unchanged"]) == (1, 1, 1)
    assert rows(db_session, fid)[0].corners == 4  # el dry-run no cambió nada


# --- CLI --------------------------------------------------------------------------------------


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


def test_cli_target_missing_or_mismatch_does_nothing(wired, db_session, season):
    season["add"](1)
    assert cli.main(["--competition-id", str(season["competition_id"]), "--season", "2025", "--dry-run"]) == 2
    assert cli.main(["--competition-id", str(season["competition_id"]), "--season", "2025", "--dry-run", "--confirm-target", "otro:5432/x"]) == 2
    assert wired["built"] == 0 and db_session.scalar(select(func.count()).select_from(StatisticsRun)) == 0


def test_cli_dry_run_end_to_end(wired, db_session, season, capsys):
    (fid, h, a), = season["add"](1)
    wired["provider"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    code = cli.main(["--competition-id", str(season["competition_id"]), "--season", "2025", "--dry-run", "--confirm-target", TARGET])
    assert code == 0
    out = capsys.readouterr().out
    assert f"Destino BD:  {TARGET}" in out and "dry_run_completed" in out and "available 1" in out
    assert observations(db_session) == []


def test_cli_unknown_season_and_nothing_to_resume(wired, db_session, season):
    assert cli.main(["--competition-id", str(season["competition_id"]), "--season", "1999", "--confirm-target", TARGET]) == 2
    assert cli.main(["--competition-id", str(season["competition_id"]), "--season", "2025", "--resume", "--confirm-target", TARGET]) == 2
    assert wired["built"] == 1  # solo el intento de --resume construyó el proveedor; ninguna llamada
    assert wired["provider"].calls == []


def test_cli_fail_stale_run(wired, db_session):
    run_id = runs.create_run(db_session, trigger="scheduler", mode="apply", scope="fixtures")
    db_session.commit()
    assert cli.main(["--fail-stale-run", "--confirm-target", TARGET]) == 1  # demasiado reciente
    db_session.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(started_at=func.now() - timedelta(hours=2)))
    db_session.commit()
    assert cli.main(["--fail-stale-run", "--confirm-target", TARGET]) == 0
    assert the_run(db_session, run_id).status == "aborted"


def test_cli_argument_validation():
    for argv in (["--season", "2025"], ["--competition-id", "1"], ["--fail-stale-run", "--season", "2025"],
                 ["--competition-id", "1", "--season", "2025", "--budget", "0"],
                 ["--competition-id", "1", "--season", "2025", "--budget", "7000", "--provider-daily-cap", "6000"],
                 ["--competition-id", "1", "--season", "2025", "--stale-after-minutes", "5"]):
        with pytest.raises(SystemExit):
            cli.parse_args(argv)
    args = cli.parse_args(["--competition-id", "1", "--season", "2025", "--dry-run"])
    assert (args.budget, args.provider_daily_cap, args.dry_run) == (1500, 6000, True)


def test_exit_codes():
    def outcome(status, stop=None, **counters):
        return service.BackfillOutcome(1, status, stop, {"blocking_count": 0, "fixtures_missing_in_response": 0, **counters})

    assert cli.exit_code(outcome("completed")) == 0
    assert cli.exit_code(outcome("dry_run_completed")) == 0
    assert cli.exit_code(outcome("dry_run_completed", fixtures_missing_in_response=1)) == 1
    assert cli.exit_code(outcome("completed_with_errors")) == 1
    assert cli.exit_code(outcome("aborted", "budget_exhausted")) == 1
    assert cli.exit_code(outcome("aborted", "rate_limited")) == 2
    assert cli.exit_code(outcome("failed", "auth_failed")) == 2
    assert cli.exit_code(service.BackfillOutcome(None, "locked")) == 2
