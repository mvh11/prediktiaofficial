"""Tests pequeños del laboratorio. No abren conexiones ni generan datasets grandes."""

import os
import subprocess
import sys
from datetime import timedelta

import pytest

from tools.perf_lab.__main__ import parse_args, write_report
from tools.perf_lab.benchmark import distribution, query_parameters
from tools.perf_lab.dataset import AS_OF, DatasetConfig, fixture_at, schedule
from tools.perf_lab.profiler import GCMeter, segments, statement_family
from tools.perf_lab.safety import authorize


def lab_env(url="postgresql+psycopg://lab@127.0.0.1:55439/prediktia_lab_test"):
    return {"PERF_LAB_DATABASE_URL": url, "PERF_LAB_ALLOW_DESTRUCTIVE": "127.0.0.1:55439/prediktia_lab_test"}


def test_explicit_local_authorization():
    target = authorize(lab_env())
    assert target.authorization == "127.0.0.1:55439/prediktia_lab_test"


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://lab@ep-example.neon.tech:55439/prediktia_lab_test",
    "postgresql+psycopg://lab@localhost:55439/prediktia_lab_test",
    "postgresql+psycopg://lab@127.0.0.1/prediktia_lab_test",
    "postgresql+psycopg://lab@127.0.0.1:5432/prediktia_lab_test",
    "postgresql+psycopg://lab@127.0.0.1:55439/production",
    "postgresql+psycopg://lab@127.0.0.1:55439/prediktia_lab_test?host=remote",
    "postgresql+psycopg://lab@127.0.0.1:55439/prediktia_lab_test?hostaddr=1.2.3.4",
    "postgresql+psycopg://lab@127.0.0.1:55439/prediktia_lab_test?service=remote",
    "postgresql+psycopg://lab@127.0.0.1:55439/prediktia_lab_test?options=x",
    "postgresql+psycopg://lab@127.0.0.1,remote:55439/prediktia_lab_test",
    "postgresql+psycopg://lab@/prediktia_lab_test",
    "sqlite:///prediktia_lab_test",
])
def test_unsafe_targets_rejected_before_connection(url):
    with pytest.raises(ValueError):
        authorize(lab_env(url))


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPORT", "PGHOST"])
def test_libpq_environment_rejected(variable):
    with pytest.raises(ValueError, match="PG"):
        authorize({**lab_env(), variable: "unsafe"})


def test_no_database_url_fallback_or_inexact_authorization():
    with pytest.raises(ValueError, match="Falta PERF_LAB"):
        authorize({"DATABASE_URL": lab_env()["PERF_LAB_DATABASE_URL"]})
    with pytest.raises(ValueError, match="autoriz|ALLOW_DESTRUCTIVE"):
        authorize({**lab_env(), "PERF_LAB_ALLOW_DESTRUCTIVE": "127.0.0.1:55440/prediktia_lab_test"})


def test_ipv6_authorization_is_literal():
    env = lab_env("postgresql+psycopg://lab@[::1]:55439/prediktia_lab_test")
    env["PERF_LAB_ALLOW_DESTRUCTIVE"] = "[::1]:55439/prediktia_lab_test"
    assert authorize(env).url.host == "::1"


def test_round_robin_has_no_duplicate_pairs_and_no_team_plays_itself():
    matches = schedule(42, 1)
    assert len(matches) == len(set(matches)) == 380
    assert all(home != away and (away, home) in matches for home, away in matches)
    for start in range(0, 380, 10):
        assert len({t for pair in matches[start:start + 10] for t in pair}) == 20


def test_generator_is_reproducible_and_varied():
    config = DatasetConfig(1000, 42)
    rows = [fixture_at(i, config) for i in (0, 379, 380, 759, 760, 999)]
    assert rows == [fixture_at(i, config) for i in (0, 379, 380, 759, 760, 999)]
    assert len({r["season_id"] for r in rows}) == 3
    assert min(r["kickoff_at"] for r in rows) < AS_OF < max(r["kickoff_at"] for r in rows)
    assert schedule(42, 1) != schedule(43, 1)
    assert (config.competitions, config.seasons, config.teams) == (1, 3, 20)
    assert DatasetConfig(1_000_000).seasons == 2632  # no materializa un millón de filas


