"""DI-A5E: integración de recovery persistente en fixture/catalog sync.

Solo usa proveedores falsos y la BD destructiva de tests autorizada por conftest.
No hay red ni producción.
"""

import asyncio
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.integrations.exceptions import ProviderAuthError, ProviderResponseError
from app.integrations.football.base import FootballDataProvider
from app.models import Fixture, FixtureObservation, ProviderIncident, SyncUnitState
from app.repositories import sync_recovery_repository as recovery_repo
from app.schemas.catalog import CompetitionData, SeasonData, TeamData
from app.services import catalog_sync_service, fixture_sync_service
from tests.conftest import make_competition, make_fixture_data

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 11)
PROVIDER = "api-football"


class RecoveryProvider(FootballDataProvider):
    name = PROVIDER

    def __init__(self) -> None:
        self.competitions: list[CompetitionData] = []
        self.fixtures: dict[int, list] = {}
        self.teams: dict[int, list[TeamData]] = {}
        self.fixture_failures: dict[int, Exception] = {}
        self.team_failures: dict[int, Exception] = {}
        self.calls: list[tuple[str, int | None]] = []
        self.on_fixture_call = None
        self.on_team_call = None

    async def check_status(self):
        raise NotImplementedError

    async def get_competitions(self, external_ids):
        self.calls.append(("leagues", None))
        return [c for c in self.competitions if c.external_id in set(external_ids)]

    async def get_teams(self, competition_external_id, season):
        self.calls.append(("teams", competition_external_id))
        if self.on_team_call:
            self.on_team_call(competition_external_id)
        if competition_external_id in self.team_failures:
            raise self.team_failures[competition_external_id]
        return list(self.teams.get(competition_external_id, []))

    async def get_fixtures(self, competition_external_id, season, date_from=None, date_to=None):
        self.calls.append(("fixtures", competition_external_id))
        if self.on_fixture_call:
            self.on_fixture_call(competition_external_id)
        if competition_external_id in self.fixture_failures:
            raise self.fixture_failures[competition_external_id]
        return list(self.fixtures.get(competition_external_id, []))


def _fixture_sync(db, provider, **kwargs):
    return asyncio.run(
        fixture_sync_service.sync_fixtures(
            db, provider=provider, today=TODAY, **kwargs
        )
    )


def _catalog_sync(db, provider):
    return asyncio.run(catalog_sync_service.sync_catalog(db, provider=provider))


def _state(db, unit_kind: str, season_id: int) -> SyncUnitState:
    db.expire_all()
    return db.scalars(
        select(SyncUnitState).where(
            SyncUnitState.unit_kind == unit_kind,
            SyncUnitState.provider == PROVIDER,
            SyncUnitState.season_id == season_id,
        )
    ).one()


def _incidents(db) -> list[ProviderIncident]:
    db.expire_all()
    return list(db.scalars(select(ProviderIncident).order_by(ProviderIncident.id)))


def test_fixture_full_sync_records_attempt_success_and_preserves_a6_evidence(db_session, monkeypatch):
    _, season_id = make_competition(db_session, 265)
    monkeypatch.setattr(
        fixture_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265]})(),
    )
    provider = RecoveryProvider()
    provider.fixtures[265] = [
        make_fixture_data(
            1001,
            status="FT",
            home_goals=1,
            away_goals=0,
            fulltime_home=1,
            fulltime_away=0,
        )
    ]
    in_transaction_during_http = []
    provider.on_fixture_call = lambda _league: in_transaction_during_http.append(
        db_session.in_transaction()
    )

    result = _fixture_sync(db_session, provider)

    state = _state(db_session, "fixtures_season", season_id)
    assert result.fixtures_synced == 1
    assert in_transaction_during_http == [False]
    assert state.last_attempt_started_at is not None
    assert state.last_outcome == "succeeded"
    assert state.last_success_at == state.last_outcome_at
    assert state.last_success_at >= state.last_attempt_started_at
    assert state.consecutive_failures == 0
    assert state.last_failure_at is None
    assert state.last_error_class is None
    assert db_session.scalar(select(func.count()).select_from(FixtureObservation)) == 1
    fixture = db_session.scalars(select(Fixture).where(Fixture.external_id == 1001)).one()
    observation = db_session.scalars(
        select(FixtureObservation).where(FixtureObservation.fixture_id == fixture.id)
    ).one()
    assert observation.source == "sync"
    assert observation.provider == PROVIDER
    assert fixture.last_observed_at == observation.observed_at
    assert bytes(fixture.last_state_hash) == bytes(observation.state_hash)


