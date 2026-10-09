# DI-A6: ensayo operativo NO productivo de `0007 → 0008` (evidencia)

**Solo ensayo NO productivo.** Producción no se tocó y `0008` no se aplicó en producción. El runbook propuesto está en
`docs/data-integrity-status.md`, "DI-A6: ensayo operativo de `0008` y runbook de producción".

**Sin datos de conexión.** La URL se cargó de forma opaca. Estos archivos no contienen URL, host, usuario, contraseña,
identificadores de Neon ni valores de filas reales: solo recuentos, tamaños, metadatos y tiempos. Se buscó cada componente de la
URL y los identificadores de rama en todos los archivos: 0 coincidencias.

## Destino

| Dato | Valor |
|---|---|
| Rama | `di-a6-0008-rehearsal`: NON_PRODUCTION, creada desde `production` por IT_SUPPORT_AGENT |
| Guarda | `neon.branch_id` igual al del handoff, comprobado **en cada conexión nueva** (`g2_target.install_branch_guard`); primario |
| Punto de recuperación | rama hija `di-a6-0008-rehearsal-checkpoint` (padre: la rama del ensayo), creada por IT antes del ensayo. **Atestiguada por IT, no verificable desde la sesión de BD** (sin API de Neon). No se tocó |
| PostgreSQL | 18.6, pooled (PgBouncer de Neon), `sslmode=require`, `SELECT 1` p50 152 ms |
| Cliente | Windows 11, Python 3.13.4, SQLAlchemy 2.1.3, psycopg 3.3.6 |
| Código | `lab/data-integrity-a6-g2`: el árbol `d31eebc` (A6 aceptado) más herramientas de laboratorio. Sin cambios en `backend/app` ni `backend/alembic` |

## Pasos y resultado

| Paso | Archivo | Resultado |
|---|---|---|
| 1. Preflight (solo lectura + DDL que se deshace) | `preflight.json` | **PASS** (ver abajo) |
| 2. Migración, `alembic -c alembic.ini upgrade 0008` (punto de entrada del CLI) | `migrate.json` | **COMMITTED_0008** |
| 3. Validación del bootstrap y de la integridad | `validate.json` | **PASS** |
| 4. Prueba del escritor (una, mínima, datos propios del ensayo) | `smoke.json` | **PASS** (17/17) |
| 5. Invariantes A6 de toda la BD tras la prueba | `invariants-after-smoke.json` | **PASS** |

**1. Preflight:**
- **Identidad:** la rama coincide, primario, PostgreSQL 18.6, `0007`, 18 671 partidos y 18 671 mappings (0 sin mapping), `fixture_observations` ausente.
- **Conexión:** pooled con `sslmode=require`.
- **Grafo de migraciones:** una sola cabeza `0008`, sin `0009`, camino `0007 → 0008` = `[0008]`.
- **Escribible:** `transaction_read_only` off; un `CREATE TABLE` dentro de una transacción que se deshace funciona y no deja nada; el rol puede alterar `fixtures`.
- **Rangos del laboratorio:** vacíos.
- **Quiescencia:** 0 otras sesiones, transacciones, xids de escritura, bloqueos sobre `fixtures` y transacciones preparadas; 0 filas `running` en `live_sync_runs`, `season_backfill_runs` y `statistics_runs`.

**2. Migración:**

| Medida | Ensayo | G2 en destino (referencia) |
|---|---:|---:|
| Inicio y fin (UTC) | 2026-10-09 21:37:33 → 21:37:41 | — |
| Migración completa (una transacción, incluye conexión y arranque de Alembic) | **7,30 s** | 6,96 s |
| Sentencias (16) | 4,28 s | 4,10 s |
| Bootstrap (LOCK + INSERT + UPDATE) | **2,01 s** (0,15 + 0,67 + 1,19) | 1,84 s |
| Lector bloqueado (lectura más larga de la sonda) | **3,37 s** | 3,24 s |
| WAL | 25,1 MiB | 25,1 MiB |
| Crecimiento | `fixtures` +5,5 MiB, `fixture_observations` +5,2 MiB (600 B por partido) | igual |

