"""Servicio de backfill histórico contra PostgreSQL (branch de tests) con un proveedor falso.

Ningún test llama a la API: el proveedor falso devuelve FixtureData y la identidad
(liga, temporada) que "declararía" la respuesta.
"""

import asyncio
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select, update

from app.integrations.exceptions import ProviderResponseError
from app.integrations.football.base import FootballDataProvider
from app.models import (
    Fixture,
    FixtureProviderMapping,
    Season,
    SeasonBackfillRun,
    SeasonTeam,
    Team,
    TeamProviderMapping,
)
from app.repositories import catalog_repository
from app.schemas.catalog import SeasonData
from app.services import history_backfill_service as service
from tests.conftest import make_competition, make_evidence, make_fixture_data

pytestmark = pytest.mark.db

LEAGUE = 39
YEAR = 2025
NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)
END_DATE = date(2026, 5, 24)  # temporada 2025 cerrada respecto a NOW
# Rango amplio para las ejecuciones reales de los tests: el servicio lo exige y Q1 sigue
# bloqueando 0 partidos aunque el rango empiece en 0
WIDE_RANGE = (0, 10_000)
KICKOFF = datetime(2025, 9, 1, 19, 0, tzinfo=timezone.utc)


class FakeHistoryProvider(FootballDataProvider):
    name = "api-football"

    def __init__(self, fixtures=None, identity=None, error=None) -> None:
        self.fixtures = list(fixtures or [])
        self.identity_override = identity
        self.error = error
        self.calls: list[tuple[int, int]] = []
        self.last_fixture_identity = None

    async def check_status(self):
        raise NotImplementedError

    async def get_competitions(self, external_ids):
        return []

    async def get_teams(self, competition_external_id, season):
        return []

    async def get_fixtures(self, competition_external_id, season, date_from=None, date_to=None):
        self.calls.append((competition_external_id, season))
        if self.error:
            raise self.error
        self.last_fixture_identity = (
            self.identity_override
            if self.identity_override is not None
            else {f.external_id: (competition_external_id, season) for f in self.fixtures}
        )
        return list(self.fixtures)


def fx(external_id: int, home: int = 1, away: int = 2, **kwargs):
    values = {"status": "FT", "home_goals": 1, "away_goals": 0, "fulltime_home": 1, "fulltime_away": 0, "kickoff_at": KICKOFF}
    values.update(kwargs)
    return make_fixture_data(external_id, home=home, away=away, **values)


@pytest.fixture
def setup(db_session):
    """Competición con temporada actual 2026 y temporada histórica 2025 cerrada (vacías)."""
    competition_id, current_season_id = make_competition(db_session, LEAGUE, name="Premier Test", current_year=2026)
    seasons = catalog_repository.upsert_seasons(
        db_session, competition_id, [SeasonData(year=YEAR, end_date=END_DATE, is_current=False)]
    )
    db_session.flush()
    return {"competition_id": competition_id, "season_id": seasons[YEAR], "current_season_id": current_season_id}


def backfill(db, setup, provider, **kwargs):
    if not kwargs.get("dry_run"):
        kwargs.setdefault("expected_range", WIDE_RANGE)
    return asyncio.run(
        service.run_backfill(db, setup["competition_id"], kwargs.pop("year", YEAR), provider=provider, now=NOW, **kwargs)
    )


def count(db, model, *where) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model).where(*where))


def domain_counts(db) -> tuple:
    return (
        count(db, Fixture),
        count(db, Team),
        count(db, SeasonTeam),
        count(db, FixtureProviderMapping),
        count(db, TeamProviderMapping),
    )


def run_row(db, run_id: int) -> SeasonBackfillRun:
    db.expire_all()
    return db.get(SeasonBackfillRun, run_id)


# --- Precondiciones -----------------------------------------------------------------------------


