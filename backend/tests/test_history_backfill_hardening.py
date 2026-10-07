"""Hardening pre-2026 del backfill histórico (PostgreSQL, branch de tests): PARITY completo y
bloqueante, Q2/Q3/Q4 con una responsabilidad cada uno, ninguna transacción abierta durante la
descarga, revalidación tras ella, cierre 'completed' atómico con el dominio y evidencia en la
recuperación de runs abandonados. Ningún test llama a la API.
"""

import asyncio

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from app.models import Fixture, Season, SeasonBackfillRun
from app.repositories import backfill_repository as runs
from app.services import history_backfill_service as service
from tests.test_history_backfill import (  # noqa: F401  (setup es una fixture de pytest)
    NOW,
    YEAR,
    FakeHistoryProvider,
    backfill,
    count,
    domain_counts,
    fx,
    run_row,
    setup,
)

pytestmark = pytest.mark.db


def _check(checks, check_id):
    found = [c for c in checks if (c["id"] if isinstance(c, dict) else c.id) == check_id]
    assert len(found) == 1, f"{check_id} aparece {len(found)} veces"
    return found[0] if isinstance(found[0], dict) else found[0].model_dump()


class HookProvider(FakeHistoryProvider):
    """Proveedor falso que ejecuta `hook` durante la descarga (simula lo que pasa mientras esperamos HTTP)."""

    def __init__(self, fixtures, hook):
        super().__init__(fixtures)
        self.hook = hook

    async def get_fixtures(self, competition_external_id, season, date_from=None, date_to=None):
        self.hook()
        return await super().get_fixtures(competition_external_id, season, date_from, date_to)


# --- PARITY -----------------------------------------------------------------------------------


def test_parity_covers_new_fixtures_and_passes(db_session, setup):
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4), fx(3, home=5, away=6)]))
    assert result.status == "completed"
    parity = _check(result.checks, "PARITY")
    assert parity["passed"] and parity["severity"] == "blocking" and "3 comparados (nuevos 3, existentes 0)" in parity["detail"]


def test_parity_not_applicable_in_dry_run(db_session, setup):
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1)]), dry_run=True)
    parity = _check(result.checks, "PARITY")
    assert parity["passed"] and "no aplica" in parity["detail"]
    assert "no aplica" in _check(result.checks, "Q2")["detail"]


def test_parity_mismatch_blocks_and_rolls_back_everything(db_session, setup, monkeypatch):
    real_predict = service.predict_stored_values

    def wrong_prediction(stored, incoming):
        predicted = real_predict(stored, incoming)
        return {**predicted, "referee": "árbitro que nunca se guardó"}

    monkeypatch.setattr(service, "predict_stored_values", wrong_prediction)
    before = domain_counts(db_session)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    assert result.status == "blocked" and "PARITY" in result.error_message
    parity = _check(result.checks, "PARITY")
    assert not parity["passed"] and parity["count"] == 2 and sorted(parity["samples"]) == [1, 2]
    assert domain_counts(db_session) == before
    assert run_row(db_session, result.run_id).status == "blocked"


def test_parity_detects_wrong_mapping_count(db_session, setup, monkeypatch):
    monkeypatch.setattr(service.runs, "provider_mapping_count", lambda *_a: 0)
    before = domain_counts(db_session)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1)]))
    assert result.status == "blocked" and "PARITY" in result.error_message
    assert domain_counts(db_session) == before


def test_parity_on_refresh_compares_existing_and_new(db_session, setup):
    backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    refreshed = [fx(1), fx(2, home=3, away=4, home_goals=2, away_goals=2, fulltime_home=2, fulltime_away=2), fx(3, home=5, away=6)]
    result = backfill(db_session, setup, FakeHistoryProvider(refreshed), refresh=True)
    assert result.status == "completed"
    assert "3 comparados (nuevos 1, existentes 2)" in _check(result.checks, "PARITY")["detail"]


# --- Q2 / Q3 / Q4 -------------------------------------------------------------------------------


def test_q2_q3_q4_appear_once_with_their_scope(db_session, setup):
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1)]))
    q2, q3, q4 = (_check(result.checks, c) for c in ("Q2", "Q3", "Q4"))
    assert q2["passed"] and "Fixtures de la temporada: 0 antes, 1 después" in q2["detail"]
    assert "Pre-write, global" in q3["detail"] and "Pre-write, global" in q4["detail"]


