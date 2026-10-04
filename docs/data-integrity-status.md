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

## Verificación

**Verificación del código de `8c00609`: hecha.** Incluye el fix de `b3af1d4` sin cambios en el código de producción.

- **Resultado de `pytest -q`:** 123 passed, 0 skipped.
- **Tests de BD:** los 34 se ejecutaron contra un PostgreSQL 18 local desechable, que escuchaba solo en `127.0.0.1:55432`.
- **Autorización usada:** `TEST_DATABASE_ALLOW_DESTRUCTIVE=127.0.0.1:55432/prediktia_tests`.
- **Bloqueos comprobados antes de la ejecución:** con la misma URL, la suite se bloqueó (34 skipped) en cuatro casos:
  - Sin autorización.
  - Autorización con otro puerto (`127.0.0.1:5432/...`).
  - Autorización en el formato antiguo sin puerto.
  - Autorización con el alias `localhost:55432/...`.
- **Neon no se tocó:** no se modificó `.env` y no se usó ninguna URL de Neon para conectarse.
- **Cluster temporal:** detenido y su directorio eliminado al terminar.

## Pendiente

- **Semántica de `extratime_*`:** no está en `_SCORE_PAIRS`, así que una sync con NULL borra la prórroga guardada. Hay que decidir cómo tratarla, dado que su semántica en API-Football no se considera estable.
- **Marcadores obsoletos tras revertir el estado:** los marcadores conservados no se limpian si el partido pasa a PST/CANC/ABD/NS, ni `penalty_*` si pasa de PEN a FT.
- **Retries/backoff** ante 429, timeouts y 5xx.
- **Rate limits y cuota:** fallar pronto cuando se agota la cuota del proveedor.
- **Normalización de hosts de Neon:** solo contempla `-pooler`. Sería más robusto comparar por el id del endpoint.
- **Merge a `main`:** `b3af1d4`, `8c00609` y los commits de documentación siguen pendientes de merge.
