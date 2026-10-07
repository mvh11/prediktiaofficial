"""UpsertCounts exactos con escritores concurrentes (DI-A6, opción A).

Cada escritor va en su hilo y su conexión. La sincronización es explícita: un escritor retiene su
transacción y el otro queda esperando un bloqueo de verdad (comprobado en pg_stat_activity), o un
escritor se detiene justo antes de su SELECT ... FOR UPDATE para forzar el entrelazado entre
sentencias. Cada escritor fija lock_timeout = 10 s: un deadlock o una espera inesperada es un error.

Contrato de los contadores: created = la creó ESTA transacción; updated = existía y ESTA transacción
cambió sus datos; unchanged = el resto (confirmación, evidencia que pierde, repetición).
"""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.models import Fixture, FixtureObservation, FixtureProviderMapping
from app.repositories import fixture_repository as repo
from app.repositories.fixture_repository import STATE_COLUMNS, state_hashes
from tests.conftest import make_evidence, make_fixture_data
from tests.test_fixture_observations_concurrency import T0, committed, wait_until_blocked  # noqa: F401

pytestmark = pytest.mark.db

S0 = dict(status="NS")
S1 = dict(status="FT", home_goals=2, away_goals=1, fulltime_home=2, fulltime_away=1)
SX = dict(status="1H", home_goals=0, away_goals=0)
TIMEOUT = 30


def at(minutes):
    return make_evidence(observed_at=T0 + timedelta(minutes=minutes))


def fixture(ext, spec):
    # Dos parejas de equipos según la paridad: los equipos se crean en `teams`
    return make_fixture_data(ext, home=1 if ext % 2 else 3, away=2 if ext % 2 else 4, **spec)


@pytest.fixture
def teams(committed):
    engine, season_id, team_ids = committed
    with Session(engine) as db:
        extra = repo.ensure_teams(db, [fixture(2, S0).home_team, fixture(2, S0).away_team], "api-football")
        db.commit()
    return engine, season_id, {**team_ids, **extra}


@pytest.fixture(autouse=True)
def pause_hook(migrated_db):
    """Detiene una conexión justo antes de la sentencia marcada (conn.info['pause'])."""
    from app.db.database import engine

    def hook(conn, cursor, statement, parameters, context, executemany):
        pause = conn.info.get("pause")
        if pause and pause[0](statement):
            del conn.info["pause"]
            pause[1].set()
            assert pause[2].wait(TIMEOUT), "la pausa nunca se liberó"

    event.listen(engine, "before_cursor_execute", hook)
    yield
    event.remove(engine, "before_cursor_execute", hook)


def is_locking_select(statement: str) -> bool:
    return statement.lstrip().upper().startswith("SELECT") and "FOR UPDATE" in statement.upper()


class Writer(threading.Thread):
    """Una transacción: trabaja, avisa (done) y espera la orden de confirmar o deshacer (finish)."""

    def __init__(self, engine, work, *, commit=True, pause_before=None):
        super().__init__(daemon=True)
        self.engine, self.work, self.commit = engine, work, commit
        self.pause_before = pause_before
        self.ready, self.done, self.finish = threading.Event(), threading.Event(), threading.Event()
        self.paused, self.release = threading.Event(), threading.Event()
        self.pid = self.result = self.error = None

    def run(self):
        try:
            with Session(self.engine) as db:
                conn = db.connection()
                db.execute(text("SET LOCAL lock_timeout = '10s'"))
                self.pid = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
                if self.pause_before:
                    conn.info["pause"] = (self.pause_before, self.paused, self.release)
                self.ready.set()
                try:
                    self.result = self.work(db)
                finally:
                    conn.info.pop("pause", None)
                self.done.set()
                assert self.finish.wait(TIMEOUT)
                db.commit() if self.commit else db.rollback()
        except BaseException as exc:  # noqa: BLE001 - se re-lanza en el hilo principal
            self.error = exc
            self.ready.set()
            self.done.set()

    def close(self):
        self.finish.set()
        self.join(TIMEOUT)
        assert not self.is_alive(), "el escritor no terminó (¿bloqueo sin resolver?)"
        if self.error:
            raise self.error


def blocked_statement(engine, pid) -> str:
    wait_until_blocked(engine, pid)
    with engine.connect() as conn:
        return conn.execute(text("SELECT left(query, 50) FROM pg_stat_activity WHERE pid = :p"), {"p": pid}).scalar()