def test_q2_blocks_if_a_season_fixture_disappears(db_session, setup, monkeypatch):
    backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    calls = {"n": 0}
    real = service.runs.season_fixture_external_ids

    def shrinking(db, season_id):
        calls["n"] += 1
        ids = real(db, season_id)
        return ids if calls["n"] == 1 else ids - {2}  # tras escribir "falta" el 2

    monkeypatch.setattr(service.runs, "season_fixture_external_ids", shrinking)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]), refresh=True)
    assert result.status == "blocked" and "Q2" in result.error_message
    assert _check(result.checks, "Q2")["samples"] == [2]


# --- Transacciones ---------------------------------------------------------------------------


def test_no_db_transaction_is_open_during_the_provider_call(db_session, setup):
    seen = []
    result = backfill(db_session, setup, HookProvider([fx(1)], lambda: seen.append(db_session.in_transaction())))
    assert result.status == "completed" and seen == [False]


def test_season_becoming_current_during_download_blocks_without_writes(db_session, setup):
    def make_current():
        db_session.execute(update(Season).where(Season.id == setup["season_id"]).values(is_current=True))
        db_session.commit()

    before = domain_counts(db_session)
    result = backfill(db_session, setup, HookProvider([fx(1)], make_current))
    assert result.status == "blocked" and result.error_message.startswith("Revalidación tras la descarga")
    assert domain_counts(db_session) == before


def test_run_failed_by_another_process_during_download_is_not_overwritten(db_session, setup):
    holder = {}

    def recover_meanwhile():
        run_id = db_session.scalar(select(func.max(SeasonBackfillRun.id)))
        holder["run_id"] = run_id
        runs.finish_run(db_session, run_id, status="failed", error_message="Recuperación administrativa (simulada)")
        db_session.commit()

    before = domain_counts(db_session)
    result = backfill(db_session, setup, HookProvider([fx(1)], recover_meanwhile))
    assert result.status == "failed" and "durante la descarga" in result.error_message
    assert domain_counts(db_session) == before
    row = run_row(db_session, holder["run_id"])
    assert row.status == "failed" and row.error_message == "Recuperación administrativa (simulada)"  # no se pisa


def test_completed_is_atomic_with_the_domain_writes(db_session, setup, monkeypatch):
    """Si cerrar el run como completed falla, tampoco queda nada del dominio."""
    real_finish = service.runs.finish_run

    def failing_on_completed(db, run_id, *, status, **values):
        if status == "completed":
            raise OperationalError("UPDATE season_backfill_runs", {}, Exception("simulado"))
        return real_finish(db, run_id, status=status, **values)

    monkeypatch.setattr(service.runs, "finish_run", failing_on_completed)
    before = domain_counts(db_session)
    result = backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    assert result.status == "failed" and "OperationalError" in result.error_message
    assert domain_counts(db_session) == before
    assert run_row(db_session, result.run_id).status == "failed"


def test_second_run_while_one_is_running_is_rejected_without_new_row(db_session, setup):
    runs.create_run(
        db_session, competition_id=setup["competition_id"], requested_year=YEAR, provider="api-football",
        is_dry_run=False, is_refresh=False, season_id=setup["season_id"],
    )
    db_session.commit()
    before = count(db_session, SeasonBackfillRun)
    provider = FakeHistoryProvider([fx(1)])
    with pytest.raises(ValueError, match="en curso"):
        backfill(db_session, setup, provider)
    assert provider.calls == [] and count(db_session, SeasonBackfillRun) == before


# --- Recuperación de runs abandonados -----------------------------------------------------------


def test_stale_recovery_records_read_only_evidence(db_session, setup):
    backfill(db_session, setup, FakeHistoryProvider([fx(1), fx(2, home=3, away=4)]))
    run_id = runs.create_run(
        db_session, competition_id=setup["competition_id"], requested_year=YEAR, provider="api-football",
        is_dry_run=False, is_refresh=True, season_id=setup["season_id"],
    )
    db_session.execute(
        update(SeasonBackfillRun).where(SeasonBackfillRun.id == run_id).values(started_at=func.now() - func.make_interval(0, 0, 0, 0, 0, 300))
    )
    db_session.commit()
    before = count(db_session, Fixture)
    recovery = service.fail_stale_run(db_session, setup["competition_id"], YEAR, 120)
    assert recovery.outcome == "recovered"
    row = run_row(db_session, run_id)
    assert row.status == "failed" and "Evidencia: 2 fixtures en la temporada" in row.error_message
    assert count(db_session, Fixture) == before  # solo lectura


def test_full_flow_still_completes(db_session, setup):
    result = asyncio.run(
        service.run_backfill(
            db_session, setup["competition_id"], YEAR, provider=FakeHistoryProvider([fx(1)]), now=NOW, expected_range=(1, 1)
        )
    )
    assert result.status == "completed" and run_row(db_session, result.run_id).status == "completed"
