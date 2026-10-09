# Estado: integridad de datos y sync de partidos

Rama `feature/data-integrity-sync` (congelada en `61428e3`), base `298b2f3`. Integrada con M4.2 en `checkpoint/m42-data-integrity` (ver [Checkpoint M4.2](#checkpoint-m42-ebcb076)). Ver [workstreams.md](workstreams.md).

## DI-A6: ensayo operativo de `0008` y runbook de producción (propuesta)

**Estado:** ensayo NO productivo **PASS** (2026-10-09). **Producción: `0008` NO ejecutada.** **PRODUCTION_READINESS: HOLD**, motivo `PRODUCTION_WRITER_INVENTORY_NOT_FULLY_RESOLVED`. No es un fallo de la arquitectura de A6, aceptada (**PASS**) por el Chief. Evidencia: `backend/tools/perf_lab/rehearsal_0008_evidence/`. Herramienta: `tools/perf_lab/rehearsal_0008.py`, solo para destinos NON_PRODUCTION verificados por `neon.branch_id`.

### Resultado del ensayo (rama `di-a6-0008-rehearsal`, NON_PRODUCTION)

| Paso | Resultado |
|---|---|
| Identidad (guarda fail-closed en cada conexión) | rama correcta, primario, PostgreSQL 18.6, pooled con `sslmode=require`, `0007`, 18 671 partidos y 18 671 mappings, `fixture_observations` ausente |
| Escribible | `transaction_read_only` off; DDL de prueba dentro de una transacción que se deshace: correcto y sin residuos |
| Grafo de migraciones | una sola cabeza `0008`, sin `0009`, camino `0007 → 0008` = `[0008]` |
| Quiescencia | 0 otras transacciones, xids de escritura, bloqueos sobre `fixtures` y transacciones preparadas; 0 runs `running` |
| Punto de recuperación | rama hija `di-a6-0008-rehearsal-checkpoint` creada por IT antes de migrar; **atestiguada por IT**, no verificable desde la sesión de BD; no se tocó |
| Migración `alembic -c alembic.ini upgrade 0008` | **COMMITTED_0008** en **7,30 s**; bootstrap 2,01 s; lector bloqueado **3,37 s**; WAL 25,1 MiB; +600 B por partido |
| Bootstrap | 18 671 = 18 671 = 18 671 con un único `evidence_id` y un único instante; hash recalculado desde la fila igual en el 100 %; 0 en todas las comprobaciones |
| Integridad | 0 huérfanas, 0 duplicados, 0 observaciones más nuevas que la ganadora; UNIQUE y FK `RESTRICT` presentes; mappings intactos |
| Prueba del escritor (20 partidos propios del ensayo) | **17/17**: inserción, repetición sin evidencia nueva, actualización, evidencia antigua que solo entra en la historia, rollback atómico y `DELETE` con historia rechazado. Filas reales intactas |
| Invariantes tras la prueba | **PASS** |

### Runbook propuesto para producción

**Responsables:**
- **CHIEF:** go/no-go y autoridad excepcional (rollback o restauración).
- **IT_SUPPORT_AGENT:** identidad del entorno, scheduler y disparadores, punto de recuperación e infraestructura.
- **CLAUDE_AGENT:** procedimiento de la migración y validaciones de BD.
- **DATA-INTEGRITY-USER:** confirmación del operador en las acciones manuales.

| # | Paso | Mecanismo real | Responsable |
|---|---|---|---|
| 0 | Go/no-go y franja | Elegir una franja **lejos de :00 (`ops-live`), :20 (`ops-stats`) y 04:50 UTC (`ops-catalog`)**, por ejemplo :35–:50 UTC | CHIEF |
| 1 | **Inventario de escritores de `fixtures` cerrado** (precondición, hoy **UNRESOLVED**) | En el código: `ops-live` y `ops-catalog` (→ `live_sync`), `ops-stats` (`statistics_reconcile`, FK a `fixtures`), los jobs manuales `live_sync`, `history_backfill`, `statistics_reconcile` y `statistics_backfill`, y la API `POST /sync/catalog` y `/sync/fixtures` (detrás de `SYNC_ENDPOINTS_ENABLED`). Falta confirmar en el despliegue que no hay más | IT_SUPPORT_AGENT + DATA-INTEGRITY-USER |
| 2 | Desactivar el scheduler | Variable del repositorio `PREDIKTIA_SCHEDULER_ENABLED` = `"false"` (Settings → Secrets and variables → Actions → Variables, o `gh variable set PREDIKTIA_SCHEDULER_ENABLED --body false`). Es la única puerta de los tres workflows, también para `workflow_dispatch` | IT_SUPPORT_AGENT (confirma DATA-INTEGRITY-USER) |
| 3 | Parar los demás escritores | `SYNC_ENDPOINTS_ENABLED` en `false` o sin definir en la API desplegada; ninguna ejecución manual de los jobs del paso 1 | IT_SUPPORT_AGENT |
| 4 | Quiescencia | (a) Ningún run de `ops-*` en curso (`gh run list --workflow ops-live.yml --status in_progress`, y lo mismo para `ops-stats` y `ops-catalog`; la puerta no para un run en marcha). (b) En la BD, solo lectura: los mismos recuentos de quiescencia del ensayo (otras transacciones, xids de escritura, bloqueos sobre `fixtures`, transacciones preparadas, filas `running`). Todo a 0; **si no, ABORT** | IT_SUPPORT_AGENT (a) + CLAUDE_AGENT (b) |
| 5 | Punto de recuperación | Rama hija de Neon (o restore point) de `production` **después** de la quiescencia y **justo antes** de migrar. Se registran su id saneado, la hora, la revisión `0007` y el recuento de partidos | IT_SUPPORT_AGENT |
| 6 | Precomprobación en BD | Revisión `0007`, recuento de partidos, `fixture_observations` ausente, una sola cabeza `0008` en el código (`alembic heads`), sin `0009`; rol con permiso para alterar `fixtures` | CLAUDE_AGENT |
| 7 | **Migración (ventana protegida ≥ 60 s)** | Desde `backend/` del árbol aceptado: `alembic -c alembic.ini upgrade 0008` con la `DATABASE_URL` de producción cargada de forma opaca. **Nunca `upgrade head`.** Es una sola transacción con `lock_timeout` de 5 s | CLAUDE_AGENT (ejecuta el operador que el CHIEF designe) |
| 8 | Validación del bootstrap y de la integridad | Las mismas consultas agregadas que `validate`, en solo lectura | CLAUDE_AGENT |
| 9 | Prueba controlada del escritor | **No se usan datos sintéticos en producción.** Es la primera escritura normal, acotada: `python -m app.jobs.live_sync fixtures --competition-id <una competición seguida> --confirm-target <destino saneado>`. Después, los invariantes agregados (`invariants`) | IT_SUPPORT_AGENT ejecuta, CLAUDE_AGENT valida |
| 10 | Reactivación | Ver el orden más abajo | IT_SUPPORT_AGENT (go del CHIEF) |
| 11 | Vigilancia del primer ciclo | El primer `ops-live` (:00) y el primer `ops-stats` (:20): conclusión del run, clasificación de `ops_tick`, `live_sync_runs` `completed` e invariantes de nuevo | IT_SUPPORT_AGENT + CLAUDE_AGENT |

**Duración prevista** (con los tiempos del ensayo):

| Fase | Tiempo |
|---|---|
| Desactivar el scheduler y la API | 1–5 min, más hasta 20 min si hay un run en curso (la franja lo evita) |
| Quiescencia | ~1–2 min |
| Punto de recuperación | ~1–2 min |
| Precomprobación | ~15–30 s |
| **Migración** | **7–8 s**, con lectores bloqueados ~3,4 s, dentro de la **ventana protegida reservada ≥ 60 s** |
| Validación | ~15–30 s |
| Prueba del escritor más invariantes | ~1–3 min |
| Reactivación | ~1 min |
| Primer ciclo | hasta ~60 min de vigilancia (hasta el siguiente :00 y :20) |

En total son **~10–20 min de operación activa**, más la vigilancia. La ventana de ≥ 60 s es la protección de la BD, no la duración del procedimiento.

### Límites de rollback y de recuperación hacia delante

- **Antes del COMMIT de `0008`:** la migración es una sola transacción. Un fallo, incluido un `lock_timeout` de 5 s con un escritor activo, la deshace entera y la BD queda en `0007`.
  - **Respaldo:** los tests `test_bootstrap_failure_rolls_back_everything_and_rerun_succeeds` y `test_bootstrap_fails_fast_while_a_writer_holds_fixtures` (39 passed en este árbol). Además, la herramienta clasifica el estado como `COMMITTED_0008`, `ROLLED_BACK_AT_0007` o `AMBIGUOUS`.
  - **Acción:** el scheduler y los escritores siguen parados. Se diagnostica y **no se reintenta a ciegas**: solo cuando la causa está clasificada y con el OK del responsable.
  - **`AMBIGUOUS` → ABORT** y escalado al CHIEF.
- **Después del COMMIT, antes de ningún escritor:** **recuperación hacia delante** por defecto, **sin downgrade rutinario**. El `downgrade` existe y está probado (`test_upgrade_downgrade_upgrade_0008`), pero usarlo solo lo decide el CHIEF.
- **Después de la primera escritura bajo `0008`** (la prueba del paso 9): es el **punto sin rollback rutinario**. Un downgrade borraría `fixture_observations`, es decir, la evidencia temporal nueva: **prohibido sin decisión explícita del CHIEF**.
- **Punto de recuperación del paso 5:** es un ancla de desastre, **no** un permiso para revertir tras una operación correcta. Restaurarlo pierde todo lo escrito después.

**Recuperación hacia delante (todos los casos):** escritores y scheduler parados → conservar el estado y la evidencia → diagnosticar → reparar hacia delante → revalidar → solo entonces reactivar.

| Caso | Respuesta |
|---|---|
| La validación falla tras el commit | Conservar; diagnosticar con consultas agregadas; corrección hacia delante autorizada por el CHIEF; revalidar |
| Recuentos del bootstrap distintos | No debería pasar: la migración lo valida y se deshace. Si aparece tras el commit, ABORT, escalado al CHIEF y decisión sobre el ancla |
| Falla la prueba del escritor | Su transacción se deshace entera (rollback atómico ensayado); el fallo queda en `live_sync_runs`; corregir y repetir la prueba. El scheduler sigue apagado |
| El scheduler no arranca | No toca la BD: revisar la variable, los runs y la clasificación de `ops_tick`; `workflow_dispatch` tras corregir |
| Fallo de proveedor o de sync tras reactivar | Manejo normal de C6 (corte por credenciales, guarda de cuota): un fallo no escribe nada; ninguna acción de esquema |

### Orden de reactivación

1. Migración PASS.
2. Bootstrap e integridad PASS.
3. Prueba del escritor PASS e invariantes PASS.
4. Sin errores de BD inesperados.
5. **Inventario de escritores de producción cerrado y bajo control.**
6. Permitir el camino que escribe `fixtures`: la API sigue con `SYNC_ENDPOINTS_ENABLED` en `false` salvo decisión aparte.
7. Verificar la primera escritura normal (la del paso 9).
8. `PREDIKTIA_SCHEDULER_ENABLED` = `"true"`.
9. Vigilar el primer ciclo programado.

No se activa nada ajeno: ni A5E, ni la recuperación de estadísticas, ni predicciones, ni funcionalidad nueva del scheduler. En el ensayo, la desactivación y la reactivación se **modelaron**: no se cambió ninguna variable ni ningún workflow de producción.

### Condiciones de ABORT

- clasificación distinta de NON_PRODUCTION en un ensayo, o un destino de producción no esperado;
- revisión inicial distinta de `0007`;
- un escritor que no se puede parar o no se puede demostrar en reposo;
- sin punto de recuperación;
- `0009` en el camino de la migración;
- estado de esquema ambiguo;
- recuento de partidos cambiado;
- observaciones de bootstrap distintas del número de partidos;
- cualquier violación de un invariante;
- prueba del escritor no atómica o incorrecta;
- necesidad de cambiar el código de la aplicación.

### Pendiente para levantar el HOLD

**OTHER_FIXTURE_WRITERS: UNRESOLVED.** IT_SUPPORT_AGENT debe confirmar con evidencia del despliegue que los escritores del paso 1 son todos, o completar la lista, y cómo se para cada uno. También hay que designar quién ejecuta los pasos 7 y 9 y desde dónde: un puesto con la credencial cargada de forma opaca, como en el ensayo, o un workflow puntual autorizado.

## DI-A6 G2 en destino (rama Neon NO productiva)

**Estado:** ensayo en destino completado (2026-10-09). **Recomendación: CONDITIONAL_PASS.** No autoriza la aceptación de A6 para producción (sigue en **HOLD**, decisión del Chief).

- **Destino:** rama `g2-di-a6-target` (NON_PRODUCTION, creada desde `production`, handoff de IT_SUPPORT_AGENT). Se conservó sin borrar.
- **Producción:** no se tocó. `0008` no se aplicó en producción y `0009` no está en este árbol.
- **Rama de trabajo:** `lab/data-integrity-a6-g2`, **sin push**.
- **Candidato:** `d31eebc` (escritor UNNEST de la opción A). Ningún cambio en la aplicación: solo herramientas de laboratorio (`tools/perf_lab/g2_target.py`).
- **Detalle y JSON:** `backend/tools/perf_lab/g2_evidence/target/`.

### Destino: identidad y representatividad

- **Identidad VERIFIED** (solo lectura): `neon.branch_id` igual al del handoff (se vuelve a comprobar en cada conexión nueva), primario, PostgreSQL 18.6, revisión `0007`, **18 671 partidos** y 18 671 mappings, `fixture_observations` ausente.
- **Entorno:**
  - endpoint pooled (PgBouncer de Neon) con `sslmode=require`;
  - `SELECT 1` con p50 de **139–149 ms** desde este cliente;
  - `fsync` y `full_page_writes` off: la durabilidad la da el almacenamiento de Neon;
  - autovacuum por defecto;
  - cliente Windows con Python 3.13.4, SQLAlchemy 2.1.3 y psycopg 3.3.6.
- **TARGET_REPRESENTATIVENESS: PARTIAL.**
  - **Reales:** el motor, el pooler, el SSL, la red desde este cliente, los datos de la migración y el autovacuum.
  - **No reales:** el runtime de C6 (Linux + Python 3.12), la ruta de red desde el host de producción, los datos del escritor (sintéticos) y la concurrencia (un solo cliente).

### Destino: `0007 → 0008` sobre los 18 671 partidos reales

| Medida | Valor |
|---|---|
| Migración completa (una transacción, incluida la conexión de Alembic) | **6,96 s** (16 sentencias, 4,10 s; el resto es conexión y arranque) |
| Bootstrap (LOCK + INSERT + UPDATE) | **1,84 s** (99 µs por partido) |
| Lector bloqueado (lectura más larga de la sonda) | **3,24 s** |
| WAL | 25,1 MiB |
| Crecimiento | +10,7 MiB (**600 B por partido**: `fixtures` +5,5, `fixture_observations` +5,2) |
| Resultado | `0008`; 18 671 partidos = 18 671 observaciones de bootstrap con un único `evidence_id`; 0 en todas las comprobaciones (sin observación, duplicados, ganador distinto, huérfanas, hash, columnas nulas) |

### Destino: escritor (dataset sintético aislado, sin VACUUM forzado)

Se creó un dataset sintético después de medir la migración (3800 partidos en rangos de ids reservados), con el escritor real. Se usó la misma metodología que Run 2: 7 modos, 30 + 5 muestras, tres pases. Ninguna escritura tocó filas reales.

| Lote | p50 / p95, pase C (estable) | p50 / p95, pase A | COMMIT p50 | Sentencias por muestra | WAL por partido (update / unchanged / insert) | HOT ≤ 1000 |
|---:|---|---|---:|---:|---|---|
| 380 | **1,93 / 2,29 s** | 2,02 / 5,91 s ¹ | 155 ms | 10 | 1261 / 1176 / 2095 B | 100 % |
| 1000 | **5,11 / 5,92 s** | 4,86 / 5,66 s | 155 ms | 30 | 1258 / 1180 / 2096 B | 100 % |
| 2000 (lote real 2000) | **10,05 / 11,38 s** | 9,62 / 10,89 s | 155 ms | 60 | 1271 / 1203 / 2098 B | (`update` 2000: 79 % en A, 97–100 % en B/C) |

¹ Los tres primeros casos de 380 del pase A fueron lentos: p50 de hasta 5,9 s, con un WAL 2–3× mayor por muestra que apunta a actividad simultánea del servidor. No se ha atribuido la causa. El resto del pase y los pases B y C están en 1,8–2,3 s.

- **Observaciones:** exactamente el lote en cada muestra (0 en `replay`). `UpsertCounts` exactos. Errores, timeouts y deadlocks: **0** (1890 muestras medidas; 3 rollbacks por pase, todos de conexiones de solo lectura del arnés).
- **Almacenamiento:** 301–324 B por observación.
- **Interrupción:** el primer intento del pase A se cortó al cerrarse la sesión del agente; su transacción se deshizo y la verificación posterior salió limpia. El pase se relanzó completo. Por eso el pase A del destino no es una primera escritura pura con 380; en el intento cortado, `update_380` tuvo un HOT del 44 %.

### Destino: HOT solo con autovacuum

| Pase | HOT del pase (`pg_stat_user_tables`) | autovacuum `fixtures` / `fixture_observations` | VACUUM manual |
|---|---:|---|---:|
| A | 91,3 % (370 416 / 405 541) | 29 / 10 | 0 |
| B | 97,5 % (340 638 / 349 541) | 22 / 4 | 0 |
| C | 97,5 % (340 644 / 349 541) | 25 / 2 | 0 |

**HOT_AUTOVACUUM_ONLY: ≥ 97 % en estado estable y 100 % por muestra con lotes ≤ 1000.** Cumple el umbral de ≥ 90 % sin el VACUUM del arnés, que en el destino estuvo desactivado.

### Destino: corrección (tras los tres pases)

- **Invariantes de A6, todos en 0:**
  - ganadores que no son una observación;
  - evidencia más nueva que la ganadora;
  - `(fixture_id, evidence_id)` duplicados (la UNIQUE está presente);
  - huérfanas;
  - partidos del laboratorio sin mapping;
  - hashes con longitud distinta de 32 B.
- **Evidencia:** solo el bootstrap abarca varias temporadas; ≤ 380 filas por evidencia de sync.
- **Filas reales:** solo su observación de bootstrap.
- **Por muestra:** `replay` sin evidencia nueva y `older` sin actualizar `fixtures`.
- **Prueba de rollback** con el escritor real: **atómica**, sin ningún cambio en recuentos ni en la huella de estado.

### Local (Run 2) frente a destino

| Lote | p50 / p95 local, pase C | p50 / p95 destino, pase C | Factor |
|---:|---|---|---:|
| 380 | 74 / 87 ms | 1,93 / 2,29 s | ×26 |
| 1000 | 202 / 230 ms | 5,11 / 5,92 s | ×25 |
| 2000 | 403 / 463 ms | 10,05 / 11,38 s | ×25 |

- **Casi todo es red.** Cada muestra son 10 / 30 / 60 sentencias más el COMMIT, y cada una paga un viaje de ~150 ms. El COMMIT es exactamente un viaje.
- **Lo que no es red:** el p50 menos `(sentencias + 1) × RTT` deja ~0,3 / 0,5 / 1,0 s para el servidor y el envío de los arrays (DERIVADO). El Python del cliente (SQLAlchemy antes del cursor) es menos del 0,5 % del total.
- **El coste crece con las sentencias, no con las filas:** el escritor manda un grupo de sentencias por temporada de la respuesta.
- **Un RTT distinto mueve la latencia en proporción.** Con la ruta real de C6 la latencia se escala con su RTT, aproximadamente `(sentencias + 1) × RTT + 0,3–1,0 s`. No se hereda ningún "p95 < 1 s".
- **Lo que no depende de la red** coincide con el local:
  - HOT en estado estable;
  - almacenamiento (301–324 frente a 305–324 B por observación);
  - bootstrap (99 frente a 66–74 µs por partido);
  - corrección.
- **WAL:** un 20–40 % mayor en Neon. Es otro almacenamiento, así que los umbrales de WAL son por entorno.

### G2: umbrales finales propuestos (para decisión del Chief)

Combinan G2 local (Run 2), G2 en destino y el comportamiento operativo. Las latencias van **en función del RTT medido** de la ruta real (`SELECT 1` p50) y, como referencia, en segundos para un RTT de ~150 ms.

| Métrica | Propuesta | Tipo | Motivo |
|---|---|---|---|
| Invariantes A6 (ganador = observación, sin evidencia nueva perdida, sin duplicados, `replay` sin inserciones, `older` sin sobrescribir) | 0 violaciones | **HARD_GATE** | Es el contrato; 0 en local y en destino |
| Errores, timeouts y deadlocks del escritor | 0 | **HARD_GATE** | 0 en 1890 + 1890 muestras; la concurrencia está cubierta por 24 tests |
| Respuesta máxima operativa | ≤ 2000 partidos (techo de seguridad 2729 sin cambios) | **HARD_GATE** | 2000 medido en destino: 11,4 s de p95 con 60 sentencias |
| p95 con 380 | WARNING > 20 × RTT (≈ 3 s); HARD > 50 × RTT (≈ 7,5 s) | **WARNING** / **HARD_GATE** | Destino estable 2,29 s ≈ 15 RTT. Los bloqueos `FOR UPDATE` de la opción A duran toda la transacción; más de 50 RTT indica un problema del servidor, no de la red |
| p95 con 1000 | WARNING > 50 × RTT (≈ 7,5 s); HARD > 100 × RTT (≈ 15 s) | **WARNING** / **HARD_GATE** | Destino 5,92 s ≈ 39 RTT |
| p95 con 2000 | WARNING > 90 × RTT (≈ 13,5 s); HARD > 180 × RTT (≈ 27 s) | **WARNING** / **HARD_GATE** | Destino 11,38 s ≈ 76 RTT; el HARD queda por debajo de un `statement_timeout` de minutos |
| Latencia del COMMIT | p50 ≈ 1 RTT; solo percentiles, nunca el máximo | **OBSERVATIONAL** | Destino 148–155 ms = 1 viaje; en local los picos coinciden con checkpoints |
| HOT con lotes ≤ 1000 bajo autovacuum, en estado estable | WARNING < 90 % | **WARNING** | Destino 100 % por muestra y 97,5 % por pase sin VACUUM manual |
| HOT tras la migración o en la primera escritura | se observa | **OBSERVATIONAL** | 33–44 % en la primera escritura (local y destino); se recupera solo con autovacuum |
| WAL por partido en estado estable | WARNING > 1,6 KB (Neon) / > 1,4 KB (local) en `update` | **WARNING** | Neon 1,26 KB y local 0,89 KB; umbral por entorno |
| Almacenamiento | WARNING > 350 B por observación | **WARNING** | 301–324 B en los dos entornos |
| Duración de `0007 → 0008` | WARNING > 30 s con el recuento real | **WARNING** | 6,96 s con 18 671 (red incluida); lineal con los partidos |
| Ventana de bloqueo de lectores | ventana planificada ≥ 60 s con syncs y lecturas paradas; WARNING si la lectura bloqueada supera 15 s | **WARNING** + requisito operativo | El `ACCESS EXCLUSIVE` bloquea `fixtures` toda la transacción: 3,24 s en destino |
| Bootstrap | observaciones = partidos y un único `evidence_id` | **HARD_GATE** | La migración lo valida y, si no, se deshace sola |
| Duración del bootstrap | WARNING > 0,5 ms por partido | **OBSERVATIONAL** / **WARNING** | 99 µs en destino y 66–74 µs en local |
| Crecimiento de almacenamiento | ~600 B por partido en la migración; ~0,3 KB por observación después | **OBSERVATIONAL** | Alimenta la arquitectura de retención y particionado, pendiente |

Estos umbrales sustituyen a la propuesta local de [la sección de laboratorio](#g2-propuesta-de-umbrales-para-producción-para-decisión-del-chief). Allí los límites de latencia (500 ms / 2 s con 380) no contaban el RTT real de Neon: con ~150 ms por sentencia, 380 ya tarda ~2 s solo de red.

### Destino: limitaciones

- **Cliente:** Windows y Python 3.13 desde la red de este equipo, no Linux + Python 3.12 en el host de C6. La latencia depende del RTT de la ruta real, que no se ha medido.
- **Escritor sobre datos sintéticos** (3800 partidos en una liga) junto a los 18 671 reales. La forma y los tamaños son reales; los valores de negocio, no.
- **Un solo cliente:** la contención está cubierta por los tests, no por esta medida.
- **Interrupción:** el pase A se relanzó tras un corte; los tres primeros casos de 380 fueron lentos sin causa atribuida.
- **TLS:** el `sslmode=require` del cliente se comprobó en la configuración. El servidor ve la conexión pooler → compute sin SSL; el tramo cliente → pooler no puede verse desde la BD.

### Destino: recomendación

**CONDITIONAL_PASS.** **A6_CHANGE_REQUIRED: NO.**
- **Corrección:** completa en datos reales (migración) y en el destino (escritor).
- **HOT bajo autovacuum:** ≥ 97 %.
- **Migración:** 7 s con el recuento real.
- **Latencia:** explicada por el RTT de Neon, sin anomalías del servidor.

Dos de las tres condiciones de la recomendación local quedan **resueltas** (HOT bajo autovacuum y ventana de `0008` con el recuento real). Queda una:

1. **Medir el RTT desde el host de C6 hasta Neon** (por ejemplo, `SELECT 1` en solo lectura) y aplicar los umbrales en múltiplos de RTT. Si el host está en la región de Neon, las latencias bajarán en proporción.

Además, antes de aplicar `0008` en producción, la decisión del Chief debe planificar la ventana operativa de lectores (≥ 60 s con syncs parados).

## DI-A6 G2 (laboratorio): rendimiento del escritor UNNEST y ensayo de `0007 → 0008`

**Estado:** evidencia de laboratorio recogida en dos ejecuciones: **Run 1** (2026-10-08, **STORAGE_PRESSURE_SUSPECTED**, latencias contaminadas por el entorno) y **Run 2** (2026-10-09, **POST_RESTART / STORAGE_PRESSURE_RELIEVED**, latencias de referencia); ver [Run 2](#g2-run-2-control-de-la-presión-de-almacenamiento). **Recomendación: CONDITIONAL_PASS.** No autoriza la aceptación de A6 para producción (sigue en **HOLD**). Rama `lab/data-integrity-a6-g2` (worktree `prediktia-di-a6-g2`), **sin push**. La base canónica sigue siendo `1c06ef9`. Nunca se tocó Neon; `0009` no está en este árbol. Detalle, entorno, metodología y JSON: `backend/tools/perf_lab/g2_evidence/`.

- **Candidato:** `d31eebc` = `1c06ef9` + 15 commits de Modular (M5.7–M5.9B) que **solo añaden** archivos. Verificado en el diff: sin cambios en migraciones, modelos, escritor, mappings, sync, backfill ni lecturas temporales, y el código añadido no escribe en la BD. **G2_CANDIDATE_MISMATCH: NO.** Se midió UNNEST (máximo 380 parámetros por sentencia).
- **Entorno:** PostgreSQL 18.6 local desechable con ajustes por defecto (`fsync` y `synchronous_commit` on), Windows 11, i3-12100F (4/8), 23,8 GB, SSD SATA, loopback sin pooler, Python 3.13.4. **Representatividad: LIMITED**: sin red hacia Neon, otro sistema operativo y otro runtime (C6 corre en Linux + Python 3.12), versión de PostgreSQL de Neon sin verificar.
- **Método:** arnés de Checkpoint C (camino real `ensure_teams` + `upsert_fixtures`), 100 000 partidos sintéticos sembrados en `0007` y migrados con el bootstrap real. Lotes 380 / 1000 / 2000 × 7 modos (`insert`, `update`, `unchanged`, `older`, `mixed`, `tie`, `replay`), **30 muestras + 5 de calentamiento** por caso, **tres pases**: A (justo tras el bootstrap), B y C (estado estable). Sin descartar muestras.

### G2 Run 2: control de la presión de almacenamiento

Después de Run 1 se supo que el SSD SATA estaba casi lleno durante las medidas y que la máquina llevaba varios días encendida. Run 2 repite **solo la evidencia sensible al rendimiento** tras reiniciar, con **107,6 GB libres de 930,4 GB (11,6 %)** antes de medir. Mismo commit `d31eebc`, mismo `g2.py`, cluster nuevo con los mismos ajustes por defecto, misma siembra, mismos 7 modos, 30 + 5 muestras, pases A/B/C y mismo orden. Detalle completo: `backend/tools/perf_lab/g2_evidence/COMPARISON.md`; JSON en `g2_evidence/run2/`.

| Lote | Pase | p50 Run 1 → Run 2 (ms) | Δ | p95 Run 1 → Run 2 (ms) | Δ |
|---:|---|---|---:|---|---:|
| 380 | A | 92 → 71 | −22,6 % | 120 → 93 | −23,1 % |
| 380 | C | 91 → 74 | −18,4 % | 121 → 87 | −28,1 % |
| 1000 | A | 232 → 199 | −14,1 % | 296 → 253 | −14,6 % |
| 1000 | C | 237 → 202 | −14,7 % | 296 → 230 | −22,1 % |
| 2000 | A | 480 → 396 | −17,4 % | 563 → 443 | −21,3 % |
| 2000 | C | 483 → 403 | −16,6 % | 602 → 463 | −23,2 % |

- **Pase B:** p50 −11 % / −14 % / −14 % y p95 −13 % / −6 % / −18 % (380 / 1000 / 2000).
- **Por modo:** 62 de las 63 combinaciones de modo, lote y pase bajan su p50 (−4,5 % a −29,5 %).
- **Máximos de Run 2:** 997 ms con 1000 en el pase B (COMMIT de 802 ms durante un checkpoint) y 1211 ms con 2000 en el pase C (una muestra aislada antes del COMMIT; el p95 de ese caso es 579 ms).
- **Dónde está la mejora:** antes del COMMIT, en la misma proporción en el cursor SQL (−12 % a −21 %) y en el trabajo de Python de SQLAlchemy antes del cursor, que es CPU del cliente sin E/S (−15 % a −22 %). **El COMMIT no mejora** (p50 1,2–4,6 ms en las dos ejecuciones; el p95 depende de los checkpoints).
- **Frente a C.12:** el p50 por modo del pase C pasa de 85–98 / 215–256 / 457–519 ms (Run 1) a **70–79 / 190–217 / 378–431 ms** (Run 2), frente a 66–78 / 181–210 / 362–413 en C.12. La diferencia del 20–25 % baja a **~0–5 %**.
- **WAL, HOT, almacenamiento y recuentos:** se reproducen. Hay dos excepciones con 2000 en los pases B y C, que dependen del estado de las páginas y van en ambos sentidos: el HOT varía (`update` B 99 → 62 %, `unchanged` C 100 → 89 %) y también el WAL de esos casos. Con 380 y 1000 el HOT de estado estable sigue en 99–100 %. 305–323 B por observación.
- **Migración `0007 → 0008`:** casi igual (100 000: 7,31 → 7,40 s, +1,2 %; 250 000: 18,37 → 17,92 s, −2,5 %; BD del escritor 8,02 → 6,58 s, −18 %, que en Run 1 era la más lenta). Siguen siendo 66–74 µs por partido, lector bloqueado toda la migración, mismo crecimiento (615 B por partido) y mismos recuentos de bootstrap.
- **Errores, timeouts y deadlocks:** 0. Los 5 rollbacks por pase son las conexiones de solo lectura del arnés, como en Run 1.

**Respuestas:**
- **PERFORMANCE_MATERIALLY_IMPROVED: YES** (latencia del escritor −11 % a −28 % en p50 y p95 en todos los lotes y pases; la migración y el COMMIT no cambian materialmente).
- **PREVIOUS_VARIANCE: REDUCED.** La diferencia frente a C.12 baja de 20–25 % a ~0–5 %.
- **RUN_1_LATENCY_CONTAMINATED: YES.** Las latencias del escritor de Run 1 quedan marcadas como contaminadas por el entorno. Sus WAL, HOT, almacenamiento, recuentos y corrección siguen siendo válidos (Run 2 los reproduce).

**Causa: no atribuida a la presión de almacenamiento.** La comparación muestra que la diferencia era del estado de la máquina en Run 1, porque el mismo código, datos y ajustes dan otra latencia. Pero el trabajo de CPU de Python sin E/S mejora tanto como la ejecución SQL, mientras que el COMMIT (`fsync`) y la migración (sobre todo E/S del servidor) no mejoran. Eso no encaja con una lentitud solo de disco, y el reinicio cambió a la vez el espacio libre y el tiempo encendida, así que no se puede separar cuál de los dos (u otro estado del host) la causó.

**Corrección de Run 2 (mínima):** por muestra, 1890 muestras sin violaciones (`UpsertCounts` exactos, `replay` sin observaciones ni actualizaciones, `older` sin actualizaciones, 380–381 parámetros por sentencia). Sobre toda la BD, 0 en todos los invariantes de A6 (ganador = observación, ninguna observación más nueva que la ganadora, sin duplicados, huérfanas ni partidos sin mapping, hashes de 32 B, solo el bootstrap multi-temporada). **FULL_SUITE_RERUN: NOT_REQUIRED**: ni la aplicación ni el arnés cambiaron.

### G2 Run 1 (STORAGE_PRESSURE_SUSPECTED): escritor (latencia total por respuesta, ms; 210 muestras por lote y pase = 7 modos × 30)

> **Latencias de Run 1 contaminadas por el entorno** (ver Run 2). Se conservan sin cambios; WAL, HOT, almacenamiento y corrección siguen siendo válidos.

| Lote | Pase | p50 | p95 | máx | p50 por modo | COMMIT p50 / p95 | WAL por partido (update / unchanged / insert / replay) | HOT update / unchanged |
|---:|---|---:|---:|---:|---|---|---|---|
| 380 | A | 92 | 120 | 212 | 79–103 | 1,7 / 17,0 | 1471 / 938 / 1783 / 222 B | 33 % / 85 % |
| 380 | C | 91 | 121 | 169 | 85–98 | 2,9 / 10,1 | 886 / 812 / 1744 / 222 B | 100 % / 100 % |
| 1000 | A | 232 | 296 | 585 | 211–261 | 1,9 / 23,2 | 1109 / 843 / 1779 / 227 B | 71 % / 97 % |
| 1000 | C | 237 | 296 | 386 | 215–256 | 2,5 / 15,1 | 894 / 822 / 1744 / 225 B | 100 % / 100 % |
| 2000 | A | 480 | 563 | 706 | 447–496 | 3,0 / 29,9 | 1121 / 901 / 1772 / 321 B | 73 % / 90 % |
| 2000 | C | 483 | 602 | 872 | 457–519 | 2,7 / 17,9 | 1100 / 840 / 1761 / 225 B | 100 % / 100 % |

- **Pase B** (convergencia), en el mismo rango: p95 106 / 284 / 561 ms. Su máximo con 2000 (970 ms) coincide con un COMMIT de 466 ms (probable checkpoint); por eso el COMMIT se juzga en percentiles, no en el máximo.
- **Observaciones por muestra:** exactamente el lote en `insert`, `update`, `unchanged`, `older`, `mixed` y `tie`; **0** en `replay`. `UpsertCounts` exactos en todos los casos (p. ej. `mixed` 2000 = 667/667/666).
- **Errores, timeouts y deadlocks:** 0 en los tres pases (2205 escrituras medidas; `pg_stat_database.deadlocks` = 0). Los 5–7 rollbacks por pase son conexiones de solo lectura del arnés, no escrituras fallidas: cada muestra comprobó sus aserciones.
- **Primera escritura frente a estado estable:** la latencia apenas cambia. Cambia el HOT: justo tras el bootstrap, que reescribe todas las filas de `fixtures`, `update` con 380 cae al 33 % y su WAL a 1,47 KB por partido; desde el pase B ambos se estabilizan (HOT ~100 %, ~0,9 KB). El HOT del estado estable se midió **con** el `VACUUM ANALYZE` que el arnés ejecuta antes de cada caso.
- **Frente a C.12 (mismo código del escritor):** aquí las latencias son un 20–25 % mayores (380: p50 79–103 frente a 66–78 ms). La diferencia está en la ejecución de sentencias, no en el COMMIT (~3 ms). Es variación de entorno y sesión, no atribuida. **Actualización (Run 2):** tras reiniciar la máquina baja a ~0–5 %; era del estado de la máquina en Run 1, sin poder separar la presión de almacenamiento del tiempo encendida.
- **Almacenamiento:** 305–324 B por observación, índices incluidos (~170–180 MiB por cada 584 440 observaciones de un pase). En estado estable, `fixtures` no crece (+0,01 MiB por pase).

### G2: ensayo de `0007 → 0008` (LAB REHEARSAL, no ensayo de producción)

| Partidos | Migración completa (una transacción) | Por partido | Lector bloqueado | Observaciones / `evidence_id` | Crecimiento |
|---:|---:|---:|---:|---|---|
| 100 000 | 7,31 s | 73 µs | 7,09 s | 100 000 / 1 | `fixtures` +30,7 MiB, `fixture_observations` +28,3 MiB (615 B por partido) |
| 100 000 (BD del escritor) | 8,02 s | 80 µs | 7,87 s | 100 000 / 1 | igual |
| 250 000 | 18,37 s | 73 µs | 18,18 s | 250 000 / 1 | 146,9 MiB (616 B por partido) |

- **Escala lineal** con el número de partidos. Recuentos antes y después idénticos (100 000 / 250 000). El `ACCESS EXCLUSIVE` bloquea a cualquier lector de `fixtures` durante toda la migración: hace falta una ventana con syncs paradas y lecturas en pausa.
- **Fallo y rollback** (tests de `test_migration_0008`, misma revisión): un fallo después del bootstrap deshace todo y se puede volver a lanzar; con un escritor que bloquea `fixtures`, la migración falla por `lock_timeout` (5 s) sin dejar nada a medias.

### G2: corrección

- **Tras los tres pases, sobre toda la BD:**
  - 0 partidos cuyo estado ganador no sea una observación;
  - 0 observaciones más nuevas que la ganadora (la evidencia antigua nunca pisó el estado);
  - 0 pares `(fixture_id, evidence_id)` duplicados;
  - 0 observaciones huérfanas y 0 partidos sin mapping;
  - todos los hashes de 32 B;
  - el único `evidence_id` que abarca varias temporadas es el del bootstrap (por diseño); cada evidencia de sync tiene ≤ 380 filas (una respuesta).
- **`replay`:** 0 inserciones de evidencia y 0 actualizaciones de `fixtures` por muestra. **`older`:** solo historia, 0 actualizaciones.
- **Tests dirigidos** (escritor, concurrencia de la opción A, evidencia, hash, `STRICT_KNOWLEDGE` / `RETROSPECTIVE_FINAL_RESULTS`, sync con evidencia, backfill, mappings, migración `0008`, perf lab): **369 passed**.
- **Suite completa** del árbol candidato: **1474 passed, 0 failed, 0 skipped, 0 errors**, 1 warning ajeno. G2 solo añade herramientas de laboratorio (`tools/perf_lab/g2.py`, `tests/test_perf_lab_g2.py`) y evidencia: **ningún cambio en el código de la aplicación**.

### G2: propuesta de umbrales para producción (para decisión del Chief)

Se basan en lo medido. Las latencias deben **volver a medirse en el entorno de destino** antes de fijarse como definitivas.

| Métrica | Propuesta | Tipo | Motivo |
|---|---|---|---|
| Invariantes A6 (ganador = observación, sin evidencia nueva perdida, sin duplicados, `replay` sin inserciones) | 0 violaciones | **HARD_GATE** | Es la corrección del contrato; cualquier violación es un defecto |
| Errores, timeouts y deadlocks del escritor | 0 en el benchmark; 0 deadlocks en los tests de concurrencia | **HARD_GATE** | Un escritor único no debe fallar; la concurrencia está cubierta por 24 tests |
| Respuesta máxima operativa | ≤ 2000 partidos (techo de seguridad 2729, sin cambios) | **HARD_GATE** | Política vigente; 2000 medido con holgura |
| p95 con 380 (respuesta típica de la sync en vivo) | WARNING > 500 ms; HARD > 2 s | **WARNING** / **HARD_GATE** | Local 106–121 ms: margen ~4× para el RTT de Neon. Un tick con ~26 respuestas a 2 s ya tarda ~1 min y mantiene más tiempo los bloqueos de fila |
| p95 con 2000 | WARNING > 1,5 s; HARD > 5 s | **WARNING** / **HARD_GATE** | Local 0,56–0,60 s. Los `FOR UPDATE` de la opción A se mantienen toda la transacción y otro escritor espera ese tiempo |
| Latencia del COMMIT | p50 y p95, nunca el máximo | **OBSERVATIONAL** | p50 ~3 ms; picos aislados de 0,3–0,5 s coinciden con checkpoints |
| WAL por partido en estado estable | WARNING > 1,0 KB confirmado, > 1,4 KB actualizado (umbrales de C.10) | **WARNING** | Estado estable 0,81–1,10 KB; tras el bootstrap 1,47 KB es esperable |
| Almacenamiento por observación | WARNING > 350 B | **WARNING** | Medido 305–324 B; alimenta la arquitectura de almacenamiento y particionado, pendiente |
| HOT | estado estable ≥ 90 % con lotes ≤ 1000 bajo **autovacuum** (sin el VACUUM del arnés); tras la migración solo se observa | **WARNING** / **OBSERVATIONAL** | Con VACUUM entre casos ~100 %; justo tras el bootstrap 33–85 %. En destino debe medirse sin VACUUM manual |
| Duración de `0007 → 0008` | WARNING > 0,2 ms por partido; la ventana planificada debe cubrir el bloqueo total | **WARNING** + requisito operativo | Local 73–80 µs por partido, lineal; los lectores quedan bloqueados toda la migración |
| Bootstrap | observaciones = partidos y un solo `evidence_id` (lo valida la migración) | **HARD_GATE** | Si no, la migración se deshace sola |

### G2: limitaciones

- **Sin el entorno de destino:** sin red (Neon añade un RTT por sentencia; el escritor ejecuta ~27 sentencias por lote de 3 respuestas en el arnés), otro sistema operativo y otro runtime, `shared_buffers` por defecto, un solo disco.
- **Un solo cliente:** la concurrencia está cubierta por tests de corrección, no por medidas de rendimiento bajo contención.
- **El HOT del estado estable** incluye el `VACUUM ANALYZE` del arnés entre casos.
- **El ensayo de la migración** es de laboratorio, sobre datos sintéticos de 100 000 / 250 000 partidos; el volumen real de producción no se ha verificado aquí.
- **Variación de entorno:** en Run 1 las latencias diferían un 20–25 % de C.12 con el mismo código. En Run 2, tras reiniciar y con espacio libre en disco, la diferencia baja a ~0–5 %. La causa concreta (espacio en disco, tiempo encendida u otro estado del host) no se ha aislado. El mismo host da ±15–20 % según su estado, así que las latencias locales son orientativas y los umbrales deben fijarse con medidas en destino.

### G2: recomendación

**CONDITIONAL_PASS.** La evidencia local cumple con margen los umbrales de C.10 (380: p95 ≤ 121 ms frente a 750; WAL, almacenamiento, HOT en estado estable y bootstrap dentro de límites) y la corrección es completa. **A6_CHANGE_REQUIRED: NO.** Antes de aceptar A6 para producción hace falta:

1. repetir este mismo arnés en un Neon no productivo aislado (o equivalente), con el runtime Linux + Python 3.12 y la red real;
2. medir el HOT bajo autovacuum;
3. fijar la ventana de la migración `0008` con el recuento real de partidos.

La arquitectura de almacenamiento y particionado de la evidencia sigue pendiente.

**Conclusión local revisada (Run 2):** **CONDITIONAL_PASS**, sin cambios en la recomendación ni en las condiciones. Run 2 da latencias del escritor un 11–28 % menores que Run 1 (380: p95 87–93 ms; 2000: p95 443–463 ms), con más margen frente a C.10 (750 ms) y frente a los umbrales propuestos, que no cambian. WAL, HOT con lotes ≤ 1000, almacenamiento, migración, bootstrap y corrección se reproducen. **A6_CHANGE_REQUIRED: NO.** Las tres condiciones de arriba (Neon no productivo o equivalente, HOT bajo autovacuum, ventana de `0008` con el recuento real) siguen siendo necesarias. La sensibilidad al estado del host refuerza la primera.

## Checkpoint conjunto DI-A6 + Modular (candidato a nuevo baseline)

**Estado:** checkpoint conjunto **PASS** en local (2026-10-07). Pendiente de que el Chief lo establezca como nuevo baseline canónico; hasta entonces sigue vigente el baseline de abajo. Rama `integration/di-a6-modular-final` (worktree `prediktia-di-a6-modular-final`), **sin push** y sin merge a `main`.

- **Entradas congeladas (commits exactos, no nombres de rama):**
  - DI: `941a17344ad0cba4771b9738a876813223fa728b` (`feature/data-integrity-a6`, igual en el remoto; ver [congelación final local](#di-a6-congelación-final-local));
  - Modular: `0bfb4cf82932033248e88049ba66a22f85508084` (`fix/c6-supply-chain-hardening`, PR #3): fija por SHA las actions de los workflows `ops-*`, añade `backend/requirements.lock` (instalación `--no-deps` + `pip check`) y la excepción del escaneo de secretos para esos SHA en `test_ops_tick.py`;
  - **base común:** `a92e8a3` (`main`). DI aporta 28 commits encima y Modular 1; ninguno reescrito.
- **Merge:** `15643f3`, `--no-ff` del commit exacto de Modular, **sin conflictos**. El árbol resultante es el previsto con `git merge-tree` antes del merge (`2d12cfe`). Frente a DI solo cambian los tres workflows, el lock y `test_ops_tick.py` (zonas distintas a las de DI).
- **Superficies compartidas:** `backend/app` y `backend/alembic` son **idénticos** a DI `941a173`, así que se conservan sin cambios el escritor de la opción A (`UpsertCounts` exactos y bloqueo ordenado de filas), el transporte UNNEST, `fixture_observations` y la semántica de la evidencia, `upsert_origin_mappings` y la sync de catálogo, el backfill y `PARITY`, la sync en vivo, el scheduler, estadísticas y reconciliación, el rollback, los fallos del proveedor, los contratos de retorno, `STRICT_KNOWLEDGE` y `RETROSPECTIVE_FINAL_RESULTS`.
  - **Nuevos escritores de fixtures:** NO. **Nuevos escritores de mappings compartidos:** NO.
  - El lock fija las mismas versiones con las que se validó DI-A6 en local (SQLAlchemy 2.1.3, psycopg 3.3.6, alembic 1.20.0).
- **Migraciones:** cabeza única **`0008`** (`0001 → … → 0007_match_statistics → 0008_fixture_observations`); sin migración de merge. **`0009`** sigue reservada para DI-A5D (no iniciado).
- **Validación** (PostgreSQL 18.6 local desechable, nunca Neon):
  - dirigidas (escritor, concurrencia, rollback, evidencia, repetición, backfill y su evidencia, mappings, catálogo, sync en vivo, `ops_tick`/scheduler, estadísticas y reconciliación, UNNEST, lecturas temporales, migraciones `0007`/`0008`): **787 passed**;
  - **suite completa combinada: 1356 passed, 0 failed, 0 skipped, 0 errors, 1 warning** (`StarletteDeprecationWarning`, ajeno; 1356 = 1355 de DI + 1 test nuevo de Modular).
- **Rendimiento:** no se vuelve a medir; el merge no toca el camino del escritor.
- **Riesgos aceptados que siguen abiertos:** aceptación de A6 para producción **HOLD** (nueva medición en el entorno de destino; arquitectura de almacenamiento y particionado de la evidencia); verificación en PostgreSQL 14–17 (aquí solo 18.6); el lock solo vale para Linux + Python 3.12.

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

**Puertas abiertas de A6** (registro de la decisión; estado posterior en [C.11](#c11-upsertcounts-exactos-con-escritores-concurrentes-opción-a)):

1. **Clasificación de `UpsertCounts` con escritores concurrentes.** Después: opción A implementada y validada en local, pendiente de revisión del Chief (C.11).
   - Validar que `created` / `updated` / `unchanged` son exactos con escritores que compiten.
   - Si se reproduce un comportamiento incorrecto, devolver la corrección más pequeña que preserve el contrato, para revisión del Chief.
2. **Hueco del test dedicado de rollback por error de BD** (backfill): aceptado como no bloqueante, pero sigue registrado. Después: **CERRADO** con tests dedicados (C.11).
3. **Nueva medición en el entorno de destino:** obligatoria antes de aceptar A6 para producción.
4. **Arquitectura de almacenamiento y particionado de la evidencia:** obligatoria antes de un despliegue amplio, prolongado y de alta frecuencia.

- **Siguiente punto seguro:** esperar la decisión del Chief sobre cuál de las puertas abiertas se aborda primero. DI-A6 **no** está aceptado para producción.

### C.11 `UpsertCounts` exactos con escritores concurrentes (opción A)

**Estado:** implementada y validada en local; **ACEPTADA por el Chief** (bloqueo de corrección de `UpsertCounts` CERRADO; momento de los bloqueos de fila aceptado; ver el [cierre de sesión](#cierre-de-sesión-di-a6-2026-10-07)). Aceptación de A6 para producción: **HOLD**. Sin cambio de esquema (cabeza única `0008`, `0009` sin crear), sin UNNEST, sin advisory locks, sin `xmax`, sin `RETURNING OLD/NEW` de PG18 y sin cambiar el aislamiento (READ COMMITTED).

- **Commits:**
  - `e2c8a5a`: corrección en `fixture_repository.upsert_fixtures`;
  - `41a23b5`: tests de concurrencia;
  - `3c5f12a` `[MODULAR-REVIEW]`: tests del rollback por error de BD en el backfill (solo tests).
- **Defecto (confirmado por DI-A3F y reproducido aquí):**
  - la clasificación comparaba con una lectura previa SIN bloqueo, tomada antes de que el upsert esperase a otro escritor;
  - el escritor que esperaba podía contar como `created` un partido creado por el otro, o juzgar los cambios de datos contra una imagen previa vieja;
  - 6 de 14 escenarios de dos escritores clasificaban mal, siempre el que esperaba;
  - **el estado guardado (fixtures y evidencia) nunca fue incorrecto.**
- **Corrección (opción A):**
  1. las filas ausentes en una comprobación de existencia sin bloqueo se insertan con `INSERT ... ON CONFLICT DO NOTHING RETURNING`: solo las devueltas son `created`;
  2. el resto se bloquea con `SELECT ... FOR UPDATE ORDER BY external_id`, que da la imagen previa exacta (última versión confirmada);
  3. sobre esas filas bloqueadas corre **el mismo** `INSERT ... ON CONFLICT DO UPDATE ... WHERE` de orden: `updated` = devueltas cuyos datos difieren de la imagen bloqueada;
  4. evidencia, repetición y mappings no cambian.

  Un partido borrado en paralelo (imposible por la app; `RESTRICT`) lanza un error.
- **Exactitud (MEDIDA):** 24 tests nuevos con dos y tres conexiones reales y sincronización explícita (esperas de bloqueo comprobadas en `pg_stat_activity`, pausas justo antes del `FOR UPDATE`) y `lock_timeout` de 10 s.
  - Cubren: creación del mismo partido (mismos datos, distintos, segunda más antigua); evidencia nueva y antigua en ambos órdenes; mismo `observed_at` con hash distinto en ambos órdenes; actualización frente a confirmación; repetición frente a confirmación independiente; rollback del competidor (inserción y actualización); lotes que se solapan parcialmente, como subconjunto y como superconjunto; rangos de `external_id` invertidos con filas nuevas y existentes; tres escritores; control disjunto; dos entrelazados entre sentencias; el camino completo con `ensure_teams`; y un escritor que falla tras la contención.
  - **Todos exactos con la opción A** (3 repeticiones). Con el escritor anterior fallan 14: 12 por la clasificación y 2 solo por el punto de espera.
- **Deadlocks y lock timeouts:** ninguno en ningún escenario.
  - Cada sentencia toma sus bloqueos en orden de `external_id`.
  - Un escritor que espera una inserción ajena (paso 1) todavía no tiene filas bloqueadas (paso 2), así que no hay ciclo. Lo comprueban los entrelazados forzados.
- **Paridad del estado guardado (MEDIDA):** en 13 escenarios con contención, el estado final de `fixtures` (incluido `venue_name`) y la historia de observaciones son **idénticos** con el escritor anterior y con la opción A. Solo cambian los contadores, que ahora son exactos.
- **Camino real hoy:** con `ensure_teams` del mismo proveedor, el segundo escritor ya esperaba en `team_provider_mappings`, antes de su lectura previa, así que en la práctica el defecto estaba enmascarado. La opción A no depende de ese bloqueo accidental.
- **Hueco del test de rollback por error de BD: CERRADO.**
  - Hay tests dedicados del camino `except _DB_ERRORS` del backfill: tras escribir, un error de BD no deja fixtures, observaciones, mappings ni equipos, y un refresh que falla conserva intacto el estado confirmado (datos, cardinalidad de la evidencia y `last_seen_at`).
  - Una mutación (`commit` en vez de `rollback`) los hace fallar.
  - Hay además un test de escritor que falla tras la contención: el competidor confirmado queda intacto y los contadores del escritor fallido no son estado confirmado.
- **Regresiones:**
  - dirigidas (escritor A6, concurrencia, `PARITY` del backfill, Modular, estadísticas, C6 y lecturas temporales): 643 passed;
  - **suite completa combinada: 1349 passed, 0 failed, 0 skipped, 0 errors, 1 warning** (`StarletteDeprecationWarning`, ajeno; 1349 = 1323 + 24 + 2).
- **Rendimiento (MEDIDO; mismo arnés de C, mismo cluster y misma sesión, 100 000 partidos, 30 + 5 muestras):**
  - opción A frente al escritor anterior (`6fa4d27`, exportado), en p50 total:
    - lote 10: 0,82–1,05×;
    - 100: 0,95–1,04×;
    - 380: 1,00–1,04×;
    - 1000: 0,97–1,01×;
    - 2000: 0,98–1,01×;
  - el único valor alto, `replay` con 2000 (1,17×), no se repite: en la repetición dirigida da 1,007×, y todos los casos repetidos quedan entre 0,987× y 1,007×;
  - frente a pre-A6, la opción A queda en 2,59–2,77× con 380 (umbral aceptado ≤ 3,0×), y con 380 p50 458–481 ms y p95 503–526 ms (umbrales 600/750);
  - una sentencia más por respuesta en estado estable (la comprobación de existencia) y el mismo WAL (0,95–1,10×).
- **Techo de parámetros (MEDIDO):**
  - con respuestas solo de partidos nuevos pasa de 2729 a **2730** (65 520 parámetros; falla 2731), porque el `INSERT ... DO NOTHING` no lleva constantes extra;
  - la sentencia sobre filas existentes es la misma de antes (24 por fila + 21), así que el techo conservador **sigue siendo 2729** y la política operativa (≤ 2000) no cambia;
  - pico de asignaciones Python cerca del techo: ~82 MiB.
- **HOT (hallazgo para el Chief, no causado por la opción A):**
  - en una BD recién migrada, el HOT de las confirmaciones empieza muy bajo y sube con cada ciclo de actualización + VACUUM, sea cual sea el escritor. En el experimento pareado (una sola BD, alternando), unchanged con 1000 da 12,8 % → 39,8 % → 74,5 % → 86,6 % → 94,6 % → 99,1 %, y cada ejecución de la opción A es mayor que la del escritor anterior que la precede;
  - el bootstrap de `0008` reescribe todas las filas de `fixtures`, así que justo después las confirmaciones son mayoritariamente no-HOT;
  - el umbral aceptado (≥ 90 % con lotes ≤ 1000) **solo se cumple en estado estable**, no tras una migración.
- **Puertas que siguen abiertas:**
  - nueva medición en el entorno de destino;
  - arquitectura de almacenamiento y particionado de la evidencia;
  - matiz de estado estable para el umbral de HOT;
  - revisión del Chief de esta corrección;
  - verificación empírica en PostgreSQL 14–17: aquí solo se ha ejecutado en 18.6; las primitivas usadas son portables según la documentación.

### C.12 Transporte UNNEST en el escritor de la opción A (candidato de producción)

**Estado:** implementado y validado en local; candidato de producción **aceptado en local por el Chief** (puerta local y de concurrencia de UNNEST CERRADA; ver el [cierre de sesión](#cierre-de-sesión-di-a6-2026-10-07)). Aceptación de A6 para producción: **HOLD**. Sin cambio de esquema (cabeza única `0008`, `0009` sin crear), sin migraciones, sin advisory locks, sin `xmax`, sin `RETURNING OLD/NEW` de PG18 y sin cambiar el aislamiento.

- **Commits:**
  - `7a8643f`: el transporte (`app/repositories/bulk_rows.py`) y su uso en `fixture_repository` y `provider_mapping_repository`;
  - `afa686c`: tests del transporte.
- **Origen:** el mecanismo de DI-A3F (`7a521fe`, cierre `e04cd9e`), portado **dentro** del escritor actual de la opción A, no como el clon de DI-A3F (que era anterior a la opción A y conservaba la carrera de los contadores).
- **Qué cambia:** solo el transporte de las sentencias multi-fila. Cada columna viaja como un array tipado y se lee con `unnest()`:
  - el hash (`fixture_state_hash_v1`, el mismo);
  - el INSERT de equipos;
  - el `INSERT ... ON CONFLICT DO NOTHING RETURNING` de los partidos ausentes;
  - el `INSERT ... ON CONFLICT DO UPDATE ... WHERE` de orden sobre las filas bloqueadas;
  - el INSERT de observaciones;
  - el upsert de mappings de origen (también usado por la sync de catálogo).
- **Qué no cambia:**
  - las filas, columnas, expresiones (`now()`), conflictos, predicados, RETURNING y defaults de la BD;
  - la transacción;
  - el `SELECT ... FOR UPDATE ORDER BY external_id` y la clasificación de la opción A;
  - las sentencias de partidos fijan su orden de proceso con `ORDER BY external_id`, el mismo orden ascendente que antes;
  - las cadenas viajan como `text[]`, así que un valor demasiado largo se rechaza y no se trunca;
  - los arrays se validan antes de construir el SQL.
- **Paridad semántica (MEDIDA):**
  - escenario diferencial de 14 pasos con `ensure_teams` + `upsert_fixtures` y los mismos ids internos: VALUES (`4309e15`) frente a UNNEST, con contadores **idénticos** en cada paso;
  - los pasos cubren: inserción con FT/AET/PEN/parcial/en vivo, actualización, confirmación, repetición, evidencia antigua, empate en el instante, respuesta parcial que conserva pares, corrección AET → FT, PST que limpia marcadores, lote mixto, duplicados en la entrada, cambio de nombre/logo de equipo, actualización deshecha y entrada vacía;
  - instantánea final **idéntica**: fixtures con `last_state_hash` y evidencia ganadora; 24 observaciones con su `state_hash` (todos canónicos); equipos (incluido `is_national`); mappings de partidos y equipos (`raw_name` y `last_seen_at`).
- **Concurrencia (MEDIDA):** los 24 tests de la opción A pasan con UNNEST, con contadores exactos por escritor, el mismo punto de espera, sin deadlocks y sin lock timeouts.
- **Rollback:** los tests del hueco cerrado siguen verdes con UNNEST: error de BD tras escribir, refresh fallido y escritor que falla tras la contención.
- **Regresiones:**
  - dirigidas (escritor, concurrencia, rollback, `PARITY` del backfill, Modular, estadísticas, C6, sync, lecturas temporales): 696 passed;
  - **suite completa combinada: 1355 passed, 0 failed, 0 skipped, 0 errors, 1 warning** (`StarletteDeprecationWarning`, ajeno; 1355 = 1349 + 6).
- **Rendimiento (MEDIDO; mismo arnés de C, mismo cluster y misma sesión, 100 000 partidos, 30 + 5 muestras; referencia: la opción A con VALUES):**

  | Lote | Aceleración p50 (mín–máx por modo; mediana) | UNNEST p50 |
  |---:|---|---:|
  | 10 | 1,54–1,90×; 1,79× | 9–14 ms |
  | 100 | 4,42–4,78×; 4,50× | 24–28 ms |
  | 380 | 5,88–6,97×; 6,54× | 66–78 ms (p95 79–96) |
  | 1000 | 5,98–6,84×; 6,37× | 181–210 ms |
  | 2000 | 6,13–6,87×; 6,32× | 362–413 ms |

  - Por clase de carga (lotes de 380 a 2000, mediana): insert 6,33×, update 6,32×, unchanged 6,26×, older 6,77×, mixed 5,98×, tie 6,61× y replay 6,87×.
  - **Con 380, UNNEST queda por debajo del escritor pre-A6** (~72 ms frente a ~175 ms). Es muy inferior a los umbrales aceptados (≤ 600/750 ms; el sobrecoste frente a pre-A6 pasa a ~0,4×).
  - La ganancia está en SQLAlchemy antes del cursor: con 1000 filas, 625–676 → 11–14 ms. El round-trip del cursor baja a menos de la mitad.
  - El COMMIT sube algo con lotes de 2000 (1,4–2,9 → 2,0–8,3 ms), igual que vio DI-A3F, pero es despreciable en el total.
  - La parte del hash en el escritor baja al ~10 %.
- **WAL:** sin cambio (0,88–1,02×).
- **HOT (lectura del umbral aceptado "≥ 90 % con lotes ≤ 1000 EN ESTADO ESTABLE"):**
  - en la matriz, UNNEST queda igual o por encima de VALUES (confirmaciones con 1000: 95,8 % frente a 94,6 %; con 2000: 88,5 % frente a 75,3 %);
  - en el experimento pareado (una sola BD, alternando), el HOT sube con el estado de las páginas en ambos transportes: 12,5 → 40,5 → 77,1 → 83,7 → **99,2 → 97,8 %**;
  - en estado estable los dos cumplen ≥ 90 %. El HOT bajo justo tras el bootstrap no es una regresión del transporte.
- **Memoria Python (pico de asignaciones en una respuesta, MEDIDO):** 380: 11,1 → 2,4 MiB; 1000: 30,0 → 5,0 MiB; 2000: 60,9 → 9,3 MiB.
- **Parámetros (capacidad técnica, separada de la política):**
  - con UNNEST, el máximo de parámetros enlazados en una sentencia es igual al número de partidos: solo quedan las listas `IN`;
  - una sola respuesta funciona con 2730, 4000, 5000, 10 000 y 20 000 partidos (20 000: 3,7 s y 96 MiB), siempre con rollback;
  - el siguiente límite técnico serían las listas `IN` (~65 535 ids por sentencia): DERIVADO, no medido;
  - **la política no cambia:** tope operativo **≤ 2000** y techo de la política de seguridad **2729** (no es el límite físico de UNNEST). Subirlos exige una decisión aparte del Chief.
- **Revisión de Modular:** `3c5f12a` (tests del rollback del backfill) y `7a8643f` (solo la superficie compartida `upsert_origin_mappings` / sync de catálogo): **APROBADAS** tras el cierre de sesión (ver [congelación final local](#di-a6-congelación-final-local)). UNNEST no cambia el comportamiento del backfill (paridad de su suite y de `PARITY`).
- **Puertas que siguen abiertas:**
  - decisión del Chief sobre este candidato;
  - nueva medición en el entorno de destino;
  - arquitectura de almacenamiento y particionado de la evidencia;
  - verificación empírica en PostgreSQL 14–17 (aquí solo 18.6; `unnest()` de varios arrays y `INSERT ... SELECT ... ON CONFLICT` están documentados desde mucho antes de la 14);
  - ~~revisión de Modular de `3c5f12a`~~ (aprobada; ver [congelación final local](#di-a6-congelación-final-local)).

### Cierre de sesión DI-A6 (2026-10-07)

Estado vigente de DI-A6. Sustituye a los "pendiente de decisión" de C.11 y C.12. **VERIFICADO** = comprobado en este repositorio, con Git o con tests; **REPORTADO** = comunicado por otro carril autorizado y no comprobado aquí; **PENDIENTE** = revisión externa o trabajo futuro.

- **Decisiones del Chief** (comunicadas en esta sesión):
  - opción A **ACEPTADA**: `UpsertCounts` exactos con escritores concurrentes; el bloqueo de corrección de `UpsertCounts` queda **CERRADO**;
  - el cambio de momento de los bloqueos de fila (`SELECT ... FOR UPDATE ORDER BY external_id`) queda **aceptado**;
  - hueco del test de rollback por error de BD: **CERRADO**;
  - UNNEST: candidato de producción **aceptado en local**; la puerta local y de concurrencia de UNNEST queda **CERRADA**;
  - implementación local de A6: **ESTABLE**;
  - **aceptación de A6 para producción: HOLD**.
- **Política vigente:**
  - tope operativo **≤ 2000** partidos por respuesta;
  - **2729 es el techo de la política de seguridad**, no el límite físico de UNNEST. Es el último tamaño que funcionó con el transporte VALUES (C.4). Con UNNEST funcionan respuestas de 20 000 (C.12), pero la política no cambia sin otra decisión del Chief.
- **VERIFICADO** (repositorio, Git y tests de esta sesión):
  - rama `feature/data-integrity-a6`, HEAD de implementación `f1396ad` (código igual que `afa686c`; `f1396ad` es solo documentación), worktree limpio;
  - **Alembic:** cabeza única **`0008`**; **`0009` ausente**, reservada para DI-A5D;
  - **DI-A5D: no iniciado**;
  - **suite completa combinada** sobre el código de `afa686c`: **1355 passed, 0 failed, 0 skipped, 0 errors**, 1 warning ajeno (PostgreSQL 18.6 local desechable, nunca Neon);
  - dirigidas: 696 passed; concurrencia de la opción A con UNNEST: 24/24; paridad diferencial VALUES frente a UNNEST: contadores e instantánea idénticos;
  - rendimiento local (C.12): UNNEST 5,9–7,0× más rápido que la opción A con VALUES desde lotes de 380 (p50 ~72 ms con 380), WAL sin cambio y HOT ≥ 90 % en estado estable;
  - **publicación:** la referencia de seguimiento local `origin/feature/data-integrity-a6` apunta a `f1396ad` y la rama tiene ese upstream configurado. Esa referencia solo cambia con un push o un fetch correctos. La comprobación contra el remoto en vivo (`git ls-remote`) no se pudo ejecutar en esta sesión: el push y esa comprobación los bloqueó el control de permisos del agente, y el push lo hizo después el usuario a mano.
- **REPORTADO:** los resultados de DI-A3F (`7a521fe`, cierre `e04cd9e`, en `feature/data-integrity-a3f`), usados como origen del mecanismo UNNEST. Aquí se volvió a medir y a validar el escritor portado; las cifras de DI-A3F no se han repetido como tales.
- **PENDIENTE:**
  - ~~**revisión de comportamiento de Modular**~~ (dueño de Modular): **APROBADA después de este cierre**; ver [congelación final local](#di-a6-congelación-final-local):
    - `3c5f12a`: adaptación de tests del rollback por error de BD en el backfill;
    - `7a8643f`: **solo** la superficie compartida `upsert_origin_mappings`, que también usa la sync de catálogo (mismo comportamiento, otro transporte);
  - **nueva medición en el entorno de destino**;
  - **arquitectura de almacenamiento y particionado de la evidencia**;
  - verificación empírica en PostgreSQL 14–17 (aquí solo 18.6);
  - **aceptación de A6 para producción: HOLD.**
- **Siguiente paso autorizado:** esperar la revisión del dueño de Modular o la siguiente sesión del Chief. Sin push nuevo, sin merge a `main`, sin migraciones, sin Neon y sin DI-A5D. (Superado por la [congelación final local](#di-a6-congelación-final-local).)

### DI-A6: congelación final local

Estado vigente de DI-A6; sustituye al cierre de sesión anterior donde difieran. **VERIFICADO** = comprobado en Git o en el repositorio; **REPORTADO** = decisión del dueño de Modular aprobada y comunicada por el Chief; **PENDIENTE** = trabajo futuro de nivel producción.

- **Resumen de estado:**

  ```
  DI_A6_CROSS_DEPARTMENT_REVIEW:     CLOSED
  DI_A6_LOCAL_IMPLEMENTATION:        FROZEN
  OPTION_A:                          ACCEPTED
  UPSERTCOUNTS_CONCURRENCY_BLOCK:    CLOSED
  DB_ERROR_ROLLBACK_GAP:             CLOSED
  UNNEST_LOCAL_PRODUCTION_CANDIDATE: ACCEPTED
  A6_PRODUCTION_ACCEPTANCE:          HOLD
  ```

- **REPORTADO** (revisión de comportamiento del dueño de Modular tras el cierre de sesión, aprobada por el Chief):

  | Punto | Resultado |
  |---|---|
  | `3c5f12a` (tests del rollback por error de BD en el backfill) | **APROBADO** |
  | `7a8643f`, solo la superficie compartida de mappings y catálogo (`upsert_origin_mappings` / sync de catálogo) | **APROBADO** |
  | Comportamiento de Modular preservado | **SÍ** |
  | Comportamiento del backfill preservado | **SÍ** |
  | Comportamiento de la sync de catálogo preservado | **SÍ** |
  | Semántica de los mappings preservada | **SÍ** |
  | Conflicto semántico | **NINGUNO** |

  Con esto queda cerrada la revisión entre departamentos de DI-A6 (`7c41705`, `1920590` y `7e4af3e` ya estaban aprobados; ver [Revisión de Modular](#revisión-de-modular-aprobada)).
- **VERIFICADO** (Git y repositorio, al registrar esta congelación):
  - rama `feature/data-integrity-a6`; HEAD de código `f1396ad` (código igual que `afa686c`), con `3cadc92` como cierre de sesión (solo documentación); worktree limpio antes de este registro;
  - `3c5f12a` y `7a8643f` existen y son ancestros del HEAD;
  - el remoto en vivo (`git ls-remote`) tiene `feature/data-integrity-a6` en `f1396ad`; `3cadc92` y este registro son locales, sin push;
  - **Alembic:** cabeza única **`0008`**; **`0009` ausente**, reservada para DI-A5D;
  - **DI-A5D: no iniciado**;
  - no se ha repetido la suite: desde `afa686c` solo ha cambiado documentación, así que la última suite completa (1355 passed, 0 failed, 0 skipped, 0 errors) sigue valiendo para el código actual.
- **Política vigente (sin cambios):** tope operativo **≤ 2000** partidos por respuesta; **2729** es el techo de la política de seguridad, **no** el límite físico de UNNEST.
- **PENDIENTE** (nivel producción; **no** bloquea el checkpoint de la línea base conjunta de desarrollo DI + Modular):
  - nueva medición en el entorno de destino;
  - arquitectura de almacenamiento y particionado de la evidencia;
  - **aceptación de A6 para producción: HOLD.**
- **Siguiente paso:** checkpoint de la línea base conjunta DI + Modular, cuando el Chief lo asigne. Sin push nuevo, sin merge a `main`, sin migraciones, sin Neon y sin DI-A5D.

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