def test_missing_season_is_blocked_and_not_created(db_session, setup):
    provider = FakeHistoryProvider([fx(1)])
    result = backfill(db_session, setup, provider, year=2019)
    assert result.status == "blocked" and result.season_id is None and "no existe" in result.error_message
    assert provider.calls == []  # ni siquiera se llama al proveedor
    run = run_row(db_session, result.run_id)
    assert (run.status, run.season_id, run.requested_year, run.finished_at is not None) == ("blocked", None, 2019, True)


def test_current_season_is_blocked(db_session, setup):
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1)]), year=2026)
    assert result.status == "blocked" and "temporada actual" in result.error_message


@pytest.mark.parametrize(
    ("end_date", "reason"),
    [
        (date(2027, 6, 1), "no está cerrada"),  # futura
        (NOW.date(), "no está cerrada"),  # termina hoy: todavía no es anterior a hoy
        (None, "no tiene end_date"),
    ],
)
def test_season_not_closed_is_blocked_without_writes(db_session, setup, end_date, reason):
    db_session.execute(update(Season).where(Season.id == setup["season_id"]).values(end_date=end_date))
    provider = FakeHistoryProvider([fx(1)])
    before = domain_counts(db_session)
    result = backfill(db_session, setup, provider)
    assert result.status == "blocked" and reason in result.error_message
    assert provider.calls == []  # ni siquiera se llama al proveedor
    assert domain_counts(db_session) == before
    run = run_row(db_session, result.run_id)
    assert (run.status, run.error_message) == ("blocked", result.error_message)


@pytest.mark.parametrize("dry_run", [True, False])
def test_closed_season_continues(db_session, setup, dry_run):
    provider = FakeHistoryProvider([fx(1)])
    result = backfill(db_session, setup, provider, dry_run=dry_run)
    assert result.status == ("dry_run_completed" if dry_run else "completed")
    assert provider.calls == [(LEAGUE, YEAR)]


@pytest.mark.parametrize("expected_range", [None, (391, 370)])
def test_real_run_without_valid_range_is_rejected_before_provider(db_session, setup, expected_range):
    provider = FakeHistoryProvider([fx(1)])
    before = domain_counts(db_session)
    with pytest.raises(ValueError, match="expected_range|Una ejecución real"):
        backfill(db_session, setup, provider, expected_range=expected_range)
    assert provider.calls == []
    assert domain_counts(db_session) == before
    assert count(db_session, SeasonBackfillRun) == 0  # ni siquiera se registra el run


def test_real_run_with_range_continues(db_session, setup):
    provider = FakeHistoryProvider([fx(1)])
    result = backfill(db_session, setup, provider, expected_range=(1, 1))
    assert result.status == "completed" and provider.calls == [(LEAGUE, YEAR)]


def test_dry_run_without_range_continues(db_session, setup):
    provider = FakeHistoryProvider([fx(1)])
    result = backfill(db_session, setup, provider, dry_run=True, expected_range=None)
    assert result.status == "dry_run_completed" and provider.calls == [(LEAGUE, YEAR)]


def test_unknown_competition_raises_without_run(db_session, setup):
    with pytest.raises(ValueError, match="No existe la competición"):
        asyncio.run(
            service.run_backfill(db_session, 999_999_999, YEAR, provider=FakeHistoryProvider(), expected_range=WIDE_RANGE)
        )
    assert count(db_session, SeasonBackfillRun) == 0


# --- Validación de la respuesta y protección cross-season -----------------------------------


@pytest.mark.parametrize("identity", [{1: (140, YEAR), 2: (LEAGUE, YEAR)}, {1: (LEAGUE, 2024), 2: (LEAGUE, YEAR)}])
def test_wrong_league_or_season_in_response_blocks_whole_pair(db_session, setup, identity):
    before = domain_counts(db_session)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)], identity=identity))
    assert result.status == "blocked" and "Q12" in result.error_message
    assert domain_counts(db_session) == before  # cero escrituras, ni siquiera del partido correcto


