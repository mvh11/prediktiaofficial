# DI-A5A — Recovery & Failure-Isolation Design Audit

Fase: solo lectura y diseño. No se modificó código, no hubo migraciones, no se llamó a proveedores ni se usó Neon.

- **Código auditado:** `main @ 2c00bff`, leído desde el worktree limpio `prediktiaofficial-di-coordination` (rama `coord/data-integrity` = `main` + el tablero).
- **Tablero:** DI-A2 está en RECOVERY_PENDING (otra máquina) y DI-A3A está ACTIVE (OpenCode, auditoría de uso/presupuesto/frescura). Ninguno es un bloque de diseño de recovery, así que DI-A5A no los duplica. Las fronteras con ellos están en §9.
- **Etiquetas:** `[verificado]` = leído en el código. `[inferido]` = deducido del código, sin ejecutarlo. `[verificar]` = depende de documentación o comportamiento del proveedor que no comprobé.

---

## 0. Hallazgo de partida

**Hoy no existe ningún planificador para la sync viva.** `[verificado]`

- **Syncs vivas:** solo se lanzan a mano con `POST /sync/catalog` y `POST /sync/fixtures` (`api/routes/*.py`). Se ejecutan dentro de la request HTTP, con la sesión de `get_db`.
- **Backfill histórico:** solo se lanza a mano por CLI (`jobs/history_backfill.py`).
- **Arranque:** `main.py` no lanza nada al arrancar (el `lifespan` solo escribe logs).

En consecuencia, "reanudar tras un reinicio" hoy significa que nada se reanuda: alguien tiene que volver a llamar. Esto simplifica el diseño, porque el recovery se puede construir **como parte del futuro scheduler** y no como un subsistema aparte.

---

## 1. Mapa del comportamiento actual

| Capacidad | Estado | Dónde / qué hay |
| --- | --- | --- |
| Retries inmediatos | **AVAILABLE** | `ApiFootballProvider._get`: 3 intentos y un máximo de 60 s de espera. Transitorios: 1 s y 2 s. 429/`rateLimit`: `Retry-After` o 5 s y 10 s. La cuota agotada no se reintenta. **5Dollar no tiene retries** (`five_dollar.py` llama directamente a `get_json`). |
| Partial sync | **PARTIAL** | Cada competición se commitea por separado (`fixture_sync_service.py:91`, `catalog_sync_service.py:49,78`), así que el trabajo hecho se conserva. Pero no queda registro de qué se completó: solo existe en la respuesta HTTP. |
| Failed competition | **PARTIAL** | El error queda en `result.error` de la respuesta y en el log. No se persiste, no tiene `next_retry_at` y no se reintenta solo. |
| Stale backfill runs | **PARTIAL** | `fail_stale_run` funciona bien: `FOR UPDATE`, umbral de 120 min, no borra nada. Pero es **manual**: mientras nadie lo ejecute, el índice `uq_season_backfill_runs_one_running` bloquea ese par. |
| Last successful sync | **MISSING** para la sync viva | Solo existe `fixtures.updated_at` por fila, que no dice si la competición entera se sincronizó. En el backfill, `season_backfill_runs.finished_at` con `status='completed'` cumple ese papel por par. |
| Provider outage | **PARTIAL** | Hay fail-fast dentro de una llamada (retries). No hay detección de caída a nivel de proveedor: con timeouts o 5xx, la sync **sigue** con la siguiente competición (ver §2). No hay incidente persistido ni circuit breaker. |
| Quota exhaustion | **PARTIAL** | Corta la ejecución en curso (`stopped`). No se recuerda: la siguiente llamada manual vuelve a pegar al proveedor. |
| Auth failure | **PARTIAL** | Corta la ejecución en curso. No se recuerda ni se alerta. |
| Process crash | **MISSING** (sync viva) / **PARTIAL** (backfill) | Sync viva: se conserva lo commiteado, pero no queda ningún rastro. Backfill: el run queda en `running` hasta que alguien ejecute `fail_stale_run` a mano. |
| Restart | **MISSING** | Al arrancar no se reanuda nada, ni hay forma de calcular qué quedó pendiente. |

