"""Sync de fixtures y evidencia temporal (DI-A6): una respuesta del proveedor = un evidence_id, con
observed_at tomado al recibirla; confirmaciones reales en cada sync; nada si la competición no escribe."""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from app.integrations.exceptions import ProviderResponseError
from app.integrations.football.base import FootballDataProvider
from app.models import Fixture, FixtureObservation, Season
from app.services import fixture_sync_service
from tests.conftest import make_competition, make_fixture_data

pytestmark = pytest.mark.db


class TimedProvider(FootballDataProvider):
    """Proveedor falso que anota cuándo devolvió cada respuesta."""

    name = "api-football"

    def __init__(self) -> None:
        self.fixtures: dict[int, list] = {}
        self.returned_at: dict[int, datetime] = {}
        self.fail: set[int] = set()
        self.on_call = None

    async def check_status(self):
        raise NotImplementedError

    async def get_competitions(self, external_ids):
        return []

    async def get_teams(self, competition_external_id, season):
        return []

    async def get_fixtures(self, competition_external_id, season, date_from=None, date_to=None):
        if self.on_call:
            self.on_call(competition_external_id)
        if competition_external_id in self.fail:
            raise ProviderResponseError(self.name, "respuesta inválida")
        self.returned_at[competition_external_id] = datetime.now(timezone.utc)
        return list(self.fixtures.get(competition_external_id, []))


@pytest.fixture
def provider(monkeypatch) -> TimedProvider:
    fake = TimedProvider()
    monkeypatch.setattr(fixture_sync_service, "get_football_provider", lambda: fake)
    return fake


def _sync(db):
    return asyncio.run(fixture_sync_service.sync_fixtures(db))


def _observations(db) -> list[FixtureObservation]:
    db.expire_all()
    return list(db.scalars(select(FixtureObservation).order_by(FixtureObservation.observed_at, FixtureObservation.fixture_id)))


def _by_competition(db, observations) -> dict[int, list[FixtureObservation]]:
    external = dict(db.execute(select(Fixture.id, Fixture.external_id)).all())
    grouped: dict[int, list] = {}
    for o in observations:
        grouped.setdefault(external[o.fixture_id] // 100, []).append(o)
    return grouped


def test_each_provider_response_is_one_evidence_taken_at_receipt(db_session, provider):
    make_competition(db_session, 265)
    make_competition(db_session, 39, name="Liga B")
    provider.fixtures[265] = [make_fixture_data(26501), make_fixture_data(26502, home=3, away=4)]
    provider.fixtures[39] = [make_fixture_data(3901, home=5, away=6, competition_external_id=39)]
    _sync(db_session)
    finished = datetime.now(timezone.utc)

    grouped = _by_competition(db_session, _observations(db_session))
    assert sorted(len(v) for v in grouped.values()) == [1, 2]
    evidence_ids = set()
    for league, obs in ((265, grouped[265]), (39, grouped[39])):
        assert len({o.evidence_id for o in obs}) == 1  # la respuesta comparte su evidence_id
        assert len({o.observed_at for o in obs}) == 1
        assert all((o.source, o.provider) == ("sync", "api-football") for o in obs)
        # Tomado al recibir la respuesta: nunca antes de que el proveedor devolviera, ni después del run
        assert provider.returned_at[league] <= obs[0].observed_at <= finished
        evidence_ids.add(obs[0].evidence_id)
    assert len(evidence_ids) == 2  # una por respuesta, nunca por run

    fixtures = list(db_session.scalars(select(Fixture)))
    observed = {o.fixture_id: o.observed_at for o in _observations(db_session)}
    assert all(f.last_observed_at == observed[f.id] for f in fixtures)


def test_every_sync_is_new_evidence_even_without_changes(db_session, provider):
    make_competition(db_session, 265)
    provider.fixtures[265] = [make_fixture_data(26501, status="FT", home_goals=1, away_goals=0, fulltime_home=1, fulltime_away=0)]
    _sync(db_session)
    result = _sync(db_session)

    [competition] = result.competitions
    assert (competition.fixtures, competition.created, competition.updated, competition.unchanged) == (1, 0, 0, 1)
    obs = _observations(db_session)
    assert len(obs) == 2 and obs[0].evidence_id != obs[1].evidence_id and obs[0].observed_at < obs[1].observed_at
    db_session.expire_all()
    assert db_session.scalars(select(Fixture)).one().last_observed_at == obs[1].observed_at


def test_failed_or_skipped_competition_writes_no_evidence(db_session, provider):
    make_competition(db_session, 265)
    competition_id, _ = make_competition(db_session, 39, name="Liga B")
    provider.fixtures[265] = [make_fixture_data(26501)]
    provider.fixtures[39] = [make_fixture_data(3901, home=5, away=6, competition_external_id=39)]
    provider.fail = {265}

    def change_season(league):  # la temporada de la liga 39 deja de ser la actual durante la descarga
        if league == 39:
            db_session.execute(update(Season).where(Season.competition_id == competition_id).values(is_current=False))
            db_session.commit()

    provider.on_call = change_season
    result = _sync(db_session)
    by_name = {c.name: c for c in result.competitions}
    assert by_name["Liga Test"].error and by_name["Liga B"].skipped
    assert _observations(db_session) == []
