"""Controles de calidad Q1–Q15 del backfill histórico.

Cada check devuelve un CheckResult con id, severidad, passed, count, un detalle corto y como
mucho unos pocos external_id de ejemplo. Los checks DETECTAN anomalías; nunca las corrigen.

Severidades (centralizadas en SEVERITY):
- blocking: si falla, el par competición-temporada NO se aplica (cero escrituras de dominio).
- warning: se registra y la ejecución continúa.

Decisiones documentadas:
- Q1 es blocking: recibir 0 partidos (o salirse del rango esperado, si se indica uno) indica
  que la temporada pedida no es la que creemos. Sin rango configurado solo se bloquea el 0.
- Q6 es warning: el adapter descarta pares de marcador inválidos y el partido se guarda con
  goals NULL. Bloquear toda la temporada por un partido así perdería el resto; las features
  solo usan partidos con marcador, así que basta con registrarlo.
"""

from collections.abc import Iterable
from datetime import datetime

from app.schemas.backfill import MAX_SAMPLES, CheckResult
from app.schemas.fixture import FINAL_STATUSES, FINISHED_STATUSES, FixtureData

SEVERITY: dict[str, str] = {
    "Q1": "blocking",
    "Q2": "blocking",
    "Q3": "blocking",
    "Q4": "blocking",
    "Q5": "blocking",
    "Q6": "warning",
    "Q7": "warning",
    "Q8": "blocking",
    "Q9": "warning",
    "Q10": "warning",
    "Q11": "warning",
    "Q12": "blocking",
    "Q13": "blocking",
    "Q14": "warning",
    "Q15": "blocking",
}

# Estados terminales: finales deportivos/administrativos más cancelado y abandonado
TERMINAL_STATUSES = FINAL_STATUSES | {"CANC", "ABD"}


def _result(check_id: str, offenders: Iterable[int] = (), detail: str = "", count: int | None = None) -> CheckResult:
    ids = list(offenders)
    n = len(ids) if count is None else count
    return CheckResult(
        id=check_id,
        severity=SEVERITY[check_id],
        passed=n == 0,
        count=n,
        detail=detail,
        samples=ids[:MAX_SAMPLES],
    )


def _pair(f: FixtureData, home: str, away: str) -> tuple[int | None, int | None]:
    return getattr(f, home), getattr(f, away)


def _complete(pair: tuple[int | None, int | None]) -> bool:
    return pair[0] is not None and pair[1] is not None


# --- Checks sobre los datos recibidos (antes de escribir) -----------------------------------


def q1_received(received: int, expected_range: tuple[int, int] | None = None) -> CheckResult:
    if received == 0:
        return _result("Q1", count=1, detail="El proveedor no devolvió ningún partido")
    if expected_range is None:
        return _result("Q1", count=0, detail=f"{received} recibidos; sin rango esperado configurado")
    low, high = expected_range
    if not low <= received <= high:
        return _result("Q1", count=1, detail=f"{received} recibidos fuera del rango esperado {low}–{high}")
    return _result("Q1", count=0, detail=f"{received} recibidos dentro del rango {low}–{high}")


def q5_kickoff_null(fixtures: list[FixtureData]) -> CheckResult:
    return _result("Q5", [f.external_id for f in fixtures if f.kickoff_at is None], "Partidos sin kickoff_at")


def q6_finished_without_goals(fixtures: list[FixtureData]) -> CheckResult:
    bad = [f.external_id for f in fixtures if f.status_short in FINISHED_STATUSES and not _complete(_pair(f, "home_goals", "away_goals"))]
    return _result("Q6", bad, "FT/AET/PEN sin goals válidos (se guardan con goals NULL)")


def q7_finished_without_fulltime(fixtures: list[FixtureData]) -> CheckResult:
    bad = [f.external_id for f in fixtures if f.status_short in FINISHED_STATUSES and not _complete(_pair(f, "fulltime_home", "fulltime_away"))]
    return _result("Q7", bad, "FT/AET/PEN sin fulltime (no se inventa)")


def q8_fulltime_partial_or_negative(fixtures: list[FixtureData]) -> CheckResult:
    bad = []
    for f in fixtures:
        home, away = _pair(f, "fulltime_home", "fulltime_away")
        if (home is None) != (away is None) or (home is not None and (home < 0 or away < 0)):
            bad.append(f.external_id)
    return _result("Q8", bad, "fulltime parcial o negativo")


def q9_fulltime_above_final(fixtures: list[FixtureData]) -> CheckResult:
    bad = []
    for f in fixtures:
        if f.status_short not in {"AET", "PEN"}:
            continue
        goals, fulltime = _pair(f, "home_goals", "away_goals"), _pair(f, "fulltime_home", "fulltime_away")
        if _complete(goals) and _complete(fulltime) and (fulltime[0] > goals[0] or fulltime[1] > goals[1]):
            bad.append(f.external_id)
    return _result("Q9", bad, "AET/PEN con fulltime mayor que el resultado final (incoherencia del proveedor; no se corrige)")


