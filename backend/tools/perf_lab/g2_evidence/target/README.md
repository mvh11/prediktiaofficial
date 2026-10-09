# DI-A6 G2 en destino: rama Neon NO productiva

**Solo ensayo en destino NO productivo.** No autoriza la aceptación de A6 para producción. Producción no se tocó; `0009` no está
en este árbol. Resumen, umbrales y recomendación: `docs/data-integrity-status.md`, "DI-A6 G2 en destino".

**Sin datos de conexión.** La URL se cargó de forma opaca en cada proceso y nunca se imprimió ni se guardó. Estos archivos
no contienen URL, host, usuario, contraseña, identificadores de Neon ni valores de filas reales. Solo hay recuentos, tamaños,
metadatos del esquema y tiempos agregados. Se comprobó con una búsqueda de cada componente de la URL y de los identificadores de
Neon sobre todos los archivos: 0 coincidencias.

## Destino

| Dato | Valor |
|---|---|
| Clasificación | **NON_PRODUCTION**, rama `g2-di-a6-target` creada desde `production` (handoff de IT_SUPPORT_AGENT) |
| Verificación | `neon.branch_id` comparado con el del handoff **en cada conexión nueva de cada engine** (guarda de `g2_target.py`); primario, no réplica |
| PostgreSQL | 18.6 (Neon) |
| Ajustes visibles | `fsync` off y `full_page_writes` off (la durabilidad está en el almacenamiento de Neon, no en el fsync local); `synchronous_commit` on; `shared_buffers` 230 MB; `autovacuum` on con valores por defecto (naptime 1 min, 3 workers, umbrales 50 / 0,2 y de inserción 1000 / 0,2); sin opciones de tabla en `fixtures` ni `fixture_observations` |
| Conexión | endpoint **pooled** (PgBouncer de Neon, modo transacción), `sslmode=require` entre el cliente y el pooler; el servidor ve la conexión pooler → compute sin SSL |
| Red | la ruta real desde este cliente: `SELECT 1` p50 **139–149 ms** (mín 144, p95 146 en la identidad) |
| Cliente | Windows 11, Python 3.13.4, SQLAlchemy 2.1.3, psycopg 3.3.6 (**no** Linux + Python 3.12 de C6) |
| Datos | 18 671 partidos reales en `0007` (los heredados de `production`) para la migración; dataset sintético aislado para el escritor |

**Representatividad: PARTIAL.** Son reales el motor, la versión, el pooler, el SSL, la red desde este cliente, el volumen real
para la migración y el autovacuum. **No** son los del despliegue: el sistema operativo y el runtime del cliente, la ruta de red
desde el host de producción de C6, los datos del escritor (sintéticos) y la concurrencia (un solo cliente).

## Metodología

1. **`identity`** (solo lectura, `BEGIN READ ONLY` por transacción): rama, revisión `0007`, 18 671 partidos y 18 671 mappings,
   `fixture_observations` ausente, rangos de ids del laboratorio vacíos, ajustes y RTT. **VERIFIED.**
2. **`upgrade`**: `alembic upgrade 0008` real sobre los 18 671 partidos reales. Se mide:
   - una sonda que lee `fixtures` cada 50 ms desde otra conexión;
   - la duración de cada sentencia de la migración, para separar el bootstrap;
   - el WAL (`pg_current_wal_insert_lsn`) y los tamaños.
   Después no hay VACUUM.
3. **`verify`** tras la migración: invariantes de A6 por recuentos agregados.
4. **`seed`**: dataset sintético determinista. Es el mismo calendario de `dataset.fixture_at`: 3800 partidos, 10 temporadas de
   una liga, 20 equipos. Usa rangos de ids reservados (partidos ≥ 10 000 000, inserciones temporales ≥ 100 000 000, equipos
   1 000 001–1 000 020, competición 500 001). La competición y las temporadas se insertan con SQL; equipos, partidos, mappings y
   observaciones los escribe el **escritor real**, una respuesta por temporada.
