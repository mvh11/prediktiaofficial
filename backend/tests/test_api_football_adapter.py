"""Tests unitarios del adapter de API-Football y de la guarda de BD de tests (sin red ni BD)."""

import asyncio
import logging

import pytest

from app.integrations.exceptions import ProviderAuthError, ProviderRateLimitError, ProviderResponseError
from app.integrations.football import api_football
from app.integrations.football.api_football import ApiFootballProvider
from app.repositories.provider_mapping_repository import canonical_external_id
from tests import conftest
from tests.conftest import ALLOW_DESTRUCTIVE_ENV, authorization_target, load_json, resolve_test_db


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
    # fulltime con un string: presente pero inválido, así que se descarta y NO recurre a goals
    # (solo un fulltime ausente o a null se sustituye por goals en FT; ver casos 201/202 y 206)
    assert _pairs(f)["fulltime"] == (None, None)
    assert _pairs(f)["goals"] == (1, 1)
    assert "Partido 308: marcador halftime con formato inesperado" in caplog.text


def test_provider_incoherence_kept_as_is(provider, respond_with):
    """Caso tipo fixture 6570: fulltime 4-0 con goals 2-0 y extratime 0-2. No se corrige."""
    f = _edge_fixtures(provider, respond_with)[309]
    assert _pairs(f)["goals"] == (2, 0)
    assert _pairs(f)["extratime"] == (0, 2)
    assert _pairs(f)["fulltime"] == (4, 0)


# --- Estado canónico y partidos no jugados ---------------------------------------------------

_MISSING = object()
_SCORE_FIELDS = (
    "home_goals", "away_goals", "halftime_home", "halftime_away", "extratime_home", "extratime_away",
    "penalty_home", "penalty_away", "fulltime_home", "fulltime_away",
)


def _single(provider, respond_with, short=_MISSING, goals=(0, 0), halftime=(0, 0), fulltime=(0, 0), extratime=(None, None), penalty=(None, None), long="Some Status"):
    """Un único partido del proveedor con el status y los marcadores indicados."""
    status = {"long": long, "elapsed": None}
    if short is not _MISSING:
        status["short"] = short
    pair = lambda p: {"home": p[0], "away": p[1]}  # noqa: E731
    item = {
        "fixture": {"id": 900, "date": "2025-12-20T19:00:00+00:00", "status": status, "venue": {}},
        "league": {"round": "Final"},
        "teams": {"home": {"id": 1, "name": "Local"}, "away": {"id": 2, "name": "Visitante"}},
        "goals": pair(goals),
        "score": {"halftime": pair(halftime), "fulltime": pair(fulltime), "extratime": pair(extratime), "penalty": pair(penalty)},
    }
    respond_with({"errors": [], "paging": {"current": 1, "total": 1}, "response": [item]})
    return asyncio.run(provider.get_fixtures(344, 2025))[0]


def _scores(f) -> tuple:
    return tuple(getattr(f, c) for c in _SCORE_FIELDS)


@pytest.mark.parametrize(
    ("short", "expected"),
    [("Canc", "CANC"), ("canc", "CANC"), ("CANC", "CANC"), ("ft", "FT"), ("FT", "FT"), (None, "TBD"), ("", "TBD"), (_MISSING, "TBD")],
)
def test_status_short_is_canonical_uppercase(provider, respond_with, short, expected):
    f = _single(provider, respond_with, short=short, long="Match Cancelled")
    assert f.status_short == expected
    assert f.status_long == "Match Cancelled"  # status.long no se toca


@pytest.mark.parametrize("short", ["NS", "TBD", "PST", "CANC", "pst", "Canc"])
@pytest.mark.parametrize(
    "scores",
    [
        dict(goals=(0, 0), halftime=(0, 0), fulltime=(0, 0)),  # marcador 0-0 "falso"
        dict(goals=(2, 1), halftime=(1, 0), fulltime=(2, 1), extratime=(0, 0), penalty=(4, 3)),  # pares completos
        dict(goals=(1, None), halftime=(None, 0), fulltime=(3, 3)),  # pares parciales
    ],
)
def test_unplayed_statuses_drop_every_score_pair(provider, respond_with, short, scores):
    f = _single(provider, respond_with, short=short, **scores)
    assert f.status_short in api_football.UNPLAYED_STATUSES
    assert _scores(f) == (None,) * len(_SCORE_FIELDS)


