"""Tests unitarios (sin BD ni red) del backfill histórico: checks Q1–Q15, predicción de cambios,
adapter que registra la identidad de la respuesta y formato del CLI."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.integrations.football import api_football
from app.integrations.football.api_football_history import RecordingApiFootballProvider
from app.jobs import history_backfill as cli
from app.schemas.backfill import BackfillResult, CheckResult
from app.services import history_quality_checks as qc
from app.services.history_backfill_service import predict_stored_values
from tests.conftest import load_json, make_fixture_data

NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _ids(check: CheckResult) -> list[int]:
    return check.samples


# --- Severidades -----------------------------------------------------------------------------


def test_all_fifteen_checks_have_explicit_severity():
    assert set(qc.SEVERITY) == {f"Q{i}" for i in range(1, 16)}
    blocking = {k for k, v in qc.SEVERITY.items() if v == "blocking"}
    warning = {k for k, v in qc.SEVERITY.items() if v == "warning"}
    assert {"Q3", "Q4", "Q5", "Q8", "Q12", "Q13", "Q15"} <= blocking
    assert {"Q7", "Q9", "Q10", "Q11", "Q14"} <= warning
    assert qc.SEVERITY["Q1"] == "blocking" and qc.SEVERITY["Q6"] == "warning"


# --- Q1 ---------------------------------------------------------------------------------------


def test_q1_zero_received_blocks():
    assert qc.q1_received(0).is_blocking_failure


def test_q1_without_range_passes_and_says_so():
    check = qc.q1_received(380)
    assert check.passed and "sin rango" in check.detail


@pytest.mark.parametrize(("received", "passed"), [(380, True), (100, False), (500, False)])
def test_q1_with_expected_range(received, passed):
    assert qc.q1_received(received, (370, 390)).passed is passed


# --- Q6–Q11, Q15 sobre partidos -----------------------------------------------------------


def test_q6_finished_without_goals_is_warning():
    check = qc.q6_finished_without_goals([make_fixture_data(1, status="FT"), make_fixture_data(2, status="NS")])
    assert check.is_warning and _ids(check) == [1]


def test_q7_finished_without_fulltime_is_warning():
    check = qc.q7_finished_without_fulltime([make_fixture_data(1, status="AET", home_goals=2, away_goals=1)])
    assert check.is_warning and _ids(check) == [1]


@pytest.mark.parametrize("fulltime", [(1, None), (None, 2), (-1, 0)])
def test_q8_partial_or_negative_fulltime_blocks(fulltime):
    f = make_fixture_data(1, status="FT").model_copy(update={"fulltime_home": fulltime[0], "fulltime_away": fulltime[1]})
    assert qc.q8_fulltime_partial_or_negative([f]).is_blocking_failure


def test_q9_detects_6570_like_anomaly_without_correcting_it():
    f = make_fixture_data(
        1, status="AET", home_goals=2, away_goals=0, extratime_home=0, extratime_away=2, fulltime_home=4, fulltime_away=0
    )
    check = qc.q9_fulltime_above_final([f])
    assert check.is_warning and _ids(check) == [1]
    assert (f.fulltime_home, f.fulltime_away) == (4, 0)  # el dato no se toca


def test_q9_ignores_coherent_aet():
    f = make_fixture_data(1, status="AET", home_goals=3, away_goals=2, fulltime_home=2, fulltime_away=2)
    assert qc.q9_fulltime_above_final([f]).passed


def test_q10_ft_fulltime_differs_from_goals():
    f = make_fixture_data(1, status="FT", home_goals=2, away_goals=1, fulltime_home=1, fulltime_away=1)
    assert qc.q10_ft_fulltime_differs([f]).is_warning


def test_q11_halftime_above_fulltime():
    f = make_fixture_data(1, status="FT", halftime_home=2, halftime_away=0, fulltime_home=1, fulltime_away=0, home_goals=1, away_goals=0)
    assert qc.q11_halftime_above_fulltime([f]).is_warning


def test_q15_same_team_blocks():
    assert qc.q15_same_team([make_fixture_data(1, home=5, away=5)]).is_blocking_failure


# --- Q12 ----------------------------------------------------------------------------------------


def test_q12_identity_ok():
    fixtures = [make_fixture_data(1), make_fixture_data(2)]
    check = qc.q12_identity(fixtures, {1: (39, 2025), 2: (39, 2025)}, 39, 2025, {1: 10}, 10)
    assert check.passed


@pytest.mark.parametrize("identity", [{1: (140, 2025)}, {1: (39, 2024)}, {}, None])
def test_q12_wrong_league_season_or_unverifiable_blocks(identity):
    assert qc.q12_identity([make_fixture_data(1)], identity, 39, 2025, {}, 10).is_blocking_failure


def test_q12_external_id_in_other_season_blocks():
    check = qc.q12_identity([make_fixture_data(1)], {1: (39, 2025)}, 39, 2025, {1: 99}, 10)
    assert check.is_blocking_failure and _ids(check) == [1]


# --- Q14, Q2, Q13 ---------------------------------------------------------------------------


def test_q14_unfinished_past_fixture_in_closed_season():
    fixtures = [make_fixture_data(1, status="NS"), make_fixture_data(2, status="FT"), make_fixture_data(3, status="CANC")]
    check = qc.q14_unfinished_in_closed_season(fixtures, True, NOW)
    assert check.is_warning and _ids(check) == [1]
    assert qc.q14_unfinished_in_closed_season(fixtures, False, NOW).passed


def test_q2_and_q13_semantics():
    assert qc.q2_fixture_count(100, None).passed  # dry-run: sin deletes
    assert qc.q2_fixture_count(100, 99).is_blocking_failure
    assert qc.q13_mappings([], True, would_create=7).passed
    assert qc.q13_mappings([5], False).is_blocking_failure


def test_samples_are_limited():
    fixtures = [make_fixture_data(i, home=1, away=1) for i in range(1, 20)]
    check = qc.q15_same_team(fixtures)
    assert check.count == 19 and len(check.samples) == 5


# --- Predicción de cambios (réplica de la política del upsert) ------------------------------


STORED = dict(
    season_id=1, round="R1", kickoff_at=NOW, status_short="FT", status_long=None, elapsed=90, venue_name=None,
    venue_city=None, referee=None, home_team_id=1, away_team_id=2, home_goals=2, away_goals=1, halftime_home=1,
    halftime_away=0, extratime_home=None, extratime_away=None, penalty_home=None, penalty_away=None,
    fulltime_home=2, fulltime_away=1,
)


def test_predict_identical_row_unchanged():
    assert predict_stored_values(STORED, STORED) == {k: STORED[k] for k in predict_stored_values(STORED, STORED)}


def test_predict_null_scores_in_final_state_kept():
    incoming = {**STORED, "home_goals": None, "away_goals": None, "fulltime_home": None, "fulltime_away": None}
    predicted = predict_stored_values(STORED, incoming)
    assert (predicted["home_goals"], predicted["fulltime_home"]) == (2, 2)


def test_predict_non_final_state_clears_scores():
    incoming = {**STORED, "status_short": "PST", "home_goals": None, "away_goals": None, "fulltime_home": None, "fulltime_away": None, "halftime_home": None, "halftime_away": None}
    predicted = predict_stored_values(STORED, incoming)
    assert predicted["home_goals"] is None and predicted["fulltime_home"] is None and predicted["halftime_home"] is None


def test_predict_complete_pair_replaces():
    predicted = predict_stored_values(STORED, {**STORED, "home_goals": 3, "away_goals": 1})
    assert (predicted["home_goals"], predicted["away_goals"]) == (3, 1)


@pytest.mark.parametrize("status", ["1H", "HT", "2H", "ET"])
def test_predict_live_partial_null_keeps_scores_but_not_fulltime(status):
    incoming = {**STORED, "status_short": status, "home_goals": None, "away_goals": None, "halftime_home": None,
                "halftime_away": None, "fulltime_home": None, "fulltime_away": None}
    predicted = predict_stored_values(STORED, incoming)
    assert (predicted["home_goals"], predicted["away_goals"]) == (2, 1)
    assert (predicted["halftime_home"], predicted["halftime_away"]) == (1, 0)
    assert (predicted["fulltime_home"], predicted["fulltime_away"]) == (None, None)  # solo en FT/AET/PEN


def _all_null(status: str) -> dict:
    nulls = {c: None for c in ("home_goals", "away_goals", "halftime_home", "halftime_away", "extratime_home",
                                "extratime_away", "penalty_home", "penalty_away", "fulltime_home", "fulltime_away")}
    return {**STORED, **nulls, "status_short": status}


def test_predict_ft_to_tbd_clears_scores():
    predicted = predict_stored_values(STORED, _all_null("TBD"))
    assert all(predicted[c] is None for c in ("home_goals", "halftime_home", "fulltime_home"))


@pytest.mark.parametrize("status", ["SUSP", "INT"])
def test_predict_suspended_or_interrupted_keeps_scores(status):
    predicted = predict_stored_values(STORED, _all_null(status))
    assert (predicted["home_goals"], predicted["away_goals"]) == (2, 1)
    assert (predicted["halftime_home"], predicted["halftime_away"]) == (1, 0)


PEN_STORED = {**STORED, "status_short": "PEN", "extratime_home": 0, "extratime_away": 0, "penalty_home": 4, "penalty_away": 3}


@pytest.mark.parametrize("status", ["FT", "AET", "AWD"])
def test_predict_pen_corrected_clears_penalty(status):
    predicted = predict_stored_values(PEN_STORED, _all_null(status))
    assert (predicted["penalty_home"], predicted["penalty_away"]) == (None, None)


@pytest.mark.parametrize(("status", "expected"), [("FT", (None, None)), ("AET", (0, 0)), ("PEN", (0, 0))])
def test_predict_extratime_only_kept_outside_ft(status, expected):
    predicted = predict_stored_values(PEN_STORED, _all_null(status))
    assert (predicted["extratime_home"], predicted["extratime_away"]) == expected


def test_predict_never_mixes_half_pairs():
    incoming = {**STORED, "home_goals": 5, "away_goals": None}
    predicted = predict_stored_values(STORED, incoming)
    assert (predicted["home_goals"], predicted["away_goals"]) == (2, 1)


def test_predict_uses_the_repository_policy():
    # predict_stored_values no tiene una copia propia de la política: delega en el repositorio
    from app.repositories.fixture_repository import predict_score_values

    incoming = {**STORED, "status_short": "PST", "home_goals": None, "away_goals": None}
    predicted = predict_stored_values(STORED, incoming)
    assert {k: predicted[k] for k in predict_score_values(STORED, incoming)} == predict_score_values(STORED, incoming)


# --- Adapter con identidad de la respuesta -----------------------------------------------------


def test_recording_provider_keeps_response_league_and_season(monkeypatch):
    payload = load_json("api_football/fixtures_mixed.json")
    payload["response"][0]["league"] = {"id": 140, "season": 2024}

    async def fake_get_json(**_kwargs):
        return payload

    monkeypatch.setattr(api_football, "get_json", fake_get_json)
    provider = RecordingApiFootballProvider(api_key="k", base_url="https://example.invalid", timeout=1)
    fixtures = asyncio.run(provider.get_fixtures(265, 2026))
    assert provider.last_fixture_identity[101] == (140, 2024)  # lo que dijo la respuesta
    assert provider.last_fixture_identity[102] == (265, 2026)
    assert len(fixtures) == 6  # el parseo es el mismo del adapter base


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_parses_single_pair_flags():
    args = cli.parse_args(["--competition-id", "5", "--season", "2025", "--dry-run", "--refresh"])
    assert (args.competition_id, args.season, args.dry_run, args.refresh) == (5, 2025, True, True)


def test_cli_requires_competition_and_season():
    with pytest.raises(SystemExit):
        cli.parse_args(["--season", "2025"])


def test_cli_output_has_all_fields_and_no_secrets():
    result = BackfillResult(
        run_id=1, competition_id=5, competition_name="Premier League", requested_year=2025, season_id=9,
        provider="api-football", is_dry_run=True, is_refresh=False, status="dry_run_completed",
        received=380, new=380, warnings=1,
        checks=[CheckResult(id="Q9", severity="warning", passed=False, count=1, detail="x", samples=[1593527])],
    )
    text = cli.format_result(result)
    for fragment in ["Premier League", "2025", "api-football", "dry-run", "380", "Se insertarían", "Q9", "DRY_RUN_COMPLETED"]:
        assert fragment in text
    assert "postgresql" not in text and "key" not in text.lower()


# --- Q1 con rango esperado -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("received", "passed"),
    [(370, True), (390, True), (380, True), (369, False), (391, False)],  # exactamente min/max, dentro, debajo, encima
)
def test_q1_range_bounds_are_inclusive(received, passed):
    check = qc.q1_received(received, (370, 390))
    assert check.passed is passed and "370–390" in check.detail


def test_q1_without_range_only_blocks_zero():
    assert qc.q1_received(1).passed and qc.q1_received(10_000).passed
    assert qc.q1_received(0).is_blocking_failure


# --- CLI: rango y recuperación de runs abandonados ----------------------------------------


BASE = ["--competition-id", "5", "--season", "2025"]


def test_cli_without_range():
    assert cli.expected_range(cli.parse_args(BASE)) is None


@pytest.mark.parametrize(("lo", "hi"), [("370", "390"), ("0", "0"), ("380", "380")])
def test_cli_valid_range(lo, hi):
    assert cli.expected_range(cli.parse_args(BASE + ["--expected-min", lo, "--expected-max", hi])) == (int(lo), int(hi))


@pytest.mark.parametrize(
    "extra",
    [
        ["--expected-min", "370"],  # solo min
        ["--expected-max", "390"],  # solo max
        ["--expected-min", "-1", "--expected-max", "5"],  # min negativo
        ["--expected-min", "390", "--expected-max", "370"],  # max < min
    ],
)
def test_cli_invalid_range_rejected(extra):
    with pytest.raises(SystemExit):
        cli.parse_args(BASE + extra)


def test_cli_fail_stale_run_defaults_and_validation():
    args = cli.parse_args(BASE + ["--fail-stale-run"])
    assert args.fail_stale_run and args.stale_after_minutes == cli.DEFAULT_STALE_AFTER_MINUTES == 120
    assert cli.parse_args(BASE + ["--fail-stale-run", "--stale-after-minutes", "30"]).stale_after_minutes == 30


@pytest.mark.parametrize(
    "extra",
    [
        ["--fail-stale-run", "--dry-run"],
        ["--fail-stale-run", "--refresh"],
        ["--fail-stale-run", "--expected-min", "1", "--expected-max", "2"],
        ["--fail-stale-run", "--stale-after-minutes", "0"],
        ["--stale-after-minutes", "30"],  # sin --fail-stale-run
    ],
)
def test_cli_fail_stale_run_is_a_separate_operation(extra):
    with pytest.raises(SystemExit):
        cli.parse_args(BASE + extra)


def test_cli_fail_stale_run_never_starts_a_backfill(monkeypatch):
    from app.schemas.backfill import StaleRunRecovery

    calls = []

    def fake_recover(_args):
        return StaleRunRecovery(outcome="not_found", competition_id=5, requested_year=2025, stale_after_minutes=120, message="nada")

    monkeypatch.setattr(cli, "_recover", fake_recover)
    monkeypatch.setattr(cli, "_run", lambda args: calls.append(args))
    assert cli.main(BASE + ["--fail-stale-run"]) == 1
    assert calls == []


def test_service_and_cli_share_default_threshold():
    from app.services.history_backfill_service import DEFAULT_STALE_AFTER_MINUTES

    assert DEFAULT_STALE_AFTER_MINUTES == cli.DEFAULT_STALE_AFTER_MINUTES
