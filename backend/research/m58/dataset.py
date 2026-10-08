"""Constructor del dataset M5.8 (solo lectura; sin numpy). Una fila por partido objetivo.

- Hechos del partido (kickoff, equipos, estado, marcador a 90') desde STRICT_KNOWLEDGE(H), con H
  fijo: nunca desde el estado actual de fixtures (que solo enumera candidatos y sirve de auditoría).
- Features: team_recent_form_v1 de local y visitante en T = kickoff − 1 h y el mismo H.
- Etiqueta: 1X2 a 90' (labels.py). Solo entran filas con etiqueta verificable; las demás se
  cuentan por motivo.
- Ninguna columna de resultado entra en FEATURE_COLUMNS (lo comprueba un test).
"""

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Competition, Fixture, FixtureStatisticsObservation, Season
from app.repositories.fixture_knowledge_repository import strict_knowledge
from app.repositories.fixture_retrospective_repository import retrospective_final_results
from app.schemas.fixture_knowledge import KnowledgeStatus
from app.schemas.prematch_features import FORM_METRICS, FeatureStatus, MetricSide, SampleStatus, TeamRecentForm
from app.services.prematch_features import fingerprint, team_recent_form_v1
from research.m58.labels import label_1x2

PROVIDER = "api-football"
CUTOFF_BEFORE_KICKOFF = timedelta(hours=1)
SIDES = ("home", "away")
FEATURE_COLUMNS = tuple(
    f"{side}_{metric}_{direction.value}{suffix}"
    for side in SIDES for metric in FORM_METRICS for direction in MetricSide for suffix in ("", "_missing")
)
COUNT_COLUMNS = tuple(f"{side}_{metric}_{direction.value}_n" for side in SIDES for metric in FORM_METRICS for direction in MetricSide)


def target_universe(db: Session) -> list[int]:
    """Partidos de las temporadas con estadísticas (enumeración; los hechos se verifican en H)."""
    seasons = select(Fixture.season_id).join(FixtureStatisticsObservation, FixtureStatisticsObservation.fixture_id == Fixture.id).distinct()
    return list(db.scalars(select(Fixture.id).where(Fixture.season_id.in_(seasons)).order_by(Fixture.id)))


def _side_columns(side: str, form: TeamRecentForm) -> dict:
    row = {}
    for metric in FORM_METRICS:
        for direction in MetricSide:
            name = f"{side}_{metric}_{direction.value}"
            value = form.metric(metric, direction) if form.status is FeatureStatus.OK else None
            v = value.value if value is not None else None
            row[name] = float(v) if isinstance(v, Decimal) else v
            row[f"{name}_missing"] = v is None
            row[f"{name}_n"] = value.samples if value is not None else 0
    counts = form.sample_counts()
    used = [m for m in form.window if m.sample is SampleStatus.USED]
    row.update({
        f"{side}_feature_status": form.status.value,
        f"{side}_window_len": len(form.window),
        f"{side}_n_used": counts[SampleStatus.USED],
        **{f"{side}_n_{s.value.lower()}": counts[s] for s in SampleStatus if s is not SampleStatus.USED},
        f"{side}_fingerprint": fingerprint(form),
        f"{side}_retrospective": form.is_retrospective,
        f"{side}_audit_window_has_target": any(m.fixture_id == form.target_fixture_id for m in form.window),
        f"{side}_audit_max_window_kickoff": max((m.kickoff_at for m in form.window), default=None),
        f"{side}_audit_max_used_available_at": max((m.statistics_available_at for m in used), default=None),
        f"{side}_audit_max_used_observed_at": max((m.statistics_observed_at for m in used), default=None),
    })
    return row


def build_rows(db: Session, horizon: datetime, fixture_ids: list[int] | None = None) -> tuple[list[dict], Counter]:
    ids = target_universe(db) if fixture_ids is None else sorted(set(fixture_ids))
    knowledge = strict_knowledge(db, ids, horizon).results if ids else {}
    seasons = {s.id: (s.competition_id, s.year) for s in db.scalars(select(Season))}
    competitions = {c.id: c.name for c in db.scalars(select(Competition))}
    retro = retrospective_final_results(db, ids) if ids else {}
    rows, excluded = [], Counter()
    for fixture_id in ids:
        k = knowledge[fixture_id]
        if k.status is not KnowledgeStatus.KNOWN:
            excluded[f"fixture_{k.status.value.lower()}"] += 1
            continue
        s = k.state
        label, reason = label_1x2(s.status_short, s.fulltime_home, s.fulltime_away)
        if label is None:
            excluded[reason] += 1
            continue
        cutoff = s.kickoff_at - CUTOFF_BEFORE_KICKOFF
        competition_id, year = seasons[s.season_id]
        row = {
            "fixture_id": fixture_id, "competition_id": competition_id, "competition": competitions[competition_id],
            "season_id": s.season_id, "season_year": year, "kickoff_at": s.kickoff_at, "cutoff": cutoff, "horizon": horizon,
            "label": label, "label_status": s.status_short, "label_source": "STRICT_KNOWLEDGE(H)", "label_known_at": k.known_at,
        }
        r = retro.get(fixture_id)
        row["audit_retro_label_agrees"] = r is not None and label_1x2(r.status_short, r.fulltime_home, r.fulltime_away)[0] == label
        for side, team_id in zip(SIDES, (s.home_team_id, s.away_team_id)):
            form = team_recent_form_v1(db, team_id=team_id, target_fixture_id=fixture_id, provider=PROVIDER, cutoff=cutoff, horizon=horizon)
            row.update(_side_columns(side, form))
        row["regime"] = "HISTORICAL_BACKTEST" if horizon > cutoff else "OPERATIONAL_STRICT"
        row["is_retrospective"] = row["home_retrospective"] or row["away_retrospective"]
        rows.append(row)
    return rows, excluded


def leakage_violations(row: dict) -> list[str]:
    """Auditoría por fila: nada del propio partido ni posterior a T/H en las features."""
    out = []
    for side in SIDES:
        if row[f"{side}_audit_window_has_target"]:
            out.append(f"{side}: el objetivo está en su ventana")
        k = row[f"{side}_audit_max_window_kickoff"]
        if k is not None and k >= row["cutoff"]:
            out.append(f"{side}: partido de la ventana no anterior a T")
        a, o = row[f"{side}_audit_max_used_available_at"], row[f"{side}_audit_max_used_observed_at"]
        if a is not None and a > row["cutoff"]:
            out.append(f"{side}: estadística disponible después de T")
        if o is not None and o > row["horizon"]:
            out.append(f"{side}: estadística recibida después de H")
    return out


# Lee el estado ACTUAL (auditoría de la etiqueta): no forma parte de la huella del dataset
NOT_FINGERPRINTED = ("audit_retro_label_agrees",)


def dataset_fingerprint(rows: list[dict]) -> str:
    ordered = [{k: v for k, v in r.items() if k not in NOT_FINGERPRINTED} for r in sorted(rows, key=lambda r: r["fixture_id"])]
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
