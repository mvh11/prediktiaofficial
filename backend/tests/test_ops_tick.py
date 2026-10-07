"""Scheduler operativo (C6): runner ops_tick (guardas, recuperación, clasificación, JSON), live_sync
SKIPPED con el scheduler, protección de POST /sync/*, checks S1–S3 y workflows de GitHub Actions.
Los jobs se ejecutan en proceso con proveedores FALSOS contra la BD de tests; ninguna llamada real."""

import inspect
import json
import re
import subprocess
import sys
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db.session import get_db
from app.integrations.exceptions import ProviderResponseError
from app.jobs import live_sync as live_cli
from app.jobs import ops_tick
from app.jobs import statistics_reconcile as stats_cli
from app.main import app
from app.models import Fixture, FixtureStatisticsObservation, LiveSyncRun, Season, StatisticsRun
from app.repositories import fixture_repository
from app.repositories import live_sync_repository as live_runs
from app.repositories import statistics_run_repository as stats_runs
from app.services import statistics_reconcile_service as reconcile
from app.services.freshness_checks import run_checks
from tests.conftest import make_competition, make_fixture_data
from tests.test_live_sync_fixtures import FakeProvider, ft
from tests.test_statistics_service import FakeStatsProvider, full_item, item

TARGET = "ep-test.us-east-2.aws.neon.tech:5432/neondb"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
EXPECTED_CRON = {"catalog": "50 4 * * *", "live": "0 * * * *", "stats": "20 * * * *"}


# --- Clasificación (pura) ---------------------------------------------------------------------


def _live(status="completed", auth=False, rate=False):
    return {"id": 1, "status": status, "auth_failed": auth, "rate_limited": rate}


def _stats(status="completed", auth=False, rate=False, stopped=None):
    return {"id": 1, "status": status, "auth_failed": auth, "rate_limited": rate, "details": [{"stopped": stopped}] if stopped else []}


@pytest.mark.parametrize("exit_code, run, expected", [
    (0, None, ("SKIPPED", "locked")),
    (2, None, ("FAILED", "job_not_started")),
    (0, _live(), ("SUCCESS", None)),
    (1, _live("completed_with_errors"), ("DEGRADED", "competitions_failed")),
    (2, _live("completed_with_errors"), ("FAILED", "job_exit_error")),  # run cortado por DI (config_failed)
    (1, _live("completed_with_errors", rate=True), ("RETRYABLE", "provider_rate_limited")),
    (2, _live("completed_with_errors", auth=True), ("FAILED", "provider_auth_failed")),
    (2, _live("failed"), ("FAILED", "run_failed")),
])
def test_classify_live_sync(exit_code, run, expected):
    assert ops_tick.classify_live_sync(exit_code, run) == expected


@pytest.mark.parametrize("exit_code, run, expected", [
    (0, None, ("SKIPPED", "locked")),
    (2, None, ("FAILED", "job_not_started")),
    (0, _stats(), ("SUCCESS", None)),
    (1, _stats("completed_with_errors"), ("DEGRADED", "blocking_or_missing")),
    (1, _stats("aborted", stopped="budget_exhausted"), ("RETRYABLE", "budget_exhausted")),
    (2, _stats("aborted", rate=True, stopped="rate_limited"), ("RETRYABLE", "provider_rate_limited")),
    (2, _stats("failed", stopped="provider_error"), ("RETRYABLE", "provider_error")),
    (2, _stats("failed", auth=True, stopped="auth_failed"), ("FAILED", "provider_auth_failed")),
    (2, _stats("failed"), ("FAILED", "run_failed")),
])
def test_classify_statistics(exit_code, run, expected):
    assert ops_tick.classify_statistics(exit_code, run) == expected


@pytest.mark.parametrize("status, expected", [("PASS", ("SUCCESS", None)), ("WARNING", ("DEGRADED", "freshness_warning")), ("ERROR", ("DEGRADED", "freshness_error"))])
def test_classify_check(status, expected):
    assert ops_tick.classify_check(0, {"id": 1, "status": "completed", "freshness": {"status": status}}) == expected
    assert ops_tick.classify_check(0, None) == ("SKIPPED", "locked")


