"""Reconciliador live de estadísticas de partido (M5.6B).

Pide estadísticas de los partidos FINALES de temporadas OPERATIVAS según un calendario fijo de
checkpoints, con el mismo pipeline por lotes que el backfill (plan_fixture, precarga, escrituras
en bloque y un UPDATE del run por lote: statistics_service.run_batches). No hay un segundo
pipeline: aquí solo se decide QUÉ partidos pedir y en qué orden.

Calendario (sin finalized_at: no se inventa ni se usa fixtures.updated_at)
- E = kickoff_at + 2 h 30 (fin estimado y conservador; incluye prórroga y penaltis).
- Checkpoints: T1 = E + 2 h, T2 = E + 6 h, T3 = E + 24 h, T4 = E + 48 h.
- Un partido es objetivo si su estado en la BD es FT/AET/PEN y ha vencido algún checkpoint de su
  calendario que todavía no se ha servido (último intento anterior a ese checkpoint).
- Calendario según la observación vigente (latest):
  sin observación: T1..T4 (primera adquisición: sigue siendo objetivo tras T4, una vez);
  available normalizado: T1 + T3 (una reconciliación) y después freeze;
  partial, empty y quality-blocked: T1..T4 y después freeze;
  empty en modo baja cobertura: T1 + T3.
- Checkpoints vencidos a la vez se sirven con UNA sola petición (solo cuenta el último vencido).
- Último intento = max(last_observed_at de la observación vigente, inicio de la última pasada
  apply que incluyó el partido en un lote, según statistics_runs.details). Lo segundo cubre los
  intentos que no dejan observación (ausente en la respuesta, bloqueo de identidad). Sin
  contadores nuevos ni migración.

Selección
- Solo temporadas OPERATIVAS (season_temporal_policy) y no dormant (polling_eligibility). Una
  temporada histórica nunca entra (sus observaciones no se tocan); una sin end_date clasificable
  queda fuera (fail closed). Todo queda anotado en details (live_selection).
- Ventana de primera adquisición (FIRST_ACQUISITION_LOOKBACK): los finales sin observación más
  antiguos no los toma el reconciliador automático; son la puesta al día explícita (CLI de
  backfill con --allow-operational-season, source=manual). Se cuentan en details.
- Prioridad: 1 primera adquisición; 2 reintentos (empty, partial, quality-blocked);
  3 reconciliación de available. Dentro de cada una: checkpoint vencido más antiguo y fixture_id.

Política temporal: una sola por run, LIVE: source='live', available_at = observed_at. Nunca se
backdata; nunca se escribe en temporadas históricas.

Modo baja cobertura (solo planificación: no imputa, no entra en features)
- Entra si las primeras adquisiciones de la temporada de los últimos 30 días son >= 20 y el
  >= 90 % fueron empty; o, con menos de 20, si la temporada anterior de la competición tiene
  >= 90 % de sus elegibles empty.
- Sale si hay >= 3 available entre las últimas 20 primeras adquisiciones.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Fixture, FixtureProviderMapping, FixtureStatisticsObservation, FixtureTeamStatistics, Season
from app.repositories import statistics_run_repository as runs
from app.services import statistics_service as svc
from app.services.polling_eligibility import polling_eligibility
from app.services.statistics_coverage import CoverageAccumulator

logger = logging.getLogger(__name__)

FIXTURE_END_ESTIMATE = timedelta(hours=2, minutes=30)
CHECKPOINTS = {"T1": timedelta(hours=2), "T2": timedelta(hours=6), "T3": timedelta(hours=24), "T4": timedelta(hours=48)}
FULL_SCHEDULE = ("T1", "T2", "T3", "T4")
AVAILABLE_SCHEDULE = ("T1", "T3")
LOW_COVERAGE_EMPTY_SCHEDULE = ("T1", "T3")
FIRST_ACQUISITION_LOOKBACK = timedelta(days=7)

LOW_COVERAGE_WINDOW = timedelta(days=30)
LOW_COVERAGE_MIN_ATTEMPTS = 20
LOW_COVERAGE_EMPTY_RATIO = 0.9
LOW_COVERAGE_EXIT_AVAILABLE = 3

# Estado de un partido según su observación vigente
STATE_NONE, STATE_AVAILABLE, STATE_PARTIAL, STATE_EMPTY, STATE_BLOCKED = "none", "available", "partial", "empty", "blocked"
PRIORITY = {STATE_NONE: 1, STATE_EMPTY: 2, STATE_PARTIAL: 2, STATE_BLOCKED: 2, STATE_AVAILABLE: 3}
PRIORITY_NAMES = {1: "first_acquisition", 2: "retry", 3: "reconciliation"}

LIVE_SOURCE, REASON_LIVE = "live", "live_reconcile"
LIVE_POLICY = svc.TemporalPolicy(svc.OPERATIONAL, REASON_LIVE, LIVE_SOURCE, svc.POLICY_OBSERVED_AT)

LIVE_STOP_MESSAGES = {
    svc.STOP_BUDGET: "Presupuesto diario de peticiones agotado: la siguiente pasada recalcula los partidos debidos",
    svc.STOP_RATE_LIMIT: "Límite o cuota del proveedor: la siguiente pasada recalcula los partidos debidos",
    svc.STOP_AUTH: "El proveedor rechazó las credenciales",
    svc.STOP_PROVIDER: "Error del proveedor en un lote (lote deshecho): la siguiente pasada recalcula los partidos debidos",
}


# --- Motor de checkpoints (puro) ----------------------------------------------------------------


def fixture_end_estimate(kickoff_at: datetime) -> datetime:
    return kickoff_at + FIXTURE_END_ESTIMATE


def first_fetch_at(kickoff_at: datetime) -> datetime:
    """T1: kickoff + 4 h 30."""
    return fixture_end_estimate(kickoff_at) + CHECKPOINTS["T1"]


def schedule_for(state: str, low_coverage: bool) -> tuple[str, ...]:
    if state == STATE_AVAILABLE:
        return AVAILABLE_SCHEDULE
    if state == STATE_EMPTY and low_coverage:
        return LOW_COVERAGE_EMPTY_SCHEDULE
    return FULL_SCHEDULE


def due_checkpoint(kickoff_at: datetime, state: str, last_attempt_at: datetime | None, now: datetime, *, low_coverage: bool = False) -> tuple[str, datetime] | None:
    """(checkpoint, instante) que el partido tiene pendiente ahora, o None. Solo cuenta el último
    checkpoint vencido de su calendario (los anteriores se coalescen en la misma petición); está
    servido si el último intento es igual o posterior a él. Pasado el último checkpoint del
    calendario y servido, el partido queda congelado."""
    end = fixture_end_estimate(kickoff_at)
    passed = [(c, end + CHECKPOINTS[c]) for c in schedule_for(state, low_coverage) if end + CHECKPOINTS[c] <= now]
    if not passed:
        return None
    checkpoint, at = passed[-1]
    if last_attempt_at is not None and last_attempt_at >= at:
        return None
    return checkpoint, at


def fixture_state(availability: str | None, latest_id: int | None, rows_linked_to_latest: int) -> str:
    """Estado de reconciliación según la observación vigente y las filas enlazadas a ella:
    available/partial válidos tienen filas de la observación vigente; una observación no vacía
    sin ellas está bloqueada por calidad (raw guardada, normalización rechazada)."""
    if latest_id is None:
        return STATE_NONE
    if availability == "empty":
        return STATE_EMPTY
    if availability == "available" and rows_linked_to_latest >= 2:
        return STATE_AVAILABLE
    if availability == "partial" and rows_linked_to_latest >= 1:
        return STATE_PARTIAL
    return STATE_BLOCKED


def low_coverage_mode(recent_first_acquisitions: list[str], previous_empty_ratio: float | None) -> tuple[bool, str]:
    """recent_first_acquisitions: disponibilidad de las primeras adquisiciones de la temporada en
    la ventana, de la más reciente a la más antigua."""
    last = recent_first_acquisitions[:LOW_COVERAGE_MIN_ATTEMPTS]
    if sum(1 for a in last if a == "available") >= LOW_COVERAGE_EXIT_AVAILABLE:
        return False, "recent_available"
    if len(recent_first_acquisitions) >= LOW_COVERAGE_MIN_ATTEMPTS:
        ratio = recent_first_acquisitions.count("empty") / len(recent_first_acquisitions)
        return (True, "recent_empty") if ratio >= LOW_COVERAGE_EMPTY_RATIO else (False, "recent_normal")
    if previous_empty_ratio is not None and previous_empty_ratio >= LOW_COVERAGE_EMPTY_RATIO:
        return True, "previous_season_empty"
    return False, "normal"


# --- Selección ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveTarget:
    fixture_id: int
    competition_id: int
    season_id: int
    provider_fixture_id: str | None
    state: str
    checkpoint: str
    due_at: datetime
    priority: int

    def as_target(self) -> svc.Target:
        return svc.Target(self.fixture_id, self.provider_fixture_id)


@dataclass
class LiveSelection:
    targets: list[LiveTarget] = field(default_factory=list)
    seasons: list[dict[str, Any]] = field(default_factory=list)  # incluidas, con su modo
    excluded_seasons: list[dict[str, Any]] = field(default_factory=list)
    backlog_outside_lookback: int = 0

    def as_detail(self, now: datetime) -> dict[str, Any]:
        by_priority = {name: 0 for name in PRIORITY_NAMES.values()}
        by_checkpoint = dict.fromkeys(CHECKPOINTS, 0)
        by_state: dict[str, int] = {}
        for t in self.targets:
            by_priority[PRIORITY_NAMES[t.priority]] += 1
            by_checkpoint[t.checkpoint] += 1
            by_state[t.state] = by_state.get(t.state, 0) + 1
        return {
            "kind": "live_selection",
            "now": now.isoformat(),
            "seasons": self.seasons,
            "excluded_seasons": self.excluded_seasons,
            "due": len(self.targets),
            "by_priority": by_priority,
            "by_checkpoint": by_checkpoint,
            "by_state": by_state,
            "backlog_outside_lookback": self.backlog_outside_lookback,
            "targets": [[t.fixture_id, t.state, t.checkpoint] for t in self.targets],
        }


def operational_seasons(db: Session, today) -> tuple[list[Any], list[dict[str, Any]]]:
    """Temporadas operativas y no dormant (incluidas) y las descartadas con su motivo. Solo mira
    temporadas que podrían ser operativas: current, sin end_date o terminadas hace poco."""
    grace = today - timedelta(days=svc.OPERATIONAL_SEASON_GRACE_DAYS)
    candidates = db.execute(
        select(Season.id, Season.competition_id, Season.year, Season.is_current, Season.end_date)
        .where((Season.is_current.is_(True)) | (Season.end_date.is_(None)) | (Season.end_date >= grace))
        .order_by(Season.id)
    ).all()
    included, excluded = [], []
    for season in candidates:
        try:
            policy = svc.season_temporal_policy(season.is_current, season.end_date, today)
        except svc.UnknownSeasonEnd:
            excluded.append({"season_id": season.id, "reason": "unknown_season_end"})
            continue
        if policy.classification != svc.OPERATIONAL:
            excluded.append({"season_id": season.id, "reason": "historical"})
            continue
        eligibility = polling_eligibility(db, season.id, today)
        if not eligibility.eligible:
            excluded.append({"season_id": season.id, "reason": "dormant"})
            continue
        included.append(season)
    return included, excluded


def _season_low_coverage(db: Session, season: Any, provider: str, now: datetime) -> tuple[bool, str]:
    first = (
        select(
            FixtureStatisticsObservation.availability,
            FixtureStatisticsObservation.observed_at,
            func.row_number().over(
                partition_by=FixtureStatisticsObservation.fixture_id,
                order_by=(FixtureStatisticsObservation.observed_at, FixtureStatisticsObservation.id),
            ).label("rn"),
        )
        .join(Fixture, Fixture.id == FixtureStatisticsObservation.fixture_id)
        .where(Fixture.season_id == season.id, FixtureStatisticsObservation.provider == provider)
        .subquery()
    )
    recent = list(db.scalars(
        select(first.c.availability)
        .where(first.c.rn == 1, first.c.observed_at >= now - LOW_COVERAGE_WINDOW)
        .order_by(first.c.observed_at.desc())
    ))
    previous_ratio = None
    if len(recent) < LOW_COVERAGE_MIN_ATTEMPTS:
        previous = db.scalar(
            select(Season.id)
            .where(Season.competition_id == season.competition_id, Season.year < season.year)
            .order_by(Season.year.desc())
            .limit(1)
        )
        if previous is not None:
            eligible = db.scalar(
                select(func.count()).select_from(Fixture)
                .where(Fixture.season_id == previous, Fixture.status_short.in_(sorted(svc.FETCH_STATUSES)))
            )
            if eligible:
                empty = db.scalar(
                    select(func.count()).select_from(FixtureStatisticsObservation)
                    .join(Fixture, Fixture.id == FixtureStatisticsObservation.fixture_id)
                    .where(
                        Fixture.season_id == previous,
                        Fixture.status_short.in_(sorted(svc.FETCH_STATUSES)),
                        FixtureStatisticsObservation.provider == provider,
                        FixtureStatisticsObservation.is_latest.is_(True),
                        FixtureStatisticsObservation.availability == "empty",
                    )
                )
                previous_ratio = empty / eligible
    return low_coverage_mode(recent, previous_ratio)


_LAST_ATTEMPTS_SQL = text("""
    SELECT x.fid::int AS fixture_id, max(coalesce(sel.at, r.started_at)) AS attempted_at
    FROM statistics_runs r
    LEFT JOIN LATERAL (
        SELECT (d2->>'now')::timestamptz AS at FROM jsonb_array_elements(r.details) d2
        WHERE d2->>'kind' = 'live_selection' LIMIT 1
    ) sel ON true
    CROSS JOIN LATERAL jsonb_array_elements(r.details) d
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(d->'fixture_ids') = 'array' THEN d->'fixture_ids' ELSE '[]'::jsonb END
    ) x(fid)
    WHERE r.mode = 'apply' AND r.started_at >= :since
    GROUP BY 1
