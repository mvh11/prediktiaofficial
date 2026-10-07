"""CLI del reconciliador live de estadísticas (M5.6B), sin FastAPI e independiente de live_sync.

Uso (desde backend/):
    python -m app.jobs.statistics_reconcile --dry-run --confirm-target <host>:<puerto>/<bd>
    python -m app.jobs.statistics_reconcile --confirm-target ... [--trigger scheduler]
    python -m app.jobs.statistics_reconcile --fail-stale-run --confirm-target ... [--stale-after-minutes 60]

Una pasada: selecciona los partidos finales DEBIDOS de las temporadas operativas no dormant
(calendario T1–T4, ver statistics_reconcile_service) y los pide en lotes de hasta 20 a
/fixtures?ids con el pipeline de siempre. Política LIVE: source=live, available_at = observed_at.
No hay --resume ni --refresh: la siguiente pasada recalcula lo debido (los lotes confirmados ya
no lo están; un lote deshecho sí).

Destino: --confirm-target o PREDIKTIA_EXPECTED_DB_TARGET debe coincidir con el destino saneado
de DATABASE_URL; si falta o no coincide sale con 2 sin crear run ni llamar al proveedor.

Presupuesto diario (UTC), el mismo contrato que el backfill: --budget (1500) para estadísticas y
--provider-daily-cap (6000) para estadísticas + live sync. Nunca se elevan solos.

Códigos de salida:
- 0: completed (también sin nada debido), dry_run_completed sin BLOCKING ni ausentes, o
     SKIPPED porque otro run de estadísticas tiene el lock con --trigger scheduler (no es un
     fallo: lo reintenta la siguiente pasada; alertar tras pasadas seguidas es cosa del scheduler);
- 1: completed_with_errors (algún BLOCKING o ausente), dry-run con BLOCKING/ausentes, o parada
     por presupuesto (aborted); con --fail-stale-run: no había run abandonado o era reciente;
- 2: destino ausente o distinto, lock ocupado con --trigger cli, credenciales rechazadas,
     límite/cuota o error del proveedor, o excepción.
"""

import argparse
import asyncio
import logging
import os
import sys

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.jobs.history_backfill import database_target
from app.jobs.statistics_backfill import DEFAULT_STALE_AFTER_MINUTES, default_provider, fail_stale_run
from app.services import statistics_reconcile_service as reconcile
from app.services import statistics_service as service

logger = logging.getLogger("prediktia.statistics_reconcile")

TARGET_ENV = "PREDIKTIA_EXPECTED_DB_TARGET"
EXIT_OK, EXIT_WARNING, EXIT_ERROR = 0, 1, 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.statistics_reconcile", description="Reconciliador live de estadísticas (sin FastAPI).")
    parser.add_argument("--dry-run", action="store_true", help="pide y evalúa, pero no escribe observaciones ni estadísticas")
    parser.add_argument("--budget", type=int, default=service.DEFAULT_STATS_DAILY_BUDGET, help="peticiones de estadísticas por día (UTC)")
    parser.add_argument("--provider-daily-cap", type=int, default=service.DEFAULT_PROVIDER_DAILY_CAP, help="estadísticas + live sync por día (UTC)")
    parser.add_argument("--confirm-target", help=f"<host>:<puerto>/<bd> esperado (o la variable {TARGET_ENV})")
    parser.add_argument("--trigger", choices=["cli", "scheduler"], default="cli")
    parser.add_argument("--fail-stale-run", action="store_true", help="marca aborted el run 'running' abandonado")
    parser.add_argument("--stale-after-minutes", type=int, default=None)
    args = parser.parse_args(argv)
    if args.fail_stale_run:
        if args.dry_run:
            parser.error("--fail-stale-run no se combina con --dry-run")
        args.stale_after_minutes = args.stale_after_minutes or DEFAULT_STALE_AFTER_MINUTES
        if args.stale_after_minutes < 1:
            parser.error("--stale-after-minutes debe ser >= 1")
        return args
    if args.stale_after_minutes is not None:
        parser.error("--stale-after-minutes solo se usa con --fail-stale-run")
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


def exit_code(outcome: service.BackfillOutcome, trigger: str) -> int:
    if outcome.status == "locked":
        return EXIT_OK if trigger == "scheduler" else EXIT_ERROR
    if outcome.stop_reason in (service.STOP_AUTH, service.STOP_RATE_LIMIT, service.STOP_PROVIDER) or outcome.status == "failed":
        return EXIT_ERROR
    if outcome.stop_reason == service.STOP_BUDGET or outcome.status == "completed_with_errors":
        return EXIT_WARNING
    if outcome.status == "dry_run_completed":
        problems = outcome.counters.get("blocking_count") or outcome.counters.get("fixtures_missing_in_response")
        return EXIT_WARNING if problems else EXIT_OK
    return EXIT_OK


def _print_outcome(outcome: service.BackfillOutcome) -> None:
    if outcome.run_id is None:
        print("statistics reconcile: SKIPPED (ya hay un run de estadísticas en curso; lo reintenta la siguiente pasada)")
        return
    c = outcome.counters
    print(f"statistics reconcile: run #{outcome.run_id} {outcome.status}" + (f" ({outcome.stop_reason})" if outcome.stop_reason else ""))
    print(
        f"  partidos: debidos {c['fixtures_targeted']}, intentados {c['fixtures_attempted']} = available {c['fixtures_available']}"
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

    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            if args.fail_stale_run:
                outcome, run_id = fail_stale_run(db, args.stale_after_minutes)
                print(f"Recuperación (statistics, umbral {args.stale_after_minutes} min): {outcome}" + (f" run #{run_id}" if run_id else ""))
                return EXIT_OK if outcome == "recovered" else EXIT_WARNING
            print(f"Política temporal: live -> source={reconcile.LIVE_SOURCE}, available_at=observed_at")
            options = reconcile.ReconcileOptions(
                mode="dry_run" if args.dry_run else "apply",
                trigger=args.trigger,
                stats_daily_budget=args.budget,
                provider_daily_cap=args.provider_daily_cap,
            )
            outcome = asyncio.run(reconcile.run_live_reconcile(db, provider=default_provider(), options=options))
            _print_outcome(outcome)
            return exit_code(outcome, args.trigger)
    except Exception as exc:  # noqa: BLE001  (solo el tipo: el mensaje puede incluir el destino)
        return _err(f"error inesperado ({exc.__class__.__name__}); revisa statistics_runs")


if __name__ == "__main__":
    sys.exit(main())