def test_external_id_in_other_season_blocks_before_upsert(db_session, setup):
    # El partido 1 ya existe en la temporada ACTUAL (2026)
    service_fixture = fx(1)
    from app.repositories import fixture_repository

    team_ids = fixture_repository.ensure_teams(db_session, [service_fixture.home_team, service_fixture.away_team], "api-football")
    fixture_repository.upsert_fixtures(db_session, setup["current_season_id"], [service_fixture], team_ids, "api-football", make_evidence())
    db_session.flush()
    before = domain_counts(db_session)

    result = backfill(db_session, setup, FakeHistoryProvider([fx(1, home_goals=9, away_goals=9, fulltime_home=9, fulltime_away=9), fx(2, home=3, away=4)]))

    assert result.status == "blocked"
    q12 = next(c for c in result.checks if c.id == "Q12")
    assert not q12.passed and 1 in q12.samples
    db_session.expire_all()
    stored = db_session.scalars(select(Fixture).where(Fixture.external_id == 1)).one()
    assert stored.season_id == setup["current_season_id"] and stored.home_goals == 1  # ni movido ni tocado
    assert domain_counts(db_session) == before


# --- Dry-run ---------------------------------------------------------------------------------


def test_dry_run_persists_nothing_but_the_run(db_session, setup):
    before = domain_counts(db_session)
    provider = FakeHistoryProvider([fx(1), fx(2, home=3, away=4), fx(3, home=5, away=6)])
    result = backfill(db_session, setup, provider, dry_run=True)

    assert result.status == "dry_run_completed"
    assert (result.received, result.new, result.existing, result.changed, result.unchanged) == (3, 3, 0, 0, 0)
    assert result.new_teams == 6 and result.new_season_teams == 6
    assert domain_counts(db_session) == before  # fixtures, teams, season_teams, mapeos: nada
    run = run_row(db_session, result.run_id)
    assert run.status == "dry_run_completed" and run.is_dry_run and run.new_count == 3
    assert any(c["id"] == "Q13" and "se crearían 3" in c["detail"] for c in run.checks)


# --- Ejecución real ---------------------------------------------------------------------------


def test_real_run_imports_pair_with_teams_season_teams_and_mappings(db_session, setup):
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))

    assert result.status == "completed", result.error_message
    assert (result.received, result.new, result.changed, result.unchanged) == (2, 2, 0, 0)
    assert count(db_session, Fixture, Fixture.season_id == setup["season_id"]) == 2
    assert count(db_session, SeasonTeam, SeasonTeam.season_id == setup["season_id"]) == 4
    assert count(db_session, FixtureProviderMapping, FixtureProviderMapping.external_id.in_(["1", "2"])) == 2
    assert count(db_session, TeamProviderMapping, TeamProviderMapping.external_id.in_(["1", "2", "3", "4"])) == 4
    run = run_row(db_session, result.run_id)
    assert run.status == "completed" and not run.is_dry_run
    assert {c["id"] for c in run.checks} >= {f"Q{i}" for i in range(1, 17)} | {"PARITY"}  # resumen persistido
    parity = next(c for c in run.checks if c["id"] == "PARITY")
    assert parity["passed"] and "2 comparados (nuevos 2, existentes 0)" in parity["detail"]
    assert [c["id"] for c in run.checks].count("Q3") == 1 and [c["id"] for c in run.checks].count("Q4") == 1


def test_completed_pair_is_not_repeated_without_refresh(db_session, setup):
    backfill(db_session, setup, FakeHistoryProvider([fx(1)]))
    provider = FakeHistoryProvider([fx(1)])
    result = backfill(db_session, setup, provider)
    assert result.status == "blocked" and "--refresh" in result.error_message
    assert provider.calls == []