def test_candidate_window_and_second_provider_coverage():
    config = DatasetConfig(1000)
    row = fixture_at(4, config)  # id=5 no tiene mapping secundario
    params = query_parameters("mapping_5dollar", True, 4, config, 360)
    assert params["external_id"] == "lab-fixture_id-4"
    hit = query_parameters("candidate", True, 4, config, 360)
    miss = query_parameters("candidate", False, 4, config, 360)
    assert hit["kickoff_from"] <= row["kickoff_at"] <= hit["kickoff_to"]
    assert miss["kickoff_from"] - hit["kickoff_from"] == timedelta(days=30)
    assert hit["home_team_id"] != hit["away_team_id"]


def test_percentiles_preserve_tail_and_require_repetition():
    stats = distribution(list(range(101)))
    assert stats == {"samples": 101, "min": 0, "p50": 50, "p95": 95, "p99": 99, "max": 100}
    with pytest.raises(ValueError):
        distribution([1])


@pytest.mark.parametrize("args", [
    ["bench", "--output", "unused.json", "--samples", "1"],
    ["bench", "--output", "unused.json", "--warmup", "0"],
    ["bench", "--output", "unused.json", "--window-minutes", "1440"],
    ["plans", "--output", "unused.json", "--only", "upsert"],
    ["seed", "--output", "unused.json", "--fixtures", "1000001"],
])
def test_cli_rejects_invalid_measurement_configuration(args):
    with pytest.raises(SystemExit) as exc:
        parse_args(args)
    assert exc.value.code == 2


def test_report_keeps_previous_evidence(tmp_path):
    path = tmp_path / "result.json"
    write_report(path, {"status": "MEDIDO"})
    with pytest.raises(FileExistsError):
        write_report(path, {"status": "overwritten"})
    assert "MEDIDO" in path.read_text(encoding="utf-8")


def test_imports_and_help_do_not_load_app_or_connect():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import tools.perf_lab.__main__; assert 'app.db.database' not in sys.modules"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, "-m", "tools.perf_lab", "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "DI-A3B" in result.stdout


def test_cli_refuses_remote_target_without_network():
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(lab_env("postgresql+psycopg://lab@ep-example.neon.tech:55439/prediktia_lab_test"))
    result = subprocess.run([sys.executable, "-m", "tools.perf_lab", "init"], env=env, capture_output=True, text=True)
    assert result.returncode == 2 and "loopback" in result.stderr


# --- DI-A3C: perfilado del upsert ---------------------------------------------------------------


@pytest.mark.parametrize(("sql", "family"), [
    ("SET LOCAL statement_timeout = 60000", "SET LOCAL statement_timeout"),
    ("INSERT INTO teams (external_id, name) VALUES (%(a)s, %(b)s) ON CONFLICT (external_id) DO NOTHING",
     "INSERT teams ON CONFLICT DO NOTHING"),
    ("INSERT INTO fixtures (external_id) VALUES (%(x)s) ON CONFLICT (external_id) DO UPDATE SET round = excluded.round",
     "INSERT fixtures ON CONFLICT DO UPDATE"),
    ("SELECT fixtures.external_id, fixtures.id \nFROM fixtures \nWHERE fixtures.external_id IN (%(p)s)",
     "SELECT fixtures"),
    ("INSERT INTO fixture_provider_mappings (fixture_id) VALUES ($1) ON CONFLICT ON CONSTRAINT uq DO UPDATE SET x = 1",
     "INSERT fixture_provider_mappings ON CONFLICT DO UPDATE"),
    ("VACUUM", "OTHER"),
])
def test_statement_family(sql, family):
    assert statement_family(sql) == family