5. **`writes`** × 3 pases (A, B, C). Es el arnés aceptado de Run 2 (`a6.measure_writes`) con los mismos modos (7),
   calentamiento (5), muestras (30) y lotes (380 / 1000 / 2000), con dos cambios solo de laboratorio:
   - las temporadas sintéticas se traducen a los ids reales de las temporadas del laboratorio;
   - **no se hace VACUUM ANALYZE antes de cada caso**: el HOT y el estado de las páginas quedan en manos del autovacuum
     (`vacuum_count` = 0 en los tres pases).
6. **`verify --rollback-probe`** al final: invariantes y una prueba de atomicidad. El escritor real escribe 380 filas del
   laboratorio, se hace rollback y se comprueba que el recuento y la huella de estado no cambian.

Nunca se escribieron ni se leyeron valores de las filas reales: el escritor solo tocó filas sintéticas, y de las reales solo se
consultaron recuentos.

### Interrupción del primer intento del pase A

El primer intento del pase A (03:04–03:22, hora local) se cortó al cerrarse la sesión del agente. Ya había medido los 7 casos de
380 e `insert_1000`, pero no guardó JSON. Solo quedan las líneas del log:

| Caso | p50 | HOT |
|---|---:|---:|
| `insert_380` | 1,81 s | — |
| `update_380` | 1,83 s | **44 %**: primera escritura tras la siembra |
| `unchanged_380` | 1,82 s | 96 % |
| `older_380` | 1,89 s | — |
| `mixed_380` | 2,27 s | — |
| `tie_380` | 1,93 s | — |
| `replay_380` | 2,07 s | — |
| `insert_1000` | 4,79 s | — |

La transacción en curso se deshizo. `verify-after-interruption.json` confirma el estado limpio: 3800 filas del laboratorio,
invariantes en 0 y 0 observaciones no-bootstrap en filas reales. El pase A se relanzó completo desde el estado base. Por eso
**el pase A del destino no es una primera escritura pura para los lotes de 380**: esas páginas ya se habían actualizado una vez.

## Archivos

| Archivo | Contenido |
|---|---|
| `identity.json` | identidad y ajustes, solo lectura |
| `upgrade.json` | migración, sentencias, bootstrap, sonda, WAL, tamaños y comprobaciones |
| `verify-post-migration.json`, `verify-after-interruption.json`, `verify-final.json` | invariantes; el último incluye la prueba de rollback |
| `seed.json` | dataset sintético |
| `writes-pass{A,B,C}.json` | matriz completa con las muestras crudas |
| `SUMMARY.md` | `python -m tools.perf_lab.g2 summarize writes-pass*.json` |

Reproducir (desde `backend/`):

```powershell
# Entorno: DI_A6_G2_DATABASE_URL (opaca), G2_TARGET_CLASSIFICATION=NON_PRODUCTION, G2_TARGET_NEON_BRANCH_ID=<id de la rama>
python -m tools.perf_lab.g2_target identity --expect-revision 0007 --expect-fixtures <n> --output <json>
python -m tools.perf_lab.g2_target upgrade --output <json>
python -m tools.perf_lab.g2_target verify --output <json>
python -m tools.perf_lab.g2_target seed --output <json>
python -m tools.perf_lab.g2_target writes --label <pase> --output <json>   # A, B, C
python -m tools.perf_lab.g2_target verify --rollback-probe --output <json>
```

## Migración `0007 → 0008` (18 671 partidos reales)

