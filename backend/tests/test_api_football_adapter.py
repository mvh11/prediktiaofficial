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


# --- Normalización de pares de marcador -------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, (None, None)),  # sin marcador
        ({"home": None, "away": None}, (None, None)),  # NULL completo
        ({}, (None, None)),  # objeto vacío
        ({"home": 2, "away": 1}, (2, 1)),  # válido
        ({"home": 0, "away": 0}, (0, 0)),  # 0 es válido
        ({"home": 2, "away": None}, (None, None)),  # home válido + away NULL
        ({"home": None, "away": 3}, (None, None)),  # home NULL + away válido
        ({"home": 2}, (None, None)),  # falta away
        ({"away": 2}, (None, None)),  # falta home
        ({"home": -1, "away": 0}, (None, None)),  # negativo
        ({"home": "1", "away": 1}, (None, None)),  # string
        ({"home": True, "away": False}, (None, None)),  # bool no es un entero válido
        ({"home": 1.0, "away": 2}, (None, None)),  # float
        ("1-0", (None, None)),  # estructura inválida
        ([1, 0], (None, None)),  # estructura inválida
    ],
)
def test_score_pair_normalization(raw, expected):
    assert api_football._score_pair(raw, "goals", 1) == expected


# --- Casos límite del proveedor (fixture JSON) ------------------------------------------


def _edge_fixtures(provider, respond_with) -> dict:
    respond_with(load_json("api_football/fixtures_score_edge_cases.json"))
    return {f.external_id: f for f in asyncio.run(provider.get_fixtures(265, 2026))}


def _pairs(f) -> dict:
    return {
        "goals": (f.home_goals, f.away_goals),
        "halftime": (f.halftime_home, f.halftime_away),
        "fulltime": (f.fulltime_home, f.fulltime_away),
        "extratime": (f.extratime_home, f.extratime_away),
        "penalty": (f.penalty_home, f.penalty_away),
    }


def test_invalid_scores_do_not_prevent_ingestion(provider, respond_with):
    assert set(_edge_fixtures(provider, respond_with)) == set(range(301, 311))


def test_never_half_pairs(provider, respond_with):
    for f in _edge_fixtures(provider, respond_with).values():
        for pair in _pairs(f).values():
            assert pair == (None, None) or None not in pair, (f.external_id, pair)


def test_ft_with_valid_fulltime_uses_score_fulltime(provider, respond_with):
    f = _edge_fixtures(provider, respond_with)[310]
    # goals llegó incompleto (se descarta) pero score.fulltime es válido y se usa tal cual
    assert _pairs(f)["goals"] == (None, None)
    assert _pairs(f)["fulltime"] == (1, 2)


@pytest.mark.parametrize("external_id", [301, 302])  # score.fulltime a null / sin la clave
def test_ft_without_fulltime_falls_back_to_goals(provider, respond_with, external_id):
    f = _edge_fixtures(provider, respond_with)[external_id]
    assert _pairs(f)["fulltime"] == _pairs(f)["goals"] != (None, None)


def test_aet_without_fulltime_is_null_never_goals(provider, respond_with):
    f = _edge_fixtures(provider, respond_with)[303]
    assert _pairs(f)["goals"] == (3, 2)
    assert _pairs(f)["extratime"] == (1, 0)
    # ni goals (3-2) ni goals - extratime (2-2)
    assert _pairs(f)["fulltime"] == (None, None)


def test_pen_without_fulltime_is_null_never_goals(provider, respond_with):
    f = _edge_fixtures(provider, respond_with)[304]
    assert _pairs(f)["goals"] == (1, 1)
    assert _pairs(f)["penalty"] == (5, 4)
    assert _pairs(f)["fulltime"] == (None, None)


def test_aet_invalid_fulltime_has_no_fallback(provider, respond_with, caplog):
    f = _edge_fixtures(provider, respond_with)[306]
    assert _pairs(f)["fulltime"] == (None, None)
    assert _pairs(f)["goals"] == (1, 0)
    assert "Partido 306: marcador fulltime inválido" in caplog.text


def test_incomplete_pairs_discarded_whole(provider, respond_with, caplog):
    f = _edge_fixtures(provider, respond_with)[305]
    # goals, halftime y fulltime venían con un lado NULL; sin goals válido no hay fallback FT
    assert _pairs(f)["goals"] == _pairs(f)["halftime"] == _pairs(f)["fulltime"] == (None, None)
    assert "Partido 305: marcador goals inválido" in caplog.text


def test_negative_goals_discarded_and_not_used_as_fulltime(provider, respond_with):
    f = _edge_fixtures(provider, respond_with)[307]
    assert _pairs(f)["goals"] == (None, None)
    assert _pairs(f)["fulltime"] == (None, None)
    assert _pairs(f)["halftime"] == (0, 0)


def test_structurally_invalid_scores_discarded(provider, respond_with, caplog):
    f = _edge_fixtures(provider, respond_with)[308]
    assert _pairs(f)["halftime"] == (None, None)  # "1-0" en lugar de un objeto
    assert _pairs(f)["extratime"] == (None, None)  # lista en lugar de un objeto
    assert _pairs(f)["penalty"] == (None, None)  # booleanos
    assert _pairs(f)["fulltime"] == (1, 1)  # fulltime con string se descarta; FT recurre a goals
    assert "Partido 308: marcador halftime con formato inesperado" in caplog.text


def test_provider_incoherence_kept_as_is(provider, respond_with):
    """Caso tipo fixture 6570: fulltime 4-0 con goals 2-0 y extratime 0-2. No se corrige."""
    f = _edge_fixtures(provider, respond_with)[309]
    assert _pairs(f)["goals"] == (2, 0)
    assert _pairs(f)["extratime"] == (0, 2)
    assert _pairs(f)["fulltime"] == (4, 0)


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
