"""Adapter de estadísticas de API-Football y contrato interno (M5.1). Sin red ni BD.

Datos: tests/data/api_football/statistics/
- fixtures_ids_recorded.json: respuesta REAL de /fixtures?ids (M5.0) recortada a lo necesario
  (8 partidos; events/lineups/players vacíos salvo un resto mínimo en 1557402).
- fixtures_ids_synthetic.json: casos límite SINTÉTICOS que la muestra real no contiene.
"""

import asyncio
from decimal import Decimal

import pytest

from app.integrations.exceptions import ProviderAuthError, ProviderResponseError
from app.integrations.football import api_football
from app.integrations.football.api_football_statistics import (
    MAX_IDS_PER_REQUEST,
    STAT_TYPE_TO_FIELD,
    ApiFootballStatisticsProvider,
    Unparseable,
    parse_decimal,
    parse_fixture_statistics,
    parse_integer,
    parse_percentage,
)
from app.schemas.statistics import CORE_FIELDS, OPTIONAL_FIELDS, STATISTIC_FIELDS, TeamStatisticValues
from tests.conftest import load_json

RECORDED = "api_football/statistics/fixtures_ids_recorded.json"
SYNTHETIC = "api_football/statistics/fixtures_ids_synthetic.json"


@pytest.fixture
def provider() -> ApiFootballStatisticsProvider:
    return ApiFootballStatisticsProvider(api_key="test-key", base_url="https://example.invalid", timeout=1)


@pytest.fixture
def http(monkeypatch):
    """Sustituye la capa HTTP: registra cada petición y devuelve `payload`."""
    state = {"calls": [], "payload": load_json(RECORDED)}

    async def fake_get_json(**kwargs):
        state["calls"].append(kwargs)
        return state["payload"]

    monkeypatch.setattr(api_football, "get_json", fake_get_json)
    return state


def _by_id(relative: str) -> dict:
    return {f.provider_fixture_id: f for f in (parse_fixture_statistics(i) for i in load_json(relative)["response"])}


@pytest.fixture(scope="module")
def recorded() -> dict:
    return _by_id(RECORDED)


@pytest.fixture(scope="module")
def synthetic() -> dict:
    return _by_id(SYNTHETIC)


def _team(fixture, team_id):
    return next(t for t in fixture.teams if t.provider_team_id == team_id)


# --- Contrato -----------------------------------------------------------------------------


def test_contract_fields_and_mapping_cover_v1_exactly():
    assert set(STAT_TYPE_TO_FIELD.values()) == set(STATISTIC_FIELDS) == set(TeamStatisticValues.model_fields)
    assert CORE_FIELDS | OPTIONAL_FIELDS == set(STATISTIC_FIELDS) and not CORE_FIELDS & OPTIONAL_FIELDS
    assert CORE_FIELDS == {"shots_on_goal", "shots_off_goal", "shots_total", "shots_blocked", "corners", "fouls", "yellow_cards", "possession_pct"}


# --- Muestra real -------------------------------------------------------------------------


def test_premier_league_full_statistics_with_xg(recorded):
    f = recorded[1557402]
    assert (f.provider, f.provider_status, f.teams_with_statistics) == ("api-football", "FT", 2)
    home = _team(f, 63)
    assert home.values == TeamStatisticValues(
        shots_on_goal=6, shots_off_goal=4, shots_total=15, shots_blocked=5, shots_inside_box=10, shots_outside_box=5,
        fouls=8, corners=5, offsides=2, yellow_cards=1, red_cards=None, goalkeeper_saves=4, passes_total=440,
        passes_accurate=368, possession_pct=Decimal("50"), passes_pct=Decimal("84"), expected_goals=Decimal("2.08"),
    )
    assert home.present == frozenset(STATISTIC_FIELDS) and home.missing == frozenset()
    assert home.raw_only == {"goals_prevented": "-1.13"} and home.unknown == {}
    assert home.unparseable == home.out_of_range == home.duplicated == {}
    assert isinstance(home.values.expected_goals, Decimal) and isinstance(home.values.possession_pct, Decimal)


def test_red_cards_null_stays_none_and_zero_stays_zero(recorded):
    # PL 2026: "Red Cards" null (no hubo rojas) → None, nunca 0; PL 2024: 0 y 1 reales
    assert [t.values.red_cards for t in recorded[1557402].teams] == [None, None]
    assert all("red_cards" in t.present for t in recorded[1557402].teams)  # vino, pero null
    assert [t.values.red_cards for t in recorded[1208397].teams] == [0, 1]
    assert _team(recorded[1159024], 1065).values.shots_on_goal == 0  # 0 del proveedor es 0


def test_argentina_possession_percentage_and_xg_string(recorded):
    home = _team(recorded[1159024], 455)
    assert home.values.possession_pct == Decimal("47")  # "47%"
    assert home.values.passes_pct == Decimal("71")  # "71%"
    assert home.values.expected_goals == Decimal("0.91")  # "0.91"


