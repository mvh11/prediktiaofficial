"""Sync de fixtures (M4.6C1/C2/C5) contra PostgreSQL con un proveedor falso: sin transacción durante
el HTTP, revalidación de la temporada, atomicidad por competición, contadores exactos y
elegibilidad del polling. Ningún test llama a la API."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.integrations.football.base import FootballDataProvider
from app.models import Fixture, Season, Team
from app.repositories import fixture_repository
from app.services import fixture_sync_service
from app.services.polling_eligibility import polling_eligibility
from tests.conftest import make_competition, make_fixture_data

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 5)


class FakeProvider(FootballDataProvider):
    name = "api-football"

    def __init__(self, fixtures=None, hook=None) -> None:
        self.fixtures: dict[int, list] = fixtures or {}
        self.hook = hook
        self.calls: list[int] = []

    async def check_status(self):
        raise NotImplementedError

    async def get_competitions(self, external_ids):
        return []

    async def get_teams(self, competition_external_id, season):
        return []

    async def get_fixtures(self, competition_external_id, season, date_from=None, date_to=None):
        self.calls.append(competition_external_id)
        if self.hook:
            self.hook(competition_external_id)
        return list(self.fixtures.get(competition_external_id, []))


def _sync(db, provider, **kwargs):
    return asyncio.run(fixture_sync_service.sync_fixtures(db, provider=provider, today=TODAY, **kwargs))


def _count(db, model, *where) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model).where(*where))


def ft(external_id, home=1, away=2, goals=(1, 0), **kw):
    return make_fixture_data(external_id, home=home, away=away, status="FT", home_goals=goals[0], away_goals=goals[1], fulltime_home=goals[0], fulltime_away=goals[1], **kw)


# --- C1: transacciones ---------------------------------------------------------------------


def test_no_db_transaction_is_open_during_provider_call(db_session):
    make_competition(db_session, 265)
    make_competition(db_session, 39, name="Otra")
    seen = []
    provider = FakeProvider({265: [ft(1)], 39: [ft(2, home=3, away=4)]}, hook=lambda _l: seen.append(db_session.in_transaction()))
    result = _sync(db_session, provider)
    assert seen == [False, False]
    assert result.fixtures_synced == 2


def test_season_changed_during_download_writes_nothing(db_session):
    cid, sid = make_competition(db_session, 265, current_year=2026)

    def flip(_league):
        # La sync del catálogo promueve otra temporada mientras descargamos
        db_session.execute(update(Season).where(Season.id == sid).values(is_current=False))
        db_session.add(Season(competition_id=cid, year=2027, is_current=True))
        db_session.commit()

    provider = FakeProvider({265: [ft(1), ft(2, home=3, away=4)]}, hook=flip)
    teams_before = _count(db_session, Team)
    result = _sync(db_session, provider)
    comp = result.competitions[0]
    assert comp.skipped.startswith("season changed") and comp.error is None and comp.fixtures == 0
    assert _count(db_session, Fixture) == 0 and _count(db_session, Team) == teams_before


def test_failure_in_one_competition_does_not_contaminate_the_next(db_session, monkeypatch):
    make_competition(db_session, 265)
    make_competition(db_session, 39, name="Otra")
    real = fixture_repository.upsert_fixtures
    calls = {"n": 0}

    def flaky(db, season_id, fixtures, team_ids, provider):
        calls["n"] += 1
        if calls["n"] == 1:
            real(db, season_id, fixtures, team_ids, provider)  # escribe y luego falla: debe deshacerse
            raise IntegrityError("INSERT fixtures", {}, Exception("simulado"))
        return real(db, season_id, fixtures, team_ids, provider)

    monkeypatch.setattr(fixture_sync_service.fixture_repository, "upsert_fixtures", flaky)
    audited = []
    result = _sync(db_session, FakeProvider({265: [ft(1)], 39: [ft(2, home=3, away=4)]}), on_competition=lambda _db, r: audited.append(r.model_dump()))
    by_league = {c.name: c for c in result.competitions}
    first, second = result.competitions
    assert first.error and "IntegrityError" in first.error and first.fixtures == 0
    assert second.error is None and second.created == 1
    assert _count(db_session, Fixture) == 1  # solo la segunda competición
    assert [a["error"] is not None for a in audited] == [True, False] and len(by_league) == 2


def test_normal_behaviour_and_audit_hook_inside_the_competition_transaction(db_session):
    make_competition(db_session, 265)
    states = []

    def hook(db, result):
        # Se llama antes del commit: los partidos de la competición ya están escritos en la transacción
        states.append((result.created, db.scalar(select(func.count()).select_from(Fixture))))

    result = _sync(db_session, FakeProvider({265: [ft(1), ft(2, home=3, away=4)]}), on_competition=hook)
    comp = result.competitions[0]
    assert (comp.fixtures, comp.created, comp.updated, comp.unchanged, comp.teams_created) == (2, 2, 0, 0, 4)
    assert states == [(2, 2)]


# --- C2: contadores --------------------------------------------------------------------------


def test_counters_insert_update_replay_and_mixed_batch(db_session):
    make_competition(db_session, 265)
    provider = FakeProvider({265: [ft(1), ft(2, home=3, away=4)]})

    first = _sync(db_session, provider).competitions[0]
    assert (first.created, first.updated, first.unchanged) == (2, 0, 0)

    replay = _sync(db_session, provider).competitions[0]
    assert (replay.created, replay.updated, replay.unchanged, replay.teams_created) == (0, 0, 2, 0)

    provider.fixtures[265] = [ft(1, goals=(3, 3)), ft(2, home=3, away=4), ft(3, home=5, away=6)]
    mixed = _sync(db_session, provider).competitions[0]
    assert (mixed.fixtures, mixed.created, mixed.updated, mixed.unchanged, mixed.teams_created) == (3, 1, 1, 1, 2)
    for c in (first, replay, mixed):
        assert c.fixtures == c.created + c.updated + c.unchanged


def test_repository_counters_ignore_duplicates_and_do_not_depend_on_updated_at(db_session):
    _, sid = make_competition(db_session, 265)
    fixtures = [ft(1), ft(1), ft(2, home=3, away=4)]  # el 1 llega duplicado
    team_ids = fixture_repository.ensure_teams(db_session, [f.home_team for f in fixtures] + [f.away_team for f in fixtures], "api-football")
    counts = fixture_repository.upsert_fixtures(db_session, sid, fixtures, team_ids, "api-football")
    assert (counts.received, counts.created, counts.updated, counts.unchanged) == (2, 2, 0, 0)
    # Mover updated_at a mano no cambia los contadores: no se usan
    db_session.execute(update(Fixture).values(updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc)))
    again = fixture_repository.upsert_fixtures(db_session, sid, fixtures, team_ids, "api-football")
    assert (again.received, again.created, again.updated, again.unchanged) == (2, 0, 0, 2)


def test_empty_response_has_zero_counters(db_session):
    make_competition(db_session, 265)
    comp = _sync(db_session, FakeProvider({265: []})).competitions[0]
    assert (comp.fixtures, comp.created, comp.updated, comp.unchanged, comp.error) == (0, 0, 0, 0, None)


# --- C5: elegibilidad del polling ----------------------------------------------------------


def _set_dates(db, sid, start, end):
    db.execute(update(Season).where(Season.id == sid).values(start_date=start, end_date=end))
    db.flush()


def test_dormant_current_season_is_skipped_without_calling_the_provider(db_session):
    _, sid = make_competition(db_session, 4, name="Torneo cerrado", current_year=2024)
    _set_dates(db_session, sid, date(2024, 6, 14), date(2024, 7, 14))
    team_ids = fixture_repository.ensure_teams(db_session, [ft(1).home_team, ft(1).away_team], "api-football")
    fixture_repository.upsert_fixtures(db_session, sid, [ft(1)], team_ids, "api-football")
    provider = FakeProvider({4: [ft(1)]})
    comp = _sync(db_session, provider).competitions[0]
    assert provider.calls == [] and comp.skipped.startswith("dormant") and comp.error is None
    assert db_session.scalar(select(Season.is_current).where(Season.id == sid)) is True  # el dato del proveedor no se toca


@pytest.mark.parametrize(
    ("start", "end", "fixtures", "eligible"),
    [
        (None, None, [], True),  # sin end_date
        (date(2026, 1, 1), date(2026, 12, 1), [], True),  # en curso
        (date(2026, 1, 1), TODAY - timedelta(days=14), ["FT"], True),  # recién terminada (límite)
        (date(2026, 1, 1), TODAY - timedelta(days=15), ["FT"], False),  # cerrada y sin pendientes
        (date(2026, 1, 1), TODAY - timedelta(days=60), ["FT", "PST"], True),  # tiene un partido no final
        (date(2026, 1, 1), TODAY - timedelta(days=60), ["FT", "CANC", "AWD"], False),  # terminales
        (TODAY + timedelta(days=20), TODAY - timedelta(days=60), [], True),  # sin partidos, empieza pronto
        (TODAY - timedelta(days=400), TODAY - timedelta(days=60), [], False),  # sin partidos y cerrada
    ],
)
def test_polling_eligibility_rule(db_session, start, end, fixtures, eligible):
    _, sid = make_competition(db_session, 4, current_year=2024)
    _set_dates(db_session, sid, start, end)
    data = [make_fixture_data(i + 1, home=2 * i + 1, away=2 * i + 2, status=s) for i, s in enumerate(fixtures)]
    if data:
        team_ids = fixture_repository.ensure_teams(db_session, [f.home_team for f in data] + [f.away_team for f in data], "api-football")
        fixture_repository.upsert_fixtures(db_session, sid, data, team_ids, "api-football")
    assert polling_eligibility(db_session, sid, TODAY).eligible is eligible
