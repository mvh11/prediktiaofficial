"""Tasas de goles por sede a partir de historia ya cualificada y suministrada (MOD-DEV-073). Puro: sin BD ni red.

Política aprobada (fija, sin parámetros): para el partido objetivo,
  lambda_home = media aritmética de los goles a 90' del LOCAL objetivo en sus N = 20 partidos
                elegibles más recientes jugados EN CASA;
  lambda_away = media aritmética de los goles a 90' del VISITANTE objetivo en sus N = 20 partidos
                elegibles más recientes jugados FUERA.
Mínimo M = 8 elegibles en cada sede, por separado. Sin suavizado, decaimiento, shrinkage,
pseudoconteos, ajuste por rival, mezcla de sedes, fallback de liga ni tasa mínima.

Entrada:
- target: Mapping con fixture_id, competition_id, season_id, home_team_id, away_team_id y
  kickoff_at (con zona). El corte T no puede ser posterior a su kickoff (como team_recent_form_v1).
- history: lista o tupla de Mappings, un partido cada uno, con fixture_id, competition_id,
  season_id, home_team_id, away_team_id, kickoff_at (con zona), status_short, fulltime_home,
  fulltime_away y result_observed_at: el instante (con zona) en que se observó ESE estado del
  partido (identidad, equipos, temporada, kickoff, estado y marcador), es decir, su known_at de
  STRICT_KNOWLEDGE. Nunca se deriva del kickoff, de la fecha ni de nada más; None = sin evidencia.
- memberships: lista o tupla de Mappings (team_id, competition_id, season_id, observed_at con zona):
  evidencia de que el equipo pertenecía a esa competición en esa temporada. Solo se usa para
  partidos de OTRA temporada que la del objetivo.
- cutoff: T, con zona horaria.
Identidades: ids internos estables (int, no bool); nunca nombres.

Elegibilidad de un partido para una sede (se aplica ANTES de elegir los 20 más recientes). El
candidato de la sede local es el partido con home_team_id = local objetivo; el de la visitante,
el de away_team_id = visitante objetivo (un partido local-objetivo contra visitante-objetivo es
candidato en las dos). Primer motivo de exclusión que se cumpla, en este orden:
  target_fixture                     es el partido objetivo;
  wrong_competition                  otra competition_id;
  kickoff_not_before_cutoff          kickoff_at >= T;
  result_observed_at_missing         sin result_observed_at;
  result_observed_after_cutoff       result_observed_at > T (conocido solo retrospectivamente);
  status_not_finished, fulltime_incomplete, fulltime_invalid
                                     liquidación a 90' de research.m58.goal_labels (FT/AET/PEN con
                                     el par fulltime; nunca prórroga ni penaltis);
  cross_season_membership_not_evidenced
                                     otra temporada sin pertenencia del equipo a (competición,
                                     temporada) observada en <= T.
Un partido del mismo día cuenta si su kickoff es < T y su resultado se observó en <= T: no hay
embargo por día.

Orden y selección: los elegibles se ordenan por kickoff_at (instante) de más reciente a más antiguo
y se toman hasta 20. Si un empate de kickoff cruza el corte entre la posición 20 y la 21, el
conjunto elegido dependería de un criterio arbitrario: NO_ESTIMATE (ambiguous_*_selection). Un
empate que queda entero dentro de los elegidos no cambia el conjunto ni la media; ahí el
fixture_id solo fija el orden de selected_fixture_ids.

Resultado: ESTIMATE solo si las dos sedes tienen >= 8 elegibles y una selección no ambigua; si no,
NO_ESTIMATE con sus motivos (insufficient_home_sample, ambiguous_home_selection y los de la sede
visitante) y ambas lambdas en None, nunca 0 ni un valor de relleno. Una media observada de 0 es una
estimación válida. Errores de forma o evidencia contradictoria (campos que faltan, tipos, zonas
horarias, fixture_id repetido, mismo equipo en las dos sedes, estado final observado antes de su
propio kickoff, T posterior al kickoff objetivo): EvidenceContractError.

Lo que este módulo NO puede verificar (EVIDENCE_LIMITATIONS): que la historia suministrada esté
completa, que los instantes observados sean auténticos, la continuidad real de la identidad de un
equipo más allá de su id estable, la verdad de competición y pertenencia fuera de la evidencia
dada, y la identidad del objetivo en T. Validar la forma de los instantes no es verificar el PIT.
"""

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from research.m58.goal_labels import goals_at_90
from research.m58.labels import LABELLED_STATUSES

N_RECENT = 20
M_MINIMUM = 8
POLICY = "VENUE_ARITHMETIC_MEAN_N20_M8"
ESTIMATE, NO_ESTIMATE = "ESTIMATE", "NO_ESTIMATE"
HOME, AWAY = "HOME", "AWAY"

