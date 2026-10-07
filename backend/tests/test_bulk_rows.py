"""Transporte UNNEST de las escrituras multi-fila (app/repositories/bulk_rows.py, DI-A6 / DI-A3F)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import Integer, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DataError

from app.models import Fixture, FixtureObservation, Team
from app.repositories import fixture_repository
from app.repositories.bulk_rows import insert_rows, unnest_rows
from tests.conftest import make_competition, make_evidence, make_fixture_data


def compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_arrays_must_be_aligned_and_non_empty():
    with pytest.raises(ValueError):
        unnest_rows([("a", Integer()), ("b", Integer())], [[1, 2], [1]])  # unnest() rellenaría con NULL
    with pytest.raises(ValueError):
        unnest_rows([("a", Integer())], [[]])
    with pytest.raises(ValueError):
        unnest_rows([("a", Integer())], [[1], [2]])


def test_rows_must_share_columns_and_expressions():
    with pytest.raises(ValueError):
        insert_rows(Team, [{"external_id": 1, "name": "a"}, {"external_id": 2}])
    with pytest.raises(ValueError):
        insert_rows(Team, [{"external_id": 1, "name": func.now()}, {"external_id": 2, "name": "b"}])
    with pytest.raises(ValueError):
        insert_rows(Team, [{"external_id": 1, "name": func.now()}, {"external_id": 2, "name": func.lower("x")}])
    with pytest.raises(ValueError):
        insert_rows(Team, [])


def test_statement_shape_does_not_depend_on_the_batch_size():
    """Una sola forma de SQL para cualquier lote: un parámetro (array) por columna, cacheable."""
    small = insert_rows(Team, [{"external_id": i, "name": f"t{i}", "logo_url": None} for i in range(3)])
    large = insert_rows(Team, [{"external_id": i, "name": f"t{i}", "logo_url": None} for i in range(3000)])
    assert compiled(small) == compiled(large)
    assert "unnest(" in compiled(small) and "VALUES" not in compiled(small)
    assert len(small.compile(dialect=postgresql.dialect()).params) == 3


def test_strings_travel_as_text_arrays():
    """Un cast a varchar(n)[] truncaría en silencio; text[] deja que la columna rechace el exceso."""
    sql = compiled(insert_rows(Team, [{"external_id": 1, "name": "a", "logo_url": None}]))
    assert "TEXT[]" in sql and "VARCHAR" not in sql


@pytest.mark.db
def test_overlong_string_is_rejected_not_truncated(db_session):
    _, season_id = make_competition(db_session, 265)
    data = make_fixture_data(1, venue_name="x" * 201)  # venue_name es VARCHAR(200)
    team_ids = fixture_repository.ensure_teams(db_session, [data.home_team, data.away_team], "api-football")
    savepoint = db_session.begin_nested()
    with pytest.raises(DataError):
        fixture_repository.upsert_fixtures(db_session, season_id, [data], team_ids, "api-football", make_evidence())
    savepoint.rollback()
    assert db_session.scalar(select(func.count()).select_from(Fixture)) == 0
    assert db_session.scalar(select(func.count()).select_from(FixtureObservation)) == 0


@pytest.mark.db
def test_database_defaults_and_values_round_trip(db_session):
    """Los defaults siguen siendo los de la BD (is_national, created_at, recorded_at) y los tipos
    (timestamptz, NULL, bytea, uuid, texto con caracteres raros) llegan intactos."""
    _, season_id = make_competition(db_session, 265)
    kickoff = datetime(2026, 9, 1, 20, 0, 0, 123456, tzinfo=timezone.utc)
    data = make_fixture_data(1, status="FT", home_goals=0, away_goals=0, fulltime_home=0, fulltime_away=0,
                             kickoff_at=kickoff, venue_name="Estadio Ñuñoa “norte”", referee=None)
    team_ids = fixture_repository.ensure_teams(db_session, [data.home_team, data.away_team], "api-football")
    evidence = make_evidence()
    fixture_repository.upsert_fixtures(db_session, season_id, [data], team_ids, "api-football", evidence)
    db_session.flush()
    f = db_session.scalars(select(Fixture)).one()
    o = db_session.scalars(select(FixtureObservation)).one()
    assert (f.kickoff_at, f.venue_name, f.referee, f.home_goals, f.halftime_home) == (kickoff, data.venue_name, None, 0, None)
    assert (o.evidence_id, o.observed_at, bytes(o.state_hash)) == (evidence.evidence_id, evidence.observed_at, bytes(f.last_state_hash))
    assert f.created_at is not None and o.recorded_at is not None
    assert db_session.execute(text("SELECT bool_and(NOT is_national) FROM teams")).scalar() is True
