# DI-A6 G2: Run 1 frente a Run 2 (control de presión de almacenamiento)

**Solo laboratorio.** Nunca se tocó Neon. Resumen y conclusión: `docs/data-integrity-status.md`, "DI-A6 G2".

| Ejecución | Estado | Fecha | Evidencia |
|---|---|---|---|
| **G2_RUN_1** | **STORAGE_PRESSURE_SUSPECTED**: el SSD SATA estaba casi lleno y la máquina llevaba varios días encendida | 2026-10-08 | `*.json` y `SUMMARY.md` de esta carpeta (sin cambios) |
| **G2_RUN_2** | **POST_RESTART / STORAGE_PRESSURE_RELIEVED**: máquina reiniciada; disco C: 107,6 GB libres de 930,4 GB (11,6 %) antes de medir | 2026-10-09 | `run2/*.json` y `run2/SUMMARY.md` |

**Qué se mantuvo igual:** commit `d31eebc` (escritor sin cambios), `g2.py` sin cambios, cluster PostgreSQL 18.6 recién creado con `initdb` y los **mismos ajustes por defecto** (`shared_buffers` 160 MB, `fsync` / `synchronous_commit` on, `max_wal_size` 1 GB, `checkpoint_timeout` 5 min, autovacuum on), la misma siembra (100 000 / 100 000 / 250 000 partidos, semilla 20261004), los mismos 7 modos, 30 + 5 muestras, tres pases A/B/C y el mismo orden (siembra → tres ensayos `0007 → 0008` → pases A, B y C). Mismo hardware, SO, Python 3.13.4, SQLAlchemy 2.1.3 y psycopg 3.3.6. **Qué cambió:** solo el estado de la máquina (reinicio y espacio libre en disco). Run 2 corrió con la sesión de PowerShell en vez de `sh` para lanzar los mismos comandos (el `PATH` de Git Bash no estaba disponible tras el reinicio); no afecta a lo medido.

Δ = (Run 2 − Run 1) / Run 1. Latencias en ms, agrupando las 210 muestras por lote y pase (7 modos × 30).

## Escritor: latencia total por respuesta

| Lote | Pase | p50 R1 → R2 | Δ p50 | p95 R1 → R2 | Δ p95 | máx R1 → R2 |
|---:|---|---|---:|---|---:|---|
| 380 | A | 92 → 71 | −22,6 % | 120 → 93 | −23,1 % | 212 → 117 |
| 380 | B | 84 → 74 | −11,0 % | 106 → 92 | −13,1 % | 128 → 116 |
| 380 | C | 91 → 74 | −18,4 % | 121 → 87 | −28,1 % | 169 → 127 |
| 1000 | A | 232 → 199 | −14,1 % | 296 → 253 | −14,6 % | 585 → 513 |
| 1000 | B | 240 → 207 | −13,7 % | 284 → 268 | −5,9 % | 366 → 997 ¹ |
| 1000 | C | 237 → 202 | −14,7 % | 296 → 230 | −22,1 % | 386 → 259 |
| 2000 | A | 480 → 396 | −17,4 % | 563 → 443 | −21,3 % | 706 → 473 |
| 2000 | B | 477 → 408 | −14,4 % | 561 → 462 | −17,7 % | 970 → 490 |
| 2000 | C | 483 → 403 | −16,6 % | 602 → 463 | −23,2 % | 872 → 1211 ² |

¹ Una muestra de `insert` 1000 con un COMMIT de 802 ms (el resto, 195 ms), durante un checkpoint, igual que el máximo de 970 ms de Run 1 en el pase B.
² Una muestra aislada de `update` 2000 con 1209 ms **antes** del COMMIT (COMMIT 2 ms); el p95 de ese caso es 579 ms. Por eso los umbrales se fijan en percentiles, no en el máximo.

