"""CLI del backfill de estadísticas de partido por temporada (M5.3), sin FastAPI.

Uso (desde backend/):
    python -m app.jobs.statistics_backfill --competition-id 5 --season 2025 --dry-run --confirm-target <host>:<puerto>/<bd>
    python -m app.jobs.statistics_backfill --competition-id 5 --season 2025 --confirm-target ... [--refresh] [--resume]
    python -m app.jobs.statistics_backfill --fail-stale-run --confirm-target ... [--stale-after-minutes 60]

Destino: --confirm-target o PREDIKTIA_EXPECTED_DB_TARGET debe coincidir con el destino saneado
de DATABASE_URL; si falta o no coincide sale con 2 sin crear run ni llamar al proveedor.

Presupuesto diario (UTC): --budget (por defecto 1500) limita las peticiones de estadísticas del
día (todos los statistics_runs); --provider-daily-cap (por defecto 6000) limita estadísticas +
live sync. Si el siguiente lote no cabe, el run se cierra 'aborted' y se reanuda con --resume.

Códigos de salida:
- 0: completed, o dry_run_completed sin partidos bloqueados ni ausentes en la respuesta;
- 1: completed_with_errors (algún partido BLOCKING o ausente; afecta solo a esos partidos),
     dry_run_completed con BLOCKING/ausentes, o parada por presupuesto (aborted, reanudable);
     con --fail-stale-run: no había run abandonado o era demasiado reciente;
- 2: destino ausente o distinto, lock ocupado, credenciales rechazadas, límite/cuota del
     proveedor, error del proveedor, nada que reanudar, temporada inexistente o excepción.
"""

import argparse
import asyncio
import logging
import os
import sys

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.integrations.football.api_football_statistics import ApiFootballStatisticsProvider
from app.jobs.history_backfill import database_target
from app.jobs.live_sync import CountingApiFootballProvider
from app.repositories import statistics_run_repository as runs
from app.services import statistics_service as service

logger = logging.getLogger("prediktia.statistics_backfill")

TARGET_ENV = "PREDIKTIA_EXPECTED_DB_TARGET"
DEFAULT_STALE_AFTER_MINUTES = 60
EXIT_OK, EXIT_WARNING, EXIT_ERROR = 0, 1, 2


class CountingStatisticsProvider(CountingApiFootballProvider, ApiFootballStatisticsProvider):
    """Adapter de estadísticas contando peticiones HTTP reales y reintentos."""


