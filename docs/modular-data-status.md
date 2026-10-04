# Estado: desarrollo modular

Rama `feature/modular-data`. Ver [workstreams.md](workstreams.md) y [agent-rules.md](agent-rules.md).

## Estado actual

- **Último commit funcional:** `6e3aff1` ("feat: add historical backfill infrastructure"), publicado en `origin/feature/modular-data`.
- **Encima:** `ff26513` ("docs: add shared development coordination"), con los documentos compartidos. Pendiente de push.
- **Base común con Data Integrity:** `298b2f3` ("mudar a neon 004").
- **Commits de la rama desde la base:** `3084996` ("avance 4.2"), `6e3aff1` y `ff26513`.
- **M4.2:** cerrado en código. **Validación contra PostgreSQL pendiente** (ver [Tests y verificación](#tests-y-verificación)).
- **M4.3:** no iniciado. Se empezará desde el `main` del checkpoint.

## Módulos completados

Componentes presentes en la rama (código comprobado en el repositorio):

- **Catálogo:** competiciones, temporadas y equipos (`catalog_sync_service.py`, `catalog_repository.py`, `api/routes/catalog.py`).
- **Partidos:** sync de fixtures desde API-Football (`fixture_sync_service.py`, `fixture_repository.py`, `api/routes/fixtures.py`).
- **Proveedores y mapeos:** tablas `providers` y `*_provider_mappings`, columnas `fulltime_*` (migración `0004`).
- **Upsert de fixtures por pares** (`3084996`, trabajo previo que M4.2 reutiliza): marcadores por pares completos, conservación según estado final, `fulltime` solo en FT/AET/PEN y tests de casos límite del adapter.
- **M4.2:** backfill histórico (detalle abajo).

La numeración de los módulos anteriores (M1–M4.1) no figura en el repositorio: **Pendiente de verificación**.

## M4.2

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

Además existe un check `PARITY` (warning) si el estado guardado no coincide con el que predijo el análisis.

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
| `backend/tests/test_fixture_upsert.py` | Test añadido: `ck_fixtures_fulltime_pair` rechaza medio par de `fulltime` |

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
- **Aplicación de `0005`:** durante el intento parcial con PostgreSQL desechable (ver abajo), `alembic upgrade head` llegó a aplicar `0005`: lo hace el fixture `migrated_db` al inicio de la sesión de tests, y los tests de backfill que usan `season_backfill_runs` se ejecutaron después. **Eso no constituye validación completa de la migración:** `test_migration_0005.py` (upgrade/downgrade y constraints) no llegó a ejecutarse, y la suite DB completa sigue pendiente del checkpoint.

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

**Suite sin BD (ejecutada en esta máquina sobre `6e3aff1`):**

- `pytest`: **122 passed, 87 skipped**.
- **Los 87 skipped son todos tests `db`: NO EJECUTADOS EN ESTA MÁQUINA.** La guarda los saltó con el motivo "falta TEST_DATABASE_URL". No cuentan como pasados.
- Archivos afectados por los skips: `test_fixture_upsert.py`, `test_history_backfill.py`, `test_history_stale_runs.py`, `test_migration_0004.py`, `test_migration_0005.py`, `test_provider_mappings.py`, `test_sync_idempotency.py` y `test_competitions_endpoint.py`.
- `git diff --check`: limpio.

**Intento parcial con PostgreSQL local (interrumpido, no constituye validación):**

- Contenedor Docker desechable `postgres:17-alpine`, datos en `tmpfs`, escuchando solo en `127.0.0.1:55432`, BD `prediktia_test`.
- Variables usadas, las que exige la guarda de esta rama: `TEST_DATABASE_URL` y `PREDIKTIA_DESTRUCTIVE_TEST_DB=localhost:55432/prediktia_test`.
- La suite se quedó detenida en `tests/test_migration_0004.py::test_upgrade_backfill`. En ese momento la BD no tenía consultas activas ni locks, y el proceso de pytest no consumía CPU. **No se determinó la causa**: no se obtuvo traza y no se afirma que sea un fallo de código ni del entorno.
- Antes de ese punto el registro mostraba 170 PASSED y ningún FAILED, incluidos tests de `test_fixture_upsert.py`, `test_history_backfill.py` y `test_history_stale_runs.py`. Como la ejecución no terminó, ese resultado **no se presenta como validación**.
- No llegaron a ejecutarse `test_migration_0005.py`, `test_provider_mappings.py`, `test_sync_idempotency.py` ni el resto de `test_migration_0004.py`.
- El intento se abandonó por indicación del usuario. El contenedor se detuvo y eliminó, y no quedó ningún proceso de pytest.
- **Neon no se tocó:** no se modificó `.env` y no se usó ninguna URL de Neon.

**Pendiente:** ejecutar la suite completa, con 0 skips de BD, contra una BD desechable segura durante el checkpoint, en otra máquina. Esa ejecución es la que debe validar `0005` (incluido `test_migration_0005.py`), los tests de backfill, de stale runs y de fixture upsert, y aclarar el bloqueo en `test_upgrade_backfill`.

### Decisiones técnicas

- **Unidad de trabajo:** un par competición-temporada = una transacción de dominio. El registro del run va en transacciones separadas para que sobreviva a los rollbacks.
- **No se crean temporadas** desde el backfill, y la temporada actual se excluye: la gestiona el sync vivo.
- **Un par ya `completed`** solo se reevalúa con `--refresh`.
- **Los checks detectan y no corrigen.** Severidad centralizada en `SEVERITY`. Q1 es blocking (0 partidos o fuera de rango indica que la temporada no es la esperada). Q6 es warning, para no perder una temporada entera por un partido sin marcador válido.
- **Q12 compara la identidad declarada por el proveedor** (`league.id`, `league.season`) con la pedida, porque `FixtureData` lleva la liga/temporada pedidas. Por eso existe `RecordingApiFootballProvider`, que no cambia el parseo.
- **Predicción en Python de la política de upsert** (`predict_stored_values`) para contar "cambiaría / sin cambios" sin escribir, más una comprobación de paridad después de escribir.
- **La migración `0005` es aditiva**; no se tocó `0004`.
- **La recuperación de runs abandonados es manual y explícita:** nunca automática, nunca lanza un backfill.
- **Las muestras de los checks** se limitan a 5 `external_id`; no se guardan dumps de fixtures.

### Riesgos y pendientes

- **Validación PostgreSQL completa pendiente** (ver arriba), incluido el bloqueo no explicado en `test_migration_0004.py::test_upgrade_backfill`.
- **Acoplamiento con `fixture_repository.py`:** el servicio importa los nombres privados `_FIXTURE_COLUMNS`, `_FULLTIME_PAIR` y `_SCORE_PAIRS`, y `predict_stored_values` replica la política de `_fixture_update_values` de esta rama. Si el checkpoint adopta otra política de upsert, la predicción, los contadores y el check `PARITY` pueden dejar de coincidir. Hay que revisarlo y volver a pasar los tests de paridad tras el checkpoint.
- **Q2, Q3 y Q4 son globales** (cuentan toda la tabla), no solo el par. Una anomalía previa ajena al par bloquea cualquier backfill. Es una decisión conservadora; su impacto operativo está **Pendiente de verificación** con datos reales.
- **Q12 sin identidad:** si el proveedor no informa la liga/temporada, Q12 bloquea la temporada entera.
- **Uso real del CLI contra Neon:** no se ha ejecutado ningún backfill real. **Pendiente de verificación.**
- **Diferencia entre la guarda de esta rama y la documentada en [agent-rules.md](agent-rules.md):** esta rama usa `PREDIKTIA_DESTRUCTIVE_TEST_DB=<host>:<puerto>/<bd>`, y la regla compartida describe `TEST_DATABASE_ALLOW_DESTRUCTIVE`, que corresponde a la guarda de `8c00609` en Data Integrity. Tras el checkpoint, los tests de M4.2 deben ejecutarse con la guarda que quede en `main`.

### Dependencias o bloqueos de Data Integrity

- **Posibles conflictos que el checkpoint debe revisar entre ambas ramas** en:

  ```
  backend/app/integrations/football/api_football.py
  backend/app/repositories/fixture_repository.py
  backend/app/services/fixture_sync_service.py
  backend/tests/conftest.py
  backend/tests/test_api_football_adapter.py
  backend/tests/test_sync_idempotency.py
  ```

  `3084996` ("avance 4.2") modificó esos archivos en esta rama, y Data Integrity los ha cambiado por su lado. **No se resuelven desde Modular.**
- **Ownership de backfills:** [agent-rules.md](agent-rules.md) y [workstreams.md](workstreams.md) asignan los "backfills" al carril Data Integrity, pero M4.2 (backfill histórico) se desarrolló en Modular con alcance confirmado por el usuario. El checkpoint debe decidir quién mantiene este código en adelante.
- **Migraciones:** M4.2 crea `0005`. Que Data Integrity no haya creado otra migración con la misma revisión o con `down_revision` `0004` está **Pendiente de verificación** en el checkpoint, según la regla de una migración nueva a la vez.
- **Semántica de marcadores al revertir el estado:** [data-integrity-status.md](data-integrity-status.md) lista como pendiente que los marcadores conservados no se limpian al pasar a PST/CANC/ABD/NS. En esta rama, `3084996` incluye un test que limpia marcadores en ese caso (`test_match_back_to_non_final_state_clears_scores`), y `predict_stored_values` depende de esa política. La política final la decide el checkpoint.
- **`extratime_*`:** Data Integrity tiene pendiente su semántica. El backfill no lo valida en ningún check y depende de lo que haga el upsert.

## Próximo trabajo modular

- **M4.3:** no iniciado. Alcance **Pendiente de verificación**; se definirá tras el checkpoint, en un carril nuevo creado desde el `main` resultante.
- **Tras el checkpoint, antes de M4.3:**
  - ejecutar la suite completa con BD desechable y 0 skips;
  - revisar la paridad `predict_stored_values` ↔ upsert final;
  - revisar el ownership del backfill.

## Checkpoint con main

- **Integración:** de `feature/data-integrity-sync` y `feature/modular-data` en `main`, coordinada por el usuario. Desde este carril no se hace merge, rebase, pull ni cherry-pick.
- **Commits de Modular a integrar:** `3084996`, `6e3aff1`, `ff26513` y el commit de este documento.
- **Debe comprobarse en el checkpoint:**
  - conflictos en los archivos listados arriba;
  - cadena de migraciones `0004` → `0005` única;
  - suite completa en verde con 0 skips de BD, incluidos los 87 tests no ejecutados aquí;
  - el bloqueo en `test_migration_0004.py::test_upgrade_backfill`.
- **Después del checkpoint**, una vez publicado el nuevo `main`:

  ```
  git fetch origin
  git checkout main
  git pull --ff-only origin main
  ```

  Se verifica que `HEAD` coincide con `origin/main` y, a partir de ese `main`, se crea una rama Modular nueva para M4.3.
- **`feature/modular-data` no continúa desarrollándose después del checkpoint.** No se hace pull, merge ni rebase de `main` sobre ella.
