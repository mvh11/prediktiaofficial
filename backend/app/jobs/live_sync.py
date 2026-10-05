"""CLI de la sync en vivo, sin FastAPI.

Uso (desde backend/):
    python -m app.jobs.live_sync catalog  --confirm-target <host>:<puerto>/<bd>
    python -m app.jobs.live_sync fixtures --confirm-target <host>:<puerto>/<bd> [--competition-id N]
    python -m app.jobs.live_sync check    --confirm-target <host>:<puerto>/<bd>
    python -m app.jobs.live_sync all      --confirm-target <host>:<puerto>/<bd>
    python -m app.jobs.live_sync fixtures --confirm-target ... --fail-stale-run [--stale-after-minutes 60]

Destino: --confirm-target o la variable PREDIKTIA_EXPECTED_DB_TARGET (para el scheduler) debe
coincidir exactamente con el destino saneado de DATABASE_URL; si falta o no coincide no se toca
nada. Nunca se imprime la URL ni la clave del proveedor.

Cada ejecución queda en live_sync_runs. El run 'running' es el lock: catálogo y fixtures
comparten ámbito (nunca a la vez) y el check de solo lectura tiene el suyo.

`all` = catalog → fixtures → check. Si el catálogo falla de forma global (excepción, /leagues
caído) NO se sincronizan fixtures: un cambio de temporada a medias podría mandar la sync a la
temporada equivocada. El check se ejecuta igualmente (solo lee) y la salida es 2.

Códigos de salida: 0 completed y frescura PASS · 1 completed_with_errors o frescura WARNING ·
2 failed, frescura ERROR, credenciales rechazadas, lock ocupado, destino incorrecto o excepción.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.integrations.exceptions import ProviderAuthError, ProviderRateLimitError
from app.integrations.football.api_football import ApiFootballProvider
from app.jobs.history_backfill import database_target
from app.repositories import live_sync_repository as runs

logger = logging.getLogger("prediktia.live_sync")

TARGET_ENV = "PREDIKTIA_EXPECTED_DB_TARGET"
DEFAULT_STALE_AFTER_MINUTES = 60
EXIT_OK, EXIT_WARNING, EXIT_ERROR = 0, 1, 2
FRESHNESS_EXIT = {"PASS": EXIT_OK, "WARNING": EXIT_WARNING, "ERROR": EXIT_ERROR}


class CountingApiFootballProvider(ApiFootballProvider):
    """El adapter de API-Football tal cual, contando peticiones HTTP reales y reintentos."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.logical_calls = 0  # llamadas a _get (cada una puede hacer varios intentos)
        self.http_requests = 0  # peticiones HTTP reales (_get_once)
        self.rate_limited = False
        self.auth_failed = False

    async def _get_once(self, path, params=None, *, allow_paging=False):
        self.http_requests += 1
        return await super()._get_once(path, params, allow_paging=allow_paging)

    async def _get(self, path, params=None, *, allow_paging=False):
        self.logical_calls += 1
        try:
            return await super()._get(path, params, allow_paging=allow_paging)
        except ProviderRateLimitError:
            self.rate_limited = True
            raise
        except ProviderAuthError:
            self.auth_failed = True
            raise

    @property
    def retries(self) -> int:
        return max(self.http_requests - self.logical_calls, 0)