def test_tick_exit_codes_and_json_line():
    result = ops_tick.TickResult("live", NOW)
    assert result.exit_code() == 0
    result.event("freshness_warning")
    assert result.exit_code() == 0
    result.event("freshness_error")
    assert result.exit_code() == 1
    result.worsen("FAILED", "db_unavailable")
    assert result.exit_code() == 2
    payload = json.loads(result.as_json())
    assert set(payload) >= {"task", "tick_utc", "class", "run_id", "exit", "reason", "events"}
    assert payload["class"] == "FAILED" and payload["tick_utc"] == NOW.isoformat()


def test_runner_requires_utc():
    with pytest.raises(ValueError):
        ops_tick.run_tick("live", target=TARGET, now=datetime(2026, 10, 7, 1, 0), session_factory=None)
    with pytest.raises(ValueError):
        ops_tick.run_tick("live", target=TARGET, now=datetime(2026, 10, 7, 1, 0, tzinfo=timezone(timedelta(hours=-3))), session_factory=None)


def test_estimated_requests_are_conservative():
    assert ops_tick.estimated_requests("live", 26) == 26 * 3
    assert ops_tick.estimated_requests("catalog", 26) == 27 * 3


# --- Workflows ----------------------------------------------------------------------------


def _workflow(task):
    yaml = pytest.importorskip("yaml")
    raw = (WORKFLOWS / f"ops-{task}.yml").read_text(encoding="utf-8")
    return raw, yaml.safe_load(raw)


