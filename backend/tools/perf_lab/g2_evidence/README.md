# DI-A6 G2: evidencia de laboratorio (escritor UNNEST y ensayo de 0007 → 0008)

**Solo laboratorio.** No es un ensayo de producción ni autoriza la aceptación de A6 para producción.
Nunca se tocó Neon. Resultado y recomendación: `docs/data-integrity-status.md`, "DI-A6 G2".

## Ejecuciones

| Ejecución | Estado | Archivos |
|---|---|---|
| **G2_RUN_1** (2026-10-08) | **STORAGE_PRESSURE_SUSPECTED**: el SSD estaba casi lleno y la máquina llevaba varios días encendida. **Sus latencias quedan marcadas como contaminadas por el entorno**; WAL, HOT, almacenamiento, recuentos y corrección siguen siendo válidos (Run 2 los reproduce) | `*.json` y `SUMMARY.md` de esta carpeta (sin cambios) |
| **G2_RUN_2** (2026-10-09) | **POST_RESTART / STORAGE_PRESSURE_RELIEVED**: tras reiniciar, con 107,6 GB libres de 930,4 GB (11,6 %). Misma metodología, mismos ajustes, mismo código | `run2/*.json` y `run2/SUMMARY.md` |

| **G2 en destino** (2026-10-09) | **NON_PRODUCTION**: rama Neon `g2-di-a6-target`, representatividad **PARTIAL**. Migración real de 18 671 partidos y escritor sobre un dataset sintético aislado, sin VACUUM forzado | `target/` (README propio con la metodología, los resultados y la comparación con Run 2) |

Comparación directa: `COMPARISON.md`. **Las latencias de referencia locales son las de Run 2**; las del destino, las de `target/`.

## Candidato

- Commit bajo prueba: `d31eebc8e923c3d0351ac55086e53e08c2a5f62b` (rama `lab/data-integrity-a6-g2`).
  Es la base canónica `1c06ef9` más 15 commits de Modular (M5.7–M5.9B) que solo **añaden** archivos
  (lecturas de estadísticas as-of, features prematch, código de investigación offline y sus tests).
  Comprobado con el diff: ningún cambio en `alembic/`, `app/models/`, `fixture_repository`,
  `bulk_rows`, `provider_mapping_repository`, la sync, el backfill ni las lecturas temporales, y el
  código añadido no escribe en la BD. El escritor medido es el de la base (opción A + UNNEST).
- Esquema: cabeza `0008` (no existe `0009` en este árbol).
- Prueba de que se midió UNNEST: el máximo de parámetros enlazados en una sentencia es 380–381
  (solo las listas `IN`, igual en las dos ejecuciones); con VALUES serían ~24 por fila.

## Entorno

| Dato | Valor |
|---|---|
| Host | escritorio local, Windows 11 Pro |
| CPU | Intel Core i3-12100F (4 núcleos / 8 hilos, 3,3 GHz) |
| RAM | 23,8 GB |
| Almacenamiento | SSD SATA (WD Green 1 TB), mismo disco para datos y WAL |
| PostgreSQL | 18.6 (x86_64-windows, MSVC), cluster desechable recién creado con `initdb` |
| Ajustes | por defecto: `shared_buffers` 160 MB, `work_mem` 4 MB, `fsync` y `synchronous_commit` on, `full_page_writes` on, `wal_compression` off, `max_wal_size` 1 GB, `checkpoint_timeout` 5 min, `autovacuum` on, `jit` on |
| Conexión | TCP loopback `127.0.0.1`, sin pooler, un solo cliente |
| Runtime | Python 3.13.4 (Windows), SQLAlchemy 2.1.3, psycopg 3.3.6 |
| Red | ninguna (loopback): **no incluye** la latencia de ida y vuelta hacia Neon |

