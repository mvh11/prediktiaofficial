# Estado: integridad de datos y sync de partidos

Rama `feature/data-integrity-sync` (congelada en `61428e3`), base `298b2f3`. Integrada con M4.2 en `checkpoint/m42-data-integrity` (ver [Checkpoint M4.2](#checkpoint-m42-ebcb076)). Ver [workstreams.md](workstreams.md).

## Auditoría hecha

Se revisaron estos puntos:

- **Migración 0004** (`0004_provider_mappings_fulltime.py`): tablas de mapeo de proveedores y columnas `fulltime_*` con sus restricciones (`ck_fixtures_fulltime_pair`, `ck_fixtures_fulltime_finished`, `ck_fixtures_fulltime_nonneg`).
- **Semántica de marcadores:**
  - `goals`: marcador final; en AET/PEN incluye la prórroga.
  - `halftime`: marcador al descanso.
  - `fulltime`: marcador a los 90'; solo existe en partidos terminados (FT/AET/PEN).
  - `extratime`: campo de prórroga recibido de API-Football. Su semántica no se considera suficientemente estable para usarlo directamente como fuente canónica sin validación.
  - `penalty`: tanda de penaltis.
- **Idempotencia de la sync de partidos.**
- **Paginación** de la API de API-Football.
- **Reintentos y backoff** ante 429, timeouts y 5xx.
- **Consultas N+1.**
- **Fallos parciales:** una competición que falla no debe afectar a las demás.
- **Backfill** de datos históricos.
- **Riesgo de pérdida de datos en Neon:** sobrescrituras con NULL y tests destructivos contra la BD real.
- **Cobertura de tests.**

## Fix de sync no destructiva (C1/C2/I1)

**Estado: aplicado en esta rama** en el commit `b3af1d4` ("fix: make fixture sync non-destructive and isolate database failures"), ya subido a `origin/feature/data-integrity-sync`. Pendiente de merge a `main`.

Qué hace:

- **Adapter:** descarta pares de marcador incompletos, negativos o mal formados, nunca medio par. En FT sin 90' usa `goals`; en AET/PEN no.
- **Upsert:**
  - Un par entrante completo sustituye al guardado; uno con NULL conserva el guardado.
  - `fulltime` se vacía si el partido deja de estar terminado.
  - Las filas sin cambios no se reescriben.
  - La política de conservación con NULL quedó sustituida en el checkpoint por una política por estado (ver [Checkpoint M4.2](#checkpoint-m42-ebcb076)).
- **Sync:** un error de BD (`IntegrityError`, `DataError`, `OperationalError`) deshace solo esa competición y se sigue con las demás.
- **Tests:** los tests `db` exigen un `TEST_DATABASE_URL` explícito, distinto de la BD de desarrollo, y una autorización destructiva explícita. La guarda se endureció después en `8c00609` (ver la sección siguiente).

## Guarda de tests destructivos (`8c00609`)

**Estado: aplicado en esta rama** en el commit `8c00609` ("test: harden destructive database guard"). Solo cambia `backend/tests/conftest.py` y sus tests; no toca código de producción.

- **Autorización exacta por cluster:** `TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>:<puerto>/<bd>`.
  - El puerto forma parte del destino (5432 si la URL no lo indica), así que autorizar un cluster no autoriza otro del mismo host con la misma BD.
  - `localhost`, `127.0.0.1` y `::1` no se consideran equivalentes para la autorización: cada uno necesita la suya.
- **URLs ambiguas bloqueadas:** solo se admiten los parámetros `sslmode`, `sslrootcert`, `channel_binding`, `connect_timeout` y `application_name`.
  - Cualquier otro se rechaza, entre ellos `?dbname=`, `database`, `host`, `hostaddr`, `port`, `service`, `servicefile`, `options` y `target_session_attrs`.
  - También se rechazan las listas de hosts, los sockets y las URLs sin host o sin BD.
- **Variables de libpq bloqueadas:** si está definida `PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS` o `PGPORT`, no se ejecuta ningún test destructivo.
- **Antes de cada operación destructiva** se comprueba que el engine y la configuración de Alembic apuntan exactamente a `<host>:<puerto>/<bd>` autorizado.
- **Comparación con desarrollo sin cambios:** sigue siendo conservadora (sin puerto, alias de localhost juntos, Neon directo y `-pooler` juntos), de modo que bloquea de más y nunca de menos.
- **Añadido en el checkpoint (regla de Modular):** si el servidor de tests es remoto y comparte host y puerto con el de desarrollo, se bloquea aunque la BD sea otra. No se aplica a servidores locales.

## Reintentos y límites del proveedor (`410f881`)

**Estado: aplicado en esta rama** en el commit `410f881` ("fix: add provider retries and rate limit fail-fast"), ya subido a `origin/feature/data-integrity-sync`. Pendiente de merge a `main`.

**Reintentos** (en `ApiFootballProvider._get()`; el adapter de odds no los recibe):

| Error | ¿Reintento? | Espera |
| --- | --- | --- |
| Timeout, conexión, HTTP 500/502/503/504 | Sí | 1 s y 2 s |
| HTTP 429 y `errors.rateLimit` | Sí | Lo que pida `Retry-After` (segundos o fecha HTTP; una fecha vencida cuenta como 0), o 5 s y 10 s si no viene o no se puede interpretar |
| `errors.requests` (cuota diaria agotada) | No | — |
| 400, 401, 403, 404, otros 4xx, `token`, otros `errors`, JSON inválido, paginación | No | — |

- **Máximo 3 intentos** por llamada.
- **Presupuesto de espera:** como mucho 60 s acumulados por llamada a `_get()`. Si la siguiente espera lo superaría (por ejemplo, `Retry-After` 40 + 40, o un `Retry-After` de más de 60), no se espera y se lanza el error.
- **Clasificación nueva:** `ProviderQuotaExceededError` (subclase de `ProviderRateLimitError`) y `ProviderConnectionError` (subclase de `ProviderResponseError`). La API sigue respondiendo igual que antes.

**Corte de la sync** (igual en `sync_fixtures` y `sync_catalog`):

- **Rate limit persistente o cuota agotada:** las competiciones restantes ya no se piden al proveedor y quedan con el motivo en su error. En el catálogo se siguen guardando los datos de `/leagues` ya obtenidos; solo se dejan de pedir los `/teams`.
- **Timeout o 5xx agotados:** falla esa competición y la sync continúa con la siguiente.

## Credenciales rechazadas (`542c761`)

**Estado: aplicado en esta rama** en el commit `542c761` ("fix: stop sync on provider auth failures"), ya subido a `origin/feature/data-integrity-sync`.

HTTP 401, HTTP 403 y `errors.token` (todos `ProviderAuthError`) cortan la sync. No se reintentan.

- **`sync_fixtures`:** la competición que falla queda con su error, no se hacen más llamadas al proveedor y las competiciones restantes quedan con el motivo del corte. Lo ya guardado se conserva.
- **`sync_catalog`:**
  - Un error de credenciales en `/teams` corta los `/teams` restantes. Las competiciones restantes se siguen guardando con los datos ya obtenidos de `/leagues` y quedan con el motivo del corte.
  - Un error de credenciales en `/leagues` se sigue propagando como antes.
- **Errores que no son de credenciales:** un 404 o un error funcional de la API (por ejemplo, `errors.season`) no corta la sync: falla esa competición y se sigue con la siguiente.

## Checkpoint M4.2 (`ebcb076`)

**Estado:** Data Integrity y M4.2 (backfill histórico de Modular) integrados en `checkpoint/m42-data-integrity`, merge commit `ebcb076` ("merge: integrate M4.2 with data integrity"). Pendiente de promoción a `main`.

- **Migración `0005_season_backfill_runs`** validada sobre `0004`; la cadena `0004` → `0005` es única.
- **Política central de pares** (`fixture_repository.py`, tabla `_NULL_PAIR_RULES`):
  - **Par entrante completo:** se guarda tal como llega, aunque sea incoherente con `status_short` (por ejemplo, `penalty` completo en un FT). Es una anomalía explícita del proveedor que se audita, no se corrige en silencio.
  - **Pares atómicos:** nunca se mezcla un valor nuevo con uno viejo. `extratime_*` ya está protegido por pares atómicos, como `goals`, `halftime` y `penalty`.
  - **Par entrante con algún NULL:**

    | Par | Se limpia en | Se conserva en |
    | --- | --- | --- |
    | `goals`, `halftime` | `NS`, `PST`, `CANC`, `ABD`, `TBD` | el resto: terminados, adjudicados, en vivo, `SUSP`, `INT` |
    | `extratime` | los mismos estados y `FT` | `AET`, `PEN`, en vivo, `AWD`, `WO`, `SUSP`, `INT` |
    | `penalty` | todo estado que no sea `PEN` o `P` | `PEN` y `P` (tanda en juego) |
    | `fulltime` | todo estado que no sea `FT`, `AET` o `PEN` | `FT`, `AET`, `PEN` |

- **`fulltime` en el adapter:**
  - FT con `fulltime` ausente o a null: fallback a `goals`.
  - `fulltime` presente pero inválido o incompleto: `(None, None)`, sin fallback a `goals`.
  - AET y PEN nunca hacen ese fallback.
- **Una sola fuente de verdad:** el `ON CONFLICT` (`_fixture_update_values`) y la predicción en Python (`predict_score_values`) salen de la misma tabla de reglas. `predict_stored_values` y el check `PARITY` del backfill usan `predict_score_values`; el servicio de backfill no tiene una copia de la política.

## Verificación

**Verificación del checkpoint `ebcb076`: hecha.** Incluye todo lo de esta rama y M4.2.

- **Resultado de `pytest -q`:** 489 passed, 0 failed, 0 skipped.
- **Tests de BD:** los 220 se ejecutaron contra un PostgreSQL 18.6 local desechable, que escuchaba solo en `127.0.0.1:55432`.
- **Autorización usada:** `TEST_DATABASE_ALLOW_DESTRUCTIVE=127.0.0.1:55432/prediktia_tests`.
- **Tests dirigidos:**
  - `test_migration_0004.py::test_upgrade_backfill`: passed en ~0,9 s;
  - `test_migration_0005.py`: 11 passed;
  - `test_fixture_upsert.py`: 120 passed, incluida la matriz de paridad `predict_score_values` ↔ `ON CONFLICT`;
  - `test_history_backfill.py`: 16 passed, con `PARITY` validado;
  - `test_history_stale_runs.py`: 14 passed;
  - `test_sync_idempotency.py`: 11 passed.
- **Esperas:** ningún test espera de verdad. Un fixture sustituye solo la espera de `api_football` y registra los segundos pedidos.
- **Neon no se tocó:** no se modificó `.env` y no se usó ninguna URL de Neon para conectarse.
- **Cluster temporal:** detenido y su directorio eliminado al terminar.

Verificación anterior, sobre `542c761` en esta rama: 204 passed, 0 skipped, con 58 tests de BD. La guarda de `8c00609` se comprobó además con cuatro casos que debían bloquearse: sin autorización, otro puerto, formato antiguo sin puerto y alias `localhost`.

## Pendiente

- **Auditoría de pares completos incoherentes con el estado:** el upsert los guarda tal como llegan (por ejemplo, `penalty` completo fuera de `PEN`/`P`, o `extratime` completo fuera de `AET`/`PEN`). Falta un quality check que los señale, sin alterar el dato.
- **Reconciliación multi-proveedor de fixtures:** correlacionar IDs distintos de API-Football y 5Dollar con el fixture interno mediante `provider_mappings`, usando matching seguro por competición, equipos y kickoff, sin fuzzy matching automático cuando el resultado sea ambiguo.
- **Provider resilience / recovery:** el fail-fast ya está implementado (`410f881` para rate limit y cuota, `542c761` para credenciales), pero hoy un fallo del proveedor solo corta la ejecución en curso. No queda constancia persistente del fallo ni hay forma de recuperarse automáticamente.

  ```
  FAIL-FAST:         implementado
  RECOVERY / RESUME: pendiente
  INCIDENT HISTORY:  pendiente
  CATCH-UP:          pendiente
  ```

  Falta:
  - estado de salud persistente del proveedor;
  - registro persistente de incidentes y caídas;
  - timestamps de inicio, último fallo y recuperación de cada incidente;
  - tipo de error, código de estado y endpoint afectado;
  - número de intentos y competiciones afectadas;
  - `Retry-After` y/o `next_retry_at` cuando corresponda;
  - política de reintento posterior según el tipo de fallo;
  - circuit breaker o mecanismo equivalente para no golpear continuamente al proveedor;
  - detección de la recuperación del proveedor;
  - reanudación y catch-up automático desde el último punto seguro, para no dejar huecos de datos;
  - evidencia suficiente para auditar el SLA y reclamar al proveedor si hubo una caída.

  Esos registros nunca deben guardar secretos ni API keys. Todavía no hay diseño de tablas ni migraciones: solo se registra el pendiente.
- **Normalización de hosts de Neon:** solo contempla `-pooler`. Sería más robusto comparar por el id del endpoint.
- **Promoción a `main`:** el checkpoint `checkpoint/m42-data-integrity` (`ebcb076`) está validado y publicado, pero todavía no se ha integrado en `main`.
