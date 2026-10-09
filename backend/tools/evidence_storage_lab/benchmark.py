"""Matched staged measurements with raw samples and actual prepared-plan EXPLAINs."""

import json
import random
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from psycopg import sql

from tools.evidence_storage_lab.candidates import (
    CANDIDATES, Candidate, UTC, add_alternative, append_batch, classify, create_candidate, lifecycle,
    maintenance, native_partition_diagnostic, payload, provision, relation, storage, strict_sql,
)
from tools.evidence_storage_lab.dataset import full_row_parity, load_candidate, seed_source
from tools.evidence_storage_lab.safety import resources, sources, write_report

BATCHES = (1, 10, 380, 1000, 10_000)
MODES = ("fresh_unprepared", "prepared_auto", "forced_custom", "forced_generic")
CUTOFFS = {
    "early": datetime(2023, 1, 1, tzinfo=UTC),
    "median": datetime(2024, 7, 1, tzinfo=UTC),
    "latest": datetime(2026, 1, 1, tzinfo=UTC),
    "exact_boundary": datetime(2024, 1, 1, tzinfo=UTC),
    "ambiguous": datetime(2025, 12, 1, tzinfo=UTC),
    "no_eligible_history": datetime(2022, 12, 31, tzinfo=UTC),
    "cold_only": datetime(2024, 12, 31, tzinfo=UTC),
    "late_old_range": datetime(2024, 6, 2, tzinfo=UTC),
}


def distribution(samples):
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("No samples")

    def percentile(p):
        index = (len(ordered)-1)*p
        low = int(index)
        high = min(low+1, len(ordered)-1)
        return ordered[low] + (ordered[high]-ordered[low])*(index-low)

    return {"samples": len(samples), "min": ordered[0], "p50": percentile(0.5),
            "p95": percentile(0.95), "p99": percentile(0.99), "max": ordered[-1]}


def plan_summary(explain):
    nodes = []

    def walk(node):
        nodes.append({k: node[k] for k in ("Node Type", "Relation Name", "Index Name", "Actual Loops", "Plan Rows",
                                          "Actual Rows", "Subplans Removed", "Shared Hit Blocks", "Shared Read Blocks", "Heap Fetches") if k in node})
        for child in node.get("Plans", []):
            walk(child)

    walk(explain[0]["Plan"])
    return {"planning_ms": explain[0]["Planning Time"], "execution_ms": explain[0]["Execution Time"],
            "seq_scans": sum(n["Node Type"] == "Seq Scan" for n in nodes),
            "subplans_removed": sum(n.get("Subplans Removed", 0) for n in nodes),
            "executed_partition_leaves": sorted({n["Relation Name"] for n in nodes if n.get("Actual Loops", 0)>0
                                                  and n.get("Relation Name", "").startswith("p_")}),
            "nodes": nodes}


def materialize(rows, ids, cutoff):
    """Use the unchanged production result types; driver/SQLAlchemy overhead is reported separately."""
    from app.schemas.fixture_knowledge import FixtureKnowledge, KnowledgeStatus, ObservedFixtureState, StrictKnowledgeReport
    from tools.evidence_storage_lab.candidates import COLUMNS

    grouped = {}
    for row in rows:
        value = dict(zip(COLUMNS, row))
        value["observation_id"] = value.pop("id")
        value["state_hash"] = bytes(value["state_hash"])
        grouped.setdefault(value["fixture_id"], []).append(ObservedFixtureState(**value))
    results = {}
    for fixture_id in ids:
        observations = tuple(grouped.get(fixture_id, ()))
        if not observations:
            results[fixture_id] = FixtureKnowledge(fixture_id, cutoff, KnowledgeStatus.UNKNOWN_AT_T)
        else:
            status = KnowledgeStatus.KNOWN if len({o.state_hash for o in observations}) == 1 else KnowledgeStatus.TEMPORAL_AMBIGUITY
            results[fixture_id] = FixtureKnowledge(fixture_id, cutoff, status, observations[0].observed_at, observations)
    return StrictKnowledgeReport(cutoff, results)


