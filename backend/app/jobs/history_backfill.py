"""CLI del backfill histórico de UN par competición-temporada.

Uso (desde backend/):
    python -m app.jobs.history_backfill --competition-id 5 --season 2025 --dry-run [--refresh]
    python -m app.jobs.history_backfill --competition-id 5 --season 2025 --expected-min 370 --expected-max 390         --confirm-target ep-xxx.region.aws.neon.tech:5432/neondb [--refresh]
    python -m app.jobs.history_backfill --competition-id 5 --season 2025 --fail-stale-run [--stale-after-minutes 120]

--competition-id es el id INTERNO de Prediktia (GET /competitions); --season es el año de la
temporada tal como lo usa el proveedor (p. ej. 2025 = 2025/26 en ligas que cruzan años).
Escribe en la BD de DATABASE_URL y hace 1 petición al proveedor por invocación.

--expected-min/--expected-max: rango de partidos esperado para Q1. Van siempre juntos; en una
ejecución real son obligatorios (en dry-run son opcionales, sirve para descubrir el rango).
--confirm-target <host>:<puerto>/<bd>: obligatorio en una ejecución real (opcional en dry-run).
Si se indica, debe coincidir exactamente con el destino de DATABASE_URL; si no, se aborta antes
de abrir la BD o llamar al proveedor. El destino se muestra siempre saneado (sin usuario ni contraseña).
--fail-stale-run es una operación administrativa SEPARADA: marca como failed el run abandonado
en 'running' de ese par (si es más antiguo que --stale-after-minutes) y NO lanza ningún backfill.

Códigos de salida: 0 completed/dry_run_completed/recuperado · 1 blocked o recuperación
rechazada/sin run · 2 failed o error de uso.
"""

import argparse
import asyncio
import sys

from sqlalchemy.engine import make_url

from app.core.logging import setup_logging
from app.schemas.backfill import BackfillResult, StaleRunRecovery

EXIT_CODES = {"completed": 0, "dry_run_completed": 0, "blocked": 1, "failed": 2}
RECOVERY_EXIT_CODES = {"recovered": 0, "not_found": 1, "too_recent": 1}
DEFAULT_STALE_AFTER_MINUTES = 120  # igual que history_backfill_service.DEFAULT_STALE_AFTER_MINUTES


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.history_backfill",
        description="Backfill histórico de fixtures/resultados de un par competición-temporada.",
    )
    parser.add_argument("--competition-id", type=int, required=True, help="id interno de la competición")
    parser.add_argument("--season", type=int, required=True, help="año de la temporada (p. ej. 2025)")
    parser.add_argument("--dry-run", action="store_true", help="evalúa y registra el run sin escribir datos")
    parser.add_argument("--refresh", action="store_true", help="permite reevaluar un par ya completado")
    parser.add_argument("--expected-min", type=int, help="Q1: mínimo de partidos esperados (con --expected-max)")
    parser.add_argument("--expected-max", type=int, help="Q1: máximo de partidos esperados (con --expected-min)")
    parser.add_argument(
        "--confirm-target",
        help="ejecución real: destino <host>:<puerto>/<bd> que debe coincidir con DATABASE_URL",
    )
    parser.add_argument(
        "--fail-stale-run",
        action="store_true",
        help="operación administrativa: marca como failed el run 'running' abandonado del par (no hace backfill)",
    )
    parser.add_argument(
        "--stale-after-minutes",
        type=int,
        default=None,
        help=f"con --fail-stale-run: antigüedad mínima del run para considerarlo abandonado (por defecto {DEFAULT_STALE_AFTER_MINUTES})",
    )
    args = parser.parse_args(argv)

    if (args.expected_min is None) != (args.expected_max is None):
        parser.error("--expected-min y --expected-max van juntos")
    if args.expected_min is not None:
        if args.expected_min < 0:
            parser.error("--expected-min no puede ser negativo")
        if args.expected_max < args.expected_min:
            parser.error("--expected-max no puede ser menor que --expected-min")
    if args.fail_stale_run:
        if args.dry_run or args.refresh or args.expected_min is not None or args.confirm_target is not None:
            parser.error(
                "--fail-stale-run es una operación separada: no admite --dry-run, --refresh, --expected-* ni --confirm-target"
            )
        if args.stale_after_minutes is None:
            args.stale_after_minutes = DEFAULT_STALE_AFTER_MINUTES
        if args.stale_after_minutes < 1:
            parser.error("--stale-after-minutes debe ser >= 1")
    elif args.stale_after_minutes is not None:
        parser.error("--stale-after-minutes solo se usa con --fail-stale-run")
    if not args.fail_stale_run and not args.dry_run:
        if args.expected_min is None:
            parser.error("una ejecución real exige --expected-min y --expected-max (usa --dry-run para descubrir el rango)")
        if args.confirm_target is None:
            parser.error("una ejecución real exige --confirm-target <host>:<puerto>/<bd>")
    return args