def test_cancelled_with_zero_zero_goals_is_not_a_result(provider, respond_with):
    """Respuesta como la de un partido cancelado de Bolivia 2025: "Canc" con goals 0-0."""
    from datetime import datetime, timezone

    from app.services import history_quality_checks as qc

    f = _single(provider, respond_with, short="Canc", long="Match Cancelled", goals=(0, 0), halftime=(None, None), fulltime=(None, None))
    assert (f.status_short, f.status_long) == ("CANC", "Match Cancelled")
    assert _scores(f) == (None,) * len(_SCORE_FIELDS)
    q14 = qc.q14_unfinished_in_closed_season([f], season_closed=True, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert q14.passed and q14.count == 0  # CANC es terminal: sin warning


def test_abandoned_keeps_its_scores(provider, respond_with):
    """ABD no es "no jugado": conserva goals/halftime; fulltime sigue siendo solo de FT/AET/PEN."""
    f = _single(provider, respond_with, short="ABD", goals=(1, 0), halftime=(1, 0), fulltime=(1, 0))
    assert (f.home_goals, f.away_goals, f.halftime_home, f.halftime_away) == (1, 0, 1, 0)
    assert (f.fulltime_home, f.fulltime_away) == (None, None)


@pytest.mark.parametrize("short", ["AWD", "awd", "WO"])
def test_awarded_and_walkover_keep_their_scores(provider, respond_with, short):
    """AWD/WO: el marcador adjudicado se conserva como hasta ahora (fulltime solo en FT/AET/PEN)."""
    f = _single(provider, respond_with, short=short, goals=(0, 3), halftime=(0, 1), fulltime=(0, 3))
    assert f.status_short == short.upper()
    assert (f.home_goals, f.away_goals, f.halftime_home, f.halftime_away) == (0, 3, 0, 1)
    assert (f.fulltime_home, f.fulltime_away) == (None, None)


def test_lowercase_played_statuses_keep_hardened_semantics(provider, respond_with):
    ft = _single(provider, respond_with, short="ft", goals=(2, 1), halftime=(1, 0), fulltime=(None, None))
    assert (ft.status_short, ft.fulltime_home, ft.fulltime_away) == ("FT", 2, 1)  # FT sin fulltime: goals
    aet = _single(provider, respond_with, short="aet", goals=(3, 2), halftime=(1, 1), fulltime=(None, None), extratime=(1, 0))
    assert (aet.status_short, aet.fulltime_home, aet.fulltime_away) == ("AET", None, None)  # nunca goals - extratime
    assert (aet.home_goals, aet.away_goals, aet.extratime_home, aet.extratime_away) == (3, 2, 1, 0)


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


def _main_pairs(f) -> dict:
    """Pares goals/halftime/fulltime (los que comparan los casos 201-208)."""
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
    assert _main_pairs(edge[205]) == {"goals": (None, None), "halftime": (None, None), "fulltime": (None, None)}


def test_incomplete_fulltime_not_replaced_by_goals(edge):
    # score.fulltime {2, null} es un dato inválido, no ausente: no se inventa con goals
    assert _main_pairs(edge[206]) == {"goals": (2, 1), "halftime": (1, 0), "fulltime": (None, None)}


def test_negative_scores_discarded(edge):
    assert _main_pairs(edge[207]) == {"goals": (1, 0), "halftime": (None, None), "fulltime": (None, None)}


def test_malformed_score_discarded(edge):
    assert _main_pairs(edge[208]) == {"goals": (1, 0), "halftime": (None, None), "fulltime": (1, 0)}


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
NEON_TARGET = "ep-calm-sun-123456.us-east-2.aws.neon.tech:5432/neondb"
OTHER_NEON = "postgresql+psycopg://u:p@ep-other-999999-pooler.us-east-2.aws.neon.tech/neondb"
OTHER_TARGET = "ep-other-999999.us-east-2.aws.neon.tech:5432/neondb"


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
    # La comparación con desarrollo sigue juntando alias y puertos, aunque la autorización sea exacta
    test_url = "postgresql+psycopg://u:p@127.0.0.1:6432/prediktia"
    url, reason = _resolve(test_url, "postgresql+psycopg://u:p@localhost:5432/prediktia", "127.0.0.1:6432/prediktia")
    assert url is None and "misma BD" in reason


@pytest.mark.parametrize(
    "allow", [None, "", "1", "true", "neondb", NEON_TARGET, "ep-other-999999.us-east-2.aws.neon.tech/neondb"]
)
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


# --- Guarda: destino ambiguo o redirigido --------------------------------------------------------

LOCAL_TEST = "postgresql+psycopg://usuario:password@localhost:55432/prediktia_tests"
LOCAL_TARGET = "localhost:55432/prediktia_tests"


@pytest.mark.parametrize(
    ("test_url", "allow"),
    [
        (LOCAL_TEST, LOCAL_TARGET),
        ("postgresql://usuario:password@127.0.0.1:55432/prediktia_tests", "127.0.0.1:55432/prediktia_tests"),
        (LOCAL_TEST + "?sslmode=disable&connect_timeout=5&application_name=pytest", LOCAL_TARGET),
    ],
    ids=["psycopg", "sin-driver-127", "params-seguros"],
)
def test_guard_allows_plain_local_url(test_url, allow):
    assert _resolve(test_url, NEON_DIRECT, allow) == (test_url, "")


def test_guard_allows_neon_ssl_params():
    url = OTHER_NEON + "?sslmode=require&channel_binding=require"
    assert _resolve(url, NEON_DIRECT, OTHER_TARGET) == (url, "")


@pytest.mark.parametrize(
    "query",
    [
        "dbname=neondb",
        "database=neondb",
        "host=ep-calm-sun-123456.us-east-2.aws.neon.tech",
        "hostaddr=10.0.0.1",
        "port=5432",
        "service=prod",
        "servicefile=C:/pg_service.conf",
        "options=endpoint%3Dep-calm-sun-123456",
        "options=-csearch_path%3Dpublic",
        "target_session_attrs=read-write",
        "passfile=C:/pgpass",
        "sslmode=disable&dbname=neondb",
        "DBNAME=neondb",
    ],
)
def test_guard_blocks_destination_overrides_in_query(query):
    # La autorización coincide con el destino visible: aun así se rechaza
    url, reason = _resolve(f"{LOCAL_TEST}?{query}", NEON_DIRECT, LOCAL_TARGET)
    assert url is None and "destino" in reason


@pytest.mark.parametrize(
    "test_url",
    [
        "postgresql+psycopg://u:p@localhost,otro:55432/prediktia_tests",
        "postgresql+psycopg://u:p@%2Ftmp%2Fsocket/prediktia_tests",
        "postgresql+psycopg://u:p@localhost:55432/",
        "postgresql+psycopg://u:p@:55432/prediktia_tests",
    ],
    ids=["multi-host", "socket", "sin-bd", "sin-host"],
)
def test_guard_blocks_ambiguous_host(test_url):
    url, reason = _resolve(test_url, NEON_DIRECT, LOCAL_TARGET)
    assert url is None and "no identifica un destino seguro" in reason


def test_guard_blocks_dev_url_with_destination_overrides():
    # Si la BD de desarrollo no se puede identificar, no se puede descartar que sea la misma
    url, reason = _resolve(LOCAL_TEST, NEON_DIRECT + "&dbname=prediktia_tests", LOCAL_TARGET)
    assert url is None and "interpretar" in reason


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPORT"])
def test_guard_blocks_libpq_redirect_env(variable):
    env = {"TEST_DATABASE_URL": LOCAL_TEST, ALLOW_DESTRUCTIVE_ENV: LOCAL_TARGET, variable: "x"}
    url, reason = resolve_test_db(env, NEON_DIRECT)
    assert url is None and variable in reason


@pytest.fixture
def alembic_calls(monkeypatch) -> list:
    """Sustituye los comandos de Alembic por un registro: así se ve si se habría llegado a ejecutarlos."""
    from alembic import command as alembic_command

    calls: list = []
    for name in ("downgrade", "upgrade"):
        monkeypatch.setattr(alembic_command, name, lambda *args, _n=name: calls.append(_n))
    return calls


@pytest.mark.parametrize("test_url", [LOCAL_TEST + "?dbname=neondb", LOCAL_TEST + "?host=otro", "esto no es una url"])
def test_ambiguous_url_blocked_before_destructive_operation(monkeypatch, alembic_calls, test_url):
    # Aunque la URL llegara a TEST_DB_URL saltándose resolve_test_db, el downgrade no se ejecuta
    monkeypatch.setattr(conftest, "TEST_DB_URL", test_url)
    with pytest.raises(RuntimeError, match="bloqueada"):
        conftest.alembic_run("downgrade", "base")
    assert alembic_calls == []


def test_libpq_env_blocked_before_destructive_operation(monkeypatch, alembic_calls):
    monkeypatch.setattr(conftest, "TEST_DB_URL", LOCAL_TEST)
    monkeypatch.setenv("PGHOSTADDR", "10.0.0.1")
    with pytest.raises(RuntimeError, match="PGHOSTADDR"):
        conftest.alembic_run("downgrade", "base")
    assert alembic_calls == []


# --- Guarda: la autorización identifica host:puerto/bd exactos ----------------------------------

LOCAL_55432 = "postgresql+psycopg://u:p@127.0.0.1:55432/prediktia_tests"
LOCAL_5432 = "postgresql+psycopg://u:p@127.0.0.1:5432/prediktia_tests"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (LOCAL_55432, "127.0.0.1:55432/prediktia_tests"),
        ("postgresql+psycopg://u:p@LocalHost./prediktia_tests", "localhost:5432/prediktia_tests"),
        ("postgresql+psycopg://u:p@[::1]:55432/prediktia_tests", "[::1]:55432/prediktia_tests"),
        (NEON_POOLER, NEON_TARGET),
        (NEON_DIRECT, NEON_TARGET),
    ],
    ids=["explicito", "sin-puerto-5432", "ipv6", "neon-pooler", "neon-directo"],
)
def test_authorization_target_format(url, expected):
    assert authorization_target(url) == expected