TARGET_FIXTURE = "target_fixture"
WRONG_COMPETITION = "wrong_competition"
KICKOFF_NOT_BEFORE_CUTOFF = "kickoff_not_before_cutoff"
RESULT_OBSERVED_AT_MISSING = "result_observed_at_missing"
RESULT_OBSERVED_AFTER_CUTOFF = "result_observed_after_cutoff"
CROSS_SEASON_MEMBERSHIP_NOT_EVIDENCED = "cross_season_membership_not_evidenced"

EVIDENCE_LIMITATIONS = (
    "HISTORY_COMPLETENESS_NOT_VERIFIED",
    "OBSERVED_AT_AUTHENTICITY_NOT_VERIFIED",
    "TEAM_IDENTITY_CONTINUITY_LIMITED_TO_STABLE_IDS",
    "COMPETITION_AND_MEMBERSHIP_LIMITED_TO_SUPPLIED_EVIDENCE",
    "TARGET_IDENTITY_AT_CUTOFF_SUPPLIED_BY_CALLER",
)

TARGET_FIELDS = ("fixture_id", "competition_id", "season_id", "home_team_id", "away_team_id", "kickoff_at")
HISTORY_FIELDS = TARGET_FIELDS + ("status_short", "fulltime_home", "fulltime_away", "result_observed_at")
MEMBERSHIP_FIELDS = ("team_id", "competition_id", "season_id", "observed_at")
_IDS = ("fixture_id", "competition_id", "season_id", "home_team_id", "away_team_id")


class EvidenceContractError(ValueError):
    """La evidencia suministrada falta, está mal formada o es contradictoria (error del llamador)."""


@dataclass(frozen=True)
class VenueSample:
    venue: str  # HOME | AWAY
    team_id: int
    candidates: int  # partidos de ese equipo en esa sede dentro de la historia suministrada
    eligible: int
    selected: int
    selected_fixture_ids: tuple[int, ...]  # del más reciente al más antiguo
    excluded_by_reason: Mapping[str, int]  # solo lectura, ordenado por motivo
    no_estimate_reason: str | None  # None si la sede cumple la política


@dataclass(frozen=True)
class VenueGoalRates:
    status: str  # ESTIMATE | NO_ESTIMATE
    no_estimate_reasons: tuple[str, ...]
    lambda_home: float | None
    lambda_away: float | None
    home: VenueSample
    away: VenueSample
    target_fixture_id: int
    cutoff: datetime
    policy: str
    n_recent: int
    m_minimum: int
    history_records: int
    evidence_limitations: tuple[str, ...]


