"""Chief-authorized Option B orchestration; measured helpers remain unchanged.

No init, source generation, completed-candidate execution, CASCADE or retry path.
The original journal is an immutable input. Recovery has a separate execution epoch.
"""

import argparse
import hashlib
import json
import os
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg import sql
from sqlalchemy.engine import make_url

from tools.evidence_storage_lab import benchmark as bench
from tools.evidence_storage_lab.candidates import (
    CANDIDATES, COLUMNS, STATE, IdentityConflict, add_alternative, append_batch, classify,
    create_candidate, lifecycle, maintenance, payload, provision, relation, storage, strict_sql,
)
from tools.evidence_storage_lab.dataset import full_row_parity, load_candidate
from tools.evidence_storage_lab.safety import (
    BASELINE, BRANCH, MARKER, ROOT, Target, environment, git, resources, sources, write_report,
)

CHECKPOINT = "3f5b1c5000596055bbaaf80c19a5af68e0b9a657"
MISSING = ("hotcold", "brin", "covering")
COMPLETED = ("control", "registry_control", "monthly", "quarterly", "archive")
RESERVE = 5 * 1024**3
ALLOWANCE = int(3.75 * 1024**3)
SOURCE_DIGEST = "9b0138593262db4526039ad5c5ec421cf5fac64bcbe9dec8f598faaec99759c3"
LAB = Path(__file__).parent


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def disk_gate(target, stage):
    value = resources(target.cluster)
    if value["disk_free_bytes"] - ALLOWANCE < RESERVE:
        raise RuntimeError(f"DISK_GATE_FAILED before {stage}: {value}")
    if value["available_memory_bytes"] < 2 * 1024**3:
        raise RuntimeError(f"MEMORY_GATE_FAILED before {stage}: {value}")
    return value