@pytest.mark.parametrize("fixture_id", [1494836, 1557417])  # Bolivia FT sin stats / PL NS
def test_empty_statistics_are_not_zeros(recorded, fixture_id):
    f = recorded[fixture_id]
    assert len(f.teams) == 2 and f.teams_with_statistics == 0
    for team in f.teams:
        assert team.provider_team_id is not None and not team.has_statistics
        assert team.values == TeamStatisticValues()  # todo None
        assert team.missing == frozenset(STATISTIC_FIELDS)
    assert f.raw_statistics == [{"team": t["team"], "statistics": []} for t in f.raw_statistics]


def test_ucl_without_xg_offsides_or_passes_pct(recorded):
    f = recorded[1635697]
    for team in f.teams:
        assert team.missing == {"expected_goals", "offsides", "passes_pct"}  # ausentes, no null
        assert team.values.expected_goals is None and team.values.offsides is None
        assert "Free Kicks" in team.raw_only and team.unknown == {}
    assert _team(f, 33).values.shots_total == 21


def test_aet_fixture_with_mostly_null_values(recorded):
    f = recorded[1593527]
    assert f.provider_status == "AET" and f.teams_with_statistics == 2
    home = _team(f, 3403)
    assert home.missing == frozenset()  # todos los tipos vinieron...
    non_null = {k: v for k, v in home.values.model_dump().items() if v is not None}
    assert non_null == {"yellow_cards": 3, "red_cards": 0}  # ...pero casi todos a null


def test_pen_fixture_without_xg_keeps_free_kicks_raw(recorded):
    team = _team(recorded[1631509], 121)
    assert team.values.offsides == 1 and team.values.passes_pct is None
    assert team.raw_only == {"Free Kicks": 12}


def test_raw_statistics_is_the_provider_array_verbatim(recorded):
    raw = load_json(RECORDED)["response"]
    for item in raw:
        assert recorded[item["fixture"]["id"]].raw_statistics == item["statistics"]


# --- Casos sintéticos -----------------------------------------------------------------------


def test_unparseable_values_do_not_break_the_fixture(synthetic):
    home = _team(synthetic[900001], 1)
    assert home.unparseable == {"fouls": "-", "corners": True, "offsides": 1.5, "passes_pct": "n/a"}
    for field in home.unparseable:
        assert getattr(home.values, field) is None and field in home.present
    # El resto del equipo sí se normaliza
    assert (home.values.shots_off_goal, home.values.yellow_cards, home.values.red_cards) == (4, 2, 0)


def test_negative_and_out_of_range_values_keep_evidence(synthetic):
    home, away = _team(synthetic[900001], 1), _team(synthetic[900001], 2)
    assert home.out_of_range == {"shots_on_goal": -1, "possession_pct": Decimal("105")}
    assert away.out_of_range == {"expected_goals": Decimal("-0.10")}
    assert home.values.shots_on_goal is None and home.values.possession_pct is None
    assert away.values.expected_goals is None  # nunca se convierte en 0


def test_zero_percentage_and_decimal_percentage(synthetic):
    away = _team(synthetic[900001], 2)
    assert away.values.possession_pct == Decimal("0")  # "0%"
    assert away.values.passes_pct == Decimal("47.5")  # "47.5" sin %
    assert away.values.shots_on_goal == 0
    assert away.values.red_cards is None and "red_cards" in away.present


def test_unknown_and_raw_only_types_are_kept(synthetic):
    home = _team(synthetic[900001], 1)
    assert home.unknown == {"Expected Assists": "1.20"}
    assert home.raw_only == {"goals_prevented": "-0.40"}
    assert home.malformed_entries == 1  # entrada sin "type": queda solo en el raw


def test_duplicated_types(synthetic):
    home, away = _team(synthetic[900001], 1), _team(synthetic[900001], 2)
    assert home.duplicated == {"shots_total": [5, 6]} and home.values.shots_total is None
    assert away.duplicated == {} and away.values.yellow_cards == 1  # repetido con el mismo valor


def test_integer_and_decimal_inputs(synthetic):
    home = _team(synthetic[900001], 1)
    assert home.values.yellow_cards == 2  # "2"
    assert home.values.expected_goals == Decimal("2")  # 2


def test_status_is_canonicalized(synthetic):
    assert synthetic[900001].provider_status == "FT"  # "ft"


def test_partial_response_with_one_team(synthetic):
    f = synthetic[900002]
    assert len(f.teams) == 1 and f.teams_with_statistics == 1
    assert f.teams[0].values.corners == 3 and f.teams[0].values.fouls is None