---

## 2. Matriz de aislamiento de fallos

La columna "Alcance real" es lo que deja de hacerse hoy cuando ocurre ese error.

| Error | Alcance real | ¿Correcto? |
| --- | --- | --- |
| Fixture sin `fixture.id` o sin id de equipo | fixture (se salta en silencio, `api_football.py`) | OK; falta un contador o log |
| Par de marcador inválido | campo (se descarta el par y se registra) | OK |
| Fixture sin `fixture["date"]` (`KeyError`) o con `kickoff_at` no parseable (`ValidationError` de Pydantic) | **sync completa / HTTP 500** `[inferido]` | **Para demasiado:** un partido roto tumba las 26 competiciones. Con DI-A2 (comportamiento conocido) el payload estructuralmente inválido se convierte en `ProviderResponseError`: falla **esa competición** y la siguiente continúa. DI-A2 **no** lo aísla por fixture individual |
| Respuesta paginada, 4xx, `errors.season`, JSON inválido | competición | OK |
| Timeout, conexión o 5xx tras los 3 intentos | competición, **y la sync sigue** | **Para demasiado poco:** con el proveedor caído, cada competición gasta hasta 3 intentos (26×3 = 78 intentos HTTP, hasta ~26×(3×10 s + 3 s) ≈ 14 min de request HTTP) `[inferido]` |
| 429 persistente o cuota agotada | proveedor, **solo en esta ejecución** | Bien en la ejecución; no se recuerda para la siguiente |
| Auth (401/403/`errors.token`) | proveedor, solo en esta ejecución | Igual que el anterior |
| `ProviderNotConfiguredError` | cada competición falla por separado (sin petición HTTP) | Inocuo, pero genera 26 errores idénticos |
| `IntegrityError`/`DataError`/`OperationalError` en el upsert de fixtures | competición (rollback) | OK para datos. **Discutible para la conexión**, ver la fila siguiente |
| BD caída a mitad de sync | competición actual (petición ya gastada); después, `get_season` (fuera del `try`) lanza y aborta **la sync completa con HTTP 500**. Lo commiteado se conserva, pero la respuesta se pierde `[inferido]` | Debería ser "sync completa" **antes** de gastar la petición, no después |
| Cualquier error de BD en `sync_catalog` | **sync completa** (no hay `try` alrededor de `upsert_*`) | **Para demasiado** frente a la sync de fixtures. Es inconsistente |
| Error en `/leagues` del catálogo | sync completa | Intencional y correcto |
| Excepción inesperada (bug) | sync completa / proceso | Intencional: los bugs deben verse |
| Crash del proceso | proceso, sin rastro persistido (sync viva) | **Falta** |
| Backfill: proveedor/BD/check | par competición-temporada (run `failed`/`blocked`) | OK |

**Resumen:**
- Se para **demasiado** con un payload roto o con errores de BD del catálogo.
- Se para **demasiado poco** con una caída transitoria del proveedor.
- **No se recuerda nada** entre ejecuciones.

---

## 3. Recovery gaps

1. **G1 — Sin estado persistido por unidad de trabajo.** No se puede responder qué falló, cuándo, qué quedó pendiente, cuándo reintentar ni desde dónde reanudar.
2. **G2 — Sin noción de caída del proveedor.** Los fallos transitorios se tratan siempre por competición.
3. **G3 — Sin memoria de cuota ni de auth** entre ejecuciones.
4. **G4 — Sin exclusión de concurrencia en la sync viva.** Dos `POST /sync/fixtures` simultáneos duplican las peticiones. Además, una respuesta más antigua puede commitearse después que una más nueva y hacer **retroceder el estado de un partido** (por ejemplo, de FT a 2H), porque el `ON CONFLICT` aplica "último en escribir gana" `[inferido; no hay test]`.
5. **G5 — Sin scheduler**, así que no hay reanudación, catch-up ni prioridades.
6. **G6 — Recuperación de stale runs del backfill solo manual.**
7. **G7 — Sin contabilidad de presupuesto** que sobreviva al proceso. Además, `HTTP attempts` ≠ cuota consumida.
8. **G8 — BD caída detectada tarde:** se gasta la petición y luego se aborta.
9. **G9 — 5Dollar sin retries ni clasificación de errores** (rate limit, auth, cuota: todo cae en `ProviderResponseError`).

