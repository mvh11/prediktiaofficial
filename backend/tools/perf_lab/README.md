# DI-A3B — Local Performance Laboratory

Laboratorio aislado de la app. No forma parte del arranque ni de pytest normal.
Requiere el Python del backend, sus dependencias existentes y PostgreSQL local.
No usa providers, API keys, SQLite, índices experimentales ni migraciones nuevas.

## Contrato de seguridad

- Entrada exclusiva: `PERF_LAB_DATABASE_URL` y `PERF_LAB_ALLOW_DESTRUCTIVE`.
- Solo `postgresql+psycopg`, IP literal `127.0.0.1` / `::1`, puerto explícito distinto
  de 5432, nombre `prediktia_lab_*`, sin query parameters ni variables `PG*`.
- Autorización exacta `host:puerto/bd` (IPv6 entre corchetes), como la guarda de tests.
- `init` exige BD vacía: no borra tablas ni ejecuta downgrade. Aplica Alembic existente.
- Después se exige una marca en el comentario de la BD creada por `init`; no hay
  tablas de benchmark paralelas a las reales. `seed` exige ausencia de datos.
- Se comprueba el servidor/puerto/BD efectivo. Antes de importar la app se fuerza
  DATABASE_URL al destino validado y se vacían las credenciales de providers.
- El engine medido es el real, incluidos los timeouts DI-A2 (conexión 3 s; SQL 60 s).
- Crea un cluster desechable exclusivo, no un túnel a otro servidor.

## Preparar PostgreSQL (PowerShell, desde backend/)

Usa una ruta **nueva** para cada cluster. No reutilices un directorio de producción.
Estos comandos dejan fsync/synchronous_commit/full_page_writes en sus defaults.

```powershell
$pg = 'C:\Program Files\PostgreSQL\18\bin'
$cluster = "$env:LOCALAPPDATA\Temp\opencode\prediktia-di-a3b-pg"
& "$pg\initdb.exe" -D $cluster -U prediktia_lab -A trust --encoding=UTF8 --locale=C
& "$pg\pg_ctl.exe" -D $cluster -l "$cluster\server.log" -o '-h 127.0.0.1 -p 55439' -w start
$py = '.\.venv\Scripts\python.exe'
```

El trust es solo para este cluster desechable escuchando en loopback. Al terminar:

```powershell
& "$pg\pg_ctl.exe" -D $cluster -m fast -w stop
```

## Generar y medir una escala

```powershell
$n = 1000  # también 100000 o 1000000
$db = "prediktia_lab_${n}_measured"
& "$pg\createdb.exe" -h 127.0.0.1 -p 55439 -U prediktia_lab $db
$env:PERF_LAB_DATABASE_URL = "postgresql+psycopg://prediktia_lab@127.0.0.1:55439/$db"
$env:PERF_LAB_ALLOW_DESTRUCTIVE = "127.0.0.1:55439/$db"
& $py -m tools.perf_lab init
& $py -m tools.perf_lab seed --fixtures $n --seed 20261004 --output "tools/perf_lab/results/seed-$n.json"
& $py -m tools.perf_lab bench --samples 30 --warmup 3 --output "tools/perf_lab/results/bench-$n.json"
```

Repite el bloque con `$n = 100000` y, si hay recursos suficientes, `$n = 1000000`.
Se exige una BD vacía por dataset y un nombre de salida nuevo por ejecución. No se
sobrescribe evidencia. Un seed interrumpido requiere otra BD, no reanuda a medias.
El ejemplo usa 30 muestras/3 warm-ups para acotar duración; el default es 100/5.
Con 30 muestras, p99 está muy cerca del máximo y no estima bien una cola rara.
Una corrida all de 100 muestras puede superar cinco minutos en esta máquina.

Selección de benchmark / planes (con las mismas variables de destino):

```powershell
& $py -m tools.perf_lab bench --only lookups --output lookups.json
& $py -m tools.perf_lab bench --only candidate --window-minutes 360 --output candidate.json
& $py -m tools.perf_lab bench --only upsert --output upsert.json
& $py -m tools.perf_lab plans --only candidate --output plans.json
```