def test_authorization_for_one_port_does_not_authorize_another():
    assert _resolve(LOCAL_55432, NEON_DIRECT, "127.0.0.1:55432/prediktia_tests")[0] == LOCAL_55432
    url, reason = _resolve(LOCAL_5432, NEON_DIRECT, "127.0.0.1:55432/prediktia_tests")
    assert url is None and f"{ALLOW_DESTRUCTIVE_ENV}=127.0.0.1:5432/prediktia_tests" in reason


def test_url_without_port_blocked_with_pgport():
    no_port = "postgresql+psycopg://u:p@127.0.0.1/prediktia_tests"
    allow = "127.0.0.1:5432/prediktia_tests"
    assert _resolve(no_port, NEON_DIRECT, allow)[0] == no_port  # sin PGPORT: 5432
    env = {"TEST_DATABASE_URL": no_port, ALLOW_DESTRUCTIVE_ENV: allow, "PGPORT": "55432"}
    url, reason = resolve_test_db(env, NEON_DIRECT)
    assert url is None and "PGPORT" in reason


def test_localhost_and_loopback_need_distinct_authorizations():
    by_localhost = "postgresql+psycopg://u:p@localhost:55432/prediktia_tests"
    by_ip = "postgresql+psycopg://u:p@127.0.0.1:55432/prediktia_tests"
    assert _resolve(by_localhost, NEON_DIRECT, "localhost:55432/prediktia_tests")[0] == by_localhost
    assert _resolve(by_ip, NEON_DIRECT, "localhost:55432/prediktia_tests")[0] is None
    assert _resolve(by_ip, NEON_DIRECT, "127.0.0.1:55432/prediktia_tests")[0] == by_ip
    assert _resolve(by_localhost, NEON_DIRECT, "127.0.0.1:55432/prediktia_tests")[0] is None


def test_engine_on_other_port_blocked_before_destructive_operation(monkeypatch, alembic_calls):
    # Mismo host y misma BD que el engine pero otro puerto: otro cluster, no se autoriza
    from app.db.database import engine

    other_port = engine.url.set(port=(engine.url.port or 5432) + 1).render_as_string(hide_password=False)
    monkeypatch.setattr(conftest, "TEST_DB_URL", other_port)
    with pytest.raises(RuntimeError, match="no apunta a la BD de pruebas autorizada"):
        conftest.alembic_run("downgrade", "base")
    assert alembic_calls == []