def test_refresh_reevaluates_with_real_counters(db_session, setup):
    backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    refreshed = [
        fx(1),  # igual
        fx(2, home=3, away=4, home_goals=2, away_goals=2, fulltime_home=2, fulltime_away=2),  # corrección
        fx(3, home=5, away=6),  # nuevo
    ]

    dry = backfill(db_session, setup, FakeHistoryProvider(refreshed), refresh=True, dry_run=True)
    assert dry.status == "dry_run_completed"
    assert (dry.received, dry.new, dry.existing, dry.changed, dry.unchanged) == (3, 1, 2, 1, 1)
    assert count(db_session, Fixture) == 2  # dry-run + refresh no escribe

    real = backfill(db_session, setup, FakeHistoryProvider(refreshed), refresh=True)
    assert real.status == "completed"
    assert (real.new, real.changed, real.unchanged) == (1, 1, 1)
    parity = next(c for c in real.checks if c.id == "PARITY")  # la predicción coincide con la BD
    assert parity.passed and "3 comparados (nuevos 1, existentes 2)" in parity.detail
    db_session.expire_all()
    assert db_session.scalars(select(Fixture.home_goals).where(Fixture.external_id == 2)).one() == 2


# --- Warnings y bloqueos ----------------------------------------------------------------------


def test_warnings_do_not_block_and_anomalies_are_kept(db_session, setup):
    fixtures = [
        fx(1, status="AET", home_goals=2, away_goals=0, extratime_home=0, extratime_away=2, fulltime_home=4, fulltime_away=0),  # tipo 6570
        fx(2, home=3, away=4, home_goals=2, away_goals=1, fulltime_home=1, fulltime_away=1),  # Q10
        fx(3, home=5, away=6, halftime_home=2, halftime_away=0),  # Q11 (fulltime 1-0)
        fx(4, home=7, away=8, status="NS", home_goals=None, away_goals=None, fulltime_home=None, fulltime_away=None),  # Q14
    ]
    result = backfill(db_session, setup, FakeHistoryProvider(fixtures))
    failed = {c.id for c in result.checks if not c.passed}
    assert result.status == "completed"
    assert {"Q9", "Q10", "Q11", "Q14"} <= failed and result.warnings >= 4 and result.blocking == 0
    db_session.expire_all()
    stored = db_session.scalars(select(Fixture).where(Fixture.external_id == 1)).one()
    assert (stored.home_goals, stored.away_goals, stored.fulltime_home, stored.fulltime_away) == (2, 0, 4, 0)


def test_blocking_check_prevents_any_write(db_session, setup):
    before = domain_counts(db_session)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=7, away=7)]))  # Q15
    assert result.status == "blocked" and "Q15" in result.error_message and result.blocking >= 1
    assert domain_counts(db_session) == before


def test_zero_fixtures_blocks_with_q1(db_session, setup):
    result = backfill(db_session, setup, FakeHistoryProvider([]))
    assert result.status == "blocked" and "Q1" in result.error_message


# --- Fallos técnicos ----------------------------------------------------------------------


def test_db_error_rolls_back_whole_pair(db_session, setup, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def failing_link(*_args, **_kwargs):
        raise IntegrityError("INSERT season_teams", {}, Exception("simulado"))

    monkeypatch.setattr(service.catalog_repository, "link_teams_to_season", failing_link)
    before = domain_counts(db_session)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))

    assert result.status == "failed" and "IntegrityError" in result.error_message
    assert domain_counts(db_session) == before  # ni fixtures, ni equipos, ni mapeos a medias
    assert run_row(db_session, result.run_id).status == "failed"


def test_provider_error_marks_run_failed(db_session, setup):
    result = backfill(db_session, setup, FakeHistoryProvider(error=ProviderResponseError("api-football", "boom")))
    assert result.status == "failed" and "boom" in result.error_message
    assert run_row(db_session, result.run_id).status == "failed"


def test_unexpected_error_closes_run_as_failed_and_reraises(db_session, setup, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(service.fixture_repository, "upsert_fixtures", broken)
    with pytest.raises(RuntimeError):
        backfill(db_session, setup, FakeHistoryProvider([fx(1)]))
    run = db_session.scalars(select(SeasonBackfillRun).order_by(SeasonBackfillRun.id.desc())).first()
    assert run.status == "failed" and run.finished_at is not None
    assert count(db_session, Fixture) == 0
