"""Real PostgreSQL gates, including blocked concurrent replay and production-reader parity."""

import copy
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tools.evidence_storage_lab.candidates import (
    CANDIDATES, COLUMNS, UTC, IdentityConflict, append_batch, classify, create_candidate,
    lifecycle, native_partition_diagnostic, payload, relation, strict_sql,
)


def fingerprint(conn, candidate, *, base_only=False):
    table = "history" if base_only else "fixture_observations"
    return conn.execute(sql.SQL("SELECT count(*),md5(coalesce(string_agg(row_to_json(o)::text,'|' ORDER BY id),'')) "
                               "FROM {} o").format(relation(candidate.schema, table))).fetchone()


def normalized(results):
    result = copy.deepcopy(results)
    for value in result.values():
        for observation in value["observations"]:
            observation.pop("id", None)
            observation.pop("recorded_at", None)
        if value["state"]:
            value["state"].pop("id", None)
            value["state"].pop("recorded_at", None)
    return result


def production_read(target, candidate, ids, cutoff):
    """Call unchanged app STRICT_KNOWLEDGE against a LAB search_path, not a clone of its code."""
    target.configure_app()
    from app.repositories.fixture_knowledge_repository import strict_knowledge

    with target.connect():
        pass
    engine = create_engine(target.url)
    try:
        with engine.connect() as conn:
            conn.execute(text(f'SET LOCAL search_path TO "{candidate.schema}",public'))
            with Session(bind=conn) as db:
                report = strict_knowledge(db, ids, cutoff)
                result = {}
                for fixture_id, knowledge in report.results.items():
                    observations = []
                    for observed in knowledge.observations:
                        row = asdict(observed)
                        row["id"] = row.pop("observation_id")
                        observations.append(row)
                    result[fixture_id] = {"status": knowledge.status.value, "observations": observations,
                                          "state": observations[0] if knowledge.state else None}
                return result
    finally:
        engine.dispose()


def concurrent_gate(target, candidate, fixture_id, *, conflict=False):
    evidence = payload(fixture_id, datetime(2025, 11, 1, tzinfo=UTC), evidence_id=uuid.uuid4())
    competing = {**evidence, "observed_at": datetime(2024, 3, 1, tzinfo=UTC)} if conflict else evidence
    connected = threading.Event()
    outcome = {}

    def waiter():
        try:
            with target.connect() as conn:
                outcome["pid"] = conn.info.backend_pid
                connected.set()
                with conn.transaction():
                    outcome["result"] = append_batch(conn, target, candidate, [competing])
        except BaseException as exc:
            outcome["exception"] = exc
            connected.set()

    with target.connect() as holding, target.connect() as observer:
        with holding.transaction():
            append_batch(holding, target, candidate, [evidence])
            thread = threading.Thread(target=waiter)
            thread.start()
            if not connected.wait(10):
                raise AssertionError("Concurrent writer did not connect")
            blocked = False
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                row = observer.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (outcome.get("pid", -1),)).fetchone()
                if row and row[0] == "Lock":
                    blocked = True
                    break
                if "exception" in outcome:
                    break
                time.sleep(0.02)
            if not blocked:
                raise AssertionError(f"Replay/conflict did not wait on real uniqueness lock: {outcome}")
        thread.join(15)
        if thread.is_alive():
            raise AssertionError("Concurrent writer failed to finish")
        if conflict:
            if not isinstance(outcome.get("exception"), IdentityConflict):
                raise AssertionError(f"Concurrent inconsistent replay not rejected: {outcome}")
        elif outcome.get("result") != {"inserted": 0, "replayed": 1}:
            raise AssertionError(f"Concurrent replay duplicated evidence: {outcome}")
        count = observer.execute(sql.SQL("SELECT count(*) FROM {} WHERE fixture_id=%s AND evidence_id=%s")
                                 .format(relation(candidate.schema, "fixture_observations")), (fixture_id, evidence["evidence_id"])).fetchone()[0]
        if count != 1:
            raise AssertionError("Concurrent global identity failed")
    return {"status": "PASS", "lock_wait_observed": blocked, "conflict": conflict, "payload_rows": count}