def explain_current(conn, query, ids, cutoff, *, prepared):
    actual = None
    if prepared:
        table = query.as_string(conn).split("FROM ", 1)[1].split(" o JOIN", 1)[0]
        candidates = conn.execute("SELECT name,statement,generic_plans,custom_plans FROM pg_prepared_statements").fetchall()
        actual = next((row for row in candidates if row[1].startswith("SELECT o.*") and table in row[1]), None)
    if actual:
        explain = conn.execute(sql.SQL("EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) EXECUTE {}({}::integer[],{}::timestamptz)")
                               .format(sql.Identifier(actual[0]), sql.Literal(ids), sql.Literal(cutoff))).fetchone()[0]
        provenance = {"kind": "EXPLAIN EXECUTE actual driver prepared statement", "name": actual[0],
                      "generic_plans_before_explain": actual[2], "custom_plans_before_explain": actual[3]}
    else:
        explain = conn.execute(sql.SQL("EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) ") + query, (ids, cutoff), prepare=False).fetchone()[0]
        provenance = {"kind": "unprepared EXPLAIN of exact query shape"}
    return explain, provenance


def read_case(target, candidate, label, cutoff, batch, mode, samples, *, shared=None):
    rng = random.Random(20261008 + batch)
    ids = sorted(rng.sample(range(1, 10_001), batch))
    if label == "cold_only":
        # With requests >380, include all 380 cold-only fixtures plus representative others.
        ids = list(range(1, min(batch, 380)+1)) + sorted(rng.sample(range(381, 10_001), max(0, batch-380)))
    if label == "late_old_range":
        ids = [1] + [fixture_id for fixture_id in ids if fixture_id != 1][:batch-1]
    conn = shared or target.connect(prepare_threshold=None if mode == "fresh_unprepared" else 5 if mode == "prepared_auto" else 0)
    try:
        if mode == "forced_custom":
            conn.execute("SET plan_cache_mode=force_custom_plan", prepare=False)
        elif mode == "forced_generic":
            conn.execute("SET plan_cache_mode=force_generic_plan", prepare=False)
        query = strict_sql(candidate)
        transitions, raw, classifications = [], [], []
        # Eight warm-ups retain the first-use/driver preparation transition separately.
        for sample in range(8 + samples):
            began = time.perf_counter_ns()
            rows = conn.execute(query, (ids, cutoff)).fetchall()
            query_ms = (time.perf_counter_ns()-began)/1e6
            began = time.perf_counter_ns()
            report = materialize(rows, ids, cutoff)
            hydration_ms = (time.perf_counter_ns()-began)/1e6
            timing = {"query_fetch_ms": query_ms, "classification_ms": hydration_ms, "total_ms": query_ms+hydration_ms}
            if sample < 8:
                transitions.append(timing)
            else:
                raw.append(timing)
            classifications.append({status.value: count for status, count in report.counts.items()})
        explain, provenance = explain_current(conn, query, ids, cutoff, prepared=mode != "fresh_unprepared")
        return {"cutoff": label, "cutoff_at": cutoff, "batch": batch, "mode": mode,
                "latency_ms": {key: distribution([sample[key] for sample in raw]) for key in raw[0]},
                "warmup_transition_samples": transitions, "raw_samples": raw,
                "result_counts": classifications[-1], "plan": plan_summary(explain),
                "explain": explain, "plan_provenance": provenance,
                "fresh_definition": "new connection per case; preparation disabled throughout" if mode == "fresh_unprepared" else None}
    finally:
        if shared is None:
            conn.close()


def reads(target, candidate, samples):
    results = []
    for mode in MODES:
        shared = None if mode == "fresh_unprepared" else target.connect(prepare_threshold=5 if mode == "prepared_auto" else 0)
        try:
            for label, cutoff in CUTOFFS.items():
                for batch in BATCHES:
                    results.append(read_case(target, candidate, label, cutoff, batch, mode, samples, shared=shared))
            print(f"reads {candidate.name} {mode}: complete", flush=True)
        finally:
            if shared:
                shared.close()
    return results


