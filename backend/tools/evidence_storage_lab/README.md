# Fixture evidence storage laboratory (Phase 2)

Owned by **OPENCODE_AGENT / EVIDENCE STORAGE LAB**, baseline
`1c06ef9c2a78a556cd8c7ded89853c19ec9a856a`, branch
`lab/data-integrity-evidence-storage`. Nothing here is an Alembic migration,
production policy, or authorization to deploy. Do not edit application code,
existing migrations, 0009, A5D, canonical DI documentation or another worktree.

## Current checkpoint: STOP / incomplete

**PHASE2_INCOMPLETE_CHECKPOINT**: the corrected 100k matrix completed; the 1M
campaign terminated with five of eight candidates validated. Hot/cold is partial;
BRIN and covering did not start. The termination cause is **UNVERIFIED**.
Completed comparisons favor unpartitioned storage, but final architecture
classification remains **PENDING**. This is not a deployment recommendation.

See [CHECKPOINT.md](CHECKPOINT.md) for the accepted state and recovery decision,
and [artifact-manifest.json](artifact-manifest.json) for external evidence paths,
sizes and SHA-256 checksums. Large raw evidence and the PostgreSQL cluster remain
outside Git. They are local disposable storage, not a durable shared backup.

Chief decision: **Option D — stop current execution**. No benchmarks, recovery
runner, candidate recreation, partial-state deletion or multi-million work are
authorized. Option B is preferred later only with explicit authorization and a
fresh conservative admission check requiring **at least 8.75 GiB free** while
preserving **5 GiB**. Observed design-time free space was approximately 7.79 GiB.

The checkpoint commit preserves the original baseline-pinned safety code.
`assert_workspace()` deliberately refuses database commands when HEAD differs
from the authorized baseline. Do not relax that guard to run this committed
snapshot. Measurement provenance remains in the original reports; checkpoint
documentation additions are not changes to the measured methodology.

## Historical reproduction recipe (not execution authorization)

