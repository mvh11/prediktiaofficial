# Evidence storage laboratory: accepted Phase 2 checkpoint

## Frozen decision

```text
AGENT_ID: OPENCODE_AGENT
OWNERSHIP: OPENCODE_AGENT / EVIDENCE STORAGE LAB
STATUS: PHASE2_INCOMPLETE_CHECKPOINT
BASELINE: 1c06ef9c2a78a556cd8c7ded89853c19ec9a856a
AUTHORIZED_BRANCH: lab/data-integrity-evidence-storage
COMPLETED_1M: 5 / 8
  control
  registry control
  monthly
  quarterly
  archive
INCOMPLETE: hot/cold
NOT_STARTED: BRIN, covering
CORRECTNESS_GATE: PASS
STRICT_KNOWLEDGE_PARITY: PASS for completed candidates
TERMINATION_CAUSE: UNVERIFIED
FINAL_ARCHITECTURE_CLASSIFICATION: PENDING
CURRENT_ARCHITECTURE_SIGNAL: completed comparisons favor unpartitioned storage;
  final A/B/C/D classification remains PENDING because three candidates are incomplete
CURRENT_DECISION: Option D — STOP CURRENT EXECUTION
RECOVERY: Option B preferred if later provisioned and explicitly authorized
DISK_GATE: conservative free-space budget >= 8.75 GiB before reconsideration
DESIGN_TIME_DISK_FREE: approximately 7.79 GiB (historical observation, not live admission)
REQUIRED_SAFETY_RESERVE: >= 5 GiB
SAFE_TO_COMPLETE_1M: NO
MULTI_MILLION: NOT AUTHORIZED; NOT EXECUTED
PRODUCTION_SCHEMA_CHANGED: NO
MIGRATION_CREATED: NO
CROSS_LANE_CONFLICT: NONE
PUSH: NOT AUTHORIZED
MERGE: NOT AUTHORIZED
NEXT_STATUS: WAITING_FOR_DISK_HEADROOM_OR_CHIEF_DECISION
```

No final architecture recommendation is made. The five completed 1M candidates
must not be rerun. Do not resume, drop partial state, recreate candidates or
implement a recovery runner without a new explicit authorization.

## Scope and environment

Only `backend/tools/evidence_storage_lab/` and
`backend/tests/test_evidence_storage_lab.py` belong to this checkpoint. Production
application files, existing migrations through 0008, reserved migration 0009,
A5D, canonical DI documentation and other worktrees remain untouched.

Measured runtime: Python 3.13.4, SQLAlchemy 2.1.3, psycopg 3.3.6, pytest 9.0.2,
Alembic 1.20.0 and PostgreSQL 18.6. Full package lists are in the external reports.
The owned PostgreSQL cluster used loopback `127.0.0.1:55449`, system identifier
`7694135067275712620`, and fsync/synchronous_commit/full_page_writes ON. No Neon or
provider calls were made. Checkpoint persistence does not start or stop the server.

The source dataset was 10,000 fixtures with 10 or 100 observations each. Equal
receipt timestamps intentionally create confirmation/tie fan-out. Results are
same-disk local measurements, not real cold-tier latency or full application
writer throughput. Preserve these qualifications when reusing the evidence.

## Retained evidence and provenance

External run root:

```text
C:\Users\estef\AppData\Local\Temp\opencode\evidence-storage-20261008-r2
```

[artifact-manifest.json](artifact-manifest.json) records retained artifact sizes
and SHA-256 checksums. Paths in that manifest are relative to this exact root.
No raw reports, journals, backup dumps, cluster files or unrelated logs are
committed. The external artifacts are reusable only while their hashes and
measurement provenance continue to match; Git contains no replacement backup.

- `gate3-gate.json`: passing corrected small PostgreSQL correctness/concurrency
  gate for all eight eligible candidates, including unchanged production-reader
  parity and small logical backup/restore. This is **not** eight completed 1M runs.
- `post-gate-checks-v2.json`: supplemental cold-history/head/RESTRICT checks.
- `v2/100k2-bench.json` and `v2/progress-100000.jsonl`: corrected complete 100k
  matrix. The first `100k-bench.json` is excluded, not deleted: its synthetic UUID
  grouping conflicted with receipt metadata. See `excluded-first-100k.json`.
- `1m-init.json` and `v2/progress-1000000.jsonl`: original 1M initialization and
  five complete candidate records. **`v2/1m-bench.json` does not exist.**

The 1M journal SHA-256 is
`68f56d59bb1ef84ce4a6bac0fe7c4aa48dcff85b0f2f19c2299cad90006d1f48`.
Each completed record contains 160 read cases, a separate late-arrival read,
eight write cases, storage/parity results and maintenance measurements. Offline
checks validated samples, percentiles, plans, counts and provenance. All five
records are internally complete; original whole-campaign final totals are not
available and must not be fabricated from recovery measurements.

Measurement-time source hashes are retained in each original report's
`source_sha256`; they match the corrected gate/initialization provenance. This
checkpoint adds documentation and modifies README only; the original Python,
PowerShell, dependency recipe and test methodology are not changed. README's
current hash is consequently not its measurement-time hash. The unchanged
baseline guard will fail closed after the checkpoint commit; that is intentional.

## Termination and exact partial hot/cold state

The last completed candidate is archive. Journal last-write time is
`2026-10-08T05:46:40.499385Z`; individual records have no completion timestamps.
Windows logs show suspension/wake at approximately 05:48:03Z / 06:03:45Z. Harness
logs show workspace-service eviction/interruption at 06:03:44Z; PostgreSQL logged
forcibly closed client connections at 06:03:45Z. Python exit status and original
shell output are unavailable. No benchmark traceback, disk-exhaustion or OOM
event was found in the inspected interval. Shared-memory reservation warnings do
not establish OOM. These correlated events **do not verify a termination cause**.

