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

- **Requisito operativo:** ejecutar la migración con las syncs y los backfills parados. Lista concreta tras el scheduler C6: [runbook de `0008`](#runbook-de-bootstrap-de-0008).
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

- ~~handoff con Modular (E)~~ (revisión de comportamiento **APROBADA**; ver [revisión de Modular](#revisión-de-modular-aprobada));
- rama de integración publicada en origin;
- entorno de medición (F);
- versión de PostgreSQL de Neon (A);
- ~~autorización del Chief para empezar la implementación~~ (concedida; ver abajo).

## DI-A6: implementación (Checkpoint A)

**Estado:** Checkpoint A **CERRADO** por el Chief (2026-10-07). Checkpoint B hecho: ver [DI-A6: Checkpoint B](#di-a6-checkpoint-b-lectura-temporal). Registro original del Checkpoint A: Rama `feature/data-integrity-a6` (worktree `prediktia-di-a6`), base `9a5a5f2`, **sin push**. HEAD de implementación: `1920590`; este registro va en el commit de documentación siguiente. No se ha tocado `main`, `feature/data-integrity-next` ni la rama de Modular. **DI-A5D: HOLD** (`0009` sin crear).

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
- **Revisión de Modular:** **APROBADA** (2026-10-07), junto con `7e4af3e`; ver [revisión de Modular](#revisión-de-modular-aprobada). Ningún test de estadísticas se ha debilitado; solo se pasa evidencia y se borra la evidencia antes de borrar fixtures.
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
    - ~~Revisión de Modular.~~ Aprobada después (2026-10-07); ver [revisión de Modular](#revisión-de-modular-aprobada).
    - Versión de PostgreSQL de Neon.
  - **Hecho nuevo:** `origin/feature/modular-data-m43` ya está en `eb4c33c`, no en el `b72b036` que contiene esta rama (visto con `ls-remote`, sin fetch). Antes de la revisión de Modular o de cualquier integración hay que mapear esos commits hacia delante, sobre todo si tocan `history_backfill_service.py`, `upsert_fixtures` o las migraciones. **Resuelto:** ver [reconciliación con `main`](#reconciliación-hacia-delante-con-main-a92e8a3).

### Reconciliación hacia delante con `main` (`a92e8a3`)

**Estado:** hecha en `feature/data-integrity-a6`, **sin push**. Nueva base hacia delante: **`origin/main@a92e8a3`** (PR #1 `integration/di-modular-c6`, que absorbe Modular `c3e2a23`, C6 scheduler y M5.6B reconciliador live de estadísticas; PR #2, entorno `production` en los workflows `ops-*`). Sin rebase: los commits de A6 conservan su identidad.

- **Auditoría previa** de `c3e2a23..a92e8a3`: linaje válido; `main` no toca `fixture_repository.py`, `fixture_sync_service.py`, `history_backfill_service.py`, modelos, esquemas ni migraciones. El único choque es semántico: dos helpers nuevos de tests de Modular llamaban a `upsert_fixtures` sin evidencia.
- **Merge real:** `3692b59` (`--no-ff`, sin conflictos).
- **Compatibilidad, marcado `[MODULAR-REVIEW]`:** `7e4af3e`. Solo `backend/tests/test_ops_tick.py` (`add_ft`) y `backend/tests/test_statistics_reconcile.py` (`add_fixtures`): pasan `make_evidence()` / `make_evidence(provider=PROVIDER)`, igual que `1920590`. Sin cambios de producción, estadísticas, scheduler ni escritores.
- **Migraciones:** cabeza única **`0008`** (`0008 → 0007`). **`0009`** sigue reservada para DI-A5D, sin crear.
- **Tests** (PostgreSQL 18.6 local desechable, `127.0.0.1:55443`; nunca Neon):
  - dirigidos (`test_ops_tick.py` + `test_statistics_reconcile.py`): 128 passed, 0 skipped;
  - **suite completa combinada sobre `7e4af3e`:** **1285 passed, 0 failed, 0 skipped, 0 errors, 1 warning** (`StarletteDeprecationWarning` de `fastapi.testclient`, ajeno). 1285 = 1156 de A6 + 129 tests que trae `main`.
- **Revisión de Modular:** **APROBADA** (ver abajo).
- **Checkpoint B:** HOLD al reconciliar; después autorizado y hecho (ver [Checkpoint B](#di-a6-checkpoint-b-lectura-temporal)). **DI-A5D:** **HOLD.**

#### Revisión de Modular: APROBADA

Revisión de comportamiento del dueño de Modular sobre el paquete `7c41705`, `1920590` y `7e4af3e` (diffs completos a nivel de línea), registrada por el Chief el 2026-10-07. **Sin conflicto semántico.**

| Punto | Resultado |
|---|---|
| `7c41705` (adopción de evidencia y `PARITY` en `history_backfill_service.py`) | **APROBADO** |
| `1920590` (tests de Modular: firma y `DELETE RESTRICT`) | **APROBADO** |
| `7e4af3e` (helpers de tests C6/M5.6B) | **APROBADO** |
| Semántica del backfill histórico preservada | **SÍ** |
| Propagación de la evidencia | **APROBADA** |
| `PARITY` consciente del orden `(last_observed_at, last_state_hash)` | **APROBADA** |
| Compatibilidad con `DELETE RESTRICT` | **APROBADA** |
| Garantías de los tests de Modular preservadas | **SÍ** |
| Hueco del test dedicado de rollback por error de BD | **ACEPTABLE**: sigue registrado como no bloqueante |

### Runbook de bootstrap de `0008`

Desde C6 hay un scheduler que escribe fixtures cada hora en producción. Antes de ejecutar el bootstrap de `0008` en un entorno operativo (nunca se ha ejecutado en Neon ni en producción):

1. **`PREDIKTIA_SCHEDULER_ENABLED`** (variable del repositorio en GitHub) **no puede valer `true`**. Es la única puerta de `ops-live`, `ops-catalog` y `ops-stats`, también para `workflow_dispatch`.
2. **`SYNC_ENDPOINTS_ENABLED`** debe ser **`false` o no estar definida** en la API desplegada (`POST /sync/catalog` y `/sync/fixtures`).
3. **Sin ejecuciones activas** de `ops-live`, `ops-catalog` ni `ops-stats`. La puerta no detiene un run ya en curso. `ops-stats` cuenta aunque no escriba fixtures: sus FK a `fixtures` chocan con el `LOCK TABLE`.
4. **Sin ejecuciones manuales** de `live_sync`, `history_backfill`, `statistics_reconcile` ni `statistics_backfill`.
5. **Comprobar que no hay nada en curso:** ningún run de GitHub Actions de `ops-*` en ejecución y ninguna fila `running` en `live_sync_runs`, `season_backfill_runs` ni `statistics_runs`.

**La quiescencia de escritores es obligatoria.** El `lock_timeout` de 5 s y el rollback de la migración son solo una protección de último recurso: evitan un estado a medias, pero no sustituyen a parar los escritores.

## DI-A6: Checkpoint B (lectura temporal)

**Estado:** Checkpoint A **CERRADO** (Chief, 2026-10-07). Checkpoint B **hecho** y después **CERRADO** por el Chief. Rama `feature/data-integrity-a6`, base `7aa25cc`, **sin push**. **Sin cambio de esquema:** cabeza única `0008`, `0009` sin crear (reservada para DI-A5D). Checkpoint C: ver [su sección](#di-a6-checkpoint-c-g2-medición-primero).

- **Commits:**
  - `17db2b1`: tipos de resultado (`app/schemas/fixture_knowledge.py`) y lectura `STRICT_KNOWLEDGE` (`app/repositories/fixture_knowledge_repository.py`).
  - `770255b`: tests de `STRICT_KNOWLEDGE` (`tests/test_fixture_strict_knowledge.py`, 20).
  - `e56d3d8`: modo `RETROSPECTIVE_FINAL_RESULTS` separado (`app/repositories/fixture_retrospective_repository.py`) y sus tests (`tests/test_fixture_retrospective_results.py`, 4).
- **`STRICT_KNOWLEDGE`** (`strict_knowledge(db, fixture_ids, cutoff)` y `strict_knowledge_at(db, fixture_id, cutoff)`):
  - el corte T es un instante con zona horaria (uno naive es `ValueError`) y es inclusivo: `observed_at <= T`;
  - lee **solo** `fixture_observations`: ni `fixtures`, ni `recorded_at` (viaja en el resultado solo como auditoría), ni `fixture_statistics_observations`;
  - por partido, `t* = max(observed_at <= T)`. Sin filas → `UNKNOWN_AT_T`; un solo `state_hash` en `t*` → `KNOWN`; más de uno → `TEMPORAL_AMBIGUITY`, sin ganador (el hash no es cronología);
  - `KNOWN` devuelve la fila observada en `t*` tal cual (G1, AS_OBSERVED): los pares parciales siguen NULL y nada se rellena desde observaciones anteriores ni desde `fixtures`. Si en `t*` hay varias respuestas con el mismo estado, se devuelven todas; `state` es una de ellas (el contenido es idéntico por hash; el orden por `evidence_id` solo estabiliza la salida);
  - `TEMPORAL_AMBIGUITY` conserva las filas en conflicto para auditoría, pero `state` es `None`;
  - **tipos explícitos:** `FixtureKnowledge` (con invariantes que impiden construir un `KNOWN` sin evidencia o un `UNKNOWN_AT_T` con `t*`) y `StrictKnowledgeReport`, ambos con `mode = STRICT_KNOWLEDGE`. Por defecto el conjunto utilizable es `known`; `UNKNOWN_AT_T` y `TEMPORAL_AMBIGUITY` quedan fuera y se cuentan (`counts`);
  - los partidos los enumera el llamador (`fixtures.id`); enumerar no aporta estado.
- **`RETROSPECTIVE_FINAL_RESULTS`** (`retrospective_final_results(db, fixture_ids)`): resultado final según el estado **actual** de `fixtures` (con la fusión de pares), solo para partidos en `FINAL_STATUSES`. Separación: otro módulo, otro tipo (`RetrospectiveFinalResult`, `mode = RETROSPECTIVE_FINAL_RESULTS`) y **sin parámetro de corte**. El módulo estricto no importa `Fixture` ni el modo retrospectivo, así que no puede recurrir a ellos. No se le ha dado más semántica que esa separación.
- **No implementado:** el indicador `partial_pairs` (propuesta [DELEGATED] de B). La incompletitud para un mercado sigue siendo responsabilidad del consumidor.
- **Consulta:** una sola sentencia para todos los partidos, con los ids como un único parámetro de array (sin `UNNEST`). Medido en una BD desechable con 1 000 000 de observaciones (100 000 partidos × 10) y una "temporada" de 380 partidos: las dos fases usan `ix_fixture_observations_fixture_observed` (bitmap index scan para `t*` e index scan por partido en `t*`), con 1,2 ms de ejecución. Es una comprobación del plan, no la medición G2 del Checkpoint C. No hacen falta índices nuevos.
- **Tests:**
  - **Nuevos (24):** clasificación con el corte incluido y excluido; gana el último `t*`; corte en otra zona horaria; mismo instante con el mismo estado (`KNOWN`) o con estados distintos (`TEMPORAL_AMBIGUITY`, en ambos órdenes de escritura); la ambigüedad solo afecta a su instante; marcadores parciales tal cual; sin fusión; **regresión explícita:** `fixtures` tiene hoy el marcador completo y `STRICT_KNOWLEDGE` devuelve la observación parcial anterior; repetición exacta sin falsa ambigüedad; evidencia antigua escrita después, ordenada por `observed_at` y no por id ni `recorded_at`; evidencia de estadísticas ignorada; una sola consulta sin `fixtures` ni `recorded_at` tras el `FROM`; informe por defecto; separación de los dos modos.
  - **Mutaciones** (aplicadas y revertidas): quitar el filtro del corte rompe 10 tests; usar `<` en vez de `<=` rompe 2; desempatar por el hash mayor rompe 4.
  - **Regresión de escritores** (evidencia, orden, atomicidad, concurrencia, paridad del backfill, Modular y `DELETE RESTRICT`): `test_fixture_observations`, `test_fixture_observations_concurrency`, `test_fixture_state_hash`, `test_migration_0008`, `test_fixture_sync_evidence`, `test_history_backfill_evidence`, `test_history_backfill`, `test_fixture_upsert`, `test_migration_0007`, `test_statistics_batch_equivalence`, `test_provider_mappings` y `test_live_sync_fixtures`: **365 passed**.
  - **Suite completa combinada sobre `e56d3d8`:** **1309 passed, 0 failed, 0 skipped, 0 errors, 1 warning** (`StarletteDeprecationWarning`, ajeno). 1309 = 1285 + 24. PostgreSQL 18.6 local desechable (`127.0.0.1:55443`); nunca Neon.
- **Riesgos y huecos conocidos:**
  - **Hueco aceptado, no bloqueante (sigue abierto):** no hay un test dedicado que demuestre que el camino de error de BD del backfill (`except _DB_ERRORS: db.rollback()`) no deja observaciones. Solo lo cubre el código.
  - Los riesgos del Checkpoint A siguen igual: techo de parámetros por respuesta, `tools/perf_lab` sin adaptar a `0008`, versión de PostgreSQL de Neon.
  - Todavía no hay ningún consumidor (generación de features o backtest) que use la lectura estricta. Cuando lo haya, tiene que usar `STRICT_KNOWLEDGE` y no `RETROSPECTIVE_FINAL_RESULTS`.
- **Siguiente punto seguro:** decisión del Chief sobre el Checkpoint C. Autorizado y hecho después: ver [Checkpoint C](#di-a6-checkpoint-c-g2-medición-primero).

## DI-A6: Checkpoint C (G2, medición primero)

**Estado:** Checkpoint B **CERRADO** (Chief). Checkpoint C **CERRADO** por el Chief; aceptación de A6 para producción: **HOLD** (ver [la decisión](#c10-decisión-del-chief-checkpoint-c-cerrado)). Registro original de la medición: rama `feature/data-integrity-a6`, **sin push**. **Sin cambios de producción, sin cambio de esquema** (cabeza única `0008`, `0009` sin crear) y **sin UNNEST**. Estas mediciones son **A6** y no se mezclan con la evidencia histórica de A3B/A3C/A3D (README de `perf_lab`). Las propuestas de C.8 fueron la base de los umbrales de desarrollo local aceptados en C.10.

- **Commits:** `1d36e9e` (laboratorio: compatibilidad con `0008` y suites `a6`; solo herramientas) y `228376b` (tests del laboratorio sin conexiones). Este registro va en el commit de documentación siguiente.
- **Qué se rompía en el laboratorio con `0008` y cómo se resolvió (sin tocar producción):**
  - `seed` hace COPY a `fixtures` sin `last_observed_at`/`last_state_hash` (NOT NULL): ahora rechaza un esquema `0008`; los laboratorios A6 se siembran en `0007` (`init --revision 0007`) y pasan a `0008` con el **bootstrap real** (`upgrade`), como en producción;
  - `repository_write` llamaba a `upsert_fixtures` sin evidencia: ahora pasa `FixtureEvidence.received(...)` por grupo cuando la firma lo pide;
  - la limpieza de `insert` borraba fixtures con historia: ahora borra antes la evidencia (`ON DELETE RESTRICT`).
- **Entorno:** PostgreSQL 18.6 local desechable (cluster nuevo en `127.0.0.1`, locale C, valores por defecto: `shared_buffers` 128MB, `work_mem` 4MB, `fsync`/`synchronous_commit`/`full_page_writes` on, autovacuum on), Windows 11, Intel i3-12100F, Python 3.13.4, SQLAlchemy 2.1.3, psycopg 3.3.6 (`prepare_threshold` 5) y venv aislado. Nunca Neon, sin llamadas a proveedores y sin migraciones de producción.
- **Línea base:** `9a5a5f2` (código de `backend/app` idéntico a `8510b77`; esquema `0007`; escritor sin evidencia), sacada con `git archive` y medida con **el mismo arnés** superpuesto. Es la comparación válida más cercana: mismo dataset, mismas cargas y mismo cluster. `older`, `tie` y `replay` no existen sin evidencia (NO APLICA).
- **Metodología:**
  - dataset determinista de A3B (temporadas de 380 partidos); 100 000 partidos (matriz completa) y 1 000 000 (subconjunto);
  - 30 muestras y 5 de calentamiento por caso, p50/p95 por interpolación, `VACUUM ANALYZE` antes de cada caso, un cliente, caché caliente;
  - un lote es un grupo de respuestas (una por temporada, ≤ 380), con una evidencia por respuesta y un COMMIT por lote;
  - por muestra y fuera del reloj: WAL (`pg_current_wal_insert_lsn`), contadores de la transacción (`pg_stat_xact_user_tables` leído antes y después **dentro** de la transacción, porque desde PG15 incluye contadores pendientes de transacciones anteriores) y validaciones de los invariantes A6 (el ganador es una observación; `older` no toca `fixtures`; `replay` no crea filas);
  - tiempos por familia de sentencia: SQLAlchemy antes del cursor y round-trip del cursor.
- **Procedencia:** cada JSON guarda el SHA-256 de `app/`, `alembic/` y `tools/perf_lab/`. Todos los informes A6 coinciden con lo confirmado en `1d36e9e` sobre `b46ca91`, y ninguno cambió durante su ejecución. JSON en `backend/tools/perf_lab/results/a6c-*.json` (locales, no versionados por la política de `1972577`).

### C.1 Escritor: A6 frente a la línea base

`total` = trabajo + COMMIT, p50 (p95) en ms; WAL p50 en bytes por lote. Escala 100 000:

| Lote | Modo | Base total | A6 total | A6/base | Base WAL | A6 WAL | WAL A6/base |
|---:|---|---:|---:|---:|---:|---:|---:|
| 10 | insert | 13,3 (16,3) | 20,5 (23,3) | 1,54× | 16 800 | 22 152 | 1,32× |
| 10 | update | 12,8 (14,5) | 20,9 (25,5) | 1,64× | 10 896 | 16 020 | 1,47× |
| 10 | unchanged | 12,9 (18,1) | 21,6 (27,4) | 1,67× | 9 356 | 15 244 | 1,63× |
| 100 | update | 51,6 (66,3) | 130,6 (171,4) | 2,53× | 49 076 | 102 688 | 2,09× |
| 100 | unchanged | 50,1 (61,8) | 126,2 (169,3) | 2,52× | 24 456 | 83 424 | 3,41× |
| 380 | insert | 184,5 (203,9) | 479,2 (533,2) | 2,60× | 462 624 | 678 272 | 1,47× |
| 380 | update | 174,7 (202,1) | 450,6 (475,7) | 2,58× | 221 536 | 424 300 | 1,92× |
| 380 | unchanged | 169,9 (200,5) | 488,6 (520,5) | 2,88× | 84 148 | 308 276 | 3,66× |
| 380 | mixed | 167,2 (196,4) | 452,1 (505,9) | 2,70× | 228 656 | 436 396 | 1,91× |
| 380 | older | — | 460,1 (503,6) | — | — | 276 988 | — |
| 380 | tie | — | 461,3 (518,5) | — | — | 270 516 | — |
| 380 | replay | — | 452,3 (511,0) | — | — | 85 188 | — |
| 1000 | update | 458,1 (521,5) | 1216,3 (1287,5) | 2,65× | 597 052 | 1 114 884 | 1,87× |
| 1000 | unchanged | 455,1 (512,5) | 1246,1 (1326,5) | 2,74× | 223 520 | 855 376 | 3,83× |
| 2000 | update | 932,3 (980,9) | 2500,9 (2582,0) | 2,68× | 1 218 648 | 2 692 304 | 2,21× |
| 2000 | unchanged | 913,5 (945,3) | 2491,0 (2574,5) | 2,73× | 446 940 | 2 346 224 | 5,25× |

Con 1 000 000 de partidos la proporción es la misma: lote 380 update 177,0 → 463,4 ms (2,62×), unchanged 177,7 → 459,0 ms (2,58×), insert 180,6 → 479,0 ms (2,65×); lote 1000 update 473,9 → 1212,0 ms (2,56×). Entre 100 000 y 1 000 000, el tiempo del escritor **no depende del tamaño de la tabla**.

- **Sobrecoste A6 (MEDIDO):** ~1,6× en lotes de 10 y **~2,5–2,9× desde 100**. Por respuesta de 380: **+280 ms** (de ~175 a ~455–490 ms). Todos los modos A6 cuestan casi lo mismo con 380 (450–490 ms), incluidos `older`, `tie` y `replay`, que no actualizan `fixtures`: el coste no está en el servidor.
- **Dónde está (MEDIDO por familia; update de 1000, p50 antes del cursor + cursor):** son nuevas la sentencia de hashes (128,6 + 48,0 ms) y el INSERT de observaciones (173,9 + 73,3 ms); el upsert de `fixtures` pasa de 187,5 + 64,0 a 206,4 + 68,3 ms. La mayor parte es **construcción y compilación en SQLAlchemy** de sentencias multi-VALUES de cientos de filas (sin caché, porque cada lote genera SQL distinto), no ejecución en PostgreSQL.
- **COMMIT:** 0,4–4,9 ms en todos los casos; no es un factor.

### C.2 Coste del hash

- **Servidor (MEDIDO con `EXPLAIN ANALYZE` con y sin el hash sobre las mismas filas; el coste por fila es DERIVADO):** `fixture_state_hash_v1` cuesta **5,7–6,6 µs por fila** (380 filas: 2,43 frente a 0,11 ms; 100 000: 686,5 frente a 22,1 ms). Es lineal con el tamaño.
- **En el escritor (MEDIDO):** la sentencia de hashes es el **14–19 %** del tiempo antes del COMMIT (p50; 8 % con lotes de 10). Con 380 filas son ~60 ms, de los que ~2,3 ms son el hash en sí; el resto es compilar y planificar un `UNION ALL` de N `SELECT` literales. El hash es barato; **la forma de invocarlo no**. No se ha cambiado: el hash es canónico y la invocación no se toca sin decisión.

### C.3 WAL, HOT y crecimiento

- **WAL por partido (MEDIDO, lote 380, 100 000), base → A6:**
  - confirmación sin cambios: 221 → **811 B**;
  - actualización: 583 → 1117 B;
  - inserción: 1217 → 1785 B;
  - solo A6: evidencia antigua 729 B, empate 712 B y repetición exacta 224 B (igual que la base: solo mappings).

  La confirmación es lo que más crece (3,7×; 5,3× con 2000), porque **cada respuesta escribe una observación y adelanta `last_observed_at`**, como fija el contrato (tabla de F).
- **HOT en `fixtures` (MEDIDO, no inferido):**
  - confirmaciones: 100 % con 10, 100 y 380; 99,95 % con 1000; **64 % con 2000** (100 000). Con 1 000 000: 93 % (380) y 95 % (1000). Baja cuando las mismas páginas se reescriben muchas veces seguidas sin VACUUM (`fillfactor` 100);
  - actualizaciones de datos: A6 igual que la base (380: 70 % frente a 66 %; 1000: 72 % frente a 73 %; 2000: 57 % frente a 69 %; con 1 000 000, 42 % frente a 39 % y 49 % frente a 50 %). A6 no empeora el HOT de las actualizaciones;
  - la línea base no actualiza `fixtures` en una confirmación; A6 sí (solo metadatos), casi siempre en HOT.
- **Crecimiento (MEDIDO como diferencia de tamaños por respuesta escrita):**
  - `fixture_observations`: ~190 B de heap y ~95–155 B de índices por observación (**~290–345 B en total**); en el bootstrap, 297 B por observación con 100 000 y 279 B con 1 000 000;
  - `fixtures` no crece en heap (HOT y espacio libre); sus índices solo crecen con inserciones y actualizaciones no-HOT;
  - `replay` no crece nada.

### C.4 Techo de parámetros (puerta obligatoria)

Una sola respuesta creciente (una llamada a `ensure_teams` + `upsert_fixtures`, siempre con rollback):

| | Mayor que funciona | Primer fallo | Parámetros en el mayor | Tiempo cerca del techo | Pico de asignaciones Python |
|---|---:|---:|---:|---:|---:|
| A6 | **2729** | **2730** (65 541 parámetros) | 65 517 | 3,8–5,9 s (2700); 4,4–6,9 s (2715–2729) | 114 MiB (2700) |
| Base | 2977 | 2978 (65 537 parámetros) | 65 515 | 1,4–1,5 s (2700–2977) | 66 MiB (2750) |

- **Modo de fallo (MEDIDO):** `psycopg.OperationalError: sending query and params failed: number of parameters must be between 0 and 65535`, en el cliente y **antes de enviar** el INSERT de `fixtures`, que es la sentencia más ancha (24 parámetros por fila; hashes 16, observaciones 21). La transacción se deshace entera: no queda nada a medias. En la sync se aísla como el fallo de esa competición.
- **Lotes del laboratorio:** con temporadas de 380, el máximo de parámetros por sentencia en cualquier lote fue **9141** (14 % del techo).
- **Memoria:** solo asignaciones Python (`tracemalloc`, en un pase aparte). RSS y memoria del servidor: NO MEDIDO.
- **Troceado:** **no es necesario** con las respuestas conocidas (temporadas de liga de ~380; algo más en ligas de 24 equipos o con fases). No hay evidencia de respuestas por encima de ~1000. **No se ha implementado.** Si algún día hiciera falta, trocear **solo las sentencias** dentro de la misma transacción y con el mismo `evidence_id`, manteniendo el orden por `external_id`, no cambiaría la atomicidad de la respuesta, la identidad de la evidencia, el rollback, `UpsertCounts` ni `PARITY`. Trocear la respuesta en varias transacciones o evidencias **sí** los cambiaría y exigiría revisión.

### C.5 Bootstrap de `0008` (para el runbook)

- **MEDIDO** (`alembic upgrade` completo, sin escritores): **5,65 s con 100 000** partidos y **62,3 s con 1 000 000** (~0,06 ms por partido, lineal).
- El bloqueo `ACCESS EXCLUSIVE` sobre `fixtures` dura todo ese tiempo, así que la ventana de quiescencia del [runbook](#runbook-de-bootstrap-de-0008) tiene que cubrirlo con margen.
- Tamaño creado de `fixture_observations`: 29,7 MB (100 000) y 279 MB (1 000 000).

### C.6 Lectura `STRICT_KNOWLEDGE`

Se mide `strict_knowledge()` real (consulta + construcción de los tipos) con ids aleatorios y tres cortes: antes de toda la historia (todo `UNKNOWN_AT_T`), la mediana de `observed_at` y después de todo (`KNOWN`, con un 10 % de `TEMPORAL_AMBIGUITY` en la historia ampliada). Historias: 100 000 partidos con ~0,7 M observaciones (1 por partido más las de las escrituras), los mismos con ~1,6 M (10 por partido tras la preparación) y 1 000 000 de partidos con ~1,15 M.

| Partidos pedidos | Corte | p50 (ms) | Notas |
|---:|---|---:|---|
| 1–10 | cualquiera | 0,7–1,1 | índice siempre |
| 380 | todo `UNKNOWN_AT_T` | 1,9–2,1 | índice siempre |
| 380 | `KNOWN` | 7–13 | p95 de hasta 149 ms (ver el riesgo) |
| 1000 | `KNOWN` | 19–30 | |
| 10 000 | todo `UNKNOWN_AT_T` | 22–31 | |
| 10 000 | `KNOWN` | 180–282 | ~55–60 % es construir los tipos en Python |

- **Riesgo de plan (MEDIDO; hallazgo principal de la lectura):**
  - con ≥ 380–1000 partidos y un corte poco selectivo, el **plan personalizado** elige un **Seq Scan de toda `fixture_observations`** con hash join: 54–185 ms con 0,7–1,6 M observaciones, y crece con la historia;
  - ese plan se usa en las primeras ejecuciones de cada conexión, antes de que psycopg prepare la sentencia, y en cualquier proceso de un solo uso. Las muestras crudas lo muestran: las 5 primeras de 380 tardan 115–143 ms y las siguientes 11–15 ms (plan genérico, búsqueda por índice);
  - causa: el planificador sobrestima los grupos del agregado (12 798 estimados frente a 380 reales);
  - **el índice existe y se usa: no falta índice ni esquema.**
- **Experimento de plan** (otra BD desechable con 100 000 partidos y 1,01 M observaciones; las variantes devuelven las mismas filas, comprobado fila a fila):
  - el plan genérico usa el índice en todos los casos (1000: ~4 ms; 10 000: ~36–39 ms);
  - una variante `LATERAL` no cambia nada (el planificador la aplana);
  - repetir el filtro en el lado externo arregla 1000 (78 → 7 ms con plan personalizado), pero no 10 000, y empeora el plan genérico con 10 000 (36 → 83–101 ms);
  - **no hay una corrección evidente y no se ha cambiado la lectura**, que es código de producción del Checkpoint B.

### C.7 Regresión semántica

- Las validaciones de cada caso se cumplieron en todas las muestras:
  - el estado ganador es siempre una observación real;
  - la evidencia antigua nunca sobrescribe;
  - la repetición exacta no crea filas ni actualiza;
  - el empate solo cambia `fixtures` una vez por instante; después, todas las respuestas pierden o empatan.
- **Suite completa combinada** tras los cambios de herramientas: **1323 passed, 0 failed, 0 skipped, 0 errors, 1 warning** (`StarletteDeprecationWarning`, ajeno). 1323 = 1309 + 14 tests nuevos del laboratorio.
- **Tests dirigidos del laboratorio:** 83 passed y 1 skipped (un test de BD, sin `TEST_DATABASE_URL` en esa ejecución).

### C.8 Propuestas para el Chief (registro previo a la decisión; ver C.10)

- **Línea base representativa:** una respuesta de una temporada (380), 100 000–1 000 000 de partidos, laboratorio local caliente y un cliente. Base ~175 ms; A6 450–490 ms p50 y 476–533 ms p95.
- **Carga normal esperada:** `ops-live` cada hora, con una respuesta de ~380 por liga seguida: ~0,46 s por liga (frente a ~0,18 s), casi siempre sin cambio de datos (confirmación).
  - **Crecimiento por confirmaciones (DERIVADO):** 380 observaciones × ~290 B ≈ 110 KB y ~308 KB de WAL por liga y hora; **~2,6 MB al día y ~0,96 GB al año de observaciones por liga activa** (~3,3 M filas al año).
  - Es el coste de guardar cada confirmación como evidencia, que el contrato exige. El contrato **no** define retención ni compactación.
- **Cargas patológicas:**
  - confirmaciones masivas (WAL 3,7–5,3× la base);
  - lotes de 2000 seguidos sobre las mismas páginas (HOT de confirmaciones al 64 %);
  - lecturas `KNOWN` de ≥ 1000 partidos en una conexión nueva (Seq Scan);
  - 10 000 partidos en una lectura (~0,2–0,3 s).
- **Cargas límite:** una respuesta de más de 2000 partidos (2,5–7 s y más de 60 MiB de Python); el techo duro es 2729.
- **Umbrales propuestos:** medidos en local, con caché caliente y un cliente. Neon tendrá otra latencia, así que hay que volver a medir allí antes de aceptar nada.

| Métrica | Propuesta | Evidencia | Por qué importa | Margen |
|---|---|---|---|---|
| Escritura de una respuesta de 380 (total) | p50 ≤ 600 ms, p95 ≤ 750 ms | p50 450–490, p95 476–533 (100 000 y 1 000 000) | presupuesto por liga de la pasada horaria | ~25 % sobre p50, ~40 % sobre p95 |
| Sobrecoste frente a la base (380) | ≤ 3,0× p50 | 2,58–2,88× | detecta regresiones del escritor | ~5–15 % |
| WAL por partido confirmado / actualizado | ≤ 1,0 KB / ≤ 1,4 KB p50 | 811 B / 1117 B | WAL y almacenamiento de Neon | ~25 % |
| HOT de confirmaciones (lotes ≤ 1000) | ≥ 90 % | 93–100 % | evita que crezcan los índices de `fixtures` | ~3–10 puntos |
| Almacenamiento por observación | ≤ 350 B (heap + índices) | 279–345 B | proyección de crecimiento | ~0–20 % |
| Partidos por respuesta | ≤ 2000 operativo; techo duro 2729 | ceiling | evita que falle la competición entera | 27 % bajo el techo |
| Bootstrap | ≤ 0,1 ms por partido, en quiescencia | 0,056–0,062 | ventana del runbook | ~40 % |
| Hash en el servidor | ≤ 10 µs por fila | 5,7–6,6 µs | detecta cambios de plataforma | ~35 % |
| Lectura estricta con ≤ 380 partidos | ≤ 15 ms p50 y **sin Seq Scan** en el plan personalizado | 7–13 ms; plan por índice ≤ 1,5 ms | features por temporada | ~15 %; **la condición de plan no se cumple con ≥ 1000** |

### C.9 Riesgos abiertos

- **Plan de la lectura estricta** (C.6): Seq Scan en planes personalizados con ≥ 1000 partidos y un corte poco selectivo. Opciones para decidir (ninguna implementada):
  - limitar cada lectura a ≤ 380 partidos dentro de **una** transacción `REPEATABLE READ` (varias consultas en `READ COMMITTED` podrían ver evidencia antigua insertada entre medias);
  - forzar el plan genérico en esa sentencia;
  - rediseñar la forma de la consulta, con más medición.
- **Crecimiento por confirmaciones** (C.8): ~1 GB al año por liga activa, sin política de retención en el contrato.
- **Coste en Python del escritor** (~2,6×): domina la compilación de sentencias multi-VALUES. Cualquier optimización (incluida UNNEST, DI-A3F) sigue **PLANNED** y necesita una decisión aparte.
- **No medido:** Neon/WAN, escritores concurrentes, caché fría, RSS y memoria del servidor.
- **Siguen abiertos:** el hueco aceptado y no bloqueante del test dedicado de rollback por error de BD en el backfill, y la versión de PostgreSQL de Neon.
- **Siguiente punto seguro (antes de la decisión):** decisión del Chief sobre los dos hallazgos y los umbrales. Decidido: ver C.10.

### C.10 Decisión del Chief (Checkpoint C CERRADO)

**Checkpoint C: CERRADO. Aceptación de A6 para producción: HOLD.** La línea base local de A6 está completa. Esta decisión sustituye a las propuestas de C.8 y a las opciones de C.9 donde difieran.

**Umbrales aceptados para desarrollo local** (mismo entorno que C.1–C.6; no valen como aceptación para producción):

| Métrica | Umbral aceptado |
|---|---|
| Escritura de una respuesta de 380 | p50 ≤ 600 ms; p95 ≤ 750 ms |
| Sobrecoste frente a pre-A6 | ≤ 3,0× |
| WAL por partido confirmado | ≤ 1,0 KB |
| WAL por partido actualizado | ≤ 1,4 KB |
| HOT de confirmaciones | ≥ 90 % con lotes ≤ 1000 |
| Almacenamiento por observación | ≤ 350 B |
| Respuesta operativa soportada | ≤ 2000 partidos |
| Techo duro de parámetros | 2729 partidos por respuesta |
| 2730 o más partidos por respuesta | no soportado con el escritor actual (VALUES) |
| Bootstrap | ≤ 0,1 ms por partido |
| Hash | ≤ 10 µs por fila |

**Política temporal de la lectura estricta:**

- **Tope temporal por ejecución:** ≤ 380 partidos en una sola llamada a `STRICT_KNOWLEDGE`.
- **Objetivo local en estado estable (sentencia preparada):** p50 ≤ 15 ms.
- **Sin troceado automático** todavía.
- **Sin rediseño de la consulta:** no está autorizado.
- **No hay criterio absoluto** de "ningún Seq Scan en ningún plan personalizado".
- Un Seq Scan persistente o patológico **sigue siendo un riesgo de rendimiento** (C.6).

**Puerta del entorno de destino:**

- Antes de aceptar A6 para producción hay que **volver a medir en el entorno de destino**.
- Solo en Neon no productivo aislado o equivalente, y **solo cuando se autorice**.
- **No** se autoriza usar datos de producción ni ejecutar la migración en producción.

**Crecimiento de la evidencia (hallazgo de C.8):**

- **Sin compactación, sin borrado y sin fusión de observaciones.** Las observaciones inmutables se conservan intactas.
- **Tema de arquitectura futuro:** particionado, archivado, ciclo de vida del almacenamiento y escalado de índices y almacenamiento.
- Cualquier política que destruya evidencia requiere una **revisión aparte del Chief**.

**UNNEST:**

- `UNNEST_USED`: **NO**.
- Adopción de UNNEST en producción: **NO AUTORIZADA en A6**.
- **DI-A3F** se puede reconsiderar por separado, ahora que existe la línea base de A6.

**Puertas abiertas de A6** (ninguna resuelta):

1. **Clasificación de `UpsertCounts` con escritores concurrentes.**
   - Validar que `created` / `updated` / `unchanged` son exactos con escritores que compiten.
   - Si se reproduce un comportamiento incorrecto, devolver la corrección más pequeña que preserve el contrato, para revisión del Chief.
2. **Hueco del test dedicado de rollback por error de BD** (backfill): aceptado como no bloqueante, pero sigue registrado.
3. **Nueva medición en el entorno de destino:** obligatoria antes de aceptar A6 para producción.
4. **Arquitectura de almacenamiento y particionado de la evidencia:** obligatoria antes de un despliegue amplio, prolongado y de alta frecuencia.

- **Siguiente punto seguro:** esperar la decisión del Chief sobre cuál de las puertas abiertas se aborda primero. DI-A6 **no** está aceptado para producción.

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