---

## 4. Diseño mínimo recomendado

### Idea central: no hay "modo recovery"

El scheduler solo hace una cosa: **ejecutar las unidades de trabajo que estén vencidas (`due`), por orden de prioridad, dentro del presupuesto**. Reanudar tras un crash, recuperar ventanas perdidas y volver de una caída son el mismo caso: las unidades que no tuvieron éxito siguen vencidas.

### Por qué no hace falta rastrear ventanas perdidas en fixtures

`GET /fixtures?league&season` devuelve la temporada entera en una sola petición `[verificado: get_fixtures]`. Volver a sincronizar la competición cubre cualquier hueco temporal. **Recuperar una ventana de fixtures equivale a re-sincronizar esa unidad.** No hay que guardar rangos de fechas pendientes.

La excepción son los datos que **no se pueden pedir después** (snapshots de odds, estado en vivo en un minuto concreto). Ahí la pérdida es irrecuperable y lo único posible es **registrar el hueco de cobertura** (integridad temporal). Si 5Dollar ofrece histórico de odds está `[verificar]`.

### Componentes

1. **Unidad de trabajo:** `(job_kind, provider, scope)`. Por ejemplo, `("fixtures_current", "api-football", competition_id)` o `("catalog_teams", …)`. El backfill ya tiene su propia unidad (par competición-temporada) y su propia tabla; no se toca.
2. **Estado actual por unidad** (una fila por unidad, actualizada con upsert): cuándo se intentó, cuándo tuvo éxito, el último error, cuándo vuelve a tocar y el lease de ejecución.
3. **Incidente por proveedor:** una fila por episodio de caída, cuota o auth. Un incidente abierto **es** el circuit breaker: mientras esté abierto, ninguna unidad de ese proveedor se ejecuta salvo la sonda.
4. **Claim por lease en PostgreSQL**, no advisory locks de sesión:
   - Claim con `UPDATE … SET lease_until = now() + X WHERE … AND (lease_until IS NULL OR lease_until < now()) RETURNING`.
   - El crash queda resuelto **sin intervención**: el lease expira solo (es la mejora sobre `fail_stale_run`).
   - **Motivo para no usar `pg_advisory_lock` de sesión:** con el endpoint `-pooler` de Neon (PgBouncer en modo transacción) los locks de sesión no son fiables `[verificar con la URL real]`. `pg_try_advisory_xact_lock` sí es seguro, pero exige mantener la transacción abierta durante la llamada HTTP, que es justo el antipatrón ya detectado en M4.4A ("T2 abierta durante HTTP").
5. **Detección de caída del proveedor:**
   - Disparo: tras **K fallos transitorios consecutivos en unidades distintas**, se abre el incidente y la ejecución se detiene igual que hoy con rate limit. **K es configurable y debe calibrarse**; `K=2` es solo un ejemplo, no una decisión.
   - Sonda: después, se prueba con backoff exponencial y tope.
   - Llamada usada como sonda: `/status` de API-Football si no consume cuota `[verificar]`; si la consume, la unidad de mayor prioridad.
6. **Aislamiento corregido:**
   - Un error de **conexión** de BD (no de datos) aborta la ejecución **antes** de la siguiente petición al proveedor (`OperationalError` ≠ automáticamente caída de conexión; ver DI-A5B en §10).
   - Un payload estructuralmente inválido hace fallar solo su competición (`ProviderResponseError`, comportamiento conocido de DI-A2), no la sync completa. DI-A2 no lo aísla por fixture individual.
   - Los errores de datos del catálogo se aíslan por competición, como en fixtures.
7. **Prioridad y presupuesto** dentro del mismo `SELECT` de unidades vencidas (ver §7).

