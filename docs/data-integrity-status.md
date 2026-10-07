# Estado: integridad de datos y sync de partidos

Rama `feature/data-integrity-sync` (congelada en `61428e3`), base `298b2f3`. Integrada con M4.2 en `checkpoint/m42-data-integrity` (ver [Checkpoint M4.2](#checkpoint-m42-ebcb076)). Ver [workstreams.md](workstreams.md).

## Baseline reconciliado DI + Modular (vigente)

**Estado:** `integration/di-modular-m43-reconcile` es el baseline canónico de Data Integrity integrado con Modular. Lo que diga la rama (Git, migraciones, tests) manda sobre este documento.

- **Contenido:** Data Integrity `feature/data-integrity-next` @ `f6ec512` + Modular `feature/modular-data-m43` @ `b72b036` (merges reales, con el linaje de Modular). Commits: `d1f11cd` (reconciliación de M4.3 hasta `cbc00dd`), `015e545` (CLI `live_sync` sale con 2 si el proveedor no está configurado) y `1ee2f9d`, merge de `b72b036` (persistencia de estadísticas por lotes, solo Modular).
- **Validación del baseline:** suite completa 1055 passed, 0 failed, 0 skipped, contra PostgreSQL local desechable (nunca Neon).
- **Contratos compartidos acordados:**
  - `upsert_fixtures` devuelve `UpsertCounts(received, created, updated, unchanged)`; `CompetitionFixtureSyncResult.fixtures` = `received` (recibidos sin duplicados).
  - El ciclo de vida del proveedor es del run, nunca de cada competición: sin proveedor inyectado la sync abre el suyo con `async with`; uno inyectado es de quien lo creó (`app.jobs.live_sync` abre el run). Ninguna transacción de BD abierta durante el HTTP.
  - La clasificación de `sync_failures` (corte del run por credenciales, proveedor sin configurar, límite/cuota o conexión con la BD perdida) convive con el hook `on_competition`, la elegibilidad del polling y la revalidación de temporada de Modular.
  - `history_backfill_service.py` es de Modular en comportamiento.
- **Migraciones:** cabeza única `0007` (`0005` → `0006` Modular → `0007` Modular). Reservas: **`0008` = DI-A6**, **`0009` = DI-A5D**. Ninguna creada todavía.
- **Siguiente carril:** DI-A6 (evidencia temporal de fixtures), una vez cumplidas todas las puertas (suite verde, cabeza `0007`, documentación al día, rama de integración publicada). El contrato está ratificado ([DI-A6C](#di-a6c-contrato-de-evidencia-temporal-de-fixtures)). Implementación autorizada y en curso en `feature/data-integrity-a6`: ver [DI-A6: implementación](#di-a6-implementación-checkpoint-a). **DI-A5D: HOLD.**
- **Separación semántica obligatoria:** la evidencia temporal de estadísticas NO es evidencia temporal de fixtures. `observed_at` / `last_observed_at` de `fixture_statistics_observations` (Modular, M5) son un dominio aparte y no se reutilizan para `fixture_observations.observed_at`, `fixtures.last_observed_at` ni `fixtures.last_state_hash` (DI-A6).

## DI-A6C: contrato de evidencia temporal de fixtures

**Estado:** arquitectura **congelada y ratificada** por el Chief, con G1, G2 y G3 resueltas (ver [decisiones ratificadas](#a6c-decisiones-ratificadas-g1g3)). Implementación autorizada por el Chief; Checkpoint A hecho en `feature/data-integrity-a6` (ver [DI-A6: implementación](#di-a6-implementación-checkpoint-a)). Migración **`0008`** creada en esa rama. Este apartado es autosuficiente: una sesión nueva no necesita los transcripts para reconstruir el contrato.

Cada punto lleva su clase:

- **[FROZEN]**: invariante congelado por el Chief. No se rediseña.
- **[DELEGATED]**: especificación técnica delegada en la implementación. Se puede ajustar sin el Chief mientras respete los [FROZEN].
- **[CHIEF]**: decisión de arquitectura pendiente. Hoy no queda ninguna.

**Fuentes** (por orden de autoridad, de mayor a menor):

0. Ratificación final del Chief, "DI-A6C — Final Documentation Clarifications" (2026-10-06): arquitectura congelada y ratificada; G1 = AS_OBSERVED, G2 = MEASUREMENT_FIRST, G3 = OBSERVED_EVIDENCE_NOT_MERGED_STATE; confirma el significado de `last_state_hash`.
1. Asignación del Chief DI-A6C-FROZEN-CONTRACT-RECOVERY (2026-10-06): lista definitiva de invariantes y propuestas reemplazadas.
2. §13 "FROZEN DI-A6 SEMANTICS" de la asignación DI-MODULAR-FORWARD-MERGE-1 (2026-10-06).
3. Revisión del Chief "DI-A6C REVISION — FINAL TEMPORAL SCHEMA FREEZE" (2026-10-05): decisiones vinculantes y lista "DO NOT CHANGE".
4. Decisiones vinculantes de DI-A6B (2026-10-05).
5. Propuestas de DI-A6C (v1 y revisión, 2026-10-05). Solo valen en lo que las fuentes 1–4 confirman. Lo demás aparece abajo como [DELEGATED] o como reemplazado.

Las fuentes 2–5 solo existen en transcripts locales de Claude Code (`~/.claude/projects/C--Users-estef-OneDrive-Escritorio-prediktia/7708fd7c-….jsonl` y `6517c957-….jsonl`). Este registro las sustituye.

### A6C: invariantes congelados [FROZEN]

**Modelo:**

- `fixtures` es el **estado operativo actual**. Nunca es fuente de estado histórico ni de features a fecha T; en un backtest solo sirve para enumerar identidades candidatas.
- `fixture_observations` es **evidencia temporal inmutable**: solo se añaden filas, nunca se actualizan. Las escribe la capa de aplicación (repositorio), no triggers.
- `recorded_at` es metadato de persistencia (momento físico de la escritura). **Nunca** interviene en el orden temporal ni en qué entra en un backtest.
- `observed_at` es el momento del conocimiento: cuándo **recibió Prediktia** la evidencia, según el reloj de la app. No se inventan timestamps del proveedor.
- Las evidencias de backfill llevan el instante real de recepción, **nunca** la fecha del partido ni la de la temporada.
- **Contenido de la observación (G3):** `fixture_observations` guarda la **evidencia del proveedor normalizada** por el adapter, nunca el estado fusionado. La fusión de pares de marcador permitida pertenece **solo** al estado actual de `fixtures`. La evidencia histórica nunca se modifica para reflejar esa fusión.

**Identidad de la evidencia:**

- `evidence_id` (UUID) identifica **una respuesta lógica del proveedor**. Lo genera Prediktia, no el proveedor. Todos los partidos de esa respuesta lo comparten.
- Reprocesar exactamente la misma respuesta reutiliza su `evidence_id` y no crea filas nuevas.
- Cada respuesta válida independiente genera observaciones nuevas, **también las confirmaciones sin cambios**. Una confirmación es evidencia real, con su `observed_at`, `source` y `provider` reales. No se reconstruyen confirmaciones a posteriori.
- Restricciones e índice:
  - `UNIQUE (fixture_id, evidence_id)`.
  - `INDEX (fixture_id, observed_at)`.

**Procedencia:**

- `source` y `provider` se guardan siempre.
- `provider` es FK a `providers.code` con `ON DELETE RESTRICT`.
- `source` y `provider` no deciden la deduplicación.

**Snapshots y borrado:**

- `season_id`, `home_team_id` y `away_team_id` se copian en la observación como **enteros del momento**, sin FK viva.
- `fixture_id` es FK a `fixtures.id` con `ON DELETE RESTRICT`: no se puede borrar un fixture que tenga historia.

**Metadatos de orden en `fixtures`:**

- Se **mantienen** `fixtures.last_observed_at` y `fixtures.last_state_hash`.
- `fixtures.state_observed_at` **no existe**.
- El orden operativo canónico es `(observed_at, state_hash)`. La evidencia con la clave mayor es la **observación ganadora**: solo ella actualiza `fixtures`, con la fusión de pares permitida.
- `fixtures.last_state_hash` identifica el estado de la **observación canónica ganadora**. **No** es necesariamente el hash de la fila operativa de `fixtures` tras la fusión de pares.
- El hash es **canonicalización, no cronología**. Solo desempata de forma determinista; nunca establece qué evidencia es posterior.

**Escritura:**

- La inserción de la evidencia y la mutación del estado actual son **atómicas**, en la misma transacción.
- La evidencia más antigua puede añadirse a la historia, pero **nunca sobrescribe** un estado actual más nuevo.
- La corrección no depende del orden de commit, de bloqueos explícitos ni de leases.

**Ambigüedad y modos de evaluación:**

- Mismo fixture, mismo `observed_at` y `state_hash` distinto es una **`TEMPORAL_AMBIGUITY`**. Se conservan ambas observaciones; el hash mayor solo decide el contenido operativo de `fixtures`.
- **`STRICT_KNOWLEDGE`** clasifica cada fixture en un corte T:
  - sin observación con `observed_at <= T` → `UNKNOWN_AT_T`;
  - un solo `state_hash` distinto en `t* = max(observed_at <= T)` → `KNOWN`;
  - más de uno → `TEMPORAL_AMBIGUITY`.
- **Contenido de `KNOWN` (G1):** `STRICT_KNOWLEDGE` devuelve la evidencia observada **tal cual** en `t*`. Los marcadores que faltan **no** se sintetizan desde observaciones anteriores. Un estado puede ser temporalmente `KNOWN` y a la vez estar incompleto para evaluar un mercado; esa incompletitud es responsabilidad de quien consume el estado y no cambia el estado temporal.
- Por defecto, `STRICT_KNOWLEDGE` **excluye** los casos `UNKNOWN_AT_T` y `TEMPORAL_AMBIGUITY` y los **cuenta en el informe**. Nunca elige un ganador por hash ni recurre a `fixtures`.
- `RETROSPECTIVE_FINAL_RESULTS` es un modo de evaluación distinto, con su propia marca. Sus resultados nunca se mezclan con los de `STRICT_KNOWLEDGE`.

**Bootstrap:**

- Usa **un único instante real**, `bootstrap_at`. No toma `created_at`, `updated_at` ni `kickoff_at`.
- No fabrica conocimiento histórico: solo afirma que "este estado existía en Prediktia en `bootstrap_at`".
- Por debajo de `bootstrap_at`, todo es `UNKNOWN_AT_T`.

**Dominios separados:**

- La evidencia temporal de fixtures y la de estadísticas (`fixture_statistics_observations`, Modular) son dominios distintos. No se comparten columnas ni semántica.

**Rendimiento:**

- El paso a producción exige validación empírica del rendimiento. La arquitectura está aprobada independientemente de esa medición.
- **Medición primero (G2):** la aceptación para producción exige mediciones representativas de la línea base. Los criterios de aceptación y los umbrales numéricos se derivan de esa evidencia y se proponen explícitamente al Chief. Hoy **no hay ningún umbral aprobado**.

**Proceso:**

- A6 va antes que A5D (`0008` = A6, `0009` = A5D).
- `history_backfill_service.py` es de Modular en comportamiento.

### A6C: propuestas reemplazadas

| Propuesta (A6C v1 o revisión) | Estado | La reemplaza |
|---|---|---|
| `UNIQUE (fixture_id, observed_at, evidence_id)` | reemplazada | `UNIQUE (fixture_id, evidence_id)` más `INDEX (fixture_id, observed_at)` (Chief). La identidad es la respuesta, no el instante; la lectura temporal tiene su propio índice |
| Eliminar `fixtures.last_state_hash` (calcularlo al vuelo) | rechazada | el Chief mantiene `last_state_hash` como hash del estado de la observación ganadora. Por G3, ese estado puede no coincidir con la fila fusionada, así que no se puede recalcular desde `fixtures` |
| `fixtures.state_observed_at` | rechazada | ya no hace falta: toda respuesta crea observación, así que sobra el bit de "estado cambiado" |
| Observaciones sintéticas `source='reassertion'` | rechazada | las confirmaciones son observaciones reales (Chief: "do not reconstruct missing confirmations later") |
| Guardar observación solo si el estado cambia | rechazada | toda respuesta válida independiente es evidencia, también si no hay cambios |
| `UNIQUE (fixture_id, observed_at, state_hash)` y desempate por hash en el modo estricto (A6C v1) | rechazada | `evidence_id` y `TEMPORAL_AMBIGUITY` |
| Fusionar la evidencia antigua con su predecesora antes de guardarla (A6C v1 y revisión, camino `rejected`) | reemplazada | G3: la observación guarda la evidencia observada, nunca un estado fabricado |
| Guardar en la observación la fila fusionada devuelta por `RETURNING` (A6C v1 y revisión) | reemplazada | G3: OBSERVED_EVIDENCE_NOT_MERGED_STATE |
| Reconstruir en `STRICT_KNOWLEDGE` los marcadores que faltan aplicando la política de pares sobre observaciones anteriores (opción G1-b de `38cc7cb`) | rechazada | G1: AS_OBSERVED |
| Umbrales numéricos de rendimiento propuestos en `38cc7cb` (2× la línea base, HOT ≥ 90 %, hash < 10 %) | retirados | G2: MEASUREMENT_FIRST. Los umbrales se derivan de mediciones y se proponen después |

### A6C: esquema de `fixture_observations` (migración `0008`)

| Columna | Tipo | Nulo | FK / default | Clase |
|---|---|---|---|---|
| `id` | `BIGINT GENERATED ALWAYS AS IDENTITY` | PK | — | DELEGATED |
| `evidence_id` | `UUID` | NOT NULL | sin default (lo pone la app) | FROZEN |
| `fixture_id` | `INTEGER` | NOT NULL | `fixtures.id` ON DELETE RESTRICT | FROZEN |
| `observed_at` | `TIMESTAMPTZ` | NOT NULL | **sin default** (nunca `now()` de la BD) | FROZEN (semántica) |
| `recorded_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | FROZEN (semántica) |
| `source` | `TEXT` | NOT NULL | CHECK `IN ('sync','backfill','bootstrap')` | FROZEN que exista; valores DELEGATED |
| `provider` | `TEXT` | NULL | `providers.code` ON DELETE RESTRICT; CHECK `(source IN ('sync','backfill')) = (provider IS NOT NULL)` | FROZEN la FK; CHECK DELEGATED |
| `state_hash` | `BYTEA` | NOT NULL | CHECK `octet_length(state_hash) = 32` | FROZEN que exista; formato DELEGATED (A) |
| `kickoff_at` | `TIMESTAMPTZ` | NOT NULL | — | contenido aprobado |
| `status_short` | `VARCHAR(10)` | NOT NULL | — | contenido aprobado |
| `season_id`, `home_team_id`, `away_team_id` | `INTEGER` | NOT NULL | **sin FK** | FROZEN |
| `home_goals`, `away_goals`, `halftime_home/away`, `fulltime_home/away`, `extratime_home/away`, `penalty_home/away` | `INTEGER` | NULL | — | contenido aprobado |

- **Restricciones e índices:**
  - `UNIQUE (fixture_id, evidence_id)` [FROZEN].
  - `INDEX (fixture_id, observed_at)` [FROZEN].
  - Las tres reglas CHECK de `fixtures` sobre `fulltime` (par completo o vacío, solo en FT/AET/PEN, no negativo) [DELEGATED].
- **Columnas nuevas en `fixtures`:**
  - `last_observed_at TIMESTAMPTZ` [FROZEN].
  - `last_state_hash BYTEA` (32 bytes) [FROZEN].
  - Se añaden nullables, se rellenan en el bootstrap y pasan a `NOT NULL` en la misma migración [DELEGATED].
  - Ningún índice nuevo en `fixtures`, para que las actualizaciones sigan siendo HOT [DELEGATED].
- **Fuera del primer esquema:** `source='manual'`, porque no existe ningún camino manual. Cuando exista, irá en su propia migración, con `actor` y `reason`.

### A6C: especificaciones técnicas

#### A. Hash canónico [DELEGATED]

- **Definición única en SQL:** función `fixture_state_hash_v1(...)`, `IMMUTABLE`, creada en `0008`. La usan el upsert, el bootstrap y los tests.
  - La aplicación no reimplementa el hash. Solo hay una referencia en Python **dentro de los tests**, para los vectores de oro.
  - `v1` nunca se redefine: un cambio exige `fixture_state_hash_v2` y una migración.
- **Campos, en este orden fijo:** `kickoff_at`, `status_short`, `season_id`, `home_team_id`, `away_team_id`, `home_goals`, `away_goals`, `halftime_home`, `halftime_away`, `fulltime_home`, `fulltime_away`, `extratime_home`, `extratime_away`, `penalty_home`, `penalty_away`. Son exactamente las columnas de estado de la observación; no entran `round`, `status_long`, `elapsed`, `venue_*`, `referee` ni los timestamps de sistema.
- **Serialización:**
  - Se concatenan `'fixture_state:v1'` y los 15 campos, separados por `chr(31)` (US).
  - `kickoff_at` va como microsegundos desde el epoch UTC, en entero decimal: `(extract(epoch FROM kickoff_at) * 1000000)::bigint`. Así no depende de la zona horaria de la sesión. `extract` devuelve `numeric` exacto desde PostgreSQL 14.
  - Los enteros van en decimal base 10, con `-` si son negativos y sin ceros a la izquierda (`::text`).
  - `status_short` va como netstring, `octet_length(s) || ':' || s`, para que ningún carácter del proveedor pueda simular un separador.
  - NULL va como `\N`. No colisiona con ningún entero ni netstring, y es distinto de `0`.
- **Hash:** `sha256(convert_to(texto, 'UTF8'))`, que da un `bytea` de 32 bytes.
- **Comparación:** `bytea` se compara byte a byte (memcmp), sin depender de la collation. La clave de orden es la tupla `(observed_at, state_hash)`.
- **El hash no es cronología:**
  - Solo desempata el contenido operativo cuando hay el mismo `observed_at`.
  - En `STRICT_KNOWLEDGE`, varios hashes en `t*` dan `TEMPORAL_AMBIGUITY`, nunca un ganador.
- **Tests:**
  - Vectores de oro: entradas fijas con su hash hex esperado, incluidos NULL frente a `0`, todos los pares NULL y `status_short` con caracteres raros.
  - Paridad entre la referencia Python de los tests y SQL.
  - El mismo instante con distinta zona horaria de sesión da el mismo hash.
  - Cambiar cualquier campo cambia el hash.
  - Un test de que `v1` no cambia (los vectores de oro quedan congelados).
- **Precondición:** verificar la versión de PostgreSQL de Neon (al menos 14; el entorno de tests usa la 18.6).

#### B. Pares de marcador y respuestas parciales [DELEGATED dentro de G1 y G3]

Lo **[FROZEN]** de este bloque (G1, G3 y `last_state_hash`) está en los invariantes. Aquí solo se describe la mecánica propuesta.

- **`fixtures` no cambia:** mantiene la política de pares vigente (`_NULL_PAIR_RULES`, `predict_score_values`), es decir, pares atómicos, NULL no destructivo según el estado y sin fallback en AET/PEN. Se aplica **solo** cuando la evidencia entrante gana el orden `(observed_at, state_hash)` frente a la fila bloqueada. La base de la fusión es entonces estado acumulado con evidencia anterior, así que no se mezcla información futura.
- **La observación guarda la evidencia tal como se observó**, normalizada por el adapter. **Nunca** guarda la fila fusionada (G3, [FROZEN]).
  - Un par incompleto queda como NULL ("no informado en esta respuesta").
  - Así ninguna observación contiene un estado que el proveedor no entregó, ni siquiera en el camino de evidencia antigua.
  - Desaparece la aproximación residual de A6C (fusionar contra un predecesor leído de una instantánea concurrente).
- **`state_hash` de la observación** se calcula sobre lo observado.
- **`fixtures.last_state_hash`** es el `state_hash` de la observación ganadora, la que fijó `last_observed_at` ([FROZEN]). **No** es necesariamente el hash de la fila fusionada. Por eso no se puede recalcular desde `fixtures` y hay que guardarlo, y por eso el desempate compara evidencia con evidencia.
- **Constraints:** la observación repite los CHECK de `fulltime` de `fixtures`. Un par completo incoherente con el estado se guarda tal cual, como hoy, y es un hallazgo para un quality check (pendiente ya registrado).
- **Concurrencia:** la fusión operativa usa la fila que el `ON CONFLICT DO UPDATE` bloquea y vuelve a evaluar (READ COMMITTED). La observación no depende de ninguna lectura previa.
- **Lectura estricta (G1):**
  - `STRICT_KNOWLEDGE` devuelve los pares NULL de la observación en `t*` tal cual ([FROZEN]).
  - **Propuesta [DELEGATED]:** que la consulta exponga un indicador `partial_pairs` (algún par NULL en un estado en el que la política operativa lo conservaría), para que quien evalúe un mercado pueda descartarlo. Ese indicador no cambia el estado temporal `KNOWN`.

#### C. Identidad de la evidencia [DELEGATED]

- **Valor `FixtureEvidence(evidence_id, observed_at, source, provider)`**, inmutable. Lo crea el servicio llamante **justo después** de que `await provider.get_fixtures(...)` devuelva con éxito (reintentos incluidos) y **antes** de abrir la transacción de escritura.
  - `evidence_id = uuid4()`.
  - `observed_at = datetime.now(timezone.utc)`.
  - Capturarlo después del parseo solo puede retrasar `observed_at`, nunca adelantarlo a antes de la recepción, así que es conservador.
  - `/fixtures` de API-Football responde en una sola petición, sin paginación (`allow_paging=False`), así que una llamada es una respuesta lógica.
- **Una respuesta, un `evidence_id`:** todos los fixtures de esa respuesta lo comparten. Los duplicados por `external_id` dentro de la respuesta ya se eliminan antes del upsert.
- **Firma:**
  - `upsert_fixtures(db, season_id, fixtures, team_ids, provider, evidence)`.
  - `provider` en la observación = `evidence.provider`.
  - Si `evidence.provider` no coincide con `provider`, es un error de programación y se lanza `ValueError` sin escribir nada.
- **Repetición exacta:** misma respuesta con el mismo `evidence_id`.
  - La observación cae en `ON CONFLICT (fixture_id, evidence_id) DO NOTHING`.
  - `fixtures` no cambia, porque la clave de orden es igual, no mayor.
- **Reintento HTTP o nueva petición:** es una **respuesta nueva**, con `evidence_id` nuevo, y es una confirmación independiente.
- **Rollback de la transacción:** no queda nada. Si la misma respuesta en memoria se vuelve a aplicar, conserva su `evidence_id`.
- **Reutilización incoherente:** el mismo `(fixture_id, evidence_id)` con otro `observed_at`, `source`, `provider` o `state_hash` es corrupción.
  - Las filas en conflicto se vuelven a leer y se comparan.
  - Si hay discrepancia, se lanza `EvidenceIdentityConflict`. Es un error de programación: **no** se aísla por competición; se propaga.

#### D. Escritores concurrentes [DELEGATED]

- **Sentencia 1 (estado operativo):**
  - `INSERT … ON CONFLICT (external_id) DO UPDATE SET <fusión de pares>, <columnas no temporales>, last_observed_at = :observed_at, last_state_hash = H(observado), updated_at = CASE WHEN <alguna columna de datos cambia> THEN now() ELSE fixtures.updated_at END`.
  - Lleva `WHERE (fixtures.last_observed_at, fixtures.last_state_hash) < (:observed_at, H(observado))`.
  - Un fixture nuevo entra por INSERT con los metadatos ya fijados.
- **Sentencia 2 (evidencia):** `INSERT INTO fixture_observations … ON CONFLICT (fixture_id, evidence_id) DO NOTHING` para **todos** los fixtures de la respuesta, gane o no la sentencia 1. Va en la misma transacción. Pueden ir en una CTE o en dos sentencias.
- **Atomicidad:** las dos sentencias van en la transacción que ya existe por competición (sync) o por par (backfill). El rollback deshace ambas.
- **Orden:**
  - El `WHERE` se vuelve a evaluar sobre la última versión confirmada de la fila bloqueada. Así el estado final es el de mayor `(observed_at, state_hash)`, sea cual sea el orden de commit.
  - La evidencia antigua solo añade la observación.
  - Con el mismo `observed_at` y distinto hash, gana el hash mayor en `fixtures` y ambas quedan en la historia (`TEMPORAL_AMBIGUITY`).
- **Aislamiento:** los escritores usan **READ COMMITTED**, el valor por defecto. Con REPEATABLE READ o SERIALIZABLE, el conflicto daría un error de serialización. Si algún día se usan, hay que reintentar la unidad completa **con el mismo `evidence_id`**.
- **Tests de regresión de carreras** (BD real, dos conexiones):
  - nueva confirma antes que antigua, y al revés;
  - mismo instante con hashes distintos en ambos órdenes;
  - repetición concurrente del mismo `evidence_id`;
  - fixture nuevo insertado por dos escritores;
  - rollback de uno de ellos.

  Criterio: el estado final de `fixtures` y el conocimiento a fecha T no dependen del orden de commit.

#### E. Adopción en el backfill de Modular [DELEGATED; requiere handoff]

Modular conserva la propiedad del comportamiento. DI define el contrato y Modular lo adopta en su rama; DI no modifica la rama de producción de Modular. La firma nueva tiene que llegar **a la vez** a la sync y al backfill (un único cambio de `upsert_fixtures` sin camino legado), porque un escritor sin evidencia rompería el invariante.

- **Contrato que Modular debe adoptar en `history_backfill_service.py`:**
  - Crear `FixtureEvidence(uuid4(), now_utc, 'backfill', provider.name)` justo después de que `get_fixtures` vuelva con éxito (paso 3), antes de `lock_run_status`, y pasarlo a `upsert_fixtures`.
  - **No** usar el `now` del run, la fecha de la temporada ni el kickoff.
  - Un `evidence_id` por par competición–temporada.
  - Dry-run, `blocked` y rollback no dejan observaciones, porque van en la misma transacción.
  - `PARITY` (`_parity_mismatches` y `predict_stored_values`) tiene que predecir con la regla de orden: si `(observed_at, hash)` no supera `(last_observed_at, last_state_hash)` previos, lo esperado es la fila previa sin cambios.
  - Comprobación nueva: hay exactamente una observación por fixture recibido con ese `evidence_id`.
  - `_would_change` y `result.changed` siguen midiendo cambios de datos.
- **`UpsertCounts`:** los cuatro campos y su semántica se mantienen.
  - `created`: filas nuevas.
  - `updated`: filas existentes cuyos **datos** cambiaron.
  - `unchanged`: el resto (confirmaciones, evidencia antigua y repeticiones).
  - `received` no cambia.
  - Contadores nuevos, como `observations_inserted` o `stale_rejected`, solo con default y acordados con Modular.
  - Los contadores son informativos: la corrección depende solo del `WHERE`.
- **Tests de paridad exigidos:** refresh igual a lo previsto con observaciones; refresh concurrente con una sync más nueva (`blocked` y rollback limpio, sin observaciones huérfanas); dry-run sin observaciones; repetición del mismo par sin duplicar observaciones.
- **Cambios que exigen handoff entre departamentos:**
  - `history_backfill_service.py` (Modular).
  - Los tests de Modular que borran fixtures con historia: `tests/test_migration_0007.py::test_deleting_a_fixture_cleans_its_statistics` y `tests/test_statistics_batch_equivalence.py::_fresh`. Con `RESTRICT` necesitan borrar antes las observaciones o crear fixtures sin evidencia.
  - Cualquier cambio de `UpsertCounts`.
  - La interacción del `CASCADE` `competitions → seasons → fixtures → estadísticas`: queda bloqueado en cuanto un fixture tenga observaciones, que es el comportamiento buscado.
- **Lado DI (sin handoff):**
  - `fixture_sync_service.py` (`source='sync'`).
  - `fixture_repository.py`.
  - Modelo y migración `0008`.
  - `tests/test_provider_mappings.py` (borra un fixture para probar el `CASCADE`).
  - La limpieza de `tools/perf_lab` (`benchmark.py` y `profiler.py`).

#### F. Aceptación de rendimiento [mediciones DELEGATED dentro de G2]

- **Mediciones obligatorias:** línea base `8510b77` frente a A6, en la BD del laboratorio y nunca en producción.
  - **Escenarios:** 1 000, 100 000 y 1 000 000 de fixtures; lote de una temporada (~380) y los tamaños del laboratorio; respuestas con 100 % sin cambios, 10 % con cambios, 100 % con cambios, 100 % fixtures nuevos y 100 % evidencia antigua.
  - **Tiempos, por lote:** tiempo total; construcción y compilación de SQLAlchemy; ejecución SQL; latencia del commit.
  - **Escrituras, por lote:** filas insertadas en observaciones; filas actualizadas en `fixtures`; filas de mappings.
  - **Recursos, por lote:** bytes de WAL (diferencia de `pg_current_wal_lsn()`); proporción de actualizaciones HOT en `fixtures` (`n_tup_hot_upd / n_tup_upd`); crecimiento de tabla e índices de `fixture_observations`; coste de CPU de `fixture_state_hash_v1` (microbenchmark y su parte del tiempo de ejecución).
- **Escrituras esperadas por fixture:**

  | Caso | Hoy | A6 |
  |---|---|---|
  | Sin cambios | 0 actualizaciones en `fixtures` y 1 en el mapping | 1 actualización en `fixtures` (metadatos, HOT), 1 inserción en observaciones y 1 en el mapping |
  | Con cambios | 1 actualización en `fixtures` y 1 en el mapping | 1 actualización, 1 inserción y 1 en el mapping |
  | Evidencia antigua | 1 actualización (**hoy la evidencia antigua sobrescribe**) y 1 en el mapping | 0 actualizaciones, 1 inserción y 1 en el mapping |
  | Fixture nuevo | 1 inserción y 1 en el mapping | 2 inserciones y 1 en el mapping |

- **Umbrales numéricos (G2, MEASUREMENT_FIRST):** **ninguno aprobado**. Primero se mide la línea base de forma representativa. Después se proponen al Chief criterios y umbrales **derivados de esas mediciones**, con su justificación.
- **Entorno:** DI-A3D está estacionado ("execution environment blocked"), así que hay que acordar dónde se mide. Es una puerta de coordinación, no de arquitectura.

#### Bootstrap [DELEGATED dentro de lo FROZEN]

Dentro de `0008`, después del DDL:

1. Añadir las columnas nullables. El bloqueo `ACCESS EXCLUSIVE` dura hasta el commit.
2. Capturar un único `bootstrap_at = datetime.now(timezone.utc)` y un `evidence_id`, ambos en Python y después del bloqueo.
3. Insertar una observación por fixture: `source='bootstrap'`, `provider=NULL`, `observed_at=bootstrap_at` y `state_hash=fixture_state_hash_v1(fila)`.
4. `UPDATE fixtures SET last_observed_at = bootstrap_at, last_state_hash = <mismo hash>`.
5. `SET NOT NULL`.

- **Requisito operativo:** ejecutar la migración con las syncs y los backfills parados.
- **`downgrade`:** borra la tabla, las columnas y la función.

### A6C: decisiones ratificadas (G1–G3)

Ratificadas por el Chief el 2026-10-06. Todas son **[FROZEN]** y ya están en los invariantes.

- **G1 = AS_OBSERVED.** `STRICT_KNOWLEDGE` devuelve la evidencia realmente observada.
  - Los marcadores que faltan no se sintetizan desde observaciones anteriores.
  - Un estado puede ser temporalmente `KNOWN` y estar incompleto para evaluar un mercado.
  - Las políticas de `UNKNOWN_AT_T` y `TEMPORAL_AMBIGUITY` no cambian.
- **G2 = MEASUREMENT_FIRST.**
  - La aceptación para producción exige mediciones representativas de la línea base.
  - Los criterios y los umbrales se derivan de esa evidencia y se proponen explícitamente.
  - No existe ningún umbral aprobado previamente.
- **G3 = OBSERVED_EVIDENCE_NOT_MERGED_STATE.**
  - `fixture_observations` conserva la evidencia normalizada del proveedor.
  - La fusión operativa de pares pertenece solo a `fixtures`.
  - La evidencia histórica nunca se modifica para reflejar esa fusión.
- **`last_state_hash` (confirmado):**
  - Identifica el estado de la observación canónica ganadora, no necesariamente el de la fila fusionada.
  - El orden canónico es `(observed_at, state_hash)`.
  - El hash desempata de forma determinista; no establece cronología.

**No quedan decisiones de arquitectura abiertas.** Si la implementación encuentra un conflicto con un invariante [FROZEN], se escala al Chief; nunca se cambia el contrato en silencio.

**Puertas de coordinación (no son de arquitectura):**

- handoff con Modular (E);
- rama de integración publicada en origin;
- entorno de medición (F);
- versión de PostgreSQL de Neon (A);
- ~~autorización del Chief para empezar la implementación~~ (concedida; ver abajo).

## DI-A6: implementación (Checkpoint A)

**Estado:** Checkpoint A hecho y pendiente de revisión. Rama `feature/data-integrity-a6` (worktree `prediktia-di-a6`), base `9a5a5f2`, **sin push**. HEAD de implementación: `1920590`; este registro va en el commit de documentación siguiente. No se ha tocado `main`, `feature/data-integrity-next` ni la rama de Modular. **DI-A5D: HOLD** (`0009` sin crear).

- **Commits (DI):**
  - `9ea08aa`: migración `0008`, modelos, `FixtureEvidence` y escritor.
  - `c55fd2d`: sync.
  - `ec58d37`: lecturas para verificar escrituras.
- **Commits Modular-facing, marcados `[MODULAR-REVIEW]`:**
  - `7c41705`: `history_backfill_service.py`.
  - `1920590`: tests de Modular.
- **Migración `0008_fixture_observations`** (`0007 → 0008`, una sola cabeza):
  - función `fixture_state_hash_v1` (solo SQL, IMMUTABLE);
  - tabla `fixture_observations` con el esquema congelado;
  - `fixtures.last_observed_at` y `last_state_hash` NOT NULL, con CHECK de 32 bytes.
- **Bootstrap** (dentro de la transacción de la migración):
  - `lock_timeout` de 5 s desde el primer bloqueo y `LOCK TABLE fixtures`;
  - un `bootstrap_at` (reloj de la app, tomado después del bloqueo) y un `evidence_id`;
  - validación de recuentos antes de `SET NOT NULL`; el índice temporal se crea al final.
  - Cualquier fallo deshace todo y se puede volver a lanzar; con un escritor activo falla por `lock_timeout`.
  - **Solo se ha ejecutado en BD de tests:** nunca en Neon ni en producción.
- **Escritor (`upsert_fixtures(..., provider, evidence)`):**
  - una consulta SQL calcula los hashes;
  - el upsert lleva `WHERE (last_observed_at, last_state_hash) < (excluded…)`, con la fusión de pares de siempre, y `updated_at` solo se mueve si cambian datos;
  - una observación por partido con lo observado, con `ON CONFLICT (fixture_id, evidence_id) DO NOTHING`, y relectura que lanza `EvidenceIdentityConflict` si se reutiliza un `evidence_id` con otro contenido;
  - filas ordenadas por `external_id`;
  - `UpsertCounts`: los mismos cuatro campos y significados, calculados con una lectura previa (informativos).
- **Evidencia:** `FixtureEvidence.received(source, provider)` se crea justo después de que `get_fixtures` vuelva con éxito, fuera de toda transacción. Lo hacen la sync (`source='sync'`) y el backfill (`source='backfill'`).
- **Backfill (Modular-facing):** `PARITY` sigue la regla de orden y exige una observación de la evidencia por partido recibido. El resto del comportamiento de Modular no cambia.
- **Tests:**
  - **Nuevos (101):** `test_migration_0008` 23, `test_fixture_state_hash` 29 (con vectores de oro congelados), `test_fixture_observations` 33, `test_fixture_observations_concurrency` 8 (dos conexiones reales, con espera de bloqueo comprobada), `test_fixture_sync_evidence` 3 y `test_history_backfill_evidence` 5.
  - **Suite completa combinada:** 1156 passed, 0 failed, 0 skipped, 0 errors, en PostgreSQL 18.6 local desechable (`127.0.0.1:55443`). Nunca Neon.
  - **Mutaciones:** quitar el `WHERE` de orden rompe 8 tests; ignorar el orden en `PARITY` rompe 1.
- **Revisión de Modular:** **PENDIENTE.** El dueño de Modular tiene que revisar `7c41705` y `1920590`. Ningún test de estadísticas se ha debilitado; solo se pasa evidencia y se borra la evidencia antes de borrar fixtures.
- **Riesgos conocidos:**
  - **Límite de parámetros:** el INSERT de `fixtures` pasa de 22 a 24 parámetros por fila (la consulta de hashes usa 16 y la de observaciones 21), así que el techo de PostgreSQL (65 535 por sentencia) baja de ~2 970 a ~2 730 partidos por respuesta. Ya existía, no hay troceado, y se medirá en el Checkpoint C sin cambiar la semántica.
  - **`tools/perf_lab` no está adaptado a `0008`:** la siembra por COPY y las llamadas a `upsert_fixtures` lo rompen. Queda para el Checkpoint C.
  - **Mapeos:** `last_seen_at` del mapeo sigue avanzando con evidencia antigua (comportamiento previo, ajeno a A6C).
  - **Pendiente operativo:** versión de PostgreSQL de Neon (al menos 14).
- **Siguiente punto seguro:**
  - el Checkpoint B (lectura `STRICT_KNOWLEDGE` / `UNKNOWN_AT_T` / `TEMPORAL_AMBIGUITY` AS_OBSERVED y el modo `RETROSPECTIVE_FINAL_RESULTS` separado), una vez revisado el A;
  - en paralelo, la revisión de Modular.
- **Cierre de sesión (2026-10-06, HEAD `2dbbb82`, working tree limpio):**
  - **Verificado:**
    - commits `9ea08aa` … `2dbbb82` presentes;
    - desde `1920590` solo cambió este documento, así que la suite de 1156 sigue correspondiendo al código actual;
    - `0008` encadenada a `0007`, sin `0009`;
    - nada publicado: `feature/data-integrity-a6` no existe en origin;
    - worktree principal de DI en `f6ec512`, limpio.
  - **Reportado** (de la ejecución anterior, no repetido al cerrar): 1156 passed y las mutaciones.
  - **Pendiente:**
    - **Checkpoint B:** NO iniciado.
    - **Checkpoint C:** `perf_lab`, mediciones y techo de parámetros.
    - Revisión de Modular.
    - Versión de PostgreSQL de Neon.
  - **Hecho nuevo:** `origin/feature/modular-data-m43` ya está en `eb4c33c`, no en el `b72b036` que contiene esta rama (visto con `ls-remote`, sin fetch). Antes de la revisión de Modular o de cualquier integración hay que mapear esos commits hacia delante, sobre todo si tocan `history_backfill_service.py`, `upsert_fixtures` o las migraciones.

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
