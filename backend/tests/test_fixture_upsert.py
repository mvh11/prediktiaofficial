"""Upsert no destructivo de fixtures: marcadores por pares, política según estado y updated_at."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from app.models import Fixture, FixtureProviderMapping
from app.repositories import fixture_repository
from tests.conftest import make_competition, make_fixture_data

pytestmark = pytest.mark.db

PAIRS = {
    "goals": ("home_goals", "away_goals"),
    "halftime": ("halftime_home", "halftime_away"),
    "extratime": ("extratime_home", "extratime_away"),
    "penalty": ("penalty_home", "penalty_away"),
    "fulltime": ("fulltime_home", "fulltime_away"),
}
# Un PEN con prórroga: todos los pares tienen valor
KNOWN_PEN = dict(
    home_goals=1, away_goals=1, halftime_home=0, halftime_away=1, extratime_home=0, extratime_away=0,
    penalty_home=4, penalty_away=3, fulltime_home=1, fulltime_away=1,
)


@pytest.fixture
def season_id(db_session) -> int:
    _, season = make_competition(db_session, 265)
    return season


def _upsert(db, season_id: int, *fixtures) -> None:
    teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
    team_ids = fixture_repository.ensure_teams(db, teams, "api-football")
    fixture_repository.upsert_fixtures(db, season_id, list(fixtures), team_ids, "api-football")
    db.flush()


def _fixture(db, external_id: int = 1) -> Fixture:
    db.expire_all()
    return db.scalars(select(Fixture).where(Fixture.external_id == external_id)).one()


def _pair(f: Fixture, name: str) -> tuple:
    home, away = PAIRS[name]
    return getattr(f, home), getattr(f, away)


@pytest.mark.parametrize("pair", ["goals", "halftime", "extratime", "penalty", "fulltime"])
def test_null_in_final_match_keeps_known_pair(db_session, season_id, pair):
    """1-5. Una respuesta parcial (pares NULL) de un partido final no borra lo conocido."""
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", referee="Nuevo árbitro"))

    f = _fixture(db_session)
    home, away = PAIRS[pair]
    assert _pair(f, pair) == (KNOWN_PEN[home], KNOWN_PEN[away])
    assert f.referee == "Nuevo árbitro"  # la metadata sí se actualiza


def test_null_keeps_known_scores_in_awd(db_session, season_id):
    """AWD también es final: un NULL no borra el resultado administrativo."""
    _upsert(db_session, season_id, make_fixture_data(1, status="AWD", home_goals=0, away_goals=3))
    _upsert(db_session, season_id, make_fixture_data(1, status="AWD"))
    f = _fixture(db_session)
    assert _pair(f, "goals") == (0, 3) and _pair(f, "fulltime") == (None, None)


def test_complete_new_pairs_correct_stored_values(db_session, season_id):
    """6. Un par completo nuevo corrige el guardado (corrección real del proveedor)."""
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))
    corrected = dict(
        home_goals=2, away_goals=2, halftime_home=1, halftime_away=1, extratime_home=1, extratime_away=1,
        penalty_home=5, penalty_away=4, fulltime_home=1, fulltime_away=1,
    )
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **corrected))
    f = _fixture(db_session)
    assert {c: getattr(f, c) for c in corrected} == corrected


def test_never_mixes_half_pairs(db_session, season_id):
    """7. Un par entrante incompleto no se mezcla con el guardado: se conserva el par entero."""
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))
    half = dict(
        home_goals=5, away_goals=None, halftime_home=None, halftime_away=3, extratime_home=2, extratime_away=None,
        penalty_home=None, penalty_away=9, fulltime_home=7, fulltime_away=None,
    )
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **half))
    f = _fixture(db_session)
    for name, (home, away) in PAIRS.items():
        assert _pair(f, name) == (KNOWN_PEN[home], KNOWN_PEN[away]), name


@pytest.mark.parametrize("status", ["NS", "PST", "CANC", "ABD"])
def test_match_back_to_non_final_state_clears_scores(db_session, season_id, status):
    """8-9. Si el partido deja de ser final, no se conservan marcadores artificialmente
    y fulltime pasa a NULL."""
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))
    _upsert(db_session, season_id, make_fixture_data(1, status=status))
    f = _fixture(db_session)
    assert f.status_short == status
    for name in PAIRS:
        assert _pair(f, name) == (None, None), name


def test_fulltime_cleared_when_no_longer_ft_aet_pen(db_session, season_id):
    """9. FT -> AWD: goals pasa al resultado administrativo y fulltime a NULL."""
    _upsert(db_session, season_id, make_fixture_data(1, status="FT", home_goals=2, away_goals=1, fulltime_home=2, fulltime_away=1))
    _upsert(db_session, season_id, make_fixture_data(1, status="AWD", home_goals=0, away_goals=3))
    f = _fixture(db_session)
    assert _pair(f, "goals") == (0, 3) and _pair(f, "fulltime") == (None, None)


def test_live_match_partial_scores_not_preserved(db_session, season_id):
    """Un estado en juego no es final: un NULL entrante sustituye al marcador en vivo."""
    _upsert(db_session, season_id, make_fixture_data(1, status="1H", home_goals=1, away_goals=0))
    _upsert(db_session, season_id, make_fixture_data(1, status="PST"))
    assert _pair(_fixture(db_session), "goals") == (None, None)


def test_identical_resync_does_not_rewrite_fixture(db_session, season_id):
    """10-11. Resync idéntico: no toca fixtures.updated_at, pero sí last_seen_at del mapeo."""
    data = make_fixture_data(1, status="FT", home_goals=1, away_goals=0, fulltime_home=1, fulltime_away=0)
    _upsert(db_session, season_id, data)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.execute(update(Fixture).values(updated_at=old))
    db_session.execute(update(FixtureProviderMapping).values(last_seen_at=old))

    _upsert(db_session, season_id, data)
    assert _fixture(db_session).updated_at == old
    db_session.expire_all()
    assert db_session.scalars(select(FixtureProviderMapping)).one().last_seen_at > old

    _upsert(db_session, season_id, make_fixture_data(1, status="FT", home_goals=2, away_goals=0, fulltime_home=2, fulltime_away=0))
    assert _fixture(db_session).updated_at > old


def test_provider_incoherence_stored_as_is(db_session, season_id):
    """Caso tipo 6570: se guarda lo que da el proveedor; lo detectarán los controles de calidad."""
    _upsert(db_session, season_id, make_fixture_data(
        1, status="AET", home_goals=2, away_goals=0, extratime_home=0, extratime_away=2, fulltime_home=4, fulltime_away=0
    ))
    f = _fixture(db_session)
    assert (_pair(f, "goals"), _pair(f, "extratime"), _pair(f, "fulltime")) == ((2, 0), (0, 2), (4, 0))