**Infraestructura:** ni Redis, ni Kafka, ni Celery. Basta un loop asyncio (o un cron que llame a un job Python) más PostgreSQL. Con 26 competiciones y unos 5 requests/min de media no hay volumen que lo justifique.

---

## 5. Datos mínimos a persistir

**Dos conceptos** (tablas), no cinco. Las columnas son orientativas, no un esquema definitivo:

**A) `sync_unit_state`** (una fila por unidad). Responde a qué falló, cuándo, qué está pendiente, cuándo reintentar y desde dónde reanudar.

- `job_kind`, `provider`, `scope_id`: la clave.
- `priority_class`: live/próximo, futuro, catch-up, histórico, mantenimiento.
- `last_attempt_at`, `last_success_at`.
- `consecutive_failures`, `last_error_class` (nombre de la excepción), `last_error_status`, `last_error_message` (truncado y **sin secretos**).
- `next_due_at`: sustituye a `next_retry_at`. Es lo mismo para el éxito (frescura) y para el fallo (backoff).
- `lease_until`.

**B) `provider_incident`** (una fila por episodio). Da evidencia para SLA y hace de circuit breaker.

- `provider`, `kind` (outage / rate_limit / quota / auth), `opened_at`, `last_failure_at`, `closed_at`.
- `endpoint`, `status_code`, `failure_count`, `affected_units`, `retry_after_s`, `next_probe_at`.

Con esas dos tablas:

| Pregunta | Se responde con |
| --- | --- |
| ¿Qué está pendiente? | `next_due_at <= now()` |
| ¿Desde dónde reanudar? | Las unidades sin `last_success_at` reciente |
| ¿Cuándo reintentar? | `next_due_at` / `next_probe_at` |
| ¿El proveedor está caído? | Incidente con `closed_at IS NULL` |

**`sync_run`** (una fila por ejecución) **no es necesario para el recovery.** Solo aportaría auditoría de ejecuciones, que hoy cubren la respuesta y los logs. Lo dejo como **opcional y posterior**, si se pide trazabilidad por ejecución.

---

## 6. Qué NO persistiría

- **Cada intento HTTP** (fila por request): el volumen no aporta al recovery. Basta con contadores agregados (DI-A2 `RequestStats`, en memoria) y, si hiciera falta, un agregado diario.
- **Los payloads del proveedor** ni dumps de fixtures (mismo criterio que las muestras de 5 ids del backfill).
- **Rangos de fechas pendientes en fixtures:** la re-sincronización de temporada completa los cubre (§4).
- **Un contador local como "cuota consumida":** `HTTP attempts` ≠ consumo de cuota confirmado por el proveedor. `/status` puede aportar el **consumo reportado por el proveedor** (`requests.current`/`limit_day` en API-Football, `usage.today` en 5Dollar, ya parseados en `check_status`), pero no atribuye necesariamente ese consumo a un intento individual. Los intentos locales se etiquetan como intentos.
- **API keys, cabeceras ni ningún secreto.**
- **Un estado de "recovering" separado:** una unidad vencida es una unidad vencida.

---

## 7. Relación con el presupuesto de proveedores

**Límites:** API-Football 7.500/día (≈5,2/min de media) y 5Dollar 10/min. No asumo otros.

**Coste actual** `[verificado en los docstrings]`:
- Sync de fixtures completa: 26 peticiones (1 por competición).
- Sync de catálogo: 27 peticiones.
- Con retries, el peor caso es ×3.

**Reglas:**

1. **Coalescencia:** una unidad vencida varias veces se ejecuta una sola vez. El backlog se mide en unidades, no en ticks perdidos. Tras 1 hora apagado, como máximo 26 peticiones de fixtures, no "60 min de ticks".
2. **Prioridad estricta en el `SELECT`:** live/próximo al kickoff > fixtures futuros > catch-up > histórico > mantenimiento.
3. **Reserva:** catch-up, histórico y mantenimiento solo consumen presupuesto si queda por encima de una reserva diaria para live/próximo. El tamaño de la reserva y las clases de frescura **son entrada de DI-A3A (OpenCode)**; no los fijo aquí para no duplicar su bloque.
4. **Ritmo:**
   - 5Dollar: un token bucket de 10/min en memoria **solo es suficiente en una primera arquitectura con un único scheduler/worker**. Si existen varios workers, el rate budget debe coordinarse entre ellos (el lease por unidad evita duplicados, pero no coordina el ritmo global).
   - API-Football: ritmo diario, no ráfagas.