def default_provider() -> CountingApiFootballProvider:
    settings = get_settings()
    return CountingApiFootballProvider(
        api_key=settings.api_football_key.get_secret_value(),
        base_url=settings.api_football_base_url,
        timeout=settings.http_timeout_seconds,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.live_sync", description="Sync en vivo auditada (sin FastAPI).")
    parser.add_argument("job", choices=["catalog", "fixtures", "check", "all"])
    parser.add_argument("--competition-id", type=int, help="solo fixtures: id interno de una competición")
    parser.add_argument("--confirm-target", help=f"<host>:<puerto>/<bd> esperado (o la variable {TARGET_ENV})")
    parser.add_argument("--trigger", choices=["cli", "scheduler"], default="cli")
    parser.add_argument("--fail-stale-run", action="store_true", help="marca como aborted el run 'running' abandonado del ámbito del job")
    parser.add_argument("--stale-after-minutes", type=int, default=None)
    args = parser.parse_args(argv)
    if args.competition_id is not None and args.job != "fixtures":
        parser.error("--competition-id solo se usa con fixtures")
    if args.fail_stale_run:
        if args.job == "all":
            parser.error("--fail-stale-run se usa con catalog/fixtures (ámbito live_sync) o check (ámbito freshness)")
        args.stale_after_minutes = args.stale_after_minutes or DEFAULT_STALE_AFTER_MINUTES
        if args.stale_after_minutes < 1:
            parser.error("--stale-after-minutes debe ser >= 1")
    elif args.stale_after_minutes is not None:
        parser.error("--stale-after-minutes solo se usa con --fail-stale-run")
    return args


def _configured_target() -> str:
    return database_target(get_settings().database_url)


def _err(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return EXIT_ERROR


# --- Jobs ------------------------------------------------------------------------------------


def _start(db, job_type: str, trigger: str) -> int | None:
    try:
        run_id = runs.create_run(db, job_type=job_type, trigger=trigger)
        db.commit()
        return run_id
    except IntegrityError as exc:
        db.rollback()
        if runs.is_lock_conflict(exc):
            return None
        raise


def _fail(db, run_id: int, exc: Exception) -> None:
    """Cierra el run como failed sin ocultar la excepción original si cerrar también falla."""
    db.rollback()
    try:
        runs.finish_run(db, run_id, status="failed", error_message=f"Error inesperado ({exc.__class__.__name__})")
        db.commit()
    except Exception as close_exc:  # noqa: BLE001
        db.rollback()
        logger.error("No se pudo cerrar el run #%s como failed (%s)", run_id, close_exc.__class__.__name__)


async def run_fixtures(db, trigger: str, provider, competition_id: int | None = None) -> tuple[int, str]:
    from app.services import fixture_sync_service

    run_id = _start(db, "fixtures", trigger)
    if run_id is None:
        return 0, "locked"

    def audit(session, result) -> None:
        runs.append_detail(session, run_id, result.model_dump())

    try:
        result = await fixture_sync_service.sync_fixtures(db, competition_id, provider=provider, on_competition=audit)
        comps = result.competitions
        failed = sum(1 for c in comps if c.error)
        skipped = sum(1 for c in comps if c.skipped and not c.error)
        status = "completed" if failed == 0 else "completed_with_errors"
        runs.finish_run(
            db,
            run_id,
            status=status,
            competitions_attempted=len(comps),
            competitions_succeeded=len(comps) - failed - skipped,
            competitions_failed=failed,
            competitions_skipped=skipped,
            fixtures_received=sum(c.fixtures for c in comps),
            fixtures_created=sum(c.created for c in comps),
            fixtures_updated=sum(c.updated for c in comps),
            fixtures_unchanged=sum(c.unchanged for c in comps),
            teams_created=sum(c.teams_created for c in comps),
            provider_requests=getattr(provider, "http_requests", 0),
            provider_retries=getattr(provider, "retries", 0),
            rate_limited=getattr(provider, "rate_limited", False),
            auth_failed=getattr(provider, "auth_failed", False),
            error_count=failed,
        )
        db.commit()
    except Exception as exc:
        _fail(db, run_id, exc)
        raise
    return run_id, status


async def run_catalog(db, trigger: str, provider) -> tuple[int, str]:
    from app.services import catalog_sync_service

    run_id = _start(db, "catalog", trigger)
    if run_id is None:
        return 0, "locked"
    try:
        result = await catalog_sync_service.sync_catalog(db, provider=provider)
        comps = result.competitions
        failed = sum(1 for c in comps if c.error)
        changes = [
            {"external_id": c.external_id, "name": c.name, "previous_season": c.previous_season, "season": c.season}
            for c in comps
            if c.name is not None and c.previous_season != c.season
        ]
        status = "completed" if failed == 0 else "completed_with_errors"
        runs.finish_run(
            db,
            run_id,
            status=status,
            competitions_attempted=len(comps),
            competitions_succeeded=len(comps) - failed,
            competitions_failed=failed,
            details=[c.model_dump() for c in comps],
            catalog_changes=changes,
            provider_requests=getattr(provider, "http_requests", 0),
            provider_retries=getattr(provider, "retries", 0),
            rate_limited=getattr(provider, "rate_limited", False),
            auth_failed=getattr(provider, "auth_failed", False),
            error_count=failed,
        )
        db.commit()
    except Exception as exc:
        _fail(db, run_id, exc)
        raise
    return run_id, status


def run_check(db, trigger: str, now: datetime | None = None) -> tuple[int, str, dict | None]:
    from app.services.freshness_checks import run_checks

    run_id = _start(db, "check", trigger)
    if run_id is None:
        return 0, "locked", None
    try:
        report = run_checks(db, now or datetime.now(timezone.utc))
        db.rollback()  # el check solo lee
        runs.finish_run(
            db, run_id, status="completed", freshness=report, warning_count=report["warnings"], error_count=report["errors"]
        )
        db.commit()
    except Exception as exc:
        _fail(db, run_id, exc)
        raise
    return run_id, "completed", report


def fail_stale_run(db, lock_scope: str, minutes: int) -> tuple[str, int | None]:
    run = runs.lock_running_run(db, lock_scope)
    if run is None:
        db.rollback()
        return "not_found", None
    run_id = run.id
    if not runs.is_older_than(db, run_id, minutes):
        db.rollback()
        return "too_recent", run_id
    runs.finish_run(
        db,
        run_id,
        status="aborted",
        error_message=f"Recuperación administrativa: run abandonado en 'running' más de {minutes} min; marcado aborted manualmente",
    )
    db.commit()
    return "recovered", run_id


# --- Salida --------------------------------------------------------------------------------


def _sync_exit(status: str, provider) -> int:
    if status == "locked" or status == "failed" or getattr(provider, "auth_failed", False):
        return EXIT_ERROR
    return EXIT_OK if status == "completed" else EXIT_WARNING


def _print_run(label: str, run_id: int, status: str) -> None:
    print(f"{label}: run #{run_id} {status}" if run_id else f"{label}: no se ejecutó ({status}: ya hay una ejecución en curso)")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    expected = (args.confirm_target or os.environ.get(TARGET_ENV) or "").strip()
    if not expected:
        return _err(f"falta --confirm-target o la variable {TARGET_ENV}; no se ha tocado nada")
    try:
        target = _configured_target()
    except ValueError as exc:
        return _err(str(exc))
    print(f"Destino BD:  {target}")
    if expected != target:
        return _err(f"el destino esperado no coincide con DATABASE_URL ({target}); no se ha tocado nada")

    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            if args.fail_stale_run:
                scope = runs.LOCK_SCOPE_BY_JOB[args.job]
                outcome, run_id = fail_stale_run(db, scope, args.stale_after_minutes)
                print(f"Recuperación ({scope}, umbral {args.stale_after_minutes} min): {outcome}" + (f" run #{run_id}" if run_id else ""))
                return EXIT_OK if outcome == "recovered" else EXIT_WARNING
            return asyncio.run(_run_jobs(db, args))
    except Exception as exc:  # noqa: BLE001  (solo el tipo: el mensaje puede incluir el destino)
        return _err(f"error inesperado ({exc.__class__.__name__}); revisa live_sync_runs")


async def _run_jobs(db, args) -> int:
    codes = []
    if args.job in ("catalog", "all"):
        provider = default_provider()
        try:
            run_id, status = await run_catalog(db, args.trigger, provider)
        except Exception as exc:  # noqa: BLE001
            print(f"catalog: failed ({exc.__class__.__name__})", file=sys.stderr)
            run_id, status = 0, "failed"
        if run_id or status != "failed":
            _print_run("catalog", run_id, status)
        codes.append(_sync_exit(status, provider))
        if args.job == "all" and status in ("failed", "locked"):
            print("fixtures: no se ejecuta porque el catálogo no terminó (fail-safe)", file=sys.stderr)
            _, check_status, report = run_check(db, args.trigger)
            codes.append(FRESHNESS_EXIT[report["status"]] if report else EXIT_ERROR)
            return max(codes)
    if args.job in ("fixtures", "all"):
        provider = default_provider()
        try:
            run_id, status = await run_fixtures(db, args.trigger, provider, args.competition_id)
        except Exception as exc:  # noqa: BLE001
            print(f"fixtures: failed ({exc.__class__.__name__})", file=sys.stderr)
            run_id, status = 0, "failed"
        if run_id or status != "failed":
            _print_run("fixtures", run_id, status)
        codes.append(_sync_exit(status, provider))
    if args.job in ("check", "all"):
        run_id, status, report = run_check(db, args.trigger)
        _print_run("check", run_id, status)
        if report is None:
            codes.append(EXIT_ERROR)
        else:
            for c in report["checks"]:
                print(f"  [{c['status']}] {c['id']}: {c['detail']}")
            print(f"Frescura: {report['status']}")
            codes.append(FRESHNESS_EXIT[report["status"]])
    return max(codes) if codes else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