The following recipe documents the original fresh-campaign workflow. Do not run
it to resume this incomplete campaign or rerun the five completed candidates.

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/tools/evidence_storage_lab/requirements.txt
$run = "$env:LOCALAPPDATA/Temp/opencode/evidence-storage-<new-run-id>"
backend/tools/evidence_storage_lab/setup.ps1 -RunRoot $run
backend/tools/evidence_storage_lab/run.ps1 -RunRoot $run -Database gate -Command init
backend/tools/evidence_storage_lab/run.ps1 -RunRoot $run -Database gate -Command gate
backend/tools/evidence_storage_lab/run.ps1 -RunRoot $run -Database 100k -Command init
backend/tools/evidence_storage_lab/run.ps1 -RunRoot $run -Database 100k -Command bench
# Review 100k resources, correctness and plans before admitting 1M.
backend/tools/evidence_storage_lab/run.ps1 -RunRoot $run -Database 1m -Command init
backend/tools/evidence_storage_lab/run.ps1 -RunRoot $run -Database 1m -Command bench
```

Small tests (backend working directory):
```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_evidence_storage_lab.py
```

Every setup uses a NEW directory; no cluster or evidence is overwritten. Trust
authentication is only on the newly created loopback-only disposable server.
The owner manifest and `pg_control_system()` system ID/data directory, effective
host/port/database, exact authorization and durability settings are checked.
No DATABASE_URL fallback, PG* overrides, Neon or provider calls. Existing
reference-fixture generator and migrations 0007 then 0008 are reused unchanged.
The real bootstrap is timed on 10,000 reference fixtures, separately from the
synthetic historical observation datasets. Synthetic timestamps are not a claim
of pre-bootstrap production knowledge. Full package versions are in each report.

Sources must match the passing gate and remain unchanged throughout a benchmark.
Reports use exclusive creation; candidate progress is retained as JSONL outside
the repository. Failed runs require NEW databases, not TRUNCATE/reseed. Stop the
owned instance when finished with `pg_ctl -D <run>/cluster -m fast -w stop`.

## Candidates and identity

- Current unpartitioned columns/CHECKs/PK/unique identity/temporal B-tree control.
- Unpartitioned + registry isolates registry overhead.
- Monthly / quarterly RANGE payloads + global identity registry.
- Archive: historical leaves remain attached/queryable, are frozen; late rows
  append to an overlay. A complete UNION ALL view participates in strict reads.
- Hot/cold: atomically detach old leaves from the hot parent and attach them to a
  cold parent. A complete hot/cold/late UNION ALL view preserves queryability.
- Supplemental BRIN(observed_at), 32 pages/range, and a full-projection covering
  temporal B-tree; original indexes remain present to expose incremental cost.

**Native-only monthly/quarterly range partitioning is an ineligible negative
control.** PostgreSQL refuses the original global UNIQUE(fixture_id,evidence_id).
The lab records that refusal and loads no rows into those parents. It never
widens the logical key to include observed_at. Eligible partitioned candidates
admit each identity through the registry, compare replay metadata/hash, insert
payload only for new admissions, and commit both together. Payload FKs bind the
registry ID, fixture, evidence UUID and observed timestamp. A global primary ID
and original pair uniqueness are retained in the registry; the payload PK must
include the native time key. Registry heap/index/WAL is counted, not hidden.

The gateway is append-only; no observation UPDATE/DELETE/compaction occurs.
Sealing/routing is an **application-gateway prototype**, not a DB permission or
production security boundary. Archive transitions require writer quiescence.
Late old evidence goes to the overlay; exact replay compares the global registry
and inserts nothing. Full row parity proves transitions do not lose audit rows.
Failure of any cold dependency must surface as a database error, not UNKNOWN.

## Correctness and recovery

Before large loads the explicit gate proves replay, inconsistent replay across
time ranges, independent confirmations, inclusive cutoffs, canonical hashes,
ties/ambiguity, partial evidence, UNKNOWN, late history, transaction/head rollback,
fixture/provider RESTRICT and unchanged historical rows. Two real connections
demonstrate actual lock waits for concurrent replay and conflict. The unchanged
production `strict_knowledge()` runs against each LAB relation via search_path.
A small custom-format logical backup is restored transactionally into a NEW
owned DB; all evidence audit signatures must match. Large restore, physical
crash/PITR recovery and interruption during partition DDL are not claimed.

Both scales use 10,000 fixtures: 10 or 100 observations each over 36 months,
380 cold-only fixtures, independent confirmation-heavy observations, delayed
recorded_at audit metadata, and 10% ambiguity at the latest common timestamp. Response UUIDs
are grouped by receipt instant as well as synthetic response batch; a SQL gate
rejects any UUID with contradictory observed_at/source/provider metadata across
fixtures. Equal timestamps are intentionally clustered (multiple unchanged rows
can coexist at t-star); returned-row fan-out is a stress condition, not a claim
about normal hourly polling. This exposes deeper fixture histories rather than
merely growing fixture identities. Source
row IDs/recorded_at are copied exactly; full EXCEPT ALL parity and canonical hash
checks run before and after lifecycle transitions. Native negative controls are
not eligible alternatives and do not count as a correctness gate failure.

## Measurement and limitations

Strict query shape is unchanged: per-fixture MAX(observed_at <= T), then all rows
at t-star. Batch 1/10/380/1000/10000; early/median/latest/boundary/ambiguous/unknown/
cold-only/late-old cutoffs. Large batches are diagnostics, not a change to the
production <=380 policy. Actual production result types are materialized.
The timed driver is psycopg rather than SQLAlchemy Session; the small gate proves
the production repository returns the same complete results. Query/fetch and
type construction are reported separately.

Fresh/unprepared means a new connection per case with preparation disabled.
Prepared-auto and forced-custom/generic retain connections through the matrix.
Eight warm-ups preserve first-use transitions; 15 timed samples by default, with
raw observations and p50/p95/p99. p99 from this sample count is near the maximum,
not a reliable rare-tail estimate. EXPLAIN EXECUTE uses the actual prepared
statement when present, capturing planning/execution, BUFFERS, pruning and
custom/generic counters. Explain is a separate diagnostic execution.

Writes measure the canonical hash + evidence gateway + COMMIT; confirmations
also lock/update fixture-head metadata in order. They **do not** measure the full
production option-A writer, score fusion, mappings, providers or service layer.
The unchanged cohort excludes ambiguous fixtures. Preparation load throughput
is explicitly separate. WAL LSN deltas include background server activity;
autovacuum and all durability settings remain ON. Seed/write storage snapshots
expose payload/registry/index growth and maintenance cost. All candidates retain
their history; measured writes grow it rather than deleting to reset cardinality.

Index experiments are motivated by recovered A6 full-history custom scans and
large projected-row fetches. BRIN has favorable time correlation in the staged
load; its late-arrival sensitivity and actual plan use must be inspected. The
covering index targets heap fetches after VACUUM visibility, at explicit extra
storage/WAL cost. Neither is presumed to fix cardinality estimation or win.

Parent partition statistics need explicit ANALYZE; leaves are VACUUM/ANALYZE'd,
historical leaves FREEZE'd, and visibility/frozen pages are recorded. DDL timing
includes initial provisioning and a new empty next partition. Same physical
disk means **no real cold-tier latency claim**. These are local warm-cache,
single-client storage measurements on PostgreSQL 18.6, not Neon/WAN capacity.

The CLI admits only 100k or 1M. Before each candidate it requires 5 GiB disk
reserve plus a conservative candidate budget and 2 GiB available memory. It
records final disk, DB, WAL, memory and runtime. **No multi-million run is
implemented or automatically authorized.** A Chief checkpoint is mandatory.
