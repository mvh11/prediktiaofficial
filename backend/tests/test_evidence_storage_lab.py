"""Small no-connection tests. Real concurrency/recovery gates are explicit LAB CLI commands."""

import copy
import uuid
from datetime import datetime

import pytest

from tools.evidence_storage_lab.__main__ import parse_args
from tools.evidence_storage_lab.benchmark import distribution, materialize, plan_summary
from tools.evidence_storage_lab.candidates import CANDIDATES, COLUMNS, UTC, append_batch, classify, identifier, month_at, payload, strict_sql
from tools.evidence_storage_lab.safety import Target, write_report


def test_candidates_have_separate_laboratory_namespaces():
    assert len({c.schema for c in CANDIDATES}) == len(CANDIDATES)
    assert all(c.schema.startswith("lab_") for c in CANDIDATES)
    assert all(c.registry for c in CANDIDATES if c.months or c.lifecycle != "none")
    assert {c.months for c in CANDIDATES} == {0, 1, 3}
    assert {c.lifecycle for c in CANDIDATES} == {"none", "archive", "hotcold"}


@pytest.mark.parametrize("name", ["public", "lab_x;DROP", "lab_foo.bar", "lab_'", ""])
def test_non_lab_identifiers_rejected(name):
    with pytest.raises(ValueError):
        identifier(name)


@pytest.mark.parametrize("url", ["postgresql+psycopg://x@neon.example:55449/prediktia_lab_x",
                                 "postgresql+psycopg://x@127.0.0.1:5432/prediktia_lab_x",
                                 "postgresql+psycopg://x@localhost:55449/prediktia_lab_x",
                                 "postgresql+psycopg://x@127.0.0.1:55449/production",
                                 "postgresql+psycopg://x@127.0.0.1:55449/prediktia_lab_x?host=neon.example"])
def test_unsafe_urls_fail_before_connection(url):
    with pytest.raises(ValueError):
        Target.from_environment({"EVIDENCE_STORAGE_DATABASE_URL": url,
                                 "EVIDENCE_STORAGE_ALLOW_DESTRUCTIVE": "127.0.0.1:55449/prediktia_lab_x"})


def test_no_database_url_fallback():
    with pytest.raises(ValueError, match="Falta"):
        Target.from_environment({"DATABASE_URL": "postgresql+psycopg://x@127.0.0.1:55449/prediktia_lab_x"})


def test_exact_authorization_and_cluster_identity_required():
    env = {"EVIDENCE_STORAGE_DATABASE_URL": "postgresql+psycopg://x@127.0.0.1:55449/prediktia_lab_x",
           "EVIDENCE_STORAGE_ALLOW_DESTRUCTIVE": "127.0.0.1:55449/prediktia_lab_x"}
    with pytest.raises(ValueError, match="cluster"):
        Target.from_environment(env)
    with pytest.raises(ValueError, match="PG"):
        Target.from_environment({**env, "PGSERVICE": "remote"})


@pytest.mark.parametrize("scale", [0, 99999, 1000001, 5000000])
def test_cli_cannot_admit_multi_million(scale, tmp_path):
    with pytest.raises(SystemExit):
        parse_args(["bench", "--observations", str(scale), "--gate-report", "gate.json", "--output", str(tmp_path/"report.json")])


def test_reports_never_overwrite(tmp_path):
    path = tmp_path/"report.json"
    write_report(path, {"original": 1})
    with pytest.raises(FileExistsError):
        write_report(path, {"changed": 1})
    assert '"original"' in path.read_text()


def test_partition_boundaries_are_utc_and_calendar_based():
    assert month_at(0) == datetime(2023, 1, 1, tzinfo=UTC)
    assert month_at(14) == datetime(2024, 3, 1, tzinfo=UTC)
    assert month_at(36) == datetime(2026, 1, 1, tzinfo=UTC)


def test_percentiles_retain_raw_tail():
    assert distribution([1, 2, 3, 4])["p50"] == 2.5
    assert distribution([1, 2, 3, 100])["max"] == 100
    with pytest.raises(ValueError):
        distribution([])


def test_partial_ties_and_unknown_use_production_types():
    cutoff = datetime(2024, 1, 1, tzinfo=UTC)
    first = {**payload(1, cutoff, partial=True), "id": 1, "recorded_at": cutoff, "state_hash": b"a"*32}
    confirmation = {**first, "id": 2, "evidence_id": uuid.uuid4()}
    conflicting = {**first, "fixture_id": 2, "id": 3, "evidence_id": uuid.uuid4(), "state_hash": b"b"*32}
    second = {**conflicting, "id": 4, "evidence_id": uuid.uuid4(), "state_hash": b"c"*32}
    rows = [tuple(r[c] for c in COLUMNS) for r in (first, confirmation, conflicting, second)]
    snapshot = copy.deepcopy(rows)
    result = materialize(rows, [1, 2, 3], cutoff)
    assert result.results[1].status.value == "KNOWN"
    assert len(result.results[1].observations) == 2
    assert result.results[1].state.fulltime_home is None
    assert result.results[2].status.value == "TEMPORAL_AMBIGUITY" and result.results[2].state is None
    assert result.results[3].status.value == "UNKNOWN_AT_T"
    assert rows == snapshot
    assert classify(rows, [1, 2, 3])[2]["state"] is None


def test_plan_summary_separates_planning_pruning_and_execution():
    plan = [{"Planning Time": 2.0, "Execution Time": 3.0, "Plan": {"Node Type": "Append", "Subplans Removed": 2,
             "Plans": [{"Node Type": "Index Scan", "Relation Name": "p_001", "Actual Loops": 1},
                       {"Node Type": "Seq Scan", "Relation Name": "p_002", "Actual Loops": 0}]}}]
    result = plan_summary(plan)
    assert result["planning_ms"] == 2 and result["execution_ms"] == 3
    assert result["executed_partition_leaves"] == ["p_001"]
    assert result["subplans_removed"] == 2


def test_strict_query_does_not_choose_a_hash_winner_or_use_persistence_time():
    query = strict_sql(CANDIDATES[0]).as_string()
    assert "max(observed_at)" in query and "observed_at<=%s" in query
    assert "fixture_id=ANY(%s::integer[])" in query
    assert "observed_at=latest.t_star" in query
    assert "recorded_at" not in query and "state_hash" not in query
    assert "LIMIT" not in query and "DISTINCT ON" not in query


def test_latest_cutoff_excludes_current_state_from_materialization():
    cutoff = datetime(2024, 1, 1, tzinfo=UTC)
    observed = {**payload(1, cutoff, partial=True), "id": 999, "recorded_at": datetime(2026, 1, 1, tzinfo=UTC),
                "state_hash": b"a"*32}
    result = materialize([tuple(observed[c] for c in COLUMNS)], [1, 2], cutoff)
    assert result.results[1].known_at == cutoff
    assert result.results[1].state.recorded_at.year == 2026
    assert result.results[1].state.fulltime_home is None
    assert result.results[2].state is None


def test_contradictory_receipt_metadata_rejected_before_any_sql():
    instant = datetime(2024, 1, 1, tzinfo=UTC)
    evidence_id = uuid.uuid4()
    rows = [payload(1, instant, evidence_id=evidence_id),
            payload(2, datetime(2025, 1, 1, tzinfo=UTC), evidence_id=evidence_id)]
    with pytest.raises(ValueError, match="receipt metadata"):
        append_batch(None, None, CANDIDATES[0], rows, verify=False)