def upsert_work(season_id, team_ids, items, evidence, *, full_path=False):
    batch = [fixture(e, s) for e, s in items]

    def work(db):
        ids = team_ids
        if full_path:  # el camino real de la sync y del backfill
            ids = repo.ensure_teams(db, [t for f in batch for t in (f.home_team, f.away_team)], "api-football")
        return repo.upsert_fixtures(db, season_id, batch, ids, "api-football", evidence)

    return work


def counts(c):
    return (c.created, c.updated, c.unchanged)


def race(engine, work_a, work_b, *, a_commits=True):
    """A trabaja y retiene; B trabaja y queda bloqueado; A confirma (o deshace); B termina y confirma."""
    a = Writer(engine, work_a, commit=a_commits)
    a.start()
    assert a.done.wait(TIMEOUT)
    if a.error:
        raise a.error
    b = Writer(engine, work_b)
    b.start()
    assert b.ready.wait(TIMEOUT)
    waited_on = blocked_statement(engine, b.pid)
    a.close()
    assert b.done.wait(TIMEOUT)
    b.close()
    return a.result, b.result, waited_on


def seed(engine, season_id, team_ids, items, evidence):
    with Session(engine) as db:
        repo.upsert_fixtures(db, season_id, [fixture(e, s) for e, s in items], team_ids, "api-football", evidence)
        db.commit()


def state(engine):
    """{external_id: (estado, last_observed_at)} y la historia {(external_id, evidence_id)}."""
    with Session(engine) as db:
        fixtures = {f.external_id: ({c: getattr(f, c) for c in ("status_short", "home_goals", "kickoff_at")}, f.last_observed_at)
                    for f in db.scalars(select(Fixture))}
        history = set(db.execute(select(Fixture.external_id, FixtureObservation.evidence_id)
                                 .join(FixtureObservation, FixtureObservation.fixture_id == Fixture.id)).all())
    return fixtures, history


def evidence_set(*pairs):
    return {(e, ev.evidence_id) for ev, exts in pairs for e in exts}


# --- Dos escritores sobre el mismo partido ---------------------------------------------------------


@pytest.mark.parametrize(
    ("b_spec", "b_minutes", "expected_b", "winner"),
    [(S1, 20, (0, 0, 1), "B"), (S0, 20, (0, 1, 0), "B"), (S0, 5, (0, 0, 1), "A")],
    ids=["same_data", "different_data", "second_is_older"],
)
def test_two_writers_create_the_same_fixture(teams, b_spec, b_minutes, expected_b, winner):
    engine, season_id, team_ids = teams
    ea, eb = at(10), at(b_minutes)
    ca, cb, waited = race(engine, upsert_work(season_id, team_ids, [(1, S1)], ea), upsert_work(season_id, team_ids, [(1, b_spec)], eb))
    assert (counts(ca), counts(cb)) == ((1, 0, 0), expected_b)
    assert waited.startswith("INSERT INTO fixtures")  # B espera la creación de A
    fixtures, history = state(engine)
    expected_state = S1 if winner == "A" else b_spec
    assert fixtures[1][0]["status_short"] == expected_state["status"]
    assert fixtures[1][1] == (ea if winner == "A" else eb).observed_at
    assert history == evidence_set((ea, [1]), (eb, [1]))  # ambas en la historia


@pytest.mark.parametrize("newer_first", [True, False], ids=["newer_commits_first", "older_commits_first"])
def test_newer_and_older_evidence_in_both_arrival_orders(teams, newer_first):
    engine, season_id, team_ids = teams
    seed(engine, season_id, team_ids, [(1, S0)], at(0))
    newer, older = ((1, S1), at(20)), ((1, SX), at(10))
    first, second = (newer, older) if newer_first else (older, newer)
    ca, cb, waited = race(engine, upsert_work(season_id, team_ids, [first[0]], first[1]),
                          upsert_work(season_id, team_ids, [second[0]], second[1]))
    # La primera siempre cambia S0; la segunda solo si es la más nueva
    assert counts(ca) == (0, 1, 0)
    assert counts(cb) == ((0, 0, 1) if newer_first else (0, 1, 0))
    assert waited.startswith("SELECT fixtures.external_id")  # B espera en el FOR UPDATE
    fixtures, history = state(engine)
    assert (fixtures[1][0]["status_short"], fixtures[1][1]) == ("FT", newer[1].observed_at)
    assert len(history) == 3


