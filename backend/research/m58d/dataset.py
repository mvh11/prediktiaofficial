"""Dataset M5.8D (solo lectura; sin numpy): como M5.8B, con etiquetas a 90' corregidas, solo
kickoff < 2026-01-01 (el test de M5.8B no se toca) y la causa de cada muestra ausente.

Causas por partido de ventana (PROTOCOL.md): USED, EXCLUDED_EXTRA_TIME, TEAM_STATISTICS_MISSING,
NEVER_INGESTED, NOT_AVAILABLE_AT_T, EMPTY, QUALITY_OR_IDENTITY. Para separar "nunca ingerida" de
"aún no disponible en T" se lee una vez el índice de observaciones de estadísticas (solo SELECT).
"""

from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Competition, Fixture, FixtureStatisticsObservation, Season
from app.repositories.fixture_knowledge_repository import strict_knowledge
from app.schemas.fixture_knowledge import KnowledgeStatus
from app.schemas.prematch_features import SampleStatus
from app.services.prematch_features import team_recent_form_v1
from research.m58.dataset import CUTOFF_BEFORE_KICKOFF, PROVIDER, SIDES, _side_columns, target_universe
from research.m58.splits import TEST_START
from research.m58d.labels import label_1x2_v2, quality_flags

USED, EXTRA_TIME, TEAM_MISSING = "USED", "EXCLUDED_EXTRA_TIME", "TEAM_STATISTICS_MISSING"
NEVER_INGESTED, NOT_AVAILABLE_AT_T, EMPTY, QUALITY = "NEVER_INGESTED", "NOT_AVAILABLE_AT_T", "EMPTY", "QUALITY_OR_IDENTITY"
_QUALITY = {SampleStatus.BLOCKED, SampleStatus.RECONSTRUCTION_MISMATCH, SampleStatus.IDENTITY_UNVERIFIED}


def coverage_cause(sample: SampleStatus, has_any_observation: bool) -> str:
    """Causa de una muestra de ventana (puro)."""
    if sample is SampleStatus.USED:
        return USED
    if sample is SampleStatus.EXCLUDED_EXTRA_TIME:
        return EXTRA_TIME
    if sample is SampleStatus.TEAM_STATISTICS_MISSING:
        return TEAM_MISSING
    if sample is SampleStatus.EMPTY:
        return EMPTY
    if sample in _QUALITY:
        return QUALITY
    if sample is SampleStatus.UNKNOWN_AT_T:
        return NOT_AVAILABLE_AT_T if has_any_observation else NEVER_INGESTED
    raise ValueError(f"muestra sin causa: {sample}")


def build_rows(db: Session, horizon: datetime, before: datetime = TEST_START) -> tuple[list[dict], Counter, list[dict]]:
    """(filas, exclusiones, coverage). coverage = una entrada por partido de ventana."""
    ids = target_universe(db)
    knowledge = strict_knowledge(db, ids, horizon).results if ids else {}
    seasons = {s.id: (s.competition_id, s.year) for s in db.scalars(select(Season))}
    competitions = {c.id: c.name for c in db.scalars(select(Competition))}
    observed = {}  # fixture_id -> fuentes de sus observaciones (cualquier instante)
    for fid, source in db.execute(select(FixtureStatisticsObservation.fixture_id, FixtureStatisticsObservation.source).where(FixtureStatisticsObservation.provider == PROVIDER)):
        observed.setdefault(fid, set()).add(source)
    # Temporada de cada partido de ventana: solo para agrupar la cobertura (descriptivo, no es un hecho temporal)
    season_of = dict(db.execute(select(Fixture.id, Fixture.season_id)).all())
    rows, excluded, coverage = [], Counter(), []
    for fixture_id in ids:
        k = knowledge[fixture_id]
        if k.status is not KnowledgeStatus.KNOWN:
            excluded[f"fixture_{k.status.value.lower()}"] += 1
            continue
        s = k.state
        if s.kickoff_at >= before:
            excluded["m58b_test_period_not_used"] += 1
            continue
        label, reason = label_1x2_v2(s.status_short, s.fulltime_home, s.fulltime_away)
        if label is None:
            excluded[reason] += 1
            continue
        cutoff = s.kickoff_at - CUTOFF_BEFORE_KICKOFF
        competition_id, year = seasons[s.season_id]
        row = {
            "fixture_id": fixture_id, "competition_id": competition_id, "competition": competitions[competition_id],
            "season_id": s.season_id, "season_year": year, "kickoff_at": s.kickoff_at, "cutoff": cutoff, "horizon": horizon,
            "label": label, "label_status": s.status_short, "label_source": "STRICT_KNOWLEDGE(H)/fulltime_90",
            "quality_flags": quality_flags(s.status_short, (s.home_goals, s.away_goals), (s.fulltime_home, s.fulltime_away),
                                           (s.extratime_home, s.extratime_away)),
            "extra_time_non_draw_at_90": s.status_short in ("AET", "PEN") and label != "D",
        }
        for side, team_id in zip(SIDES, (s.home_team_id, s.away_team_id)):
            form = team_recent_form_v1(db, team_id=team_id, target_fixture_id=fixture_id, provider=PROVIDER, cutoff=cutoff, horizon=horizon)
            row.update(_side_columns(side, form))
            for m in form.window:
                coverage.append({
                    "target_fixture_id": fixture_id, "side": side, "window_fixture_id": m.fixture_id,
                    "cause": coverage_cause(m.sample, m.fixture_id in observed),
                    "observation_sources": sorted(observed.get(m.fixture_id, ())), "window_kickoff": m.kickoff_at,
                })
        row["regime"] = "HISTORICAL_BACKTEST" if horizon > cutoff else "OPERATIONAL_STRICT"
        row["is_retrospective"] = row["home_retrospective"] or row["away_retrospective"]
        rows.append(row)
    for entry in coverage:  # temporada/competición del partido de ventana (no del objetivo)
        sid = season_of.get(entry["window_fixture_id"])
        entry["window_competition_id"] = seasons[sid][0] if sid else None
        entry["window_competition"] = competitions[seasons[sid][0]] if sid else None
        entry["window_season"] = seasons[sid][1] if sid else None
    return rows, excluded, coverage