| Medida | Destino | Run 2 local (100 000 sintéticos) |
|---|---:|---:|
| Migración completa (una transacción, incluye conexión y arranque de Alembic) | **6,96 s** | 7,40 s |
| Suma de las 16 sentencias | 4,10 s | — |
| Bootstrap (LOCK + INSERT de observaciones + UPDATE de `fixtures`) | **1,84 s** (0,65 + 1,05 s; 99 µs por partido) | dentro de los 7,40 s |
| Lector bloqueado (lectura más larga de la sonda) | **3,24 s** | 7,17 s |
| WAL | 25,1 MiB | — |
| Crecimiento | `fixtures` +5,5 MiB, `fixture_observations` +5,2 MiB (**600 B por partido**) | 615 B por partido |
| Comprobaciones | revisión `0008`; 18 671 partidos y 18 671 observaciones de bootstrap con un único `evidence_id`; 0 partidos sin observación, duplicados, ganadores distintos de su observación, huérfanas, hashes de longitud distinta de 32 B y columnas de orden nulas | igual |

Con 145 ms por sentencia, la parte de red de la migración (~16 viajes) pesa más que el trabajo de datos. El bootstrap por
partido (99 µs) es del orden del local (66–74 µs por partido en la migración completa).

## Escritor: destino frente a Run 2 local

Ms en local, s en destino. 210 muestras por lote y pase. El tiempo en cursor SQL incluye la red.

| Lote | Pase | p50 local → destino | p95 local → destino | máx destino | COMMIT p50 destino | cursor SQL p50 destino |
|---:|---|---|---|---:|---:|---:|
| 380 | A | 71 ms → 2,02 s | 93 ms → **5,91 s** ¹ | 6,09 s | 148 ms | 1,82 s |
| 380 | B | 74 ms → 1,93 s | 92 ms → 2,26 s | 2,75 s | 153 ms | 1,73 s |
| 380 | C | 74 ms → **1,93 s** | 87 ms → **2,29 s** | 2,44 s | 155 ms | 1,73 s |
| 1000 | A | 199 ms → 4,86 s | 253 ms → 5,66 s | 5,80 s | 148 ms | 4,58 s |
| 1000 | B | 207 ms → 5,08 s | 268 ms → 5,85 s | 5,99 s | 153 ms | 4,78 s |
| 1000 | C | 202 ms → **5,11 s** | 230 ms → **5,92 s** | 6,07 s | 155 ms | 4,80 s |
| 2000 | A | 396 ms → 9,62 s | 443 ms → 10,89 s | 11,14 s | 148 ms | 9,18 s |
| 2000 | B | 408 ms → 9,97 s | 462 ms → 11,25 s | 11,81 s | 153 ms | 9,53 s |
| 2000 | C | 403 ms → **10,05 s** | 463 ms → **11,38 s** | 11,69 s | 155 ms | 9,61 s |

¹ El p95 de 380 del pase A lo inflan los primeros casos del relanzamiento. `insert`, `update` y `unchanged` de 380 tuvieron un
p50 de 4,6 / 5,9 / 1,9 s y un p95 de 5,3 / 5,9 / 5,9 s, frente a ~1,8 s en el resto y en el intento cortado. Coincidieron con
un WAL de 2–3× por muestra (4,3–4,6 KB por partido frente a 1,2–2,1 KB). Es actividad simultánea del servidor, probablemente el
autovacuum de las ~83 000 observaciones del intento cortado o el calentamiento de la caché de Neon. **No se ha atribuido la
causa.** Desde `older_380` todo vuelve a ~1,8–2,3 s.

**De dónde sale la latencia en destino.** Es casi toda red:
- **Por muestra:** el escritor ejecuta **10 / 30 / 60** sentencias con 380 / 1000 / 2000 (`update`; son las mismas familias que
  en local) más el COMMIT. Cada una es un viaje de ~145–155 ms.
- **COMMIT:** es exactamente **un viaje** (148–155 ms).
- **Fuera de la red:** el p50 menos `(sentencias + 1) × RTT` deja **~0,3 / 0,5 / 1,0 s** para el servidor y el envío de los
  arrays de UNNEST. Es DERIVADO, no medido aparte.
- **Python del cliente:** el tiempo de SQLAlchemy antes del cursor es 8,7 / 25,7 / 50 ms, el doble que en local, pero menos del
  0,5 % del total.

El escritor escala con el número de sentencias, que crece con el número de temporadas de la respuesta (un grupo por temporada),
no con el número de filas.