""")


def last_attempts(db: Session, fixture_ids: set[int], since: datetime) -> dict[int, datetime]:
    """Último intento (apply) por partido según los lotes registrados en statistics_runs.details.
    Instante: el `now` de la selección en las pasadas live; started_at en los demás runs."""
    if not fixture_ids:
        return {}
    rows = db.execute(_LAST_ATTEMPTS_SQL, {"since": since}).all()
    return {fid: at for fid, at in rows if fid in fixture_ids}


def select_live_targets(db: Session, provider: str, now: datetime, *, lookback: timedelta = FIRST_ACQUISITION_LOOKBACK) -> LiveSelection:
    """Partidos debidos ahora, ordenados por prioridad. Solo lee."""
    selection = LiveSelection()
    seasons, selection.excluded_seasons = operational_seasons(db, now.date())
    if not seasons:
        return selection
    by_id = {s.id: s for s in seasons}
    window = max(lookback, CHECKPOINTS["T4"])
    oldest_kickoff = now - window - FIXTURE_END_ESTIMATE - CHECKPOINTS["T1"]
    latest_kickoff = now - FIXTURE_END_ESTIMATE - CHECKPOINTS["T1"]
    fixtures = db.execute(
        select(Fixture.id, Fixture.season_id, Fixture.kickoff_at)
        .where(
            Fixture.season_id.in_(list(by_id)),
            Fixture.status_short.in_(sorted(svc.FETCH_STATUSES)),
            Fixture.kickoff_at <= latest_kickoff,
        )
        .order_by(Fixture.id)
    ).all()
    ids = [f.id for f in fixtures]
    mapping: dict[int, str] = {}
    for fixture_id, external_id in db.execute(
        select(FixtureProviderMapping.fixture_id, FixtureProviderMapping.external_id)
        .where(FixtureProviderMapping.fixture_id.in_(ids), FixtureProviderMapping.provider == provider, FixtureProviderMapping.is_active.is_(True))
        .order_by(FixtureProviderMapping.fixture_id, FixtureProviderMapping.id)
    ):
        mapping.setdefault(fixture_id, external_id)
    latest = {
        r.fixture_id: r
        for r in db.execute(
            select(FixtureStatisticsObservation.fixture_id, FixtureStatisticsObservation.id, FixtureStatisticsObservation.availability,
                   FixtureStatisticsObservation.last_observed_at)
            .where(FixtureStatisticsObservation.fixture_id.in_(ids), FixtureStatisticsObservation.provider == provider,
                   FixtureStatisticsObservation.is_latest.is_(True))
        )
    }
    linked = dict(db.execute(
        select(FixtureTeamStatistics.observation_id, func.count())
        .where(FixtureTeamStatistics.observation_id.in_([r.id for r in latest.values()]), FixtureTeamStatistics.provider == provider)
        .group_by(FixtureTeamStatistics.observation_id)
    ).all()) if latest else {}

    in_window = [f for f in fixtures if f.kickoff_at >= oldest_kickoff]
    selection.backlog_outside_lookback = sum(1 for f in fixtures if f.kickoff_at < oldest_kickoff and f.id not in latest)
    attempts = last_attempts(db, {f.id for f in in_window}, oldest_kickoff)
    low_cov = {}
    for season in seasons:
        low, reason = _season_low_coverage(db, season, provider, now)
        low_cov[season.id] = low
        selection.seasons.append({"competition_id": season.competition_id, "season_id": season.id, "low_coverage": low, "low_coverage_reason": reason})

    targets = []
    for f in in_window:
        obs = latest.get(f.id)
        state = fixture_state(obs.availability if obs else None, obs.id if obs else None, linked.get(obs.id, 0) if obs else 0)
        if state == STATE_NONE and f.kickoff_at < now - lookback - FIXTURE_END_ESTIMATE - CHECKPOINTS["T1"]:
            selection.backlog_outside_lookback += 1  # fuera de la ventana de primera adquisición
            continue
        tried = [t for t in (obs.last_observed_at if obs else None, attempts.get(f.id)) if t is not None]
        due = due_checkpoint(f.kickoff_at, state, max(tried) if tried else None, now, low_coverage=low_cov[f.season_id])
        if due is None:
            continue
        season = by_id[f.season_id]
        targets.append(LiveTarget(f.id, season.competition_id, f.season_id, mapping.get(f.id), state, due[0], due[1], PRIORITY[state]))
    targets.sort(key=lambda t: (t.priority, t.due_at, t.fixture_id))
    selection.targets = targets
    return selection


# --- Cobertura multi-temporada --------------------------------------------------------------------


class LiveCoverage:
    """Cobertura de un run live: resumen global (mismas claves que la de una temporada) y la
    misma cobertura por competición/temporada en `by_season`."""

    def __init__(self, targets: list[LiveTarget]) -> None:
        self.total = CoverageAccumulator()
        self.season_of = {t.fixture_id: (t.competition_id, t.season_id) for t in targets}
        self.by_season: dict[tuple[int, int], CoverageAccumulator] = {}
        for key in sorted(set(self.season_of.values())):
            self.by_season[key] = CoverageAccumulator(*key)

    def add_plan(self, plan: Any) -> None:
        self.total.add_plan(plan)
        self.by_season[self.season_of[plan.target.fixture_id]].add_plan(plan)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.total.as_dict(),
            "scope": "live",
            "by_season": {f"{c}:{s}": acc.as_dict() for (c, s), acc in self.by_season.items()},
        }


# --- Run ------------------------------------------------------------------------------------------


@dataclass
class ReconcileOptions:
    mode: str = "dry_run"  # dry_run | apply
    trigger: str = "cli"  # cli | scheduler
    stats_daily_budget: int = svc.DEFAULT_STATS_DAILY_BUDGET
    provider_daily_cap: int = svc.DEFAULT_PROVIDER_DAILY_CAP
    batch_size: int = svc.BATCH_SIZE
    first_acquisition_lookback: timedelta = FIRST_ACQUISITION_LOOKBACK


async def run_live_reconcile(
    db: Session,
    *,
    provider: Any,
    options: ReconcileOptions,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> svc.BackfillOutcome:
    """Una pasada del reconciliador. El lock es el de siempre (un run 'running' de estadísticas):
    con otro run en curso devuelve status 'locked' sin seleccionar ni pedir nada."""
    if options.batch_size > svc.BATCH_SIZE:
        raise ValueError(f"batch_size máximo {svc.BATCH_SIZE}")
    try:
        run_id = runs.create_run(db, trigger=options.trigger, mode=options.mode, scope="live")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if runs.is_lock_conflict(exc):
            return svc.BackfillOutcome(None, "locked")
        raise

    calls = [0]
    try:
        now = clock()
        selection = select_live_targets(db, provider.name, now, lookback=options.first_acquisition_lookback)
        allowed = {s["season_id"] for s in selection.seasons}
        if any(t.season_id not in allowed for t in selection.targets):  # defensa: nunca una temporada no operativa
            raise RuntimeError("objetivo live fuera de las temporadas operativas")
        coverage = LiveCoverage(selection.targets)
        runs.add_counters(db, run_id, fixtures_targeted=len(selection.targets))
        runs.append_detail(db, run_id, {"kind": "temporal_policy", **LIVE_POLICY.as_dict()})
        runs.append_detail(db, run_id, selection.as_detail(now))
        runs.set_coverage(db, run_id, coverage.as_dict())
        db.commit()
        targets = [t.as_target() for t in selection.targets]
        stop = await svc.run_batches(db, run_id, targets, provider, options, LIVE_POLICY, coverage, clock, calls)
    except Exception as exc:
        svc.fail_run(db, run_id, provider, calls[0], exc)
        raise
    return svc.close_run(db, run_id, stop, options.mode, LIVE_STOP_MESSAGES)