Comprobaciones tras la migración, todas correctas:
- revisión `0008`;
- 18 671 partidos = 18 671 observaciones = 18 671 de bootstrap, con un único `evidence_id`;
- 0 en: partidos sin observación de bootstrap, bootstrap duplicado, ganador distinto de su observación, huérfanas, hashes de longitud distinta de 32 B, columnas de orden nulas.

**3. Validación:**
- un único instante de bootstrap; `last_observed_at` igual a ese instante en todos los partidos;
- 0 observaciones de bootstrap con proveedor;
- **0 partidos cuyo `last_state_hash` no coincide con el recalculado desde la fila** (`fixture_state_hash_v1`);
- 0 observaciones de bootstrap con un hash distinto del de su partido;
- 0 `recorded_at` nulos;
- 0 observaciones más nuevas que la ganadora y 0 `(fixture_id, evidence_id)` duplicados;
- restricciones presentes: UNIQUE `(fixture_id, evidence_id)` y FK hacia `fixtures` con `ON DELETE RESTRICT`;
- mappings: 18 671, 0 partidos sin mapping y 0 mappings huérfanos.

**4. Prueba del escritor:** el escritor real (`ensure_teams` + `upsert_fixtures`) sobre 20 partidos propios del ensayo (ids
reservados, una temporada y una competición del ensayo).

| Paso | `UpsertCounts` (recibidos / creados / actualizados / sin cambios) | Resultado |
|---|---|---|
| Inserción con evidencia E1 | 20 / 20 / 0 / 0 | 20 observaciones; ganador = observación; 20 mappings |
| Repetición de la misma respuesta (E1) | 20 / 0 / 0 / 20 | 0 observaciones nuevas; estado idéntico |
| Actualización con evidencia más nueva | 20 / 0 / 20 / 0 | +20 observaciones; ganador = observación; el estado cambia |
| Evidencia antigua (anterior a E1) con otros datos | 20 / 0 / 0 / 20 | +20 en la historia; **el estado actual no cambia** |
| Escritura con rollback | 20 / 0 / 20 / 0 antes del rollback | 0 observaciones nuevas y estado idéntico (**atómico**) |
| `DELETE` de un partido con historia, en una transacción que se deshace | — | **rechazado** por `RESTRICT`; el partido sigue |

Las 18 671 filas reales quedaron idénticas antes y después (recuento y huella de estado).

**5. Invariantes de toda la BD:**
- 18 671 partidos reales con solo su observación de bootstrap; 20 del ensayo con 60 de sync;
- 0 ganadores que no son observación, 0 observaciones más nuevas que la ganadora, 0 duplicados, 0 huérfanas, 0 hashes de longitud distinta de 32 B;
- solo el bootstrap abarca varias temporadas; 0 deadlocks;
- la BD sigue en reposo.

## Lo que queda en la rama del ensayo

La rama `di-a6-0008-rehearsal` queda en `0008` con el bootstrap y los datos de la prueba: una competición, una temporada, 10 equipos, 20 partidos y 60 observaciones de sync, todo en rangos reservados. La rama de punto de recuperación no se tocó. No se borró nada.

## Reproducir

```powershell
# Desde backend/. Entorno: DI_A6_0008_REHEARSAL_DATABASE_URL (opaca), G2_TARGET_CLASSIFICATION=NON_PRODUCTION,
# REHEARSAL_NEON_BRANCH_ID=<id de la rama>
python -m tools.perf_lab.rehearsal_0008 preflight --expect-fixtures <n> --output <json>
python -m tools.perf_lab.rehearsal_0008 migrate --output <json>
python -m tools.perf_lab.rehearsal_0008 validate --expect-fixtures <n> --expect-mappings <m> --output <json>
python -m tools.perf_lab.rehearsal_0008 smoke --output <json>
python -m tools.perf_lab.rehearsal_0008 invariants --output <json>
```

La herramienta rechaza cualquier destino que no sea una rama NON_PRODUCTION con el `neon.branch_id` esperado. **No sirve
para producción**, por diseño.
