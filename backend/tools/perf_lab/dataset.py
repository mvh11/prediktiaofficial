"""Calendario determinista de ligas de 20 equipos, ida/vuelta, hasta diez temporadas.

COPY acelera exclusivamente la preparación, nunca los upserts medidos. Todas las
tablas/constraints/índices proceden de las migraciones reales de PREDIKTIA.
"""

import math
import random
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text

from tools.perf_lab.safety import LabTarget, read_metadata, write_metadata

FIXTURE_EXTERNAL_BASE = 10_000_000
TEAM_EXTERNAL_BASE = 1_000_000
COMPETITION_EXTERNAL_BASE = 500_000
AS_OF = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
FIXTURES_PER_SEASON = 380
TEAMS_PER_LEAGUE = 20


@dataclass(frozen=True)
class DatasetConfig:
    fixtures: int
    seed: int = 20261004

    def __post_init__(self):
        if not 1 <= self.fixtures <= 1_000_000:
            raise ValueError("fixtures debe estar entre 1 y 1.000.000")

    @property
    def seasons(self) -> int:
        return math.ceil(self.fixtures / FIXTURES_PER_SEASON)

    @property
    def competitions(self) -> int:
        return math.ceil(self.seasons / 10)

    @property
    def teams(self) -> int:
        return self.competitions * TEAMS_PER_LEAGUE

    def year(self, season_id: int) -> int:
        league_start = ((season_id - 1) // 10) * 10
        available = min(10, self.seasons - league_start)
        return 2027 - available + (season_id - 1) % 10


@lru_cache(maxsize=32)
def schedule(seed: int, season_id: int) -> tuple[tuple[int, int], ...]:
    """Round-robin: un equipo juega una vez por ronda, invierte localía en la vuelta."""
    league = (season_id - 1) // 10
    ring = list(range(league * 20 + 1, league * 20 + 21))
    random.Random(seed + season_id).shuffle(ring)
    first = []
    for round_no in range(19):
        for match in range(10):
            pair = (ring[match], ring[-match - 1])
            first.append(pair if (round_no + match) % 2 else pair[::-1])
        ring = [ring[0], ring[-1], *ring[1:-1]]
    return tuple(first + [(away, home) for home, away in first])


def fixture_at(index: int, config: DatasetConfig) -> dict:
    if not 0 <= index < config.fixtures:
        raise ValueError("Índice de fixture fuera del dataset")
    season_id, slot = divmod(index, FIXTURES_PER_SEASON)
    season_id += 1
    year = config.year(season_id)
    home, away = schedule(config.seed, season_id)[slot]
    round_no, match = divmod(slot, 10)
    kickoff = datetime(year, 8, 1, 15, tzinfo=timezone.utc) + timedelta(
        weeks=round_no, days=match // 5, hours=2 * (match % 5)
    )
    # Reprogramaciones reproducibles: no todos los partidos caen en idénticos slots.
    if (index + config.seed) % 29 == 0:
        kickoff += timedelta(days=3)
    finished = kickoff < AS_OF - timedelta(hours=2)
    home_goals = ((index * 7 + config.seed) % 11) // 3 if finished else None
    away_goals = ((index * 3 + config.seed) % 9) // 3 if finished else None
    return {
        "id": index + 1,
        "external_id": FIXTURE_EXTERNAL_BASE + index + 1,
        "season_id": season_id,
        "round": f"Regular Season - {round_no + 1}",
        "kickoff_at": kickoff,
        "status_short": "FT" if finished else ("PST" if index % 97 == 0 else "NS"),
        "status_long": "Match Finished" if finished else "Not Started",
        "elapsed": 90 if finished else None,
        "venue_name": f"Estadio {home}",
        "venue_city": f"Ciudad {home % 71}",
        "referee": f"Arbitro {index % 120}" if finished else None,
        "home_team_id": home,
        "away_team_id": away,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "halftime_home": home_goals // 2 if finished else None,
        "halftime_away": away_goals // 2 if finished else None,
        "fulltime_home": home_goals,
        "fulltime_away": away_goals,
    }


def _copy(conn, table: str, columns: tuple[str, ...], rows) -> None:
    # Identificadores internos constantes, nunca procedentes de una URL o del usuario.
    with conn.connection.driver_connection.cursor() as cursor:
        with cursor.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)


def _mappings(rows, entity_column: str):
    for row in rows:
        yield (row["id"], "api-football", str(row["external_id"]), "origin", 1, AS_OF)
        # Cobertura sintética parcial: 80% para fixtures, 100% del catálogo.
        if entity_column != "fixture_id" or row["id"] % 5:
            yield (row["id"], "5dollarfootballapi", f"lab-{entity_column}-{row['id']}", "auto_exact", 1, AS_OF)