def q10_ft_fulltime_differs(fixtures: list[FixtureData]) -> CheckResult:
    bad = []
    for f in fixtures:
        goals, fulltime = _pair(f, "home_goals", "away_goals"), _pair(f, "fulltime_home", "fulltime_away")
        if f.status_short == "FT" and _complete(goals) and _complete(fulltime) and goals != fulltime:
            bad.append(f.external_id)
    return _result("Q10", bad, "FT con fulltime distinto de goals")


def q11_halftime_above_fulltime(fixtures: list[FixtureData]) -> CheckResult:
    bad = []
    for f in fixtures:
        halftime, fulltime = _pair(f, "halftime_home", "halftime_away"), _pair(f, "fulltime_home", "fulltime_away")
        if _complete(halftime) and _complete(fulltime) and (halftime[0] > fulltime[0] or halftime[1] > fulltime[1]):
            bad.append(f.external_id)
    return _result("Q11", bad, "halftime mayor que fulltime")


def q12_identity(
    fixtures: list[FixtureData],
    identity: dict[int, tuple[int | None, int | None]] | None,
    league_external_id: int,
    year: int,
    existing_seasons: dict[int, int],
    target_season_id: int,
) -> CheckResult:
    """Liga/temporada de la respuesta distinta de la pedida, o external_id ya guardado en otra season.

    identity: {external_id: (league.id, league.season)} tal como vino en la respuesta del proveedor.
    existing_seasons: {external_id: season_id} de los partidos que ya existen en Prediktia.
    """
    if identity is None:
        return _result("Q12", count=len(fixtures) or 1, detail="El proveedor no informa la liga/temporada de la respuesta: no se puede verificar")
    wrong_identity = [f.external_id for f in fixtures if identity.get(f.external_id) != (league_external_id, year)]
    cross_season = [ext for ext, season_id in existing_seasons.items() if season_id != target_season_id]
    details = []
    if wrong_identity:
        details.append(f"{len(wrong_identity)} de otra liga/temporada")
    if cross_season:
        details.append(f"{len(cross_season)} ya existen en otra season")
    return _result("Q12", wrong_identity + cross_season, "; ".join(details) or "Liga, temporada y season coherentes")


def q14_unfinished_in_closed_season(fixtures: list[FixtureData], season_closed: bool, now: datetime) -> CheckResult:
    if not season_closed:
        return _result("Q14", detail="Temporada no cerrada: no aplica")
    bad = [f.external_id for f in fixtures if f.status_short not in TERMINAL_STATUSES and f.kickoff_at is not None and f.kickoff_at < now]
    return _result("Q14", bad, "Temporada cerrada con partidos pasados en estado no final (no se cambia el estado)")


def q15_same_team(fixtures: list[FixtureData]) -> CheckResult:
    bad = [f.external_id for f in fixtures if f.home_team.external_id == f.away_team.external_id]
    return _result("Q15", bad, "Local y visitante son el mismo equipo")


# --- Checks sobre el estado de la BD --------------------------------------------------------


def q2_fixture_count(before: int, after: int | None) -> CheckResult:
    """El conteo global de fixtures nunca disminuye. after=None: dry-run (el plan no tiene deletes)."""
    if after is None:
        return _result("Q2", count=0, detail=f"Dry-run: el plan solo inserta/actualiza ({before} fixtures)")
    decrease = max(before - after, 0)
    return _result("Q2", count=decrease, detail=f"fixtures antes {before}, después {after}")


def q3_duplicate_mappings(count: int) -> CheckResult:
    return _result("Q3", count=count, detail="(provider, external_id) duplicados en fixture_provider_mappings")


def q4_orphan_mappings(count: int) -> CheckResult:
    return _result("Q4", count=count, detail="Mapeos de fixtures sin fixture")


def q13_mappings(missing: list[int], is_dry_run: bool, would_create: int = 0) -> CheckResult:
    if is_dry_run:
        return _result("Q13", count=0, detail=f"Dry-run: se crearían {would_create} mapeos de fixtures")
    return _result("Q13", missing, "Partidos recibidos sin mapeo del proveedor tras la escritura")


def evaluate_incoming(
    fixtures: list[FixtureData],
    *,
    identity: dict[int, tuple[int | None, int | None]] | None,
    league_external_id: int,
    year: int,
    existing_seasons: dict[int, int],
    target_season_id: int,
    season_closed: bool,
    now: datetime,
    expected_range: tuple[int, int] | None = None,
) -> list[CheckResult]:
    """Checks que se pueden evaluar antes de escribir nada (Q1, Q5–Q12, Q14, Q15)."""
    return [
        q1_received(len(fixtures), expected_range),
        q5_kickoff_null(fixtures),
        q6_finished_without_goals(fixtures),
        q7_finished_without_fulltime(fixtures),
        q8_fulltime_partial_or_negative(fixtures),
        q9_fulltime_above_final(fixtures),
        q10_ft_fulltime_differs(fixtures),
        q11_halftime_above_fulltime(fixtures),
        q12_identity(fixtures, identity, league_external_id, year, existing_seasons, target_season_id),
        q14_unfinished_in_closed_season(fixtures, season_closed, now),
        q15_same_team(fixtures),
    ]


def sort_checks(checks: list[CheckResult]) -> list[CheckResult]:
    return sorted(checks, key=lambda c: int(c.id[1:]) if c.id[1:].isdigit() else 99)
