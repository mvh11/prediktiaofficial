"""Tests unitarios del adapter de API-Football y de la guarda de BD de tests (sin red ni BD)."""

import asyncio
import logging

import pytest

from app.integrations.exceptions import ProviderAuthError, ProviderRateLimitError, ProviderResponseError
from app.integrations.football import api_football
from app.integrations.football.api_football import ApiFootballProvider
from app.repositories.provider_mapping_repository import canonical_external_id
from tests import conftest
from tests.conftest import ALLOW_DESTRUCTIVE_ENV, load_json, resolve_test_db


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


# --- Normalización de marcadores --------------------------------------------------------------


@pytest.fixture
def edge(provider, respond_with) -> dict:
    respond_with(load_json("api_football/fixtures_score_edge.json"))
    return {f.external_id: f for f in asyncio.run(provider.get_fixtures(265, 2026))}


def _pairs(f) -> dict:
    return {
        "goals": (f.home_goals, f.away_goals),
        "halftime": (f.halftime_home, f.halftime_away),
        "fulltime": (f.fulltime_home, f.fulltime_away),
    }


def test_invalid_scores_do_not_drop_fixtures(edge):
    assert set(edge) == {201, 202, 203, 204, 205, 206, 207, 208}


@pytest.mark.parametrize("external_id", [201, 202])  # score.fulltime a NULL / sin la clave
def test_ft_without_fulltime_uses_goals(edge, external_id):
    f = edge[external_id]
    assert (f.fulltime_home, f.fulltime_away) == (f.home_goals, f.away_goals) != (None, None)


@pytest.mark.parametrize("external_id", [203, 204])  # AET, PEN
def test_aet_pen_without_fulltime_never_use_goals(edge, external_id):
    f = edge[external_id]
    assert f.home_goals is not None
    assert (f.fulltime_home, f.fulltime_away) == (None, None)


def test_incomplete_pair_discarded_whole(edge):
    # goals {2, null} y halftime {1, null}: ni medio marcador ni fallback de fulltime
    assert _pairs(edge[205]) == {"goals": (None, None), "halftime": (None, None), "fulltime": (None, None)}


def test_incomplete_fulltime_not_replaced_by_goals(edge):
    # score.fulltime {2, null} es un dato inválido, no ausente: no se inventa con goals
    assert _pairs(edge[206]) == {"goals": (2, 1), "halftime": (1, 0), "fulltime": (None, None)}


def test_negative_scores_discarded(edge):
    assert _pairs(edge[207]) == {"goals": (1, 0), "halftime": (None, None), "fulltime": (None, None)}


def test_malformed_score_discarded(edge):
    assert _pairs(edge[208]) == {"goals": (1, 0), "halftime": (None, None), "fulltime": (1, 0)}


def test_invalid_scores_are_logged(provider, respond_with, caplog):
    respond_with(load_json("api_football/fixtures_score_edge.json"))
    with caplog.at_level(logging.WARNING, logger=api_football.__name__):
        asyncio.run(provider.get_fixtures(265, 2026))
    logged = {(r.args[0], r.args[1]) for r in caplog.records}
    assert {(205, "goals"), (205, "halftime"), (206, "fulltime"), (207, "halftime"), (207, "fulltime"), (208, "halftime")} == logged


# --- Guarda de la BD de tests -------------------------------------------------------------------
# Credenciales y endpoints ficticios

NEON_DIRECT = "postgresql+psycopg://u:p@ep-calm-sun-123456.us-east-2.aws.neon.tech/neondb?sslmode=require"
NEON_POOLER = "postgresql+psycopg://u:p@ep-calm-sun-123456-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
NEON_TARGET = "ep-calm-sun-123456.us-east-2.aws.neon.tech/neondb"
OTHER_NEON = "postgresql+psycopg://u:p@ep-other-999999-pooler.us-east-2.aws.neon.tech/neondb"
OTHER_TARGET = "ep-other-999999.us-east-2.aws.neon.tech/neondb"


def _resolve(test_url: str | None, dev_url: str | None = None, allow: str | None = None, dotenv_url: str | None = None):
    env = {k: v for k, v in {"TEST_DATABASE_URL": test_url, "DATABASE_URL": dev_url, ALLOW_DESTRUCTIVE_ENV: allow}.items() if v is not None}
    return resolve_test_db(env, dotenv_url)


def test_guard_allows_distinct_authorized_db():
    assert _resolve(OTHER_NEON, NEON_DIRECT, OTHER_TARGET) == (OTHER_NEON, "")


@pytest.mark.parametrize(
    ("test_url", "dev_url"),
    [(NEON_POOLER, NEON_DIRECT), (NEON_DIRECT, NEON_POOLER), (NEON_DIRECT, NEON_DIRECT)],
    ids=["pooler-vs-directo", "directo-vs-pooler", "misma-url"],
)
def test_guard_blocks_same_neon_db(test_url, dev_url):
    url, reason = _resolve(test_url, dev_url, NEON_TARGET)  # aunque esté autorizada
    assert url is None and "misma BD" in reason


def test_guard_blocks_same_db_from_dotenv():
    url, reason = _resolve(NEON_POOLER, None, NEON_TARGET, dotenv_url=NEON_DIRECT)
    assert url is None and ".env" in reason


def test_guard_blocks_local_aliases_and_other_port():
    test_url = "postgresql+psycopg://u:p@127.0.0.1:6432/prediktia"
    url, _ = _resolve(test_url, "postgresql+psycopg://u:p@localhost:5432/prediktia", "localhost/prediktia")
    assert url is None


@pytest.mark.parametrize("allow", [None, "", "1", "true", "neondb", NEON_TARGET])
def test_guard_requires_exact_authorization(allow):
    url, reason = _resolve(OTHER_NEON, NEON_DIRECT, allow)
    assert url is None and f"{ALLOW_DESTRUCTIVE_ENV}={OTHER_TARGET}" in reason


def test_guard_requires_explicit_test_url():
    # DATABASE_URL nunca sustituye a TEST_DATABASE_URL
    assert _resolve(None, NEON_DIRECT, NEON_TARGET)[0] is None


@pytest.mark.parametrize(
    "test_url",
    [
        "postgresql+psycopg://u:p@/neondb?host=ep-calm-sun-123456.us-east-2.aws.neon.tech",
        "postgresql+psycopg://u:p@proxy.local/neondb?options=endpoint%3Dep-calm-sun-123456",
        "sqlite:///tests.db",
        "esto no es una url",
    ],
)
def test_guard_blocks_unidentifiable_test_url(test_url):
    assert _resolve(test_url, NEON_DIRECT, "proxy.local/neondb")[0] is None


def test_guard_blocks_unparseable_dev_url():
    assert _resolve(OTHER_NEON, "esto no es una url", OTHER_TARGET)[0] is None


def test_destructive_operations_blocked_without_authorization(monkeypatch):
    monkeypatch.setattr(conftest, "TEST_DB_URL", None)
    with pytest.raises(RuntimeError, match="bloqueada"):
        conftest.alembic_run("downgrade", "base")