5. **Incidente abierto = 0 peticiones** salvo la sonda. Una caída de 30 min cuesta unas pocas sondas, no 78 intentos por ejecución.
6. **Cuota agotada:** `next_probe_at` = próximo reset diario. La hora exacta del reset de API-Football está `[verificar]`.
7. **Antes de un lote grande** de catch-up o histórico: consultar `/status` (consumo reportado por el proveedor, no atribuible a intentos individuales) y no lanzarlo si la cuota restante no cubre el lote más la reserva.

---

## 8. Escenarios

| Escenario | Comportamiento actual | Deseado | Persistir | Siguiente acción automática |
| --- | --- | --- | --- | --- |
| Proveedor cae 2 min | Cada competición gasta 3 intentos y falla; la sync sigue y quema hasta 78 intentos; nada se recuerda | Tras K fallos en unidades distintas (K configurable) se abre un incidente y se para; las unidades restantes quedan vencidas | incidente (outage) + `consecutive_failures`/`next_due_at` por unidad | Sonda con backoff; si responde, se cierra el incidente y se ejecutan las unidades vencidas por prioridad |
| Proveedor cae 30 min | Igual, multiplicado por cada llamada manual | Igual; el backoff de la sonda crece con tope | lo mismo, más `failure_count` | Igual; catch-up coalescido (≤ 1 petición por unidad) |
| Cuota agotada | Corta esta ejecución; la siguiente vuelve a pegar | Incidente `quota`; nada se ejecuta hasta el reset | incidente con `next_probe_at` = reset | Al reset: `/status`, cerrar el incidente, ejecutar por prioridad respetando la reserva |
| Auth inválida | Corta esta ejecución; sin alerta | Incidente `auth` **sin reintento automático** (no se arregla solo); alerta en log/estado | incidente `auth` | Ninguna hasta un cambio de configuración o reset manual; luego, una sonda |
| PostgreSQL cae | Al inicio: 500 sin gastar nada (bien). A mitad: se gasta 1 petición, rollback, y luego 500; la respuesta se pierde | Un error de conexión aborta antes de la siguiente petición | nada (no se puede); el estado previo sigue en la BD | Al volver: los leases expiran y las unidades sin éxito siguen vencidas |
| El proceso muere tras 10 de 26 ligas | 10 commiteadas sin rastro; relanzar repite las 26 | Se saltan las 10 con `last_success_at` reciente | `last_success_at` por unidad, `lease_until` | El lease de la unidad en curso expira; siguiente tick: 16 unidades |
| La misma sync se lanza dos veces | Las dos corren completas (×2 peticiones); riesgo de retroceso de estado y deadlocks aislados como error de competición | La segunda solo coge unidades sin lease | `lease_until` | La segunda termina sin trabajo o con el que quede libre |
| Prediktia apagado 1 hora | No pasa nada distinto (no hay scheduler); los datos siguen viejos hasta que alguien llame | Al arrancar, las unidades vencidas se ejecutan por prioridad | `last_success_at`/`next_due_at` (ya existen) | Ejecutar unidades vencidas; registrar huecos irrecuperables (odds/live) si aplica |
| Vuelve el servicio con backlog | No aplica (no hay backlog: no hay scheduler) | Coalescencia + prioridad + ritmo + reserva | ninguno nuevo | Primero live/próximo, después el resto según presupuesto |

---

## 9. Posibles conflictos con DI-A2 / DI-A4A (y Modular)

