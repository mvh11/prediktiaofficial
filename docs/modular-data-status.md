# Estado: desarrollo modular

## Ciclo actual (desde el checkpoint conjunto DI-A6 + Modular)

- **Baseline canónico:** `1c06ef9` (`origin/integration/di-a6-modular-final`), Alembic `0008` (cabeza única; `0009` reservada para DI-A5D). Rama de trabajo: `feature/modular-m57-stats-as-of`.
- **M5.6** (reconciliador live + scheduler C6): implementado, publicado y verificado con smokes supervisados en producción. Pendiente de activación: piloto de 24 h, catch-up manual y alertas.
- **M5.7A: lectura as-of de estadísticas** (`app/schemas/statistics_knowledge.py`, `app/repositories/statistics_knowledge_repository.py`, `app/services/statistics_as_of.py`). Solo lee; sin migración.
  - **Selección:** por partido y proveedor, observaciones con `available_at <= T` (incluido); manda la más reciente por `(observed_at, id)`, el orden de versionado del esquema.
  - **Reconstrucción:** los valores salen del raw de ESA observación con el normalizador vigente (parser del adapter). Nunca de `fixture_team_statistics` (estado actual fusionado) ni fusionando versiones.
  - **Calidad:** se recalculan los checks de identidad y de valores del service; con algún BLOCKING el partido queda `BLOCKED`, sin valores. Si el hash del raw, la disponibilidad registrada o los BLOCKING que registró el run no se reproducen: `RECONSTRUCTION_MISMATCH`, sin valores y sin elegir ganador.
  - **Estados:** `UNKNOWN_AT_T`, `AVAILABLE`, `PARTIAL`, `EMPTY` (evidencia válida, no ceros), `BLOCKED`, `RECONSTRUCTION_MISMATCH`.
  - **Procedencia:** `HISTORICAL_SYNTHETIC` (`backfill`, `available_at = kickoff + 6 h`) u `OPERATIONAL` (`live`/`manual`, `available_at = observed_at`). `observed_after(T)` marca lo disponible por política pero recibido después de T.
  - **Contexto prepartido** (`app/services/statistics_prematch.py`, capa pura aparte): corte = kickoff del objetivo; se excluyen el propio partido y los de kickoff igual o posterior.
  - **Pendiente para M5.7B:** fuente del kickoff de contexto (STRICT_KNOWLEDGE de DI-A6), proyección de una observación frente a fusión as-of, y la deuda de refresh histórico (una revisión descubierta tarde también recibe `kickoff + 6 h`).

El resto de este documento conserva el estado anterior a este ciclo.

Rama anterior `feature/modular-data-m43`, checkpoint conocido `6952425` (`6952425e0e84bf78d6bc414df85a4b3263d9ff89`). Ver [workstreams.md](workstreams.md) y [agent-rules.md](agent-rules.md), teniendo en cuenta la deuda documental de ownership registrada abajo.

## Estado actual

