"""Esquemas del backfill histórico: resultado de cada check y de cada ejecución."""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["blocking", "warning"]
MAX_SAMPLES = 5


class CheckResult(BaseModel):
    """Resultado de un control de calidad (Q1–Q15). Nunca lleva dumps de fixtures."""

    id: str
    severity: Severity
    passed: bool
    count: int = 0
    detail: str = ""
    # Como mucho MAX_SAMPLES external_id de ejemplo, para poder investigar sin volcar datos
    samples: list[int] = Field(default_factory=list)

    @property
    def is_blocking_failure(self) -> bool:
        return not self.passed and self.severity == "blocking"

    @property
    def is_warning(self) -> bool:
        return not self.passed and self.severity == "warning"


class BackfillResult(BaseModel):
    run_id: int
    competition_id: int
    competition_name: str
    requested_year: int
    season_id: int | None
    provider: str
    is_dry_run: bool
    is_refresh: bool
    status: str
    received: int = 0
    new: int = 0
    existing: int = 0
    changed: int = 0
    unchanged: int = 0
    new_teams: int = 0
    new_season_teams: int = 0
    warnings: int = 0
    blocking: int = 0
    checks: list[CheckResult] = Field(default_factory=list)
    error_message: str | None = None


class StaleRunRecovery(BaseModel):
    """Resultado de marcar como failed un run abandonado en 'running'."""

    outcome: Literal["recovered", "not_found", "too_recent"]
    competition_id: int
    requested_year: int
    stale_after_minutes: int
    run_id: int | None = None
    message: str