**Representatividad: LIMITED.** Producción es Neon (PostgreSQL gestionado, versión no verificada aquí, red
WAN, endpoint `-pooler` posible) y el runtime de C6 es Linux + Python 3.12 (`requirements.lock`).
Las versiones de SQLAlchemy y psycopg sí coinciden con el lock.

## Metodología

- **Escritor:** `tools.perf_lab.a6.measure_writes` (el arnés de Checkpoint C) a través de `g2.py`.
  Camino real: `ensure_teams` + `upsert_fixtures` por grupo de temporada (una respuesta lógica y una
  evidencia por grupo; 380 partidos por grupo), un COMMIT por lote.
- **Datos:** sintéticos y deterministas (`seed --fixtures 100000`, semilla 20261004), sembrados en
  `0007` con COPY y llevados a `0008` con el bootstrap real de la migración.
- **Lotes:** 380, 1000 y 2000. **Modos (7):** `insert` (primera escritura), `update`, `unchanged`
  (confirmación), `older` (evidencia antigua), `mixed` (1/3 cada uno), `tie` (mismo instante, otro
  estado) y `replay` (la misma respuesta otra vez).
- **Repeticiones:** 30 muestras medidas + 5 de calentamiento (descartadas) por caso; 21 casos por
  pase; **tres pases consecutivos** sobre la misma BD:
  - **A**: inmediatamente después del bootstrap (primera escritura, "en frío");
  - **B**: convergencia;
  - **C**: **estado estable**.
  No se descarta ninguna muestra; percentiles por interpolación lineal `(n-1)·p`.
- **Por muestra (fuera del reloj):** WAL con `pg_current_wal_insert_lsn` desde otra conexión;
  contadores de la transacción (`pg_stat_xact_user_tables` dentro de la misma transacción); el
  COMMIT se cronometra aparte.
- **Por pase:** `pg_stat_database` (commits, rollbacks, deadlocks, conflictos) y
  `pg_stat_user_tables` antes/después, y tamaños por relación (`pg_relation_size`,
  `pg_indexes_size`, `pg_total_relation_size`).
- **Mantenimiento del arnés:** antes de cada caso el arnés normaliza el estado y ejecuta
  `VACUUM ANALYZE` (21 por pase). Por eso el HOT de estado estable está medido **con** ese
  mantenimiento entre casos; producción depende de autovacuum.
- **Ensayo de `0007 → 0008`:** `g2.py upgrade` sobre BD separadas de 100 000 y 250 000 partidos
  (más el de la BD del escritor). Una sonda lee `fixtures` cada 50 ms desde otra conexión para
  medir cuánto bloquea la migración a un lector.

## Archivos

- `seed-*.json`: siembra.
- `upgrade-*.json`: migración y bootstrap (duración, recuentos, tamaños, sonda).
- `writes-pass{A,B,C}.json`: la matriz completa, con las muestras crudas (`raw_ms`, `raw_wal_bytes`).
- `SUMMARY.md`: tablas generadas con `python -m tools.perf_lab.g2 summarize <json>...`.
- `run2/`: los mismos archivos para Run 2.
- `COMPARISON.md`: Run 1 frente a Run 2.

Reproducir (desde `backend/`, PostgreSQL local desechable; nunca Neon):

```powershell
$env:PERF_LAB_DATABASE_URL = "postgresql+psycopg://postgres@127.0.0.1:<puerto>/prediktia_lab_<nombre>"
$env:PERF_LAB_ALLOW_DESTRUCTIVE = "127.0.0.1:<puerto>/prediktia_lab_<nombre>"
& $py -m tools.perf_lab init --revision 0007
& $py -m tools.perf_lab seed --fixtures 100000 --output tools/perf_lab/results/seed.json
& $py -m tools.perf_lab.g2 upgrade --output tools/perf_lab/results/upgrade.json
& $py -m tools.perf_lab.g2 writes --label pass-A --output tools/perf_lab/results/writes-passA.json  # y B, C
```
