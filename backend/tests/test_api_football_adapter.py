"""Tests unitarios del adapter de API-Football (sin red ni BD)."""

import asyncio

import pytest

from app.integrations.exceptions import ProviderAuthError, ProviderRateLimitError, ProviderResponseError
from app.integrations.football import api_football
from app.integrations.football.api_football import ApiFootballProvider
from app.repositories.provider_mapping_repository import canonical_external_id
from tests.conftest import load_json


@pytest.fixture
def provider() -> ApiFootballProvider:
    return ApiFootballProvider(api_key="test-key", base_url="https://example.invalid", timeout=1)


@pytest.fixture
def respond_with(monkeypatch):
    """Hace que el adapter reciba `payload` en lugar de llamar a la API."""

    def _set(payload: dict) -> None:
        async def fake_get_json(**_kwargs):
            return payload

        monkeypatch.setattr(api_football, "get_json", fake_get_json)

    return _set


def _fixtures_by_id(provider, respond_with) -> dict:
    respond_with(load_json("api_football/fixtures_mixed.json"))
    fixtures = asyncio.run(provider.get_fixtures(265, 2026))
    return {f.external_id: f for f in fixtures}


# --- Marcador a 90' -----------------------------------------------------------------------


def test_fixture_ft_fulltime_from_score(provider, respond_with):
    f = _fixtures_by_id(provider, respond_with)[101]
    assert (f.status_short, f.fulltime_home, f.fulltime_away) == ("FT", 2, 1)


def test_fixture_aet_fulltime_excludes_extratime(provider, respond_with):
    f = _fixtures_by_id(provider, respond_with)[102]
    assert (f.fulltime_home, f.fulltime_away) == (2, 2)
    assert (f.home_goals, f.away_goals) == (3, 2)  # goals sigue incluyendo la prórroga


def test_fixture_pen_fulltime(provider, respond_with):
    f = _fixtures_by_id(provider, respond_with)[103]
    assert (f.fulltime_home, f.fulltime_away) == (1, 1)
    assert (f.penalty_home, f.penalty_away) == (4, 3)


@pytest.mark.parametrize("external_id", [104, 105, 106])  # NS, AWD, 2H
def test_fixture_not_finished_fulltime_none(provider, respond_with, external_id):
    f = _fixtures_by_id(provider, respond_with)[external_id]
    # el proveedor envía valores en score.fulltime, pero el partido no terminó (o fue adjudicado)
    assert (f.fulltime_home, f.fulltime_away) == (None, None)


# --- Errores en HTTP 200 --------------------------------------------------------------------


def test_errors_rate_limit_raises_rate_limit_error(provider, respond_with):
    respond_with(load_json("api_football/errors_rate_limit.json"))
    with pytest.raises(ProviderRateLimitError):
        asyncio.run(provider.get_fixtures(265, 2026))


def test_errors_daily_quota_raises_rate_limit_error(provider, respond_with):
    respond_with(load_json("api_football/errors_daily_quota.json"))
    with pytest.raises(ProviderRateLimitError):
        asyncio.run(provider.get_fixtures(265, 2026))


def test_errors_token_raises_auth_error(provider, respond_with):
    respond_with(load_json("api_football/errors_token.json"))
    with pytest.raises(ProviderAuthError):
        asyncio.run(provider.check_status())


def test_errors_other_raises_response_error(provider, respond_with):
    respond_with(load_json("api_football/errors_other.json"))
    with pytest.raises(ProviderResponseError) as exc:
        asyncio.run(provider.get_fixtures(265, 1800))
    assert not isinstance(exc.value, (ProviderRateLimitError, ProviderAuthError))


# --- Guarda de paginación -----------------------------------------------------------------


def test_paging_guard_raises_when_total_gt_1(provider, respond_with):
    respond_with(load_json("api_football/paging_multi.json"))
    with pytest.raises(ProviderResponseError, match="paginada"):
        asyncio.run(provider.get_fixtures(265, 2026))


def test_paging_guard_allows_when_caller_paginates(provider, respond_with):
    respond_with(load_json("api_football/paging_multi.json"))
    data = asyncio.run(provider._get("/players", allow_paging=True))
    assert data["paging"]["total"] == 3


@pytest.mark.parametrize("paging", [{"current": 1, "total": 1}, None, {}])
def test_paging_single_page_or_missing_ok(provider, respond_with, paging):
    payload = load_json("api_football/fixtures_mixed.json")
    payload["paging"] = paging
    respond_with(payload)
    assert len(asyncio.run(provider.get_fixtures(265, 2026))) == 6


# --- IDs externos -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1505529, "1505529"), (" 4160026622 ", "4160026622"), ("007", "7"), ("bet365", "bet365")],
)
def test_canonical_external_id(value, expected):
    assert canonical_external_id(value) == expected