def test_response_without_teams_and_without_statistics_key(synthetic):
    assert synthetic[900003].teams == [] and synthetic[900003].raw_statistics == []
    assert synthetic[900004].teams == [] and synthetic[900004].raw_statistics is None


def test_team_without_valid_id_is_kept_with_none(synthetic):
    f = synthetic[900005]
    assert [t.provider_team_id for t in f.teams] == [None, None]  # sin id / id en texto
    assert f.teams[0].values.fouls == 9


def test_fixture_without_id_is_a_provider_error():
    with pytest.raises(ProviderResponseError):
        parse_fixture_statistics({"fixture": {"status": {"short": "FT"}}, "statistics": []})


# --- Parsers --------------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (7, 7), ("12", 12), (" 3 ", 3), ("-2", -2), (None, None)])
def test_parse_integer(value, expected):
    assert parse_integer(value) == expected


@pytest.mark.parametrize("value", [True, False, 1.5, "1.0", "-", "", "abc", [], {}])
def test_parse_integer_rejects(value):
    with pytest.raises(Unparseable):
        parse_integer(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("47%", Decimal("47")), ("0%", Decimal("0")), ("100%", Decimal("100")), ("47.5%", Decimal("47.5")), ("84", Decimal("84")), (55, Decimal("55")), (None, None)],
)
def test_parse_percentage(value, expected):
    assert parse_percentage(value) == expected


@pytest.mark.parametrize("value", ["%", "47 %", "n/a", "-", True, "4%7"])
def test_parse_percentage_rejects(value):
    with pytest.raises(Unparseable):
        parse_percentage(value)


@pytest.mark.parametrize(
    ("value", "expected"), [("2.08", Decimal("2.08")), ("0", Decimal("0")), (2, Decimal("2")), (0.5, Decimal("0.5")), ("-0.1", Decimal("-0.1")), (None, None)]
)
def test_parse_decimal(value, expected):
    assert parse_decimal(value) == expected


@pytest.mark.parametrize("value", ["2,08", "abc", "", "-", True, float("nan"), "1e3"])
def test_parse_decimal_rejects(value):
    with pytest.raises(Unparseable):
        parse_decimal(value)


# --- Petición por lotes -------------------------------------------------------------------------


def test_batch_uses_a_single_ids_request_and_ignores_events_lineups_players(provider, http):
    result = asyncio.run(provider.get_fixture_statistics([1557402, 1208397, 1159024]))
    assert len(http["calls"]) == 1
    call = http["calls"][0]
    assert call["path"] == "/fixtures" and call["params"] == {"ids": "1159024-1208397-1557402"}
    # La respuesta grabada trae 8 partidos (incluidos events/lineups/players en 1557402)
    assert [f.provider_fixture_id for f in result] == [i["fixture"]["id"] for i in http["payload"]["response"]]
    assert "events" not in result[0].model_dump() and "players" not in result[0].model_dump()


def test_batch_accepts_twenty_ids(provider, http):
    asyncio.run(provider.get_fixture_statistics(list(range(1, MAX_IDS_PER_REQUEST + 1))))
    assert http["calls"][0]["params"]["ids"] == "-".join(str(i) for i in range(1, 21))


def test_batch_rejects_twenty_one_ids_before_any_request(provider, http):
    with pytest.raises(ValueError, match="20"):
        asyncio.run(provider.get_fixture_statistics(list(range(1, 22))))
    assert http["calls"] == []


def test_batch_deduplicates_and_sorts_ids(provider, http):
    ids = list(range(20, 0, -1)) + [5, 7, 20]  # 23 entradas, 20 distintas
    asyncio.run(provider.get_fixture_statistics(ids))
    assert http["calls"][0]["params"]["ids"] == "-".join(str(i) for i in range(1, 21))


@pytest.mark.parametrize("ids", [[], [0], [-3], [True], ["123"], [1.0]])
def test_batch_rejects_invalid_ids_before_any_request(provider, http, ids):
    with pytest.raises(ValueError):
        asyncio.run(provider.get_fixture_statistics(ids))
    assert http["calls"] == []


def test_batch_with_unexpected_response_format(provider, http):
    http["payload"] = {"errors": [], "paging": {"current": 1, "total": 1}, "response": {"not": "a list"}}
    with pytest.raises(ProviderResponseError):
        asyncio.run(provider.get_fixture_statistics([1]))


def test_batch_propagates_provider_errors(provider, http):
    http["payload"] = load_json("api_football/errors_token.json")
    with pytest.raises(ProviderAuthError):
        asyncio.run(provider.get_fixture_statistics([1557402]))
    assert len(http["calls"]) == 1  # credenciales: no se reintenta


def test_batch_empty_response_returns_empty_list(provider, http):
    http["payload"] = {"errors": [], "results": 0, "paging": {"current": 1, "total": 1}, "response": []}
    assert asyncio.run(provider.get_fixture_statistics([1, 2])) == []
