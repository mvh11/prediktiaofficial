# Fixture evidence storage: final 1M audit and recommendation

## Decision

**A — KEEP_UNPARTITIONED.** Eight eligible candidates completed and passed the
audit. Preserve existing evidence identity and historical semantics. At this
tested scale, partitioning adds registry/storage/WAL and operational costs without
a compensating STRICT_KNOWLEDGE benefit. This is a LAB recommendation for Chief
review, not authorization to change production, merge or adopt an index.

Owner: `OPENCODE_AGENT / EVIDENCE STORAGE LAB`; worktree:
`C:\Users\estef\OneDrive\Escritorio\prediktia-evidence-storage`; branch:
`lab/data-integrity-evidence-storage`. Original baseline:
`1c06ef9c2a78a556cd8c7ded89853c19ec9a856a`; authorized recovery checkpoint:
`3f5b1c5000596055bbaaf80c19a5af68e0b9a657`.

## Evidence, recovery and execution epochs

- Original epoch: **2026-10-08**, control, registry control, monthly, quarterly,
  archive. These five measurements were not rerun. Their database evidence and
  fixture-head/registry snapshots remained unchanged through recovery.
- Recovery epoch: **2026-10-09T05:02:36.446908+00:00**, hot/cold, BRIN, covering,
  in that order, once each. This is the shared recovery-start label, not a
  fabricated per-candidate completion timestamp.
- Chief's **Option B recovery strategy** is distinct from architecture option B.
  Before rebuilding hot/cold, its 1M identity/payload counts, complete relation/
  index/constraint/partition inventory, binary checksum, lifecycle state and known
  maintenance timestamps were preserved. Partial timings were explicitly marked
  incomplete. The partial footprint was **528,007,168 bytes**; cold/hot/overlay
  counts were **692,160 / 307,840 / 0**. No external dependents were found. Only
  that isolated schema was removed, transactionally with **RESTRICT**, never
  CASCADE. Parent-table drops include their own schema-local leaves/indexes.
- `missing_only.py` called existing DDL/load/index/maintenance/lifecycle/read/write
  helpers unchanged. Same 1M deterministic source, batches, cutoffs, plan modes,
  eight read warm-ups, three write warm-ups and 15 read/write samples; no source
  regeneration or retry. No two candidate benchmarks executed concurrently.
- Original journal SHA-256 remains
  `68f56d59bb1ef84ce4a6bac0fe7c4aa48dcff85b0f2f19c2299cad90006d1f48`.
  Original `v2/1m-bench.json` remains absent. Original whole-campaign runtime and
  WAL totals are **unavailable**, not reconstructed or merged across epochs.
- Source fingerprint remains
  `9b0138593262db4526039ad5c5ec421cf5fac64bcbe9dec8f598faaec99759c3`:
  original column projection, ordered by ID, PostgreSQL binary COPY. Exact seed
  audit rows in every candidate retain ID, receipt, recorded_at, hash and partial
  columns. Independent final read-only verification passed after execution.
- Owned disposable server: `127.0.0.1:55449`, database
  `prediktia_lab_evidence_storage_1m`, system ID `7694135067275712620`.
  Identity and **fsync/synchronous_commit/full_page_writes ON** verified. No
  DATABASE_URL fallback, Neon or provider calls. No other worktree was used.
- Recovery admission preserved the conservative **3.75 GiB** additional-space
  allowance plus **5 GiB** reserve. Lowest recorded candidate-boundary free space
  during recovery: **104.802 GiB**. These snapshots are not continuous peak-disk
  telemetry. Candidate and space-heavy-stage gates all passed.

Retained external evidence root:
`C:\Users\estef\AppData\Local\Temp\opencode\evidence-storage-20261008-r2`.
Recovery namespace: `option-b-recovery-20261009-r1`.
The original [manifest](artifact-manifest.json) and frozen
[checkpoint](CHECKPOINT.md) are preserved; the new
[recovery manifest](recovery-manifest.json) pins forensic metadata, immutable
database snapshots, the separate recovery journal, aggregate and output checksums.
Raw evidence is outside Git: these local paths are **not a durable shared backup**.

## Correctness and auditability

**PASS: 8 / 8**, 1,000,000 seed observations and **1,050,077 final observations**
per candidate. Each record has 160 matched read cases, one late-arrival read,
eight write cases and post-write FREEZE/ANALYZE. Offline audit recomputed
percentiles from raw samples and plans from full EXPLAIN; checked case coverage,
counts, canonical hashes, full-row parity and unchanged original/recovery records.

