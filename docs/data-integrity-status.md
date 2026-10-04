# Estado: integridad de datos y sync de partidos

Rama `feature/data-integrity-sync`, base `298b2f3`. Ver [workstreams.md](workstreams.md).

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
- **Sync:** un error de BD (`IntegrityError`, `DataError`, `OperationalError`) deshace solo esa competición y se sigue con las demás.
- **Tests:**
  - Los tests `db` exigen un `TEST_DATABASE_URL` explícito, distinto de la BD de desarrollo.
  - También exigen la autorización `TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>/<bd>`.
  - Antes de cada operación destructiva se vuelve a comprobar el destino.

Verificación:

- **Commit actual `b3af1d4`, en este PC:** 48 tests pasan y 34 tests de BD se saltan porque no existe `TEST_DATABASE_URL` autorizada.
- **Verificación DB del commit exacto:** pendiente antes del merge a `main`.
- Una implementación anterior/equivalente fue probada contra PostgreSQL desechable, pero esa ejecución no se usa como evidencia definitiva para `b3af1d4`.

## Pendiente (del review local de `b3af1d4`)

- Ejecutar los tests de BD contra una BD de pruebas autorizada antes del merge.
- `extratime_*` no está en `_SCORE_PAIRS`: una sync con NULL borra la prórroga guardada.
- Los marcadores conservados no se limpian si el partido pasa a PST/CANC/ABD/NS, ni `penalty_*` si pasa de PEN a FT.
- La protección de `TEST_DATABASE_URL` no rechaza `?dbname=`, que puede cambiar la BD real.
- La normalización de hosts de Neon solo contempla `-pooler`. Sería más robusto comparar por el id del endpoint.