@pytest.mark.parametrize("task", ["catalog", "live", "stats"])
def test_workflow_schedule_task_and_concurrency(task):
    raw, wf = _workflow(task)
    on = wf.get("on", wf.get(True))  # YAML 1.1 lee `on` como True
    assert on["schedule"] == [{"cron": EXPECTED_CRON[task]}] and "workflow_dispatch" in on
    assert wf["concurrency"] == {"group": f"ops-{task}", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["tick"]
    assert job["if"] == "${{ vars.PREDIKTIA_SCHEDULER_ENABLED == 'true' }}"  # gate de encendido
    assert job["env"]["TZ"] == "UTC"
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert runs[-1] == f"python -m app.jobs.ops_tick {task}"
    others = {t for t in EXPECTED_CRON if t != task}
    assert not any(f"ops_tick {o}" in raw for o in others)  # selección de tarea inequívoca


SECRET_LIKE = re.compile(r"postgres(ql)?(\+\w+)?://|npg_|neon\.tech|x-apisports-key|[0-9a-f]{32}", re.I)
# Única excepción: el SHA de 40 hex de una action fijada (`uses: owner/repo@<sha>`); el resto de la línea se sigue escaneando.
PINNED_ACTION_SHA = re.compile(r"^(\s*(?:-\s+)?uses:\s+[\w.-]+/[\w.-]+@)[0-9a-f]{40}(?=\s|$)", re.M)


def _secret_like(raw):
    return SECRET_LIKE.search(PINNED_ACTION_SHA.sub(r"\1<sha>", raw))


@pytest.mark.parametrize("task", ["catalog", "live", "stats"])
def test_workflow_secrets_are_referenced_never_materialized(task):
    raw, wf = _workflow(task)
    env = wf["jobs"]["tick"]["env"]
    for name in ("DATABASE_URL", "API_FOOTBALL_KEY", "PREDIKTIA_EXPECTED_DB_TARGET"):
        assert env[name] == "${{ secrets.%s }}" % name
    assert not _secret_like(raw)


def test_secret_scan_ignores_only_pinned_action_shas():
    sha = "0123456789abcdef0123456789abcdef01234567"
    assert not _secret_like(f"    steps:\n      - uses: actions/checkout@{sha} # v4.4.0\n")
    assert _secret_like(f"    env:\n      TOKEN: {sha}\n")  # mismo hex fuera de `uses:` se detecta
    assert _secret_like(f"      - run: echo {sha}\n")
    assert _secret_like(f"      - uses: actions/checkout@{sha} # {sha[:32]}\n")  # el resto de la línea se escanea


# --- Separación y subprocesos ------------------------------------------------------------------


def test_runner_has_no_domain_logic_or_cross_job_transaction():
    source = inspect.getsource(ops_tick)
    for forbidden in ("fixture_sync_service", "catalog_sync_service", "statistics_service", "statistics_reconcile_service",
                      "run_live_reconcile", "sync_fixtures(", "INSERT ", "UPDATE ", "DELETE "):
        assert forbidden not in source
    assert "FIRST_ACQUISITION_LOOKBACK" not in source and "CHECKPOINTS" not in source  # no toca la política


def test_subprocess_runner_launches_existing_cli_in_its_own_process(monkeypatch, capsys):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return SimpleNamespace(returncode=0, stdout="salida humana\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ops_tick.subprocess_runner("app.jobs.live_sync", ["fixtures", "--trigger", "scheduler"]) == 0
    assert seen["argv"] == [sys.executable, "-m", "app.jobs.live_sync", "fixtures", "--trigger", "scheduler"]
    assert seen["kwargs"]["cwd"] == ops_tick.BACKEND_DIR
    captured = capsys.readouterr()
    assert captured.out == "" and "salida humana" in captured.err  # stdout queda para el JSON


def test_policy_constants_unchanged():
    assert reconcile.FIRST_ACQUISITION_LOOKBACK == timedelta(days=7)
    assert reconcile.CHECKPOINTS == {"T1": timedelta(hours=2), "T2": timedelta(hours=6), "T3": timedelta(hours=24), "T4": timedelta(hours=48)}
    assert ops_tick.STALE_AFTER_MINUTES == 60 and ops_tick.AUTH_CIRCUIT_WINDOW == timedelta(hours=6) and ops_tick.PROVIDER_DAILY_CAP == 6000


# --- POST /sync/* -------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    calls = []

    async def fake_fixtures(*args, **kwargs):
        calls.append("fixtures")
        return {"fixtures_synced": 0, "competitions": []}

    async def fake_catalog(*args, **kwargs):
        calls.append("catalog")
        return {"competitions": [], "competitions_synced": 0, "teams_synced": 0}

    monkeypatch.setattr("app.services.fixture_sync_service.sync_fixtures", fake_fixtures)
    monkeypatch.setattr("app.services.catalog_sync_service.sync_catalog", fake_catalog)
    app.dependency_overrides[get_db] = lambda: None
    try:
        yield TestClient(app), calls
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_sync_endpoints_are_disabled_by_default(api, monkeypatch):
    client, calls = api
    assert Settings.model_fields["sync_endpoints_enabled"].default is False
    monkeypatch.setattr("app.api.errors.get_settings", lambda: SimpleNamespace(sync_endpoints_enabled=False))
    for path in ("/sync/fixtures", "/sync/catalog"):
        response = client.post(path)
        assert response.status_code == 403 and "ops_tick" in response.json()["detail"]
    assert calls == []  # ni BD ni proveedor


def test_sync_endpoints_can_be_enabled_explicitly_for_development(api, monkeypatch):
    client, calls = api
    monkeypatch.setattr("app.api.errors.get_settings", lambda: SimpleNamespace(sync_endpoints_enabled=True))
    assert client.post("/sync/fixtures").status_code == 200
    assert calls == ["fixtures"]


# --- Integración con la BD de tests (jobs en proceso, proveedores falsos) ---------------------


@pytest.fixture
def ops(db_session, monkeypatch):
    state = {"live": FakeProvider(), "stats": FakeStatsProvider(), "calls": []}
    monkeypatch.setattr(live_cli, "_configured_target", lambda: TARGET)
    monkeypatch.setattr(stats_cli, "_configured_target", lambda: TARGET)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(live_cli, "default_provider", lambda: state["live"])
    monkeypatch.setattr(stats_cli, "default_provider", lambda: state["stats"])
    for name in (live_cli.TARGET_ENV, stats_cli.TARGET_ENV):
        monkeypatch.delenv(name, raising=False)

    def runner(module, args):
        state["calls"].append((module, list(args)))
        return {ops_tick.LIVE_SYNC: live_cli.main, ops_tick.STATS: stats_cli.main}[module](args)

    def tick(task, at=None, factory=None):
        return ops_tick.run_tick(task, target=TARGET, now=at or NOW, session_factory=factory or (lambda: nullcontext(db_session)), runner=runner)

    state["tick"] = tick
    return state


def jobs(state):
    """Pasos lanzados que no son recuperación de stale."""
    return [(m, a[0] if m == ops_tick.LIVE_SYNC else "reconcile") for m, a in state["calls"] if "--fail-stale-run" not in a]


def current_league(db, external_id=265, name="Liga live"):
    _cid, sid = make_competition(db, external_id, name=name)
    db.commit()
    return sid


def add_ft(db, sid, n=1, start=1001, kickoff=None):
    data = [make_fixture_data(start + i, home=2 * (start + i) + 1, away=2 * (start + i) + 2, status="FT", kickoff_at=kickoff or NOW - timedelta(hours=5)) for i in range(n)]
    team_ids = fixture_repository.ensure_teams(db, [t for f in data for t in (f.home_team, f.away_team)], "api-football")
    fixture_repository.upsert_fixtures(db, sid, data, team_ids, "api-football")
    db.commit()
    return [(f.external_id, f.home_team.external_id, f.away_team.external_id) for f in data]


def insert_live_run(db, **values):
    cols = {"job_type": "fixtures", "trigger": "scheduler", "status": "completed", "lock_scope": "live_sync", "finished_at": NOW, **values}
    placeholders = [f"CAST(:{k} AS jsonb)" if k == "details" else f":{k}" for k in cols]
    db.execute(text(f"INSERT INTO live_sync_runs ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"), cols)
    db.commit()


def step(result, name):
    return next(s for s in result.steps if s["step"] == name)


@pytest.mark.db
def test_live_tick_runs_fixtures_then_check_in_order(ops, db_session):
    current_league(db_session)
    ops["live"] = FakeProvider({265: [ft(1, kickoff_at=NOW - timedelta(hours=5))]})
    result = ops["tick"]("live")
    assert jobs(ops) == [(ops_tick.LIVE_SYNC, "fixtures"), (ops_tick.LIVE_SYNC, "check")]
    assert [s["step"] for s in result.steps if s["step"] != "stale_recovery"] == ["fixtures", "check"]
    run = db_session.get(LiveSyncRun, result.run_id)
    assert (run.job_type, run.trigger, run.status) == ("fixtures", "scheduler", "completed")
    assert result.cls in ("SUCCESS", "DEGRADED") and result.metrics["fixtures"]["competitions_attempted"] >= 1
    assert "freshness" in result.metrics


@pytest.mark.db
def test_live_then_stats_consumes_the_final_state(ops, db_session):
    current_league(db_session)
    ops["live"] = FakeProvider({265: [ft(1, kickoff_at=NOW - timedelta(hours=5))]})
    ops["tick"]("live")
    ops["stats"] = FakeStatsProvider({1: full_item(1, 1, 2)})
    result = ops["tick"]("stats")
    assert result.cls == "SUCCESS" and ops["stats"].calls == [[1]]
    (obs,) = db_session.scalars(select(FixtureStatisticsObservation)).all()
    assert obs.source == "live" and obs.available_at == obs.observed_at
    run = db_session.get(StatisticsRun, result.run_id)
    assert (run.scope, run.trigger) == ("live", "scheduler")
    assert result.metrics["statistics"]["fixtures_available"] == 1


@pytest.mark.db
def test_overlap_live_lock_does_not_block_stats(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    live_runs.create_run(db_session, job_type="fixtures", trigger="cli")  # live_sync en curso
    db_session.commit()
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    assert ops["tick"]("stats").cls == "SUCCESS"


@pytest.mark.db
def test_live_locked_is_skipped_without_provider_calls(ops, db_session):
    current_league(db_session)
    live_runs.create_run(db_session, job_type="catalog", trigger="cli")
    db_session.commit()
    ops["live"] = FakeProvider({265: [ft(1)]})
    result = ops["tick"]("live")
    assert ops["live"].calls == [] and step(result, "fixtures")["class"] == "SKIPPED"
    assert (result.reason in ("locked", "freshness_warning", "freshness_error")) and result.cls in ("SKIPPED", "DEGRADED")
    assert result.exit_code() in (0, 1) and result.cls != "FAILED"


@pytest.mark.db
def test_stats_locked_is_skipped(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    stats_runs.create_run(db_session, trigger="cli", mode="apply", scope="fixtures")
    db_session.commit()
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    result = ops["tick"]("stats")
    assert (result.cls, result.reason, result.exit_code()) == ("SKIPPED", "locked", 0) and ops["stats"].calls == []


@pytest.mark.db
def test_second_consecutive_skip_raises_skipped_twice(ops, db_session, monkeypatch):
    monkeypatch.setattr(ops_tick, "STALE_AFTER_MINUTES", 180)  # que el holder no se recupere como stale
    run_id = stats_runs.create_run(db_session, trigger="cli", mode="apply", scope="fixtures")
    db_session.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(started_at=NOW - timedelta(minutes=90)))
    db_session.commit()
    result = ops["tick"]("stats")
    assert result.cls == "SKIPPED" and [e["event"] for e in result.events] == ["skipped_twice"]
    assert result.events[0]["severity"] == "WARNING" and result.events[0]["holder_run_id"] == run_id


@pytest.mark.db
def test_stale_run_is_recovered_before_the_job(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    run_id = stats_runs.create_run(db_session, trigger="scheduler", mode="apply", scope="live")
    db_session.execute(update(StatisticsRun).where(StatisticsRun.id == run_id).values(started_at=NOW - timedelta(hours=2)))
    db_session.commit()
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    result = ops["tick"]("stats")
    db_session.expire_all()
    assert db_session.get(StatisticsRun, run_id).status == "aborted"
    assert "stale_recovered" in [e["event"] for e in result.events] and result.cls == "SUCCESS"


@pytest.mark.db
def test_recent_running_run_is_not_recovered_and_tick_is_skipped(ops, db_session):
    stats_runs.create_run(db_session, trigger="scheduler", mode="apply", scope="live")
    db_session.commit()
    result = ops["tick"]("stats")
    assert result.cls == "SKIPPED" and "stale_recovered" not in [e["event"] for e in result.events]


@pytest.mark.db
def test_live_failure_does_not_stop_stats(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid, start=5001)

    def boom(_league):
        raise RuntimeError("bug")

    ops["live"] = FakeProvider({265: [ft(1)]}, hook=boom)
    live = ops["tick"]("live")
    assert step(live, "fixtures")["class"] == "FAILED" and live.cls == "FAILED" and live.exit_code() == 2
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    assert ops["tick"]("stats").cls == "SUCCESS"


@pytest.mark.db
def test_stats_failure_does_not_stop_future_live(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid, start=5001)
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)}, errors=[RuntimeError("bug")])
    stats = ops["tick"]("stats")
    assert stats.cls == "FAILED" and "job_failed" in [e["event"] for e in stats.events]
    ops["live"] = FakeProvider({265: [ft(1, kickoff_at=NOW - timedelta(hours=5))]})
    live = ops["tick"]("live")
    assert step(live, "fixtures")["class"] == "SUCCESS"


@pytest.mark.db
def test_provider_error_in_stats_is_retryable(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)}, errors=[ProviderResponseError("api-football", "502")])
    result = ops["tick"]("stats")
    assert (result.cls, result.reason) == ("RETRYABLE", "provider_error")