@pytest.mark.parametrize("larger_first", [True, False], ids=["larger_hash_first", "smaller_hash_first"])
def test_equal_observed_at_with_different_state_hash(teams, larger_first):
    engine, season_id, team_ids = teams
    with Session(engine) as db:
        rows = [{**fixture(1, s).model_dump(), "season_id": season_id, "home_team_id": team_ids[1], "away_team_id": team_ids[2]}
                for s in (S1, SX)]
        h = state_hashes(db, rows)
    larger, smaller = (S1, SX) if h[0] > h[1] else (SX, S1)
    # Estado base = el del hash mayor: el empate lo vuelve a dejar ahí si llega el segundo
    seed(engine, season_id, team_ids, [(1, larger)], at(0))
    a_spec, b_spec = (larger, smaller) if larger_first else (smaller, larger)
    ca, cb, _ = race(engine, upsert_work(season_id, team_ids, [(1, a_spec)], at(10)),
                     upsert_work(season_id, team_ids, [(1, b_spec)], at(10)))
    if larger_first:  # A confirma el estado base y gana el empate; B pierde
        assert (counts(ca), counts(cb)) == ((0, 0, 1), (0, 0, 1))
    else:  # A cambia al estado pequeño; B gana el empate y devuelve el estado grande
        assert (counts(ca), counts(cb)) == ((0, 1, 0), (0, 1, 0))
    fixtures, history = state(engine)
    assert fixtures[1][0]["status_short"] == larger["status"] and len(history) == 3


@pytest.mark.parametrize(
    ("b_spec", "expected_b"), [(S0, (0, 1, 0)), (S1, (0, 0, 1))],
    ids=["newer_confirmation_reverts_concurrent_update", "newer_confirmation_of_the_update"],
)
def test_update_vs_confirmation(teams, b_spec, expected_b):
    engine, season_id, team_ids = teams
    seed(engine, season_id, team_ids, [(1, S0)], at(0))
    ca, cb, _ = race(engine, upsert_work(season_id, team_ids, [(1, S1)], at(10)),
                     upsert_work(season_id, team_ids, [(1, b_spec)], at(20)))
    assert (counts(ca), counts(cb)) == ((0, 1, 0), expected_b)
    assert state(engine)[0][1][0]["status_short"] == b_spec["status"]


def test_replay_vs_independent_confirmation(teams):
    engine, season_id, team_ids = teams
    e0 = at(0)
    seed(engine, season_id, team_ids, [(1, S0)], e0)
    e1 = at(10)
    ca, cb, _ = race(engine, upsert_work(season_id, team_ids, [(1, S0)], e1), upsert_work(season_id, team_ids, [(1, S0)], e0))
    assert (counts(ca), counts(cb)) == ((0, 0, 1), (0, 0, 1))
    fixtures, history = state(engine)
    assert fixtures[1][1] == e1.observed_at and history == evidence_set((e0, [1]), (e1, [1]))  # la repetición no crea filas


@pytest.mark.parametrize("existing", [False, True], ids=["competing_insert_rolls_back", "competing_update_rolls_back"])
def test_competitor_rolls_back(teams, existing):
    engine, season_id, team_ids = teams
    if existing:
        seed(engine, season_id, team_ids, [(1, S0)], at(0))
    ea, eb = at(10), at(20)
    ca, cb, _ = race(engine, upsert_work(season_id, team_ids, [(1, S1)], ea), upsert_work(season_id, team_ids, [(1, S1)], eb),
                     a_commits=False)
    assert counts(cb) == ((0, 1, 0) if existing else (1, 0, 0))  # B hace el trabajo que A deshizo
    fixtures, history = state(engine)
    assert fixtures[1] == ({"status_short": "FT", "home_goals": 2, "kickoff_at": fixture(1, S1).kickoff_at}, eb.observed_at)
    assert ea.evidence_id not in {ev for _, ev in history}  # nada de A sobrevive


# --- Lotes que se solapan ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a_exts", "b_exts", "expected_a", "expected_b"),
    [
        ([1, 2], [2, 3], (2, 0, 0), (1, 0, 1)),
        ([1, 2, 3, 4], [2, 3], (4, 0, 0), (0, 0, 2)),
        ([2, 3], [1, 2, 3, 4], (2, 0, 0), (2, 0, 2)),
    ],
    ids=["partial_overlap", "b_is_subset", "b_is_superset"],
)
def test_overlapping_batches(teams, a_exts, b_exts, expected_a, expected_b):
    engine, season_id, team_ids = teams
    ea, eb = at(10), at(20)
    ca, cb, _ = race(engine, upsert_work(season_id, team_ids, [(e, S1) for e in a_exts], ea),
                     upsert_work(season_id, team_ids, [(e, S1) for e in b_exts], eb))
    assert (counts(ca), counts(cb)) == (expected_a, expected_b)
    fixtures, history = state(engine)
    assert set(fixtures) == set(a_exts) | set(b_exts)
    assert history == evidence_set((ea, a_exts), (eb, b_exts))


