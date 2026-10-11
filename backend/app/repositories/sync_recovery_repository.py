"""Persistence primitives for DI-A5 recovery state and provider incidents.

No function commits. Transaction boundaries belong to the sync service layer.
The schema is the draft 0009 train from DI-A5D; A5E only integrates it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.integrations.exceptions import (
    ProviderAuthError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
)
from app.models import ProviderIncident, SyncUnitState

FIXTURES_SEASON = "fixtures_season"
CATALOG_SEASON_TEAMS = "catalog_season_teams"

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|authorization|x-apisports-key)\b\s*[:=]\s*[^\s,;]+"
)
_ENDPOINT_RE = re.compile(r"^/[A-Za-z0-9/_-]*$")


def safe_error_message(value: str | None, fallback: str) -> str:
    """Sanitize persisted error text and enforce the 0009 500-char contract."""
    text = (value or fallback).replace("\x00", " ")
    text = _URL_RE.sub("[url]", text)
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = " ".join(text.split())
    return (text or fallback)[:500]


def incident_kind(exc: ProviderError) -> str | None:
    """Map only the provider-wide incident kinds frozen in A5D.

    ProviderNotConfiguredError is deliberately not an incident; outage remains
    RESERVED_BUT_INACTIVE until a later work package defines its producer.
    """
    if isinstance(exc, ProviderNotConfiguredError):
        return None
    if isinstance(exc, ProviderAuthError):
        return "auth"
    if isinstance(exc, ProviderQuotaExceededError):
        return "quota"
    if isinstance(exc, ProviderRateLimitError):
        return "rate_limit"
    return None


def begin_attempt(db: Session, unit_kind: str, provider: str, season_id: int) -> datetime:
    """Persist an attempt immediately before HTTP and return its DB timestamp token.

    The token lets a later outcome update only the same attempt. This is not a
    lease/claim (A5F); it merely prevents an older concurrent attempt from
    overwriting recovery metadata belonging to a newer attempt.
    """
    ts = func.statement_timestamp()
    stmt = pg_insert(SyncUnitState).values(
        unit_kind=unit_kind,
        provider=provider,
        season_id=season_id,
        last_attempt_started_at=ts,
        updated_at=ts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SyncUnitState.unit_kind, SyncUnitState.provider, SyncUnitState.season_id],
        set_={
            "last_attempt_started_at": stmt.excluded.last_attempt_started_at,
            "updated_at": stmt.excluded.updated_at,
        },
    ).returning(SyncUnitState.last_attempt_started_at)
    return db.execute(stmt).scalar_one()


def record_success(
    db: Session,
    unit_kind: str,
    provider: str,
    season_id: int,
    attempt_started_at: datetime,
) -> bool:
    """Stage success in the caller's domain transaction.

    A rollback of the fixture/catalog domain write also rolls this state back.
    Returns False only when a newer attempt already replaced the token.
    """
    ts = func.statement_timestamp()
    stmt = (
        update(SyncUnitState)
        .where(
            SyncUnitState.unit_kind == unit_kind,
            SyncUnitState.provider == provider,
            SyncUnitState.season_id == season_id,
            SyncUnitState.last_attempt_started_at == attempt_started_at,
        )
        .values(
            last_outcome="succeeded",
            last_outcome_at=ts,
            last_success_at=ts,
            consecutive_failures=0,
            last_failure_at=None,
            last_error_scope=None,
            last_error_class=None,
            last_error_status=None,
            last_error_message=None,
            updated_at=ts,
        )
        .returning(SyncUnitState.season_id)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def record_skipped(
    db: Session,
    unit_kind: str,
    provider: str,
    season_id: int,
    attempt_started_at: datetime,
) -> bool:
    """Record a post-HTTP skip for the same attempt without erasing prior failure history."""
    ts = func.statement_timestamp()
    stmt = (
        update(SyncUnitState)
        .where(
            SyncUnitState.unit_kind == unit_kind,
            SyncUnitState.provider == provider,
            SyncUnitState.season_id == season_id,
            SyncUnitState.last_attempt_started_at == attempt_started_at,
        )
        .values(last_outcome="skipped", last_outcome_at=ts, updated_at=ts)
        .returning(SyncUnitState.season_id)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def record_failure(
    db: Session,
    unit_kind: str,
    provider: str,
    season_id: int,
    attempt_started_at: datetime,
    *,
    error_scope: str,
    error_class: str,
    error_status: int | None,
    error_message: str | None,
) -> bool:
    """Record a failed outcome for the current attempt after the domain rollback."""
    ts = func.statement_timestamp()
    message = safe_error_message(error_message, error_class)
    stmt = (
        update(SyncUnitState)
        .where(
            SyncUnitState.unit_kind == unit_kind,
            SyncUnitState.provider == provider,
            SyncUnitState.season_id == season_id,
            SyncUnitState.last_attempt_started_at == attempt_started_at,
        )
        .values(
            last_outcome="failed",
            last_outcome_at=ts,
            consecutive_failures=SyncUnitState.consecutive_failures + 1,
            last_failure_at=ts,
            last_error_scope=error_scope,
            last_error_class=error_class,
            last_error_status=error_status,
            last_error_message=message,
            updated_at=ts,
        )
        .returning(SyncUnitState.season_id)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def open_or_continue_incident(
    db: Session,
    provider: str,
    exc: ProviderError,
    *,
    endpoint: str | None,
    affected_units: int,
) -> str | None:
    """Open/continue the frozen A5D incident kinds atomically.

    Returns the incident kind, or None for errors that must not create an
    incident. No host, query string, payload or credential is persisted.
    """
    kind = incident_kind(exc)
    if kind is None:
        return None
    endpoint = endpoint if endpoint and _ENDPOINT_RE.fullmatch(endpoint) else None
    retry_after = getattr(exc, "retry_after", None)
    retry_until = None
    if retry_after is not None:
        retry_until = datetime.now(timezone.utc) + timedelta(seconds=max(float(retry_after), 0.0))

    ts = func.statement_timestamp()
    stmt = pg_insert(ProviderIncident).values(
        provider=provider,
        kind=kind,
        last_failure_at=ts,
        failure_count=1,
        affected_units=max(affected_units, 0),
        last_error_class=exc.__class__.__name__,
        last_status_code=exc.status_code,
        last_endpoint=endpoint,
        retry_after_until=retry_until,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ProviderIncident.provider, ProviderIncident.kind],
        index_where=ProviderIncident.closed_at.is_(None),
        set_={
            "last_failure_at": stmt.excluded.last_failure_at,
            "failure_count": ProviderIncident.failure_count + 1,
            "affected_units": ProviderIncident.affected_units + stmt.excluded.affected_units,
            "last_error_class": stmt.excluded.last_error_class,
            "last_status_code": stmt.excluded.last_status_code,
            "last_endpoint": stmt.excluded.last_endpoint,
            "retry_after_until": stmt.excluded.retry_after_until,
        },
    )
    db.execute(stmt)
    return kind


def add_incident_affected_units(db: Session, provider: str, kind: str, count: int = 1) -> int:
    if count <= 0:
        return 0
    result = db.execute(
        update(ProviderIncident)
        .where(
            ProviderIncident.provider == provider,
            ProviderIncident.kind == kind,
            ProviderIncident.closed_at.is_(None),
        )
        .values(affected_units=ProviderIncident.affected_units + count)
    )
    return int(result.rowcount or 0)


def close_recovered_incidents(db: Session, provider: str, recovered_at: datetime) -> int:
    """Close only incidents whose latest failure is not newer than this good response."""
    ts = func.statement_timestamp()
    result = db.execute(
        update(ProviderIncident)
        .where(
            ProviderIncident.provider == provider,
            ProviderIncident.closed_at.is_(None),
            ProviderIncident.last_failure_at <= recovered_at,
        )
        .values(closed_at=ts, close_reason="provider_recovered")
    )
    return int(result.rowcount or 0)
