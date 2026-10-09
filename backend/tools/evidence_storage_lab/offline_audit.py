"""Validate retained 5+3 evidence without connecting to PostgreSQL or running work.

Run from backend: python -B -m tools.evidence_storage_lab.offline_audit
Original and recovery manifests are immutable artifact references, not a resume API.
"""

import json
from pathlib import Path

from tools.evidence_storage_lab.benchmark import CUTOFFS, MODES
from tools.evidence_storage_lab.missing_only import (
    COMPLETED, LAB, MISSING, SOURCE_DIGEST, audit_record, file_digest, verify_inputs,
)
from tools.evidence_storage_lab.safety import sources


def verify_artifact(path, expected):
    if path.stat().st_size != expected["bytes"] or file_digest(path) != expected["sha256"]:
        raise AssertionError(f"Artifact checksum/size mismatch: {path}")


def compare_records(original, recovered, aggregate):
    expected_names = COMPLETED + MISSING
    records = aggregate["candidates"]
    if aggregate["status"] != "AUDITED_8_OF_8" or not aggregate["immutable_inputs_preserved"]:
        raise AssertionError("Final aggregation is not audited")
    if aggregate["observations"] != 1_000_000:
        raise AssertionError("Unauthorized scale")
    if tuple(c["candidate"] for c in records) != expected_names:
        raise AssertionError("Candidate uniqueness/order mismatch")
    if len(original) != 5 or len(recovered) != 3:
        raise AssertionError("Expected exactly five original and three recovery records")
    for saved, merged in zip(original, records[:5]):
        if merged != {**saved, "execution_epoch": "original 2026-10-08"}:
            raise AssertionError("Original candidate record was altered")
    if recovered != records[5:]:
        raise AssertionError("Recovery journal differs from aggregate")
    if len({c["execution_epoch"] for c in recovered}) != 1 or recovered[0]["execution_epoch"] == records[0]["execution_epoch"]:
        raise AssertionError("Execution epochs must remain separate")
    for key in ("original_campaign_runtime_seconds", "original_campaign_wal_bytes"):
        if aggregate[key] is not None:
            raise AssertionError("Unavailable original campaign totals must stay null")
    reference = {(r["cutoff"], r["batch"], r["mode"]): r["result_counts"] for r in records[0]["reads"]}
    for candidate in records:
        audit_record(candidate)
        if candidate["strict_parity"]["cutoffs"] != list(CUTOFFS):
            raise AssertionError("Strict parity cutoff coverage changed")
        actual = {(r["cutoff"], r["batch"], r["mode"]): r["result_counts"] for r in candidate["reads"]}
        if actual != reference or candidate["late_read_after_insert"]["result_counts"] != records[0]["late_read_after_insert"]["result_counts"]:
            raise AssertionError("Read classifications differ from unpartitioned control")
        writes = candidate["writes"]
        if {(w["operation"], w["batch"]) for w in writes} != {
            (op, batch) for op in ("observation_insert", "unchanged_confirmation") for batch in (1, 10, 380, 1000)
        }:
            raise AssertionError("Write case coverage changed")
        if not candidate["index_alternative"]["existing_indexes_preserved"]:
            raise AssertionError("Index experiment dropped original indexes")
        if candidate["candidate"] in MISSING:
            if candidate["unpartitioned_control_parity"]["status"] != "PASS":
                raise AssertionError("Direct recovery/control row parity failed")
            semantics = candidate["post_measurement_semantics"]
            if any(semantics[k] != "PASS" for k in ("status", "exact_replay", "inconsistent_replay", "confirmations_partial_ties")):
                raise AssertionError("Recovered identity/temporal semantics failed")
            if semantics["extra_evidence_committed"] or semantics["delete_restrict"] != ["fixtures", "providers"]:
                raise AssertionError("Rollback/RESTRICT audit failed")
    return records