def test_reversed_external_id_ranges_mixed_new_and_existing(teams):
    """Entradas en órdenes opuestos, con partidos nuevos y existentes mezclados: sin deadlock y exacto."""
    engine, season_id, team_ids = teams
    seed(engine, season_id, team_ids, [(e, S0) for e in (2, 4)], at(0))
    a_items = [(e, S1) for e in (5, 4, 3, 2, 1)]
    b_items = [(e, S1) for e in (1, 2, 3, 4, 5, 6)]
    ca, cb, _ = race(engine, upsert_work(season_id, team_ids, a_items, at(10)), upsert_work(season_id, team_ids, b_items, at(20)))
    assert counts(ca) == (3, 2, 0)  # crea 1, 3, 5; actualiza 2 y 4
    assert counts(cb) == (1, 0, 5)  # crea 6; el resto ya tiene los mismos datos


def test_disjoint_batches_control(teams):
    engine, season_id, team_ids = teams
    a = Writer(engine, upsert_work(season_id, team_ids, [(1, S1)], at(10)))
    b = Writer(engine, upsert_work(season_id, team_ids, [(3, S1)], at(20)))
    a.start()
    assert a.done.wait(TIMEOUT)
    b.start()
    assert b.done.wait(TIMEOUT)  # B no espera a A
    a.close()
    b.close()
    assert (counts(a.result), counts(b.result)) == ((1, 0, 0), (1, 0, 0))


def test_three_concurrent_writers(teams):
    """A retiene; B y C quedan esperando a la vez; al confirmar A, B y C compiten entre sí."""
    engine, season_id, team_ids = teams
    ea, eb, ec = at(10), at(20), at(30)
    a = Writer(engine, upsert_work(season_id, team_ids, [(e, S1) for e in (1, 2, 3)], ea))
    b = Writer(engine, upsert_work(season_id, team_ids, [(e, S1) for e in (2, 3, 4)], eb))
    c = Writer(engine, upsert_work(season_id, team_ids, [(e, S1) for e in (3, 4, 5)], ec))
    a.start()
    assert a.done.wait(TIMEOUT)
    for w in (b, c):
        w.finish.set()  # B y C confirman en cuanto terminan: el orden entre ellos lo decide la BD
        w.start()
        assert w.ready.wait(TIMEOUT)
        blocked_statement(engine, w.pid)
    a.close()
    b.close()
    c.close()
    results = [counts(w.result) for w in (a, b, c)]
    assert results[0] == (3, 0, 0)
    # 4 lo crea quien llegue antes (B o C); 5 solo C. Nadie actualiza: todos traen los mismos datos
    assert sum(r[0] for r in results) == 5 and all(r[1] == 0 for r in results)
    assert results[1][0] + results[2][0] == 2 and results[2][0] >= 1
    fixtures, history = state(engine)
    assert set(fixtures) == {1, 2, 3, 4, 5}
    assert history == evidence_set((ea, [1, 2, 3]), (eb, [2, 3, 4]), (ec, [3, 4, 5]))


# --- Entrelazados entre sentencias (puerta de deadlock) ---------------------------------------------


def test_both_writers_hold_new_rows_before_locking_existing_ones(teams):
    """A y B crean cada uno un partido nuevo distinto y se detienen antes de bloquear los existentes,
    que comparten. Después bloquean en orden de external_id: uno espera al otro, sin deadlock."""
    engine, season_id, team_ids = teams
    seed(engine, season_id, team_ids, [(1, S0), (3, S0)], at(0))
    a = Writer(engine, upsert_work(season_id, team_ids, [(1, S1), (2, S1), (3, S1)], at(10)), pause_before=is_locking_select)
    b = Writer(engine, upsert_work(season_id, team_ids, [(1, S1), (3, S1), (4, S1)], at(20)), pause_before=is_locking_select)
    a.start()
    assert a.paused.wait(TIMEOUT)  # A ya insertó 2
    b.start()
    assert b.paused.wait(TIMEOUT)  # B ya insertó 4
    a.release.set()
    assert a.done.wait(TIMEOUT)  # A bloquea 1 y 3 sin esperar
    b.release.set()
    assert blocked_statement(engine, b.pid).startswith("SELECT fixtures.external_id")  # B espera a A en el FOR UPDATE
    a.close()
    assert b.done.wait(TIMEOUT)
    b.close()
    assert (counts(a.result), counts(b.result)) == ((1, 2, 0), (1, 0, 2))


