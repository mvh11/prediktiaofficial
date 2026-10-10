"""Features prepartido de estadísticas (M5.7B). Contrato en app/schemas/prematch_features.py.

Solo lee y no persiste nada. Hechos de partidos (kickoff, equipos, estado) desde la evidencia de
DI-A6, STRICT_KNOWLEDGE(H), sin modificar su contrato; el estado actual de fixtures solo enumera
candidatos. Estadísticas desde la capa as-of (statistics_as_of) con el mismo T y H.
"""

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import statistics_knowledge_repository as knowledge_repo
from app.repositories.fixture_knowledge_repository import strict_knowledge
from app.schemas.fixture import FINISHED_STATUSES
from app.schemas.fixture_knowledge import KnowledgeStatus, require_utc
from app.schemas.prematch_features import (
    FORM_METRICS,
    FeatureStatus,
    MetricSide,
    MetricValue,
    SampleStatus,
    TeamRecentForm,
    WindowMatch,
)
from app.schemas.statistics_knowledge import StatisticsAsOfStatus, regime_of
from app.services.statistics_as_of import NORMALIZER_VERSION, statistics_as_of

DEFAULT_WINDOW = 5
DEFAULT_MIN_SAMPLES = 3
EXTRA_TIME_STATUSES = frozenset({"AET", "PEN"})
MEAN_QUANTUM = Decimal("0.0001")

_SAMPLE_BY_AS_OF = {
    StatisticsAsOfStatus.UNKNOWN_AT_T: SampleStatus.UNKNOWN_AT_T,
    StatisticsAsOfStatus.EMPTY: SampleStatus.EMPTY,
    StatisticsAsOfStatus.BLOCKED: SampleStatus.BLOCKED,
    StatisticsAsOfStatus.RECONSTRUCTION_MISMATCH: SampleStatus.RECONSTRUCTION_MISMATCH,
    StatisticsAsOfStatus.IDENTITY_UNVERIFIED: SampleStatus.IDENTITY_UNVERIFIED,
}


def team_recent_form_v1(
    db: Session,
    *,
    team_id: int,
    target_fixture_id: int,
    provider: str,
    cutoff: datetime,
    horizon: datetime | None = None,
    window: int = DEFAULT_WINDOW,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> TeamRecentForm:
    """Forma reciente en estadísticas del equipo antes del partido objetivo, en el corte T."""
    require_utc(cutoff)
    horizon = cutoff if horizon is None else horizon
    require_utc(horizon)
    regime = regime_of(cutoff, horizon)
    if window < 1 or min_samples < 1:
        raise ValueError("window y min_samples tienen que ser >= 1")
    base = dict(team_id=team_id, target_fixture_id=target_fixture_id, provider=provider, cutoff=cutoff, horizon=horizon,
                regime=regime, window_size=window, min_samples=min_samples, normalizer_version=NORMALIZER_VERSION)

    def fail(status: FeatureStatus, **extra: Any) -> TeamRecentForm:
        return TeamRecentForm(status=status, **base, **extra)

    # 1. Objetivo: conocido en H, con el equipo, y T no posterior a su kickoff (T no se deriva de K)
    target = strict_knowledge(db, [target_fixture_id], horizon).results[target_fixture_id]
    if target.status is KnowledgeStatus.UNKNOWN_AT_T:
        return fail(FeatureStatus.TARGET_UNKNOWN)
    if target.status is KnowledgeStatus.TEMPORAL_AMBIGUITY:
        return fail(FeatureStatus.TARGET_AMBIGUOUS)
    state = target.state
    known = dict(target_kickoff=state.kickoff_at, target_known_at=target.known_at)
    if team_id not in (state.home_team_id, state.away_team_id):
        return fail(FeatureStatus.TARGET_TEAM_MISMATCH, **known)
    if cutoff > state.kickoff_at:
        return fail(FeatureStatus.CUTOFF_AFTER_KICKOFF, **known)

    # 2. Partidos jugados del equipo antes de T según la evidencia en H (fixtures solo enumera)
    candidates = [f for f in knowledge_repo.enumerate_team_fixtures(db, team_id) if f != target_fixture_id]
    played = []
    for fixture_id, k in strict_knowledge(db, candidates, horizon).results.items():
        if k.status is KnowledgeStatus.TEMPORAL_AMBIGUITY:
            if any(o.kickoff_at < cutoff and team_id in (o.home_team_id, o.away_team_id) for o in k.observations):
                return fail(FeatureStatus.AMBIGUOUS_HISTORY, **known)
            continue
        s = k.state
        if s is None or team_id not in (s.home_team_id, s.away_team_id):
            continue
        if s.status_short in FINISHED_STATUSES and s.kickoff_at < cutoff:
            played.append((s, k.known_at))
    played.sort(key=lambda item: (item[0].kickoff_at, item[0].fixture_id), reverse=True)
    frontier = [s.kickoff_at for s, _ in played[: window + 1]]
    if len(set(frontier)) != len(frontier):
        return fail(FeatureStatus.AMBIGUOUS_ORDER, **known)
    selected = played[:window]

    # 3. Estadísticas as-of de la ventana con el mismo T y H
    report = statistics_as_of(db, [s.fixture_id for s, _ in selected], provider, cutoff, horizon=horizon)
    matches, samples = [], []
    for s, known_at in selected:
        venue = "home" if s.home_team_id == team_id else "away"
        stats = report.results[s.fixture_id]
        own, rival = stats.team(venue), stats.team("away" if venue == "home" else "home")
        if s.status_short in EXTRA_TIME_STATUSES:
            sample = SampleStatus.EXCLUDED_EXTRA_TIME
        elif stats.status in _SAMPLE_BY_AS_OF:
            sample = _SAMPLE_BY_AS_OF[stats.status]
        elif own is None or own.team_id != team_id:
            sample = SampleStatus.TEAM_STATISTICS_MISSING
        else:
            sample = SampleStatus.USED
            samples.append((own.values, rival.values if rival is not None else None))
        obs = stats.observation
        matches.append(WindowMatch(
            s.fixture_id, s.kickoff_at, venue, s.status_short, known_at, sample,
            obs.observation_id if obs else None, obs.observed_at if obs else None,
            obs.available_at if obs else None, obs.payload_hash if obs else None, obs.provenance if obs else None,
        ))

    metrics = []
    for name in FORM_METRICS:
        for side, pick in ((MetricSide.FOR, 0), (MetricSide.AGAINST, 1)):
            values = [Decimal(getattr(pair[pick], name)) for pair in samples if pair[pick] is not None and getattr(pair[pick], name) is not None]
            mean = (sum(values) / len(values)).quantize(MEAN_QUANTUM, ROUND_HALF_EVEN) if len(values) >= min_samples else None
            metrics.append(MetricValue(name, side, mean, len(values)))
    return TeamRecentForm(status=FeatureStatus.OK, **base, **known, window=tuple(matches), metrics=tuple(metrics))


def fingerprint(form: TeamRecentForm) -> str:
    """Huella del CONTENIDO de la feature (sha256): no incluye ids de observaciones, que dependen
    del orden de ingestión; sí el hash del raw y sus instantes."""
    data = asdict(form)
    for match in data["window"]:
        match.pop("statistics_observation_id")
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