| Zona | Conflicto | Propuesta |
| --- | --- | --- |
| `api_football.py`, `http.py` (retries, clasificación de errores) | DI-A2 (métricas y attempts) y DI-A4A (ciclo de vida de `AsyncClient`) tocan estos archivos | DI-A5 **no toca** `http.py` ni el bucle `_get` hasta que DI-A2 y DI-A4A estén commiteados. La detección de caída va en el **service**, no en el adapter |
| `fixture_sync_service.py`, `catalog_sync_service.py` | DI-A2 añadió `RequestStats`, duración y payload tipado en estos servicios | La implementación de DI-A5 depende de DI-A2 commiteado; si se implementa antes, conflicto seguro |
| `config.py`, `database.py` | DI-A2 añade timeouts de BD; `statement_timeout` también lanza `OperationalError` | `OperationalError` ≠ automáticamente caída de conexión. La futura clasificación debe distinguir al menos: connection lost / invalidated, statement timeout y other operational DB error. Hay que acordarlo con DI-A2 |
| `RequestStats` | Es la fuente natural de los contadores de intentos | Reutilizarla, no duplicarla; mantener `attempts` = intentos HTTP |
| `season_backfill_runs`, `history_backfill_service.py` | **Reservados por Modular Principal** | No reutilizar ni modificar la tabla. Se reutiliza **el patrón**: run en su propia transacción, índice único parcial de "uno en curso", recuperación con umbral. Pasar la recuperación automática por lease al backfill sería una propuesta posterior para Modular |
| DI-A3A (OpenCode) | Presupuesto, frescura y reserva | DI-A5 consume sus salidas (clases de frescura, reserva); no las define |
| Migraciones | Las dos tablas nuevas requieren una migración (`0006`) | Regla: una migración a la vez entre carriles. Hay que coordinarla con Modular antes |

---

## 10. Implementación en bloques pequeños

Orden propuesto. Cada bloque es revisable por separado.

1. **DI-A5B — Aislamiento sin esquema** (sin migración; depende de DI-A2 commiteado):
   - El catálogo aísla los errores de BD por competición, como fixtures.
   - Un error de **conexión** de BD aborta antes de la siguiente petición. Importante: `OperationalError` ≠ automáticamente caída de conexión, porque DI-A2 introduce `statement_timeout`, que también lo lanza. La futura clasificación debe distinguir al menos: connection lost / invalidated, statement timeout y other operational DB error. Esa clasificación **no se implementa todavía** y se acuerda con DI-A2.
   - Tras K fallos transitorios consecutivos en unidades distintas, se corta la ejecución (fail-fast de caída en memoria). K es configurable y calibrable (`K=2` solo como ejemplo).
   - Tests.
2. **DI-A5C — 5Dollar: clasificación de errores y retries** (sin migración; después de DI-A4A, porque toca el cliente HTTP): rate limit, auth y cuota como en API-Football, respetando 10/min.
3. **DI-A5D — Esquema mínimo** (migración `0006`, coordinada con Modular): `sync_unit_state` y `provider_incident`, con constraints que protejan los invariants (estados válidos, `closed_at >= opened_at`, contadores ≥ 0, un único incidente abierto por proveedor y tipo con un índice único parcial). Tests de migración.
4. **DI-A5E — Registro de estado en las syncs existentes:** los endpoints actuales escriben `last_attempt/success/error` y abren o cierran incidentes. Todavía sin scheduler. Esto ya da memoria entre llamadas manuales: se salta un proveedor con incidente abierto.
5. **DI-A5F — Claim por lease:** la sync viva reclama unidades. Resuelve la ejecución doble y el crash sin intervención.
6. **DI-A5G — Scheduler mínimo:** un loop o job que ejecuta unidades vencidas por prioridad, con sonda de incidentes, coalescencia y ritmo, usando las clases y la reserva de DI-A3A.
7. **DI-A5H — Huecos irrecuperables:** registro de cobertura para odds/live cuando exista ese módulo (integridad temporal).

**Pendientes de verificar antes de DI-A5D:**
- si `/status` de API-Football consume cuota;
- la hora del reset diario;
- si `DATABASE_URL` usa `-pooler`;
- si los 5xx o timeouts cuentan como cuota consumida.
