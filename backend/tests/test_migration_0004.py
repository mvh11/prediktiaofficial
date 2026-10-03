"""Migración 0004 sobre una BD de pruebas: backfill de mapeos y de fulltime, y downgrade."""

import pytest
from sqlalchemy import inspect, text

from tests.conftest import alembic_run

pytestmark = pytest.mark.db

SEED_0003 = """
INSERT INTO competitions (id, external_id, name) VALUES (1, 265, 'Liga Test'), (2, 13, 'Copa Test');
INSERT INTO seasons (id, competition_id, year, is_current) VALUES (1, 1, 2026, true), (2, 2, 2026, true);
INSERT INTO teams (id, external_id, name) VALUES (1, 2323, 'Equipo A'), (2, 2329, 'Equipo B'), (3, 2315, 'Equipo C');
INSERT INTO season_teams (season_id, team_id) VALUES (1, 1), (1, 2);
INSERT INTO fixtures (id, external_id, season_id, kickoff_at, status_short, home_team_id, away_team_id,
                      home_goals, away_goals, extratime_home, extratime_away, penalty_home, penalty_away) VALUES
  (1, 1505353, 1, '2026-01-30 23:00+00', 'FT',  1, 2, 2, 1, NULL, NULL, NULL, NULL),
  (2, 1622633, 2, '2026-08-27 17:00+00', 'AET', 1, 3, 5, 1, 1, 0, NULL, NULL),
  (3, 1631509, 2, '2026-09-16 22:00+00', 'PEN', 2, 3, 3, 2, NULL, NULL, 3, 4),
  (4, 1642009, 1, '2026-07-31 22:30+00', 'AWD', 2, 1, 0, 3, NULL, NULL, NULL, NULL),
  (5, 1505531, 1, '2026-10-10 18:00+00', 'NS',  3, 1, NULL, NULL, NULL, NULL, NULL, NULL);
"""
ENTITY_TABLES = ["competitions", "seasons", "teams", "season_teams", "fixtures"]


def _counts(conn) -> dict[str, int]:
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in ENTITY_TABLES}


@pytest.fixture
def db_at_0003_with_data(migrated_db):
    from app.db.database import engine

    alembic_run("downgrade", "0003")
    with engine.begin() as conn:
        conn.execute(text(SEED_0003))
    try:
        yield engine
    finally:
        alembic_run("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE fixtures, season_teams, seasons, teams, competitions CASCADE"))


def test_upgrade_backfill(db_at_0003_with_data):
    engine = db_at_0003_with_data
    with engine.connect() as conn:
        before = _counts(conn)

    alembic_run("upgrade", "0004")

    with engine.connect() as conn:
        assert _counts(conn) == before
        for table, entity, n in [
            ("competition_provider_mappings", "competitions", 2),
            ("team_provider_mappings", "teams", 3),
            ("fixture_provider_mappings", "fixtures", 5),
        ]:
            rows = conn.execute(
                text(f"SELECT provider, match_method, confidence, verified_at, last_seen_at FROM {table}")
            ).all()
            assert len(rows) == n
            assert all(r.provider == "api-football" and r.match_method == "origin" for r in rows)
            assert all(r.confidence == 1 and r.verified_at is None and r.last_seen_at is not None for r in rows)
            # external_id idéntico (como texto) al de la entidad
            fk = table.split("_provider")[0] + "_id"
            drift = conn.execute(
                text(
                    f"SELECT count(*) FROM {entity} e LEFT JOIN {table} m "
                    f"ON m.{fk} = e.id AND m.external_id = e.external_id::text WHERE m.id IS NULL"
                )
            ).scalar_one()
            assert drift == 0

        fulltime = dict(
            (r.status_short, (r.fulltime_home, r.fulltime_away))
            for r in conn.execute(text("SELECT status_short, fulltime_home, fulltime_away FROM fixtures"))
        )
        assert fulltime == {
            "FT": (2, 1),  # FT: fulltime = goals
            "AET": (None, None),  # no se deriva de extratime
            "PEN": (None, None),
            "AWD": (None, None),
            "NS": (None, None),
        }


def test_downgrade_roundtrip(db_at_0003_with_data):
    engine = db_at_0003_with_data
    alembic_run("upgrade", "0004")
    with engine.connect() as conn:
        before = _counts(conn)

    alembic_run("downgrade", "0003")
    with engine.connect() as conn:
        insp = inspect(conn)
        tables = set(insp.get_table_names())
        assert not {"providers", "competition_provider_mappings", "team_provider_mappings", "fixture_provider_mappings"} & tables
        assert "fulltime_home" not in {c["name"] for c in insp.get_columns("fixtures")}
        assert _counts(conn) == before
        assert conn.execute(text("SELECT external_id FROM fixtures WHERE id = 1")).scalar_one() == 1505353

    alembic_run("upgrade", "0004")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM fixture_provider_mappings")).scalar_one() == 5
