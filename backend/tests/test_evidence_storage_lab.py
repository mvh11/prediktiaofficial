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


@pytest.mark.parametrize("candidate", ["control", "registry_control", "monthly", "quarterly", "archive"])
def test_missing_only_refuses_completed_candidates(candidate, tmp_path):
    from tools.evidence_storage_lab.missing_only import parse_args as recovery_args
    with pytest.raises(SystemExit):
        recovery_args(["--run-root", str(tmp_path), "--output", str(tmp_path/"new"), "--candidates", candidate])


def test_missing_only_requires_exact_order_and_fresh_output(tmp_path):
    from tools.evidence_storage_lab.missing_only import parse_args as recovery_args
    base = ["--run-root", str(tmp_path), "--output", str(tmp_path/"new"), "--candidates"]
    assert recovery_args(base+["hotcold", "brin", "covering"]).candidates == ["hotcold", "brin", "covering"]
    for names in (["hotcold"], ["brin", "hotcold", "covering"], ["hotcold", "hotcold", "covering"]):
        with pytest.raises(SystemExit):
            recovery_args(base+names)
    (tmp_path/"new").mkdir()
    with pytest.raises(SystemExit):
        recovery_args(base+["hotcold", "brin", "covering"])


def test_missing_only_disk_gate_projects_five_gib_reserve(monkeypatch):
    from types import SimpleNamespace
    from tools.evidence_storage_lab import missing_only
    target = SimpleNamespace(cluster="unused")
    def measured(free):
        return {"disk_free_bytes": free, "available_memory_bytes": 3*1024**3}
    monkeypatch.setattr(missing_only, "resources", lambda _: measured(missing_only.RESERVE+missing_only.ALLOWANCE-1))
    with pytest.raises(RuntimeError, match="DISK_GATE_FAILED"):
        missing_only.disk_gate(target, "test")
    monkeypatch.setattr(missing_only, "resources", lambda _: measured(missing_only.RESERVE+missing_only.ALLOWANCE))
    assert missing_only.disk_gate(target, "test")["disk_free_bytes"] == missing_only.RESERVE+missing_only.ALLOWANCE


def test_offline_artifact_audit_detects_content_and_size_changes(tmp_path):
    from tools.evidence_storage_lab.missing_only import file_digest
    from tools.evidence_storage_lab.offline_audit import verify_artifact
    path = tmp_path/"journal.jsonl"
    path.write_bytes(b"original")
    expected = {"bytes": path.stat().st_size, "sha256": file_digest(path)}
    verify_artifact(path, expected)
    for content in (b"modified", b"truncated"):
        path.write_bytes(content)
        with pytest.raises(AssertionError, match="checksum/size"):
            verify_artifact(path, expected)


@pytest.fixture
def offline_records(monkeypatch):
    from tools.evidence_storage_lab import offline_audit
    from tools.evidence_storage_lab.benchmark import CUTOFFS
    from tools.evidence_storage_lab.missing_only import COMPLETED, MISSING
    # Exercise aggregation guards independently; real sample/plan auditing is
    # performed by audit_record() on the retained artifacts, not these tiny stubs.
    monkeypatch.setattr(offline_audit, "audit_record", lambda _: None)
    def record(name):
        value = {"candidate": name, "strict_parity": {"cutoffs": list(CUTOFFS)},
                 "reads": [{"cutoff": cutoff, "batch": 380, "mode": "forced_generic", "result_counts": {"KNOWN": 380}} for cutoff in CUTOFFS],
                 "late_read_after_insert": {"result_counts": {"KNOWN": 380}},
                 "writes": [{"operation": op, "batch": batch} for op in ("observation_insert", "unchanged_confirmation") for batch in (1, 10, 380, 1000)],
                 "index_alternative": {"existing_indexes_preserved": True}}
        if name in MISSING:
            value.update(execution_epoch="recovery", unpartitioned_control_parity={"status": "PASS"},
                         post_measurement_semantics={"status": "PASS", "exact_replay": "PASS", "inconsistent_replay": "PASS",
                             "confirmations_partial_ties": "PASS", "delete_restrict": ["fixtures", "providers"], "extra_evidence_committed": False})
        return value
    original = [record(name) for name in COMPLETED]
    recovered = [record(name) for name in MISSING]
    aggregate = {"status": "AUDITED_8_OF_8", "immutable_inputs_preserved": True, "observations": 1_000_000,
                 "original_campaign_runtime_seconds": None, "original_campaign_wal_bytes": None,
                 "candidates": copy.deepcopy([{**r, "execution_epoch": "original 2026-10-08"} for r in original]+recovered)}
    return original, recovered, aggregate


def test_offline_aggregation_accepts_unchanged_five_plus_three(offline_records):
    from tools.evidence_storage_lab.offline_audit import compare_records
    assert len(compare_records(*offline_records)) == 8


@pytest.mark.parametrize("corruption", ["original", "recovery", "duplicate", "epoch", "runtime", "wal", "scale",
                                        "classifications", "writes", "semantics", "indexes"])
def test_offline_aggregation_rejects_tampered_evidence(offline_records, corruption):
    from tools.evidence_storage_lab.offline_audit import compare_records
    original, recovered, aggregate = offline_records
    records = aggregate["candidates"]
    if corruption == "original":
        records[0]["extra"] = "changed"
    elif corruption == "recovery":
        records[5]["extra"] = "changed"
    elif corruption == "duplicate":
        records[-1]["candidate"] = "brin"
    elif corruption == "epoch":
        for r in recovered:
            r["execution_epoch"] = "original 2026-10-08"
        records[5:] = copy.deepcopy(recovered)
    elif corruption in ("runtime", "wal"):
        aggregate["original_campaign_runtime_seconds" if corruption == "runtime" else "original_campaign_wal_bytes"] = 123
    elif corruption == "scale":
        aggregate["observations"] = 5_000_000
    else:
        if corruption == "classifications":
            recovered[0]["reads"][0]["result_counts"] = {"UNKNOWN_AT_T": 380}
        elif corruption == "writes":
            recovered[0]["writes"].pop()
        elif corruption == "semantics":
            recovered[0]["post_measurement_semantics"]["exact_replay"] = "FAIL"
        elif corruption == "indexes":
            recovered[0]["index_alternative"]["existing_indexes_preserved"] = False
        records[5:] = copy.deepcopy(recovered)
    with pytest.raises(AssertionError):
        compare_records(original, recovered, aggregate)
