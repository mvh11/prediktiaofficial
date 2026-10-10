"""Lectura STRICT_KNOWLEDGE de fixtures en un corte T (DI-A6, Checkpoint B).

La evidencia se escribe con el escritor real (upsert_fixtures), así que los casos son los que
produce la aplicación: confirmaciones, evidencia antigua, empates de instante y respuestas parciales.
"""

from datetime import timedelta, timezone

import pytest
from sqlalchemy import event, select, text

from app.models import Fixture, FixtureObservation
from app.repositories.fixture_knowledge_repository import strict_knowledge, strict_knowledge_at
from app.repositories.fixture_repository import STATE_COLUMNS, state_hashes
from app.schemas.fixture_knowledge import EvaluationMode, FixtureKnowledge, KnowledgeStatus
from tests.conftest import make_evidence, make_fixture_data
from tests.test_fixture_observations import FT_1_0, FT_2_0, T0, at, observations, row_state_of, season_id, upsert  # noqa: F401

pytestmark = pytest.mark.db

KNOWN, UNKNOWN, AMBIGUOUS = KnowledgeStatus.KNOWN, KnowledgeStatus.UNKNOWN_AT_T, KnowledgeStatus.TEMPORAL_AMBIGUITY
# FT con marcador 2-1 pero sin descanso ni marcador a 90': respuesta parcial
FT_PARTIAL = dict(status="FT", home_goals=2, away_goals=1)
FT_FULL = dict(status="FT", home_goals=2, away_goals=1, halftime_home=1, halftime_away=0, fulltime_home=2, fulltime_away=1)


def T(minutes: float):
    return T0 + timedelta(minutes=minutes)


def fid(db, external_id: int = 1) -> int:
    return db.scalar(select(Fixture.id).where(Fixture.external_id == external_id))


def current(db, external_id: int = 1) -> Fixture:
    db.expire_all()
    return db.scalars(select(Fixture).where(Fixture.external_id == external_id)).one()


def state_of(knowledge: FixtureKnowledge) -> dict:
    return {c: getattr(knowledge.state, c) for c in STATE_COLUMNS}


def as_stored(o: FixtureObservation) -> dict:
    return {c: getattr(o, c) for c in STATE_COLUMNS}


# --- UNKNOWN_AT_T y KNOWN ----------------------------------------------------------------------


def test_no_evidence_before_cutoff_is_unknown(db_session, season_id):
    upsert(db_session, season_id, at(10), make_fixture_data(1, **FT_1_0))
    k = strict_knowledge_at(db_session, fid(db_session), T(5))
    assert (k.status, k.known_at, k.observations, k.state) == (UNKNOWN, None, (), None)


def test_ids_without_any_observation_are_unknown(db_session, season_id):
    report = strict_knowledge(db_session, [987654321], T(60))
    assert report.unknown_at_t == [987654321] and report.known == {}


def test_one_observation_before_cutoff_is_known_as_observed(db_session, season_id):
    ev = at(0)
    upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0))
    [o] = observations(db_session)
    k = strict_knowledge_at(db_session, fid(db_session), T(5))
    assert (k.status, k.known_at) == (KNOWN, ev.observed_at)
    s = k.state
    assert (s.observation_id, s.evidence_id, s.source, s.provider, s.state_hash) == (o.id, ev.evidence_id, "sync", "api-football", bytes(o.state_hash))
    assert state_of(k) == as_stored(o)


def test_latest_observation_at_or_before_cutoff_wins(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, status="NS"))
    upsert(db_session, season_id, at(10), make_fixture_data(1, status="1H", home_goals=1, away_goals=0))
    upsert(db_session, season_id, at(20), make_fixture_data(1, **FT_2_0))
    f = fid(db_session)
    assert strict_knowledge_at(db_session, f, T(5)).state.status_short == "NS"
    assert (strict_knowledge_at(db_session, f, T(15)).state.status_short, strict_knowledge_at(db_session, f, T(15)).state.home_goals) == ("1H", 1)
    assert strict_knowledge_at(db_session, f, T(25)).state.home_goals == 2


def test_observation_exactly_at_cutoff_is_included(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, status="NS"))
    ev = at(10)
    upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0))
    k = strict_knowledge_at(db_session, fid(db_session), ev.observed_at)
    assert (k.known_at, k.state.status_short) == (ev.observed_at, "FT")


def test_observations_after_cutoff_are_excluded(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, status="NS"))
    upsert(db_session, season_id, at(10), make_fixture_data(1, **FT_1_0))
    k = strict_knowledge_at(db_session, fid(db_session), T(10) - timedelta(microseconds=1))
    assert (k.known_at, k.state.status_short) == (T(0), "NS")


def test_cutoff_is_an_instant_whatever_its_timezone(db_session, season_id):
    upsert(db_session, season_id, at(10), make_fixture_data(1, **FT_1_0))
    local = T(10).astimezone(timezone(timedelta(hours=-3)))  # el mismo instante en -03:00
    assert strict_knowledge_at(db_session, fid(db_session), local).status is KNOWN
    with pytest.raises(ValueError):
        strict_knowledge_at(db_session, fid(db_session), T(10).replace(tzinfo=None))