def _aware(value, what: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceContractError(f"{what}: se espera un datetime con zona horaria, no {value!r}")
    return value


def _id(value, what: str) -> int:
    if type(value) is not int:
        raise EvidenceContractError(f"{what}: se espera un id entero (no bool), no {value!r}")
    return value


def _mapping(value, fields, what: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise EvidenceContractError(f"{what}: se espera un Mapping, no {type(value).__name__}")
    missing = [f for f in fields if f not in value]
    if missing:
        raise EvidenceContractError(f"{what}: faltan los campos {missing}")
    return value


def _sequence(value, what: str) -> tuple:
    if not isinstance(value, (list, tuple)):
        raise EvidenceContractError(f"{what}: se espera una lista o tupla, no {type(value).__name__}")
    return tuple(value)


def _fixture(value, fields, what: str) -> Mapping:
    row = _mapping(value, fields, what)
    for name in _IDS:
        _id(row[name], f"{what}.{name}")
    if row["home_team_id"] == row["away_team_id"]:
        raise EvidenceContractError(f"{what}: el mismo equipo en las dos sedes")
    _aware(row["kickoff_at"], f"{what}.kickoff_at")
    return row


def _history(history) -> tuple:
    rows, seen = _sequence(history, "history"), set()
    for i, row in enumerate(rows):
        what = f"history[{i}]"
        _fixture(row, HISTORY_FIELDS, what)
        if row["fixture_id"] in seen:
            raise EvidenceContractError(f"{what}: fixture_id repetido {row['fixture_id']}")
        seen.add(row["fixture_id"])
        observed = row["result_observed_at"]
        if observed is not None:
            _aware(observed, f"{what}.result_observed_at")
            if row["status_short"] in LABELLED_STATUSES and observed < row["kickoff_at"]:
                raise EvidenceContractError(f"{what}: estado final observado antes de su propio kickoff")
    return rows


def _memberships(memberships, cutoff) -> frozenset:
    """(team_id, competition_id, season_id) con pertenencia observada en <= T."""
    known = set()
    for i, m in enumerate(_sequence(memberships, "memberships")):
        what = f"memberships[{i}]"
        _mapping(m, MEMBERSHIP_FIELDS, what)
        key = tuple(_id(m[f], f"{what}.{f}") for f in ("team_id", "competition_id", "season_id"))
        if _aware(m["observed_at"], f"{what}.observed_at") <= cutoff:
            known.add(key)
    return frozenset(known)


def _exclusion(row, target, team_id, side: int, cutoff, members) -> tuple[str | None, int | None]:
    """(motivo de exclusión o None, goles a 90' del equipo en su sede: side 0 local, 1 visitante)."""
    if row["fixture_id"] == target["fixture_id"]:
        return TARGET_FIXTURE, None
    if row["competition_id"] != target["competition_id"]:
        return WRONG_COMPETITION, None
    if not row["kickoff_at"] < cutoff:
        return KICKOFF_NOT_BEFORE_CUTOFF, None
    if row["result_observed_at"] is None:
        return RESULT_OBSERVED_AT_MISSING, None
    if row["result_observed_at"] > cutoff:
        return RESULT_OBSERVED_AFTER_CUTOFF, None
    goals, reason = goals_at_90(row["status_short"], row["fulltime_home"], row["fulltime_away"])
    if goals is None:
        return reason, None
    if row["season_id"] != target["season_id"] and (team_id, row["competition_id"], row["season_id"]) not in members:
        return CROSS_SEASON_MEMBERSHIP_NOT_EVIDENCED, None
    return None, goals[side]


def _venue(venue, team_id, rows, target, cutoff, members) -> tuple[VenueSample, float | None]:
    role, side = ("home_team_id", 0) if venue == HOME else ("away_team_id", 1)
    excluded, eligible, candidates = Counter(), [], 0
    for row in rows:
        if row[role] != team_id:
            continue
        candidates += 1
        reason, goals = _exclusion(row, target, team_id, side, cutoff, members)
        if reason is None:
            eligible.append((row["kickoff_at"], row["fixture_id"], goals))
        else:
            excluded[reason] += 1
    ordered = sorted(sorted(eligible, key=lambda e: e[1]), key=lambda e: e[0], reverse=True)  # kickoff desc; id solo dentro de empates
    selected = ordered[:N_RECENT]
    reason = None
    if len(ordered) < M_MINIMUM:
        reason = f"insufficient_{venue.lower()}_sample"
    elif len(ordered) > N_RECENT and ordered[N_RECENT - 1][0] == ordered[N_RECENT][0]:
        reason = f"ambiguous_{venue.lower()}_selection"
    if candidates != len(eligible) + sum(excluded.values()):
        raise AssertionError("recuento incoherente en la sede")
    sample = VenueSample(venue=venue, team_id=team_id, candidates=candidates, eligible=len(ordered),
                         selected=len(selected) if reason is None else 0,
                         selected_fixture_ids=tuple(e[1] for e in selected) if reason is None else (),
                         excluded_by_reason=MappingProxyType(dict(sorted(excluded.items()))), no_estimate_reason=reason)
    mean = sum(e[2] for e in selected) / len(selected) if reason is None else None
    return sample, mean


def estimate_venue_goal_rates(target, history, cutoff, memberships=()) -> VenueGoalRates:
    """ESTIMATE con (lambda_home, lambda_away) o NO_ESTIMATE con motivos; EvidenceContractError si la
    evidencia es inválida. No modifica sus argumentos."""
    cutoff = _aware(cutoff, "cutoff")
    target = _fixture(target, TARGET_FIELDS, "target")
    if cutoff > target["kickoff_at"]:
        raise EvidenceContractError("el corte T es posterior al kickoff del partido objetivo")
    rows = _history(history)
    members = _memberships(memberships, cutoff)
    home, lambda_home = _venue(HOME, target["home_team_id"], rows, target, cutoff, members)
    away, lambda_away = _venue(AWAY, target["away_team_id"], rows, target, cutoff, members)
    reasons = tuple(r for r in (home.no_estimate_reason, away.no_estimate_reason) if r is not None)
    if reasons:
        lambda_home = lambda_away = None
    elif not all(math.isfinite(x) and x >= 0 for x in (lambda_home, lambda_away)):
        raise AssertionError("tasa no finita o negativa")
    return VenueGoalRates(status=NO_ESTIMATE if reasons else ESTIMATE, no_estimate_reasons=reasons, lambda_home=lambda_home,
                          lambda_away=lambda_away, home=home, away=away, target_fixture_id=target["fixture_id"], cutoff=cutoff,
                          policy=POLICY, n_recent=N_RECENT, m_minimum=M_MINIMUM, history_records=len(rows),
                          evidence_limitations=EVIDENCE_LIMITATIONS)
