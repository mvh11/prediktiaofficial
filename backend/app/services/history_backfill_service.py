"""Backfill histórico de fixtures/resultados por competición + temporada.

Unidad de trabajo: UN par competición-temporada = UNA transacción de dominio. Un par nunca
queda importado a medias.

Flujo de run_backfill:
1. Registro del run (status=running) en su propia transacción, que se confirma enseguida:
   el registro sobrevive aunque luego se deshagan las escrituras de dominio.
2. Precondiciones (sin escribir datos de dominio): la temporada existe en Prediktia (no se crea),
   no es la temporada actual (esa la gestiona el sync vivo), está cerrada (end_date conocida y
   anterior a hoy) y no tiene ya un backfill `completed` salvo que se pida --refresh.
3. Descarga de la temporada del proveedor (el adapter normaliza los marcadores).
4. Análisis contra el estado previo de la BD, solo con lecturas: contadores nuevo/existente/
   cambiaría/sin cambios, equipos y season_teams que faltarían, protección cross-season y
   checks Q1, Q3–Q12, Q14, Q15.
5. Si hay algún check bloqueante: rollback y status=blocked (cero escrituras de dominio).
6. Dry-run: rollback y status=dry_run_completed. No se escribe en fixtures, teams, season_teams
   ni mappings; el run sí queda registrado para auditar lo evaluado.
7. Ejecución real, en una sola transacción: equipos (ensure_teams), fixtures (upsert no
   destructivo), mapeos (escritura doble del repositorio) y season_teams. Después Q2, Q3, Q4 y
   Q13 sobre el estado resultante. Si alguno bloquea: rollback y status=blocked. Si todo pasa:
   commit y status=completed. Un error de BD: rollback y status=failed.
8. Cierre del run (contadores, checks y mensaje) en otra transacción.

Las anomalías del proveedor se detectan (warnings) pero nunca se corrigen.
"""

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.exceptions import ProviderError
from app.integrations.football.api_football_history import RecordingApiFootballProvider
from app.integrations.football.base import FootballDataProvider
from app.models import Competition, Season
from app.repositories import backfill_repository as runs
from app.repositories import catalog_repository, fixture_repository
from app.repositories.fixture_repository import _FIXTURE_COLUMNS, predict_score_values
from app.schemas.backfill import BackfillResult, CheckResult, StaleRunRecovery
from app.schemas.fixture import FixtureData
from app.services import history_quality_checks as qc

logger = logging.getLogger(__name__)

_DB_ERRORS = (IntegrityError, DataError, OperationalError)
# Un par tarda segundos; 2 horas sin terminar es, con mucho margen, un proceso muerto
DEFAULT_STALE_AFTER_MINUTES = 120
# Columnas que escribe el upsert de fixtures (las que pueden "cambiar")
_COMPARED_COLUMNS = [c for c in _FIXTURE_COLUMNS if c != "external_id"] + ["season_id", "home_team_id", "away_team_id"]


def default_provider() -> RecordingApiFootballProvider:
    settings = get_settings()
    return RecordingApiFootballProvider(
        api_key=settings.api_football_key.get_secret_value(),
        base_url=settings.api_football_base_url,
        timeout=settings.http_timeout_seconds,
    )


def predict_stored_values(stored: dict, incoming: dict) -> dict:
    """Valores que dejaría el upsert de fixtures en una fila existente.

    Los marcadores salen de fixture_repository.predict_score_values, la versión Python de la
    misma política que aplica el upsert (no hay una copia aparte aquí); el resto de columnas
    se sobrescriben tal cual. Sirve para contar 'cambiaría / sin cambios' sin escribir.
    """
    result = {c: incoming.get(c) for c in _COMPARED_COLUMNS}
    result.update(predict_score_values(stored, incoming))
    return result


