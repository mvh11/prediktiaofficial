"""Backfill histórico de fixtures/resultados por competición + temporada.

Unidad de trabajo: UN par competición-temporada = UNA transacción de dominio. Un par nunca
queda importado a medias.

Flujo de run_backfill:
1. Registro del run (status=running) en su propia transacción, que se confirma enseguida:
   el registro sobrevive aunque luego se deshagan las escrituras de dominio. Si ya hay otro run
   'running' del par: ValueError, sin registrar nada.
2. Precondiciones (solo lecturas): la temporada existe en Prediktia (no se crea), no es la
   temporada actual (esa la gestiona el sync vivo), está cerrada (end_date conocida y anterior a
   hoy) y no tiene ya un backfill `completed` salvo --refresh. La transacción se cierra ANTES
   de llamar al proveedor.
3. Descarga de la temporada del proveedor, sin transacción de BD abierta.
4. Transacción de escritura: bloquea el propio run (FOR UPDATE; si otro proceso lo cambió, no se
   escribe nada) y revalida las precondiciones del paso 2.
5. Análisis contra el estado actual, solo con lecturas: contadores nuevo/existente/cambiaría/
   sin cambios, equipos y season_teams que faltarían, protección cross-season, membresía
   (season_membership) y checks Q1, Q3 y Q4 (pre-write, globales), Q5–Q12, Q14, Q15 y Q16.
6. Si hay algún check bloqueante: rollback y status=blocked (cero escrituras de dominio).
7. Dry-run: rollback y status=dry_run_completed (Q2 y PARITY no aplican). No se escribe en
   fixtures, teams, season_teams ni mappings; el run sí queda registrado.
8. Ejecución real: equipos (ensure_teams), fixtures (upsert no destructivo), mapeos y
   season_teams. Teams, mapeos y fixtures reciben a TODOS los participantes; season_teams solo a
   los miembros. Después Q2 (la temporada no pierde fixtures), Q13 y PARITY (cada recibido quedó
   como se previó). Si alguno bloquea: rollback y status=blocked. Si todo pasa: el run se cierra
   como completed EN LA MISMA transacción y se hace commit; un error de BD: rollback y failed.
9. Los demás cierres (blocked, failed, dry_run_completed) van en otra transacción.

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
from app.services.season_membership import season_members

logger = logging.getLogger(__name__)

_DB_ERRORS = (IntegrityError, DataError, OperationalError)
# Índice único parcial que impide dos runs 'running' del mismo par (migración 0005)
_RUNNING_UNIQUE_INDEX = "uq_season_backfill_runs_one_running"
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

    Lanza ValueError, sin registrar run ni llamar al proveedor, si la competición no existe, si
    el rango esperado no es válido (obligatorio en una ejecución real, min <= max; opcional en
    dry-run) o si ya hay otro run en curso del mismo par.
    """
    _validate_expected_range(expected_range, dry_run)
    provider = provider or default_provider()
    now = now or datetime.now(timezone.utc)

    competition = db.get(Competition, competition_id)
    if competition is None:
        raise ValueError(f"No existe la competición con id interno {competition_id}")
    competition_name, league_external_id, competition_type = competition.name, competition.external_id, competition.type
    season = db.scalars(select(Season).where(Season.competition_id == competition_id, Season.year == year)).first()
    season_id = season.id if season else None

    try:
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
    except IntegrityError as exc:
        db.rollback()
        if _RUNNING_UNIQUE_INDEX in str(exc.orig):
            raise ValueError(
                "Ya hay un run en curso ('running') de este par; espera a que termine o usa --fail-stale-run si quedó abandonado"
            ) from exc
        raise

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
        already_closed = await _execute(
            db, result, provider, league_external_id, refresh, expected_range, now, competition_type=competition_type
        )
    except Exception as exc:  # cualquier fallo no previsto: el run no puede quedarse en 'running'
        db.rollback()
        result.status = "failed"
        result.error_message = f"Error inesperado ({exc.__class__.__name__})"
        _finish(db, result)
        raise
    if not already_closed:
        _finish(db, result)
    return result