def default_provider() -> CountingStatisticsProvider:
    settings = get_settings()
    return CountingStatisticsProvider(
        api_key=settings.api_football_key.get_secret_value(),
        base_url=settings.api_football_base_url,
        timeout=settings.http_timeout_seconds,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.statistics_backfill", description="Backfill auditado de estadísticas de partido.")
    parser.add_argument("--competition-id", type=int, help="id interno de la competición")
    parser.add_argument("--season", type=int, help="año de la temporada (p. ej. 2025)")
    parser.add_argument("--dry-run", action="store_true", help="pide y evalúa, pero no escribe observaciones ni estadísticas")
    parser.add_argument("--refresh", action="store_true", help="vuelve a pedir partidos que ya tienen observación")
    parser.add_argument("--resume", action="store_true", help="continúa tras el cursor del último run interrumpido")
    parser.add_argument("--budget", type=int, default=service.DEFAULT_STATS_DAILY_BUDGET, help="peticiones de estadísticas por día (UTC)")
    parser.add_argument("--provider-daily-cap", type=int, default=service.DEFAULT_PROVIDER_DAILY_CAP, help="estadísticas + live sync por día (UTC)")
    parser.add_argument("--confirm-target", help=f"<host>:<puerto>/<bd> esperado (o la variable {TARGET_ENV})")
    parser.add_argument("--trigger", choices=["cli", "scheduler"], default="cli")
    parser.add_argument("--fail-stale-run", action="store_true", help="marca aborted el run 'running' abandonado")
    parser.add_argument("--stale-after-minutes", type=int, default=None)
    args = parser.parse_args(argv)
    if args.fail_stale_run:
        if args.competition_id is not None or args.season is not None:
            parser.error("--fail-stale-run no se combina con --competition-id/--season")
        args.stale_after_minutes = args.stale_after_minutes or DEFAULT_STALE_AFTER_MINUTES
        if args.stale_after_minutes < 1:
            parser.error("--stale-after-minutes debe ser >= 1")
        return args
    if args.stale_after_minutes is not None:
        parser.error("--stale-after-minutes solo se usa con --fail-stale-run")
    if args.competition_id is None or args.season is None:
        parser.error("hacen falta --competition-id y --season")
    if args.budget < 1 or args.provider_daily_cap < 1:
        parser.error("--budget y --provider-daily-cap deben ser >= 1")
    if args.budget > args.provider_daily_cap:
        parser.error("--budget no puede superar --provider-daily-cap")
    return args


def _configured_target() -> str:
    return database_target(get_settings().database_url)


def _err(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return EXIT_ERROR


def fail_stale_run(db, minutes: int) -> tuple[str, int | None]:
    run = runs.lock_running_run(db)
    if run is None:
        db.rollback()
        return "not_found", None
    run_id = run.id
    if not runs.is_older_than(db, run_id, minutes):
        db.rollback()
        return "too_recent", run_id
    runs.finish_run(
        db, run_id, status="aborted",
        error_message=f"Recuperación administrativa: run abandonado en 'running' más de {minutes} min; marcado aborted manualmente",
    )
    db.commit()
    return "recovered", run_id


def exit_code(outcome: service.BackfillOutcome) -> int:
    if outcome.status == "locked" or outcome.stop_reason in (service.STOP_AUTH, service.STOP_RATE_LIMIT, service.STOP_PROVIDER):
        return EXIT_ERROR
    if outcome.status == "failed":
        return EXIT_ERROR
    if outcome.stop_reason == service.STOP_BUDGET:
        return EXIT_WARNING
    if outcome.status == "completed_with_errors":
        return EXIT_WARNING
    if outcome.status == "dry_run_completed":
        problems = outcome.counters.get("blocking_count") or outcome.counters.get("fixtures_missing_in_response")
        return EXIT_WARNING if problems else EXIT_OK
    return EXIT_OK


def _print_outcome(outcome: service.BackfillOutcome) -> None:
    if outcome.run_id is None:
        print("statistics: no se ejecutó (ya hay un run de estadísticas en curso)")
        return
    c = outcome.counters
    print(f"statistics: run #{outcome.run_id} {outcome.status}" + (f" ({outcome.stop_reason})" if outcome.stop_reason else ""))
    print(
        f"  partidos: objetivo {c['fixtures_targeted']}, intentados {c['fixtures_attempted']} = available {c['fixtures_available']}"
        f" + partial {c['fixtures_partial']} + empty {c['fixtures_empty']} + blocked {c['fixtures_blocked']}"
        f" + ausentes {c['fixtures_missing_in_response']}"
    )
    print(f"  proveedor: {c['provider_requests']} peticiones, {c['provider_retries']} reintentos")
    print(
        f"  escritura: observaciones {c['observations_created']} nuevas / {c['observations_unchanged']} iguales;"
        f" filas {c['rows_created']} creadas / {c['rows_updated']} actualizadas / {c['rows_unchanged']} iguales"
    )
    print(f"  calidad: {c['blocking_count']} BLOCKING, {c['warning_count']} WARNING")


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

    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import Season

    try:
        with SessionLocal() as db:
            if args.fail_stale_run:
                outcome, run_id = fail_stale_run(db, args.stale_after_minutes)
                print(f"Recuperación (statistics, umbral {args.stale_after_minutes} min): {outcome}" + (f" run #{run_id}" if run_id else ""))
                return EXIT_OK if outcome == "recovered" else EXIT_WARNING
            season_id = db.scalar(select(Season.id).where(Season.competition_id == args.competition_id, Season.year == args.season))
            db.rollback()
            if season_id is None:
                return _err(f"no existe la temporada {args.season} de la competición {args.competition_id}")
            options = service.BackfillOptions(
                mode="dry_run" if args.dry_run else "apply",
                trigger=args.trigger,
                refresh=args.refresh,
                resume=args.resume,
                stats_daily_budget=args.budget,
                provider_daily_cap=args.provider_daily_cap,
            )
            try:
                outcome = asyncio.run(
                    service.run_season_backfill(
                        db, competition_id=args.competition_id, season_id=season_id, provider=default_provider(), options=options
                    )
                )
            except service.NothingToResume as exc:
                return _err(str(exc))
            _print_outcome(outcome)
            return exit_code(outcome)
    except Exception as exc:  # noqa: BLE001  (solo el tipo: el mensaje puede incluir el destino)
        return _err(f"error inesperado ({exc.__class__.__name__}); revisa statistics_runs")


if __name__ == "__main__":
    sys.exit(main())
