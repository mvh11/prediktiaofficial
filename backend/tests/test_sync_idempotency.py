"""Sync de fixtures con un proveedor falso: idempotencia, cambios, equipos nuevos y ligas seguidas."""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, update

from app.integrations.football.base import FootballDataProvider
from app.models import Fixture, FixtureProviderMapping, Team, TeamProviderMapping
from app.services import fixture_sync_service
from tests.conftest import make_competition, make_fixture_data

pytestmark = pytest.mark.db


class FakeFootballProvider(FootballDataProvider):
    name = "api-football"

    def __init__(self) -> None:
        self.fixtures: dict[int, list] = {}
        self.calls: list[int] = []

    async def check_status(self):
        raise NotImplementedError

    async def get_competitions(self, external_ids):
        return []

    async def get_teams(self, competition_external_id, season):
        return []

    async def get_fixtures(self, competition_external_id, season, date_from=None, date_to=None):
        self.calls.append(competition_external_id)
        return list(self.fixtures.get(competition_external_id, []))


@pytest.fixture
def fake(monkeypatch) -> FakeFootballProvider:
    provider = FakeFootballProvider()
    monkeypatch.setattr(fixture_sync_service, "get_football_provider", lambda: provider)
    return provider


def _sync(db, **kwargs):
    return asyncio.run(fixture_sync_service.sync_fixtures(db, **kwargs))


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _fixture(db, external_id: int) -> Fixture:
    db.expire_all()
    return db.scalars(select(Fixture).where(Fixture.external_id == external_id)).one()


def test_fixture_sync_twice_is_idempotent(db_session, fake):
    make_competition(db_session, 265)
    fake.fixtures[265] = [
        make_fixture_data(1, status="FT", home_goals=2, away_goals=0, fulltime_home=2, fulltime_away=0),
        make_fixture_data(2, home=2, away=1),
    ]
    _sync(db_session)
    counts = (_count(db_session, Fixture), _count(db_session, FixtureProviderMapping), _count(db_session, TeamProviderMapping))

    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.execute(update(FixtureProviderMapping).values(last_seen_at=old))
    _sync(db_session)

    assert counts == (2, 2, 2)
    assert (_count(db_session, Fixture), _count(db_session, FixtureProviderMapping), _count(db_session, TeamProviderMapping)) == counts
    db_session.expire_all()
    assert all(m.last_seen_at > old and m.verified_at is None for m in db_session.scalars(select(FixtureProviderMapping)))


def test_fixture_sync_applies_changes(db_session, fake):
    make_competition(db_session, 265)
    fake.fixtures[265] = [make_fixture_data(1), make_fixture_data(2, status="FT", home_goals=1, away_goals=1, fulltime_home=1, fulltime_away=1)]
    _sync(db_session)
    assert (_fixture(db_session, 1).fulltime_home, _fixture(db_session, 1).fulltime_away) == (None, None)

    new_kickoff = datetime(2026, 9, 8, 18, 30, tzinfo=timezone.utc)
    fake.fixtures[265] = [
        # NS -> FT con cambio de hora: se rellena fulltime
        make_fixture_data(1, status="FT", home_goals=3, away_goals=1, fulltime_home=3, fulltime_away=1, kickoff_at=new_kickoff),
        # FT -> AWD: fulltime vuelve a NULL y goals pasa al resultado administrativo
        make_fixture_data(2, status="AWD", home_goals=0, away_goals=3),
    ]
    _sync(db_session)

    f1, f2 = _fixture(db_session, 1), _fixture(db_session, 2)
    assert (f1.status_short, f1.home_goals, f1.fulltime_home, f1.fulltime_away, f1.kickoff_at) == ("FT", 3, 3, 1, new_kickoff)
    assert (f2.status_short, f2.home_goals, f2.away_goals, f2.fulltime_home) == ("AWD", 0, 3, None)
    assert _count(db_session, Fixture) == 2


def test_unknown_team_gets_team_and_mapping(db_session, fake):
    make_competition(db_session, 265)
    fake.fixtures[265] = [make_fixture_data(1, home=77, away=88)]
    _sync(db_session)
    team_id = db_session.scalar(select(Team.id).where(Team.external_id == 77))
    assert team_id is not None
    mapping = db_session.scalars(select(TeamProviderMapping).where(TeamProviderMapping.team_id == team_id)).one()
    assert (mapping.provider, mapping.external_id, mapping.match_method) == ("api-football", "77", "origin")


def test_sync_respects_tracked_league_ids(db_session, fake):
    make_competition(db_session, 265)  # en TRACKED_LEAGUE_IDS
    untracked_id, _ = make_competition(db_session, 999_999, name="No seguida")
    fake.fixtures[999_999] = [make_fixture_data(5)]

    result = _sync(db_session)
    assert 999_999 not in fake.calls and 265 in fake.calls
    assert all(r.competition_id != untracked_id for r in result.competitions)

    result = _sync(db_session, competition_id=untracked_id)
    assert [r.error for r in result.competitions] == ["No está en TRACKED_LEAGUE_IDS"]
    assert 999_999 not in fake.calls


def test_db_error_in_one_competition_rolls_back_and_continues(db_session, fake):
    # la sync recorre las competiciones por país y nombre: "A ..." va antes que "B ..."
    failing_id, _ = make_competition(db_session, 265, name="A Liga que falla")
    ok_id, _ = make_competition(db_session, 39, name="B Liga correcta")
    db_session.commit()

    fake.fixtures[265] = [
        make_fixture_data(10, home=31, away=32),
        # par fulltime incompleto (el adapter nunca lo entregaría): viola ck_fixtures_fulltime_pair
        make_fixture_data(11, home=31, away=32, status="FT", home_goals=1, away_goals=0, fulltime_home=1, fulltime_away=None),
    ]
    fake.fixtures[39] = [make_fixture_data(20, home=41, away=42)]

    result = _sync(db_session)
    by_id = {r.competition_id: r for r in result.competitions}

    assert fake.calls == [265, 39]
    assert by_id[failing_id].fixtures == 0 and "IntegrityError" in by_id[failing_id].error
    assert by_id[ok_id].fixtures == 1 and by_id[ok_id].error is None
    assert result.fixtures_synced == 1
    # de la competición fallida no queda nada a medias: ni partidos, ni equipos, ni mapeos
    db_session.expire_all()
    assert set(db_session.scalars(select(Fixture.external_id))) == {20}
    assert db_session.scalar(select(func.count()).select_from(Team).where(Team.external_id.in_([31, 32]))) == 0
    assert set(db_session.scalars(select(FixtureProviderMapping.external_id))) == {"20"}