class RecoveryTarget(Target):
    """Same explicit cluster/database checks, under the newly authorized checkpoint.

    The original baseline-pinned Target is not relaxed or monkey-patched.
    """

    def verify(self, conn, *, initialized=True):
        if git("rev-parse", "HEAD") != CHECKPOINT or git("branch", "--show-current") != BRANCH:
            raise RuntimeError("Recovery checkpoint/branch ownership mismatch")
        authorized = Path(r"C:\Users\estef\OneDrive\Escritorio\prediktia-evidence-storage")
        if ROOT.resolve() != authorized.resolve() or Path(git("rev-parse", "--show-toplevel")).resolve() != authorized:
            raise RuntimeError("Recovery worktree mismatch")
        if git("diff", "--name-only", BASELINE, "--", "backend/app", "backend/alembic", "docs"):
            raise RuntimeError("CROSS_LANE_CONFLICT")
        if list((ROOT / "backend/alembic/versions").glob("0009*")):
            raise RuntimeError("CROSS_LANE_CONFLICT: 0009")
        if RecoveryTarget.from_environment() != self:
            raise RuntimeError("Explicit recovery target authorization changed")
        url = make_url(self.url)
        if (url.host, url.port, url.database) != ("127.0.0.1", 55449, "prediktia_lab_evidence_storage_1m"):
            raise RuntimeError("Only the existing owned 1M database is authorized")
        row = conn.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
            current_setting('data_directory'),system_identifier::text,current_setting('fsync'),
            current_setting('synchronous_commit'),current_setting('full_page_writes'),
            current_setting('listen_addresses') FROM pg_control_system()""").fetchone()
        if row[:3] != (url.database, url.host, url.port) or Path(row[3]).resolve() != self.cluster:
            raise RuntimeError("Effective recovery server/database mismatch")
        if row[4] != self.system_identifier or row[5:] != ("on", "on", "on", "127.0.0.1"):
            raise RuntimeError("Owned identifier/durability/loopback mismatch")
        owner = json.loads((self.cluster.parent / "owner.json").read_text(encoding="utf-8-sig"))
        if owner["marker"] != MARKER or owner["baseline"] != BASELINE or Path(owner["worktree"]).resolve() != ROOT:
            raise RuntimeError("Cluster owner mismatch")
        if str(owner["system_identifier"]) != self.system_identifier:
            raise RuntimeError("Owner system identifier mismatch")
        comment = conn.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
        if comment != f"{MARKER}:{BASELINE}":
            raise RuntimeError("Unmarked recovery database")


def configure(run_root):
    owner = json.loads((run_root / "owner.json").read_text(encoding="utf-8-sig"))
    database = "prediktia_lab_evidence_storage_1m"
    os.environ.update(
        EVIDENCE_STORAGE_DATABASE_URL=f"postgresql+psycopg://evidence_lab@127.0.0.1:{owner['port']}/{database}",
        EVIDENCE_STORAGE_ALLOW_DESTRUCTIVE=f"127.0.0.1:{owner['port']}/{database}",
        EVIDENCE_STORAGE_CLUSTER=owner["cluster"], EVIDENCE_STORAGE_SYSTEM_IDENTIFIER=str(owner["system_identifier"]),
    )
    return RecoveryTarget.from_environment()


def verify_inputs(run_root):
    manifest = json.loads((LAB / "artifact-manifest.json").read_text())
    if run_root.resolve() != Path(manifest["run_root"]).resolve():
        raise RuntimeError("Original run root mismatch")
    for artifact in manifest["artifacts"]:
        path = run_root / artifact["path"]
        if path.stat().st_size != artifact["bytes"] or file_digest(path) != artifact["sha256"]:
            raise RuntimeError(f"Original artifact changed: {artifact['path']}")
    gate = json.loads((run_root / "gate3-gate.json").read_text())
    if gate["result"]["status"] != "PASS" or gate["result"]["strict_knowledge_parity"] != "PASS":
        raise RuntimeError("Original correctness gate failed")
    current = sources()
    for path, digest in gate["source_sha256"].items():
        if path.replace("\\", "/") != "tools/evidence_storage_lab/README.md" and current.get(path) != digest:
            raise RuntimeError(f"Measured original source changed: {path}")
    with (run_root / "v2/progress-1000000.jsonl").open() as stream:
        original = [json.loads(line) for line in stream]
    if tuple(c["candidate"] for c in original) != COMPLETED:
        raise RuntimeError("Exactly five original completed records required")
    for candidate in original:
        audit_record(candidate)
    return original, gate


def binary_digest(conn, schema, table, columns=None, where=""):
    projection = sql.SQL("*") if columns is None else sql.SQL(",").join(map(sql.Identifier, columns))
    query = sql.SQL("COPY (SELECT {} FROM {} {} ORDER BY id) TO STDOUT WITH(FORMAT BINARY)")
    query = query.format(projection, relation(schema, table), sql.SQL(where))
    digest = hashlib.sha256()
    with conn.cursor().copy(query) as stream:
        for block in stream:
            digest.update(block)
    return digest.hexdigest()


def frozen_snapshot(conn):
    result = {"source": binary_digest(conn, "lab_source", "fixture_observations", COLUMNS)}
    if result["source"] != SOURCE_DIGEST:
        raise RuntimeError("Deterministic source checksum mismatch")
    for name in COMPLETED:
        schema = "lab_" + name
        result[name] = {
            "observations": binary_digest(conn, schema, "fixture_observations", COLUMNS),
            "heads": binary_digest(conn, schema, "fixtures"),
        }
        if name != "control":
            result[name]["registry"] = binary_digest(conn, schema, "identity_registry")
    return result


def inventory(conn):
    return {
        "relations": conn.execute("SELECT oid,relname,relkind,pg_total_relation_size(oid) FROM pg_class "
                                  "WHERE relnamespace='lab_hotcold'::regnamespace ORDER BY relname").fetchall(),
        "indexes": conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='lab_hotcold' ORDER BY indexname").fetchall(),
        "constraints": conn.execute("SELECT conname,conrelid::regclass::text,pg_get_constraintdef(oid) FROM pg_constraint "
                                    "WHERE connamespace='lab_hotcold'::regnamespace ORDER BY conname,conrelid").fetchall(),
        "partitions": conn.execute("SELECT c.relname,p.relname,pg_get_expr(c.relpartbound,c.oid) FROM pg_inherits i "
                                   "JOIN pg_class c ON c.oid=i.inhrelid JOIN pg_class p ON p.oid=i.inhparent "
                                   "WHERE c.relnamespace='lab_hotcold'::regnamespace ORDER BY c.relname").fetchall(),
        "maintenance_timestamps": conn.execute("SELECT relname,last_vacuum,last_analyze FROM pg_stat_all_tables "
                                               "WHERE schemaname='lab_hotcold' ORDER BY relname").fetchall(),
        "view": conn.execute("SELECT pg_get_viewdef('lab_hotcold.fixture_observations'::regclass,true)").fetchone()[0],
    }


def reject_external_dependencies(conn):
    inbound = conn.execute("SELECT conname FROM pg_constraint WHERE contype='f' AND connamespace<>'lab_hotcold'::regnamespace "
                           "AND confrelid IN(SELECT oid FROM pg_class WHERE relnamespace='lab_hotcold'::regnamespace)").fetchall()
    dependencies = conn.execute("""SELECT d.classid::regclass::text,d.objid,x.schema,x.identity
        FROM pg_depend d CROSS JOIN LATERAL pg_identify_object(d.classid,d.objid,d.objsubid) x
        WHERE d.deptype='n' AND ((d.refclassid='pg_class'::regclass AND d.refobjid IN
        (SELECT oid FROM pg_class WHERE relnamespace='lab_hotcold'::regnamespace)) OR
        (d.refclassid='pg_namespace'::regclass AND d.refobjid='lab_hotcold'::regnamespace))
        AND x.schema IS DISTINCT FROM 'lab_hotcold'""").fetchall()
    external = []
    for class_name, oid, schema, identity in dependencies:
        if class_name == "pg_rewrite":
            owner = conn.execute("SELECT c.relnamespace='lab_hotcold'::regnamespace FROM pg_rewrite r "
                                 "JOIN pg_class c ON c.oid=r.ev_class WHERE r.oid=%s", (oid,)).fetchone()
            if owner and owner[0]:
                continue
        external.append((class_name, oid, schema, identity))
    if inbound or external:
        raise RuntimeError(f"External dependencies prohibit isolated RESTRICT cleanup: {inbound}, {external}")
    return {"inbound_external_foreign_keys": [], "external_dependents": [], "status": "PASS"}


def preserve_and_remove_partial(conn, target, output):
    target.verify(conn)
    disk_gate(target, "partial forensic preservation")
    with conn.transaction():
        conn.execute("LOCK TABLE lab_hotcold.history,lab_hotcold.cold_history,lab_hotcold.late_evidence,"
                     "lab_hotcold.identity_registry,lab_hotcold.fixtures,lab_hotcold.providers,lab_hotcold.archive_manifest "
                     "IN ACCESS EXCLUSIVE MODE")
        counts = conn.execute("SELECT (SELECT count(*) FROM lab_hotcold.identity_registry),"
                              "(SELECT count(*) FROM lab_hotcold.history),(SELECT count(*) FROM lab_hotcold.cold_history),"
                              "(SELECT count(*) FROM lab_hotcold.late_evidence)").fetchone()
        if counts != (1_000_000, 307840, 692160, 0):
            raise RuntimeError("Partial hot/cold state differs from approved forensic state")
        digest = binary_digest(conn, "lab_hotcold", "fixture_observations", COLUMNS)
        if digest != SOURCE_DIGEST:
            raise RuntimeError("Partial hot/cold checksum mismatch")
        captured_inventory = inventory(conn)
        metadata = {"captured_at": datetime.now(timezone.utc), "schema": "lab_hotcold", "counts": counts,
                    "binary_copy_sha256": digest, "inventory": captured_inventory,
                    "allocated_relation_bytes": sum(r[3] for r in captured_inventory["relations"] if r[2] in ("r", "S")),
                    "lifecycle": conn.execute("SELECT * FROM lab_hotcold.archive_manifest").fetchall(),
                    "timing_measurements": "INCOMPLETE: no valid 1M timing samples persisted",
                    "dependencies": reject_external_dependencies(conn), "cleanup": "schema-local RESTRICT; no CASCADE"}
        path = output / "partial-hotcold-forensics.json"
        write_report(path, metadata)
        expected = json.loads(path.read_text())
        if expected["binary_copy_sha256"] != digest:
            raise RuntimeError("Forensic metadata verification failed")
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())
        target.verify(conn)
        disk_gate(target, "isolated hot/cold RESTRICT cleanup")
        reject_external_dependencies(conn)
        conn.execute("DROP VIEW lab_hotcold.fixture_observations RESTRICT")
        for name in ("late_evidence", "history", "cold_history", "identity_registry", "fixtures", "providers", "archive_manifest"):
            conn.execute(sql.SQL("DROP TABLE {} RESTRICT").format(relation("lab_hotcold", name)))
        conn.execute("DROP SCHEMA lab_hotcold RESTRICT")
    return {"metadata_sha256": file_digest(path), "removed_only": "lab_hotcold", "cascade": False}


def audit_record(candidate):
    import math

    if candidate["seed_storage"]["observations"] != 1_000_000 or candidate["final_storage"]["observations"] != 1_050_077:
        raise AssertionError("1M seed/final cardinality mismatch")
    for key in ("full_row_parity_before_lifecycle", "full_row_parity_after_lifecycle"):
        if candidate[key]["status"] != "PASS" or candidate[key]["full_row_symmetric_difference"] or candidate[key]["noncanonical_hashes"]:
            raise AssertionError("Full row/hash parity failed")
    expected = {(t, b, m) for t in bench.CUTOFFS for b in bench.BATCHES for m in bench.MODES}
    if len(candidate["reads"]) != 160 or {(r["cutoff"], r["batch"], r["mode"]) for r in candidate["reads"]} != expected:
        raise AssertionError("Read case coverage mismatch")
    for read in candidate["reads"] + [candidate["late_read_after_insert"]]:
        if len(read["raw_samples"]) != 15 or len(read["warmup_transition_samples"]) != 8:
            raise AssertionError("Sample count changed")
        if bench.plan_summary(read["explain"]) != read["plan"] or sum(read["result_counts"].values()) != read["batch"]:
            raise AssertionError("Plan/result summary mismatch")
        for key, value in read["latency_ms"].items():
            actual = bench.distribution([s[key] for s in read["raw_samples"]])
            if any(not math.isclose(actual[k], value[k], rel_tol=1e-12, abs_tol=1e-9) for k in actual):
                raise AssertionError("Read percentiles mismatch")
    if len(candidate["writes"]) != 8 or not candidate["post_write_maintenance"]["freeze"]:
        raise AssertionError("Write/maintenance coverage mismatch")
    for write in candidate["writes"]:
        if len(write["raw_samples"]) != 15 or write["storage_growth"]["observations"] != 18 * write["batch"]:
            raise AssertionError("Write samples/count mismatch")
        for key, value in write["metrics"].items():
            actual = bench.distribution([s[key] for s in write["raw_samples"]])
            if any(not math.isclose(actual[k], value[k], rel_tol=1e-12, abs_tol=1e-9) for k in actual):
                raise AssertionError("Write percentiles mismatch")
    if candidate["strict_parity"]["status"] != "PASS" or not candidate["late_arrival"]["readable_at_cutoff"]:
        raise AssertionError("Strict/late parity failed")


def compare_control(conn, candidate):
    """Audit reads only, never rerun/timestamp the completed control benchmark."""
    control = CANDIDATES[0]
    ids = list(range(1, 10001)) + [10001]
    for cutoff in bench.CUTOFFS.values():
        actual = conn.execute(strict_sql(candidate), (ids, cutoff)).fetchall()
        expected = conn.execute(strict_sql(control), (ids, cutoff)).fetchall()
        # Live admission recorded_at belongs to each epoch; immutable source audit fields
        # were checked separately byte-for-byte, and live audit rows remain unchanged.
        def comparable(rows):
            return [tuple(value for index, value in enumerate(row) if index != COLUMNS.index("recorded_at")) for row in rows]
        if comparable(actual) != comparable(expected):
            raise AssertionError(f"Unpartitioned-control parity failed for {candidate.name}/{cutoff}")
        result = classify(actual, ids)
        if result[10001]["status"] != "UNKNOWN_AT_T":
            raise AssertionError("UNKNOWN_AT_T weakened")
    return {"status": "PASS", "reference": "original unpartitioned control, audit reads only",
            "cutoffs": list(bench.CUTOFFS), "live_recorded_at": "different epochs; audited locally, not equated"}


def replay_audit(conn, target, candidate):
    """Extra correctness work follows measurements and always rolls back."""
    row = dict(zip(COLUMNS, conn.execute(sql.SQL("SELECT * FROM {} WHERE id=1").format(
        relation(candidate.schema, "fixture_observations"))).fetchone()))
    with conn.transaction(force_rollback=True):
        if append_batch(conn, target, candidate, [row]) != {"inserted": 0, "replayed": 1}:
            raise AssertionError("Exact replay changed identity")
    for key, value in (("home_goals", 7), ("observed_at", datetime(2024, 3, 1, tzinfo=timezone.utc))):
        try:
            with conn.transaction(force_rollback=True):
                append_batch(conn, target, candidate, [{**row, key: value}])
        except IdentityConflict:
            pass
        else:
            raise AssertionError("Inconsistent replay accepted")
    from uuid import UUID
    instant = datetime(2024, 5, 15, tzinfo=timezone.utc)
    with conn.transaction(force_rollback=True):
        for fixture, eid, goals in ((1, 90000001, 1), (1, 90000002, 1), (2, 90000003, 1), (2, 90000004, 2)):
            append_batch(conn, target, candidate, [payload(fixture, instant, evidence_id=UUID(int=eid), partial=True, goals=goals)])
        result = classify(conn.execute(strict_sql(candidate), ([1, 2, 10001], instant)).fetchall(), [1, 2, 10001])
        if result[1]["status"] != "KNOWN" or len(result[1]["observations"]) != 2 or result[1]["state"]["fulltime_home"] is not None:
            raise AssertionError("Independent unchanged confirmation / AS_OBSERVED failed")
        if result[2]["status"] != "TEMPORAL_AMBIGUITY" or result[2]["state"] is not None:
            raise AssertionError("Tie ambiguity failed")
    restrict = []
    for table, key, value in (("fixtures", "id", 1), ("providers", "code", "api-football")):
        try:
            with conn.transaction(force_rollback=True):
                conn.execute(sql.SQL("DELETE FROM {} WHERE {}=%s").format(relation(candidate.schema, table), sql.Identifier(key)), (value,))
        except Exception as exc:
            if getattr(exc, "sqlstate", None) not in ("23503", "23001"):
                raise
            restrict.append(table)
        else:
            raise AssertionError("DELETE RESTRICT missing")
    return {"status": "PASS", "exact_replay": "PASS", "inconsistent_replay": "PASS", "confirmations_partial_ties": "PASS",
            "delete_restrict": restrict, "extra_evidence_committed": False, "scope": "post-measurement rollback-only audit"}


def run_candidate(conn, target, candidate, epoch):
    checkpoints = {"candidate": candidate.name, "execution_epoch": epoch,
                   "resources_before": disk_gate(target, candidate.name)}

    def stage(name, function):
        disk_gate(target, f"{candidate.name}/{name}")
        print(f"recovery {candidate.name}: {name}", flush=True)
        checkpoints[name] = function()

    stage("ddl", lambda: create_candidate(conn, target, candidate))
    stage("load", lambda: load_candidate(conn, target, candidate))
    stage("index_alternative", lambda: add_alternative(conn, target, candidate))
    stage("maintenance", lambda: maintenance(conn, target, candidate))
    stage("full_row_parity_before_lifecycle", lambda: full_row_parity(conn, candidate))
    stage("lifecycle", lambda: lifecycle(conn, target, candidate))
    stage("full_row_parity_after_lifecycle", lambda: full_row_parity(conn, candidate))
    stage("strict_parity", lambda: bench.temporal_parity(conn, candidate))
    stage("seed_storage", lambda: storage(conn, candidate))
    stage("reads", lambda: bench.reads(target, candidate, 15))
    stage("late_arrival", lambda: bench.add_late_row(conn, target, candidate))
    late = classify(conn.execute(strict_sql(candidate), ([1], bench.CUTOFFS["late_old_range"])).fetchall(), [1])
    if late[1]["state"]["home_goals"] != 3:
        raise AssertionError("Late historical evidence missing")
    checkpoints["late_arrival"]["readable_at_cutoff"] = True
    stage("late_read_after_insert", lambda: bench.read_case(target, candidate, "late_old_range", bench.CUTOFFS["late_old_range"], 380, "forced_generic", 15))
    stage("writes", lambda: bench.writes(conn, target, candidate, 15))
    stage("final_storage", lambda: storage(conn, candidate))
    stage("post_write_maintenance", lambda: maintenance(conn, target, candidate, freeze=True))
    if candidate.months:
        disk_gate(target, "next partition")
        target.verify(conn)
        started = time.perf_counter()
        with conn.transaction():
            provision(conn, candidate, 42, parent="history")
        checkpoints["next_partition_provision_seconds"] = time.perf_counter() - started
    audit_record(checkpoints)
    checkpoints["unpartitioned_control_parity"] = compare_control(conn, candidate)
    before = binary_digest(conn, candidate.schema, "fixture_observations", COLUMNS)
    checkpoints["post_measurement_semantics"] = replay_audit(conn, target, candidate)
    if binary_digest(conn, candidate.schema, "fixture_observations", COLUMNS) != before:
        raise AssertionError("Rollback audit changed committed evidence")
    if binary_digest(conn, candidate.schema, "fixture_observations", COLUMNS, "WHERE id<=1000000") != SOURCE_DIGEST:
        raise AssertionError("Source audit rows changed")
    checkpoints["resources_after"] = resources(target.cluster)
    return checkpoints


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidates", nargs="+", choices=MISSING, required=True)
    args = parser.parse_args(argv)
    if tuple(args.candidates) != MISSING:
        parser.error("This authorization requires exactly hotcold brin covering, in that order")
    if args.output.exists():
        parser.error("Recovery output must be NEW; never overwrite or silently retry")
    return args


def main(argv=None):
    args = parse_args(argv)
    original, gate = verify_inputs(args.run_root)
    target = configure(args.run_root)
    initial_sources = sources()
    disk_gate(target, "recovery admission")
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        with target.connect() as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(20261009,55449)").fetchone()[0]:
                raise RuntimeError("Another recovery owns the lab lock")
            if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()[0]:
                raise RuntimeError("Unexpected other clients; recovery requires quiescence")
            if conn.execute("SELECT to_regnamespace('lab_brin'),to_regnamespace('lab_covering')").fetchone() != (None, None):
                raise RuntimeError("Missing candidate namespace exists; no retry or overwrite")
            frozen = frozen_snapshot(conn)
            write_report(args.output / "immutable-inputs.json", {"database_snapshot": frozen, "source_sha256": initial_sources,
                         "original_gate_sha256": file_digest(args.run_root / "gate3-gate.json"), "environment": environment(target)})
            cleanup = preserve_and_remove_partial(conn, target, args.output)
            if frozen_snapshot(conn) != frozen:
                raise AssertionError("Isolated cleanup altered immutable inputs")
            epoch = datetime.now(timezone.utc).isoformat()
            recovered = []
            with (args.output / "recovery-1m.jsonl").open("x", encoding="utf-8") as journal:
                for name in args.candidates:
                    candidate = next(c for c in CANDIDATES if c.name == name)
                    record = run_candidate(conn, target, candidate, epoch)
                    journal.write(json.dumps(record, default=str) + "\n")
                    journal.flush()
                    os.fsync(journal.fileno())
                    recovered.append(record)
            if frozen_snapshot(conn) != frozen or sources() != initial_sources:
                raise AssertionError("Immutable inputs or recovery sources changed")
            verify_inputs(args.run_root)
            merged = [{**c, "execution_epoch": "original 2026-10-08"} for c in original] + recovered
            if {c["candidate"] for c in merged} != {c.name for c in CANDIDATES} or len(merged) != 8:
                raise AssertionError("Final eight-candidate uniqueness audit failed")
            write_report(args.output / "aggregated-1m.json", {"status": "AUDITED_8_OF_8", "observations": 1_000_000,
                "candidates": merged, "cleanup": cleanup, "immutable_inputs_preserved": True,
                "source_sha256": initial_sources, "original_correctness_gate": gate["result"],
                "original_campaign_runtime_seconds": None, "original_campaign_wal_bytes": None,
                "epoch_policy": "five original records + three sequential recovery records; no fabricated whole-campaign totals"})
            write_report(args.output / "output-checksums.json", {p.name: {"bytes": p.stat().st_size, "sha256": file_digest(p)}
                         for p in args.output.iterdir() if p.is_file()})
            print(f"AUDIT COMPLETE: {args.output}; 8/8; immutable inputs preserved", flush=True)
    except BaseException:
        write_report(args.output / "failure.json", {"status": "BLOCKED", "traceback": traceback.format_exc(),
                     "silent_retry": False, "completed_recovery_evidence_retained": True})
        raise


if __name__ == "__main__":
    main()
