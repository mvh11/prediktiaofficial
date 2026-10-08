"""Capa as-of de estadísticas (M5.7A): reconstruye qué estadísticas de un partido tenía Prediktia
disponibles en un corte T. Contrato y tipos en app/schemas/statistics_knowledge.py.

Solo lee. Para cada partido:
1. Elige la observación vigente en T entre las disponibles en T (repository, una consulta).
2. Comprueba que la evidencia sigue íntegra: el hash del raw y la disponibilidad registrada se
   reproducen con el normalizador vigente. Si no, RECONSTRUCTION_MISMATCH (sin valores).
3. Re-normaliza el raw con el parser del proveedor (el del adapter: aquí no se conoce ningún
   nombre de estadística) y recalcula los checks de identidad y de valores del service.
   Cualquier BLOCKING -> BLOCKED, sin valores, como cuando el run no lo normalizó.
4. Contrasta los BLOCKING recalculados con los que registró el run que creó la observación; si no
   coinciden, RECONSTRUCTION_MISMATCH: no se elige cuál de los dos tenía razón.

No usa fixture_team_statistics (estado actual, fusionado entre versiones) ni fusiona versiones:
los valores son la proyección de UNA observación. La identidad de los equipos se resuelve con
los equipos del partido y los mappings activos actuales (no tienen historia).
"""

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.football.api_football_statistics import parse_team_statistics as parse_api_football_team
from app.repositories import statistics_knowledge_repository as knowledge_repo
from app.repositories import statistics_repository as stats_repo
from app.repositories.provider_mapping_repository import canonical_external_id
from app.schemas.fixture_knowledge import require_utc
from app.schemas.statistics import TeamStatisticsData
from app.schemas.statistics_knowledge import (
    FixtureStatisticsAsOf,
    QualityIssueRef,
    StatisticsAsOfReport,
    StatisticsAsOfStatus,
    StatisticsObservationRef,
    TeamStatisticsAsOf,
    provenance_of,
)
from app.services import statistics_quality_checks as qc
from app.services.statistics_service import NORMALIZER_VERSION

# Parser de una entrada {team, statistics} del raw guardado, por proveedor (vive en el adapter)
RAW_PARSERS: dict[str, Callable[[Any], TeamStatisticsData]] = {"api-football": parse_api_football_team}

# Desajustes de reconstrucción (no son checks de calidad del proveedor)
PAYLOAD_NOT_A_LIST = "payload_not_a_list"
PAYLOAD_HASH_MISMATCH = "payload_hash_mismatch"
AVAILABILITY_MISMATCH = "availability_mismatch"
QUALITY_RECORD_MISMATCH = "quality_record_mismatch"

_STATUS_BY_AVAILABILITY = {
    "available": StatisticsAsOfStatus.AVAILABLE,
    "partial": StatisticsAsOfStatus.PARTIAL,
    "empty": StatisticsAsOfStatus.EMPTY,
}


def statistics_as_of(db: Session, fixture_ids: Iterable[int], provider: str, cutoff: datetime) -> StatisticsAsOfReport:
    """Estadísticas disponibles en T de cada partido pedido (todos salen en el informe)."""
    require_utc(cutoff)
    parser = RAW_PARSERS.get(provider)
    if parser is None:
        raise ValueError(f"sin parser de estadísticas para el proveedor {provider!r}")
    ids = sorted(set(fixture_ids))
    chosen = knowledge_repo.eligible_observations(db, ids, provider, cutoff)
    teams_by_fixture = knowledge_repo.fixture_teams(db, chosen)
    parsed = {f: _parse(row["payload"], parser) for f, row in chosen.items()}
    external = {canonical_external_id(t.provider_team_id) for teams in parsed.values() for t in teams or () if t.provider_team_id is not None}
    team_map = stats_repo.prefetch_team_mappings(db, external, provider)
    checks = knowledge_repo.run_checks(db, (row["run_id"] for row in chosen.values() if row["run_id"] is not None))

    results = {}
    for fixture_id in ids:
        row = chosen.get(fixture_id)
        if row is None:
            results[fixture_id] = FixtureStatisticsAsOf(fixture_id, provider, cutoff, StatisticsAsOfStatus.UNKNOWN_AT_T)
            continue
        recorded = _recorded_blocking(checks.get(row["run_id"]), fixture_id) if row["run_id"] in checks else None
        results[fixture_id] = _reconstruct(
            row, parsed[fixture_id], teams_by_fixture[fixture_id], team_map, recorded, provider=provider, cutoff=cutoff
        )
    return StatisticsAsOfReport(cutoff, provider, results)


def statistics_as_of_fixture(db: Session, fixture_id: int, provider: str, cutoff: datetime) -> FixtureStatisticsAsOf:
    return statistics_as_of(db, [fixture_id], provider, cutoff).results[fixture_id]


def _parse(payload: Any, parser: Callable[[Any], TeamStatisticsData]) -> list[TeamStatisticsData] | None:
    return [parser(entry) for entry in payload] if isinstance(payload, list) else None


