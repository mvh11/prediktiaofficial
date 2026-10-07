"""Runner del scheduler operativo (C6): UNA pasada de una tarea, sin lógica de dominio.

Uso (desde backend/; lo lanza el workflow programado, fuente única de ejecución):
    python -m app.jobs.ops_tick catalog|live|stats [--confirm-target <host>:<puerto>/<bd>]

Tareas (horario en UTC, ver .github/workflows/ops-*.yml):
- catalog (04:50 diario): live_sync catalog;
- live (:00 cada hora): live_sync fixtures y después live_sync check;
- stats (:20 cada hora): statistics_reconcile.

Cada pasada, en este orden:
1. conectividad con la BD (SELECT 1). Si falla: FAILED db_unavailable, sin subprocesos ni
   peticiones al proveedor;
2. recuperación del run colgado del ámbito (--fail-stale-run, 60 min, en subproceso);
3. guardas de solo lectura: circuit breaker de credenciales (6 h), cuota diaria agotada (hasta la
   medianoche UTC) y, para catalog/live, presupuesto combinado (6000/día);
4. el CLI existente en SUBPROCESO con --trigger scheduler (cada job con su proceso, su sesión y
   sus transacciones; ningún job llama a otro);
5. lectura del run que dejó el job (live_sync_runs / statistics_runs) y clasificación:
   SUCCESS, SKIPPED, DEGRADED, FAILED o RETRYABLE;
6. UNA línea JSON en stdout (la salida humana de los jobs va a stderr). Sin secretos.

El runner solo hace lecturas; las escrituras las hacen los jobs. Salida: 2 si la clase es FAILED,
1 si hay algún evento ERROR o CRITICAL (el workflow queda en rojo: es la alerta de la fase 1),
0 en el resto (SUCCESS, SKIPPED, DEGRADED y RETRYABLE sin eventos graves).
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.integrations.football.api_football import MAX_ATTEMPTS
from app.jobs.history_backfill import database_target

TARGET_ENV = "PREDIKTIA_EXPECTED_DB_TARGET"
BACKEND_DIR = Path(__file__).resolve().parents[2]
TASKS = ("catalog", "live", "stats")
INTERVALS = {"catalog": timedelta(days=1), "live": timedelta(hours=1), "stats": timedelta(hours=1)}
STALE_AFTER_MINUTES = 60
AUTH_CIRCUIT_WINDOW = timedelta(hours=6)
PROVIDER_DAILY_CAP = 6000  # el mismo tope combinado que statistics (DEFAULT_PROVIDER_DAILY_CAP)
# Mensaje que el adapter de API-Football pone a ProviderQuotaExceededError; live_sync_runs no
# guarda la clase de la excepción, solo el mensaje de cada competición
QUOTA_MESSAGE = "Cuota diaria de peticiones agotada"
QUOTA_EXCEPTION = "ProviderQuotaExceededError"

SUCCESS, SKIPPED, DEGRADED, FAILED, RETRYABLE = "SUCCESS", "SKIPPED", "DEGRADED", "FAILED", "RETRYABLE"
CLASS_ORDER = {SUCCESS: 0, SKIPPED: 1, DEGRADED: 2, RETRYABLE: 3, FAILED: 4}
INFO, WARNING, ERROR, CRITICAL = "INFO", "WARNING", "ERROR", "CRITICAL"
EVENT_SEVERITY = {
    "stale_recovered": WARNING,
    "skipped_twice": WARNING,
    "provider_auth_failed": CRITICAL,
    "provider_auth_circuit_open": CRITICAL,
    "provider_rate_limited": WARNING,
    "provider_quota_exhausted": WARNING,
    "db_unavailable": CRITICAL,
    "budget_guard": WARNING,
    "freshness_warning": WARNING,
    "freshness_error": ERROR,
    "backlog_growth": INFO,
    "job_failed": ERROR,
}
EXIT_OK, EXIT_ALERT, EXIT_FAILED = 0, 1, 2

# Comando de cada paso (módulo, argumentos); --confirm-target y --trigger se añaden al lanzar
LIVE_SYNC, STATS = "app.jobs.live_sync", "app.jobs.statistics_reconcile"

Runner = Callable[[str, list[str]], int]


@dataclass
class TickResult:
    task: str
    tick_utc: datetime
    cls: str = SUCCESS
    run_id: int | None = None
    exit: int | None = None
    reason: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def event(self, name: str, severity: str | None = None, **detail: Any) -> None:
        self.events.append({"event": name, "severity": severity or EVENT_SEVERITY[name], **detail})

    def worsen(self, cls: str, reason: str | None = None) -> None:
        if CLASS_ORDER[cls] > CLASS_ORDER[self.cls]:
            self.cls = cls
            self.reason = reason
        elif reason and self.reason is None and cls == self.cls and cls != SUCCESS:
            self.reason = reason

    def as_json(self) -> str:
        return json.dumps({
            "task": self.task,
            "tick_utc": self.tick_utc.isoformat(),
            "class": self.cls,
            "run_id": self.run_id,
            "exit": self.exit,
            "reason": self.reason,
            "events": self.events,
            "steps": self.steps,
            "metrics": self.metrics,
        }, default=str, ensure_ascii=False, sort_keys=False)

    def exit_code(self) -> int:
        if self.cls == FAILED:
            return EXIT_FAILED
        if any(e["severity"] in (ERROR, CRITICAL) for e in self.events):
            return EXIT_ALERT
        return EXIT_OK


# --- Clasificación (pura) ---------------------------------------------------------------------


def classify_live_sync(exit_code: int, run: dict[str, Any] | None) -> tuple[str, str | None]:
    """catalog/fixtures: exit + run de live_sync_runs. Sin run nuevo y exit 0 con el scheduler =
    lock ocupado (SKIPPED); sin run y otro exit = el job no pudo ni empezar (FAILED)."""
    if run is None:
        return (SKIPPED, "locked") if exit_code == 0 else (FAILED, "job_not_started")
    if run["auth_failed"]:
        return FAILED, "provider_auth_failed"
    if run["status"] == "completed":
        return SUCCESS, None
    if run["status"] == "completed_with_errors":
        if exit_code == 2:  # el job cortó el run como error (p. ej. proveedor sin configurar: config_failed)
            return FAILED, "job_exit_error"
        return (RETRYABLE, "provider_rate_limited") if run["rate_limited"] else (DEGRADED, "competitions_failed")
    if run["status"] == "running":
        return FAILED, "run_left_running"
    return FAILED, f"run_{run['status']}"


def classify_check(exit_code: int, run: dict[str, Any] | None) -> tuple[str, str | None]:
    """check: la clase del job (lectura de frescura). WARNING/ERROR de frescura son DEGRADED
    (problema de datos, no del job); se avisan con eventos."""
    if run is None:
        return (SKIPPED, "locked") if exit_code == 0 else (FAILED, "job_not_started")
    if run["status"] != "completed":
        return FAILED, f"run_{run['status']}"
    status = (run.get("freshness") or {}).get("status")
    return (SUCCESS, None) if status == "PASS" else (DEGRADED, f"freshness_{(status or 'unknown').lower()}")


def classify_statistics(exit_code: int, run: dict[str, Any] | None) -> tuple[str, str | None]:
    """statistics_reconcile: exit + run de statistics_runs (scope live)."""
    if run is None:
        return (SKIPPED, "locked") if exit_code == 0 else (FAILED, "job_not_started")
    stopped = next((d.get("stopped") for d in reversed(run.get("details") or []) if d.get("stopped")), None)
    if run["auth_failed"]:
        return FAILED, "provider_auth_failed"
    if run["status"] == "completed":
        return SUCCESS, None
    if run["status"] == "completed_with_errors":
        return DEGRADED, "blocking_or_missing"
    if run["status"] == "aborted":
        if stopped == "budget_exhausted":
            return RETRYABLE, "budget_exhausted"
        if run["rate_limited"] or stopped == "rate_limited":
            return RETRYABLE, "provider_rate_limited"
        return FAILED, "run_aborted"
    if run["status"] == "failed":
        return (RETRYABLE, "provider_error") if stopped == "provider_error" else (FAILED, "run_failed")
    return FAILED, f"run_{run['status']}"


# --- Lecturas (solo SELECT) ------------------------------------------------------------------


def _one(db, sql: str, **params) -> dict[str, Any] | None:
    row = db.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def auth_circuit_open(db, now: datetime) -> dict[str, Any] | None:
    """Último rechazo de credenciales (live_sync o statistics) de hace menos de 6 h sin un run
    con proveedor correcto después. Pasada la ventana se permite UN intento; si vuelve a fallar,
    ese fallo abre otra ventana. Reset manual: cualquier job con proveedor que termine bien
    (p. ej. `live_sync catalog --confirm-target …` tras rotar la clave) cierra el circuito."""
    last = _one(db, """
        SELECT max(at) AS at FROM (
            SELECT finished_at AS at FROM live_sync_runs WHERE auth_failed AND finished_at IS NOT NULL
            UNION ALL SELECT finished_at FROM statistics_runs WHERE auth_failed AND finished_at IS NOT NULL
        ) x""")
    at = last["at"] if last else None
    if at is None or now - at >= AUTH_CIRCUIT_WINDOW:
        return None
    ok_after = _one(db, """
        SELECT 1 AS ok FROM live_sync_runs WHERE finished_at > :at AND NOT auth_failed AND provider_requests > 0
            AND status IN ('completed', 'completed_with_errors')
        UNION ALL SELECT 1 FROM statistics_runs WHERE finished_at > :at AND NOT auth_failed AND provider_requests > 0
            AND status IN ('completed', 'completed_with_errors', 'dry_run_completed')
        LIMIT 1""", at=at)
    if ok_after:
        return None
    return {"last_auth_failure": at.isoformat(), "reopens_at": (at + AUTH_CIRCUIT_WINDOW).isoformat()}


def quota_exhausted_today(db, now: datetime) -> bool:
    """Cuota diaria del proveedor agotada hoy (UTC): statistics guarda la clase de la excepción
    en details; live_sync solo el mensaje de cada competición (el del adapter)."""
    midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    stats = _one(db, """
        SELECT 1 AS hit FROM statistics_runs r, jsonb_array_elements(r.details) d
        WHERE r.started_at >= :m AND d->>'error' = :exc LIMIT 1""", m=midnight, exc=QUOTA_EXCEPTION)
    live = _one(db, """
        SELECT 1 AS hit FROM live_sync_runs r, jsonb_array_elements(r.details) d
        WHERE r.started_at >= :m AND r.rate_limited AND d->>'error' LIKE :msg LIMIT 1""", m=midnight, msg=f"%{QUOTA_MESSAGE}%")
    return bool(stats or live)


def requests_today(db, now: datetime) -> tuple[int, int]:
    midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    row = _one(db, """
        SELECT (SELECT coalesce(sum(provider_requests), 0) FROM statistics_runs WHERE started_at >= :m) AS stats,
               (SELECT coalesce(sum(provider_requests), 0) FROM live_sync_runs WHERE started_at >= :m) AS live""", m=midnight)
    return int(row["stats"]), int(row["live"])


def estimated_requests(task: str, tracked_leagues: int) -> int:
    """Estimación conservadora: una llamada por competición seguida (+ /leagues en catalog), cada
    una con todos los intentos del adapter."""
    calls = tracked_leagues + (1 if task == "catalog" else 0)
    return calls * MAX_ATTEMPTS


def max_id(db, table: str) -> int:
    return int(db.execute(text(f"SELECT coalesce(max(id), 0) FROM {table}")).scalar())


def new_live_sync_run(db, after_id: int, job_type: str) -> dict[str, Any] | None:
    return _one(db, """
        SELECT id, status, auth_failed, rate_limited, provider_requests, provider_retries, competitions_attempted,
               competitions_succeeded, competitions_failed, competitions_skipped, freshness,
               extract(epoch FROM finished_at - started_at) AS seconds
        FROM live_sync_runs WHERE id > :a AND job_type = :j AND trigger = 'scheduler' ORDER BY id LIMIT 1""", a=after_id, j=job_type)


def new_statistics_run(db, after_id: int) -> dict[str, Any] | None:
    return _one(db, """
        SELECT id, status, auth_failed, rate_limited, provider_requests, provider_retries, fixtures_targeted,
               fixtures_attempted, fixtures_available, fixtures_partial, fixtures_empty, fixtures_blocked,
               fixtures_missing_in_response, details, extract(epoch FROM finished_at - started_at) AS seconds
        FROM statistics_runs WHERE id > :a AND scope = 'live' AND trigger = 'scheduler' ORDER BY id LIMIT 1""", a=after_id)


def lock_holder(db, task: str) -> dict[str, Any] | None:
    if task == "stats":
        return _one(db, "SELECT id, started_at FROM statistics_runs WHERE status = 'running' LIMIT 1")
    return _one(db, "SELECT id, started_at FROM live_sync_runs WHERE status = 'running' AND lock_scope = 'live_sync' LIMIT 1")


def previous_backlog(db, before_id: int) -> int | None:
    row = _one(db, """
        SELECT (d->>'backlog_outside_lookback')::int AS backlog FROM statistics_runs r, jsonb_array_elements(r.details) d
        WHERE r.id <= :b AND r.scope = 'live' AND r.mode = 'apply' AND d->>'kind' = 'live_selection'
        ORDER BY r.id DESC LIMIT 1""", b=before_id)
    return row["backlog"] if row else None


# --- Subprocesos -----------------------------------------------------------------------------


def subprocess_runner(module: str, args: list[str]) -> int:
    """Lanza un CLI existente en su propio proceso. Su salida humana va a stderr: stdout queda
    para la línea JSON del tick."""
    proc = subprocess.run([sys.executable, "-m", module, *args], cwd=BACKEND_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stderr.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


# --- Tick --------------------------------------------------------------------------------------


def run_tick(task: str, *, target: str, now: datetime, session_factory: Callable[[], Any], runner: Runner = subprocess_runner,
             tracked_leagues: int | None = None) -> TickResult:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("ops_tick trabaja solo en UTC (datetime con tzinfo UTC)")
    result = TickResult(task, now)
    common = ["--confirm-target", target]
    tracked = len(get_settings().tracked_league_ids) if tracked_leagues is None else tracked_leagues

    # 1. Conectividad con la BD: sin ella no se lanza nada (ninguna petición sin auditoría)
    try:
        with session_factory() as db:
            db.execute(text("SELECT 1"))
            db.rollback()
    except Exception as exc:  # noqa: BLE001  (solo el tipo: el mensaje puede incluir el destino)
        result.worsen(FAILED, "db_unavailable")
        result.event("db_unavailable", error=exc.__class__.__name__)
        return result

    # 2. Recuperación de runs colgados (60 min), en subproceso: 0 recuperado · 1 nada que hacer
    stale_steps = [(STATS, ["--fail-stale-run"])] if task == "stats" else [(LIVE_SYNC, ["fixtures", "--fail-stale-run"])]
    if task == "live":
        stale_steps.append((LIVE_SYNC, ["check", "--fail-stale-run"]))
    for module, args in stale_steps:
        code = runner(module, [*args, "--stale-after-minutes", str(STALE_AFTER_MINUTES), *common])
        result.steps.append({"step": "stale_recovery", "module": module, "args": args, "exit": code})
        if code == 0:
            result.event("stale_recovered", module=module)
        elif code != 1:
            result.worsen(FAILED, "stale_recovery_failed")
            result.event("job_failed", step="stale_recovery", module=module, exit=code)
            return result

    # 3. Guardas de solo lectura + ids previos para encontrar el run de este tick
    with session_factory() as db:
        circuit = auth_circuit_open(db, now)
        quota = quota_exhausted_today(db, now)
        stats_today, live_today = requests_today(db, now)
        before_live, before_stats = max_id(db, "live_sync_runs"), max_id(db, "statistics_runs")
        backlog_before = previous_backlog(db, before_stats) if task == "stats" else None
        db.rollback()
    result.metrics["budget"] = {"stats_today": stats_today, "live_sync_today": live_today, "provider_cap": PROVIDER_DAILY_CAP}

    blocked = None
    if circuit:
        blocked = "provider_auth_circuit_open"
        result.event("provider_auth_circuit_open", **circuit)
    elif quota:
        blocked = "provider_quota_exhausted"
        result.event("provider_quota_exhausted")
    elif task in ("catalog", "live"):
        estimate = estimated_requests(task, tracked)
        if stats_today + live_today + estimate > PROVIDER_DAILY_CAP:
            blocked = "budget"
            result.event("budget_guard", estimate=estimate)

    # 4–5. Job(s) en subproceso y clasificación por el run persistido
    if task == "stats":
        if blocked:
            result.worsen(SKIPPED, blocked)
        else:
            code = runner(STATS, ["--trigger", "scheduler", *common])
            with session_factory() as db:
                run = new_statistics_run(db, before_stats)
                holder = lock_holder(db, task) if run is None and code == 0 else None
                db.rollback()
            cls, reason = classify_statistics(code, run)
            _record(result, "statistics_reconcile", code, run, cls, reason, holder, now)
            if run:
                selection = next((d for d in run.get("details") or [] if d.get("kind") == "live_selection"), {})
                backlog = selection.get("backlog_outside_lookback")
                result.metrics["statistics"] = {k: run[k] for k in (
                    "fixtures_targeted", "fixtures_attempted", "fixtures_available", "fixtures_partial", "fixtures_empty",
                    "fixtures_blocked", "fixtures_missing_in_response", "provider_requests", "provider_retries", "seconds")}
                result.metrics["statistics"]["backlog_outside_lookback"] = backlog
                result.metrics["statistics"]["first_acquisition_due"] = (selection.get("by_priority") or {}).get("first_acquisition")
                if reason == "budget_exhausted":
                    result.event("budget_guard", job="statistics_reconcile")
                if backlog is not None and backlog_before is not None and backlog > backlog_before:
                    result.event("backlog_growth", previous=backlog_before, current=backlog)
        return result

    job = "catalog" if task == "catalog" else "fixtures"
    if blocked:
        result.worsen(SKIPPED, blocked)
    else:
        code = runner(LIVE_SYNC, [job, "--trigger", "scheduler", *common])
        with session_factory() as db:
            run = new_live_sync_run(db, before_live, job)
            holder = lock_holder(db, task) if run is None and code == 0 else None
            db.rollback()
        cls, reason = classify_live_sync(code, run)
        _record(result, job, code, run, cls, reason, holder, now)
        if run:
            result.metrics[job] = {k: run[k] for k in (
                "provider_requests", "provider_retries", "competitions_attempted", "competitions_succeeded",
                "competitions_failed", "competitions_skipped", "seconds")}

    if task == "live":  # el check solo lee (sin proveedor): corre aunque fixtures se haya saltado
        code = runner(LIVE_SYNC, ["check", "--trigger", "scheduler", *common])
        with session_factory() as db:
            run = new_live_sync_run(db, before_live, "check")
            db.rollback()
        cls, reason = classify_check(code, run)
        result.steps.append({"step": "check", "exit": code, "run_id": run["id"] if run else None, "class": cls})
        result.worsen(cls, reason)
        if run and run.get("freshness"):
            report = run["freshness"]
            result.metrics["freshness"] = {"status": report.get("status"), "warnings": report.get("warnings"), "errors": report.get("errors")}
            for check in report.get("checks", []):
                if check["id"] == "S1_first_acquisition_overdue":
                    if check["status"] in ("WARNING", "ERROR"):
                        result.event("first_acquisition_overdue", check["status"], count=check["count"])
                    result.metrics["first_acquisition_overdue"] = check["count"]
                elif check["id"] == "S2_backlog_outside_lookback":
                    result.metrics["backlog_outside_lookback"] = check["count"]
                elif check["status"] == "WARNING":
                    result.event("freshness_warning", check=check["id"], count=check["count"])
                elif check["status"] == "ERROR":
                    result.event("freshness_error", check=check["id"], count=check["count"])
    return result


def _record(result: TickResult, job: str, code: int, run, cls: str, reason: str | None, holder, now: datetime) -> None:
    result.run_id = run["id"] if run else None
    result.exit = code
    result.steps.append({"step": job, "exit": code, "run_id": result.run_id, "class": cls})
    result.worsen(cls, reason)
    if run and run.get("rate_limited"):
        result.event("provider_rate_limited", job=job)
    if run and run.get("auth_failed"):
        result.event("provider_auth_failed", job=job)
    if cls == FAILED and reason not in ("provider_auth_failed",):
        result.event("job_failed", job=job, reason=reason, exit=code)
    # 2.º SKIPPED seguido: el lock lo tiene un run que ya lo tenía en el tick anterior
    if cls == SKIPPED and holder is not None and holder["started_at"] <= now - INTERVALS[result.task]:
        result.event("skipped_twice", holder_run_id=holder["id"], holder_started_at=holder["started_at"].isoformat())


# --- CLI ---------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.ops_tick", description="Una pasada del scheduler operativo (UTC).")
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("--confirm-target", help=f"<host>:<puerto>/<bd> esperado (o la variable {TARGET_ENV})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, runner: Runner = subprocess_runner, clock: Callable[[], datetime] | None = None) -> int:
    args = parse_args(argv)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    result = TickResult(args.task, now)
    expected = (args.confirm_target or os.environ.get(TARGET_ENV) or "").strip()
    try:
        target = database_target(get_settings().database_url)
    except ValueError:
        target = None
    if not expected or target is None or expected != target:
        result.worsen(FAILED, "target_missing" if not expected else "target_mismatch")
        print(result.as_json())
        return result.exit_code()

    from app.db import session as db_session

    try:
        result = run_tick(args.task, target=target, now=now, session_factory=db_session.SessionLocal, runner=runner)
    except Exception as exc:  # noqa: BLE001  (solo el tipo: el mensaje puede incluir el destino)
        result.worsen(FAILED, "runner_exception")
        result.event("job_failed", error=exc.__class__.__name__)
    print(result.as_json())
    return result.exit_code()


if __name__ == "__main__":
    sys.exit(main())