`bench` ya registra EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) completo para cada
SELECT hit/miss. `plans` repite las lecturas con warm-up/muestras y guarda planes;
no ejecuta EXPLAIN ANALYZE de escrituras. Cada plan individual es un diagnóstico,
no un percentil de tiempos del servidor.

## Dataset

- Seed fijo; ligas de 20 equipos y calendario ida/vuelta de 380 fixtures por temporada.
- Hasta diez temporadas por liga terminando en 2026; última temporada puede ser parcial.
- Aumentan temporadas/ligas/equipos, no se infla una temporada hasta un millón de partidos.
- Local/visitante distintos, parejas únicas por localía/temporada, diez partidos por
  ronda, horarios variados y reprogramaciones deterministas. As-of fijo 2026-10-04.
- API-Football tiene mapping para todos; el segundo provider para 80% de fixtures.
  Son IDs sintéticos, no una afirmación sobre formatos reales de 5Dollar.
- COPY por chunks de 10.000 exclusivamente para preparación. Se mantienen los
  índices y constraints de las migraciones. VACUUM ANALYZE antes de medir.

## Operaciones y metodología

- Mapping: `fixture_provider_mappings(provider, external_id) -> fixture_id`.
- Fixture: IDs interno/externo, misma proyección SQL, hit y miss.
- Candidatos: season + local + visitante + ventana kickoff ±360 minutos por defecto.
  Es una consulta hipotética, no un reconciliador. Sin fuzzy matching ni OR invertido.
- Lecturas: por defecto 100 claves pseudoaleatorias después de cinco warm-ups. Una conexión y
  transacción reutilizadas; incluye execute/fetchall, no hidratación ORM del endpoint.
- Upserts: `ensure_teams` + `upsert_fixtures` **reales**, con su escritura de mappings,
  para lotes 1/10/100/380/1000, en modos unchanged/update/insert. Equipos ya existentes.
- Cada lote se agrupa por season. 1000 fixtures requieren tres llamadas al path real
  y **un commit de laboratorio**; la sync de producción confirma por competición.
- Payloads Pydantic construidos fuera del reloj; SQLAlchemy genera el SQL dentro.
- Update alterna venue para garantizar cambios; unchanged mantiene fixtures iguales,
  pero los mappings sí actualizan last_seen_at. INSERT confirma y después elimina
  fuera del reloj para mantener cardinalidad; esa limpieza puede afectar cache/bloat.
- Se valida una muestra persistida por caso fuera del reloj. Se normalizan fixtures
  y hace VACUUM ANALYZE antes de cada caso. No se altera la configuración del planner.
- Reloj `perf_counter_ns`; min/p50/p95/p99/max/samples, percentiles por interpolación
  lineal `(n-1)*p`, muestras crudas incluidas. p99 de 100 muestras tiene poca precisión.
- `wall_before_commit_ms`: path Python/repositorio completo antes de commit.
- `sql_cursor_ms`: tiempo driver/round-trip de cursor.execute, no CPU del servidor.
- `commit_ms`: commit real independiente; `total_ms`: path + commit.
- `python_other_ms`: **INFERIDO**, residual de wall menos cursor; no demuestra por sí
  solo que el cuello sea Python. Incluye compilación, fetch y procesamiento.
- Lecturas y upserts son **MEDIDOS**. Cold cache, WAN, concurrencia, nuevos índices,
  providers y otras distribuciones son **NO MEDIDOS**.
- JSON incluye versiones, settings PG, índices existentes, tamaños, Git y hashes de
  fuentes. Un cambio de fuentes durante el run marca el resultado NO COMPARABLE.

No interpretar escalas mayores con 380 filas/season como prueba sobre seasons gigantes.
No extrapolar tiempos locales a Neon. No proponer índices sin evidencia de un plan lento.