def test_fixture_range_sync_does_not_create_recovery_unit(db_session, monkeypatch):
    make_competition(db_session, 265)
    monkeypatch.setattr(
        fixture_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265]})(),
    )
    provider = RecoveryProvider()
    provider.fixtures[265] = [make_fixture_data(1002)]

    result = _fixture_sync(
        db_session,
        provider,
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 2),
    )

    assert result.fixtures_synced == 1
    assert db_session.scalar(select(func.count()).select_from(SyncUnitState)) == 0
    assert db_session.scalar(select(func.count()).select_from(FixtureObservation)) == 1


def test_fixture_local_provider_failure_persists_unit_failure_and_continues(db_session, monkeypatch):
    _, season_a = make_competition(db_session, 265, name="A Liga")
    _, season_b = make_competition(db_session, 39, name="B Liga")
    monkeypatch.setattr(
        fixture_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265, 39]})(),
    )
    provider = RecoveryProvider()
    provider.fixture_failures[265] = ProviderResponseError(
        PROVIDER, "Respuesta HTTP 503", 503
    )
    provider.fixtures[39] = [
        make_fixture_data(3901, home=3, away=4, competition_external_id=39)
    ]

    result = _fixture_sync(db_session, provider)

    assert provider.calls == [("fixtures", 265), ("fixtures", 39)]
    assert sum(1 for c in result.competitions if c.error) == 1
    failed = _state(db_session, "fixtures_season", season_a)
    succeeded = _state(db_session, "fixtures_season", season_b)
    assert failed.last_outcome == "failed"
    assert failed.last_error_scope == "unit"
    assert failed.last_error_class == "ProviderResponseError"
    assert failed.last_error_status == 503
    assert failed.consecutive_failures == 1
    assert succeeded.last_outcome == "succeeded"
    assert _incidents(db_session) == []


def test_fixture_auth_failure_opens_incident_and_counts_unrequested_units(db_session, monkeypatch):
    _, season_a = make_competition(db_session, 265, name="A Liga")
    make_competition(db_session, 39, name="B Liga")
    monkeypatch.setattr(
        fixture_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265, 39]})(),
    )
    provider = RecoveryProvider()
    provider.fixture_failures[265] = ProviderAuthError(
        PROVIDER, "API key inválida", 401
    )

    result = _fixture_sync(db_session, provider)

    assert provider.calls == [("fixtures", 265)]
    assert len(result.competitions) == 2
    state = _state(db_session, "fixtures_season", season_a)
    assert state.last_outcome == "failed"
    assert state.last_error_scope == "provider"
    incident = _incidents(db_session)[0]
    assert incident.kind == "auth"
    assert incident.closed_at is None
    assert incident.failure_count == 1
    assert incident.affected_units == 2
    assert incident.last_endpoint == "/fixtures"


def test_success_closes_older_open_provider_incident(db_session, monkeypatch):
    _, season_id = make_competition(db_session, 265)
    monkeypatch.setattr(
        fixture_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265]})(),
    )
    recovery_repo.open_or_continue_incident(
        db_session,
        PROVIDER,
        ProviderAuthError(PROVIDER, "credencial rechazada", 401),
        endpoint="/fixtures",
        affected_units=1,
    )
    db_session.commit()

    provider = RecoveryProvider()
    provider.fixtures[265] = [make_fixture_data(1003)]
    _fixture_sync(db_session, provider)

    assert _state(db_session, "fixtures_season", season_id).last_outcome == "succeeded"
    incident = _incidents(db_session)[0]
    assert incident.closed_at is not None
    assert incident.close_reason == "provider_recovered"