- **Por modo:** la mejora es general. De las 63 combinaciones de modo, lote y pase, 62 bajan su p50 (entre −4,5 % y −29,5 %; la mayoría entre −10 % y −20 %). La excepción es `update` 1000 del pase A (229 → 233, +1,6 %). El p50 por modo está en `results[].total_ms.p50` de cada JSON.
- **Dónde está la mejora (p50 agrupado, pase C):**

  | Lote | antes del COMMIT | cursor SQL | SQLAlchemy antes del cursor (CPU de Python, sin E/S) |
  |---:|---|---|---|
  | 380 | 87,9 → 72,4 (−18 %) | 55,8 → 45,6 (−18 %) | 5,6 → 4,4 (−21 %) |
  | 1000 | 232,8 → 197,5 (−15 %) | 146,7 → 123,4 (−16 %) | 15,4 → 13,1 (−15 %) |
  | 2000 | 476,5 → 397,4 (−17 %) | 298,5 → 248,9 (−17 %) | 32,1 → 25,8 (−20 %) |

  Los pases A y B dan lo mismo (−12 % a −22 % en los tres componentes). **El trabajo de CPU del cliente en Python, que no toca el disco, mejora en la misma proporción que la ejecución SQL**, y el COMMIT (la parte más ligada a `fsync`) no mejora. La lentitud de Run 1 afectaba a toda la máquina, no solo a la E/S de disco: estos datos **no** permiten atribuirla a la presión de almacenamiento frente al tiempo encendida (u otro estado del host).

## Escritor: COMMIT, WAL, HOT y almacenamiento

| Métrica | Lote | Pase A R1 → R2 | Pase B R1 → R2 | Pase C R1 → R2 |
|---|---:|---|---|---|
| COMMIT p50 / p95 (ms) | 380 | 1,7 / 17,0 → 1,2 / 2,6 | 2,9 / 8,1 → 2,9 / 13,1 | 2,9 / 10,1 → 1,3 / 6,0 |
| | 1000 | 1,9 / 23,2 → 2,2 / 33,7 | 2,8 / 25,6 → 2,6 / 19,5 | 2,5 / 15,1 → 4,6 / 10,9 |
| | 2000 | 3,0 / 29,9 → 3,3 / 25,0 | 2,9 / 21,6 → 3,0 / 16,6 | 2,7 / 17,9 → 3,1 / 28,4 |
| WAL por partido, update / unchanged (B) | 380 | 1471 / 938 → 1471 / 938 | 889 / 811 → 885 / 808 | 886 / 812 → 886 / 841 |
| | 1000 | 1109 / 843 → 1069 / 826 | 897 / 829 → 897 / 830 | 894 / 822 → 909 / 821 |
| | 2000 | 1121 / 901 → 1157 / 893 | 925 / 1030 → 1231 / 937 | 1100 / 840 → 1073 / 937 |
| WAL por partido, insert (B) | 380–2000 | 1772–1783 → 1771–1783 | 1745–1758 → 1746–1761 | 1744–1761 → 1744–1763 |
| HOT update / unchanged | 380 | 33 / 85 % → 33 / 85 % | 100 / 100 % → 100 / 100 % | 100 / 100 % → 100 / 100 % |
| | 1000 | 71 / 97 % → 76 / 99 % | 100 / 100 % → 100 / 100 % | 100 / 100 % → 99 / 100 % |
| | 2000 | 73 / 90 % → 75 / 95 % | 99 / 85 % → 62 / 85 % | 100 / 100 % → 97 / 89 % |
| Almacenamiento (B por observación) | todos | 306 → 306 | 305 → 305 | 324 → 323 |

- **COMMIT:** sin cambio material. p50 entre 1,2 y 4,6 ms en las dos ejecuciones; el p95 sube o baja según dónde caigan los checkpoints (Run 2 tuvo checkpoints por WAL casi continuos durante los pases, con `sync` de 0,04–0,6 s).
- **WAL:** sin cambio material con 380 y 1000 (±0–5 %). Con 2000 en los pases B y C hay tres casos que se mueven más (`update` B +33 %, `unchanged` B −9 %, `unchanged` C +12 %), en ambos sentidos y junto a los cambios de HOT: dependen del estado de las páginas, no de la ejecución. Con 380 el WAL de los tres pases es **idéntico** byte a byte por partido: la carga es determinista.
- **HOT:** el patrón se reproduce (bajo justo tras el bootstrap, ~100 % en estado estable con 380 y 1000). Con **2000** varía entre ejecuciones (pase B `update` 99 → 62 %, pase C `unchanged` 100 → 89 %): depende del espacio libre en las páginas y ya era variable en C.12 (88,5 % frente a 75,3 %). El umbral propuesto solo aplica a lotes ≤ 1000, que cumplen en las dos ejecuciones.
- **Almacenamiento, observaciones y recuentos:** idénticos (+584 440 observaciones por pase; `fixtures` +5,2 MiB en el pase A y ~0 después).