## Tests pequeños (sin conexiones)

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_perf_lab.py tests/test_conftest_guard.py
```

La smoke/integración real es `init` + `seed --fixtures 1000` + `bench` sobre el cluster
desechable. Los benchmarks grandes nunca se incorporan a la colección pytest.

## Evidencia inicial incluida

`results/seed-{1000,100000,1000000}.json`: generación y conteos reales.
`results/bench-{1000,100000,1000000}.json`: 25 casos por escala, 30 muestras por caso,
tres warm-ups, muestras crudas, percentiles, 10 planes completos y entorno/hashes.
Las fuentes Python medidas son idénticas entre las tres escalas.

MEDIDO, p50 en ms (upserts incluyen commit):

| Operación | 1.000 | 100.000 | 1.000.000 |
| --- | ---: | ---: | ---: |
| Mapping API-Football hit | 0,189 | 0,235 | 0,217 |
| Mapping segundo provider hit | 0,178 | 0,205 | 0,191 |
| Fixture internal id hit | 0,193 | 0,285 | 0,204 |
| Fixture external_id hit | 0,233 | 0,188 | 0,296 |
| Candidato hit | 0,237 | 0,322 | 0,626 |
| Upsert update, lote 380 | 253,012 | 269,897 | 248,123 |
| Upsert update, lote 1000 | 682,156 | 728,588 | 706,093 |

Python 3.13.4, SQLAlchemy 2.1.3, psycopg 3.3.6, PostgreSQL 18.6, Windows 11 Pro,
Intel i3-12100F (4 cores / 8 threads), ~23,85 GiB de RAM visible. Cluster local con
shared_buffers=128MB, work_mem=4MB, fsync/synchronous_commit/full_page_writes=on.
Locale C, TimeZone America/Santiago, plan_cache_mode=auto, psycopg prepare_threshold=5.
El warm-up de tres muestras no garantiza completar todas las transiciones de
preparación/generic plans del driver. EXPLAIN es una ejecución diagnóstica separada.

Generación (incluye mantenimiento/conteos, excluye migraciones): 1,85 / 10,15 /
96,23 s. Tamaño inicial tablas+índices: 1,55 / 70,10 / 698,97 MiB.

No se observan sequential scans en los 30 planes guardados. Candidato hit pasa de
Index Scan kickoff a Bitmap Heap Scan; en 1M filtra nueve de diez filas de la pareja
entre temporadas, con 16 shared hits, cero shared reads y 0,093 ms de ejecución PG.
Índices candidatos: **NONE** con esta evidencia.

En update de 1000 sobre 1M, p50 cursor=172,189 ms, commit=1,474 ms, total=706,093 ms.
El residual por muestra tiene p50=521,929 ms: justifica perfilar el path fuera del
cursor, no atribuirlo todavía a una función ni cambiar producción. Las medianas de
componentes no suman necesariamente la mediana del total.

Limitaciones: un solo cliente, claves calientes y cohortes fijas en escrituras,
30 muestras (p99 poco preciso), temporada acotada a 380 partidos, sin ascensos,
sin validación de cold cache/concurrencia/WAN. Update de 100 sobre 1M mostró una
mediana elevada (137,696 ms) que requiere repetición; no demuestra crecimiento
monótono. Un intento previo de 100 muestras sobre 1K excedió el timeout del comando
antes de completar; fue descartado y las tres corridas guardadas usan BDs nuevas.

## DI-A3C — Perfil del path de upsert

`profile` reutiliza el mismo destino, guarda, dataset y path real (`repository_write`:
`ensure_teams` + `upsert_fixtures` con sus mappings, un commit por lote). No cambia SQL,
repositorios ni configuración del servidor.

```powershell
& $py -m tools.perf_lab profile --batches 100 380 1000 --modes update unchanged insert `
    --samples 30 --warmup 5 --cprofile-samples 5 --output "tools/perf_lab/results/profile-$n-main.json"
# Repetición independiente (un proceso nuevo por corrida) y control sin prepared statements:
& $py -m tools.perf_lab profile --batches 100 --modes unchanged update --samples 30 --warmup 3 `
    --cprofile-samples 0 --output "tools/perf_lab/results/profile-$n-anomaly100-run1.json"
& $py -m tools.perf_lab profile --batches 100 --modes unchanged update --samples 30 --warmup 3 `
    --cprofile-samples 0 --prepare-threshold off --output "tools/perf_lab/results/profile-$n-anomaly100-noprepare-run1.json"
```

