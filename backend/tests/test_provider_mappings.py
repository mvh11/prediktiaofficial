"""Constraints de las tablas de mapeo y del marcador a 90' (BD de pruebas)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.models import (
    CompetitionProviderMapping,
    Fixture,
    FixtureObservation,
    FixtureProviderMapping,
    Provider,
    TeamProviderMapping,
)
from app.repositories import fixture_repository
from app.repositories.provider_mapping_repository import upsert_origin_mappings
from tests.conftest import make_competition, make_evidence, make_fixture_data

pytestmark = pytest.mark.db

FIVE = "5dollarfootballapi"


def _two_fixtures(db) -> tuple[int, int]:
    _, season_id = make_competition(db)
    data = [make_fixture_data(9001), make_fixture_data(9002)]
    teams = [d.home_team for d in data] + [d.away_team for d in data]
    team_ids = fixture_repository.ensure_teams(db, teams, "api-football")
    fixture_repository.upsert_fixtures(db, season_id, data, team_ids, "api-football", make_evidence())
    ids = dict(db.execute(select(Fixture.external_id, Fixture.id)).all())
    return ids[9001], ids[9002]


def _add(db, model, **values) -> None:
    with db.begin_nested():
        db.add(model(**values))
        db.flush()


def _fixture_mapping(db, fixture_id: int, external_id: str, **extra) -> None:
    values = {"match_method": "manual", **extra}
    _add(db, FixtureProviderMapping, fixture_id=fixture_id, provider=FIVE, external_id=external_id, **values)


def test_providers_seeded(db_session):
    codes = set(db_session.scalars(select(Provider.code)))
    assert {"api-football", FIVE} <= codes


def test_same_external_id_cannot_point_to_two_fixtures(db_session):
    f1, f2 = _two_fixtures(db_session)
    _fixture_mapping(db_session, f1, "4000000001")
    with pytest.raises(IntegrityError):
        _fixture_mapping(db_session, f2, "4000000001")


def test_same_external_id_cannot_point_to_two_competitions(db_session):
    c1, _ = make_competition(db_session, 265)
    c2, _ = make_competition(db_session, 39)
    _add(db_session, CompetitionProviderMapping, competition_id=c1, provider=FIVE, external_id="1126893247", match_method="manual")
    with pytest.raises(IntegrityError):
        _add(db_session, CompetitionProviderMapping, competition_id=c2, provider=FIVE, external_id="1126893247", match_method="manual")


def test_multiple_external_ids_same_provider_same_fixture(db_session):
    """N:1: un partido aplazado puede tener el ID viejo y el nuevo, incluso los dos activos."""
    f1, _ = _two_fixtures(db_session)
    _fixture_mapping(db_session, f1, "111", match_method="auto_exact", confidence=0.95)
    _fixture_mapping(db_session, f1, "222", match_method="auto_exact", confidence=0.95)
    rows = db_session.scalars(
        select(FixtureProviderMapping).where(
            FixtureProviderMapping.fixture_id == f1, FixtureProviderMapping.provider == FIVE
        )
    ).all()
    assert len(rows) == 2 and all(r.is_active for r in rows)


def test_is_active_can_change_without_deleting(db_session):
    f1, _ = _two_fixtures(db_session)
    _fixture_mapping(db_session, f1, "333")
    db_session.execute(
        update(FixtureProviderMapping).where(FixtureProviderMapping.external_id == "333").values(is_active=False)
    )
    row = db_session.scalars(select(FixtureProviderMapping).where(FixtureProviderMapping.external_id == "333")).one()
    assert row.is_active is False and row.fixture_id == f1


def test_last_seen_updates_and_verified_untouched(db_session):
    competition_id, _ = make_competition(db_session)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    verified = datetime(2021, 6, 1, tzinfo=timezone.utc)
    db_session.execute(
        update(CompetitionProviderMapping)
        .where(CompetitionProviderMapping.competition_id == competition_id)
        .values(last_seen_at=old, verified_at=verified)
    )
    # nueva observación del mismo ID por el proveedor
    upsert_origin_mappings(db_session, CompetitionProviderMapping, "api-football", [(competition_id, 265, "Liga Test")])
    db_session.expire_all()
    row = db_session.scalars(
        select(CompetitionProviderMapping).where(CompetitionProviderMapping.competition_id == competition_id)
    ).one()
    assert row.last_seen_at > old
    assert row.verified_at == verified


def test_new_origin_mapping_is_not_verified(db_session):
    competition_id, _ = make_competition(db_session)
    row = db_session.scalars(
        select(CompetitionProviderMapping).where(CompetitionProviderMapping.competition_id == competition_id)
    ).one()
    assert row.match_method == "origin"
    assert row.verified_at is None and row.last_seen_at is not None


@pytest.mark.parametrize(
    ("method", "confidence"),
    [("origin", 1.2), ("auto_fuzzy", -0.1), ("foo", 1), ("origin", 0.8), ("manual", 0.5)],
)
def test_confidence_and_match_method_checks(db_session, method, confidence):
    f1, _ = _two_fixtures(db_session)
    with pytest.raises(IntegrityError):
        _fixture_mapping(db_session, f1, "444", match_method=method, confidence=confidence)


def test_fuzzy_mapping_with_partial_confidence_ok(db_session):
    f1, _ = _two_fixtures(db_session)
    _fixture_mapping(db_session, f1, "555", match_method="auto_fuzzy", confidence=0.8)


@pytest.mark.parametrize("external_id", ["", " 12", "12 "])
def test_external_id_format_check(db_session, external_id):
    f1, _ = _two_fixtures(db_session)
    with pytest.raises(IntegrityError):
        _fixture_mapping(db_session, f1, external_id)


def test_fk_integrity(db_session):
    f1, _ = _two_fixtures(db_session)
    with pytest.raises(IntegrityError):  # entidad inexistente
        _fixture_mapping(db_session, 999_999_999, "666")
    with pytest.raises(IntegrityError):  # proveedor inexistente
        _add(db_session, FixtureProviderMapping, fixture_id=f1, provider="nadie", external_id="666", match_method="manual")

    _fixture_mapping(db_session, f1, "777")
    with pytest.raises(IntegrityError):  # RESTRICT: no se borra un proveedor con mapeos
        with db_session.begin_nested():
            db_session.execute(delete(Provider).where(Provider.code == FIVE))

    with pytest.raises(IntegrityError):  # RESTRICT (DI-A6): un fixture con historia no se borra en silencio
        with db_session.begin_nested():
            db_session.execute(delete(Fixture).where(Fixture.id == f1))
    # Borrado explícito: primero su evidencia temporal y después el fixture, cuyos mapeos van en CASCADE
    db_session.execute(delete(FixtureObservation).where(FixtureObservation.fixture_id == f1))
    db_session.execute(delete(Fixture).where(Fixture.id == f1))  # CASCADE
    assert db_session.scalar(select(FixtureProviderMapping.id).where(FixtureProviderMapping.fixture_id == f1)) is None


@pytest.mark.parametrize(
    "values",
    [
        {"status_short": "NS", "fulltime_home": 1, "fulltime_away": 0},  # no terminado
        {"status_short": "FT", "fulltime_home": 1, "fulltime_away": None},  # par incompleto
        {"status_short": "FT", "fulltime_home": -1, "fulltime_away": 0},  # negativo
    ],
)
def test_fulltime_checks(db_session, values):
    f1, _ = _two_fixtures(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(update(Fixture).where(Fixture.id == f1).values(**values))


def test_team_mappings_written_by_fixture_sync_helpers(db_session):
    _two_fixtures(db_session)
    external_ids = set(
        db_session.scalars(select(TeamProviderMapping.external_id).where(TeamProviderMapping.provider == "api-football"))
    )
    assert {"1", "2"} <= external_ids