def run_gate(target):
    with target.connect() as conn:
        diagnostic = native_partition_diagnostic(conn, target)
        for candidate in CANDIDATES:
            create_candidate(conn, target, candidate, fixtures=64)
        t0 = datetime(2023, 1, 1, tzinfo=UTC)
        t1 = datetime(2024, 4, 1, tzinfo=UTC)
        latest = datetime(2025, 12, 1, tzinfo=UTC)
        events = [payload(1, t0), payload(2, t0), payload(2, t1, partial=True),
                  payload(3, t1), payload(3, t1, goals=2), payload(4, t1), payload(4, t1),
                  payload(5, latest), payload(6, t1), payload(7, latest, goals=2),
                  payload(7, t0+timedelta(days=1)), payload(8, t1, source="bootstrap", provider=None)]
        ids = list(range(1, 10)) + [999999]
        cutoffs = [t0-timedelta(microseconds=1), t0, t1-timedelta(microseconds=1), t1, latest]
        candidates = []
        references = {}
        for candidate in CANDIDATES:
            for event in events:
                with conn.transaction():
                    append_batch(conn, target, candidate, [event], update_heads=True)
            shared_response = uuid.UUID(int=1213)
            with conn.transaction():
                shared = append_batch(conn, target, candidate,
                    [payload(12, t1, evidence_id=shared_response), payload(13, t1, evidence_id=shared_response, goals=2)],
                    update_heads=True)
            assert shared == {"inserted": 2, "replayed": 0}
            receipt_times = conn.execute(sql.SQL("SELECT count(*),count(DISTINCT observed_at) FROM {} WHERE evidence_id=%s")
                                         .format(relation(candidate.schema, "fixture_observations")), (shared_response,)).fetchone()
            assert receipt_times == (2, 1)
            before_invalid_batch = fingerprint(conn, candidate)
            try:
                with conn.transaction():
                    append_batch(conn, target, candidate,
                        [payload(14, t1, evidence_id=shared_response), payload(15, latest, evidence_id=shared_response)])
            except ValueError:
                pass
            else:
                raise AssertionError("One response UUID accepted contradictory receipt timestamps")
            assert fingerprint(conn, candidate) == before_invalid_batch
            before = fingerprint(conn, candidate)
            with conn.transaction():
                replay = append_batch(conn, target, candidate, [events[0]], update_heads=True)
            assert replay == {"inserted": 0, "replayed": 1}
            assert before == fingerprint(conn, candidate), "Replay changed original audit evidence"
            rejected = []
            for field, value in (("observed_at", latest), ("home_goals", 7), ("source", "backfill"),
                                 ("provider", "5dollarfootballapi")):
                try:
                    with conn.transaction():
                        append_batch(conn, target, candidate, [{**events[0], field: value}], update_heads=True)
                except IdentityConflict:
                    rejected.append(field)
                else:
                    raise AssertionError(f"Inconsistent replay {field} accepted")
                assert fingerprint(conn, candidate) == before
            try:
                with conn.transaction():
                    append_batch(conn, target, candidate, [payload(9, latest)], update_heads=True)
                    raise IdentityConflict("deliberate rollback after evidence and heads")
            except IdentityConflict:
                pass
            assert fingerprint(conn, candidate) == before
            assert conn.execute(sql.SQL("SELECT last_observed_at FROM {} WHERE id=9")
                                .format(relation(candidate.schema, "fixtures"))).fetchone()[0] is None
            # Restrict both fixture and provider deletion, including historical references.
            restrict = []
            for table, key, value in (("fixtures", "id", 6), ("providers", "code", "api-football")):
                try:
                    with conn.transaction():
                        conn.execute(sql.SQL("DELETE FROM {} WHERE {}=%s").format(
                            relation(candidate.schema, table), sql.Identifier(key)), (value,))
                except Exception as exc:
                    if getattr(exc, "sqlstate", None) not in ("23503", "23001"):
                        raise
                    restrict.append(table)
                else:
                    raise AssertionError("DELETE RESTRICT weakened")
            states = {}
            for cutoff in cutoffs:
                raw = conn.execute(strict_sql(candidate), (ids, cutoff)).fetchall()
                classified = classify(raw, ids)
                assert production_read(target, candidate, ids, cutoff) == classified
                states[str(cutoff)] = normalized(classified)
            final = states[str(latest)]
            assert final[2]["state"]["fulltime_home"] is None, "AS_OBSERVED partial state fused"
            assert final[3]["status"] == "TEMPORAL_AMBIGUITY" and final[3]["state"] is None
            assert final[4]["status"] == "KNOWN" and len(final[4]["observations"]) == 2
            assert final[999999]["status"] == "UNKNOWN_AT_T"
            if not references:
                references = states
            else:
                assert states == references, "STRICT_KNOWLEDGE control parity failed"
            transition = lifecycle(conn, target, candidate)
            assert fingerprint(conn, candidate) == before, "Lifecycle changed/missed historical rows"
            sealed = fingerprint(conn, candidate, base_only=True) if candidate.lifecycle != "none" else None
            late = payload(6, t1+timedelta(days=1), evidence_id=uuid.UUID(int=6001), goals=3)
            with conn.transaction():
                append_batch(conn, target, candidate, [late], update_heads=True)
            late_result = classify(conn.execute(strict_sql(candidate), ([6], t1+timedelta(days=2))).fetchall(), [6])
            assert late_result[6]["state"]["home_goals"] == 3
            if sealed:
                assert sealed == fingerprint(conn, candidate, base_only=True), "Sealed historical payload mutated"
                assert conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE evidence_id=%s")
                                    .format(relation(candidate.schema, "late_evidence")), (late["evidence_id"],)).fetchone()[0] == 1
            assert production_read(target, candidate, [6], t1+timedelta(days=2)) == late_result
            candidates.append({"candidate": candidate.name, "status": "PASS", "replay": replay,
                               "shared_response_uuid_metadata": "PASS",
                               "inconsistent_replay_rejected": rejected, "delete_restrict": restrict,
                               "rollback": "PASS", "production_strict_reader": "PASS",
                               "partial_ambiguity_confirmations_cutoffs": "PASS",
                               "late_historical_evidence": "PASS", "lifecycle": transition})
    for candidate, result in zip(CANDIDATES, candidates):
        result["concurrent_replay"] = concurrent_gate(target, candidate, 10)
        result["concurrent_conflict"] = concurrent_gate(target, candidate, 11, conflict=True)
    return {"status": "PASS", "strict_knowledge_parity": "PASS", "native_partition_diagnostic": diagnostic,
            "candidates": candidates, "observation_deletion_or_compaction": False,
            "audit_fields": "Replay/lifecycle retain exact local audit rows; cross-candidate live recorded_at/id are not equated",
            "scope": "LAB evidence gateway and unchanged production reader; not production writer replacement"}
