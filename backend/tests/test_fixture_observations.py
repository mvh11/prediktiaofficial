"""Escritura de evidencia temporal en upsert_fixtures (DI-A6): observaciones inmutables, orden
(observed_at, state_hash), confirmaciones reales, evidencia antigua, repetición exacta y G3.
Solo BD de tests; la concurrencia real está en test_fixture_observations_concurrency.py."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update

from app.models import Fixture, FixtureObservation, FixtureProviderMapping
from app.repositories import fixture_repository
from app.repositories.fixture_repository import STATE_COLUMNS, EvidenceIdentityConflict, UpsertCounts, state_hashes
from app.schemas.fixture_evidence import FixtureEvidence
from tests.conftest import make_competition, make_evidence, make_fixture_data

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
FT_1_0 = dict(status="FT", home_goals=1, away_goals=0, halftime_home=1, halftime_away=0, fulltime_home=1, fulltime_away=0)
FT_2_0 = dict(status="FT", home_goals=2, away_goals=0, halftime_home=1, halftime_away=0, fulltime_home=2, fulltime_away=0)


def at(minutes: float) -> FixtureEvidence:
    return make_evidence(observed_at=T0 + timedelta(minutes=minutes))


@pytest.fixture
def season_id(db_session) -> int:
    return make_competition(db_session, 265)[1]


def upsert(db, season_id: int, evidence: FixtureEvidence, *fixtures, provider: str = "api-football") -> UpsertCounts:
    teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
    team_ids = fixture_repository.ensure_teams(db, teams, provider)
    counts = fixture_repository.upsert_fixtures(db, season_id, list(fixtures), team_ids, provider, evidence)
    db.flush()
    return counts


def fixture(db, external_id: int = 1) -> Fixture:
    db.expire_all()
    return db.scalars(select(Fixture).where(Fixture.external_id == external_id)).one()


def observations(db, external_id: int = 1) -> list[FixtureObservation]:
    db.expire_all()
    return list(
        db.scalars(
            select(FixtureObservation)
            .join(Fixture, Fixture.id == FixtureObservation.fixture_id)
            .where(Fixture.external_id == external_id)
            .order_by(FixtureObservation.observed_at, FixtureObservation.id)
        )
    )


def observed_state(o: FixtureObservation) -> dict:
    return {c: getattr(o, c) for c in STATE_COLUMNS}


def row_state(f: Fixture) -> dict:
    return {c: getattr(f, c) for c in STATE_COLUMNS}


# --- Partido nuevo y evidencia más nueva -----------------------------------------------------


def test_new_fixture_creates_row_and_its_observation(db_session, season_id):
    ev = at(0)
    counts = upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0))
    assert counts == UpsertCounts(received=1, created=1, updated=0, unchanged=0)
    f, [o] = fixture(db_session), observations(db_session)
    assert (o.evidence_id, o.observed_at, o.source, o.provider) == (ev.evidence_id, ev.observed_at, "sync", "api-football")
    assert (f.last_observed_at, bytes(f.last_state_hash)) == (ev.observed_at, bytes(o.state_hash))
    assert observed_state(o) == row_state(f)
    assert o.recorded_at is not None  # metadato físico: no interviene en el orden
    assert bytes(o.state_hash) == state_hashes(db_session, [observed_state(o)])[0]


def test_newer_identical_response_is_a_real_confirmation(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_1_0))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.execute(update(Fixture).values(updated_at=old))
    confirmation = at(10)
    counts = upsert(db_session, season_id, confirmation, make_fixture_data(1, **FT_1_0))

    assert counts == UpsertCounts(received=1, created=0, updated=0, unchanged=1)
    f, obs = fixture(db_session), observations(db_session)
    assert len(obs) == 2  # la confirmación es evidencia real, con su instante y procedencia
    assert obs[1].evidence_id == confirmation.evidence_id and obs[1].observed_at == confirmation.observed_at
    assert observed_state(obs[0]) == observed_state(obs[1])
    assert f.last_observed_at == confirmation.observed_at  # solo avanza el orden
    assert f.updated_at == old  # no es una actualización de datos


def test_newer_changed_response_updates_state(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_1_0))
    newer = at(10)
    counts = upsert(db_session, season_id, newer, make_fixture_data(1, **FT_2_0))
    assert counts == UpsertCounts(received=1, created=0, updated=1, unchanged=0)
    f, obs = fixture(db_session), observations(db_session)
    assert (f.home_goals, f.fulltime_home, f.last_observed_at) == (2, 2, newer.observed_at)
    assert bytes(f.last_state_hash) == bytes(obs[-1].state_hash)
    assert [o.home_goals for o in obs] == [1, 2]


# --- Evidencia antigua que llega tarde -------------------------------------------------------


@pytest.mark.parametrize("late", [FT_1_0, FT_2_0, dict(status="2H", home_goals=0, away_goals=0)], ids=["same", "changed", "live"])
def test_older_evidence_goes_to_history_but_never_overwrites(db_session, season_id, late):
    current = at(10)
    upsert(db_session, season_id, current, make_fixture_data(1, **FT_1_0))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.execute(update(Fixture).values(updated_at=old))
    before = row_state(fixture(db_session))

    late_evidence = at(5)
    counts = upsert(db_session, season_id, late_evidence, make_fixture_data(1, referee="Otro", **late))

    assert counts == UpsertCounts(received=1, created=0, updated=0, unchanged=1)
    f = fixture(db_session)
    assert row_state(f) == before and f.referee is None and f.updated_at == old
    assert f.last_observed_at == current.observed_at
    obs = observations(db_session)
    assert [o.observed_at for o in obs] == [late_evidence.observed_at, current.observed_at]
    assert obs[0].status_short == late["status"] and obs[0].home_goals == late["home_goals"]


def test_mapping_last_seen_still_advances_with_older_evidence(db_session, season_id):
    upsert(db_session, season_id, at(10), make_fixture_data(1, **FT_1_0))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.execute(update(FixtureProviderMapping).values(last_seen_at=old))
    upsert(db_session, season_id, at(5), make_fixture_data(1, **FT_1_0))
    db_session.expire_all()
    assert db_session.scalars(select(FixtureProviderMapping)).one().last_seen_at > old


# --- Mismo observed_at ----------------------------------------------------------------------


def test_equal_timestamp_same_state_other_response_is_kept(db_session, season_id):
    first = at(0)
    upsert(db_session, season_id, first, make_fixture_data(1, **FT_1_0))
    other = make_evidence(observed_at=first.observed_at)  # otra respuesta, mismo instante
    counts = upsert(db_session, season_id, other, make_fixture_data(1, **FT_1_0))
    assert counts.unchanged == 1
    assert [o.evidence_id for o in observations(db_session)] == [first.evidence_id, other.evidence_id]


@pytest.mark.parametrize("first_wins_hash", [True, False])
def test_equal_timestamp_conflicting_states_keep_both_and_larger_hash_is_operational(db_session, season_id, first_wins_hash):
    states = [make_fixture_data(1, **FT_1_0), make_fixture_data(1, **FT_2_0)]
    hashes = state_hashes(db_session, [dict(row_state_of(db_session, season_id, s)) for s in states])
    larger = max(range(2), key=lambda i: hashes[i])
    order = [larger, 1 - larger] if first_wins_hash else [1 - larger, larger]
    t = T0
    for i in order:
        upsert(db_session, season_id, make_evidence(observed_at=t), states[i])

    f, obs = fixture(db_session), observations(db_session)
    assert len(obs) == 2 and {o.observed_at for o in obs} == {t}
    assert len({bytes(o.state_hash) for o in obs}) == 2  # TEMPORAL_AMBIGUITY en t
    assert f.home_goals == states[larger].home_goals  # canonicalización determinista, no cronología
    assert bytes(f.last_state_hash) == hashes[larger]


def row_state_of(db, season_id: int, data) -> dict:
    """Estado que guardaría upsert_fixtures para `data` (equipos ya existentes o creados aquí)."""
    team_ids = fixture_repository.ensure_teams(db, [data.home_team, data.away_team], "api-football")
    values = data.model_dump()
    values.update(season_id=season_id, home_team_id=team_ids[data.home_team.external_id], away_team_id=team_ids[data.away_team.external_id])
    return {c: values[c] for c in STATE_COLUMNS}


# --- Repetición exacta e identidad de la evidencia -------------------------------------------


def test_exact_replay_creates_nothing(db_session, season_id):
    ev = at(0)
    upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0), make_fixture_data(2, home=3, away=4))
    before = (row_state(fixture(db_session)), fixture(db_session).last_observed_at)
    counts = upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0), make_fixture_data(2, home=3, away=4))
    assert counts == UpsertCounts(received=2, created=0, updated=0, unchanged=2)
    assert len(observations(db_session, 1)) == len(observations(db_session, 2)) == 1
    assert (row_state(fixture(db_session)), fixture(db_session).last_observed_at) == before


@pytest.mark.parametrize(
    "change",
    [
        lambda ev: (FixtureEvidence(ev.source, ev.provider, ev.observed_at + timedelta(seconds=1), ev.evidence_id), FT_1_0),
        lambda ev: (ev, FT_2_0),
        lambda ev: (FixtureEvidence("backfill", ev.provider, ev.observed_at, ev.evidence_id), FT_1_0),
    ],
    ids=["other_observed_at", "other_state", "other_source"],
)
def test_inconsistent_reuse_of_evidence_id_is_rejected(db_session, season_id, change):
    ev = at(0)
    upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0))
    reused, values = change(ev)
    with pytest.raises(EvidenceIdentityConflict, match=str(ev.evidence_id)):
        upsert(db_session, season_id, reused, make_fixture_data(1, **values))


def test_one_response_shares_its_evidence_id_and_duplicates_are_one_observation(db_session, season_id):
    ev = at(0)
    counts = upsert(
        db_session, season_id, ev,
        make_fixture_data(1), make_fixture_data(2, home=3, away=4), make_fixture_data(1, status="1H", home_goals=0, away_goals=0),
    )
    assert counts.received == 2
    rows = db_session.execute(text("SELECT evidence_id, count(*) FROM fixture_observations GROUP BY evidence_id")).all()
    assert rows == [(ev.evidence_id, 2)]
    assert observations(db_session, 1)[0].status_short == "1H"  # el último de la respuesta, como antes


def test_evidence_must_match_the_writer(db_session, season_id):
    data = make_fixture_data(1)
    with pytest.raises(ValueError):
        upsert(db_session, season_id, make_evidence(provider="5dollarfootballapi"), data)
    with pytest.raises(ValueError):
        upsert(db_session, season_id, make_evidence(source="bootstrap", provider=None), data)
    assert db_session.execute(text("SELECT count(*) FROM fixture_observations")).scalar_one() == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(source="manual", provider=None, observed_at=T0),
        dict(source="sync", provider=None, observed_at=T0),
        dict(source="bootstrap", provider="api-football", observed_at=T0),
        dict(source="sync", provider="api-football", observed_at=datetime(2026, 9, 1, 12, 0)),
        dict(source="sync", provider="api-football", observed_at=T0.astimezone(timezone(timedelta(hours=3)))),
    ],
)
def test_fixture_evidence_validation(kwargs):
    with pytest.raises(ValueError):
        FixtureEvidence(**kwargs)


def test_received_gives_new_identity_and_utc_now():
    before = datetime.now(timezone.utc)
    a, b = FixtureEvidence.received("sync", "api-football"), FixtureEvidence.received("sync", "api-football")
    assert a.evidence_id != b.evidence_id
    assert before <= a.observed_at <= b.observed_at <= datetime.now(timezone.utc)


# --- G3: lo observado, no la fila fusionada ---------------------------------------------------


def test_partial_response_keeps_operational_pairs_but_history_is_as_observed(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_1_0))
    partial = at(10)
    upsert(db_session, season_id, partial, make_fixture_data(1, status="FT"))  # marcadores NULL

    f, obs = fixture(db_session), observations(db_session)
    assert (f.home_goals, f.away_goals, f.fulltime_home) == (1, 0, 1)  # fusión permitida, solo en fixtures
    assert (obs[1].home_goals, obs[1].away_goals, obs[1].fulltime_home) == (None, None, None)  # tal cual
    assert bytes(f.last_state_hash) == bytes(obs[1].state_hash)  # hash de la observación ganadora...
    assert bytes(f.last_state_hash) != state_hashes(db_session, [row_state(f)])[0]  # ...no el de la fila
    assert observed_state(obs[0])["home_goals"] == 1  # la evidencia histórica no se toca


def test_observations_are_never_updated(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_1_0))
    [first] = observations(db_session)
    snapshot = (first.id, observed_state(first), bytes(first.state_hash), first.recorded_at)
    for minutes, values in ((10, FT_2_0), (5, {"status": "PST"}), (20, {"status": "FT"})):
        upsert(db_session, season_id, at(minutes), make_fixture_data(1, **values))
    again = next(o for o in observations(db_session) if o.id == snapshot[0])
    assert (again.id, observed_state(again), bytes(again.state_hash), again.recorded_at) == snapshot


# --- Atomicidad ---------------------------------------------------------------------------


def test_rollback_discards_observation_and_state_together(db_session, season_id):
    upsert(db_session, season_id, at(0), make_fixture_data(1, **FT_1_0))
    before = (row_state(fixture(db_session)), fixture(db_session).last_observed_at, len(observations(db_session)))
    savepoint = db_session.begin_nested()
    upsert(db_session, season_id, at(10), make_fixture_data(1, **FT_2_0))
    savepoint.rollback()
    assert (row_state(fixture(db_session)), fixture(db_session).last_observed_at, len(observations(db_session))) == before


def test_conflict_error_happens_inside_the_transaction(db_session, season_id):
    """Una reutilización incoherente lanza antes de que el llamante haga commit: su rollback lo deshace todo."""
    ev = at(0)
    upsert(db_session, season_id, ev, make_fixture_data(1, **FT_1_0))
    before = (row_state(fixture(db_session)), fixture(db_session).last_observed_at)
    savepoint = db_session.begin_nested()
    with pytest.raises(EvidenceIdentityConflict):
        reused = FixtureEvidence(ev.source, ev.provider, ev.observed_at + timedelta(hours=1), ev.evidence_id)
        upsert(db_session, season_id, reused, make_fixture_data(1, **FT_2_0))
    savepoint.rollback()
    assert (row_state(fixture(db_session)), fixture(db_session).last_observed_at) == before
    assert len(observations(db_session)) == 1