def test_segments_tile_the_measured_window():
    insert = {"family": "INSERT fixtures ON CONFLICT DO UPDATE"}
    mapping = {"family": "INSERT fixture_provider_mappings ON CONFLICT DO UPDATE"}
    marks = [
        (0, "start", None), (5, "enter", "upsert_fixtures"),
        (20, "execute", insert), (50, "cursor", insert), (80, "cursor_done", insert), (81, "executed", insert),
        (90, "enter", "upsert_origin_mappings"), (95, "execute", mapping), (99, "cursor", mapping),
        (110, "cursor_done", mapping), (111, "executed", mapping), (112, "exit", "upsert_origin_mappings"),
        (115, "exit", "upsert_fixtures"), (120, "end", None),
    ]
    parts = segments(marks)
    assert sum(p[3] for p in parts) == 120  # nada queda sin etiqueta
    by = {(c, cat, d): ns for c, cat, d, ns in parts}
    assert by[("upsert_fixtures", "python", "enter:upsert_fixtures -> execute:INSERT fixtures ON CONFLICT DO UPDATE")] == 15
    assert by[("upsert_fixtures", "sqlalchemy_pre_cursor", insert["family"])] == 30
    assert by[("upsert_fixtures", "cursor_execute", insert["family"])] == 30
    assert by[("upsert_fixtures>upsert_origin_mappings", "cursor_execute", mapping["family"])] == 11
    assert by[("repository_write", "python", "exit:upsert_fixtures -> end")] == 5


def test_gc_meter_counts_only_enabled_pauses():
    meter = GCMeter()
    meter.callback("start", {"generation": 2})
    meter.callback("stop", {"generation": 2})
    meter.enabled = True
    meter.callback("start", {"generation": 0})
    meter.callback("stop", {"generation": 0})
    assert dict(meter.collections) == {0: 1} and meter.nanoseconds >= 0


def test_profile_cli_defaults_and_rejections():
    args = parse_args(["profile", "--output", "unused.json"])
    assert (args.batches, args.modes, args.samples, args.prepare_threshold) == ([100, 380, 1000], ["update"], 30, "driver")
    for bad in (["--batches", "500"], ["--samples", "5"], ["--cprofile-samples", "21"],
                ["--prepare-threshold", "-1"], ["--modes", "delete"]):
        with pytest.raises(SystemExit) as exc:
            parse_args(["profile", "--output", "unused.json", *bad])
        assert exc.value.code == 2


def test_profiler_import_does_not_load_app():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import tools.perf_lab.profiler; assert 'app.db.database' not in sys.modules"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


# --- DI-A6 Checkpoint C: escritor con evidencia y lectura temporal ------------------------------


@pytest.mark.parametrize(("sql", "family"), [
    ("SELECT %(param_1)s AS i, fixture_state_hash_v1(CAST(%(param_2)s AS TIMESTAMP WITH TIME ZONE), ...) AS h "
     "UNION ALL SELECT ...", "hash_select"),
    ("INSERT INTO fixtures (external_id, round) VALUES (%(external_id_m0)s, %(round_m0)s) ON CONFLICT (external_id) "
     "DO UPDATE SET round = excluded.round WHERE (fixtures.last_observed_at, fixtures.last_state_hash) < (...)",
     "fixtures_upsert"),
    ("INSERT INTO fixture_observations (evidence_id, fixture_id) VALUES (%(e)s, %(f)s) ON CONFLICT DO NOTHING "
     "RETURNING fixture_observations.fixture_id", "observations_insert"),
    ("INSERT INTO fixture_provider_mappings (fixture_id) VALUES (%(x)s) ON CONFLICT ON CONSTRAINT uq DO UPDATE SET x = 1",
     "fixture_mappings_upsert"),
    ("INSERT INTO teams (external_id) VALUES (%(x)s) ON CONFLICT (external_id) DO NOTHING", "teams_insert"),
    ("SELECT fixture_observations.fixture_id \nFROM fixture_observations JOIN (SELECT ...) AS anon_1 ON ...",
     "select_fixture_observations"),
    ("SELECT fixtures.external_id, fixtures.id \nFROM fixtures \nWHERE fixtures.external_id IN (%(p)s)", "select_fixtures"),
    ("SET LOCAL statement_timeout = 60000", "other"),
])
def test_a6_statement_family(sql, family):
    from tools.perf_lab.a6 import statement_family as a6_family

    assert a6_family(sql) == family


