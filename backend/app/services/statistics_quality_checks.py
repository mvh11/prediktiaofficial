"""Quality checks de estadísticas de partido (M5.3). Funciones puras sobre el contrato interno:
no conocen al proveedor ni la BD (la identidad la comprueba el service con los mappings).

Gravedad:
- BLOCKING: el partido no se normaliza (afecta solo a ese partido, nunca a la temporada).
- WARNING: se guarda, pero hay algo raro que revisar.
- INFO: dato esperable (tipos desconocidos o solo-raw, partido final sin estadísticas...).

La ausencia legítima de un campo (p. ej. xG, opcional) NO genera avisos por campo: se mide en
la cobertura.
"""

from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.schemas.statistics import PERCENTAGE_FIELDS, TeamStatisticsData, TeamStatisticValues

BLOCKING, WARNING, INFO = "BLOCKING", "WARNING", "INFO"

# --- Códigos ---------------------------------------------------------------------------------
# BLOCKING
FIXTURE_NOT_FOUND = "fixture_not_found"  # el partido ya no existe en la BD
FIXTURE_WITHOUT_MAPPING = "fixture_without_mapping"  # sin mapping activo del proveedor
FIXTURE_MAPPING_CHANGED = "fixture_mapping_changed"  # el mapping cambió entre la selección y la escritura
UNEXPECTED_FIXTURE = "unexpected_fixture_in_response"  # el proveedor devolvió un partido no pedido
DUPLICATE_FIXTURE_IN_RESPONSE = "duplicate_fixture_in_response"
FIXTURE_NOT_FINAL = "fixture_not_final"  # nuestra BD no lo tiene como final
TOO_MANY_TEAMS = "too_many_teams"  # más de 2 equipos en la respuesta
TEAM_WITHOUT_ID = "team_without_id"
TEAM_WITHOUT_MAPPING = "team_without_mapping"
TEAM_NOT_IN_FIXTURE = "team_not_in_fixture"
DUPLICATE_TEAM = "duplicate_team"
NEGATIVE_VALUE = "negative_value"
PERCENTAGE_OVER_100 = "percentage_over_100"
# WARNING
PARTIAL_STATISTICS = "partial_statistics"
DEGRADED_OBSERVATION = "degraded_observation"  # la respuesta nueva trae menos que lo ya normalizado
PASSES_ACCURATE_OVER_TOTAL = "passes_accurate_over_total"
SHOTS_ON_GOAL_OVER_TOTAL = "shots_on_goal_over_total"
SHOT_COMPONENTS_MISMATCH = "shot_components_mismatch"  # on + off + blocked != total
SHOT_ZONES_MISMATCH = "shot_zones_mismatch"  # inside + outside != total
POSSESSION_SUM_OUT_OF_RANGE = "possession_sum_out_of_range"
DUPLICATED_STAT_TYPE = "duplicated_stat_type"
UNPARSEABLE_VALUE = "unparseable_value"
MALFORMED_STAT_ENTRY = "malformed_stat_entry"
# INFO
UNKNOWN_STAT_TYPE = "unknown_stat_type"
RAW_ONLY_STAT_TYPE = "raw_only_stat_type"
FINAL_FIXTURE_EMPTY = "final_fixture_empty"
AWARDED_WITHOUT_STATISTICS = "awarded_without_statistics"  # AWD/WO: no se piden

SEVERITY = {
    **dict.fromkeys(
        (FIXTURE_NOT_FOUND, FIXTURE_WITHOUT_MAPPING, FIXTURE_MAPPING_CHANGED, UNEXPECTED_FIXTURE,
         DUPLICATE_FIXTURE_IN_RESPONSE, FIXTURE_NOT_FINAL, TOO_MANY_TEAMS, TEAM_WITHOUT_ID, TEAM_WITHOUT_MAPPING,
         TEAM_NOT_IN_FIXTURE, DUPLICATE_TEAM, NEGATIVE_VALUE, PERCENTAGE_OVER_100),
        BLOCKING,
    ),
    **dict.fromkeys(
        (PARTIAL_STATISTICS, DEGRADED_OBSERVATION, PASSES_ACCURATE_OVER_TOTAL, SHOTS_ON_GOAL_OVER_TOTAL,
         SHOT_COMPONENTS_MISMATCH, SHOT_ZONES_MISMATCH, POSSESSION_SUM_OUT_OF_RANGE, DUPLICATED_STAT_TYPE,
         UNPARSEABLE_VALUE, MALFORMED_STAT_ENTRY),
        WARNING,
    ),
    **dict.fromkeys((UNKNOWN_STAT_TYPE, RAW_ONLY_STAT_TYPE, FINAL_FIXTURE_EMPTY, AWARDED_WITHOUT_STATISTICS), INFO),
}