def writes(conn, target, candidate, samples):
    results = []
    for label, heads in (("observation_insert", False), ("unchanged_confirmation", True)):
        for batch in (1, 10, 380, 1000):
            raw = []
            before = storage(conn, candidate)
            for sample in range(3 + samples):
                # Same normalized state/hash as the source's ordinary unchanged fixture cohort.
                evidence = uuid.uuid4()
                stamp = datetime(2026, 2, 1, tzinfo=UTC) + timedelta(seconds=sample + batch*100 + (10_000 if heads else 0))
                cohort = [i for i in range(4001, 10001) if i % 10][:batch]
                rows = [payload(i, stamp, evidence_id=evidence, partial=True) for i in cohort]
                target.verify(conn)  # safety/provenance checks are outside the timing interval
                lsn = conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
                began = time.perf_counter_ns()
                with conn.transaction():
                    result = append_batch(conn, target, candidate, rows, update_heads=heads, verify=False)
                elapsed = (time.perf_counter_ns()-began)/1e6
                assert result == {"inserted": batch, "replayed": 0}
                wal = int(conn.execute("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),%s)", (lsn,)).fetchone()[0])
                if sample >= 3:
                    raw.append({"total_commit_ms": elapsed, "wal_bytes": wal, "observations_per_second": batch/(elapsed/1000)})
            after = storage(conn, candidate)
            results.append({"operation": label, "batch": batch, "raw_samples": raw,
                            "metrics": {key: distribution([sample[key] for sample in raw]) for key in raw[0]},
                            "storage_growth": {key: after[key]-before[key] for key in
                                               ("observations", "payload_heap_toast_bytes", "payload_index_bytes", "registry_bytes")},
                            "scope": "canonical hash + evidence gateway + commit" + (" + ordered head metadata update" if heads else ""),
                            "not_full_production_writer": True})
    return results


def temporal_parity(conn, candidate):
    reference = Candidate("source")
    ids = list(range(1, 10_001)) + [10001]
    for label, cutoff in CUTOFFS.items():
        expected = conn.execute(strict_sql(reference), (ids, cutoff)).fetchall()
        actual = conn.execute(strict_sql(candidate), (ids, cutoff)).fetchall()
        if actual != expected:
            raise AssertionError(f"Strict full-row parity failed: {candidate.name}/{label}")
        if classify(actual, ids) != classify(expected, ids):
            raise AssertionError("Strict classification parity failed")
    return {"status": "PASS", "cutoffs": list(CUTOFFS), "requested_fixtures": len(ids),
            "compared": "all t-star rows including audit IDs, recorded_at, hashes and partial columns"}


def add_late_row(conn, target, candidate):
    row = payload(1, datetime(2024, 6, 1, tzinfo=UTC), evidence_id=uuid.UUID(int=424242), partial=True, goals=3)
    # Admission through the actual gateway is first proved in the small correctness gate.
    target.verify(conn)
    lsn = conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
    started = time.perf_counter()
    with conn.transaction():
        appended = append_batch(conn, target, candidate, [row], verify=False)
    elapsed = time.perf_counter()-started
    assert appended["inserted"] == 1
    # Live recorded_at differs between gateway writes; parity compares semantic columns for this
    # extra row, and exact audit fields for all staged source rows (never rewrite observations).
    return {"seconds": elapsed, "wal_bytes": int(conn.execute(
        "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),%s)", (lsn,)).fetchone()[0]),
        "evidence_id": row["evidence_id"], "source_fixture": 1, "observed_at": row["observed_at"]}


def assert_headroom(target, observations):
    value = resources(target.cluster)
    reserve = 5 * 1024**3
    next_candidate_budget = observations * 900
    if value["disk_free_bytes"] < reserve + next_candidate_budget or value["available_memory_bytes"] < 2*1024**3:
        raise RuntimeError(f"Insufficient safety headroom; no further dataset/candidate admitted: {value}")
    return value