The original small PostgreSQL gate for all eight candidates proved logical
`(fixture_id, evidence_id)` uniqueness, exact replay, inconsistent replay rejection
including time-range changes, independent unchanged confirmations, immutable
audit fields, inclusive observed-time cutoffs, UNKNOWN_AT_T, TEMPORAL_AMBIGUITY,
partial AS_OBSERVED, late historical evidence, transaction/head rollback,
fixture/provider DELETE RESTRICT, concurrency and unchanged production-reader
parity. No hash winner, receipt collapse, persistence-time cutoff or current-state
backfill was introduced. Native-only partitioning was rejected as an ineligible
negative control, not accepted by widening the logical identity key.

At 1M, each candidate's complete pre-write t-star rows matched the same
unpartitioned source at all eight cutoffs, including exact audit fields, providing
transitive parity with the unpartitioned control. Additional final direct
read-only comparisons of **all eight** candidates against the retained control
passed all cutoffs across **10,001 requested IDs**, including the unknown sentinel and late
historical row. Only the live-admission `recorded_at` of the extra late row differs
between epochs; it was not rewritten or equated. All staged seed audit fields were
checked byte-for-byte separately. Offline read classifications in all 160 cases
and the late-read case also match the unpartitioned control for all eight records.
Final database row count, distinct ID count and distinct `(fixture_id,evidence_id)`
count were each **1,050,077** for every candidate.

Post-measurement rollback-only audits of hot/cold, BRIN and covering additionally
passed exact replay/conflicts, independent partial confirmations, tie ambiguity
and DELETE RESTRICT. No extra audit-test evidence was committed; payload checksum
before/after matched. Rollback tests can consume sequence IDs; this is not
evidence deletion or a promise of gap-free IDs.

Archive and hot/cold preserve every historical leaf, use complete historical
views and route late evidence to an append-only overlay. No observation deletion,
compaction or cold-history exclusion occurs. Sealing is a LAB gateway prototype,
not a production permission/security boundary.

## Storage and index cost

Decimal **MB** (1,000,000 bytes); GiB only for disk admission. Evidence total
includes payload heap/TOAST/indexes and, where required, the registry. Registry MB
includes its heap/indexes; payload index MB alone is not the complete index bill.
Totals do not include whole-database catalogs, fixtures/providers or shared WAL.

| Candidate | Epoch | Seed MB | Payload indexes MB | Registry MB | Final MB | Total growth MB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Original | 273.514 | 99.131 | 0 | 288.965 | 15.450 |
| Registry control | Original | 598.712 | 111.362 | 312.967 | 628.621 | 29.909 |
| Monthly | Original | 533.963 | 56.304 | 301.597 | 561.529 | 27.566 |
| Quarterly | Original | 536.535 | 60.908 | 300.761 | 563.003 | 26.468 |
| Archive | Original | 533.078 | 56.246 | 300.761 | 559.784 | 26.706 |
| Hot/cold | Recovery | 497.500 | 55.222 | 266.207 | 524.009 | 26.509 |
| BRIN | Recovery | 273.580 | 99.197 | 0 | 289.047 | 15.466 |
| Covering | Recovery | 467.911 | 293.528 | 0 | 501.801 | 33.890 |

Monthly/quarterly/archive require about **1.95–1.96x** control seed storage;
hot/cold **1.82x**, registry-only control **2.19x**. The registry-only control
exposes that global uniqueness bookkeeping is not free. Allocation/bloat/cache
differences across epochs also affect measured footprints; hot/cold's lower
registry footprint is not evidence that detaching leaves saves registry space.

BRIN adds **65,536 bytes** of seed indexes, without replacing the existing
B-tree. Covering adds **194.396 MB** (seed storage **+71.1%**). Payload index growth
after the matched writes is **6.717 MB** control versus **6.734 MB** BRIN and
**25.158 MB** covering. Registry candidates additionally grow registry storage
by **14.115–15.213 MB**. Retaining all evidence makes growth explicit, not hidden
by truncation or deleting confirmations.

## STRICT_KNOWLEDGE reads and plans

Batch **380**, latest cutoff, p50 end-to-end query/fetch + materialization, ms.
Modes are shown separately, not averaged into a campaign-wide score.

