# Data Integrity — HANDOFF

> Archivo operativo. Se sobrescribe cuando cambia el estado de la sesión/carril.

## Identidad

- AGENT_ID: `PrimaryIntegrity`
- ROLE: `PRIMARY_IMPLEMENTATION_AGENT — DATA INTEGRITY`
- Estado: `VERIFIED`

## Work package activo

- Paquete: `DI-A5E — persistent recovery-state and provider-incident integration`
- Autorización: `REPORTED` — CHIEF_ARCHITECT, DEVELOPMENT, SPEED_WITH_CONTROL.
- Rama aislada: `feature/data-integrity-a5e`
- Base canónica: `main@d3f2bec08bc75357dc438086092c44693a8a6b97`
- HEAD de implementación antes de este handoff: `fd93d2bac6e644d5c4a6f1d927d5de3ac0fc12bf`
- Producción: esquema `0008`; no se ha accedido ni modificado producción.

## Estado técnico

### VERIFIED

- La rama A5E parte exactamente del `main` indicado por Chief.
- Se compuso la dependencia A5D sobre esa base como `DRAFT_0009`, sin crear `0010`.
- `0009_sync_recovery_state.py` sigue con `down_revision = "0008"`; no se modificó `0008`.
- Implementación A5E contenida en Data Integrity:
  - `app/repositories/sync_recovery_repository.py`;
  - `app/services/sync_recovery_service.py`;
  - integración en `fixture_sync_service.py`;
  - integración en `catalog_sync_service.py`;
  - tests dirigidos en `tests/test_sync_recovery.py`.
- No se modificaron `live_sync.py`, `ops_tick.py`, estadísticas, scheduler ni writers de A6.
- `fixture_sync_service` conserva `FixtureEvidence.received("sync", provider.name)` después de la respuesta del proveedor y antes de la transacción de dominio.
- Los syncs de fixtures por rango no crean/actualizan `sync_unit_states`.
- `outage` permanece `RESERVED_BUT_INACTIVE`.

### Semántica implementada

- `last_attempt_started_at`: se persiste y confirma inmediatamente antes del HTTP de la unidad.
- Éxito: `last_success_at`/outcome se staged dentro de la misma transacción que el dominio; un rollback revierte ambos.
- Fallo: rollback de dominio y luego bookkeeping de fallo best-effort en transacción separada.
- Error de bookkeeping posterior no sustituye el error original.
- Incidentes: solo `auth`, `quota`, `rate_limit`; `ProviderNotConfiguredError` no abre incidente.
- Una respuesta posterior correcta del mismo proveedor cierra incidentes anteriores de manera best-effort.
- Se conserva la clasificación existente de aislamiento por proveedor/competición y el accounting existente de requests/retries.
- El texto persistido de error se limita a 500 caracteres y sanea URLs y etiquetas de secretos.

### PENDING

- Ejecutar los tests dirigidos A5E contra PostgreSQL local desechable.
- Ejecutar una sola vez los tests de integración DI relevantes.
- Si están verdes, registrar el resultado y cerrar A5E.
- No ejecutar suite completa en este work package.

## Restricciones vigentes

- NO producción.
- NO aplicar `0009` a producción.
- NO `0010`.
- NO scheduler.
- NO lease/claim (A5F).
- NO A5G.
- NO recovery de estadísticas Modular.
- NO merge a `main`.

## Nota de ejecución

Esta sesión dispone de escritura GitHub pero no de un checkout/runner del repositorio ni de PostgreSQL local alcanzable desde el entorno de herramientas. Por ello los tests están `PENDING`, no reportados como PASS.

## Siguiente acción

En un worktree local aislado de `feature/data-integrity-a5e`, con PostgreSQL local desechable autorizado por el guard de `tests/conftest.py`:

1. Targeted una vez:
   `pytest -q tests/test_sync_recovery.py tests/test_migration_0009.py`
2. Integración DI relevante una vez:
   `pytest -q tests/test_fixture_sync_evidence.py tests/test_live_sync_fixtures.py tests/test_live_sync_job.py tests/test_provider_limits_sync.py tests/test_sync_failures.py tests/test_sync_idempotency.py tests/test_migration_0008.py`

No ejecutar la suite completa. Si ambos grupos quedan verdes, actualizar HANDOFF/PROGRESS, hacer un commit de cierre y declarar `DI_A5E: PASS`.