# --- Mismo instante: el mismo estado es KNOWN, estados distintos son TEMPORAL_AMBIGUITY ----------


def test_equal_instant_identical_state_is_known(db_session, season_id):
    first = at(0)
    upsert(db_session, season_id, first, make_fixture_data(1, **FT_1_0))
    other = make_evidence(observed_at=first.observed_at)  # otra respuesta, mismo instante y estado
    upsert(db_session, season_id, other, make_fixture_data(1, **FT_1_0))
    k = strict_knowledge_at(db_session, fid(db_session), T(5))
    assert k.status is KNOWN and len(k.observations) == 2
    assert {o.evidence_id for o in k.observations} == {first.evidence_id, other.evidence_id}
    assert len({o.state_hash for o in k.observations}) == 1 and k.state.home_goals == 1


@pytest.mark.parametrize("larger_first", [True, False])
def test_equal_instant_conflicting_states_is_temporal_ambiguity(db_session, season_id, larger_first):
    states = [make_fixture_data(1, **FT_1_0), make_fixture_data(1, **FT_2_0)]
    hashes = state_hashes(db_session, [row_state_of(db_session, season_id, s) for s in states])
    larger = max(range(2), key=lambda i: hashes[i])
    for i in ([larger, 1 - larger] if larger_first else [1 - larger, larger]):
        upsert(db_session, season_id, make_evidence(observed_at=T(0)), states[i])

    k = strict_knowledge_at(db_session, fid(db_session), T(5))
    assert (k.status, k.known_at, k.state) == (AMBIGUOUS, T(0), None)  # nunca un ganador por hash
    assert {o.state_hash for o in k.observations} == set(hashes)  # las dos, conservadas para auditoría
    assert current(db_session).home_goals == states[larger].home_goals  # fixtures sí desempata (operativo)
    report = strict_knowledge(db_session, [fid(db_session)], T(5))
    assert report.known == {} and report.temporal_ambiguity == [fid(db_session)]


def test_ambiguity_is_only_at_its_instant(db_session, season_id):
    upsert(db_session, season_id, make_evidence(observed_at=T(0)), make_fixture_data(1, **FT_1_0))
    upsert(db_session, season_id, make_evidence(observed_at=T(0)), make_fixture_data(1, **FT_2_0))
    upsert(db_session, season_id, at(10), make_fixture_data(1, **FT_2_0))  # una evidencia posterior
    f = fid(db_session)
    assert strict_knowledge_at(db_session, f, T(5)).status is AMBIGUOUS
    later = strict_knowledge_at(db_session, f, T(15))
    assert (later.status, later.state.home_goals) == (KNOWN, 2)


# --- AS_OBSERVED (G1): marcadores parciales tal cual, sin fusión ----------------------------------


def test_partial_scores_are_returned_as_observed(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_PARTIAL))
    s = strict_knowledge_at(db_session, fid(db_session), T(5)).state
    assert (s.status_short, s.home_goals, s.away_goals) == ("FT", 2, 1)
    assert (s.halftime_home, s.halftime_away, s.fulltime_home, s.fulltime_away) == (None, None, None, None)


def test_no_score_fusion_from_earlier_or_current_state(db_session, season_id):
    """Una respuesta completa y después una parcial: fixtures conserva los pares (fusión operativa),
    pero lo conocido en T es la parcial tal cual; la completa anterior no rellena nada."""
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_FULL))
    upsert(db_session, season_id, at(10), make_fixture_data(1, status="FT"))  # todos los marcadores NULL
    f = current(db_session)
    assert (f.home_goals, f.halftime_home, f.fulltime_home) == (2, 1, 2)  # fusionado en fixtures
    s = strict_knowledge_at(db_session, f.id, T(15)).state
    assert (s.home_goals, s.away_goals, s.halftime_home, s.fulltime_home, s.fulltime_away) == (None, None, None, None, None)
    assert strict_knowledge_at(db_session, f.id, T(5)).state.fulltime_home == 2  # la completa, en su momento


def test_regression_current_fixture_is_more_complete_than_strict_knowledge(db_session, season_id):
    """Regresión explícita: fixtures tiene hoy el marcador completo, pero en T solo se había
    observado una respuesta parcial. STRICT_KNOWLEDGE devuelve la parcial inmutable."""
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_PARTIAL))
    upsert(db_session, season_id, at(20), make_fixture_data(1, **FT_FULL))
    f = current(db_session)
    assert (f.halftime_home, f.fulltime_home, f.fulltime_away) == (1, 2, 1)  # hoy: completo
    k = strict_knowledge_at(db_session, f.id, T(10))
    assert (k.status, k.known_at) == (KNOWN, T(0))  # KNOWN aunque esté incompleto para un mercado
    assert (k.state.home_goals, k.state.halftime_home, k.state.fulltime_home, k.state.fulltime_away) == (2, None, None, None)
    assert k.state.state_hash != bytes(f.last_state_hash)