| Candidate | Fresh/unprepared | Prepared auto | Forced custom | Forced generic | Generic p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Control | 78.604 | 17.418 | 74.211 | 18.675 | 25.780 |
| Registry control | 74.374 | 15.093 | 75.655 | 13.435 | 14.101 |
| Monthly | 141.934 | 134.962 | 138.599 | 119.469 | 158.176 |
| Quarterly | 104.720 | 105.134 | 105.532 | 101.545 | 158.072 |
| Archive | 141.314 | 134.525 | 136.956 | 121.634 | 161.628 |
| Hot/cold | 143.844 | 153.643 | 146.507 | 148.638 | 187.477 |
| BRIN | 77.222 | 16.005 | 79.773 | 16.799 | 19.566 |
| Covering | 80.311 | 19.101 | 78.170 | 19.290 | 25.219 |

Monthly, quarterly, archive and hot/cold generic latest/380 medians are
**6.40x / 5.44x / 6.51x / 7.96x** control respectively. Earlier-time pruning does
not remove the dominant historical-retrieval cost. In generic early/380 plans,
monthly/archive/hotcold remove 41 subplans in one part of the query, but another
part still executes nodes for all **42 distinct leaves**; quarterly removes 13
while nodes for all **14 leaves** execute. At latest/380 these become five and one
removed subplans respectively. Removed-subplan totals are not a whole-query
pruning percentage and executed-leaf counts are not row counts. Complete
MAX-then-all-t-star query shape is unchanged; do not weaken it to manufacture wins.

Cold-only/380 generic p50 is **267.577 ms** control, **364.127 ms** hot/cold,
**252.826 ms** BRIN and **233.362 ms** covering. Late-evidence post-insert/380
generic p50 is **20.224 / 123.484 / 17.794 / 18.211 ms** respectively. Index gains
are workload-dependent, not universal; registry control's faster reads do not
establish that the unused registry is a read optimization.

BRIN appears in only **8 / 160** plans: no-eligible-history, fresh/custom,
batches 10/380/1000/10000. It is not selected for the latest generic case. Its
**16.799 vs 18.675 ms** median there cannot be attributed to BRIN use. Covering
is selected in **122 / 160** plans; latest generic/380 has index-only scans with
zero heap fetches, yet **19.290 vs 18.675 ms** is not a compelling win for its cost.
Planning and EXPLAIN execution diagnostics remain separate from sampled timings.

## Writes and WAL

Batch **380**, gateway/hash/COMMIT p50; confirmation also updates ordered fixture
heads. These are not full production-writer timings. WAL bytes/row are median
sample deltas divided by 380, not whole-campaign totals.

| Candidate | Insert ms | Confirmation ms | Insert WAL B/row | Confirmation WAL B/row | Load WAL MB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Control | 27.617 | 31.214 | 556.61 | 785.18 | 479.818 |
| Registry control | 36.840 | 41.712 | 1013.07 | 1241.62 | 1206.946 |
| Monthly | 37.103 | 41.809 | 938.55 | 1174.63 | 1019.857 |
| Quarterly | 36.789 | 41.389 | 949.39 | 1169.60 | 1041.536 |
| Archive | 37.069 | 41.645 | 940.88 | 1175.58 | 1023.125 |
| Hot/cold | 41.401 | 48.588 | 943.24 | 1169.60 | 986.980 |
| BRIN | 28.452 | 33.528 | 537.24 | 785.12 | 498.061 |
| Covering | 26.531 | 33.215 | 760.15 | 975.98 | 517.595 |

Registry-backed candidates add an admission/FK/index path to every new identity;
confirmations remain independent evidence, not deduplicated states. Covering's
slightly faster insert median in this epoch does not erase its extra WAL/index
growth or justify adoption. LSN deltas include background cluster activity;
autovacuum and durability remain ON. Load WAL precedes supplemental index build
and is **not** total setup WAL. No synthetic original campaign WAL total is given.

## Maintenance, lifecycle, bootstrap and recovery

| Candidate | Initial VACUUM/ANALYZE s | Post-write FREEZE/ANALYZE s |
| --- | ---: | ---: |
| Control | 0.258 | 1.017 |
| Registry control | 0.598 | 0.644 |
| Monthly | 7.628 | 5.868 |
| Quarterly | 1.957 | 2.293 |
| Archive | 7.644 | 4.707 |
| Hot/cold | 5.862 | 7.646 |
| BRIN | 0.294 | 2.112 |
| Covering | 0.321 | 0.482 |