At read-only diagnosis the owned PostgreSQL instance was healthy and continuously
running since `2026-10-08T03:33:44Z`. Source row/identity/hash/receipt audits passed.
Source and partial hot/cold checksum:

```text
9b0138593262db4526039ad5c5ec421cf5fac64bcbe9dec8f598faaec99759c3
```

This hashes PostgreSQL 18.6 binary COPY output of the original `COLUMNS` projection
in `candidates.py`, ordered by `id`, from the complete 1M source and hot/cold view.
It is a content fingerprint, separate from code-file hashes; it is not an
authorization to copy, regenerate or mutate the dataset.

Schema `lab_hotcold` contains two partitioned parents (`history`, `cold_history`),
42 leaves (`p_000`–`p_041`), five ordinary supporting tables (`fixtures`,
`providers`, `identity_registry`, `late_evidence`, `archive_manifest`), the complete
UNION ALL `fixture_observations` view and `identity_registry_id_seq`. There are
93 physical indexes and four parent indexes. Every leaf retains its time-key PK
and fixture/time B-tree; the registry retains global identity constraints.

Registry rows: 1,000,000. Cold rows: 692,160 in 24 leaves. Hot rows: 307,840 in
18 leaves. Overlay rows: zero. Lifecycle committed at
`2026-10-08T05:47:29.969258Z`; all 24 cold leaves are fully frozen, with final manual
ANALYZE at `05:47:31.938867Z`. Initial DDL/load/maintenance and historical lifecycle
completed, but their elapsed timings were not persisted. Subsequent read progress
is unknown; late insertion, write passes and `p_042` provisioning were not reached.
There are **no retained valid 1M hot/cold timing samples**. BRIN/covering schemas
were not created. Allocated partial-schema relations, indexes, TOAST and sequence
total **528,007,168 bytes (0.491745 GiB)**, excluding shared catalog/WAL overhead.

Dependency inspection found no external dependent objects; the view's rewrite
rule belongs to this same schema. A later isolated cleanup is technically
possible, but no drop is authorized now. Do not apply unrestricted CASCADE.

## Accepted recovery design — not implemented

Option A reuses state but leaves setup/lifecycle timings unavailable. Option B
preserves forensic metadata and, **only if explicitly authorized later**, removes
only isolated disposable hot/cold state, recreates it with the unchanged helpers,
then measures BRIN and covering. It is the preferred complete-method recovery.
Option C retains partial state beside replacement state, at higher disk cost.
Option D stops with current evidence and is the accepted present decision.

No existing CLI selector/resume coordinator supports missing-only completion.
A future authorized wrapper would accept only hot/cold, BRIN and covering;
refuse the five completed candidates and overwrites; verify exact target identity,
source checksum and original code/artifact provenance; use a separate recovery
journal/output namespace; execute unchanged stages sequentially; and stop before
any projected free-space violation. It would preserve the original journal and
permit offline aggregation of five original plus three recovery records, with
separate execution epochs and unavailable original campaign totals explicit.

Disk model, in GiB, based on existing 100k storage/growth, completed 1M load WAL,
artifact sizes and an unexecuted parity plan containing four million-row sorts:

| Additional-space allowance | Expected B | Conservative B |
| --- | ---: | ---: |
| Net relations/write growth | 0.75 | 1.00 |
| Additional WAL | 0.25 | 1.125 |
| Temporary workspace | 0.35 | 1.00 |
| Journals/outputs/aggregation | 0.25 | 0.50 |
| Catalog/metadata margin | 0.05 | 0.125 |
| **Peak additional** | **1.65** | **3.75** |

At 7.79 GiB observed free: expected minimum 6.14 GiB; conservative minimum
**4.04 GiB**, below the required 5 GiB reserve. A has similar disk growth but
incomplete timing coverage. C adds approximately 0.49 GiB: conservative minimum
approximately **3.55 GiB**. B credits reclaimed space only after verified
reclamation. These are planning allowances, not guaranteed peak bounds; other
disk users and fresh resource admission must be checked later. Existing WAL
occupies 1 GiB and its configured maximum is not a hard physical cap.

Consequently **SAFE_TO_COMPLETE_1M: NO**. B reconsideration requires at least
**8.75 GiB free**, sufficient memory, and explicit Chief authorization. No
multi-million phase is authorized or proposed.

## Lightweight validation

From `backend/`, the existing no-connection tests can be run with:

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_evidence_storage_lab.py tests/test_perf_lab.py tests/test_conftest_guard.py
```

The database-only guard test may skip without its explicit test target. Do not
enable database fixtures, launch lab CLI init/gate/bench, generate data or rerun
benchmarks to validate this checkpoint. Offline artifact validation uses existing
`distribution()` and `plan_summary()` against retained JSON/JSONL; it requires no
database connection. Run `git diff --check` before the scoped checkpoint commit.

Checkpoint validation completed: **108 passed, 1 skipped** (the existing
database-only guard test). All 13 manifested external artifacts matched their
sizes/checksums. Offline checks passed for eight corrected 100k records and five
completed 1M records, including case coverage, samples, percentile recomputation,
plan summaries, counts, parity and post-write maintenance. Original measured
source files matched gate provenance except the documented README update. No
database connection, dataset generation or benchmark execution was used for
these checkpoint validations.