def _incoming_row(f: FixtureData, season_id: int, team_ids: dict[int, int]) -> dict:
    return {
        **f.model_dump(include=set(_FIXTURE_COLUMNS)),
        "season_id": season_id,
        "home_team_id": team_ids.get(f.home_team.external_id),
        "away_team_id": team_ids.get(f.away_team.external_id),
    }


def _would_change(stored: dict, incoming: dict) -> bool:
    predicted = predict_stored_values(stored, incoming)
    return any(stored.get(c) != predicted[c] for c in _COMPARED_COLUMNS)


def _validate_expected_range(expected_range: tuple[int, int] | None, dry_run: bool) -> None:
    if expected_range is None:
        if not dry_run:
            raise ValueError("Una ejecución real exige expected_range (mínimo y máximo de partidos esperados)")
        return
    low, high = expected_range
    if low > high:
        raise ValueError(f"expected_range inválido: el mínimo {low} es mayor que el máximo {high}")


def _not_closed_reason(end_date: date | None, now: datetime) -> str | None:
    """Motivo de bloqueo si la temporada no está cerrada: end_date debe existir y ser anterior a hoy."""
    today = now.astimezone(timezone.utc).date()
    if end_date is None:
        return "La temporada no tiene end_date: no se puede confirmar que esté cerrada"
    if end_date >= today:
        return f"La temporada no está cerrada: end_date {end_date.isoformat()} no es anterior a hoy ({today.isoformat()})"
    return None


async def run_backfill(
    db: Session,
    competition_id: int,
    year: int,
    *,
    dry_run: bool = False,
    refresh: bool = False,
    provider: FootballDataProvider | None = None,
    expected_range: tuple[int, int] | None = None,
    now: datetime | None = None,
) -> BackfillResult:
    """Backfill de un par competición-temporada.

    Lanza ValueError, sin registrar run ni llamar al proveedor, si la competición no existe o si
    el rango esperado no es válido: obligatorio en una ejecución real (min <= max), opcional en
    dry-run.
    """
    _validate_expected_range(expected_range, dry_run)
    provider = provider or default_provider()
    now = now or datetime.now(timezone.utc)

    competition = db.get(Competition, competition_id)
    if competition is None:
        raise ValueError(f"No existe la competición con id interno {competition_id}")
    competition_name, league_external_id = competition.name, competition.external_id
    season = db.scalars(select(Season).where(Season.competition_id == competition_id, Season.year == year)).first()
    season_id = season.id if season else None
    season_is_current = bool(season and season.is_current)
    season_end_date = season.end_date if season else None

    run_id = runs.create_run(
        db,
        competition_id=competition_id,
        requested_year=year,
        provider=provider.name,
        is_dry_run=dry_run,
        is_refresh=refresh,
        season_id=season_id,
    )
    db.commit()

    result = BackfillResult(
        run_id=run_id,
        competition_id=competition_id,
        competition_name=competition_name,
        requested_year=year,
        season_id=season_id,
        provider=provider.name,
        is_dry_run=dry_run,
        is_refresh=refresh,
        status="running",
    )

    try:
        await _execute(
            db, result, provider, league_external_id, season_is_current, season_end_date, refresh, expected_range, now
        )
    except Exception as exc:  # cualquier fallo no previsto: el run no puede quedarse en 'running'
        db.rollback()
        result.status = "failed"
        result.error_message = f"Error inesperado ({exc.__class__.__name__})"
        _finish(db, result)
        raise
    _finish(db, result)
    return result