@pytest.mark.db
def test_auth_circuit_breaker_skips_provider_jobs_for_six_hours(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    insert_live_run(db_session, status="completed_with_errors", auth_failed=True, started_at=NOW - timedelta(hours=1, minutes=2), finished_at=NOW - timedelta(hours=1))
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    ops["live"] = FakeProvider({265: [ft(1)]})
    for task in ("stats", "live", "catalog"):
        result = ops["tick"](task)
        assert result.reason == "provider_auth_circuit_open" or result.steps[-1]["step"] == "check"
        assert "provider_auth_circuit_open" in [e["event"] for e in result.events] and result.exit_code() == 1
    assert ops["stats"].calls == [] and ops["live"].calls == []
    assert [j for j in jobs(ops) if j[1] != "check"] == []  # el check (sin proveedor) sí corre
    later = ops["tick"]("stats", at=NOW + timedelta(hours=5, minutes=1))  # 6 h después del fallo: un intento
    assert later.cls == "SUCCESS" and ops["stats"].calls == [[fid]]


@pytest.mark.db
def test_auth_circuit_closes_after_a_successful_manual_run(ops, db_session):
    insert_live_run(db_session, status="completed_with_errors", auth_failed=True, started_at=NOW - timedelta(hours=2), finished_at=NOW - timedelta(hours=2))
    insert_live_run(db_session, job_type="catalog", trigger="cli", provider_requests=5, started_at=NOW - timedelta(minutes=30), finished_at=NOW - timedelta(minutes=29))
    result = ops["tick"]("stats")
    assert "provider_auth_circuit_open" not in [e["event"] for e in result.events]


@pytest.mark.db
def test_transient_rate_limit_does_not_block_next_tick_but_quota_does(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    insert_live_run(db_session, status="completed_with_errors", rate_limited=True, started_at=NOW - timedelta(minutes=50), finished_at=NOW - timedelta(minutes=49),
                    details=json.dumps([{"error": "Límite de peticiones por minuto superado"}]))
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    assert ops["tick"]("stats").cls == "SUCCESS"
    db_session.execute(text(
        "INSERT INTO statistics_runs (trigger, mode, scope, status, finished_at, rate_limited, details) "
        "VALUES ('scheduler', 'apply', 'live', 'aborted', now(), true, CAST(:d AS jsonb))"
    ), {"d": json.dumps([{"stopped": "rate_limited", "error": "ProviderQuotaExceededError"}])})
    db_session.commit()
    blocked = ops["tick"]("stats", at=NOW + timedelta(minutes=1))
    assert (blocked.cls, blocked.reason) == ("SKIPPED", "provider_quota_exhausted") and len(ops["stats"].calls) == 1
    tomorrow = datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(minutes=20)
    assert ops["tick"]("stats", at=tomorrow).reason != "provider_quota_exhausted"


@pytest.mark.db
def test_quota_detected_from_live_sync_competition_errors(ops, db_session):
    insert_live_run(db_session, status="completed_with_errors", rate_limited=True, started_at=NOW - timedelta(minutes=50), finished_at=NOW - timedelta(minutes=49),
                    details=json.dumps([{"error": "Cuota diaria de peticiones agotada: {'requests': 'limit'}"}]))
    result = ops["tick"]("live")
    assert "provider_quota_exhausted" in [e["event"] for e in result.events] and ops["live"].calls == []


@pytest.mark.db
def test_db_unavailable_launches_nothing(ops):
    class Down:
        def __enter__(self):
            raise OperationalError("SELECT 1", {}, Exception("down"))

        def __exit__(self, *exc):
            return False

    result = ops["tick"]("stats", factory=Down)
    assert (result.cls, result.reason, result.exit_code()) == ("FAILED", "db_unavailable", 2)
    assert ops["calls"] == [] and [e["event"] for e in result.events] == ["db_unavailable"]


@pytest.mark.db
def test_restart_does_not_replay(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    ops["tick"]("stats")
    again = ops["tick"]("stats", at=NOW + timedelta(minutes=2))  # el scheduler se reinicia y vuelve a lanzar
    assert again.metrics["statistics"]["fixtures_targeted"] == 0 and len(ops["stats"].calls) == 1


@pytest.mark.db
def test_multi_instance_second_tick_is_skipped_without_duplicate_calls(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    stats_runs.create_run(db_session, trigger="scheduler", mode="apply", scope="live")  # la otra instancia
    db_session.commit()
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    result = ops["tick"]("stats")
    assert result.cls == "SKIPPED" and ops["stats"].calls == []
    assert db_session.query(StatisticsRun).count() == 1  # ningún run duplicado


@pytest.mark.db
def test_dormant_seasons_untouched(ops, db_session):
    _cid, sid = make_competition(db_session, 4, name="Torneo cerrado", current_year=2024)
    db_session.execute(update(Season).where(Season.id == sid).values(start_date=date(2024, 6, 14), end_date=date(2024, 7, 14)))
    db_session.commit()
    add_ft(db_session, sid, kickoff=NOW - timedelta(days=400))
    ops["live"] = FakeProvider({4: [ft(9)]})
    ops["tick"]("live")
    ops["tick"]("stats")
    assert ops["live"].calls == [] and ops["stats"].calls == []


@pytest.mark.db
def test_backlog_outside_lookback_is_observed_not_drained(ops, db_session):
    sid = current_league(db_session)
    (old, h, a), = add_ft(db_session, sid, kickoff=NOW - timedelta(days=10))
    ops["stats"] = FakeStatsProvider({old: full_item(old, h, a)})
    result = ops["tick"]("stats")
    assert ops["stats"].calls == [] and result.metrics["statistics"]["backlog_outside_lookback"] == 1


@pytest.mark.db
def test_backlog_growth_event(ops, db_session):
    sid = current_league(db_session)
    add_ft(db_session, sid, kickoff=NOW - timedelta(days=10))
    ops["tick"]("stats")
    add_ft(db_session, sid, start=2001, kickoff=NOW - timedelta(days=11))
    result = ops["tick"]("stats", at=NOW + timedelta(minutes=5))
    assert [e for e in result.events if e["event"] == "backlog_growth"][0]["current"] == 2


@pytest.mark.db
def test_budget_guard_skips_live_but_check_still_runs(ops, db_session):
    current_league(db_session)
    insert_live_run(db_session, provider_requests=5990, started_at=NOW - timedelta(minutes=30), finished_at=NOW - timedelta(minutes=29))
    ops["live"] = FakeProvider({265: [ft(1)]})
    result = ops["tick"]("live")
    assert ops["live"].calls == [] and "budget_guard" in [e["event"] for e in result.events]
    assert jobs(ops) == [(ops_tick.LIVE_SYNC, "check")]


@pytest.mark.db
def test_stats_budget_exhausted_is_retryable(ops, db_session):
    sid = current_league(db_session)
    (fid, h, a), = add_ft(db_session, sid)
    db_session.execute(text(
        "INSERT INTO statistics_runs (trigger, mode, scope, status, finished_at, provider_requests) "
        "VALUES ('cli', 'apply', 'fixtures', 'completed', now(), 1499)"
    ))
    db_session.commit()
    ops["stats"] = FakeStatsProvider({fid: full_item(fid, h, a)})
    result = ops["tick"]("stats")
    assert (result.cls, result.reason) == ("RETRYABLE", "budget_exhausted") and ops["stats"].calls == []
    assert "budget_guard" in [e["event"] for e in result.events]


@pytest.mark.db
def test_check_reads_only_and_reports_statistics_metrics(ops, db_session):
    sid = current_league(db_session)
    add_ft(db_session, sid, kickoff=NOW - timedelta(hours=30))  # primer fetch vencido > 24 h
    before = db_session.execute(text("SELECT md5(string_agg(f::text, ',' ORDER BY f.id)) FROM fixtures f")).scalar()
    ops["live"] = FakeProvider({})
    result = ops["tick"]("live")
    after = db_session.execute(text("SELECT md5(string_agg(f::text, ',' ORDER BY f.id)) FROM fixtures f")).scalar()
    assert before == after
    overdue = [e for e in result.events if e["event"] == "first_acquisition_overdue"]
    assert overdue and overdue[0]["severity"] == "ERROR" and result.metrics["first_acquisition_overdue"] == 1
    assert result.exit_code() == 1


@pytest.mark.db
def test_main_prints_exactly_one_json_line_without_secrets(ops, db_session, monkeypatch, capsys):
    monkeypatch.setattr(ops_tick, "database_target", lambda _url: TARGET)
    monkeypatch.setenv(ops_tick.TARGET_ENV, TARGET)

    def runner(module, args):
        return {ops_tick.LIVE_SYNC: live_cli.main, ops_tick.STATS: stats_cli.main}[module](args)

    code = ops_tick.main(["stats"], runner=runner)
    out = capsys.readouterr().out.strip().splitlines()
    json_lines = [line for line in out if line.startswith("{")]
    assert len(json_lines) == 1 and code == 0
    payload = json.loads(json_lines[0])
    assert payload["task"] == "stats" and payload["class"] == "SUCCESS"
    assert not re.search(r"postgres|npg_|password|api_key|neon\.tech", json_lines[0], re.I)


def test_main_target_mismatch_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(ops_tick, "database_target", lambda _url: TARGET)
    called = []
    code = ops_tick.main(["live", "--confirm-target", "otro:5432/x"], runner=lambda m, a: called.append(m) or 0)
    payload = json.loads(capsys.readouterr().out.strip())
    assert (code, payload["class"], payload["reason"], called) == (2, "FAILED", "target_mismatch", [])
    monkeypatch.delenv(ops_tick.TARGET_ENV, raising=False)
    code = ops_tick.main(["live"], runner=lambda m, a: called.append(m) or 0)
    assert code == 2 and json.loads(capsys.readouterr().out.strip())["reason"] == "target_missing" and called == []


# --- live_sync con el scheduler -------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("job", ["fixtures", "catalog", "check"])
def test_live_sync_scheduler_locked_is_skipped_exit_0(ops, db_session, capsys, job):
    scope_job = "check" if job == "check" else "catalog"
    live_runs.create_run(db_session, job_type=scope_job, trigger="cli")
    db_session.commit()
    ops["live"] = FakeProvider({265: [ft(1)]})
    assert live_cli.main([job, "--trigger", "scheduler", "--confirm-target", TARGET]) == 0
    assert "SKIPPED" in capsys.readouterr().out and ops["live"].calls == []
    assert live_cli.main([job, "--confirm-target", TARGET]) == 2  # manual: sin cambios


# --- Frescura S1–S3 -------------------------------------------------------------------------


def _check(report, check_id):
    return next(c for c in report["checks"] if c["id"] == check_id)


@pytest.mark.db
def test_freshness_statistics_checks(db_session):
    now = live_runs.now(db_session)
    report = run_checks(db_session, now)
    assert [_check(report, c)["status"] for c in ("S1_first_acquisition_overdue", "S2_backlog_outside_lookback", "S3_statistics_reconcile_last_run")] == ["PASS", "INFO", "INFO"]
    sid = current_league(db_session)
    add_ft(db_session, sid, kickoff=now - timedelta(hours=5))  # primer fetch vencido hace 30 min: aún no
    assert _check(run_checks(db_session, now), "S1_first_acquisition_overdue")["status"] == "PASS"
    add_ft(db_session, sid, start=2001, kickoff=now - timedelta(hours=8))  # vencido hace 3,5 h
    assert _check(run_checks(db_session, now), "S1_first_acquisition_overdue")["status"] == "WARNING"
    add_ft(db_session, sid, start=3001, kickoff=now - timedelta(hours=40))  # vencido hace > 24 h
    s1 = _check(run_checks(db_session, now), "S1_first_acquisition_overdue")
    assert (s1["status"], s1["count"]) == ("ERROR", 2)
    add_ft(db_session, sid, start=4001, kickoff=now - timedelta(days=10))
    s2 = _check(run_checks(db_session, now), "S2_backlog_outside_lookback")
    assert (s2["status"], s2["count"]) == ("INFO", 1)


@pytest.mark.db
@pytest.mark.parametrize("hours, expected", [(1, "PASS"), (3, "WARNING"), (30, "ERROR")])
def test_freshness_last_live_statistics_run(db_session, hours, expected):
    now = live_runs.now(db_session)
    db_session.execute(text(
        "INSERT INTO statistics_runs (trigger, mode, scope, status, started_at, finished_at) "
        "VALUES ('scheduler', 'apply', 'live', 'completed', :s, :f)"
    ), {"s": now - timedelta(hours=hours, minutes=1), "f": now - timedelta(hours=hours)})
    db_session.flush()
    assert _check(run_checks(db_session, now), "S3_statistics_reconcile_last_run")["status"] == expected


@pytest.mark.db
def test_freshness_statistics_ignore_dormant_and_observed(db_session):
    now = live_runs.now(db_session)
    _cid, dormant = make_competition(db_session, 4, name="Cerrado", current_year=2024)
    db_session.execute(update(Season).where(Season.id == dormant).values(start_date=date(2024, 6, 14), end_date=date(2024, 7, 14)))
    db_session.commit()
    add_ft(db_session, dormant, kickoff=now - timedelta(days=400))
    sid = current_league(db_session)
    (fid, _h, _a), = add_ft(db_session, sid, start=2001, kickoff=now - timedelta(hours=30))
    internal = db_session.scalar(select(Fixture.id).where(Fixture.external_id == fid))
    db_session.execute(text(
        "INSERT INTO fixture_statistics_observations (fixture_id, provider, provider_fixture_id, source, availability, teams_returned, "
        "payload, payload_hash, observed_at, last_observed_at, available_at) VALUES (:f, 'api-football', :p, 'live', 'empty', 0, "
        "'[]'::jsonb, :h, :n, :n, :n)"
    ), {"f": internal, "p": str(fid), "h": "0" * 64, "n": now})
    db_session.flush()
    report = run_checks(db_session, now)
    assert _check(report, "S1_first_acquisition_overdue")["status"] == "PASS"
    assert _check(report, "S2_backlog_outside_lookback")["count"] == 0
