"""GET /competitions sin N+1."""

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.repositories import catalog_repository
from app.schemas.catalog import SeasonData
from tests.conftest import make_competition

pytestmark = pytest.mark.db


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_competitions_single_query(db_session, client, query_counter):
    for external_id in (265, 39):
        competition_id, _ = make_competition(db_session, external_id, name=f"Liga {external_id}")
        catalog_repository.upsert_seasons(
            db_session, competition_id, [SeasonData(year=2025), SeasonData(year=2024)]
        )
    make_competition(db_session, 13, name="Sin temporada actual", current_year=None)
    db_session.flush()

    query_counter["n"] = 0
    response = client.get("/competitions")

    assert response.status_code == 200
    assert query_counter["n"] <= 2
    by_external = {c["external_id"]: c["current_season"] for c in response.json()}
    assert by_external == {265: 2026, 39: 2026, 13: None}