def _recorded_blocking(run_checks: list[Any], fixture_id: int) -> tuple[str, ...]:
    return tuple(sorted({
        c["code"] for c in run_checks
        if isinstance(c, dict) and c.get("fixture_id") == fixture_id and c.get("severity") == qc.BLOCKING
    }))


def _issue_ref(issue: qc.Issue) -> QualityIssueRef:
    return QualityIssueRef(issue.severity, issue.code, issue.provider_team_id, issue.detail.get("field"))


def _reconstruct(
    row: Any,
    teams: list[TeamStatisticsData] | None,
    fixture_sides: tuple[int, int],
    team_map: dict[str, int],
    recorded: tuple[str, ...] | None,
    *,
    provider: str,
    cutoff: datetime,
) -> FixtureStatisticsAsOf:
    fixture_id = row["fixture_id"]
    observation = StatisticsObservationRef(
        observation_id=row["id"],
        run_id=row["run_id"],
        source=row["source"],
        provenance=provenance_of(row["source"], row["observed_at"], row["available_at"]),
        observed_at=row["observed_at"],
        available_at=row["available_at"],
        payload_hash=row["payload_hash"],
        availability=row["availability"],
        teams_returned=row["teams_returned"],
    )

    def result(status: StatisticsAsOfStatus, issues: list[QualityIssueRef], team_values: tuple[TeamStatisticsAsOf, ...] = ()) -> FixtureStatisticsAsOf:
        return FixtureStatisticsAsOf(
            fixture_id, provider, cutoff, status, observation, row["eligible_versions"], team_values, tuple(issues),
            recorded, NORMALIZER_VERSION,
        )

    def mismatch(code: str) -> FixtureStatisticsAsOf:
        return result(StatisticsAsOfStatus.RECONSTRUCTION_MISMATCH, [QualityIssueRef(qc.BLOCKING, code)])

    # 1. Integridad de la evidencia y compatibilidad del normalizador con el raw histórico
    if teams is None:
        return mismatch(PAYLOAD_NOT_A_LIST)
    if stats_repo.payload_hash(row["payload"]) != row["payload_hash"]:
        return mismatch(PAYLOAD_HASH_MISMATCH)
    with_stats = [t for t in teams if t.has_statistics]
    if len(with_stats) != row["teams_returned"] or len(teams) > 2:
        return mismatch(AVAILABILITY_MISMATCH)

    # 2. Checks del service sobre el raw (identidad de TODAS las entradas y valores)
    home_id, away_id = fixture_sides
    sides = {home_id: "home", away_id: "away"}
    issues: list[qc.Issue] = []
    resolved: dict[int, int] = {}
    for index, team in enumerate(teams):
        tid = team.provider_team_id
        if tid is None:
            issues.append(qc.Issue(qc.TEAM_WITHOUT_ID, fixture_id=fixture_id, detail={"entry": index}))
        else:
            team_id = team_map.get(canonical_external_id(tid))
            if team_id is None:
                issues.append(qc.Issue(qc.TEAM_WITHOUT_MAPPING, fixture_id=fixture_id, provider_team_id=tid))
            elif team_id not in sides:
                issues.append(qc.Issue(qc.TEAM_NOT_IN_FIXTURE, fixture_id=fixture_id, provider_team_id=tid))
            elif team_id in resolved.values():
                issues.append(qc.Issue(qc.DUPLICATE_TEAM, fixture_id=fixture_id, provider_team_id=tid))
            else:
                resolved[index] = team_id
        issues.extend(qc.team_value_issues(team, fixture_id=fixture_id))
    blocking = tuple(sorted({i.code for i in issues if i.severity == qc.BLOCKING}))

    if recorded is not None and recorded != blocking:
        refs = [_issue_ref(i) for i in issues if i.severity != qc.INFO]
        return result(StatisticsAsOfStatus.RECONSTRUCTION_MISMATCH, [*refs, QualityIssueRef(qc.BLOCKING, QUALITY_RECORD_MISMATCH)])
    if blocking:
        return result(StatisticsAsOfStatus.BLOCKED, [_issue_ref(i) for i in issues if i.severity != qc.INFO])

    # 3. Proyección de la observación: equipos con estadísticas, home antes que away
    team_values = []
    by_side = {}
    for index, team in enumerate(teams):
        if not team.has_statistics:
            continue
        side = sides[resolved[index]]
        issues.extend(qc.consistency_issues(team.values, team.provider_team_id, fixture_id=fixture_id))
        by_side[side] = team.values
        team_values.append(TeamStatisticsAsOf(side, resolved[index], team.provider_team_id, team.values, team.present))
    if "home" in by_side and "away" in by_side and (issue := qc.possession_issue(by_side["home"], by_side["away"], fixture_id=fixture_id)):
        issues.append(issue)
    team_values.sort(key=lambda t: t.side != "home")
    status = _STATUS_BY_AVAILABILITY[("empty", "partial", "available")[len(with_stats)]]
    if status.value.lower() != row["availability"]:
        return mismatch(AVAILABILITY_MISMATCH)
    return result(status, [_issue_ref(i) for i in issues if i.severity != qc.INFO], tuple(team_values))