async def _execute(
    db: Session,
    result: BackfillResult,
    provider: FootballDataProvider,
    league_external_id: int,
    season_is_current: bool,
    season_end_date: date | None,
    refresh: bool,
    expected_range: tuple[int, int] | None,
    now: datetime,
) -> None:
    # 2. Precondiciones
    blocked_reason = None
    if result.season_id is None:
        blocked_reason = f"La temporada {result.requested_year} no existe en Prediktia para esta competición (no se crea)"
    elif season_is_current:
        blocked_reason = "Es la temporada actual: la gestiona el sync vivo, no el backfill histórico"
    elif (not_closed := _not_closed_reason(season_end_date, now)) is not None:
        blocked_reason = not_closed
    elif not refresh and runs.has_completed_run(db, result.competition_id, result.requested_year):
        blocked_reason = "Ya existe un backfill completed de este par; usa --refresh para reevaluarlo"
    if blocked_reason:
        db.rollback()
        result.status, result.error_message = "blocked", blocked_reason
        return

    # 3. Descarga
    try:
        fetched = await provider.get_fixtures(league_external_id, result.requested_year)
    except ProviderError as exc:
        db.rollback()
        result.status, result.error_message = "failed", f"Proveedor: {exc.message}"
        return
    identity = getattr(provider, "last_fixture_identity", None)
    fixtures = list({f.external_id: f for f in fetched}.values())
    external_ids = [f.external_id for f in fixtures]

    # 4. Análisis contra el estado previo (solo lecturas)
    existing = runs.existing_fixtures(db, external_ids)
    team_external_ids = sorted({f.home_team.external_id for f in fixtures} | {f.away_team.external_id for f in fixtures})
    known_teams = runs.known_teams(db, team_external_ids)
    in_season = runs.season_team_ids(db, result.season_id)
    changed = sum(
        1
        for f in fixtures
        if f.external_id in existing
        and _would_change(existing[f.external_id], _incoming_row(f, result.season_id, known_teams))
    )
    result.received = len(fixtures)
    result.existing = len(existing)
    result.new = result.received - result.existing
    result.changed = changed
    result.unchanged = result.existing - changed
    result.new_teams = len(set(team_external_ids) - set(known_teams))
    result.new_season_teams = sum(1 for e in team_external_ids if known_teams.get(e) not in in_season)

    before = runs.count_fixtures(db)
    checks = qc.evaluate_incoming(
        fixtures,
        identity=identity,
        league_external_id=league_external_id,
        year=result.requested_year,
        existing_seasons={e: row["season_id"] for e, row in existing.items()},
        target_season_id=result.season_id,
        season_closed=not season_is_current,
        now=now,
        expected_range=expected_range,
    )
    checks += [qc.q3_duplicate_mappings(runs.duplicate_mapping_count(db)), qc.q4_orphan_mappings(runs.orphan_mapping_count(db))]

    if any(c.is_blocking_failure for c in checks):  # 5. Bloqueo antes de escribir
        db.rollback()
        _set_checks(result, checks)
        result.status = "blocked"
        result.error_message = "Check bloqueante antes de escribir: " + ", ".join(c.id for c in checks if c.is_blocking_failure)
        return

    if result.is_dry_run:  # 6. Dry-run
        mapped = runs.mapped_fixture_external_ids(db, provider.name, list(existing))
        checks += [qc.q2_fixture_count(before, None), qc.q13_mappings([], True, would_create=result.received - len(mapped))]
        db.rollback()
        _set_checks(result, checks)
        result.status = "dry_run_completed"
        return

    # 7. Ejecución real: una transacción para todo el par
    try:
        teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
        team_ids = fixture_repository.ensure_teams(db, teams, provider.name)
        fixture_repository.upsert_fixtures(db, result.season_id, fixtures, team_ids, provider.name)
        catalog_repository.link_teams_to_season(db, result.season_id, sorted(set(team_ids.values())))

        # Paridad: el estado real tras el upsert debe ser exactamente el que predijo el análisis
        after_state = runs.existing_fixtures(db, list(existing))
        by_external = {f.external_id: f for f in fixtures}
        mismatched = [
            e
            for e, stored in existing.items()
            if any(
                after_state[e].get(c) != v
                for c, v in predict_stored_values(stored, _incoming_row(by_external[e], result.season_id, team_ids)).items()
            )
        ]
        mapped = runs.mapped_fixture_external_ids(db, provider.name, external_ids)
        checks += [
            qc.q2_fixture_count(before, runs.count_fixtures(db)),
            qc.q3_duplicate_mappings(runs.duplicate_mapping_count(db)),
            qc.q4_orphan_mappings(runs.orphan_mapping_count(db)),
            qc.q13_mappings([e for e in external_ids if e not in mapped], False),
        ]
        if mismatched:
            checks.append(
                CheckResult(
                    id="PARITY",
                    severity="warning",
                    passed=False,
                    count=len(mismatched),
                    detail="El estado guardado no coincide con el previsto por el análisis",
                    samples=mismatched[:5],
                )
            )
        if any(c.is_blocking_failure for c in checks):
            db.rollback()
            _set_checks(result, checks)
            result.status = "blocked"
            result.error_message = "Check bloqueante tras escribir (rollback): " + ", ".join(c.id for c in checks if c.is_blocking_failure)
            return
        db.commit()
    except _DB_ERRORS as exc:
        db.rollback()
        _set_checks(result, checks)
        result.status = "failed"
        result.error_message = f"Error de base de datos ({exc.__class__.__name__}): rollback completo del par"
        logger.error("Backfill %s %s: %s", result.competition_name, result.requested_year, exc.__class__.__name__)
        return
    _set_checks(result, checks)
    result.status = "completed"