def test_a6_payloads_change_only_what_each_scenario_needs():
    from tools.perf_lab.a6 import INSERT_OFFSET, MIXED_INSERT_OFFSET, groups_for

    config = DatasetConfig(1000)
    flat = lambda groups: [f for fs in groups.values() for f in fs]  # noqa: E731
    base = flat(groups_for(config, 400, "unchanged", 0))
    assert len(base) == 400 and len(groups_for(config, 400, "unchanged", 0)) == 2  # dos respuestas (temporadas)
    assert all(f.external_id >= INSERT_OFFSET for f in flat(groups_for(config, 10, "insert", 0)))
    update = [flat(groups_for(config, 10, "update", v)) for v in (0, 1)]
    assert all(a.venue_name != b.venue_name and a.kickoff_at == b.kickoff_at for a, b in zip(*update))
    tie = [flat(groups_for(config, 10, "tie", v)) for v in (0, 1)]
    assert all(a.kickoff_at != b.kickoff_at for a, b in zip(*tie))  # estados distintos (hash distinto)
    mixed = flat(groups_for(config, 9, "mixed", 0))
    assert sum(f.external_id >= MIXED_INSERT_OFFSET for f in mixed) == 3


def test_a6_ceiling_payload_is_one_response_with_valid_matches():
    from tools.perf_lab.a6 import ceiling_payload

    season_id, fixtures = ceiling_payload(DatasetConfig(1000), 3000)
    assert season_id == 1 and len({f.external_id for f in fixtures}) == 3000
    assert all(f.home_team.external_id != f.away_team.external_id for f in fixtures)


def test_a6_evidence_clock_is_utc_and_older_is_strictly_older():
    from datetime import datetime, timezone

    from tools.perf_lab.a6 import EvidenceClock

    current = datetime(2026, 1, 7, 9, tzinfo=timezone(timedelta(hours=-3)))  # como lo devuelve la sesión
    clock = EvidenceClock(current)
    first, second = clock.older(), clock.older()
    assert first.observed_at.utcoffset() == timedelta(0) and second.observed_at < first.observed_at < current
    assert first.evidence_id != second.evidence_id and clock.newer().observed_at > current


def test_a6_stats_delta_isolates_the_measured_transaction():
    from tools.perf_lab.a6 import stats_delta

    before = {"fixtures": {"n_tup_ins": 5, "n_tup_upd": 3, "n_tup_hot_upd": 2, "n_tup_del": 0}}
    after = {"fixtures": {"n_tup_ins": 5, "n_tup_upd": 13, "n_tup_hot_upd": 6, "n_tup_del": 0},
             "fixture_observations": {"n_tup_ins": 10, "n_tup_upd": 0, "n_tup_hot_upd": 0, "n_tup_del": 0}}
    delta = stats_delta(before, after)
    assert delta["fixtures"] == {"n_tup_ins": 0, "n_tup_upd": 10, "n_tup_hot_upd": 4, "n_tup_del": 0}
    assert delta["fixture_observations"]["n_tup_ins"] == 10


def test_a6_cli_commands():
    args = parse_args(["a6", "writes", "--output", "unused.json"])
    assert (args.batches, args.samples, args.warmup) == ([10, 100, 380, 1000, 2000], 30, 5)
    assert parse_args(["init", "--revision", "0007"]).revision == "0007"
    assert parse_args(["upgrade", "--output", "unused.json"]).command == "upgrade"
    for bad in (["a6", "writes", "--output", "u.json", "--samples", "5"],
                ["a6", "writes", "--output", "u.json", "--modes", "delete"],
                ["a6", "history", "--output", "u.json", "--per-fixture", "1"],
                ["a6", "unknown", "--output", "u.json"]):
        with pytest.raises(SystemExit) as exc:
            parse_args(bad)
        assert exc.value.code == 2


def test_a6_module_import_does_not_load_app():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import tools.perf_lab.a6; assert 'app.db.database' not in sys.modules"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