def database_target(url: str) -> str:
    """Destino saneado "<host>:<puerto>/<bd>" de una URL: nunca incluye usuario ni contraseña."""
    parsed = make_url(url)
    if not parsed.host or not parsed.database:
        raise ValueError("DATABASE_URL no tiene un host y una base de datos identificables")
    return f"{parsed.host.lower()}:{parsed.port or 5432}/{parsed.database}"


def _configured_target() -> str:
    from app.core.config import get_settings

    return database_target(get_settings().database_url)


def expected_range(args: argparse.Namespace) -> tuple[int, int] | None:
    return None if args.expected_min is None else (args.expected_min, args.expected_max)


def format_result(result: BackfillResult) -> str:
    mode = ("dry-run" if result.is_dry_run else "real") + (" + refresh" if result.is_refresh else "")
    verbs = ("se insertarían", "cambiarían", "quedarían iguales") if result.is_dry_run else ("nuevos", "cambiados", "sin cambios")
    lines = [
        f"Run #{result.run_id}",
        f"Competición: {result.competition_name} (id {result.competition_id})",
        f"Temporada:   {result.requested_year} (season_id {result.season_id})",
        f"Proveedor:   {result.provider}",
        f"Modo:        {mode}",
        f"Recibidos:   {result.received}",
        f"{verbs[0].capitalize()}: {result.new} · {verbs[1]}: {result.changed} · {verbs[2]}: {result.unchanged}",
        f"Equipos nuevos: {result.new_teams} · season_teams nuevos: {result.new_season_teams}",
        f"Warnings: {result.warnings} · Bloqueantes: {result.blocking}",
    ]
    for check in result.checks:
        if not check.passed:
            sample = f" ej. {check.samples}" if check.samples else ""
            lines.append(f"  [{check.severity.upper()}] {check.id}: {check.count} · {check.detail}{sample}")
    lines.append(f"Resultado:   {result.status.upper()}")
    if result.error_message:
        lines.append(f"Motivo:      {result.error_message}")
    return "\n".join(lines)


def format_recovery(recovery: StaleRunRecovery) -> str:
    return "\n".join(
        [
            f"Recuperación de run abandonado · competición {recovery.competition_id} · temporada {recovery.requested_year}",
            f"Umbral: {recovery.stale_after_minutes} min",
            f"Resultado: {recovery.outcome.upper()}",
            recovery.message,
        ]
    )


async def _run(args: argparse.Namespace) -> BackfillResult:
    from app.db.session import SessionLocal
    from app.services.history_backfill_service import run_backfill

    with SessionLocal() as db:
        return await run_backfill(
            db,
            args.competition_id,
            args.season,
            dry_run=args.dry_run,
            refresh=args.refresh,
            expected_range=expected_range(args),
        )


def _recover(args: argparse.Namespace) -> StaleRunRecovery:
    from app.db.session import SessionLocal
    from app.services.history_backfill_service import fail_stale_run

    with SessionLocal() as db:
        return fail_stale_run(db, args.competition_id, args.season, args.stale_after_minutes)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("WARNING")
    if args.fail_stale_run:
        recovery = _recover(args)
        print(format_recovery(recovery))
        return RECOVERY_EXIT_CODES[recovery.outcome]
    try:
        target = _configured_target()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Destino BD:  {target}")
    if args.confirm_target is not None and args.confirm_target.strip() != target:
        print(f"Error: --confirm-target no coincide con el destino de DATABASE_URL ({target}); no se ha escrito nada", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(_run(args))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(format_result(result))
    return EXIT_CODES.get(result.status, 2)


if __name__ == "__main__":
    sys.exit(main())
