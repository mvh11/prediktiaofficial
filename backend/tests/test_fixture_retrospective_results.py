"""RETROSPECTIVE_FINAL_RESULTS frente a STRICT_KNOWLEDGE (DI-A6, Checkpoint B): dos modos que no
se mezclan ni se sustituyen."""

import inspect
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Fixture
from app.repositories import fixture_knowledge_repository, fixture_retrospective_repository
from app.repositories.fixture_knowledge_repository import strict_knowledge, strict_knowledge_at
from app.repositories.fixture_retrospective_repository import retrospective_final_results
from app.schemas.fixture_knowledge import EvaluationMode, FixtureKnowledge, RetrospectiveFinalResult
from tests.conftest import make_fixture_data
from tests.test_fixture_observations import T0, at, season_id, upsert  # noqa: F401

pytestmark = pytest.mark.db

FT_PARTIAL = dict(status="FT", home_goals=2, away_goals=1)
FT_FULL = dict(status="FT", home_goals=2, away_goals=1, halftime_home=1, halftime_away=0, fulltime_home=2, fulltime_away=1)


def fid(db, external_id: int = 1) -> int:
    return db.scalar(select(Fixture.id).where(Fixture.external_id == external_id))


def test_modes_are_behaviorally_separate(db_session, season_id):
    """En T solo había una respuesta parcial; hoy fixtures tiene el resultado completo. Cada modo
    da lo suyo, con su tipo y su marca, y ninguno toma valores del otro."""
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_PARTIAL))
    upsert(db_session, season_id, at(20), make_fixture_data(1, **FT_FULL))
    f = fid(db_session)

    strict = strict_knowledge_at(db_session, f, T0 + timedelta(minutes=10))
    retro = retrospective_final_results(db_session, [f])[f]
    assert (strict.mode, retro.mode) == (EvaluationMode.STRICT_KNOWLEDGE, EvaluationMode.RETROSPECTIVE_FINAL_RESULTS)
    assert not isinstance(retro, FixtureKnowledge) and not hasattr(retro, "known_at")
    assert (strict.state.fulltime_home, strict.state.halftime_home) == (None, None)  # lo observado en T
    assert (retro.fulltime_home, retro.fulltime_away, retro.halftime_home) == (2, 1, 1)  # lo de hoy


def test_strict_never_falls_back_to_current_final_result(db_session, season_id):
    upsert(db_session, season_id, at(30), make_fixture_data(1, **FT_FULL))
    f = fid(db_session)
    assert retrospective_final_results(db_session, [f])[f].home_goals == 2  # hay resultado final hoy...
    report = strict_knowledge(db_session, [f], T0)
    assert report.known == {} and report.unknown_at_t == [f]  # ...pero en T no se sabía nada
    assert report.results[f].state is None


def test_retrospective_lists_only_final_states(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_FULL), make_fixture_data(2, home=3, away=4, status="NS"))
    results = retrospective_final_results(db_session, [fid(db_session, 1), fid(db_session, 2)])
    assert list(results) == [fid(db_session, 1)] and isinstance(results[fid(db_session, 1)], RetrospectiveFinalResult)
    assert retrospective_final_results(db_session, []) == {}


def test_entrypoints_cannot_be_confused():
    """El modo retrospectivo no acepta corte; el estricto lo exige. Y el módulo estricto no conoce
    fixtures ni el modo retrospectivo, así que no puede recurrir a ellos."""
    assert "cutoff" not in inspect.signature(retrospective_final_results).parameters
    assert "cutoff" in inspect.signature(strict_knowledge).parameters
    source = Path(fixture_knowledge_repository.__file__).read_text(encoding="utf-8")
    assert "retrospective" not in source.lower()
    assert "import FixtureObservation\n" in source and "Fixture," not in source and "import Fixture\n" not in source
    assert "FixtureObservation" not in Path(fixture_retrospective_repository.__file__).read_text(encoding="utf-8")
