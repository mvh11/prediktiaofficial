# PREDIKTIA — Data Integrity Coordination

> Tablero operativo compartido entre máquinas/agentes.
> No sustituye `docs/data-integrity-status.md`.

## Operating rules

- Leer este tablero antes de asignar o comenzar un bloque.
- No duplicar un bloque ACTIVE.
- Claude y OpenCode son implementadores paralelos.
- Un agente puede implementar código, medir, auditar y probar.
- No modificar archivos reservados por otro bloque ACTIVE sin decisión explícita.
- Los resultados permanentes se consolidan posteriormente en `docs/data-integrity-status.md`.
- No pegar logs extensos aquí.
- Si existe conflicto de ownership/arquitectura: `DECISION_NEEDED`.
- PostgreSQL protege invariants; Python protege decisiones; auditoría global fuera del hot path cuando sea posible.
- Medir antes de optimizar.

## Provider constraints

API-Football:
- 7,500 requests/day known limit.

5Dollar:
- 10 requests/minute known limit.

`HTTP attempts` != confirmed provider quota consumption.

## ACTIVE

### DI-A2 — Operational Hardening & Measurement Baseline

Owner: Claude / other machine

Status: RECOVERY_PENDING

Last known validation:

`512 passed, 0 skipped`

Known implementation:

- DB connect timeout
- DB statement timeout
- typed invalid-fixture payload handling
- request attempt/retry metrics
- duration per competition/sync
- RequestStats

Remaining known task:

`attempts` must mean HTTP attempts made by Prediktia, not confirmed provider quota consumption.

Important:

Do NOT recreate DI-A2 until its original working copy is recovered.

### DI-A3A — Provider Usage, Budget & Freshness Audit

Owner: OpenCode

Status: ACTIVE

Scope:

- API-Football / 5Dollar request inventory
- request amplification
- retries
- freshness classes
- avoidable requests
- conceptual provider-budget policy

## READY

### DI-A4A — HTTP Client Lifecycle & Connection Reuse

Preferred owner: Claude

Dependency: DI-A2 recovered and committed.

Goal:

reuse AsyncClient per provider/run safely.

### DI-A3B — Local Performance Laboratory

Preferred owner: OpenCode

Dependency: DI-A3A reviewed.

Goal:

PostgreSQL-local p50/p95/p99 benchmarks and query plans before index optimization.

## QUEUE

### Provider Budget & Freshness Policy

Inputs:

- API-Football 7,500/day
- 5Dollar 10/min
- proximity to kickoff
- live/non-live
- retries
- reserve capacity
- outage/catch-up

### Multi-provider Identity

Goal:

API-Football / 5Dollar → canonical Prediktia identity.

Never fuzzy auto-map ambiguous fixtures.

### Recovery / Resume / Catch-up

Goal:

detect missing windows, resume safely and recover provider outages without wasting request budget.

### Temporal Integrity

Goal:

answer:

`What did Prediktia know about X at timestamp T?`

Includes future odds snapshots and model outputs.

## CROSS-LANE

Modular Data is developing M4.3 separately.

Do not duplicate its implementation.

## DECISIONS

D001 — Prefer the simplest architecture satisfying correctness, speed, resilience and auditability.

D002 — Do not optimize based only on intuition.

D003 — Expensive reconciliation/audits must not become part of the future odds hot path.

D004 — Claude and OpenCode are parallel implementers; neither is permanently secondary.

## HANDOFF

DI-A2: RECOVERY_PENDING on other machine.

DI-A3A: ACTIVE — OpenCode.

DI-A4A: BLOCKED by DI-A2 recovery.

DI-A3B: READY after DI-A3A review.
