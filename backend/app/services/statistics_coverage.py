"""Cobertura de estadísticas de un run (M5.3): mínima para el dry-run y el piloto.

Se acumula en memoria a medida que se evalúan los partidos (funciona igual en dry-run, que no
escribe estadísticas) y se guarda en statistics_runs.coverage tras cada lote.

- fixtures: cuántos partidos quedaron available / partial / empty / blocked / missing;
- fields: para cada campo v1, % de equipos con estadísticas que lo traen con valor;
- xg / core: % de partidos con estadísticas en los que AMBOS equipos tienen xG / todos los
  campos core. xG es opcional: su ausencia no es un aviso, solo un número aquí.
"""

from dataclasses import dataclass, field

from app.schemas.statistics import CORE_FIELDS, STATISTIC_FIELDS, TeamStatisticValues

OUTCOMES = ("available", "partial", "empty", "blocked", "missing")


def _pct(part: int, total: int) -> float | None:
    return round(100 * part / total, 1) if total else None


@dataclass
class CoverageAccumulator:
    competition_id: int | None = None
    season_id: int | None = None
    fixtures: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    teams_with_stats: int = 0
    field_counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATISTIC_FIELDS, 0))
    fixtures_with_stats: int = 0
    fixtures_with_xg: int = 0
    fixtures_with_core: int = 0
    excluded_awarded: int = 0
    skipped_existing: int = 0

    def add_outcome(self, outcome: str) -> None:
        self.fixtures[outcome] += 1

    def add_fixture_values(self, teams: list[TeamStatisticValues]) -> None:
        """Valores normalizados de los equipos (con estadísticas) de un partido no bloqueado."""
        if not teams:
            return
        self.fixtures_with_stats += 1
        for values in teams:
            self.teams_with_stats += 1
            for name in STATISTIC_FIELDS:
                if getattr(values, name) is not None:
                    self.field_counts[name] += 1
        if len(teams) == 2:
            if all(t.expected_goals is not None for t in teams):
                self.fixtures_with_xg += 1
            if all(getattr(t, f) is not None for t in teams for f in CORE_FIELDS):
                self.fixtures_with_core += 1

    def as_dict(self) -> dict:
        return {
            "competition_id": self.competition_id,
            "season_id": self.season_id,
            "fixtures": dict(self.fixtures),
            "excluded_awarded": self.excluded_awarded,
            "skipped_existing": self.skipped_existing,
            "fixtures_with_stats": self.fixtures_with_stats,
            "teams_with_stats": self.teams_with_stats,
            "fields_pct": {f: _pct(n, self.teams_with_stats) for f, n in self.field_counts.items()},
            "xg_pct": _pct(self.fixtures_with_xg, self.fixtures_with_stats),
            "core_pct": _pct(self.fixtures_with_core, self.fixtures_with_stats),
        }