def analyze(engine: Engine) -> None:
    # Conexión de mantenimiento sin el listener transaccional de DI-A2:
    # VACUUM exige autocommit. Las medidas sí usan el engine real.
    maintenance = create_engine(engine.url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    try:
        with maintenance.connect() as conn:
            conn.exec_driver_sql("VACUUM (ANALYZE)")
    finally:
        maintenance.dispose()


def seed_database(engine: Engine, target: LabTarget, config: DatasetConfig) -> dict:
    metadata = read_metadata(engine, target)
    with engine.connect() as conn:
        occupied = conn.execute(text("SELECT EXISTS (SELECT 1 FROM competitions) OR EXISTS (SELECT 1 FROM fixtures)")).scalar_one()
    if occupied or metadata.get("dataset"):
        raise RuntimeError("seed exige laboratorio sin datos; crea otra BD para otra escala")
    with engine.connect() as conn:
        a6_schema = conn.execute(text("SELECT to_regclass('fixture_observations') IS NOT NULL")).scalar_one()
    if a6_schema:
        # El COPY directo no puede fabricar evidencia: en DI-A6 se siembra en 0007 y el bootstrap real
        # de 0008 (`upgrade`) crea la observación inicial de cada partido, como en producción
        raise RuntimeError("esquema DI-A6 (0008): usa `init --revision 0007`, `seed` y después `upgrade`")
    started = time.perf_counter()
    competitions = [
        {"id": i, "external_id": COMPETITION_EXTERNAL_BASE + i, "name": f"Liga laboratorio {i}",
         "type": "League", "country": ("Chile", "Spain", "England", "Brazil")[i % 4]}
        for i in range(1, config.competitions + 1)
    ]
    teams = [
        {"id": i, "external_id": TEAM_EXTERNAL_BASE + i, "name": f"Equipo laboratorio {i}",
         "is_national": False, "founded": 1900 + i % 100}
        for i in range(1, config.teams + 1)
    ]
    with engine.begin() as conn:
        for table, rows, entity in (("competitions", competitions, "competition_id"), ("teams", teams, "team_id")):
            columns = tuple(rows[0])
            _copy(conn, table, columns, (tuple(row[c] for c in columns) for row in rows))
            _copy(conn, f"{entity.removesuffix('_id')}_provider_mappings",
                  (entity, "provider", "external_id", "match_method", "confidence", "last_seen_at"),
                  _mappings(rows, entity))
        seasons = []
        members = []
        for sid in range(1, config.seasons + 1):
            league = (sid - 1) // 10 + 1
            year = config.year(sid)
            seasons.append((sid, league, year, date(year, 8, 1), date(year + 1, 5, 31),
                            sid == min(league * 10, config.seasons)))
            members.extend((sid, tid) for tid in range((league - 1) * 20 + 1, league * 20 + 1))
        _copy(conn, "seasons", ("id", "competition_id", "year", "start_date", "end_date", "is_current"), seasons)
        _copy(conn, "season_teams", ("season_id", "team_id"), members)

    for start in range(0, config.fixtures, 10_000):
        rows = [fixture_at(i, config) for i in range(start, min(start + 10_000, config.fixtures))]
        columns = tuple(rows[0])
        with engine.begin() as conn:
            _copy(conn, "fixtures", columns, (tuple(row[c] for c in columns) for row in rows))
            _copy(conn, "fixture_provider_mappings",
                  ("fixture_id", "provider", "external_id", "match_method", "confidence", "last_seen_at"),
                  _mappings(rows, "fixture_id"))
        if (start + len(rows)) % 100_000 == 0:
            print(f"seed: {start + len(rows):,} fixtures", flush=True)

    with engine.begin() as conn:
        for table in ("competitions", "teams", "seasons", "fixtures"):
            conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))"))
    analyze(engine)
    with engine.connect() as conn:
        counts = {table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in (
            "competitions", "seasons", "teams", "season_teams", "fixtures",
            "competition_provider_mappings", "team_provider_mappings", "fixture_provider_mappings",
        )}
    assert counts["fixtures"] == config.fixtures
    assert counts["fixture_provider_mappings"] == config.fixtures * 2 - config.fixtures // 5
    summary = {
        **asdict(config), "competitions": config.competitions, "seasons": config.seasons,
        "teams": config.teams, "fixture_mappings": config.fixtures * 2 - config.fixtures // 5,
        "team_mappings": config.teams * 2, "competition_mappings": config.competitions * 2,
        "as_of": AS_OF.isoformat(), "generation_seconds": time.perf_counter() - started,
        "generation_status": "MEDIDO; excluido de benchmarks",
        "actual_counts": counts,
    }
    write_metadata(engine, target, {**metadata, "dataset": summary})
    return summary