POSSESSION_SUM_RANGE = (Decimal(98), Decimal(102))


@dataclass(frozen=True)
class Issue:
    code: str
    provider_fixture_id: str | None = None
    fixture_id: int | None = None
    provider_team_id: Any = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> str:
        return SEVERITY[self.code]

    def as_dict(self) -> dict[str, Any]:
        out = {"severity": self.severity, **{k: v for k, v in asdict(self).items() if v not in (None, {})}}
        return _jsonable(out)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return value


def team_value_issues(team: TeamStatisticsData, **ctx: Any) -> list[Issue]:
    """Evidencia que el adapter dejó en el contrato: fuera de rango (BLOCKING), no parseable,
    duplicado o entrada mal formada (WARNING), tipos desconocidos o solo-raw (INFO)."""
    issues: list[Issue] = []
    tid = team.provider_team_id
    for fld, value in team.out_of_range.items():
        code = PERCENTAGE_OVER_100 if fld in PERCENTAGE_FIELDS and value > 100 else NEGATIVE_VALUE
        issues.append(Issue(code, provider_team_id=tid, detail={"field": fld, "value": value}, **ctx))
    for fld, raw in team.unparseable.items():
        issues.append(Issue(UNPARSEABLE_VALUE, provider_team_id=tid, detail={"field": fld, "raw": repr(raw)[:80]}, **ctx))
    for fld, values in team.duplicated.items():
        issues.append(Issue(DUPLICATED_STAT_TYPE, provider_team_id=tid, detail={"field": fld, "values": [repr(v)[:40] for v in values]}, **ctx))
    if team.malformed_entries:
        issues.append(Issue(MALFORMED_STAT_ENTRY, provider_team_id=tid, detail={"count": team.malformed_entries}, **ctx))
    if team.unknown:
        issues.append(Issue(UNKNOWN_STAT_TYPE, provider_team_id=tid, detail={"types": sorted(team.unknown)}, **ctx))
    if team.raw_only:
        issues.append(Issue(RAW_ONLY_STAT_TYPE, provider_team_id=tid, detail={"types": sorted(team.raw_only)}, **ctx))
    return issues


def consistency_issues(values: TeamStatisticValues, provider_team_id: Any = None, **ctx: Any) -> list[Issue]:
    """Incoherencias entre campos del mismo equipo (WARNING: el dato raro se conserva)."""
    v = values
    issues: list[Issue] = []

    def warn(code: str, **detail: Any) -> None:
        issues.append(Issue(code, provider_team_id=provider_team_id, detail=detail, **ctx))

    if v.passes_accurate is not None and v.passes_total is not None and v.passes_accurate > v.passes_total:
        warn(PASSES_ACCURATE_OVER_TOTAL, passes_accurate=v.passes_accurate, passes_total=v.passes_total)
    if v.shots_on_goal is not None and v.shots_total is not None and v.shots_on_goal > v.shots_total:
        warn(SHOTS_ON_GOAL_OVER_TOTAL, shots_on_goal=v.shots_on_goal, shots_total=v.shots_total)
    parts = (v.shots_on_goal, v.shots_off_goal, v.shots_blocked)
    if v.shots_total is not None and None not in parts and sum(parts) != v.shots_total:
        warn(SHOT_COMPONENTS_MISMATCH, on=parts[0], off=parts[1], blocked=parts[2], total=v.shots_total)
    zones = (v.shots_inside_box, v.shots_outside_box)
    if v.shots_total is not None and None not in zones and sum(zones) != v.shots_total:
        warn(SHOT_ZONES_MISMATCH, inside=zones[0], outside=zones[1], total=v.shots_total)
    return issues


def possession_issue(home: TeamStatisticValues, away: TeamStatisticValues, **ctx: Any) -> Issue | None:
    if home.possession_pct is None or away.possession_pct is None:
        return None
    total = home.possession_pct + away.possession_pct
    low, high = POSSESSION_SUM_RANGE
    if low <= total <= high:
        return None
    return Issue(POSSESSION_SUM_OUT_OF_RANGE, detail={"home": home.possession_pct, "away": away.possession_pct, "sum": total}, **ctx)


def summarize(issues: list[Issue]) -> dict[str, dict[str, int]]:
    """{severidad: {código: n}} para details/checks del run."""
    out: dict[str, Counter] = {BLOCKING: Counter(), WARNING: Counter(), INFO: Counter()}
    for issue in issues:
        out[issue.severity][issue.code] += 1
    return {sev: dict(sorted(c.items())) for sev, c in out.items()}
