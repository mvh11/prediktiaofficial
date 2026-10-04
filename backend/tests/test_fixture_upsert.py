"""Upsert no destructivo de fixtures: marcadores por pares, política según estado y updated_at."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

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
    """7. Un par entrante incompleto no se mezcla con el guardado: se conserva el par entero.

    Cubre goals, halftime, extratime y penalty, que llegan al DO UPDATE. fulltime no se incluye:
    ck_fixtures_fulltime_pair rechaza el medio par ya en la fila propuesta del INSERT (ver
    test_partial_fulltime_pair_is_rejected_by_database).
    """
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))
    half = dict(
        home_goals=5, away_goals=None, halftime_home=None, halftime_away=3, extratime_home=2, extratime_away=None,
        penalty_home=None, penalty_away=9,
    )
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **half))
    f = _fixture(db_session)
    for name, (home, away) in PAIRS.items():
        assert _pair(f, name) == (KNOWN_PEN[home], KNOWN_PEN[away]), name


def test_partial_fulltime_pair_is_rejected_by_database(db_session, season_id):
    """Un medio par de fulltime enviado directamente al repositorio (saltándose el adapter, que
    nunca lo entrega) lo rechaza PostgreSQL por ck_fixtures_fulltime_pair, y la fila guardada
    queda intacta tras el rollback."""
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))

    with pytest.raises(IntegrityError, match="ck_fixtures_fulltime_pair"):
        with db_session.begin_nested():
            _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **{**KNOWN_PEN, "fulltime_away": None, "fulltime_home": 7}))

    f = _fixture(db_session)
    assert f.status_short == "PEN"
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


def test_live_match_then_postponed_clears_scores(db_session, season_id):
    """Un partido en juego que pasa a PST con NULL: se limpia el marcador en vivo."""
    _upsert(db_session, season_id, make_fixture_data(1, status="1H", home_goals=1, away_goals=0))
    _upsert(db_session, season_id, make_fixture_data(1, status="PST"))
    assert _pair(_fixture(db_session), "goals") == (None, None)


@pytest.mark.parametrize("status", ["1H", "HT", "2H", "ET", "P"])
def test_live_match_partial_null_keeps_previous_score(db_session, season_id, status):
    """En vivo, una respuesta parcial con NULL no borra el marcador conocido (no es un estado que limpie)."""
    _upsert(db_session, season_id, make_fixture_data(1, status="1H", home_goals=1, away_goals=0, halftime_home=None))
    _upsert(db_session, season_id, make_fixture_data(1, status=status))
    f = _fixture(db_session)
    assert f.status_short == status and _pair(f, "goals") == (1, 0)


@pytest.mark.parametrize("status", ["SUSP", "INT"])
def test_live_match_suspended_or_interrupted_with_null_keeps_score(db_session, season_id, status):
    """Un partido suspendido/interrumpido ya tuvo minutos jugados: un NULL no borra el último marcador."""
    _upsert(db_session, season_id, make_fixture_data(1, status="2H", home_goals=2, away_goals=1, halftime_home=1, halftime_away=1))
    _upsert(db_session, season_id, make_fixture_data(1, status=status))
    f = _fixture(db_session)
    assert f.status_short == status
    assert (_pair(f, "goals"), _pair(f, "halftime")) == ((2, 1), (1, 1))


@pytest.mark.parametrize("status", ["PST", "CANC", "ABD", "TBD"])
def test_ft_match_back_to_pst_canc_abd_tbd_clears_scores(db_session, season_id, status):
    _upsert(db_session, season_id, make_fixture_data(
        1, status="FT", home_goals=2, away_goals=1, halftime_home=1, halftime_away=0, fulltime_home=2, fulltime_away=1
    ))
    _upsert(db_session, season_id, make_fixture_data(1, status=status))
    f = _fixture(db_session)
    for name in PAIRS:
        assert _pair(f, name) == (None, None), name


@pytest.mark.parametrize("status", ["FT", "AET", "AWD"])
def test_pen_corrected_to_non_pen_clears_penalty(db_session, season_id, status):
    """penalty_* solo es válido en PEN (o P, tanda en juego): una corrección PEN -> FT/AET/AWD con
    penalty NULL limpia la tanda en lugar de dejarla asociada a un partido sin penaltis."""
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))
    _upsert(db_session, season_id, make_fixture_data(1, status=status, home_goals=1, away_goals=1))
    f = _fixture(db_session)
    assert f.status_short == status
    assert _pair(f, "penalty") == (None, None)
    assert _pair(f, "goals") == (1, 1)


def test_complete_pair_incoherent_with_status_is_stored_as_received(db_session, season_id):
    """Un par COMPLETO se guarda tal como llega aunque contradiga el estado (FT con tanda y prórroga):
    es una anomalía explícita del proveedor que se audita, no se corrige en silencio."""
    _upsert(db_session, season_id, make_fixture_data(1, status="PEN", **KNOWN_PEN))
    _upsert(db_session, season_id, make_fixture_data(
        1, status="FT", home_goals=1, away_goals=1, penalty_home=5, penalty_away=4, extratime_home=1, extratime_away=1
    ))
    f = _fixture(db_session)
    assert f.status_short == "FT"
    assert (_pair(f, "penalty"), _pair(f, "extratime")) == ((5, 4), (1, 1))


def test_penalty_kept_during_live_shootout(db_session, season_id):
    """P (tanda en juego) es un estado en vivo de la tanda: un NULL parcial no la borra."""
    _upsert(db_session, season_id, make_fixture_data(1, status="P", home_goals=1, away_goals=1, penalty_home=2, penalty_away=1))
    _upsert(db_session, season_id, make_fixture_data(1, status="P"))
    assert _pair(_fixture(db_session), "penalty") == (2, 1)


def test_aet_corrected_to_ft_clears_extratime(db_session, season_id):
    """La prórroga no existe en un partido FT: AET -> FT con extratime NULL la limpia."""
    _upsert(db_session, season_id, make_fixture_data(
        1, status="AET", home_goals=3, away_goals=2, extratime_home=1, extratime_away=0, fulltime_home=2, fulltime_away=2
    ))
    _upsert(db_session, season_id, make_fixture_data(1, status="FT", home_goals=2, away_goals=2))
    f = _fixture(db_session)
    assert _pair(f, "extratime") == (None, None)
    assert _pair(f, "fulltime") == (2, 2)  # FT sigue siendo un estado con marcador a 90'


@pytest.mark.parametrize("status", ["AET", "PEN"])
def test_extratime_kept_in_aet_pen_with_null(db_session, season_id, status):
    _upsert(db_session, season_id, make_fixture_data(1, status=status, **KNOWN_PEN))
    _upsert(db_session, season_id, make_fixture_data(1, status=status))
    assert _pair(_fixture(db_session), "extratime") == (0, 0)


# --- Paridad: la predicción en Python coincide con el ON CONFLICT real -----------------------

PARITY_STORED = [
    dict(status="PEN", **KNOWN_PEN),
    dict(status="AET", home_goals=3, away_goals=2, extratime_home=1, extratime_away=0, fulltime_home=2, fulltime_away=2),
    dict(status="FT", home_goals=2, away_goals=1, halftime_home=1, halftime_away=0, fulltime_home=2, fulltime_away=1),
    dict(status="1H", home_goals=1, away_goals=0, halftime_home=None, halftime_away=None),
]
PARITY_INCOMING = [
    dict(status=s)  # todo NULL
    for s in ["FT", "AET", "PEN", "AWD", "WO", "1H", "HT", "2H", "ET", "P", "TBD", "SUSP", "INT", "NS", "PST", "CANC", "ABD"]
] + [
    dict(status="FT", home_goals=3, away_goals=1, fulltime_home=3, fulltime_away=1),  # pares completos
    dict(status="AWD", home_goals=0, away_goals=3),
    dict(status="PEN", home_goals=None, away_goals=None, penalty_home=5, penalty_away=4),
    dict(status="2H", home_goals=2, away_goals=None, extratime_home=1, extratime_away=None),  # medios pares
]


@pytest.mark.parametrize("stored", PARITY_STORED, ids=lambda d: f"de-{d['status']}")
@pytest.mark.parametrize("incoming", PARITY_INCOMING, ids=lambda d: "a-" + "-".join(f"{k}{v}" for k, v in d.items()))
def test_predicted_scores_match_real_upsert(db_session, season_id, stored, incoming):
    """predict_score_values (que usa el backfill para PARITY) debe dar exactamente lo que deja el upsert."""
    stored = {**stored}
    stored_status = stored.pop("status")
    incoming = {**incoming}
    incoming_status = incoming.pop("status")
    _upsert(db_session, season_id, make_fixture_data(1, status=stored_status, **stored))
    before = _fixture(db_session)
    before_values = {col: getattr(before, col) for pair in PAIRS.values() for col in pair}

    new = make_fixture_data(1, status=incoming_status, **incoming)
    predicted = fixture_repository.predict_score_values(before_values, new.model_dump())
    _upsert(db_session, season_id, new)
    after = _fixture(db_session)
    assert {col: getattr(after, col) for col in predicted} == predicted


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