# --- Identidad de la evidencia y orden por observed_at ------------------------------------------


def test_exact_replay_does_not_create_false_ambiguity(db_session, season_id):
    ev = at(0)
    upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0))
    upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0))  # la misma respuesta otra vez
    upsert(db_session, season_id, at(10), make_fixture_data(1, **FT_1_0))  # y una confirmación real
    f = fid(db_session)
    first = strict_knowledge_at(db_session, f, T(5))
    assert (first.status, len(first.observations), first.state.evidence_id) == (KNOWN, 1, ev.evidence_id)
    assert strict_knowledge_at(db_session, f, T(15)).status is KNOWN


def test_older_evidence_inserted_later_is_ordered_by_observed_at(db_session, season_id):
    newer, older = at(20), at(10)
    upsert(db_session, season_id, newer, make_fixture_data(1, **FT_2_0))
    upsert(db_session, season_id, older, make_fixture_data(1, **FT_1_0))  # llega después
    f = fid(db_session)
    # El orden físico de la escritura queda en contra del temporal: la fila antigua se escribió
    # después (id mayor) y su recorded_at se adelanta a propósito para que tampoco ayude
    obs = {o.evidence_id: o for o in observations(db_session)}
    assert obs[older.evidence_id].id > obs[newer.evidence_id].id
    db_session.execute(
        text("UPDATE fixture_observations SET recorded_at = :r WHERE evidence_id = :e"),
        {"r": T(1000), "e": older.evidence_id},
    )
    k15, k25 = strict_knowledge_at(db_session, f, T(15)), strict_knowledge_at(db_session, f, T(25))
    assert (k15.state.evidence_id, k15.state.home_goals) == (older.evidence_id, 1)
    assert (k25.state.evidence_id, k25.state.home_goals) == (newer.evidence_id, 2)
    assert k15.state.recorded_at == T(1000)  # recorded_at viaja como auditoría, sin decidir nada


# --- Separación de fuentes ------------------------------------------------------------------------


def test_statistics_evidence_is_not_fixture_evidence(db_session, season_id):
    upsert(db_session, season_id, at(60), make_fixture_data(1, **FT_1_0))
    f = fid(db_session)
    db_session.execute(
        text(
            "INSERT INTO fixture_statistics_observations (fixture_id, provider, provider_fixture_id, source, availability, "
            "teams_returned, payload, payload_hash, observed_at, last_observed_at, available_at) VALUES "
            "(:f, 'api-football', '1', 'live', 'empty', 0, '[]'::jsonb, :h, :n, :n, :n)"
        ),
        {"f": f, "h": "0" * 64, "n": T(0)},
    )
    assert strict_knowledge_at(db_session, f, T(30)).status is UNKNOWN


def test_reads_only_fixture_observations_and_never_orders_by_recorded_at(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_1_0))
    f = fid(db_session)
    statements = []
    engine = db_session.get_bind().engine

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        strict_knowledge(db_session, [f], T(5))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1  # una consulta para todos los partidos
    sql = statements[0]
    for other in (" fixtures ", " fixtures\n", "fixture_statistics_observations", "fixture_team_statistics"):
        assert other not in sql.replace("fixture_observations", "")
    after_from = sql[sql.index("\nfrom "):]
    assert "recorded_at" not in after_from  # ni en el filtro ni en el orden


# --- Informe por defecto ----------------------------------------------------------------------


def test_report_excludes_and_counts_unknown_and_ambiguous(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_1_0))  # 1: KNOWN
    upsert(db_session, season_id, at(30), make_fixture_data(2, home=3, away=4, status="NS"))  # 2: UNKNOWN en T(10)
    upsert(db_session, season_id, make_evidence(observed_at=T(0)), make_fixture_data(3, home=5, away=6, **FT_1_0))
    upsert(db_session, season_id, make_evidence(observed_at=T(0)), make_fixture_data(3, home=5, away=6, **FT_2_0))  # 3: AMBIGUOUS
    ids = [fid(db_session, e) for e in (1, 2, 3)]
    report = strict_knowledge(db_session, ids, T(10))
    assert report.mode is EvaluationMode.STRICT_KNOWLEDGE and FixtureKnowledge.mode is EvaluationMode.STRICT_KNOWLEDGE
    assert list(report.known) == [ids[0]]
    assert (report.unknown_at_t, report.temporal_ambiguity) == ([ids[1]], [ids[2]])
    assert report.counts == {KNOWN: 1, UNKNOWN: 1, AMBIGUOUS: 1}


def test_inconsistent_knowledge_cannot_be_built():
    with pytest.raises(ValueError):
        FixtureKnowledge(1, T(0), KNOWN)  # KNOWN sin evidencia
    with pytest.raises(ValueError):
        FixtureKnowledge(1, T(0), UNKNOWN, known_at=T(0))  # UNKNOWN con t*
