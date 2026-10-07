"""Backfill de estadísticas de partido por temporada (M5.3).

Flujo por lote de hasta 20 partidos (una petición /fixtures?ids vía el adapter de M5.1):
1. Se seleccionan los partidos (valores planos) y se cierra la transacción de lectura.
2. Petición al proveedor SIN transacción de BD abierta.
3. Nueva transacción: PRECARGA del lote (partidos + mappings, mappings de equipos, observaciones
   vigentes y filas normalizadas, estas dos con FOR UPDATE en apply y en ese orden), PLAN puro
   por partido (plan_fixture: revalidación, identidad, quality checks, versionado y merge, sin
   SQL; el dry-run usa el mismo plan), escrituras EN BLOQUE (observaciones y filas) y un único
   UPDATE del run (contadores, detail, checks, cursor, cobertura). Commit: el lote es atómico.
   El número de sentencias por lote no depende del número de partidos (M5.4D).

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

Política temporal (por temporada, decidida antes de crear el run; nunca se mezcla en un run):
- HISTÓRICA: source='backfill', available_at = kickoff + 6 h, una disponibilidad SINTÉTICA
  (no es cuándo publicó el proveedor); observed_at = ahora real.
- OPERATIVA (is_current, o terminada hace como mucho OPERATIONAL_SEASON_GRACE_DAYS días):
  source='manual', available_at = observed_at (el mismo instante: una sola lectura del reloj por
  lote). Nunca se backdata una observación de una temporada en curso. Exige
  --allow-operational-season; sin él no se crea run ni se llama al proveedor.
  source='live' queda reservado para la ingestión automática (M5.6).
Deuda conocida (no resuelta aquí): en una temporada HISTÓRICA, una revisión del proveedor
descubierta con --refresh mucho después recibe también kickoff + 6 h.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.integrations.exceptions import ProviderAuthError, ProviderError, ProviderRateLimitError
from app.models import Fixture, FixtureProviderMapping, FixtureStatisticsObservation, FixtureTeamStatistics, Season
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
# Una temporada en curso, o que terminó hace como mucho estos días, es OPERATIVA
OPERATIONAL_SEASON_GRACE_DAYS = 7

HISTORICAL, OPERATIONAL = "historical", "operational"
REASON_IS_CURRENT, REASON_RECENTLY_ENDED, REASON_HISTORICAL = "is_current", "recently_ended", "historical"
POLICY_KICKOFF_PLUS_6H, POLICY_OBSERVED_AT = "kickoff_plus_6h", "observed_at"

# Motivos de parada global (run sin terminar el recorrido)
STOP_BUDGET = "budget_exhausted"
STOP_RATE_LIMIT = "rate_limited"
STOP_AUTH = "auth_failed"
STOP_PROVIDER = "provider_error"


class NothingToResume(Exception):
    """--resume sin ningún run interrumpido de esa temporada y modo."""


class SeasonMismatch(Exception):
    """La temporada no pertenece a la competición indicada."""


class UnknownSeasonEnd(Exception):
    """Temporada no current sin end_date: no se puede clasificar con seguridad (fail closed)."""


@dataclass(frozen=True)
class TemporalPolicy:
    classification: str  # historical | operational
    reason: str  # is_current | recently_ended | historical
    source: str  # backfill | manual
    availability_policy: str  # kickoff_plus_6h | observed_at

    def available_at(self, kickoff_at: datetime, observed_at: datetime) -> datetime:
        if self.availability_policy == POLICY_OBSERVED_AT:
            return observed_at  # el mismo objeto: available_at == observed_at exactamente
        return kickoff_at + BACKFILL_AVAILABILITY_DELAY

    def as_dict(self) -> dict[str, Any]:
        return {
            "season_temporal_classification": self.classification,
            "season_temporal_reason": self.reason,
            "observation_source": self.source,
            "availability_policy": self.availability_policy,
            "operational_grace_days": OPERATIONAL_SEASON_GRACE_DAYS,
        }


class OperationalSeasonNotAllowed(Exception):
    """Temporada operativa sin --allow-operational-season."""

    def __init__(self, policy: TemporalPolicy) -> None:
        self.policy = policy
        super().__init__(
            f"temporada OPERATIVA (criterio: {policy.reason}); su política es source={policy.source} y "
            f"available_at={policy.availability_policy}. Para procesarla hay que pasar --allow-operational-season"
        )


def season_temporal_policy(is_current: bool, end_date: date | None, today: date) -> TemporalPolicy:
    """Clasificación determinista de una TEMPORADA (no de un partido)."""
    if is_current:
        return TemporalPolicy(OPERATIONAL, REASON_IS_CURRENT, "manual", POLICY_OBSERVED_AT)
    if end_date is None:
        raise UnknownSeasonEnd("temporada no current sin end_date: no se puede clasificar (fail closed)")
    if end_date >= today - timedelta(days=OPERATIONAL_SEASON_GRACE_DAYS):
        return TemporalPolicy(OPERATIONAL, REASON_RECENTLY_ENDED, "manual", POLICY_OBSERVED_AT)
    return TemporalPolicy(HISTORICAL, REASON_HISTORICAL, "backfill", POLICY_KICKOFF_PLUS_6H)


def resolve_season_policy(db: Session, season_id: int, *, allow_operational: bool, today: date) -> TemporalPolicy:
    """Política de la temporada. Solo lee. Lanza OperationalSeasonNotAllowed si es operativa y
    no se autorizó, o UnknownSeasonEnd si no se puede clasificar. El flag autoriza procesar una
    temporada operativa; no cambia la política de una histórica."""
    season = db.get(Season, season_id)
    if season is None:
        raise SeasonMismatch(f"La temporada {season_id} no existe")
    policy = season_temporal_policy(season.is_current, season.end_date, today)
    if policy.classification == OPERATIONAL and not allow_operational:
        raise OperationalSeasonNotAllowed(policy)
    return policy


@dataclass
class BackfillOptions:
    mode: str = "dry_run"  # dry_run | apply
    trigger: str = "cli"
    refresh: bool = False  # vuelve a pedir partidos que ya tienen observación
    resume: bool = False  # continúa tras el cursor del último run interrumpido
    stats_daily_budget: int = DEFAULT_STATS_DAILY_BUDGET
    provider_daily_cap: int = DEFAULT_PROVIDER_DAILY_CAP
    batch_size: int = BATCH_SIZE
    allow_operational_season: bool = False  # autoriza una temporada operativa; no fuerza su política


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
class ObservationPlan:
    action: str  # created | changed | unchanged
    latest_id: int | None  # observación vigente antes del lote
    latest_last_observed_at: datetime | None
    row: dict[str, Any]  # campos de la observación nueva (si created/changed)


@dataclass
class RowPlan:
    team_id: int
    side: str
    values: TeamStatisticValues  # ya fusionados (merge no destructivo)
    action: str  # created | updated | relinked | unchanged
    existing_id: int | None


@dataclass
class FixturePlan:
    target: Target
    outcome: str  # available | partial | empty | blocked | missing
    issues: list[qc.Issue] = field(default_factory=list)
    observation: str | None = None  # created | changed | unchanged (real o previsto)
    rows: list[str] = field(default_factory=list)  # created | updated | unchanged por equipo (relinked cuenta como unchanged)
    values_for_coverage: list[TeamStatisticValues] = field(default_factory=list)
    observation_plan: ObservationPlan | None = None
    row_plans: list[RowPlan] = field(default_factory=list)


@dataclass
class BatchContext:
    """Precarga de un lote (ver _prefetch). Todo lo que plan_fixture necesita leer de la BD."""

    fixtures: dict[int, Any] = field(default_factory=dict)
    active_ids: dict[int, set[str]] = field(default_factory=dict)
    team_map: dict[str, int] = field(default_factory=dict)
    latest: dict[int, Any] = field(default_factory=dict)
    rows: dict[tuple[int, int], Any] = field(default_factory=dict)


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


def plan_fixture(
    target: Target,
    data: FixtureStatisticsData,
    ctx: BatchContext,
    *,
    provider: str,
    observed_at: datetime,
    policy: TemporalPolicy,
) -> FixturePlan:
    """Plan PURO de un partido (sin SQL): revalidación, identidad, checks, versionado de la
    observación y merge de las filas, con los datos precargados del lote. Lo comparten el
    dry-run (solo cuenta) y el apply (_apply_plans escribe el plan)."""
    pid = target.provider_fixture_id
    ictx = {"provider_fixture_id": pid, "fixture_id": target.fixture_id}
    plan = FixturePlan(target, "blocked")

    def block(code: str, **detail: Any) -> FixturePlan:
        plan.issues.append(qc.Issue(code, detail=detail, **ictx))
        return plan

    fixture = ctx.fixtures.get(target.fixture_id)
    if fixture is None:
        return block(qc.FIXTURE_NOT_FOUND)
    current_ids = ctx.active_ids.get(target.fixture_id, set())
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
            plan.issues.append(qc.Issue(qc.TEAM_WITHOUT_ID, detail={"entry": index}, **ictx))
            continue
        team_id = ctx.team_map.get(canonical_external_id(tid))
        if team_id is None:
            plan.issues.append(qc.Issue(qc.TEAM_WITHOUT_MAPPING, provider_team_id=tid, **ictx))
        elif team_id not in sides:
            plan.issues.append(qc.Issue(qc.TEAM_NOT_IN_FIXTURE, provider_team_id=tid, detail={"team_id": team_id}, **ictx))
        elif team_id in seen_teams:
            plan.issues.append(qc.Issue(qc.DUPLICATE_TEAM, provider_team_id=tid, detail={"team_id": team_id}, **ictx))
        else:
            seen_teams.add(team_id)
            resolved[index] = team_id
        plan.issues.extend(qc.team_value_issues(team, **ictx))

    availability = ("empty", "partial", "available")[len(with_stats)]
    blocked = any(i.severity == qc.BLOCKING for i in plan.issues)

    # Observación raw (también si el partido queda bloqueado por equipos o valores): versionado
    # por hash contra la vigente, igual que record_observation
    latest = ctx.latest.get(target.fixture_id)
    payload = data.raw_statistics if data.raw_statistics is not None else []
    digest = stats_repo.payload_hash(payload)
    action = "created" if latest is None else ("unchanged" if latest.payload_hash == digest else "changed")
    plan.observation = action
    plan.observation_plan = ObservationPlan(
        action=action,
        latest_id=latest.id if latest is not None else None,
        latest_last_observed_at=latest.last_observed_at if latest is not None else None,
        row={
            "fixture_id": target.fixture_id,
            "provider": provider,
            "provider_fixture_id": pid,
            "source": policy.source,
            "fixture_status_at_fetch": data.provider_status,
            "availability": availability,
            "teams_returned": len(with_stats),
            "payload": payload,
            "observed_at": observed_at,
            "available_at": policy.available_at(fixture.kickoff_at, observed_at),
        },
    )
    if blocked:
        return plan

    plan.outcome = availability
    if availability == "partial":
        plan.issues.append(qc.Issue(qc.PARTIAL_STATISTICS, **ictx))
    elif availability == "empty":
        plan.issues.append(qc.Issue(qc.FINAL_FIXTURE_EMPTY, detail={"status": fixture.status_short}, **ictx))

    existing_rows = {team_id: row for (fixture_id, team_id), row in ctx.rows.items() if fixture_id == target.fixture_id}
    degraded: dict[str, Any] = {}
    present_team_ids = {resolved[i] for i, t in enumerate(data.teams) if t.has_statistics and i in resolved}
    missing_teams = sorted(set(existing_rows) - present_team_ids)
    if missing_teams:
        degraded["teams_without_statistics_now"] = missing_teams

    # Observación a la que apuntarán las filas: la vigente si no cambia; si no, la nueva (None)
    target_observation = latest.id if action == "unchanged" else None
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
        plan.issues.extend(qc.consistency_issues(team.values, team.provider_team_id, **ictx))
        by_side[side] = team.values
        plan.values_for_coverage.append(team.values)
        row_action = stats_repo.classify_team_row(existing, side, merged, target_observation, NORMALIZER_VERSION)
        plan.row_plans.append(RowPlan(team_id, side, merged, row_action, existing.id if existing is not None else None))
        plan.rows.append("unchanged" if row_action == "relinked" else row_action)
    if degraded:
        plan.issues.append(qc.Issue(qc.DEGRADED_OBSERVATION, detail=degraded, **ictx))
    if "home" in by_side and "away" in by_side:
        issue = qc.possession_issue(by_side["home"], by_side["away"], **ictx)
        if issue:
            plan.issues.append(issue)
    return plan


def _prefetch(db: Session, fixture_ids: list[int], team_external_ids: set[str], provider: str, *, lock: bool) -> BatchContext:
    """4 consultas por lote: partidos + mappings, mappings de equipos, observaciones vigentes y
    filas (las dos últimas FOR UPDATE en apply, en ese orden y por clave ascendente)."""
    fixtures, active_ids = stats_repo.prefetch_fixtures(db, fixture_ids, provider)
    return BatchContext(
        fixtures=fixtures,
        active_ids=active_ids,
        team_map=stats_repo.prefetch_team_mappings(db, team_external_ids, provider),
        latest=stats_repo.prefetch_latest_observations(db, fixture_ids, provider, for_update=lock),
        rows=stats_repo.prefetch_team_statistics(db, fixture_ids, provider, for_update=lock),
    )


def _apply_plans(db: Session, plans: list[FixturePlan], *, run_id: int, observed_at: datetime) -> None:
    """Escribe los planes del lote: observaciones (≤ 3 sentencias) y filas (≤ 3 sentencias)."""
    with_observation = [p for p in plans if p.observation_plan is not None]
    for plan in with_observation:
        op = plan.observation_plan
        if op.action == "changed" and observed_at < op.latest_last_observed_at:
            raise stats_repo.out_of_order(op.latest_last_observed_at, observed_at, plan.target.fixture_id)
    new_ids = stats_repo.write_observations(
        db,
        unchanged_ids=[p.observation_plan.latest_id for p in with_observation if p.observation_plan.action == "unchanged"],
        superseded_ids=[p.observation_plan.latest_id for p in with_observation if p.observation_plan.action == "changed"],
        new_rows=[{**p.observation_plan.row, "run_id": run_id} for p in with_observation if p.observation_plan.action in ("created", "changed")],
        observed_at=observed_at,
    )
    created, updated, relinked = [], [], []
    for plan in plans:
        op = plan.observation_plan
        for rp in plan.row_plans:
            observation_id = op.latest_id if op.action == "unchanged" else new_ids[plan.target.fixture_id]
            if rp.action == "created":
                created.append({
                    "fixture_id": plan.target.fixture_id, "team_id": rp.team_id, "provider": op.row["provider"],
                    "observation_id": observation_id, "normalizer_version": NORMALIZER_VERSION, "side": rp.side,
                    **stats_repo.column_values(rp.values),
                })
            elif rp.action == "updated":
                updated.append({
                    "id": rp.existing_id, "observation_id": observation_id, "normalizer_version": NORMALIZER_VERSION,
                    "side": rp.side, **stats_repo.column_values(rp.values),
                })
            elif rp.action == "relinked":
                relinked.append({"id": rp.existing_id, "observation_id": observation_id, "normalizer_version": NORMALIZER_VERSION})
    stats_repo.write_team_statistics(db, created=created, updated=updated, relinked=relinked)


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
    # Antes de crear el run y de cualquier petición: una temporada operativa sin autorizar se rechaza
    policy = resolve_season_policy(db, season_id, allow_operational=options.allow_operational_season, today=clock().date())
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

    calls = [0]  # llamadas lógicas al proveedor (para cerrar el run si algo falla a mitad)
    try:
        targets, awarded, skipped = select_targets(db, season_id, provider_code, refresh=options.refresh, after_fixture_id=after)
        coverage = CoverageAccumulator(competition_id, season_id, excluded_awarded=awarded, skipped_existing=skipped)
        runs.add_counters(db, run_id, fixtures_targeted=len(targets))
        runs.append_detail(db, run_id, {"kind": "temporal_policy", **policy.as_dict()})
        info = []
        if awarded:
            info.append(qc.Issue(qc.AWARDED_WITHOUT_STATISTICS, detail={"count": awarded}).as_dict())
        runs.append_checks(db, run_id, info)
        runs.set_coverage(db, run_id, coverage.as_dict())
        db.commit()
        stop = await run_batches(db, run_id, targets, provider, options, policy, coverage, clock, calls)
    except Exception as exc:
        fail_run(db, run_id, provider, calls[0], exc)
        raise
    return close_run(db, run_id, stop, options.mode)


async def run_batches(
    db: Session,
    run_id: int,
    targets: list[Target],
    provider: Any,
    options: Any,
    policy: TemporalPolicy,
    coverage: Any,
    clock: Callable[[], datetime],
    calls: list[int],
) -> str | None:
    """Recorre los objetivos por lotes (presupuesto antes de cada lote, HTTP sin transacción,
    lote atómico). Lo comparten el backfill por temporada y el reconciliador live (M5.6B).
    `options` aporta mode, batch_size y presupuesto; `calls` acumula las llamadas lógicas.
    Devuelve el motivo de parada global, o None si recorrió todos los objetivos."""
    provider_code = provider.name
    for start in range(0, len(targets), options.batch_size):
        batch = targets[start : start + options.batch_size]
        ok, budget = _budget_allows(db, options, clock())
        if not ok:
            runs.append_detail(db, run_id, {"stopped": STOP_BUDGET, "budget": budget, "next_fixture_id": batch[0].fixture_id})
            db.commit()
            return STOP_BUDGET
        db.commit()  # cierra la transacción de lectura: el HTTP va sin transacción abierta

        requested = {i: t for t in batch if (i := _requestable_id(t)) is not None}
        before_requests, before_retries = _requests_so_far(provider, calls[0])
        response: list[FixtureStatisticsData] = []
        provider_error: ProviderError | None = None
        if requested:
            calls[0] += 1
            try:
                response = await provider.get_fixture_statistics(sorted(requested))
            except ProviderError as exc:
                provider_error = exc
        after_requests, after_retries = _requests_so_far(provider, calls[0])
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
            return stop

        plans, extra_issues = _process_batch(db, batch, requested, response, provider_code, options.mode, run_id, clock(), policy)
        _record_batch(db, run_id, batch, requested, response, plans, extra_issues, coverage, request_delta, retry_delta)
        db.commit()
    return None


def fail_run(db: Session, run_id: int, provider: Any, calls: int, exc: Exception) -> None:
    """Cierra como failed un run cortado por una excepción inesperada (el lote en curso ya se
    deshizo); el llamador relanza la excepción."""
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


BACKFILL_STOP_MESSAGES = {
    STOP_BUDGET: "Presupuesto diario de peticiones agotado: reanudar con --resume",
    STOP_RATE_LIMIT: "Límite o cuota del proveedor: reanudar con --resume más tarde",
    STOP_AUTH: "El proveedor rechazó las credenciales",
    STOP_PROVIDER: "Error del proveedor en un lote (lote deshecho): reanudar con --resume",
}


def close_run(db: Session, run_id: int, stop: str | None, mode: str, messages: dict[str, str] = BACKFILL_STOP_MESSAGES) -> BackfillOutcome:
    """Estado final del run según el motivo de parada y lo registrado (contrato de M5.3)."""
    run = runs.get_run(db, run_id)
    if stop in (STOP_BUDGET, STOP_RATE_LIMIT):
        status, message = "aborted", messages[stop]
    elif stop in (STOP_AUTH, STOP_PROVIDER):
        status, message = "failed", messages[stop]
    elif mode == "dry_run":
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
    policy: TemporalPolicy,
) -> tuple[list[FixturePlan], list[qc.Issue]]:
    by_id: dict[int, list[FixtureStatisticsData]] = {}
    for item in response:
        by_id.setdefault(item.provider_fixture_id, []).append(item)
    extra = [
        qc.Issue(qc.UNEXPECTED_FIXTURE, provider_fixture_id=str(pid), detail={"teams": len(items[0].teams)})
        for pid, items in sorted(by_id.items())
        if pid not in requested
    ]
    evaluable = [(t, by_id[pid][0]) for t in batch if (pid := _requestable_id(t)) is not None and len(by_id.get(pid, ())) == 1]
    ctx = BatchContext()
    if evaluable:
        team_ids = {canonical_external_id(team.provider_team_id) for _, data in evaluable for team in data.teams if team.provider_team_id is not None}
        ctx = _prefetch(db, [t.fixture_id for t, _ in evaluable], team_ids, provider, lock=mode == "apply")
    plans: list[FixturePlan] = []
    for target in batch:
        pid = _requestable_id(target)
        ictx = {"provider_fixture_id": target.provider_fixture_id, "fixture_id": target.fixture_id}
        if pid is None:
            plans.append(FixturePlan(target, "blocked", [qc.Issue(qc.FIXTURE_WITHOUT_MAPPING, **ictx)]))
        elif pid not in by_id:
            plans.append(FixturePlan(target, "missing"))  # no se inventa un "empty"
        elif len(by_id[pid]) > 1:
            plans.append(FixturePlan(target, "blocked", [qc.Issue(qc.DUPLICATE_FIXTURE_IN_RESPONSE, detail={"times": len(by_id[pid])}, **ictx)]))
        else:
            plans.append(plan_fixture(target, by_id[pid][0], ctx, provider=provider, observed_at=observed_at, policy=policy))
    if mode == "apply":
        _apply_plans(db, plans, run_id=run_id, observed_at=observed_at)
    return plans, extra


def _record_batch(db, run_id, batch, requested, response, plans, extra_issues, coverage, request_delta, retry_delta) -> None:
    outcomes = {o: sum(1 for p in plans if p.outcome == o) for o in ("available", "partial", "empty", "blocked", "missing")}
    issues = [i for p in plans for i in p.issues] + extra_issues
    observation_counts = {o: sum(1 for p in plans if p.observation == o) for o in ("created", "changed", "unchanged")}
    row_counts = {o: sum(p.rows.count(o) for p in plans) for o in ("created", "updated", "unchanged")}
    for plan in plans:
        coverage.add_plan(plan)
    summary = qc.summarize(issues)
    increments = dict(
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
    detail = {
        "fixture_ids": [t.fixture_id for t in batch],
        "requested": sorted(requested),
        "returned": [r.provider_fixture_id for r in response],
        "provider_requests": request_delta,
        "provider_retries": retry_delta,
        **outcomes,
        "observations": observation_counts,
        "rows": row_counts,
        "issues": summary,
    }
    runs.record_batch(
        db,
        run_id,
        increments=increments,
        detail=detail,
        checks=[i.as_dict() for i in issues if i.severity != qc.INFO],
        cursor=batch[-1].fixture_id,
        coverage=coverage.as_dict(),
    )
