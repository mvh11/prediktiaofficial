"""Escritores concurrentes de verdad (DI-A6): dos conexiones, transacciones confirmadas y una
espera real por el bloqueo de la fila. El estado final de fixtures y la historia no dependen del
orden de commit. Solo BD de tests; cada test limpia lo que confirma.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Fixture, FixtureObservation
from app.repositories import fixture_repository
from app.repositories.fixture_repository import STATE_COLUMNS, state_hashes
from tests.conftest import assert_destructive_db_allowed, make_competition, make_evidence, make_fixture_data

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
OLD = make_fixture_data(1, status="1H", home_goals=0, away_goals=0)
NEW = make_fixture_data(1, status="FT", home_goals=2, away_goals=1, fulltime_home=2, fulltime_away=1)
CLEANUP = "TRUNCATE fixture_observations, fixtures, season_teams, seasons, teams, competitions CASCADE"


@pytest.fixture
def committed(migrated_db):
    """Competición, temporada y equipos confirmados; al terminar se borra todo lo confirmado."""
    from app.db.database import engine

    assert_destructive_db_allowed()
    with Session(engine) as db:
        _, season_id = make_competition(db, 265)
        team_ids = fixture_repository.ensure_teams(db, [OLD.home_team, OLD.away_team], "api-football")
        db.commit()
    try:
        yield engine, season_id, team_ids
    finally:
        with engine.begin() as conn:
            conn.execute(text(CLEANUP))


def write(db: Session, season_id: int, team_ids: dict, data, evidence) -> None:
    fixture_repository.upsert_fixtures(db, season_id, [data], team_ids, "api-football", evidence)


def wait_until_blocked(engine, pid: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
            ).scalar()
            if waiting == "Lock":
                return
            conn.rollback()
            time.sleep(0.02)
    raise AssertionError("el segundo escritor nunca llegó a esperar el bloqueo")


def race(engine, season_id, team_ids, first, second, *, first_commits: bool = True) -> None:
    """`first` escribe y retiene su transacción; `second` escribe en otro hilo y queda bloqueado en la
    fila; entonces `first` confirma (o se deshace) y `second` termina y confirma."""
    errors: list[BaseException] = []
    pid: list[int] = []
    with Session(engine) as db1:
        write(db1, season_id, team_ids, *first)

        def run_second():
            try:
                with Session(engine) as db2:
                    pid.append(db2.execute(text("SELECT pg_backend_pid()")).scalar_one())
                    write(db2, season_id, team_ids, *second)
                    db2.commit()
            except BaseException as exc:  # noqa: BLE001 - se re-lanza en el hilo principal
                errors.append(exc)

        thread = threading.Thread(target=run_second)
        thread.start()
        try:
            for _ in range(500):
                if pid:
                    break
                time.sleep(0.01)
            wait_until_blocked(engine, pid[0])
        finally:
            db1.commit() if first_commits else db1.rollback()
            thread.join(30)
    assert not thread.is_alive()
    if errors:
        raise errors[0]


def final_state(engine) -> tuple:
    with Session(engine) as db:
        f = db.scalars(select(Fixture).where(Fixture.external_id == 1)).one()
        history = sorted(
            (o.observed_at, bytes(o.state_hash), o.evidence_id)
            for o in db.scalars(select(FixtureObservation).where(FixtureObservation.fixture_id == f.id))
        )
        return {c: getattr(f, c) for c in STATE_COLUMNS}, f.last_observed_at, bytes(f.last_state_hash), history


@pytest.mark.parametrize("newer_first", [True, False], ids=["newer_commits_first", "older_commits_first"])
def test_newer_evidence_wins_whatever_the_commit_order(committed, newer_first):
    engine, season_id, team_ids = committed
    with Session(engine) as db:  # el partido ya existe: ambos escritores chocan en la misma fila
        write(db, season_id, team_ids, make_fixture_data(1), make_evidence(observed_at=T0 - timedelta(hours=1)))
        db.commit()
    older = (OLD, make_evidence(observed_at=T0))
    newer = (NEW, make_evidence(observed_at=T0 + timedelta(minutes=30)))
    race(engine, season_id, team_ids, *((newer, older) if newer_first else (older, newer)))

    state, last_observed_at, _, history = final_state(engine)
    assert (state["status_short"], state["home_goals"], state["fulltime_home"]) == ("FT", 2, 2)
    assert last_observed_at == newer[1].observed_at
    assert [h[2] for h in history][1:] == [older[1].evidence_id, newer[1].evidence_id]  # ambas en la historia


@pytest.mark.parametrize("larger_first", [True, False], ids=["larger_hash_first", "smaller_hash_first"])
def test_equal_timestamps_resolve_to_the_larger_hash_whatever_the_order(committed, larger_first):
    engine, season_id, team_ids = committed
    with Session(engine) as db:
        write(db, season_id, team_ids, make_fixture_data(1), make_evidence(observed_at=T0 - timedelta(hours=1)))
        db.commit()
        states = [
            {**d.model_dump(), "season_id": season_id, "home_team_id": team_ids[1], "away_team_id": team_ids[2]}
            for d in (OLD, NEW)
        ]
        hashes = state_hashes(db, states)
    larger, smaller = (NEW, OLD) if hashes[1] > hashes[0] else (OLD, NEW)
    a = (larger, make_evidence(observed_at=T0))
    b = (smaller, make_evidence(observed_at=T0))
    race(engine, season_id, team_ids, *((a, b) if larger_first else (b, a)))

    state, last_observed_at, last_hash, history = final_state(engine)
    assert state["status_short"] == larger.status_short and last_hash == max(hashes)
    assert last_observed_at == T0
    assert len({h[1] for h in history if h[0] == T0}) == 2  # ambigüedad conservada en la historia


def test_concurrent_replay_of_the_same_response_writes_once(committed):
    engine, season_id, team_ids = committed
    evidence = make_evidence(observed_at=T0)
    race(engine, season_id, team_ids, (NEW, evidence), (NEW, evidence))
    _, last_observed_at, _, history = final_state(engine)
    assert [h[2] for h in history] == [evidence.evidence_id]
    assert last_observed_at == T0


def test_new_fixture_inserted_by_two_writers(committed):
    engine, season_id, team_ids = committed
    older = (OLD, make_evidence(observed_at=T0))
    newer = (NEW, make_evidence(observed_at=T0 + timedelta(minutes=1)))
    race(engine, season_id, team_ids, newer, older)  # el partido no existía: chocan en el INSERT
    state, last_observed_at, _, history = final_state(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM fixtures")).scalar_one() == 1
    assert state["status_short"] == "FT" and last_observed_at == newer[1].observed_at
    assert len(history) == 2


def test_rolled_back_writer_leaves_nothing(committed):
    engine, season_id, team_ids = committed
    with Session(engine) as db:
        write(db, season_id, team_ids, make_fixture_data(1), make_evidence(observed_at=T0 - timedelta(hours=1)))
        db.commit()
    lost = (NEW, make_evidence(observed_at=T0 + timedelta(hours=1)))
    kept = (OLD, make_evidence(observed_at=T0))
    race(engine, season_id, team_ids, lost, kept, first_commits=False)
    state, last_observed_at, _, history = final_state(engine)
    assert state["status_short"] == "1H" and last_observed_at == T0
    assert lost[1].evidence_id not in {h[2] for h in history}


def test_final_knowledge_is_independent_of_arrival_order(committed):
    """Las mismas tres respuestas en dos órdenes de llegada distintos dejan el mismo estado e historia."""
    engine, season_id, team_ids = committed
    responses = [
        (make_fixture_data(1), make_evidence(observed_at=T0)),
        (OLD, make_evidence(observed_at=T0 + timedelta(minutes=10))),
        (NEW, make_evidence(observed_at=T0 + timedelta(minutes=20))),
    ]
    results = []
    for order in ([0, 1, 2], [2, 0, 1]):
        for i in order:
            with Session(engine) as db:
                write(db, season_id, team_ids, *responses[i])
                db.commit()
        results.append(final_state(engine))
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE fixture_observations, fixtures CASCADE"))
    assert results[0] == results[1]
