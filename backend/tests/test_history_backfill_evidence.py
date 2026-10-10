"""Backfill histórico y evidencia temporal de fixtures (DI-A6), adaptación Modular-facing.

El comportamiento del backfill es de Modular; aquí solo se comprueba el contrato DI-A6C que adopta:
evidence_id por par, observed_at = recepción real (no el `now` del run), source='backfill',
atomicidad con el par y PARITY consciente del orden (observed_at, state_hash).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError

from app.models import Fixture, FixtureObservation, FixtureProviderMapping
from app.services import history_backfill_service as service
from tests.test_history_backfill import NOW, FakeHistoryProvider, backfill, count, domain_counts, fx, setup  # noqa: F401

pytestmark = pytest.mark.db


def _observations(db) -> list[FixtureObservation]:
    db.expire_all()
    return list(db.scalars(select(FixtureObservation).order_by(FixtureObservation.observed_at, FixtureObservation.id)))


def test_backfill_writes_one_evidence_per_pair_at_real_receipt(db_session, setup):
    started = datetime.now(timezone.utc)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    finished = datetime.now(timezone.utc)

    assert result.status == "completed"
    obs = _observations(db_session)
    assert len(obs) == 2 and len({o.evidence_id for o in obs}) == 1
    assert all((o.source, o.provider) == ("backfill", "api-football") for o in obs)
    observed_at = obs[0].observed_at
    assert started <= observed_at <= finished  # recepción real...
    assert observed_at != NOW and all(observed_at != o.kickoff_at for o in obs)  # ...no el now del run ni el kickoff
    assert all(f.last_observed_at == observed_at for f in db_session.scalars(select(Fixture)))


def test_dry_run_and_blocked_runs_leave_no_evidence(db_session, setup):
    assert backfill(db_session, setup, FakeHistoryProvider([fx(1)]), dry_run=True).status == "dry_run_completed"
    blocked = backfill(db_session, setup, FakeHistoryProvider([fx(1)]), expected_range=(5, 10))
    assert blocked.status == "blocked"
    assert _observations(db_session) == [] and count(db_session, Fixture) == 0


def test_refresh_is_a_new_independent_confirmation(db_session, setup):
    assert backfill(db_session, setup, FakeHistoryProvider([fx(1)])).status == "completed"
    again = backfill(db_session, setup, FakeHistoryProvider([fx(1)]), refresh=True)
    assert again.status == "completed" and again.unchanged == 1
    obs = _observations(db_session)
    assert len(obs) == 2 and obs[0].evidence_id != obs[1].evidence_id and obs[0].observed_at < obs[1].observed_at


def test_parity_accepts_evidence_older_than_the_current_state(db_session, setup):
    """Si el partido ya tiene una evidencia más nueva, el refresh entra en la historia sin tocar
    fixtures, y PARITY lo prevé así (no bloquea por no haber aplicado el cambio)."""
    assert backfill(db_session, setup, FakeHistoryProvider([fx(1)])).status == "completed"
    future = datetime.now(timezone.utc) + timedelta(days=1)
    db_session.execute(update(Fixture).values(last_observed_at=future))  # alguien vio algo más nuevo
    db_session.commit()

    result = backfill(db_session, setup, FakeHistoryProvider([fx(1, home_goals=3, fulltime_home=3)]), refresh=True)
    assert result.status == "completed", result.error_message
    db_session.expire_all()
    f = db_session.scalars(select(Fixture)).one()
    assert (f.home_goals, f.last_observed_at) == (1, future)  # la evidencia antigua no pisa el estado
    assert [o.home_goals for o in _observations(db_session)] == [1, 3]  # pero queda en la historia


def test_parity_blocks_when_the_evidence_is_incomplete(db_session, setup, monkeypatch):
    """Una observación que falta (o sobra) es un PARITY fallido: rollback de todo el par."""
    real = service.fixture_repository.upsert_fixtures

    def losing_one_observation(db, season_id, fixtures, team_ids, provider, evidence):
        counts = real(db, season_id, fixtures, team_ids, provider, evidence)
        db.execute(delete(FixtureObservation).where(FixtureObservation.id == db.scalar(select(FixtureObservation.id).limit(1))))
        return counts

    monkeypatch.setattr(service.fixture_repository, "upsert_fixtures", losing_one_observation)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    assert result.status == "blocked" and "PARITY" in result.error_message
    assert _observations(db_session) == [] and count(db_session, Fixture) == 0


def _fail_after_the_writer(monkeypatch):
    """Error de BD dentro de la transacción del par, DESPUÉS de upsert_fixtures (fixtures, evidencia y
    mappings ya escritos): el siguiente paso del servicio (season_teams) falla."""
    def failing(*_args, **_kwargs):
        raise OperationalError("INSERT INTO season_teams", {}, Exception("conexión simulada caída"))

    monkeypatch.setattr(service.catalog_repository, "link_teams_to_season", failing)


def _snapshot(db):
    db.expire_all()
    fixtures = sorted((f.external_id, f.home_goals, f.last_observed_at, bytes(f.last_state_hash)) for f in db.scalars(select(Fixture)))
    observations = sorted((o.fixture_id, o.evidence_id, o.observed_at) for o in db.scalars(select(FixtureObservation)))
    mappings = sorted((m.external_id, m.last_seen_at) for m in db.scalars(select(FixtureProviderMapping)))
    return fixtures, observations, mappings, domain_counts(db)


def test_db_error_after_writing_rolls_back_fixtures_evidence_and_mappings(db_session, setup, monkeypatch):
    """Hueco cerrado (DI-A6): el camino `except _DB_ERRORS` del backfill no deja nada a medias."""
    before = _snapshot(db_session)
    _fail_after_the_writer(monkeypatch)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    assert result.status == "failed" and "Error de base de datos" in result.error_message
    assert result.received == 2  # el análisis previo se informa, pero no es estado confirmado
    assert _snapshot(db_session) == before  # ni fixtures, ni observaciones, ni mappings, ni equipos
    assert _observations(db_session) == []


def test_db_error_on_refresh_keeps_the_previously_committed_state(db_session, setup, monkeypatch):
    """Con un estado ya confirmado (backfill anterior), un refresh que falla tras escribir deja ese
    estado intacto: los datos, la evidencia (cardinalidad incluida) y el last_seen_at de los mappings."""
    assert backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)])).status == "completed"
    committed = _snapshot(db_session)
    _fail_after_the_writer(monkeypatch)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1, home_goals=3, fulltime_home=3), fx(2, home=3, away=4)]), refresh=True)
    assert result.status == "failed"
    assert _snapshot(db_session) == committed
    assert len(_observations(db_session)) == 2
