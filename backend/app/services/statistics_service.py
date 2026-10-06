"""Backfill de estadísticas de partido por temporada (M5.3).

Flujo por lote de hasta 20 partidos (una petición /fixtures?ids vía el adapter de M5.1):
1. Se seleccionan los partidos (valores planos) y se cierra la transacción de lectura.
2. Petición al proveedor SIN transacción de BD abierta.
3. Nueva transacción: revalidación (el partido existe, sigue final y con el mismo mapping),
   identidad de los equipos, quality checks, escritura de observaciones/estado normalizado,
   contadores, detail, cursor y cobertura del run. Commit: el lote es atómico.

El service no conoce los nombres del proveedor: trabaja con el contrato interno.

Elegibilidad: solo se piden partidos FT/AET/PEN. AWD/WO no se piden (resultado administrativo,
sin juego que medir): se cuentan en la cobertura como excluidos y quedan como INFO.

Estado normalizado (fixture_team_statistics) = último estado ÚTIL, no la proyección exacta de
la última observación raw. Merge no destructivo:
- un valor nuevo no nulo sustituye al anterior (también enriquece un NULL previo);
- un NULL o un tipo ausente en la respuesta nueva NO borra un valor ya normalizado;
- un equipo que deja de traer estadísticas conserva su fila;
- la observación raw nueva siempre se registra y pasa a ser la vigente;
- cualquier pérdida frente a lo normalizado es un WARNING degraded_observation.
observation_id de una fila = última observación que aportó estadísticas de ese equipo.

Disponibilidad temporal: en backfill (source='backfill') available_at = kickoff + 6 h, una
disponibilidad SINTÉTICA (no es cuándo publicó el proveedor); observed_at = ahora real.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.exceptions import ProviderAuthError, ProviderError, ProviderRateLimitError
from app.models import Fixture, FixtureProviderMapping, FixtureStatisticsObservation, FixtureTeamStatistics, Season, TeamProviderMapping
from app.repositories import statistics_repository as stats_repo
from app.repositories import statistics_run_repository as runs
from app.repositories.provider_mapping_repository import canonical_external_id
from app.schemas.fixture import FINISHED_STATUSES
from app.schemas.statistics import STATISTIC_FIELDS, FixtureStatisticsData, TeamStatisticValues
from app.services import statistics_quality_checks as qc
from app.services.statistics_coverage import CoverageAccumulator

logger = logging.getLogger(__name__)

FETCH_STATUSES = frozenset(FINISHED_STATUSES)  # FT, AET, PEN
AWARDED_STATUSES = frozenset({"AWD", "WO"})
BATCH_SIZE = 20
BACKFILL_AVAILABILITY_DELAY = timedelta(hours=6)
NORMALIZER_VERSION = 1
DEFAULT_STATS_DAILY_BUDGET = 1500
DEFAULT_PROVIDER_DAILY_CAP = 6000  # margen bajo el límite del plan (7500) para la live sync
REQUEST_HEADROOM = 3  # una petición puede convertirse en hasta 3 con los reintentos del adapter

# Motivos de parada global (run sin terminar el recorrido)
STOP_BUDGET = "budget_exhausted"
STOP_RATE_LIMIT = "rate_limited"
STOP_AUTH = "auth_failed"
STOP_PROVIDER = "provider_error"


class NothingToResume(Exception):
    """--resume sin ningún run interrumpido de esa temporada y modo."""


class SeasonMismatch(Exception):
    """La temporada no pertenece a la competición indicada."""


@dataclass
class BackfillOptions:
    mode: str = "dry_run"  # dry_run | apply
    trigger: str = "cli"
    refresh: bool = False  # vuelve a pedir partidos que ya tienen observación
    resume: bool = False  # continúa tras el cursor del último run interrumpido
    stats_daily_budget: int = DEFAULT_STATS_DAILY_BUDGET
    provider_daily_cap: int = DEFAULT_PROVIDER_DAILY_CAP
    batch_size: int = BATCH_SIZE


@dataclass
class BackfillOutcome:
    run_id: int | None
    status: str  # estado final del run, o "locked"
    stop_reason: str | None = None
    counters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    fixture_id: int
    provider_fixture_id: str | None  # mapping activo (texto), None si no hay


@dataclass
class FixturePlan:
    target: Target
    outcome: str  # available | partial | empty | blocked | missing
    issues: list[qc.Issue] = field(default_factory=list)
    observation: str | None = None  # created | changed | unchanged (real o previsto)
    rows: list[str] = field(default_factory=list)  # created | updated | unchanged por equipo
    values_for_coverage: list[TeamStatisticValues] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Selección ----------------------------------------------------------------------------


def select_targets(db: Session, season_id: int, provider: str, *, refresh: bool, after_fixture_id: int | None) -> tuple[list[Target], int, int]:
    """Partidos FT/AET/PEN de la temporada, por id interno ascendente (cursor estable).
    Devuelve (objetivos, excluidos AWD/WO, saltados por tener ya observación)."""
    mapping = (
        select(FixtureProviderMapping.fixture_id, FixtureProviderMapping.external_id)
        .where(FixtureProviderMapping.provider == provider, FixtureProviderMapping.is_active.is_(True))
        .subquery()
    )
    rows = db.execute(
        select(Fixture.id, Fixture.status_short, mapping.c.external_id)
        .outerjoin(mapping, mapping.c.fixture_id == Fixture.id)
        .where(Fixture.season_id == season_id)
        .order_by(Fixture.id)
    ).all()
    observed = set()
    if not refresh:
        observed = set(
            db.scalars(
                select(FixtureStatisticsObservation.fixture_id)
                .join(Fixture, Fixture.id == FixtureStatisticsObservation.fixture_id)
                .where(Fixture.season_id == season_id, FixtureStatisticsObservation.provider == provider,
                       FixtureStatisticsObservation.is_latest.is_(True))
            )
        )
    targets, awarded, skipped, seen = [], 0, 0, set()
    for fixture_id, status, external_id in rows:
        if fixture_id in seen:  # varios mappings activos: el primero (la identidad la revalida el lote)
            continue
        seen.add(fixture_id)
        if after_fixture_id is not None and fixture_id <= after_fixture_id:
            continue
        if status in AWARDED_STATUSES:
            awarded += 1
            continue
        if status not in FETCH_STATUSES:
            continue
        if fixture_id in observed:
            skipped += 1
            continue
        targets.append(Target(fixture_id, external_id))
    return targets, awarded, skipped


def _requestable_id(target: Target) -> int | None:
    if target.provider_fixture_id is None:
        return None
    value = canonical_external_id(target.provider_fixture_id)
    return int(value) if value.isdigit() else None


# --- Evaluación de un partido ------------------------------------------------------------------


def _merge(existing: FixtureTeamStatistics | None, new: TeamStatisticValues) -> tuple[TeamStatisticValues, list[str]]:
    """Merge no destructivo: (valores resultantes, campos que la respuesta nueva perdió)."""
    if existing is None:
        return new, []
    merged, lost = {}, []
    for name in STATISTIC_FIELDS:
        value, previous = getattr(new, name), getattr(existing, name)
        if value is None and previous is not None:
            lost.append(name)
            merged[name] = previous
        else:
            merged[name] = value
    return TeamStatisticValues(**merged), lost


def _predict_row(existing: FixtureTeamStatistics | None, side: str, values: TeamStatisticValues) -> str:
    if existing is None:
        return "created"
    stored = {name: getattr(existing, name) for name in (*STATISTIC_FIELDS, "side")}
    return "unchanged" if stored == {**stats_repo.column_values(values), "side": side} else "updated"


def evaluate_fixture(
    db: Session,
    target: Target,
    data: FixtureStatisticsData,
    *,
    provider: str,
    mode: str,
    run_id: int,
    observed_at: datetime,
) -> FixturePlan:
    pid = target.provider_fixture_id
    ctx = {"provider_fixture_id": pid, "fixture_id": target.fixture_id}
    plan = FixturePlan(target, "blocked")

    def block(code: str, **detail: Any) -> FixturePlan:
        plan.issues.append(qc.Issue(code, detail=detail, **ctx))
        return plan

    fixture = db.execute(
        select(Fixture.id, Fixture.status_short, Fixture.kickoff_at, Fixture.home_team_id, Fixture.away_team_id).where(Fixture.id == target.fixture_id)
    ).first()
    if fixture is None:
        return block(qc.FIXTURE_NOT_FOUND)
    current_ids = {
        canonical_external_id(e)
        for e in db.scalars(
            select(FixtureProviderMapping.external_id).where(
                FixtureProviderMapping.fixture_id == target.fixture_id,
                FixtureProviderMapping.provider == provider,
                FixtureProviderMapping.is_active.is_(True),
            )
        )
    }
    if canonical_external_id(pid) not in current_ids:
        return block(qc.FIXTURE_MAPPING_CHANGED, active_ids=sorted(current_ids))
    with_stats = [t for t in data.teams if t.has_statistics]
    if fixture.status_short not in FETCH_STATUSES:
        return block(qc.FIXTURE_NOT_FINAL, status=fixture.status_short, provider_status=data.provider_status, has_content=bool(with_stats))
    if len(data.teams) > 2:  # decisión A: ni observación ni normalización; evidencia en details
        return block(qc.TOO_MANY_TEAMS, teams=len(data.teams), provider_team_ids=[t.provider_team_id for t in data.teams])

    # Identidad de TODAS las entradas de equipo (también las vacías: un equipo ajeno delata
    # un partido equivocado)
    sides = {fixture.home_team_id: "home", fixture.away_team_id: "away"}
    resolved: dict[int, int] = {}  # índice de entrada → team_id interno
    seen_teams: set[int] = set()
    for index, team in enumerate(data.teams):
        tid = team.provider_team_id
        if tid is None:
            plan.issues.append(qc.Issue(qc.TEAM_WITHOUT_ID, detail={"entry": index}, **ctx))
            continue
        team_id = db.scalar(
            select(TeamProviderMapping.team_id).where(
                TeamProviderMapping.provider == provider,
                TeamProviderMapping.external_id == canonical_external_id(tid),
                TeamProviderMapping.is_active.is_(True),
            )
        )
        if team_id is None:
            plan.issues.append(qc.Issue(qc.TEAM_WITHOUT_MAPPING, provider_team_id=tid, **ctx))
        elif team_id not in sides:
            plan.issues.append(qc.Issue(qc.TEAM_NOT_IN_FIXTURE, provider_team_id=tid, detail={"team_id": team_id}, **ctx))
        elif team_id in seen_teams:
            plan.issues.append(qc.Issue(qc.DUPLICATE_TEAM, provider_team_id=tid, detail={"team_id": team_id}, **ctx))
        else:
            seen_teams.add(team_id)
            resolved[index] = team_id
        plan.issues.extend(qc.team_value_issues(team, **ctx))

    availability = ("empty", "partial", "available")[len(with_stats)]
    blocked = any(i.severity == qc.BLOCKING for i in plan.issues)

    # Observación raw: se conserva también si el partido queda bloqueado por equipos o valores
    latest = stats_repo.get_latest_observation(db, target.fixture_id, provider)
    payload = data.raw_statistics if data.raw_statistics is not None else []
    if mode == "apply":
        result = stats_repo.record_observation(
            db,
            fixture_id=target.fixture_id,
            provider=provider,
            provider_fixture_id=pid,
            payload=payload,
            source="backfill",
            availability=availability,
            teams_returned=len(with_stats),
            observed_at=observed_at,
            available_at=fixture.kickoff_at + BACKFILL_AVAILABILITY_DELAY,
            fixture_status_at_fetch=data.provider_status,
            run_id=run_id,
        )
        plan.observation, observation_id = result.outcome, result.observation_id
    else:
        digest = stats_repo.payload_hash(payload)
        plan.observation = "created" if latest is None else ("unchanged" if latest.payload_hash == digest else "changed")
        observation_id = None
    if blocked:
        return plan

    plan.outcome = availability
    if availability == "partial":
        plan.issues.append(qc.Issue(qc.PARTIAL_STATISTICS, **ctx))
    elif availability == "empty":
        plan.issues.append(qc.Issue(qc.FINAL_FIXTURE_EMPTY, detail={"status": fixture.status_short}, **ctx))

    existing_rows = {
        row.team_id: row
        for row in db.scalars(
            select(FixtureTeamStatistics)
            .where(FixtureTeamStatistics.fixture_id == target.fixture_id, FixtureTeamStatistics.provider == provider)
            .execution_options(populate_existing=True)
        )
    }
    degraded: dict[str, Any] = {}
    present_team_ids = {resolved[i] for i, t in enumerate(data.teams) if t.has_statistics and i in resolved}
    missing_teams = sorted(set(existing_rows) - present_team_ids)
    if missing_teams:
        degraded["teams_without_statistics_now"] = missing_teams

    by_side: dict[str, TeamStatisticValues] = {}
    for index, team in enumerate(data.teams):
        if not team.has_statistics:
            continue
        team_id = resolved[index]
        side = sides[team_id]
        existing = existing_rows.get(team_id)
        merged, lost = _merge(existing, team.values)
        if lost:
            degraded.setdefault("fields_lost", {})[str(team_id)] = lost
        plan.issues.extend(qc.consistency_issues(team.values, team.provider_team_id, **ctx))
        by_side[side] = team.values
        plan.values_for_coverage.append(team.values)
        if mode == "apply":
            row = stats_repo.upsert_team_statistics(
                db,
                fixture_id=target.fixture_id,
                team_id=team_id,
                provider=provider,
                side=side,
                observation_id=observation_id,
                values=merged,
                normalizer_version=NORMALIZER_VERSION,
            )
            plan.rows.append(row.outcome)
        else:
            plan.rows.append(_predict_row(existing, side, merged))
    if degraded:
        plan.issues.append(qc.Issue(qc.DEGRADED_OBSERVATION, detail=degraded, **ctx))
    if "home" in by_side and "away" in by_side:
        issue = qc.possession_issue(by_side["home"], by_side["away"], **ctx)
        if issue:
            plan.issues.append(issue)
    return plan


# --- Run --------------------------------------------------------------------------------------


def _requests_so_far(provider: Any, fallback_calls: int) -> tuple[int, int]:
    """(peticiones HTTP, reintentos) del proveedor: el contador del adapter si existe."""
    requests = getattr(provider, "http_requests", None)
    retries = getattr(provider, "retries", 0)
    return (fallback_calls if requests is None else requests), int(retries or 0)


def _budget_allows(db: Session, options: BackfillOptions, now: datetime) -> tuple[bool, dict[str, int]]:
    midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    stats, live = runs.requests_since(db, midnight)
    state = {"stats_today": stats, "live_sync_today": live, "stats_budget": options.stats_daily_budget, "provider_cap": options.provider_daily_cap}
    ok = stats + REQUEST_HEADROOM <= options.stats_daily_budget and stats + live + REQUEST_HEADROOM <= options.provider_daily_cap
    return ok, state


async def run_season_backfill(
    db: Session,
    *,
    competition_id: int,
    season_id: int,
    provider: Any,
    options: BackfillOptions,
    clock: Callable[[], datetime] = _utcnow,
) -> BackfillOutcome:
    """Recorre la temporada por lotes. Las paradas globales (presupuesto, límite del proveedor,
    credenciales, error del proveedor) cierran el run de forma reanudable; una excepción
    inesperada cierra el run como failed y se relanza."""
    provider_code = provider.name
    season = db.get(Season, season_id)
    if season is None or season.competition_id != competition_id:
        raise SeasonMismatch(f"La temporada {season_id} no pertenece a la competición {competition_id}")
    after = None
    if options.resume:
        previous = runs.latest_resumable_run(db, season_id, options.mode)
        if previous is None:
            raise NothingToResume(f"No hay ningún run {options.mode} interrumpido de la temporada {season_id}")
        after = previous.cursor_fixture_id

    try:
        run_id = runs.create_run(db, trigger=options.trigger, mode=options.mode, scope="season", competition_id=competition_id, season_id=season_id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if runs.is_lock_conflict(exc):
            return BackfillOutcome(None, "locked")
        raise

    stop: str | None = None
    calls = 0
    try:
        targets, awarded, skipped = select_targets(db, season_id, provider_code, refresh=options.refresh, after_fixture_id=after)
        coverage = CoverageAccumulator(competition_id, season_id, excluded_awarded=awarded, skipped_existing=skipped)
        runs.add_counters(db, run_id, fixtures_targeted=len(targets))
        info = []
        if awarded:
            info.append(qc.Issue(qc.AWARDED_WITHOUT_STATISTICS, detail={"count": awarded}).as_dict())
        runs.append_checks(db, run_id, info)
        runs.set_coverage(db, run_id, coverage.as_dict())
        db.commit()

        for start in range(0, len(targets), options.batch_size):
            batch = targets[start : start + options.batch_size]
            ok, budget = _budget_allows(db, options, clock())
            if not ok:
                stop = STOP_BUDGET
                runs.append_detail(db, run_id, {"stopped": STOP_BUDGET, "budget": budget, "next_fixture_id": batch[0].fixture_id})
                db.commit()
                break
            db.commit()  # cierra la transacción de lectura: el HTTP va sin transacción abierta

            requested = {i: t for t in batch if (i := _requestable_id(t)) is not None}
            before_requests, before_retries = _requests_so_far(provider, calls)
            response: list[FixtureStatisticsData] = []
            provider_error: ProviderError | None = None
            if requested:
                calls += 1
                try:
                    response = await provider.get_fixture_statistics(sorted(requested))
                except ProviderError as exc:
                    provider_error = exc
            after_requests, after_retries = _requests_so_far(provider, calls)
            request_delta, retry_delta = after_requests - before_requests, after_retries - before_retries

            if provider_error is not None:
                stop = STOP_AUTH if isinstance(provider_error, ProviderAuthError) else (
                    STOP_RATE_LIMIT if isinstance(provider_error, ProviderRateLimitError) else STOP_PROVIDER)
                runs.add_counters(db, run_id, provider_requests=request_delta, provider_retries=retry_delta)
                runs.set_flags(db, run_id, auth_failed=stop == STOP_AUTH, rate_limited=stop == STOP_RATE_LIMIT)
                runs.append_detail(db, run_id, {
                    "stopped": stop, "requested": sorted(requested), "error": provider_error.__class__.__name__,
                    "provider_requests": request_delta,
                })
                db.commit()
                break

            plans, extra_issues = _process_batch(db, batch, requested, response, provider_code, options.mode, run_id, clock())
            _record_batch(db, run_id, batch, requested, response, plans, extra_issues, coverage, request_delta, retry_delta)
            db.commit()
    except Exception as exc:
        db.rollback()
        try:
            requests, retries = _requests_so_far(provider, calls)
            committed = runs.get_run(db, run_id)
            runs.add_counters(db, run_id, provider_requests=max(requests - committed.provider_requests, 0))
            runs.finish_run(db, run_id, status="failed", error_message=f"Error inesperado ({exc.__class__.__name__})")
            db.commit()
        except Exception as close_exc:  # noqa: BLE001
            db.rollback()
            logger.error("No se pudo cerrar el run de estadísticas #%s como failed (%s)", run_id, close_exc.__class__.__name__)
        raise

    run = runs.get_run(db, run_id)
    if stop == STOP_BUDGET:
        status, message = "aborted", "Presupuesto diario de peticiones agotado: reanudar con --resume"
    elif stop == STOP_RATE_LIMIT:
        status, message = "aborted", "Límite o cuota del proveedor: reanudar con --resume más tarde"
    elif stop == STOP_AUTH:
        status, message = "failed", "El proveedor rechazó las credenciales"
    elif stop == STOP_PROVIDER:
        status, message = "failed", "Error del proveedor en un lote (lote deshecho): reanudar con --resume"
    elif options.mode == "dry_run":
        status, message = "dry_run_completed", None
    else:
        problems = run.blocking_count or run.fixtures_missing_in_response
        status, message = ("completed_with_errors" if problems else "completed"), None
    runs.finish_run(db, run_id, status=status, error_message=message)
    db.commit()
    run = runs.get_run(db, run_id)
    counters = {c: getattr(run, c) for c in (
        "fixtures_targeted", "fixtures_attempted", "fixtures_available", "fixtures_partial", "fixtures_empty",
        "fixtures_blocked", "fixtures_missing_in_response", "provider_requests", "provider_retries",
        "observations_created", "observations_unchanged", "rows_created", "rows_updated", "rows_unchanged",
        "warning_count", "blocking_count", "rate_limited", "auth_failed",
    )}
    return BackfillOutcome(run_id, status, stop, counters)


def _process_batch(
    db: Session,
    batch: list[Target],
    requested: dict[int, Target],
    response: list[FixtureStatisticsData],
    provider: str,
    mode: str,
    run_id: int,
    observed_at: datetime,
) -> tuple[list[FixturePlan], list[qc.Issue]]:
    by_id: dict[int, list[FixtureStatisticsData]] = {}
    for item in response:
        by_id.setdefault(item.provider_fixture_id, []).append(item)
    extra = [
        qc.Issue(qc.UNEXPECTED_FIXTURE, provider_fixture_id=str(pid), detail={"teams": len(items[0].teams)})
        for pid, items in sorted(by_id.items())
        if pid not in requested
    ]
    plans: list[FixturePlan] = []
    for target in batch:
        pid = _requestable_id(target)
        ctx = {"provider_fixture_id": target.provider_fixture_id, "fixture_id": target.fixture_id}
        if pid is None:
            plans.append(FixturePlan(target, "blocked", [qc.Issue(qc.FIXTURE_WITHOUT_MAPPING, **ctx)]))
        elif pid not in by_id:
            plans.append(FixturePlan(target, "missing"))  # no se inventa un "empty"
        elif len(by_id[pid]) > 1:
            plans.append(FixturePlan(target, "blocked", [qc.Issue(qc.DUPLICATE_FIXTURE_IN_RESPONSE, detail={"times": len(by_id[pid])}, **ctx)]))
        else:
            plans.append(evaluate_fixture(db, target, by_id[pid][0], provider=provider, mode=mode, run_id=run_id, observed_at=observed_at))
    return plans, extra


def _record_batch(db, run_id, batch, requested, response, plans, extra_issues, coverage, request_delta, retry_delta) -> None:
    outcomes = {o: sum(1 for p in plans if p.outcome == o) for o in ("available", "partial", "empty", "blocked", "missing")}
    issues = [i for p in plans for i in p.issues] + extra_issues
    observation_counts = {o: sum(1 for p in plans if p.observation == o) for o in ("created", "changed", "unchanged")}
    row_counts = {o: sum(p.rows.count(o) for p in plans) for o in ("created", "updated", "unchanged")}
    for plan in plans:
        coverage.add_outcome(plan.outcome)
        if plan.outcome in ("available", "partial"):
            coverage.add_fixture_values(plan.values_for_coverage)
    summary = qc.summarize(issues)
    runs.add_counters(
        db,
        run_id,
        fixtures_attempted=len(plans),
        fixtures_available=outcomes["available"],
        fixtures_partial=outcomes["partial"],
        fixtures_empty=outcomes["empty"],
        fixtures_blocked=outcomes["blocked"],
        fixtures_missing_in_response=outcomes["missing"],
        provider_requests=request_delta,
        provider_retries=retry_delta,
        observations_created=observation_counts["created"] + observation_counts["changed"],
        observations_unchanged=observation_counts["unchanged"],
        rows_created=row_counts["created"],
        rows_updated=row_counts["updated"],
        rows_unchanged=row_counts["unchanged"],
        warning_count=sum(summary[qc.WARNING].values()),
        blocking_count=sum(summary[qc.BLOCKING].values()),
    )
    runs.append_detail(db, run_id, {
        "fixture_ids": [t.fixture_id for t in batch],
        "requested": sorted(requested),
        "returned": [r.provider_fixture_id for r in response],
        "provider_requests": request_delta,
        "provider_retries": retry_delta,
        **outcomes,
        "observations": observation_counts,
        "rows": row_counts,
        "issues": summary,
    })
    runs.append_checks(db, run_id, [i.as_dict() for i in issues if i.severity != qc.INFO])
    runs.set_cursor(db, run_id, batch[-1].fixture_id)
    runs.set_coverage(db, run_id, coverage.as_dict())