def test_writer_waiting_on_a_new_row_holds_no_row_locks(teams):
    """A crea 2 y se detiene antes de su FOR UPDATE; B también trae 2 como nuevo y espera en el INSERT
    sin haber bloqueado ninguna fila existente, así que A puede bloquear las suyas."""
    engine, season_id, team_ids = teams
    seed(engine, season_id, team_ids, [(1, S0), (3, S0)], at(0))
    a = Writer(engine, upsert_work(season_id, team_ids, [(1, S1), (2, S1), (3, S1)], at(10)), pause_before=is_locking_select)
    b = Writer(engine, upsert_work(season_id, team_ids, [(2, S1), (3, S1), (4, S1)], at(20)))
    a.start()
    assert a.paused.wait(TIMEOUT)
    b.start()
    assert b.ready.wait(TIMEOUT)
    assert blocked_statement(engine, b.pid).startswith("INSERT INTO fixtures")
    a.release.set()
    assert a.done.wait(TIMEOUT)
    a.close()
    assert b.done.wait(TIMEOUT)
    b.close()
    assert (counts(a.result), counts(b.result)) == ((1, 2, 0), (1, 0, 2))


# --- Camino completo (ensure_teams + upsert_fixtures) -----------------------------------------------


@pytest.mark.parametrize(
    ("setup", "a_spec", "b_spec", "expected"),
    [(False, S1, S1, ((1, 0, 0), (0, 0, 1))), (True, S1, S0, ((0, 1, 0), (0, 1, 0)))],
    ids=["create_same_data", "newer_confirmation_reverts_concurrent_update"],
)
def test_full_call_path(teams, setup, a_spec, b_spec, expected):
    engine, season_id, team_ids = teams
    if setup:
        seed(engine, season_id, team_ids, [(1, S0)], at(0))
    ca, cb, waited = race(engine, upsert_work(season_id, team_ids, [(1, a_spec)], at(10), full_path=True),
                          upsert_work(season_id, team_ids, [(1, b_spec)], at(20), full_path=True))
    assert (counts(ca), counts(cb)) == expected
    assert waited.startswith("INSERT INTO team_provider_mappings")  # hoy espera ya en ensure_teams


# --- Fallo después de la contención (rollback) -------------------------------------------------------


def test_failed_writer_after_contention_leaves_the_committed_competitor_intact(teams):
    """B espera a A, escribe y obtiene sus contadores, y después falla: nada de B sobrevive (ni
    fixtures, ni observaciones, ni mappings) y sus contadores no son la verdad confirmada."""
    engine, season_id, team_ids = teams
    ea, eb = at(10), at(20)
    a = Writer(engine, upsert_work(season_id, team_ids, [(1, S0)], ea))
    a.start()
    assert a.done.wait(TIMEOUT)

    def failing(db):
        counts_b = repo.upsert_fixtures(db, season_id, [fixture(1, S1)], team_ids, "api-football", eb)
        db.execute(text("SELECT 1/0"))  # error de BD en la misma transacción, después de escribir
        return counts_b

    b = Writer(engine, failing)
    b.start()
    assert b.ready.wait(TIMEOUT)
    blocked_statement(engine, b.pid)
    a.close()
    with Session(engine) as db:
        seen_after_a = db.scalar(select(FixtureProviderMapping.last_seen_at).where(FixtureProviderMapping.external_id == "1"))
    assert b.done.wait(TIMEOUT)
    b.finish.set()
    b.join(TIMEOUT)
    assert b.error is not None and "division by zero" in str(b.error)
    fixtures, history = state(engine)
    assert fixtures[1] == ({"status_short": "NS", "home_goals": None, "kickoff_at": fixture(1, S0).kickoff_at}, ea.observed_at)
    assert history == evidence_set((ea, [1]))
    with Session(engine) as db:
        mappings = db.execute(select(FixtureProviderMapping.last_seen_at).where(FixtureProviderMapping.external_id == "1")).all()
    assert len(mappings) == 1 and mappings[0][0] == seen_after_a


@pytest.mark.parametrize("columns", [STATE_COLUMNS])
def test_state_columns_unchanged(columns):
    """Guarda: la opción A no toca el conjunto de columnas de estado (ni, por tanto, el hash)."""
    assert columns[0] == "kickoff_at" and columns[-1] == "penalty_away" and len(columns) == 15