def benchmark(target, observations, samples, output_directory):
    start_sources = sources()
    began = time.perf_counter()
    initial_resources = assert_headroom(target, observations)
    partial_path = Path(output_directory) / f"progress-{observations}.jsonl"
    if partial_path.exists():
        raise RuntimeError("New run/database required; progress evidence already exists")
    candidates = []
    with partial_path.open("x", encoding="utf-8") as journal, target.connect() as conn:
        start_lsn = conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
        diagnostic = native_partition_diagnostic(conn, target)
        dataset = seed_source(conn, target, observations)
        for candidate in CANDIDATES:
            checkpoint = {"candidate": candidate.name, "resources_before": assert_headroom(target, observations)}
            print(f"stage {observations:,}: {candidate.name}", flush=True)
            checkpoint["ddl"] = create_candidate(conn, target, candidate)
            checkpoint["load"] = load_candidate(conn, target, candidate)
            checkpoint["index_alternative"] = add_alternative(conn, target, candidate)
            checkpoint["maintenance"] = maintenance(conn, target, candidate)
            checkpoint["full_row_parity_before_lifecycle"] = full_row_parity(conn, candidate)
            checkpoint["lifecycle"] = lifecycle(conn, target, candidate)
            checkpoint["full_row_parity_after_lifecycle"] = full_row_parity(conn, candidate)
            checkpoint["strict_parity"] = temporal_parity(conn, candidate)
            checkpoint["seed_storage"] = storage(conn, candidate)
            # Read baselines precede write-growth passes so all candidates have matched histories.
            checkpoint["reads"] = reads(target, candidate, samples)
            checkpoint["late_arrival"] = add_late_row(conn, target, candidate)
            late = classify(conn.execute(strict_sql(candidate), ([1], CUTOFFS["late_old_range"])).fetchall(), [1])
            assert late[1]["state"]["home_goals"] == 3
            checkpoint["late_arrival"]["readable_at_cutoff"] = True
            # Explicitly measure a read after insertion into an old range, including cold overlay.
            checkpoint["late_read_after_insert"] = read_case(target, candidate, "late_old_range", CUTOFFS["late_old_range"],
                                                            380, "forced_generic", samples)
            checkpoint["writes"] = writes(conn, target, candidate, samples)
            checkpoint["final_storage"] = storage(conn, candidate)
            checkpoint["post_write_maintenance"] = maintenance(conn, target, candidate, freeze=True)
            if candidate.months:
                target.verify(conn)
                started = time.perf_counter()
                with conn.transaction():
                    provision(conn, candidate, 42, parent="history" if candidate.lifecycle != "none" else "fixture_observations")
                checkpoint["next_partition_provision_seconds"] = time.perf_counter()-started
            checkpoint["resources_after"] = resources(target.cluster)
            journal.write(json.dumps(checkpoint, default=str) + "\n")
            journal.flush()
            candidates.append(checkpoint)
        final_resources = resources(target.cluster)
        database_bytes = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        total_wal = int(conn.execute("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),%s)", (start_lsn,)).fetchone()[0])
        wal_on_disk = conn.execute("SELECT coalesce(sum(size),0) FROM pg_ls_waldir()").fetchone()[0]
    result = {"status": "MEASURED", "observations": observations, "dataset": dataset, "samples": samples,
              "native_partition_diagnostic": diagnostic, "candidates": candidates,
              "runtime_seconds": time.perf_counter()-began, "database_bytes": database_bytes,
              "wal_generated_bytes": total_wal, "wal_directory_bytes": int(wal_on_disk),
              "initial_resources": initial_resources, "final_resources": final_resources,
              "source_unchanged": start_sources == sources(), "multi_million_executed": False,
              "scope": "evidence storage gateway; not full application writer; same-disk hot/cold, warm cache, single client except correctness races",
              "limitations": ["p99 from 15 samples is near maximum, not a reliable rare-tail estimate",
                              "WAL LSN deltas include background cluster WAL; exclusive client workload, autovacuum remains on",
                              "Exact plan diagnostics are separate executions, not latency percentiles",
                              "Only PostgreSQL 18.6; no WAN, real tiered disks, PITR or large restore timing",
                              "Physical placement and archive seal enforcement are gateway-local prototypes"]}
    return result