- **M4.2: completado e integrado.** La integración y validación originales se conservan como antecedente histórico abajo, no como una promoción pendiente ni como el siguiente paso operativo.
- **M4.3: completado.** Backfills reales de Premier League 2025 (380 fixtures) y Liga MX 2025 (337 fixtures).
- **M4.4A: completado.** Discovery de 34 pares P0: 2 ya `completed` y 32 dry-runs realizados (runs #5–#36, todos `dry_run_completed`), sin cambios de dominio.
- **M4.4 real: PAUSADO** hasta validar el hardening de `season_teams` bajo Modular Principal.

El estado operativo y los conteos de M4.3/M4.4A aquí documentados son los comunicados por el usuario; esta actualización documental no ejecuta tests, consultas de BD ni llamadas a proveedores.

## Coordinación: Modular Principal y Modular Auxiliar

- **Modular Principal = ChatGPT + Claude.** Tiene ownership del hardening de `season_teams`, membership/backfill, Q16 y tests directamente relacionados. Para ese trabajo tiene reservados:
  - `backend/app/services/history_backfill_service.py`;
  - `backend/app/services/season_membership.py`: **ruta prevista si Principal decide crearla**; el archivo todavía no existe;
  - Q16 y los history quality checks relacionados en `backend/app/services/history_quality_checks.py`;
  - los tests directamente relacionados con membership/backfill, incluidos los existentes `backend/tests/test_history_backfill.py`, `backend/tests/test_history_quality_checks.py` y `backend/tests/test_history_stale_runs.py`, y los nuevos que Principal cree para ese alcance.
- **Modular Auxiliar = ChatGPT + OpenCode.** No toca esos archivos ni tests mientras Principal trabaja en ellos. OpenCode puede realizar investigaciones, documentación y tareas pequeñas expresamente delegadas; no asume cambios de código ni ownership por iniciativa propia.

Las rutas existentes del servicio, quality checks y tests anteriores se han verificado en el repositorio; Q16 y el nuevo módulo de membership forman parte del hardening pendiente, no se dan por implementados aquí.

### Deuda documental de ownership

[workstreams.md](workstreams.md), [agent-rules.md](agent-rules.md) y [data-integrity-status.md](data-integrity-status.md) mantienen el ownership antiguo de backfills asociado a Data Integrity / Sync. No reflejan todavía la división Principal/Auxiliar ni la reserva actual de membership/backfill para Principal. Se registra esta deuda sin modificar esos documentos; su actualización requiere una tarea expresamente autorizada.

## Módulos completados

Componentes presentes en la rama (código comprobado en el repositorio):

- **Catálogo:** competiciones, temporadas y equipos (`catalog_sync_service.py`, `catalog_repository.py`, `api/routes/catalog.py`).
- **Partidos:** sync de fixtures desde API-Football (`fixture_sync_service.py`, `fixture_repository.py`, `api/routes/fixtures.py`).
- **Proveedores y mapeos:** tablas `providers` y `*_provider_mappings`, columnas `fulltime_*` (migración `0004`).
- **Upsert de fixtures por pares** (`3084996`, trabajo previo que M4.2 reutiliza): marcadores por pares completos y tests de casos límite del adapter. La política final de conservación/limpieza por estado se fijó en el checkpoint y está descrita en [data-integrity-status.md](data-integrity-status.md).
- **M4.2:** backfill histórico (detalle abajo).

M1–M4.1 son antecedentes completados del roadmap. El desarrollo actual ya está en M4.4; este documento no reconstruye ni atribuye detalles de implementación a cada una de esas fases anteriores.

## M4.2: antecedente histórico completado e integrado

El detalle siguiente conserva únicamente como antecedente la implementación y validación del checkpoint histórico `checkpoint/m42-data-integrity @ ebcb076`.

### Objetivo

Importar de forma auditada el histórico de fixtures y resultados de **un par competición-temporada** cerrado, sin dejar nunca un par importado a medias y sin corregir anomalías del proveedor: se detectan y se registran.

### Implementación

Flujo de `run_backfill` (`history_backfill_service.py`):

1. **Registro del run** (`status=running`) en su propia transacción, confirmada enseguida, para que sobreviva aunque se deshagan las escrituras de dominio.
2. **Precondiciones**, sin escribir datos de dominio:
   - la temporada existe en Prediktia (no se crea);
   - no es la temporada actual (la gestiona el sync vivo);
   - no hay ya un backfill `completed` del par, salvo `--refresh`.
3. **Descarga** de la temporada con `RecordingApiFootballProvider`, que además guarda la liga y temporada que declara el proveedor para cada partido.
4. **Análisis solo con lecturas:** contadores nuevo/existente/cambiaría/sin cambios, equipos y `season_teams` que faltarían, y checks previos.
5. **Check bloqueante** → rollback y `blocked`, sin escrituras de dominio.
6. **Dry-run** → rollback y `dry_run_completed`. El run queda registrado.
7. **Ejecución real en una sola transacción:** `ensure_teams`, `upsert_fixtures`, mapeos y `link_teams_to_season`. Después, checks sobre el estado resultante y comprobación de paridad entre lo previsto y lo guardado. Si algo bloquea → rollback y `blocked`. Error de BD (`IntegrityError`, `DataError`, `OperationalError`) → rollback y `failed`. Si todo pasa → commit y `completed`.
8. **Cierre del run** (contadores, checks, mensaje) en otra transacción. Cualquier excepción no prevista marca el run `failed` y se relanza: un run nunca se queda en `running` por un error capturable.

**Quality checks Q1–Q15** (`history_quality_checks.py`). Detectan, nunca corrigen.

| Check | Severidad | Qué detecta |
| --- | --- | --- |
| Q1 | blocking | 0 partidos recibidos, o fuera del rango `--expected-min/--expected-max` si se indica |
| Q2 | blocking | El conteo global de fixtures disminuye |
| Q3 | blocking | `(provider, external_id)` duplicados en `fixture_provider_mappings` |
| Q4 | blocking | Mapeos de fixtures sin fixture |
| Q5 | blocking | Partidos sin `kickoff_at` |
| Q6 | warning | FT/AET/PEN sin `goals` válidos (se guardan con `goals` NULL) |
| Q7 | warning | FT/AET/PEN sin `fulltime` (no se inventa) |
| Q8 | blocking | `fulltime` parcial o negativo |
| Q9 | warning | AET/PEN con `fulltime` mayor que el resultado final |
| Q10 | warning | FT con `fulltime` distinto de `goals` |
| Q11 | warning | `halftime` mayor que `fulltime` |
| Q12 | blocking | Liga/temporada de la respuesta distinta de la pedida, o `external_id` ya guardado en otra season |
| Q13 | blocking | Partidos recibidos sin mapeo del proveedor tras escribir |
| Q14 | warning | Temporada cerrada con partidos pasados en estado no final |
| Q15 | blocking | Local y visitante son el mismo equipo |

Además existe un check `PARITY` (warning) si el estado guardado no coincide con el que predijo el análisis. La predicción usa la política central de `fixture_repository.py` (`predict_score_values`), la misma que aplica el upsert.

**Recuperación de runs abandonados** (`fail_stale_run`): operación administrativa explícita y separada.

- Bloquea la fila `running` del par con `FOR UPDATE`, de modo que dos recuperaciones simultáneas no se pisan.
- Solo actúa si `started_at` es anterior a `now() - stale_after_minutes` (120 min por defecto, mínimo 1).
- Marca el run `failed` con un mensaje de recuperación. No lo borra, no lo marca `completed` y no lanza ningún backfill.
- Resultados posibles: `recovered`, `not_found` o `too_recent`.

### Archivos / componentes relevantes

| Archivo | Papel |
| --- | --- |
| `backend/alembic/versions/0005_season_backfill_runs.py` | Migración de la tabla de runs |
| `backend/app/models/backfill.py` | Modelo `SeasonBackfillRun` |
| `backend/app/models/__init__.py` | Registra `SeasonBackfillRun` para Alembic |
| `backend/app/schemas/backfill.py` | `CheckResult`, `BackfillResult`, `StaleRunRecovery` |
| `backend/app/repositories/backfill_repository.py` | Registro de runs y lecturas de estado (fixtures existentes, equipos, mapeos, conteos, bloqueo de runs) |
| `backend/app/services/history_backfill_service.py` | `run_backfill`, `predict_stored_values`, `fail_stale_run` |
| `backend/app/services/history_quality_checks.py` | Q1–Q15 y su severidad centralizada |
| `backend/app/integrations/football/api_football_history.py` | `RecordingApiFootballProvider`: hereda el adapter sin cambiar el parseo |
| `backend/app/jobs/history_backfill.py` | CLI |
| `backend/tests/test_history_backfill.py` | Tests DB del flujo completo |
| `backend/tests/test_history_stale_runs.py` | Tests DB de recuperación de runs y Q1 |
| `backend/tests/test_history_quality_checks.py` | Tests unitarios de checks, predicción y CLI |
| `backend/tests/test_migration_0005.py` | Tests DB de upgrade/downgrade y constraints |
| `backend/tests/test_fixture_upsert.py` | Tests DB de la política de upsert por pares, incluida la matriz de paridad `predict_score_values` ↔ `ON CONFLICT` |

### Base de datos / migraciones

- **Migración `0005_season_backfill_runs.py`** (revisión `0005`, `down_revision` `0004`). Tabla nueva y aislada: no modifica tablas existentes ni migra datos. El downgrade elimina la tabla (se pierde el historial de runs, no datos deportivos).
- **Tabla `season_backfill_runs`:**
  - Claves: `competition_id` → `competitions` (CASCADE), `season_id` → `seasons` (CASCADE, NULL solo si la temporada no existe), `provider` → `providers.code` (RESTRICT).
  - Estado: `requested_year`, `status`, `is_dry_run`, `is_refresh`, `started_at`, `finished_at`.
  - Contadores: `received`, `new`, `existing`, `changed`, `unchanged`, `new_teams`, `new_season_teams`, `warning`, `blocking` (todos `>= 0`).
  - `checks` (JSONB) y `error_message`.
- **Constraints:**
  - `ck_season_backfill_runs_status`: `running`, `completed`, `dry_run_completed`, `blocked`, `failed`.
  - `ck_season_backfill_runs_finished`: `finished_at` existe si y solo si el run no está `running`.
  - `ck_season_backfill_runs_order`: `finished_at >= started_at`.
  - `ck_season_backfill_runs_dry_run`: un dry-run nunca figura como `completed`, ni al revés.
  - `ck_season_backfill_runs_counters`.
- **Índices:** `ix_season_backfill_runs_competition_year`, `ix_season_backfill_runs_season_status` y el único parcial `uq_season_backfill_runs_one_running` (nunca dos runs `running` del mismo par).
- **Validación de `0005`:** validada sobre `0004` en el checkpoint. `test_migration_0005.py` (upgrade/downgrade, constraints, FK, unicidad del run `running` y JSON de checks): 11 passed. La cadena `0004` → `0005` es única.

### Endpoints o servicios

- **Sin endpoints HTTP nuevos.** M4.2 no toca `api/routes`.
- **Servicios:** `run_backfill(db, competition_id, year, *, dry_run, refresh, provider, expected_range, now)` y `fail_stale_run(db, competition_id, year, stale_after_minutes)`.
- **CLI** (desde `backend/`):

  ```
  python -m app.jobs.history_backfill --competition-id <id interno> --season <año> [--dry-run] [--refresh] [--expected-min N --expected-max M]
  python -m app.jobs.history_backfill --competition-id <id interno> --season <año> --fail-stale-run [--stale-after-minutes 120]
  ```

  Hace 1 petición al proveedor por invocación y escribe en la BD de `DATABASE_URL`. Códigos de salida: `0` completed/dry_run_completed/recuperado; `1` blocked o recuperación rechazada/sin run; `2` failed o error de uso.

### Tests y verificación

**Validación histórica del checkpoint (`checkpoint/m42-data-integrity`, merge `ebcb076`): completa.** No es una nueva ejecución ni valida el hardening pendiente de M4.4.

- **Entorno:** PostgreSQL 18.6 local y desechable (cluster creado solo para la validación, escuchando únicamente en `127.0.0.1`, eliminado al terminar).
- **Contrato de la guarda:** `TEST_DATABASE_URL` más `TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>:<puerto>/<bd>`, con el destino exacto.
- **`pytest -q`:** **489 passed, 0 failed, 0 skipped.** Los **220 tests de BD se ejecutaron realmente**; ninguno se saltó.
- **Tests dirigidos:**

  | Test | Resultado |
  | --- | --- |
  | `test_migration_0004.py::test_upgrade_backfill` | passed en ~0,9 s; **el bloqueo anterior no se reprodujo** |
  | `test_migration_0005.py` | 11 passed (`0005` validada sobre `0004`) |
  | `test_fixture_upsert.py` | 120 passed (política de upsert y matriz de paridad) |
  | `test_history_backfill.py` | 16 passed (flujo completo y `PARITY` validado) |
  | `test_history_stale_runs.py` | 14 passed |
  | `test_sync_idempotency.py` | 11 passed |

- **Neon no se tocó:** no se modificó `.env` y no se usó ninguna URL de Neon para conectarse.

**Antecedente:** en `feature/modular-data`, un intento anterior con PostgreSQL desechable (Docker) se quedó detenido en `test_migration_0004.py::test_upgrade_backfill` sin causa determinada. Ese intento no constituyó validación; queda sustituido por la validación del checkpoint, en la que el test pasa.

### Decisiones técnicas

- **Unidad de trabajo:** un par competición-temporada = una transacción de dominio. El registro del run va en transacciones separadas para que sobreviva a los rollbacks.
- **No se crean temporadas** desde el backfill, y la temporada actual se excluye: la gestiona el sync vivo.
- **Un par ya `completed`** solo se reevalúa con `--refresh`.
- **Los checks detectan y no corrigen.** Severidad centralizada en `SEVERITY`. Q1 es blocking (0 partidos o fuera de rango indica que la temporada no es la esperada). Q6 es warning, para no perder una temporada entera por un partido sin marcador válido.
- **Q12 compara la identidad declarada por el proveedor** (`league.id`, `league.season`) con la pedida, porque `FixtureData` lleva la liga/temporada pedidas. Por eso existe `RecordingApiFootballProvider`, que no cambia el parseo.
- **Predicción de la política de upsert** (`predict_stored_values`) para contar "cambiaría / sin cambios" sin escribir, más una comprobación de paridad después de escribir. Desde el checkpoint, los marcadores se predicen con `fixture_repository.predict_score_values`, que sale de la misma tabla de reglas que el `ON CONFLICT`: el servicio de backfill no tiene una copia propia de la política.
- **La migración `0005` es aditiva**; no se tocó `0004`.
- **La recuperación de runs abandonados es manual y explícita:** nunca automática, nunca lanza un backfill.
- **Las muestras de los checks** se limitan a 5 `external_id`; no se guardan dumps de fixtures.

### Riesgos y pendientes

- **Q2, Q3 y Q4 son globales** (cuentan toda la tabla), no solo el par. Una anomalía previa ajena al par bloquea cualquier backfill. Es una decisión conservadora; su impacto operativo está **Pendiente de verificación** con datos reales.
- **Q12 sin identidad:** si el proveedor no informa la liga/temporada, Q12 bloquea la temporada entera.
- **Pares completos incoherentes con el estado** (por ejemplo, `penalty` completo en un FT): el upsert los guarda tal como llegan. Ningún check del backfill los señala todavía; es una auditoría posterior al checkpoint.

### Resuelto en el checkpoint histórico M4.2

- **Conflictos con Data Integrity** en `api_football.py`, `fixture_repository.py`, `fixture_sync_service.py`, `conftest.py`, `test_api_football_adapter.py` y `test_sync_idempotency.py`: resueltos en `ebcb076`.
- **Acoplamiento con `fixture_repository.py`:** `predict_stored_values` ya no replica la política; importa `_FIXTURE_COLUMNS` y `predict_score_values`. La paridad está cubierta por la matriz de `test_fixture_upsert.py` y por `PARITY` en `test_history_backfill.py`.
- **Guarda de tests destructivos:** unificada con la de Data Integrity (`TEST_DATABASE_URL` + `TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>:<puerto>/<bd>`), más la regla de Modular que bloquea un servidor remoto compartido con desarrollo. `test_conftest_guard.py` usa ese contrato.
- **Migraciones:** cadena `0004` → `0005` única; Data Integrity no creó ninguna migración.
- **Semántica de marcadores al revertir el estado y de `extratime_*`:** política final fijada en el checkpoint (ver [data-integrity-status.md](data-integrity-status.md)).

## M4.3: backfills reales completados

| Par competición-temporada | Fixtures reales | Estado |
| --- | --- | --- |
| Premier League 2025 | 380 | `completed` |
| Liga MX 2025 | 337 | `completed` |

Estos son los 2 pares ya `completed` del conjunto P0 de M4.4A.

## M4.4A: discovery P0 completado

- **Alcance:** 34 pares P0; 2 ya `completed` y 32 dry-runs realizados.
- **Runs:** #5–#36, todos con estado `dry_run_completed`.
- **API-Football:** 32 requests, 0 retries, 0 errores y 0 rate limits.
- **5Dollar:** 0 llamadas.
- **Dominio:** cero cambios; los dry-runs registraron sus runs sin escribir datos de dominio.

### Conteos conocidos tras discovery

| Tabla | Conteo |
| --- | --- |
| `teams` | 617 |
| `season_teams` | 895 |
| `fixtures` | 7643 |
| `fixture_provider_mappings` | 7643 |
| `season_backfill_runs` | 36 |

Son los conteos conocidos tras discovery, no una lectura nueva de la BD en esta tarea.

### Hallazgos pendientes de M4.4A

- **Q14:** Bolivia 2025, Colombia 2025, Ecuador 2025 y Perú 2025.
- Status `Canc` minúsculo detectado en Bolivia 2025; PST/no-final en temporadas cerradas.
- Fixtures posteriores a `season.end_date` donde corresponda.
- Estructuras/rounds complejos o ambiguos; MLS con playoffs best-of-three variables; Argentina 2024/2025 y la deuda conocida de Copa de la Liga.
- `PARITY` incompleto/no explícito.
- Q2/Q3/Q4 globales y duplicación pre/post de Q3/Q4.
- Código de salida ante excepción inesperada.
- Transacción T2 abierta durante la llamada HTTP.

Son hallazgos pendientes de investigación/hardening, no resueltos en esta actualización. Auxiliar **NO los corrige automáticamente** ni cruza las reservas de Principal; cualquier corrección requiere autorización expresa.

## Próximo trabajo modular

### Principal

Hardening de `season_teams` + Q16 + tests directamente relacionados con membership/backfill, bajo su reserva.

### Auxiliar

Siguiente investigación autorizada: **OA-02 — Auditoría de Q14 en Bolivia/Colombia/Ecuador/Perú 2025**. No se ejecuta dentro de OA-01/OA-01B ni autoriza correcciones automáticas o modificaciones de archivos reservados.

### M4.4 real

**PAUSADO hasta validar el hardening de Principal.** No existen nuevas escrituras reales autorizadas todavía; el discovery completado no las habilita.

## Referencias históricas de M4.2

- **Commits de Modular integrados en aquel checkpoint:** `3084996` ("avance 4.2"), `6e3aff1` ("feat: add historical backfill infrastructure"), `ff26513` ("docs: add shared development coordination") y `456c556` ("docs: add modular data status"), sobre la base común `298b2f3` ("mudar a neon 004").
- **`feature/modular-data`:** rama original congelada en `456c556`; no continúa desarrollándose.