def _set_checks(result: BackfillResult, checks: list[CheckResult]) -> None:
    result.checks = qc.sort_checks(checks)
    result.warnings = sum(1 for c in checks if c.is_warning)
    result.blocking = sum(1 for c in checks if c.is_blocking_failure)


def _finish(db: Session, result: BackfillResult) -> None:
    runs.finish_run(
        db,
        result.run_id,
        status=result.status,
        received_count=result.received,
        new_count=result.new,
        existing_count=result.existing,
        changed_count=result.changed,
        unchanged_count=result.unchanged,
        new_teams_count=result.new_teams,
        new_season_teams_count=result.new_season_teams,
        warning_count=result.warnings,
        blocking_count=result.blocking,
        checks=[c.model_dump() for c in result.checks],
        error_message=result.error_message,
    )
    db.commit()


def fail_stale_run(
    db: Session, competition_id: int, year: int, stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES
) -> StaleRunRecovery:
    """Marca como failed el run 'running' abandonado de un par. Operación administrativa explícita.

    - No borra el run ni lo marca completed, y NO lanza ningún backfill después.
    - Solo actúa si started_at es anterior a now() - stale_after_minutes; si no, lo rechaza.
    - Bloquea la fila (FOR UPDATE) antes de decidir, así dos recuperaciones simultáneas no
      pueden pisarse.
    """
    if stale_after_minutes < 1:
        raise ValueError("stale_after_minutes debe ser >= 1")
    base = dict(competition_id=competition_id, requested_year=year, stale_after_minutes=stale_after_minutes)
    run = runs.lock_running_run(db, competition_id, year)
    if run is None:
        db.rollback()
        return StaleRunRecovery(outcome="not_found", message="No hay ningún run en 'running' para este par; no se modificó nada", **base)
    run_id = run.id
    if not runs.is_older_than(db, run_id, stale_after_minutes):
        db.rollback()
        return StaleRunRecovery(
            outcome="too_recent",
            run_id=run_id,
            message=f"El run #{run_id} empezó hace menos de {stale_after_minutes} min: puede seguir activo; no se modificó",
            **base,
        )
    runs.finish_run(
        db,
        run_id,
        status="failed",
        error_message=f"Recuperación administrativa: run abandonado en 'running' más de {stale_after_minutes} min; marcado failed manualmente",
    )
    db.commit()
    return StaleRunRecovery(outcome="recovered", run_id=run_id, message=f"Run #{run_id} marcado como failed", **base)