Archive's transition/freeze: **0.059 / 1.842 s**; hot/cold: **0.317 / 2.573 s**.
Both retain 24 historical leaves and require writer quiescence. Hot/cold adds
atomic detach/attach, a second parent and UNION routing; archive retains attached
leaves plus overlay. Both need manifests, complete reader dependencies and late
admission discipline. Small next-partition provisioning is **0.008 s** archive,
**0.019 s** hot/cold; this is not production lock-contention/DDL recovery evidence.
BRIN and covering builds take **0.102 s** and **3.091 s**. Parent statistics and
leaf maintenance require explicit attention; more objects are not a benefit.

The unchanged real bootstrap on 10,000 reference fixtures took **1.782 s**,
generated **12.988 MB WAL**, and created 10,000 bootstrap observations at migration
head **0008**, with no pre-bootstrap knowledge. This common bootstrap is separate
from deterministic source generation and per-candidate historical load.

The original **small** logical backup/restore gate passed exact audit signatures:
**2,145,296-byte** dump, **0.496 s** dump, **2.236 s** transactional restore into
a new owned database. Registry, payloads, overlays, manifests, views, constraints
and hash function must restore together. Large restore, physical crash/PITR,
interrupted detach/attach, real tiered-disk latency and production failover remain
**unmeasured**. Completing missing measurements is not proof of those properties.

## Why A, not B/C/D

- **A:** lowest demonstrated base evidence footprint and simpler global identity,
  recovery and operations; no demonstrated need to partition before production
  scale at the tested 1M history. Preserve the current semantics and indexes.
- **Not B:** partitioned payload index savings are outweighed by the identity
  registry and slower representative strict reads, higher write/WAL costs and
  maintenance/dependency complexity. Native partitioning alone cannot preserve
  the original global pair uniqueness. Do not reward added complexity.
- **Not C:** archival lifecycle preserved semantics but demonstrated neither
  storage reduction nor real cold-tier benefit here. An unpartitioned archival
  operational policy was not separately benchmarked; that is not grounds to
  adopt it. Future retention work must preserve complete historical queryability.
- **Not D:** all eight 1M candidates and relevant semantic checks are now complete;
  the evidence suffices for keeping the present architecture at this scale.
  Remaining larger-scale/production questions limit confidence, not the right to
  retain the current simpler design.

Reconsider only after a measured production bottleneck or materially different
history/cardinality, real archival requirements, or a separately authorized larger
campaign. More millions could change crossover points: this report does **not**
prove unpartitioned storage is best at every future scale. BRIN's tiny cost may
merit a future targeted experiment, but no production/index change is authorized.

## Validation, limitations and stop state

Final lightweight tests: **128 passed, 1 skipped** (existing database-only guard).
`offline_audit.py` verified all **13 original + 5 recovery** artifact references,
journal-to-aggregate equality, separate epochs, source provenance, control
classifications, eight-candidate coverage and partial-state forensic checks.
All four numeric report tables were independently checked against raw records.
One supplemental read-only audit hit its shell's 120-second limit after printing
all eight identity/control-parity passes, while doing its trailing immutable
snapshot check. That snapshot check was then completed separately with a longer
shell limit and passed. No candidate execution failed, repeated or changed.
`git diff --check` passed before the scoped final commit. The frozen original
checkpoint and its manifest were not rewritten to hide their incomplete epoch.

Limitations: deliberately timestamp/tie-fan-out-heavy 10,000-fixture dataset;
local warm cache; same physical disk for hot/cold; sequential, single-client
benchmark driver; separate execution epochs without statistical significance claims;
15 samples give weak rare-tail/p99 evidence; gateway rather than full application
writer; only PostgreSQL 18.6; concurrency and logical restore proved on the small
gate, not a production-scale workload. Batches 1000/10000 are diagnostics, not
relaxation of the production <=380 contract. Audit evidence is retained, not
compacted or concealed, and no source/production files were modified.
Auxiliary read-only checksum inspections were issued after recovery launch;
their exact overlap with recovery stages was not timestamped. Admission required
quiescence, but uninterrupted exclusive-client I/O throughout recovery is not
claimed. Such inspection may perturb cache/I/O and further limits small
cross-epoch performance differences; it does not change preserved rows or helpers.

**STOP / WAIT FOR CHIEF REVIEW.** No migration (including 0009), production
schema/application change, A5D/A6 semantic change, canonical DI edit, other-lane
change, multi-million execution, merge or production adoption. No push authorized.
After the final scoped commit, both execution guards intentionally refuse the
new HEAD; do not relax them or silently rerun. The retained local server/cluster
and external evidence are not deleted by this persistence step.