def test_fixture_domain_rollback_does_not_leave_success_or_a6_evidence(db_session, monkeypatch):
    _, season_id = make_competition(db_session, 265)
    monkeypatch.setattr(
        fixture_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265]})(),
    )
    provider = RecoveryProvider()
    provider.fixtures[265] = [make_fixture_data(1004)]

    def fail_once(_db, result):
        if result.error is None:
            raise IntegrityError("audit", {}, Exception("simulado"))

    result = _fixture_sync(db_session, provider, on_competition=fail_once)

    assert result.competitions[0].error is not None
    state = _state(db_session, "fixtures_season", season_id)
    assert state.last_outcome == "failed"
    assert state.last_success_at is None
    assert state.last_error_scope == "database"
    assert db_session.scalar(select(func.count()).select_from(Fixture)) == 0
    assert db_session.scalar(select(func.count()).select_from(FixtureObservation)) == 0


def test_catalog_success_records_attempt_success_without_open_transaction_during_http(db_session, monkeypatch):
    monkeypatch.setattr(
        catalog_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265]})(),
    )
    provider = RecoveryProvider()
    provider.competitions = [
        CompetitionData(
            external_id=265,
            name="Liga",
            seasons=[SeasonData(year=2026, is_current=True)],
        )
    ]
    provider.teams[265] = [
        TeamData(external_id=1, name="A"),
        TeamData(external_id=2, name="B"),
    ]
    seen = []
    provider.on_team_call = lambda _league: seen.append(db_session.in_transaction())

    result = _catalog_sync(db_session, provider)

    season_id = db_session.scalars(
        select(SyncUnitState.season_id).where(
            SyncUnitState.unit_kind == "catalog_season_teams"
        )
    ).one()
    state = _state(db_session, "catalog_season_teams", season_id)
    assert result.teams_synced == 2
    assert seen == [False]
    assert state.last_outcome == "succeeded"
    assert state.last_success_at == state.last_outcome_at


def test_catalog_auth_failure_opens_incident_and_stops_later_team_requests(db_session, monkeypatch):
    monkeypatch.setattr(
        catalog_sync_service,
        "get_settings",
        lambda: type("S", (), {"tracked_league_ids": [265, 39]})(),
    )
    provider = RecoveryProvider()
    provider.competitions = [
        CompetitionData(
            external_id=265,
            name="A Liga",
            seasons=[SeasonData(year=2026, is_current=True)],
        ),
        CompetitionData(
            external_id=39,
            name="B Liga",
            seasons=[SeasonData(year=2026, is_current=True)],
        ),
    ]
    provider.team_failures[265] = ProviderAuthError(
        PROVIDER, "API key inválida", 401
    )

    result = _catalog_sync(db_session, provider)

    assert provider.calls == [("leagues", None), ("teams", 265)]
    assert sum(1 for c in result.competitions if c.error) == 2
    states = list(db_session.scalars(select(SyncUnitState)))
    assert len(states) == 1
    assert states[0].unit_kind == "catalog_season_teams"
    assert states[0].last_outcome == "failed"
    assert states[0].last_error_scope == "provider"
    incident = _incidents(db_session)[0]
    assert incident.kind == "auth"
    assert incident.affected_units == 2
    assert incident.last_endpoint == "/teams"


def test_persisted_error_message_is_sanitized_and_bounded():
    raw = (
        "request https://secret.example/x?token=abc "
        "api_key=supersecret password=hunter2 " + "x" * 600
    )
    safe = recovery_repo.safe_error_message(raw, "ProviderError")
    assert "secret.example" not in safe
    assert "supersecret" not in safe
    assert "hunter2" not in safe
    assert len(safe) <= 500