def summarize(candidate):
    seed, final = candidate["seed_storage"], candidate["final_storage"]
    reads = candidate["reads"]
    latest = {r["mode"]: r for r in reads if r["cutoff"] == "latest" and r["batch"] == 380}
    return {
        "candidate": candidate["candidate"], "execution_epoch": candidate["execution_epoch"],
        "seed_bytes": seed["evidence_total_bytes"], "final_bytes": final["evidence_total_bytes"],
        "payload_index_bytes": seed["payload_index_bytes"], "registry_bytes": seed["registry_bytes"],
        "payload_index_growth_bytes": final["payload_index_bytes"] - seed["payload_index_bytes"],
        "latest_380_p50_ms": {mode: latest[mode]["latency_ms"]["total_ms"]["p50"] for mode in MODES},
        "latest_380_generic_p95_ms": latest["forced_generic"]["latency_ms"]["total_ms"]["p95"],
        "generic_380_p50_by_cutoff_ms": {r["cutoff"]: r["latency_ms"]["total_ms"]["p50"] for r in reads if r["batch"] == 380 and r["mode"] == "forced_generic"},
        "writes_380": {w["operation"]: {"p50_ms": w["metrics"]["total_commit_ms"]["p50"],
                           "p50_wal_bytes_per_row": w["metrics"]["wal_bytes"]["p50"] / 380}
                       for w in candidate["writes"] if w["batch"] == 380},
        "load_wal_bytes": candidate["load"]["wal_bytes"],
        "vacuum_analyze_seconds": candidate["maintenance"]["seconds"],
        "post_write_freeze_seconds": candidate["post_write_maintenance"]["seconds"],
        "alternative_index_used_read_cases": {name: sum(any(n.get("Index Name") == name for n in r["plan"]["nodes"]) for r in reads)
                                               for name in ("lab_observed_brin", "lab_temporal_covering")},
    }


def audit():
    manifest = json.loads((LAB / "recovery-manifest.json").read_text())
    root = Path(manifest["run_root"])
    original, gate = verify_inputs(root)
    for artifact in manifest["artifacts"]:
        verify_artifact(root / artifact["path"], artifact)
    output = root / manifest["recovery_namespace"]
    checks = json.loads((output / "output-checksums.json").read_text())
    if set(checks) != {"aggregated-1m.json", "immutable-inputs.json", "partial-hotcold-forensics.json", "recovery-1m.jsonl"}:
        raise AssertionError("Recovery artifact inventory mismatch")
    for name, expected in checks.items():
        verify_artifact(output / name, expected)
    aggregate = json.loads((output / "aggregated-1m.json").read_text())
    with (output / "recovery-1m.jsonl").open() as stream:
        recovered = [json.loads(line) for line in stream]
    records = compare_records(original, recovered, aggregate)
    immutable = json.loads((output / "immutable-inputs.json").read_text())
    if immutable["database_snapshot"]["source"] != SOURCE_DIGEST:
        raise AssertionError("Source fingerprint changed")
    current = sources()
    for path, expected in aggregate["source_sha256"].items():
        if path.replace("\\", "/") != "tools/evidence_storage_lab/README.md" and current.get(path) != expected:
            raise AssertionError(f"Recovery measurement source changed: {path}")
    forensic = json.loads((output / "partial-hotcold-forensics.json").read_text())
    if (forensic["counts"] != [1_000_000, 307840, 692160, 0] or forensic["binary_copy_sha256"] != SOURCE_DIGEST
            or forensic["allocated_relation_bytes"] != 528007168 or forensic["dependencies"]["status"] != "PASS"
            or forensic["dependencies"]["external_dependents"] or forensic["dependencies"]["inbound_external_foreign_keys"]):
        raise AssertionError("Partial-state forensic provenance failed")
    if aggregate["cleanup"] != {"metadata_sha256": file_digest(output / "partial-hotcold-forensics.json"), "removed_only": "lab_hotcold", "cascade": False}:
        raise AssertionError("Isolated RESTRICT cleanup evidence failed")
    if gate["result"]["backup_restore"]["status"] != "PASS":
        raise AssertionError("Original logical recovery gate failed")
    return {"status": "PASS", "completed_1m": "8 / 8", "original_records_unchanged": True,
            "execution_epochs_separate": True, "strict_knowledge_control_parity": "PASS",
            "database_connection": False, "benchmark_execution": False,
            "original_campaign_runtime_seconds": None, "original_campaign_wal_bytes": None,
            "candidates": [summarize(c) for c in records]}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