## Migración `0007 → 0008` y bootstrap

| Ensayo | Partidos | Migración R1 → R2 (s) | Δ | µs por partido | Lector bloqueado R1 → R2 (s) | Crecimiento (B por partido) |
|---|---:|---|---:|---|---|---|
| BD de ensayo | 100 000 | 7,31 → 7,40 | +1,2 % | 73 → 74 | 7,09 → 7,17 | 615 → 615 |
| BD del escritor | 100 000 | 8,02 → 6,58 | −18,0 % | 80 → 66 | 7,87 → 6,39 | 615 → 615 |
| BD de ensayo | 250 000 | 18,37 → 17,92 | −2,5 % | 73 → 72 | 18,18 → 17,72 | 616 → 616 |

- **Bootstrap:** recuentos idénticos en las dos ejecuciones (observaciones = partidos, un único `evidence_id`, mismos bytes por relación).
- **La migración casi no cambia** en las dos BD de ensayo (+1,2 % y −2,5 %). Solo la BD del escritor baja un 18 %; en Run 1 era la más lenta de las tres (80 µs frente a 73), y en Run 2 queda en línea con las otras. La migración, sobre todo E/S y trabajo del servidor en una sola sentencia larga, **no** muestra la mejora del 15–20 % del escritor; otra razón para no atribuir la diferencia a la presión de almacenamiento sin más pruebas.

## Comparación con C.12 (mismo código del escritor, otra sesión)

| Lote | C.12 p50 por modo | Run 1, pase C | Run 2, pase C |
|---:|---|---|---|
| 380 | 66–78 | 85–98 | **70–79** |
| 1000 | 181–210 | 215–256 | **190–217** |
| 2000 | 362–413 | 457–519 | **378–431** |

La diferencia del 20–25 % frente a C.12 baja a **~0–5 %**: Run 2 queda dentro del rango de C.12 con 380 y a pocos ms del máximo con 1000 y 2000.

## Corrección (comprobación mínima de Run 2)

- **Por muestra (1890 muestras medidas):** 0 violaciones de "ganador = observación"; `UpsertCounts` exactos (creados + actualizados + sin cambios = lote, sin variación entre muestras); el lote completo en observaciones salvo `replay`; `replay` con 0 observaciones y 0 actualizaciones; `older` con 0 actualizaciones; máximo de parámetros por sentencia 380–381, igual que en Run 1 (UNNEST). Alembic `0008` antes y después de cada pase y el código fuente sin cambios durante la ejecución.
- **Sobre toda la BD tras los tres pases:** cabeza `0008`; 100 000 partidos; 100 000 observaciones de bootstrap + 1 753 320 de sync; 0 ganadores que no sean una observación; 0 observaciones más nuevas que la ganadora; 0 pares `(fixture_id, evidence_id)` duplicados; 0 huérfanas; 0 partidos sin mapping; 0 hashes con longitud distinta de 32 B; un solo `evidence_id` multi-temporada (el bootstrap); ≤ 380 filas por evidencia de sync; 0 deadlocks.
- **Sin cambios en la aplicación ni en el arnés** (`g2.py` y `tools/perf_lab` idénticos a `f4871a4`): **no hace falta repetir la suite completa.**

## Reproducir la comparación

Las tablas salen de los JSON con un script de una sola vez (no versionado) que agrupa `raw_ms` por lote y pase y calcula percentiles por interpolación lineal `(n-1)·p`, el mismo método que `g2.py`. Los resúmenes por ejecución: `python -m tools.perf_lab.g2 summarize <json>...`.