def _precondition_block(db: Session, result: BackfillResult, refresh: bool, now: datetime) -> str | None:
    """Motivo de bloqueo por precondiciones (lecturas directas de la BD), o None."""
    if result.season_id is None:
        return f"La temporada {result.requested_year} no existe en Prediktia para esta competición (no se crea)"
    state = runs.season_state(db, result.season_id)
    if state is None:
        return f"La temporada {result.requested_year} ya no existe en Prediktia"
    if state["is_current"]:
        return "Es la temporada actual: la gestiona el sync vivo, no el backfill histórico"
    if (not_closed := _not_closed_reason(state["end_date"], now)) is not None:
        return not_closed
    if not refresh and runs.has_completed_run(db, result.competition_id, result.requested_year):
        return "Ya existe un backfill completed de este par; usa --refresh para reevaluarlo"
    return None


async def _execute(
    db: Session,
    result: BackfillResult,
    provider: FootballDataProvider,
    league_external_id: int,
    refresh: bool,
    expected_range: tuple[int, int] | None,
    now: datetime,
    *,
    competition_type: str | None,
) -> bool:
    """Ejecuta el par. Devuelve True si el run ya quedó cerrado dentro de su propia transacción
    (completed, atómico con el dominio; o run cambiado por otro proceso, que no se pisa) y False
    si el llamador debe cerrarlo con _finish."""
    # 2. Precondiciones (solo lecturas) y fin de la transacción ANTES de llamar al proveedor
    blocked_reason = _precondition_block(db, result, refresh, now)
    db.rollback()
    if blocked_reason:
        result.status, result.error_message = "blocked", blocked_reason
        return False

    # 3. Descarga, sin ninguna transacción de BD abierta
    try:
        fetched = await provider.get_fixtures(league_external_id, result.requested_year)
    except ProviderError as exc:
        result.status, result.error_message = "failed", f"Proveedor: {exc.message}"
        return False
    identity = getattr(provider, "last_fixture_identity", None)
    fixtures = list({f.external_id: f for f in fetched}.values())
    external_ids = [f.external_id for f in fixtures]

    # 4. Transacción de escritura: bloquea el propio run y revalida las precondiciones, porque
    # durante la descarga otro proceso pudo cambiar la temporada o el run
    status_now = runs.lock_run_status(db, result.run_id)
    if status_now != "running":
        db.rollback()
        result.status = "failed"
        result.error_message = f"El run pasó a '{status_now}' durante la descarga (otro proceso); no se escribió nada"
        return True  # no se pisa lo que dejó el otro proceso
    blocked_reason = _precondition_block(db, result, refresh, now)
    if blocked_reason:
        db.rollback()
        result.status, result.error_message = "blocked", f"Revalidación tras la descarga: {blocked_reason}"
        return False

    # 5. Análisis contra el estado actual (solo lecturas)
    existing = runs.existing_fixtures(db, external_ids)
    team_external_ids = sorted({f.home_team.external_id for f in fixtures} | {f.away_team.external_id for f in fixtures})
    known_teams = runs.known_teams(db, team_external_ids)
    in_season = runs.season_team_ids(db, result.season_id)
    # Todos los participantes se guardan (teams, mapeos, fixtures); solo los miembros van a season_teams
    membership = season_members(fixtures, competition_type)
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
    result.new_season_teams = sum(1 for e in membership.members if known_teams.get(e) not in in_season)

    season_before = runs.season_fixture_external_ids(db, result.season_id)
    checks = qc.evaluate_incoming(
        fixtures,
        identity=identity,
        league_external_id=league_external_id,
        year=result.requested_year,
        existing_seasons={e: row["season_id"] for e, row in existing.items()},
        target_season_id=result.season_id,
        season_closed=True,  # revalidado arriba en esta misma transacción
        now=now,
        expected_range=expected_range,
    )
    checks += [
        qc.q3_duplicate_mappings(runs.duplicate_mapping_count(db)),
        qc.q4_orphan_mappings(runs.orphan_mapping_count(db)),
        qc.q16_season_membership(membership),
    ]

    if any(c.is_blocking_failure for c in checks):  # 6. Bloqueo antes de escribir
        db.rollback()
        _set_checks(result, checks)
        result.status = "blocked"
        result.error_message = "Check bloqueante antes de escribir: " + ", ".join(c.id for c in checks if c.is_blocking_failure)
        return False

    if result.is_dry_run:  # 7. Dry-run
        mapped = runs.mapped_fixture_external_ids(db, provider.name, list(existing))
        checks += [
            qc.q2_season_fixtures_kept(season_before, None),
            qc.q13_mappings([], True, would_create=result.received - len(mapped)),
            qc.parity_not_applicable(),
        ]
        db.rollback()
        _set_checks(result, checks)
        result.status = "dry_run_completed"
        return False

    # 8. Ejecución real: dominio y cierre del run en UNA transacción
    try:
        teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
        team_ids = fixture_repository.ensure_teams(db, teams, provider.name)
        fixture_repository.upsert_fixtures(db, result.season_id, fixtures, team_ids, provider.name)
        member_ids = {team_ids[e] for e in membership.members}
        catalog_repository.link_teams_to_season(db, result.season_id, sorted(member_ids))

        mapped = runs.mapped_fixture_external_ids(db, provider.name, external_ids)
        mismatched = _parity_mismatches(db, provider.name, fixtures, existing, result.season_id, team_ids)
        checks += [
            qc.q2_season_fixtures_kept(season_before, runs.season_fixture_external_ids(db, result.season_id)),
            qc.q13_mappings([e for e in external_ids if e not in mapped], False),
            qc.parity(mismatched, len(fixtures), result.new, result.existing),
        ]
        _set_checks(result, checks)
        if result.blocking:
            db.rollback()
            result.status = "blocked"
            result.error_message = "Check bloqueante tras escribir (rollback): " + ", ".join(c.id for c in checks if c.is_blocking_failure)
            return False
        result.status = "completed"
        _finish(db, result)  # mismo commit que el dominio: o queda todo, o no queda nada
        return True
    except _DB_ERRORS as exc:
        db.rollback()
        _set_checks(result, checks)
        result.status = "failed"
        result.error_message = f"Error de base de datos ({exc.__class__.__name__}): rollback completo del par"
        logger.error("Backfill %s %s: %s", result.competition_name, result.requested_year, exc.__class__.__name__)
        return False


