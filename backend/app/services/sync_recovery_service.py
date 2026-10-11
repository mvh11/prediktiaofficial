"""A5E integration helpers for persistent sync recovery state.

Attempt persistence is fail-closed before provider HTTP. Success is staged inside
its domain transaction. Failure/skip/incident bookkeeping after rollback is
best-effort so it never hides the original outcome.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.integrations.exceptions import ProviderError
from app.repositories import sync_recovery_repository as repo

logger = logging.getLogger(__name__)


def begin_attempt(db: Session, unit_kind: str, provider: str, season_id: int) -> datetime:
    """Persist and commit last_attempt immediately before the provider request."""
    try:
        started_at = repo.begin_attempt(db, unit_kind, provider, season_id)
        db.commit()
        return started_at
    except Exception:
        db.rollback()
        raise


def stage_success(
    db: Session,
    unit_kind: str,
    provider: str,
    season_id: int,
    attempt_started_at: datetime,
) -> bool:
    """Stage success in the caller's current domain transaction; never commits."""
    return repo.record_success(db, unit_kind, provider, season_id, attempt_started_at)


def record_skipped_best_effort(
    db: Session,
    unit_kind: str,
    provider: str,
    season_id: int,
    attempt_started_at: datetime,
) -> None:
    try:
        repo.record_skipped(db, unit_kind, provider, season_id, attempt_started_at)
        db.commit()
    except Exception as exc:  # bookkeeping must not replace the real outcome
        db.rollback()
        logger.error("Recovery state: no se pudo guardar skipped (%s)", exc.__class__.__name__)


def record_failure_best_effort(
    db: Session,
    *,
    unit_kind: str | None,
    provider: str,
    season_id: int | None,
    attempt_started_at: datetime | None,
    error_scope: str,
    error_class: str,
    error_status: int | None = None,
    error_message: str | None = None,
    provider_error: ProviderError | None = None,
    endpoint: str | None = None,
    affected_units: int = 0,
) -> str | None:
    """Persist a failure and optional provider incident in one best-effort transaction.

    Unit state is written only when a recoverable attempt token exists. Provider
    incidents are independent of unit state, so e.g. an auth error on /leagues
    can still be recorded with affected_units=0.
    """
    incident_kind: str | None = None
    try:
        if unit_kind is not None and season_id is not None and attempt_started_at is not None:
            repo.record_failure(
                db,
                unit_kind,
                provider,
                season_id,
                attempt_started_at,
                error_scope=error_scope,
                error_class=error_class,
                error_status=error_status,
                error_message=error_message,
            )
        if provider_error is not None:
            incident_kind = repo.open_or_continue_incident(
                db,
                provider,
                provider_error,
                endpoint=endpoint,
                affected_units=affected_units,
            )
        db.commit()
    except Exception as exc:  # preserve the original sync/provider failure
        db.rollback()
        logger.error("Recovery state: no se pudo persistir el fallo (%s)", exc.__class__.__name__)
        if provider_error is not None:
            incident_kind = repo.incident_kind(provider_error)
    return incident_kind


def add_incident_affected_units_best_effort(db: Session, provider: str, kind: str | None, count: int = 1) -> None:
    if kind is None or count <= 0:
        return
    try:
        repo.add_incident_affected_units(db, provider, kind, count)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Recovery state: no se pudo ampliar affected_units (%s)", exc.__class__.__name__)


def close_recovered_incidents_best_effort(db: Session, provider: str, recovered_at: datetime) -> None:
    try:
        repo.close_recovered_incidents(db, provider, recovered_at)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Recovery state: no se pudieron cerrar incidentes recuperados (%s)", exc.__class__.__name__)