| Lote | WAL por partido, estable (update / unchanged / insert) | HOT `update` / `unchanged`, local → destino (pase C) | Observaciones por muestra | `replay` | `UpsertCounts` |
|---:|---|---|---|---|---|
| 380 | 1261 / 1176 / 2095 B | 100 → 100 % ; 100 → 100 % | 380 | 0 | exactos |
| 1000 | 1258 / 1180 / 2096 B | 99 → 100 % ; 100 → 100 % | 1000 | 0 | exactos |
| 2000 | 1271 / 1203 / 2098 B | 97 → 100 % ; 89 → 100 % | 2000 | 0 | exactos |

- **WAL:** un 40 % por encima del local en `update` y `unchanged` y un 20 % en `insert` (local ~0,9 / 0,8 / 1,7 KB). Es estable entre los pases B y C, sin efecto de `full_page_writes`
  (está off). Es otro motor de almacenamiento: los umbrales de WAL deben fijarse por entorno.
- **Almacenamiento:** 301–324 B por observación, igual que en local (305–324).

## HOT solo con autovacuum (fase C)

| Pase | `n_tup_upd` | `n_tup_hot_upd` | HOT del pase | HOT por muestra, lotes ≤ 1000 (`update` / `unchanged`) | autovacuum (`fixtures` / `fixture_observations`) | VACUUM manual |
|---|---:|---:|---:|---|---|---:|
| A | 405 541 | 370 416 | **91,3 %** | 100 / 100 % (380); 100 / 100 % (1000) | 29 / 10 | 0 |
| B | 349 541 | 340 638 | **97,5 %** | 100 / 100 %; 100 / 100 % | 22 / 4 | 0 |
| C | 349 541 | 340 644 | **97,5 %** | 100 / 100 %; 100 / 100 % | 25 / 2 | 0 |

- **Fuente:** `pg_stat_user_tables` de `fixtures` por pase y `pg_stat_xact_user_tables` por muestra.
- **Intervalos de medida (UTC):** A 06:24–07:56, B 07:57–09:27, C 09:27–10:59.
- **Sin VACUUM forzado, el HOT de estado estable se mantiene ≥ 97 %**, y en lotes ≤ 1000 es del 100 %.
- **El HOT por debajo** solo aparece en la primera escritura tras la siembra (`update_380` 44 % en el intento cortado), en
  `update_2000` del pase A (79 %) y en el agregado del pase A (91 %, que incluye las normalizaciones). Coincide con lo visto en
  local tras el bootstrap.

## Corrección (`verify-final.json`, tras los tres pases)

- **Partidos:** 18 671 reales y 3800 del laboratorio.
- **Observaciones:** 18 671 de bootstrap y 1 931 595 de sync.
- **Filas reales:** solo su observación de bootstrap (0 no-bootstrap).
- **Invariantes de A6, todos en 0:**
  - partidos cuyo ganador no es una observación;
  - observaciones más nuevas que la ganadora;
  - pares `(fixture_id, evidence_id)` duplicados (la restricción UNIQUE está presente);
  - observaciones huérfanas;
  - partidos del laboratorio sin mapping de api-football;
  - hashes de longitud distinta de 32 B.
- **Evidencia:** un único `evidence_id` multi-temporada, el del bootstrap; ≤ 380 filas por evidencia de sync.
- **Deadlocks:** 0 en total.
- **Prueba de rollback:** el escritor real escribió 380 actualizaciones y se hizo rollback. El recuento de observaciones del
  laboratorio y la huella de estado quedaron idénticos (**atómico**).
- **Por muestra (1890 muestras medidas):**
  - `UpsertCounts` exactos;
  - el lote completo en observaciones (`replay` 0);
  - `older` sin actualizaciones de `fixtures` (lo comprueba el arnés);
  - 0 violaciones de "ganador = observación";
  - 0 errores, 0 timeouts y 0 deadlocks (3 rollbacks por pase, todos de conexiones de solo lectura del arnés).