def _parity_mismatches(
    db: Session,
    provider_name: str,
    fixtures: list[FixtureData],
    existing: dict[int, dict],
    season_id: int,
    team_ids: dict[int, int],
) -> list[int]:
    """external_id de los partidos recibidos cuyo estado guardado no es el previsto.

    Para cada recibido, lo guardado debe ser predict_stored_values(fila previa o {}, entrante):
    con {} es exactamente la semántica de un insert. Además, entre todos, los recibidos deben
    tener exactamente un mapeo del proveedor por partido.
    """
    external_ids = [f.external_id for f in fixtures]
    after = runs.existing_fixtures(db, external_ids)
    mismatched = [
        f.external_id
        for f in fixtures
        if f.external_id not in after
        or any(
            after[f.external_id].get(c) != v
            for c, v in predict_stored_values(existing.get(f.external_id, {}), _incoming_row(f, season_id, team_ids)).items()
        )
    ]
    if not mismatched and runs.provider_mapping_count(db, provider_name, external_ids) != len(external_ids):
        mismatched = sorted(external_ids)[:1]  # mapeos de más o de menos: se marca el par
    return mismatched


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
    # Evidencia de solo lectura para la auditoría. Desde que el cierre 'completed' va en la misma
    # transacción que el dominio, un run abandonado no deja escrituras de dominio propias.
    evidence = "sin temporada"
    if run.season_id is not None:
        total, since_start = runs.season_fixture_evidence(db, run.season_id, run.started_at)
        evidence = f"{total} fixtures en la temporada, {since_start} creados desde el inicio del run"
    runs.finish_run(
        db,
        run_id,
        status="failed",
        error_message=(
            f"Recuperación administrativa: run abandonado en 'running' más de {stale_after_minutes} min; "
            f"marcado failed manualmente. Evidencia: {evidence}"
        ),
    )
    db.commit()
    return StaleRunRecovery(outcome="recovered", run_id=run_id, message=f"Run #{run_id} marcado como failed", **base)