- Tras el warm-up alterna muestras plain (listeners inactivos) e instrumentadas en la misma conexión.
- Instrumentadas: eventos públicos del engine y envoltorios temporales de `ensure_teams`,
  `upsert_fixtures` y `upsert_origin_mappings`, restaurados al terminar. Los segmentos entre
  marcas teselan exactamente `wall_before_commit` (se comprueba en cada muestra).
- Categorías MEDIDAS: `sqlalchemy_pre_cursor` (before_execute→before_cursor_execute: clave de
  caché, compilación si no hay caché, parámetros), `cursor_execute` (incluye adaptación y
  parseo de psycopg), `sqlalchemy_post_cursor` y `python` (intervalo entre fronteras; su
  contenido solo se INFIERE con cProfile, que corre en un pase separado e inflado).
- Por sentencia: familia, executemany, filas devueltas, tamaño del SQL y estado de la caché
  de SQLAlchemy. Por caso: `pg_prepared_statements`, GC (`gc.callbacks`), CPU del hilo y
  `pg_stat_user_tables` antes/después.
- Limitaciones: `thread_time` en Windows avanza en escalones de 15,625 ms (solo válido en
  agregados); la alternancia plain/instrumentada puede sincronizarse con el GC de generación 2
  (en lote 1000 cayó siempre en las plain); no se mide el tiempo exclusivo del servidor.

## DI-A6 — Checkpoint C (G2, medición primero)

`a6` mide el escritor con evidencia y la lectura temporal con el **mismo arnés** sobre dos árboles:
A6 (`0008`) y la línea base pre-A6 (`9a5a5f2`, `0007`, sacada con `git archive` y con este
`tools/perf_lab` superpuesto; `PERF_LAB_CODE_LABEL` la identifica en el JSON). El modo se detecta
por la firma de `upsert_fixtures`; `older`, `tie` y `replay` son NO APLICA en la línea base.

```powershell
# A6: sembrar en 0007 y pasar a 0008 con el bootstrap real (como en producción)
& $py -m tools.perf_lab init --revision 0007
& $py -m tools.perf_lab seed --fixtures 100000 --output tools/perf_lab/results/a6c-a6-100k-seed.json
& $py -m tools.perf_lab upgrade --output tools/perf_lab/results/a6c-a6-100k-upgrade.json
& $py -m tools.perf_lab a6 writes  --output tools/perf_lab/results/a6c-a6-100k-writes.json
& $py -m tools.perf_lab a6 ceiling --output tools/perf_lab/results/a6c-a6-100k-ceiling.json
& $py -m tools.perf_lab a6 hash    --output tools/perf_lab/results/a6c-a6-100k-hash.json
& $py -m tools.perf_lab a6 reads   --output tools/perf_lab/results/a6c-a6-100k-reads-1perfixture.json
& $py -m tools.perf_lab a6 history --per-fixture 10 --ambiguity-every 10 --output tools/perf_lab/results/a6c-a6-100k-history.json
& $py -m tools.perf_lab a6 reads   --output tools/perf_lab/results/a6c-a6-100k-reads-10perfixture.json
```

- `seed` rechaza un esquema `0008`: el COPY no puede fabricar evidencia.
- Escenarios de `writes`: `insert`, `update`, `unchanged` (confirmación), `older` (evidencia
  antigua), `mixed` (1/3 cada uno), `tie` (mismo instante, otro estado) y `replay` (misma respuesta).
  Un grupo de temporada = una respuesta lógica = una evidencia; un COMMIT por lote.
- Por muestra, fuera del reloj: WAL (`pg_current_wal_insert_lsn`), contadores de la transacción
  (`pg_stat_xact_user_tables` leído antes y después dentro de la misma transacción, porque desde
  PG15 incluye contadores pendientes de transacciones anteriores), validaciones de invariantes A6.
- `ceiling`: una sola respuesta creciente hasta el primer fallo, siempre con rollback.
- Las limpiezas de `insert` borran antes la evidencia (`ON DELETE RESTRICT`).
- Resultados y conclusiones: `docs/data-integrity-status.md`, "DI-A6: Checkpoint C". Los JSON
  `results/a6c-*.json` no se versionan (política de `1972577`).
