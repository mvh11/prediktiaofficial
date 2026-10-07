"""Membresía de season_teams en el backfill histórico, contra PostgreSQL (branch de tests).

Liga sintética de 18 equipos a doble vuelta (306 partidos) más una promoción a doble partido
contra un club de una división inferior (99): 19 participantes, 18 miembros. Ningún test llama
a la API.
"""

import pytest
from sqlalchemy import or_, select, update

from app.models import Competition, Fixture, SeasonTeam, Team, TeamProviderMapping
from app.services import history_backfill_service as service
from app.services.season_membership import SeasonMembership
from tests.test_history_backfill import (  # noqa: F401  (setup es una fixture de pytest)
    FakeHistoryProvider,
    backfill,
    count,
    domain_counts,
    fx,
    run_row,
    setup,
)

pytestmark = pytest.mark.db

LEAGUE18 = list(range(1, 19))
EXTERNAL = 99


def round_robin(teams: list[int], prefix: str = "Regular Season", double: bool = True, first_id: int = 1) -> list:
    rotation, n, pairs = list(teams), len(teams), []
    for r in range(n - 1):
        pairs += [(rotation[i], rotation[n - 1 - i], r + 1) for i in range(n // 2)]
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    if double:
        pairs += [(away, home, r + n - 1) for home, away, r in pairs]
    return [fx(first_id + i, home=h, away=a, round=f"{prefix} - {r}") for i, (h, a, r) in enumerate(pairs)]


def league_with_promotion(playoff_round: str = "Relegation Round") -> list:
    fixtures = round_robin(LEAGUE18)
    return fixtures + [
        fx(10_001, home=16, away=EXTERNAL, round=playoff_round),
        fx(10_002, home=EXTERNAL, away=16, round=playoff_round),
    ]


@pytest.fixture
def league(db_session, setup):
    db_session.execute(update(Competition).where(Competition.id == setup["competition_id"]).values(type="League"))
    db_session.flush()
    return setup


def season_team_external_ids(db, season_id: int) -> set[int]:
    db.expire_all()
    stmt = select(Team.external_id).join(SeasonTeam, SeasonTeam.team_id == Team.id).where(SeasonTeam.season_id == season_id)
    return set(db.scalars(stmt))


def q16(checks) -> dict:
    return next(c if isinstance(c, dict) else c.model_dump() for c in checks if (c["id"] if isinstance(c, dict) else c.id) == "Q16")


# --- Dry-run ----------------------------------------------------------------------------------


def test_dry_run_counts_only_members_as_new_season_teams(db_session, league):
    before = domain_counts(db_session)
    result = backfill(db_session, league, FakeHistoryProvider(league_with_promotion()), dry_run=True)

    assert result.status == "dry_run_completed" and result.received == 308
    assert result.new_teams == 19  # hay que crear a los 19 participantes para guardar los 308 partidos
    assert result.new_season_teams == 18  # pero solo 18 son miembros de la temporada
    check = q16(result.checks)
    assert check["passed"] and check["count"] == 1 and check["samples"] == [EXTERNAL]
    assert result.warnings == 0 and result.blocking == 0
    assert domain_counts(db_session) == before  # cero escrituras de dominio
    assert q16(run_row(db_session, result.run_id).checks)["samples"] == [EXTERNAL]  # auditado en el run


# --- Ejecución real ------------------------------------------------------------------------------


def test_real_run_keeps_all_fixtures_and_links_only_members(db_session, league):
    result = backfill(db_session, league, FakeHistoryProvider(league_with_promotion()))
    assert result.status == "completed", result.error_message
    assert (result.received, result.new, result.new_teams, result.new_season_teams) == (308, 308, 19, 18)

    season_id = league["season_id"]
    assert count(db_session, Fixture, Fixture.season_id == season_id) == 308  # ningún partido filtrado
    participants = set(LEAGUE18) | {EXTERNAL}
    assert set(db_session.scalars(select(Team.external_id).where(Team.external_id.in_(participants)))) == participants
    mapped = db_session.scalars(
        select(TeamProviderMapping.external_id).where(
            TeamProviderMapping.provider == "api-football",
            TeamProviderMapping.external_id.in_([str(t) for t in participants]),
        )
    )
    assert set(mapped) == {str(t) for t in participants}

    external_id = db_session.scalars(select(Team.id).where(Team.external_id == EXTERNAL)).one()
    external_fixtures = count(
        db_session, Fixture, Fixture.season_id == season_id, or_(Fixture.home_team_id == external_id, Fixture.away_team_id == external_id)
    )
    assert external_fixtures == 2  # el club externo aparece en sus partidos
    assert season_team_external_ids(db_session, season_id) == set(LEAGUE18)  # exactamente los miembros


def test_refresh_does_not_add_the_external_club_and_is_idempotent(db_session, league):
    first = backfill(db_session, league, FakeHistoryProvider(league_with_promotion()))
    assert first.status == "completed"
    before = domain_counts(db_session)

    again = backfill(db_session, league, FakeHistoryProvider(league_with_promotion()), refresh=True)
    assert again.status == "completed", again.error_message
    assert (again.new, again.existing, again.changed, again.new_season_teams) == (0, 308, 0, 0)
    assert domain_counts(db_session) == before
    assert season_team_external_ids(db_session, league["season_id"]) == set(LEAGUE18)


def test_membership_safeguard_blocks_before_any_write(db_session, league, monkeypatch):
    def absurd(fixtures, competition_type):
        participants = frozenset(t for f in fixtures for t in (f.home_team.external_id, f.away_team.external_id))
        return SeasonMembership(
            competition_type=competition_type,
            participants=participants,
            members=frozenset({1, 2}),
            excluded=participants - {1, 2},
            ambiguous=False,
            opponent_counts={},
            median_opponents=None,
            threshold=None,
            numbered_members=None,
        )

    monkeypatch.setattr(service, "season_members", absurd)
    before = domain_counts(db_session)
    result = backfill(db_session, league, FakeHistoryProvider(league_with_promotion()))
    assert result.status == "blocked" and "Q16" in result.error_message
    assert domain_counts(db_session) == before


# --- Regresión M4.3 ------------------------------------------------------------------------------


def test_pure_league_of_20_links_all_20(db_session, league):
    """Premier League 2025: 20 participantes, 20 miembros."""
    result = backfill(db_session, league, FakeHistoryProvider(round_robin(list(range(1, 21)))))
    assert result.status == "completed" and result.received == 380 and result.new_season_teams == 20
    assert season_team_external_ids(db_session, league["season_id"]) == set(range(1, 21))
    assert q16(result.checks)["count"] == 0


def test_apertura_clausura_links_its_18(db_session, league):
    """Liga MX 2025: Apertura y Clausura a una vuelta más liguilla interna: 18 miembros."""
    fixtures = (
        round_robin(LEAGUE18, prefix="Apertura", double=False)
        + round_robin(LEAGUE18, prefix="Clausura", double=False, first_id=1_001)
        + [fx(2_001, home=1, away=8, round="Apertura - Quarter-finals"), fx(2_002, home=8, away=1, round="Apertura - Quarter-finals")]
    )
    result = backfill(db_session, league, FakeHistoryProvider(fixtures))
    assert result.status == "completed" and result.new_season_teams == 18
    assert season_team_external_ids(db_session, league["season_id"]) == set(LEAGUE18)
