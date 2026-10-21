# Estado: desarrollo modular

## Ciclo actual (desde el checkpoint conjunto DI-A6 + Modular)

- **Baseline canónico:** `1c06ef9` (`origin/integration/di-a6-modular-final`), Alembic `0008` (cabeza única; `0009` reservada para DI-A5D). Rama de trabajo: `feature/modular-m57-stats-as-of`.
- **M5.6** (reconciliador live + scheduler C6): implementado, publicado y verificado con smokes supervisados en producción. Pendiente de activación: piloto de 24 h, catch-up manual y alertas.
- **M5.7A: lectura as-of de estadísticas** (`app/schemas/statistics_knowledge.py`, `app/repositories/statistics_knowledge_repository.py`, `app/services/statistics_as_of.py`). Solo lee; sin migración.
  - **Selección:** por partido y proveedor, observaciones con `available_at <= T` (incluido); manda la más reciente por `(observed_at, id)`, el orden de versionado del esquema.
  - **Reconstrucción:** los valores salen del raw de ESA observación con el normalizador vigente (parser del adapter). Nunca de `fixture_team_statistics` (estado actual fusionado) ni fusionando versiones.
  - **Calidad:** se recalculan los checks de identidad y de valores del service; con algún BLOCKING el partido queda `BLOCKED`, sin valores. Si el hash del raw, la disponibilidad registrada o los BLOCKING que registró el run no se reproducen: `RECONSTRUCTION_MISMATCH`, sin valores y sin elegir ganador.
  - **Estados:** `UNKNOWN_AT_T`, `AVAILABLE`, `PARTIAL`, `EMPTY` (evidencia válida, no ceros), `BLOCKED`, `RECONSTRUCTION_MISMATCH` e `IDENTITY_UNVERIFIED` (añadido en M5.7B).
  - **Procedencia:** `HISTORICAL_SYNTHETIC` (`backfill`, `available_at = kickoff + 6 h`) u `OPERATIONAL` (`live`/`manual`, `available_at = observed_at`). `observed_after(T)` marca lo disponible por política pero recibido después de T.
  - **Contexto prepartido** (`app/services/statistics_prematch.py`, capa pura aparte): corte = kickoff del objetivo; se excluyen el propio partido y los de kickoff igual o posterior.
- **M5.7B: contrato temporal completo y `team_recent_form_v1`** (`app/schemas/prematch_features.py`, `app/services/prematch_features.py`). Solo lee; sin migración ni cambios en contratos de DI-A6 (solo los consume).
  - **Dos instantes:** T (corte de disponibilidad) y H >= T (horizonte de conocimiento). Una observación de estadísticas cuenta si `available_at <= T` y `observed_at <= H`. `OPERATIONAL_STRICT` si H = T; `HISTORICAL_BACKTEST` si H > T: reproducible con H fijo (un refresh posterior a H no cambia nada), pero retrospectivo y así marcado (`observed_after(T)`, `fixture_retrospective(T)`).
  - **Hechos de partidos** (kickoff, equipos, estado): solo de `STRICT_KNOWLEDGE(H)`. El estado actual de `fixtures` solo enumera candidatos. `RETROSPECTIVE_FINAL_RESULTS` no se usa para features. Con T anterior a la evidencia (p. ej. antes del bootstrap de `0008`) el régimen estricto no tiene datos: fail-closed, sin error.
  - **Identidad** (fail-closed, `IDENTITY_UNVERIFIED` sin valores): home/away de la evidencia de DI-A6 en H (solo `KNOWN`); cada mapping de equipo activo y con `created_at <= observed_at` de la observación; BLOCKING recalculados = registrados por el run. Los mappings no tienen historia: una edición manual sin cambiar `created_at` no se detectaría.
  - **`team_recent_form_v1`:** T lo fija el llamador y se valida contra el kickoff conocido en H (T > K, objetivo desconocido, ambiguo o sin el equipo → fail-closed). Ventana = últimos 5 partidos JUGADOS antes de T según la evidencia (FT/AET/PEN, kickoff < T), por (kickoff, id) descendente, sin el objetivo; empate de kickoff en la frontera → `AMBIGUOUS_ORDER`. AET/PEN se excluyen de las medias con su motivo; una muestra sin estadísticas cuenta en la ventana y no se salta a partidos más viejos. Métricas a favor/en contra (tiros, tiros a puerta, córners, posesión, xG): media de los valores no nulos, mínimo 3 por métrica; nunca ceros. `fingerprint()` resume el contenido sin ids de observación (no depende del orden de ingestión).
  - **Pendiente:** disponibilidad sintética de RESULTADOS de fixtures para backtests históricos (semántica de DI: requiere revisión interdepartamental), historia de mappings, persistencia de features.
- **M5.7C: validación empírica (VALIDATED).** Extracción única de solo lectura de la principal (una transacción `REPEATABLE READ READ ONLY`, snapshot 2026-10-08 02:12:59Z, 7 tablas con árbitro, sede y logos vaciados), restaurada en PostgreSQL 18.6 local desechable con `0008` aplicada SOLO en la copia (bootstrap local 02:13:31Z). Datos y contenedor borrados al terminar; 0 escrituras y 0 migraciones en producción. Medido en local (no es una cifra de producción):
  - **Datos:** 18 671 fixtures; 12 064 observaciones de stats (backfill 11 723, manual 258, live 83), una versión por partido; 42 temporadas, 19 competiciones.
  - **Muestra:** 840 partidos finales (20 por temporada con stats, orden md5(id)) × 2 equipos = 1680 features; T = kickoff − 1 h; H = bootstrap_at (`HISTORICAL_BACKTEST`).
  - **Cobertura:** 1680 OK; ventana completa en el 89,5 %; métricas con ≥ 3 muestras: 72,7 % (córners 72,6 %, xG 47,1 %). 380 features sin muestras: 280 de temporadas en curso (stats operativas con `available_at` del 6–7 oct, posterior a T, correcto por contrato) y 100 de 1360 históricas (7,4 %).
  - **Ventana (7842 partidos):** USED 6082, UNKNOWN_AT_T 1219, EMPTY 503, AET/PEN 37, BLOCKED 1. 0 `IDENTITY_UNVERIFIED`, 0 `RECONSTRUCTION_MISMATCH` (BLOCKING recalculados = registrados en todos).
  - **Procedencia:** 100 % de las muestras `HISTORICAL_SYNTHETIC`, 100 % de las features retrospectivas.
  - **Régimen estricto (H = T):** 100/100 `TARGET_UNKNOWN`, esperado: la evidencia de fixtures empieza en el bootstrap.
  - **Rendimiento:** p50 24,5 ms, p95 41,5 ms, máx. 117 ms por feature; 3–7 SELECT constantes (igual con ventana 3/5/10), sin N+1.
  - **Reproducibilidad y leakage:** 400 recálculos idénticos; un refresh tardío no cambia nada con H fijo (30/30) y sí al ampliar H (30/30); cambiar el marcador actual no altera nada; solo SELECT; 0 violaciones de leakage.

- **M5.8B: primer experimento cuantitativo 1X2 a 90' (retrospectivo).** Código en `backend/research/m58/` (fuera del runtime: `app/` no lo importa; dependencias en `requirements-research.txt`, nunca en `requirements.lock`). Modelos probados en `backend/research/tests` con el venv de investigación; el resto en la suite normal.
  - **Datos:** snapshot de solo lectura 2026-10-08 02:45:25Z, restaurado en local con `0008` (H = 02:45:56Z), borrado al terminar. 13 913 filas con etiqueta verificable desde `STRICT_KNOWLEDGE(H)` (excluidas: 656 no finalizadas, 23 AET/PEN cuyo 90' no era empate); 51 AET/PEN liquidados como X; 0 discrepancias con el estado actual; 0 violaciones de leakage; todo `HISTORICAL_BACKTEST`.
  - **Splits** (embargo 7 días): train 7227 (2024-01 → 2025-06), validación 2908 (A/B cronológicas), test 3681 (2026-01 → 2026-10), purgadas 97. Filas informativas (ambos equipos con ≥ 1 muestra): 91,9 % / 94,4 % / 47,5 % (el test incluye temporadas en curso sin stats disponibles en T).
  - **Selección en validación A/B:** alpha(B1) = 200, C(B2) = 0,01, temperatura = 1,0 (sin cambio). Test evaluado una sola vez con la configuración congelada.
  - **Test (log loss, IC 95 % por semanas):** B0 1,0706 [1,061; 1,081], B1 1,0688 [1,059; 1,079], B2 1,0474 [1,035; 1,060]. B2 − B1 = −0,021 [−0,030; −0,013]; B2 − B0 = −0,023 [−0,033; −0,014]. Brier 0,647 / 0,646 / 0,631; RPS 0,225 / 0,225 / 0,217.
  - **Calibración de B2:** ECE medio 0,015; pendientes H 0,85, A 0,90, **D 0,42** (fuera de [0,8; 1,2]).
  - **Criterios de aceptación:** mejora de log loss con IC < 0 sí; leakage 0 sí; **calibración del empate no; cobertura informativa del test 47,5 % < 60 % no** → señal **no confirmada** (INCONCLUSIVE). No se ha vuelto a evaluar test.

- **M5.8C (diagnóstico):** los 23 AET/PEN excluidos en M5.8B eran todos vueltas de eliminatorias a doble partido empatadas en el global tras los 90' (verificado con una lectura de solo lectura acotada): la regla "AET/PEN ⇒ X" de M5.8A era errónea; los datos y DI son correctos.
- **M5.8D: experimento EXPLORATORIO (no confirma señal).** Protocolo registrado antes de extraer datos (`backend/research/m58d/PROTOCOL.md`). Etiqueta 1X2 = `fulltime` del propio partido también en AET/PEN. Solo kickoff < 2026-01-01: el test de M5.8B no se usa. Snapshot de solo lectura 2026-10-08 03:20:19Z, `0008` local (H = 03:20:41Z), borrado al terminar.
  - **Datos:** 10 239 filas (H 45,7 / D 25,6 / A 28,7 %); 7 AET/PEN no empatados a los 90' ahora etiquetados; 0 avisos de coherencia goals/fulltime/extratime; 0 leakage.
  - **Cobertura por causa (96 797 partidos de ventana, < 2026):** USED 93,4 %, EMPTY 6,3 % (casi todo "Primera División" con stats vacías), AET/PEN 0,3 %, calidad/identidad 10 casos, **nunca ingeridas 0 y no disponibles en T 0**. La cobertura pobre del test de M5.8B no aparece antes de 2026 (no se ha inspeccionado ese periodo). xG presente en el 54,8 % de las features.
  - **Origen móvil (4 trimestres de 2025, 5927 partidos evaluados):** log loss B0 1,0666, B1 1,0638, B2* 1,0292 [1,020; 1,040], B2 calibrado por clase 1,0301, ordinal 1,0297. B2* − B1 = −0,035 [−0,042; −0,027]. La calibración por clase y el ordinal no mejoran a B2* (diferencias con IC que incluye 0). C entre 0,003 y 0,01 es lo mejor; más C da peor log loss y p(D) más dispersa.
  - **Empate:** pendiente de calibración de D en B2* = 0,79 [0,52; 1,04] (el IC incluye 1), por clase 0,86 [0,48; 1,15] y ordinal 0,68 [0,40; 0,91]. El 0,42 de M5.8B no se reproduce antes de 2026.
  - **Sensibilidad:** B2* mejora a B1 en 12 de 13 competiciones; empeora en Argentina.
  - **Agrupación:** el informe agrupaba por NOMBRE de competición, y "Primera División" junta competiciones distintas (sus cifras de cobertura y sensibilidad son de ese conjunto). El código agrupa ya por `competition_id`; las métricas experimentales no cambian y no se ha reejecutado (los datos se borraron).
- **Estado:** M5.8D cerrado como investigación exploratoria; señal predictiva NO confirmada.
- **M5.9B: infraestructura local de predicción prospectiva** (`backend/research/m59/`, ver su README): manifiestos JSON sellados de B0/B1/B2 con inferencia en Python puro (equivalente a sklearn a < 1e-12), registro append-only con cadena de hashes, anclajes externos (RFC 3161, hora del servidor de GitHub o del hilo del proyecto; nunca un commit local), emisor con reloj inyectable (T = K − 1 h, H = emisión <= T, sin emisión tardía ni fallback, B2 nulo si no hay features) y contrato de evaluación con preregistro y evaluación única. Todo simulado: 0 predicciones reales. Cierre técnico: el registro distingue CHAIN_VALID / ANCHOR_VERIFIED / EVALUATION_ELIGIBLE (sin anclaje externo verificado no se evalúa) y la cobertura se separa en operativa (todos los elegibles) y evaluable (elegibles con etiqueta); el denominador del umbral confirmatorio queda para decisión gerencial antes del preregistro.

**DI-A6 en producción (decisión del Chief, 2026-10-10):** cerrado y aceptado.

- `A6_PRODUCTION_READ_ONLY_AUDIT: PASS`
- `PRODUCTION_SCHEMA: 0008`
- `A6_PRODUCTION_DATA_STATE: CLOSED_ACCEPTED`
- `FIRST_POST_0008_WRITE: DEFERRED` (no se ha ejecutado ninguna escritura normal posterior a `0008`).

**Candidato de integración compatible con A6 (`integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Origen:** `origin/main` `c3d16d2` + Modular `d31eebc` (que contiene el baseline canónico `1c06ef9`), merge `--no-ff`. Merge-base `a92e8a3`.
- **Drift de `main`:** solo `.github/workflows/diagnostic-c6-neon-rtt.yml` (autocontenido, sin checkout del repositorio). Ningún archivo tocado por ambos lados; 0 conflictos.
- **Esquema:** cabeza única `0008`; `0008_fixture_observations.py` idéntica al baseline; `alembic check` sin operaciones pendientes en PostgreSQL 18.6 local. Sin migraciones nuevas.
- **Contrato A6:** `app/`, `alembic/`, `tests/`, `tools/` y los workflows `ops-*` sin archivos modificados ni borrados respecto de `1c06ef9`. `upsert_fixtures` exige `FixtureEvidence` y solo lo llaman `fixture_sync_service` y `history_backfill_service`.
- **Tests:** suite completa 1472 passed, 0 skipped (PostgreSQL local desechable); research 13 passed.

**Cierre de integración MOD-PAR-010 (2026-10-11; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **HEAD final:** `46bd881b36201146a5f67fb6c438caf5214989e4`. Dos cherry-picks sobre `d3f2bec`, 0 conflictos; solo archivos nuevos bajo `backend/research/` (5 archivos, +996).
- **Paquete de SECONDARY_MODULAR:** origen `37911f402f01e4d61d315b8b21d36f8e54112555`, integrado como `bc4f0f0`. Diagnósticos agregados de disponibilidad (`backend/research/stat_quality_independent/` y sus tests).
- **Paquete de PRIMARY_MODULAR:** origen `abb2fdb78120251cd3a7f6a23d48cc6eb5b7bd05`, integrado como `46bd881`. Tests de regresión de contratos de M5.9B (`backend/research/tests/test_m59_contracts.py`).
- **Validación (en `46bd881`):** `backend/research/stat_quality_independent_tests` 24 passed; `backend/research/tests` 254 passed (venv de investigación, `DATABASE_URL` ficticia e inalcanzable, sin conexión a BD). La suite completa no se ha reejecutado en este HEAD.
- **Límites operativos:** sin cambios de esquema, sin migraciones, sin peticiones a proveedores, sin operaciones de base de datos, sin activación en producción.
- **Seguimientos pendientes de revisión:** normalización UTC de identidad; validación de probabilidades; contrato de errores; semántica de reintentos tardíos; aislamiento del entorno de tests de research.

**Cierre de integración MOD-PAR-015 (2026-10-12; cierre documental MOD-PAR-017; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Decisión departamental:** `MOD-PAR-015 ACCEPTED` (MODULAR_PRINCIPAL). Integración aceptada en local; NO lista para producción.
- **Coordinación:** resultado `CLEAR`, referencia `INTEG-MOD-M59B-013-014-001`.
- **Evidencia de gobierno:** la resolución de coordinación en Slack asociada a esa referencia y la autorización y aceptación explícitas de MODULAR_PRINCIPAL. Ninguna de las dos constaba en el repositorio antes de la ejecución. Enlace o identificador del mensaje de Slack: pendiente de verificación (no disponible ni verificado al redactar esta entrada).
- **Base y HEAD:** base `cd80fcf064fe424266d480ab3b6e7bb7c3a0bcf0`; HEAD antes de esta entrada documental `c833af9ea00a233be8a293b7e35e6caaf8d69663`. Dos cherry-picks en orden, 0 conflictos; 4 archivos bajo `backend/research/` (+311/−1).
- **MOD-PAR-014, diagnósticos de disponibilidad agrupados:** origen `5686617a6556a970c905eb4ee8e6c9fdffa6cc79`, integrado como `ea42e95ab1fb993df07469406e58dd79c56ac907`. Archivos nuevos `backend/research/stat_quality_independent/grouped_availability.py` y su test.
- **MOD-PAR-013, endurecimiento de la validación de probabilidades de M5.9B:** origen `36459cf93688f92b17a97baa6525167180c33e85`, integrado como `c833af9ea00a233be8a293b7e35e6caaf8d69663`. Modifica `backend/research/m59/manifest.py` y `backend/research/tests/test_m59_contracts.py`.
- **Validación (en `c833af9`):** `backend/research/tests` 303 passed; `backend/research/stat_quality_independent_tests` 57 passed; en total 360 passed (venv de investigación, `DATABASE_URL` ficticia e inalcanzable, `TEST_DATABASE_URL` sin definir, sin conexión a BD). Suite completa del backend: NO EJECUTADA.
- **Límites operativos:** sin operaciones de base de datos, sin peticiones a proveedores, sin migraciones, sin activación en producción, sin push, sin promoción a `main`.
- **Pendiente:** la corrección UTC de identidad sigue en `WAIT` y se sigue por separado; la suite completa del backend no se ha ejecutado todavía.

**Cierre de integración MOD-PAR-020 (2026-10-12; cierre documental MOD-PAR-021; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Tareas:** MOD-PAR-019 (endurecimiento de la identidad UTC del emisor de M5.9B), MOD-PAR-020 (integración local) y MOD-PAR-021 (este cierre documental).
- **Decisión departamental:** MOD-PAR-019 aceptada; integración local MOD-PAR-020 aceptada (MODULAR_PRINCIPAL). NO lista para producción.
- **Coordinación:** resultado `CLEAR`, referencia `INTEG-MOD-M59B-019-001`, según lo comunicado por MODULAR_PRINCIPAL (sin registro previo en el repositorio).
- **Base y HEAD:** base `b56fa0302237166381a93d3756fdd907768b80d9`; origen `d05a15759eb94a2009015e8eefff53bb8d4bc48b`, integrado como `22bd128076f97d1976329414041c651c5fade6ab` (HEAD antes de esta entrada documental). Un cherry-pick, 0 conflictos.
- **Archivos de implementación modificados:** `backend/research/m59/emitter.py` y `backend/research/tests/test_m59_contracts.py`.
- **Validación integrada (en `22bd128`):** `backend/research/tests` 319 passed; `backend/research/stat_quality_independent_tests` 57 passed; en total 376 passed, 0 failed (venv de investigación, `DATABASE_URL` ficticia e inalcanzable, `TEST_DATABASE_URL` sin definir, sin conexión a BD). La selección centrada en UTC (17 passed) forma parte de `research/tests`; no es una suite adicional. Suite completa del backend: NO EJECUTADA.
- **Validación aislada original (MOD-PAR-019, según su informe):** 399 passed, 1 skipped, 0 failed, sobre `research/tests`, `research/stat_quality_independent_tests` y `tests/test_research_m59.py`.
- **Conciliación de totales:** las dos ejecuciones NO cubren las mismas suites. La aislada incluye además `backend/tests/test_research_m59.py`, que no formaba parte de la validación autorizada en MOD-PAR-020 y no se ha ejecutado en `22bd128`. Una recolección sin ejecución (`--collect-only`) en `22bd128`, cuyo árbol es idéntico al de `d05a157`, da 400 tests: 319 + 57 + 24. De esos 24, uno usa `db_session` y `tests/conftest.py` lo salta sin `TEST_DATABASE_URL`, lo que es coherente con 376 + 23 = 399 passed y 1 skipped.
- **Limitación de la evidencia:** no se dispone del log original de MOD-PAR-019; sus cifras son las comunicadas, y qué test se saltó se deduce de la recolección y del conftest, no de ese log.
- **Compatibilidad:** instantes equivalentes con zona generan el mismo `prediction_id` nuevo; las identidades UTC existentes y los hashes fijados se conservan; el comportamiento con fechas sin zona no cambia; sin reescritura de registros históricos ni recálculo de cadenas de hashes o anclajes. Los registros históricos externos siguen `UNVERIFIED`: hace falta una revisión de compatibilidad antes de cualquier activación real.
- **Gobierno:** sin cambios de esquema ni migraciones; sin cambios en Data Integrity ni en contratos temporales compartidos; sin operaciones de base de datos ni peticiones a proveedores; sin push ni promoción a `main`; predicciones reales OFF; scheduler OFF.

**Cierre de integración MOD-PAR-025 (2026-10-12; cierre documental MOD-PAR-026; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Tareas:** MOD-PAR-022 (endurecimiento del contrato de errores de M5.9B), MOD-PAR-025 (integración local controlada) y MOD-PAR-026 (este cierre documental).
- **Decisión departamental:** MOD-PAR-022 aceptada; integración local MOD-PAR-025 aceptada (MODULAR_PRINCIPAL). NO lista para producción.
- **Coordinación:** resultado `CLEAR`, referencia `INTEG-MOD-M59B-022-001`, según lo comunicado por MODULAR_PRINCIPAL (sin registro previo en el repositorio).
- **Base y HEAD:** base `d6e1c22a4d0169a012e101669bbb2763e2336845`; origen `6e635ce4fd7a510cee05ffe4a4fbe37e9f397369`, integrado como `03b582f3dbd7c5ba3cc8eb23d136633df494242d` (HEAD antes de esta entrada documental). Un cherry-pick, 0 conflictos.
- **Archivos de implementación modificados:** `backend/research/m59/manifest.py`, `backend/research/m59/registry.py` y `backend/research/tests/test_m59_contracts.py`.
- **Validación integrada (en `03b582f`):** `backend/research/tests` 370 passed; `backend/research/stat_quality_independent_tests` 57 passed; `backend/tests/test_research_m59.py` 23 passed y 1 skipped; en total 450 passed, 1 skipped, 0 failed (venv de investigación, `DATABASE_URL` ficticia e inalcanzable, sin conexión a BD). El test saltado es el que depende de la BD: `TEST_DATABASE_URL` estaba sin definir. Suite completa del backend: NO EJECUTADA.
- **Cambios de contrato:** los manifiestos malformados lanzan `ManifestError` y los registros malformados lanzan `RegistryError` donde el contrato lo documenta; según los tests de regresión integrados, `evaluation.registry_status` trata esos casos de cadena no válida sin romperse y las entradas antes válidas se siguen aceptando. Las firmas públicas no cambian.
- **Compatibilidad:** las huellas fijadas y los hashes canónicos se conservan; las identidades UTC de predicción existentes no cambian; sin reescritura de registros históricos ni recálculo de anclajes.
- **Pendiente (diferido, NO resuelto):** tratamiento de excepciones en la validación de anclajes; validación de `scaler_scale` y de tipos de entrada en B2; endurecimiento de la validación de `prediction_id` en el registro; semántica de reintentos `LATE`; normalización del contrato de errores para fechas sin zona.
- **Gobierno:** sin cambios en Data Integrity ni en el feed compartido de fixtures; sin esquemas ni migraciones; sin reescritura de registros históricos; sin push ni integración en `main`; sin activación de producción ni del scheduler; predicciones reales OFF; scheduler OFF.

**Cierre de integración MOD-PAR-030 (2026-10-12; cierre documental MOD-PAR-031; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Tareas:** MOD-PAR-027 (reintentos `LATE` secuenciales en el emisor de M5.9B), MOD-PAR-029 (cierre con regresiones deterministas), MOD-PAR-030 (integración local) y MOD-PAR-031 (este cierre, solo documental).
- **Decisión departamental:** MOD-PAR-027 y MOD-PAR-029 aceptadas; MOD-PAR-030 aceptada como integración local (MODULAR_PRINCIPAL). La integración local NO implica que esté lista para producción.
- **Coordinación:** resultado `CLEAR`, referencia `INTEG-MOD-M59B-LATE-027-029-001`, según lo comunicado por MODULAR_PRINCIPAL (sin registro previo en el repositorio).
- **Trazabilidad:** base `a108a883586c38e829a3e0080107ad102472a26e`. Implementación: origen `e74ea600b3dff122719f939911d40ff4a7941060`, integrada como `e875afabf9fed71b74a89bf8e8e8b64dd1255c25`. Regresiones: origen `952667a2e49d1f96b55449d80fccd9c16912aeaf`, integradas como `01263ecc451a716793f599bc3ccf27d31f825c70` (HEAD antes de esta entrada documental). Dos cherry-picks en orden, 0 conflictos.
- **Archivos modificados:** `backend/research/m59/emitter.py` y `backend/research/tests/test_m59_contracts.py`; el commit de regresiones solo toca tests.
- **Validación integrada (en `01263ec`):** `backend/research/tests` 423 passed; `backend/research/stat_quality_independent_tests` 57 passed; `backend/tests/test_research_m59.py` 23 passed y 1 skipped; en total 503 passed, 1 skipped, 0 failed, sobre tres rutas distintas y sin contar ningún test dos veces (venv de investigación, `DATABASE_URL` ficticia e inalcanzable, sin conexión a BD). Motivo del skip: «BD de pruebas no autorizada: Falta TEST_DATABASE_URL». Suite completa del backend: NO EJECUTADA.
- **Contrato aprobado:** idempotencia para un único escritor secuencial. La clave lógica terminal es (`model_set`, `fixture_id`, T). Una predicción ya registrada es terminal; sin predicción previa, un `LATE` ya registrado es terminal; la predicción tiene precedencia sobre el `LATE`. Reutilizar una decisión terminal no añade registros duplicados. Las negativas que no son `LATE` se pueden reintentar.
- **Compatibilidad:** se conserva el comportamiento de identidad UTC existente; los formatos históricos del registro y las identidades de hash no cambian; el registro sigue siendo append-only dentro del alcance probado.
- **Riesgos no resueltos:** (1) las contradicciones entre los metadatos históricos y actuales de equipos y competición no se pueden detectar del todo; (2) reutilizar solo por clave lógica cuando los metadatos se contradicen es el comportamiento de compatibilidad existente, NO una prueba de corrección semántica; (3) no hay garantía atómica con varios escritores concurrentes; (4) al reutilizar una decisión terminal no se hace ninguna verificación adicional de la cadena de hashes; (5) los registros históricos externos y sus consumidores siguen `UNVERIFIED`; (6) la suite completa del backend sigue pendiente.
- **Gobierno:** sin cambios de esquema ni migraciones; sin cambios en Data Integrity ni en el feed compartido; sin activación de producción ni del scheduler; sin push ni promoción a `main`.

**Cierre de integración MOD-PAR-035 (2026-10-12; cierre documental MOD-PAR-036; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Tareas:** MOD-PAR-032 (endurecimiento de errores de anclajes de M5.9B), MOD-PAR-034 (verificación estructural independiente, de solo lectura), MOD-PAR-035 (integración local) y MOD-PAR-036 (este cierre, solo documental).
- **Decisión departamental:** MOD-PAR-032 aceptada; MOD-PAR-034 aceptada; MOD-PAR-035 aceptada como integración local (MODULAR_PRINCIPAL). NO lista para producción.
- **Coordinación:** resultado `CLEAR`, referencia `INTEG-MOD-M59B-ANCHOR-032-001`, según lo comunicado por MODULAR_PRINCIPAL (sin registro previo en el repositorio).
- **Trazabilidad:** base `51c1ba58140d8ec9a83410434fafe0f45d553307`; origen (MOD-PAR-032) `26d2239787da84e26cf7d265e4535aeedb63eb3d`, integrado (MOD-PAR-035) como `621224b5bc0b1881add1c8a1dd1c71808c78107a` (HEAD antes de esta entrada documental). Un cherry-pick, 0 conflictos.
- **Archivos modificados:** `backend/research/m59/anchors.py`, `backend/research/m59/evaluation.py` y `backend/research/tests/test_m59_contracts.py`.
- **Validación integrada (en `621224b`):** `backend/research/tests` 448 passed; `backend/research/stat_quality_independent_tests` 57 passed; `backend/tests/test_research_m59.py` 23 passed y 1 skipped; en total 528 passed, 1 skipped, 0 failed, sobre tres rutas distintas (venv de investigación, `DATABASE_URL` ficticia e inalcanzable, sin conexión a BD). El test saltado es el que depende de la BD: `TEST_DATABASE_URL` estaba sin definir. Suite completa del backend: NO EJECUTADA.
- **Correcciones aceptadas:** una autoridad de anclaje malformada lanza `AnchorError`; un anclaje que no es un objeto lanza `AnchorError`; un `record_hash` referenciado no válido falla cerrado; `evaluation.registry_status` trata esa evidencia malformada sin romperse. Se conserva la compatibilidad de `RegistryError`, y los anclajes válidos existentes y los hashes fijados no cambian.
- **Gobierno del proceso:** el incumplimiento original del STOP de preflight en MOD-PAR-032 sigue registrado, según lo comunicado por MODULAR_PRINCIPAL. MOD-PAR-034 verificó de forma independiente la estructura del commit, no los tests. El preflight de la integración MOD-PAR-035 se informó como PASS.
- **Pendiente (diferido, NO resuelto):** excepciones de `make_statement` ante entradas malformadas; entradas de verificación del registro que no son listas; validación de B2; endurecimiento de la identidad en el registro; los riesgos previos a la activación de M5.9B ya documentados arriba.
- **Gobierno:** sin reescritura de registros históricos ni de hashes; sin cambios en Data Integrity ni de esquema; sin push ni promoción a `main`; sin activación de producción ni del scheduler.

**Cierre de integración MOD-PAR-040 (2026-10-12; cierre documental MOD-PAR-041; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Tareas:** MOD-PAR-037 (validación del manifiesto B2 de M5.9B, implementación aislada), MOD-PAR-040 (integración local) y MOD-PAR-041 (este cierre, solo documental: ni implementación ni integración).
- **Decisión departamental:** MOD-PAR-037 aceptada a nivel departamental; MOD-PAR-040 aceptada como integración local (MODULAR_PRINCIPAL). La aceptación de la integración local NO es que esté lista para producción.
- **Coordinación:** resultado `CLEAR`, referencia `INTEG-MOD-M59B-B2-037-001`, según lo comunicado por MODULAR_PRINCIPAL (sin registro previo en el repositorio).
- **Trazabilidad:** base `a594ed47730b4eb459086a9f79a683d5b01497aa`; origen (MOD-PAR-037) `fbf84f932a94716775ac7adabdea164ca86f9051`, integrado (MOD-PAR-040) como `a71ec50297847a52bf0310561524086919789c18` (HEAD antes de esta entrada documental). Un cherry-pick, 0 conflictos.
- **Archivos modificados:** `backend/research/m59/manifest.py` y `backend/research/tests/test_m59_contracts.py`.
- **Validación de la integración MOD-PAR-040 (en `a71ec50`; no se ha vuelto a ejecutar en MOD-PAR-041):** `backend/research/tests` 475 passed; `backend/research/stat_quality_independent_tests` 57 passed; `backend/tests/test_research_m59.py` 23 passed y 1 skipped; en total 555 passed, 1 skipped, 0 failed, sobre tres rutas distintas. Los tests centrados en B2 forman parte de `research/tests` y no se cuentan dos veces. Motivo del skip: «BD de pruebas no autorizada: Falta TEST_DATABASE_URL». Suite completa del backend: NO EJECUTADA.
- **Contrato B2 aceptado, `scaler_scale`:** la validación rechaza una escala nula, incluido `-0.0`; los valores no finitos se siguen rechazando con `ManifestError`; los valores finitos, no nulos y negativos se siguen permitiendo.
- **Contrato B2 aceptado, contenedores:** `numeric_columns`, `flag_columns` y `competitions` tienen que ser listas de verdad; para esos tres campos se rechazan texto, tuplas y diccionarios. No se añade ninguna validación del tipo de los elementos de esas listas, y la semántica de las listas vacías no cambia.
- **Compatibilidad:** los manifiestos válidos ya aceptados siguen siendo válidos; `freeze_b2` no cambia; las huellas fijadas de B0, B1 y B2, los hashes canónicos y las identidades de predicción no cambian.
- **Pendiente (diferido, NO resuelto):** validación de contenedores del registro (MOD-PAR-038/039); comportamiento de `make_statement` ante una cabeza malformada; endurecimiento de la identidad en el registro; consistencia de los metadatos históricos; idempotencia con varios escritores; artefactos históricos externos y sus consumidores; cobertura de la suite completa del backend. Siguen vigentes las demás limitaciones previas a la activación de M5.9B ya documentadas arriba.
- **Gobierno:** sin reescritura de manifiestos históricos; sin cambios en Data Integrity; sin esquemas ni migraciones; sin push ni promoción a `main`; sin activación de producción, de predicciones ni del scheduler.

**Cierre de integración MOD-PAR-044 (2026-10-12; cierre documental MOD-PAR-045; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Tareas:** MOD-PAR-038 (auditoría independiente del defecto de contenedores del registro), MOD-PAR-039 (especificación de compatibilidad, con la Opción A aprobada), MOD-PAR-042 (implementación aislada), MOD-PAR-044 (integración local) y MOD-PAR-045 (este cierre, solo documental: ni implementación ni integración).
- **Decisión departamental:** MOD-PAR-042 aceptada a nivel departamental; MOD-PAR-044 aceptada como integración local (MODULAR_PRINCIPAL). La aceptación de la integración local NO es que esté lista para producción.
- **Coordinación:** resultado `CLEAR` recibido para MOD-PAR-044, referencia `INTEG-MOD-M59B-REGISTRY-042-001`, según lo comunicado por MODULAR_PRINCIPAL (sin registro previo en el repositorio).
- **Trazabilidad:** base `eb3eaecfda8fe534542a0714d5ac1cf2126ffebf`; origen (MOD-PAR-042) `1dc594542576d563eb9391cda8faa4d76fe1e3c7`, integrado (MOD-PAR-044) como `4c58fa8a4e7d0d99abdd66eee4361f0306b28b4f` (HEAD antes de esta entrada documental). Un cherry-pick, 0 conflictos.
- **Archivos modificados:** `backend/research/m59/registry.py`, `backend/research/m59/anchors.py`, `backend/research/m59/evaluation.py` y `backend/research/tests/test_m59_contracts.py`.
- **Validación de la integración MOD-PAR-044 (en `4c58fa8`; no se ha vuelto a ejecutar en MOD-PAR-045):** `backend/research/tests` 570 passed; `backend/research/stat_quality_independent_tests` 57 passed; `backend/tests/test_research_m59.py` 23 passed y 1 skipped; en total 650 passed, 1 skipped, 0 failed, sobre tres rutas distintas. Los 95 casos de regresión de MOD-PAR-042 forman parte de `research/tests` y no se cuentan dos veces. Motivo del skip: «BD de pruebas no autorizada: Falta TEST_DATABASE_URL». Suite completa del backend: NO EJECUTADA.
- **Contrato aceptado, contenedor de registros:** una lista o una tupla siguen siendo los contenedores externos admitidos, y la lista vacía y la tupla vacía siguen siendo válidas y equivalentes. Con cualquier otro contenedor, `registry.verify` lanza `RegistryError` y `anchors.verify_anchor` lanza `AnchorError`, antes de recorrerlo, medirlo o indexarlo. Los registros anclados válidos se siguen aceptando.
- **Contrato aceptado, evaluación:** con un contenedor externo no válido, `registry_status` devuelve su respuesta fail-closed de siempre (`CHAIN_VALID=False`, `ANCHOR_VERIFIED=False`, `EVALUATION_ELIGIBLE=False`) y no verifica anclajes; en ese retorno anticipado puede faltar la nota «sin anclaje externo». `evaluation.classify` rechaza con `RegistryError` un iterable de un solo uso en vez de clasificar todo como `NO_RECORD`.
- **Opción A aprobada:** si el contenedor es una lista o tupla válida y lo malformado es el contenido de la cadena, se conservan los diagnósticos `problems` y `verified_anchors`. No se introduce un retorno anticipado general para toda cadena no válida.
- **Compatibilidad:** los hashes, las huellas, las identidades de predicción y de registro y los registros históricos válidos existentes no cambian.
- **Resuelto respecto de entradas anteriores:** los cierres MOD-PAR-036 y MOD-PAR-041 anotaban como pendiente la validación de contenedores del registro; queda resuelta aquí con el alcance descrito, y esas entradas se conservan tal como se escribieron.
- **Pendiente (diferido, NO resuelto):** comportamiento de `make_statement` ante una cabeza malformada; endurecimiento de la identidad en el registro; consistencia de los metadatos históricos; límites de concurrencia e idempotencia con varios escritores; artefactos históricos externos y sus consumidores; cobertura de la suite completa del backend; los riesgos previos a la activación de M5.9B ya documentados arriba.
- **Gobierno:** sin cambios en Data Integrity; sin esquemas ni migraciones; sin push ni promoción a `main`; sin activación de producción ni del scheduler.

**Cierre de integración M5.10A-INTEGRATION-001 (2026-10-12; cierre documental M5.10A-DOC-CLOSURE-001; `integration/a6-main-compat`, local; sin integrar en `main` ni desplegar):**

- **Alcance:** integración local de M5.10A, la base SINTÉTICA de replicación offline de B0/B1/B2.
- **Tareas:** MOD-PAR-048 (implementación sintética completada), MOD-PAR-051 (verificación focalizada completada), M5.10A-INTEGRATION-001 (integración local completada) y M5.10A-DOC-CLOSURE-001 (este cierre, solo documental: ni implementación ni integración).
- **Decisión departamental:** integración local aceptada (MODULAR_PRINCIPAL). La aceptación de la integración local NO es que esté lista para producción.
- **Coordinación:** referencia `INTEG-MOD-M510A-CLEAR-CONDITIONAL` para la integración, según lo comunicado por MODULAR_PRINCIPAL (sin registro previo en el repositorio; sus condiciones no constan aquí).
- **Trazabilidad:** base `fedb1f420fe540b98526f0f38b899607b491a294`; origen (implementación) `2fe013e0a4fca59862905d6f212c620a58d12d40`, integrado como `3dfb6902ae994a3a82c321bd93bee6160a268440` (HEAD antes de esta entrada documental). Un cherry-pick, 0 conflictos.
- **Archivos añadidos (ninguno existente modificado):** `backend/research/m510a/__init__.py`, `rows.py`, `config.py`, `evaluate.py` y `report.py`; `backend/research/tests/test_m510a_replication.py` y `backend/research/tests/test_m510a_fitted_models.py`.
- **Evidencia (MOD-PAR-051; mismos resultados en la integración sobre `3dfb690`; no se ha vuelto a ejecutar en este cierre):** `test_m510a_replication.py` 130 passed; `test_m510a_fitted_models.py` 2 passed; en total 132 passed, 0 failed, 0 skipped. Solo esos dos archivos: el resto de `research/tests` y la suite completa del backend NO se han ejecutado sobre este commit.
- **Qué aporta M5.10A:** una base de replicación offline solo sintética; identidad determinista del dataset; identidad determinista de la configuración; evaluación desde manifiestos congelados, sin ajustar nada; generación reproducible del informe comparativo; carga útil idéntica byte a byte entre ejecuciones, verificada en el entorno probado (no se afirma equivalencia entre plataformas); huella del informe sin autorreferencia; separación entre la carga útil que se huella y los metadatos operativos.
- **M5.10A NO DEMUESTRA:** rendimiento predictivo real; ausencia de fuga de información histórica; ventaja en apuestas; que esté listo para producción.
- **Pendiente (NO resuelto):** la evaluación histórica; la integración de un dataset histórico real; los contratos con Data Integrity. No ha habido activación de producción.
- **Gobierno:** cierre solo documental; no está listo para producción; sin cambios en Data Integrity; sin esquemas ni migraciones; sin activación de producción ni del scheduler; sin promoción a `main`; sin push.

**Cierre de integración MOD-INT-100-101 — 2026-10-20/21 (`origin/integration/a6-main-compat`, staging compartido; sin integrar en `main` ni desplegar):**

- **Estado:** `SHARED_STAGING_INTEGRATED` / DONE.
- **Destino:** `origin/integration/a6-main-compat`. HEAD de staging verificado: `bf0623d86d38d3eaf7deeeaabdf52dde3cc495b0`. Base de staging anterior: `48e35293e7beb1c891037c8535d024eee434c2f0`.
- **Composición:** cinco commits lineales sobre la base, sin merges. MOD-DEV-101: `cff6d638` → `508ab7e` (R1). Replay de MOD-DEV-100: `fd6e2f3` → `066f375` → `bf0623d`, que corresponden al original, D1 y D2 (orígenes `32bc09a`, `64301e9` y `dc9bea7`), reproducidos con `cherry-pick -x` y con equivalencia de parche verificada (patch-id estable idéntico al de cada origen).
- **Alcance:** exactamente siete rutas de `backend/research`: cuatro modificadas por MOD-DEV-101 (`goals_markets/evaluation.py`, `goals_markets/integer_evaluation.py`, `tests/test_goals_markets.py` y `tests/test_integer_goals_markets.py`) y tres añadidas por MOD-DEV-100 (`market_value_math/__init__.py`, `market_value_math/value.py` y `tests/test_market_value_math.py`). Fuera de esas rutas, el árbol es idéntico a la base.
- **QA independiente (MOD-QA-INT100-101-FINAL-CANDIDATE-001, PRIMARY_MODULAR_TERMINAL, sobre una exportación aislada de `bf0623d`):** 280/280 passed: 61 binarios (`test_goals_markets.py`), 70 enteros (`test_integer_goals_markets.py`) y 149 de valor de mercado (`test_market_value_math.py`). Solo esas tres suites: el resto de `research/tests` y la suite completa del backend NO se han ejecutado sobre este commit.
- **Autorización:** `CHIEF-MOD-INT100-101-FF-001` CONSUMIDA, según lo comunicado por la dirección (MODULAR_PRINCIPAL, Slack). Este documento no la demuestra por sí mismo y no hay registro previo en el repositorio.
- **Verificación remota:** MODULAR_PRINCIPAL verificó de forma independiente en GitHub el HEAD remoto de staging, según lo comunicado. En local, `origin/integration/a6-main-compat` apunta a `bf0623d` (reflog: «update by push», 2026-10-20 23:42:35 −03:00).
- **Canónico histórico:** `integration/di-a6-modular-final` sigue en `1c06ef9c`.
- **Checkout local obsoleto:** la rama local `integration/a6-main-compat` está en `97ee8d5` a propósito. Está obsoleta y no se debe confundir con la rama remota.
- **Límites:** sin promoción a `main`, al canónico ni a producción; sin cambios en Data Integrity; sin esquemas ni migraciones. No es una certificación TRUE_PIT ni una garantía de EV económico aprovechable.
- **Salvedades conocidas (no bloqueantes):** representación de entradas recursivas patológicas; excepciones de `repr` personalizados hostiles, no soportadas; underflow de coma flotante en casos extremos; la procedencia de las probabilidades y de las cuotas la aporta quien llama.
- **Alcance de esta entrada:** registra solo MOD-INT-100-101. Las entradas anteriores y la cabecera de este ciclo se conservan tal como se escribieron y no describen el estado actual. El trabajo integrado en staging entre `97ee8d5` y `48e3529` (según los asuntos de sus commits, MOD-DEV-064 a MOD-DEV-099 y MOD-DATA-083) no se registra aquí.

**Addendum de estado — 2026-10-21 (MOD-DOC-INT102-M59-103A-104A-STATE-001; solo investigación, `NOT_CERTIFIED`; sin integrar en `main` ni desplegar):**

Clases: `VERIFIED_REMOTE` (leído en el remoto con `git ls-remote`), `VERIFIED_INDEPENDENT_QA` (QA de PRIMARY_MODULAR, según lo comunicado por MODULAR_PRINCIPAL; este documento no lo demuestra), `TERMINAL_REPORTED` (informado por el terminal que hizo el trabajo) y `PENDING`.

- **A. Cierre de staging MOD-INT102-M59 (MOD-INT102-M59-FF-EXEC-001):**
  - `VERIFIED_REMOTE`: `origin/integration/a6-main-compat` pasó de `bf0623d86d38d3eaf7deeeaabdf52dde3cc495b0` a `edcf3cd260f6099f7f1bb1d63eb6218328d75372` con un push fast-forward sin forzar, verificado después en el remoto (reflog local: «update by push», 2026-10-21 03:34:32 −03:00).
  - `TERMINAL_REPORTED`: siete commits lineales sobre `bf0623d`, replays con `cherry-pick -x` y patch-id idéntico al de cada origen revisado: MOD-DEV-050 `a6acf8c`, MOD-DEV-055-R1 `b1e0b33`, R2 `f20d9b6`, R3 `58d8893`, MOD-DEV-102C `fc1c7a7`, 102C-R1 `9ef8e5a` y la entrada documental MOD-INT-100-101 `edcf3cd`. Exactamente seis rutas acumuladas: `backend/research/m59/anchors.py`, `backend/research/m59/emitter.py`, `backend/research/tests/test_m59_contracts.py`, `backend/research/quote_temporal_eligibility/__init__.py`, `backend/research/tests/test_quote_temporal_eligibility.py` y `docs/modular-data-status.md`.
  - `VERIFIED_INDEPENDENT_QA`: 824/824 passed (679 de `test_m59_contracts.py` + 145 de `test_quote_temporal_eligibility.py`), solo esas dos suites.
  - **Autorización:** `CHIEF-MOD-INT102-M59-FF-001` CONSUMIDA, según lo comunicado por la dirección; este documento no la demuestra.
  - `VERIFIED_REMOTE`: `main` sigue en `d3f2bec` y el canónico histórico `integration/di-a6-modular-final` en `1c06ef9c`. No hay despliegue de producción ni de un modelo real.
- **B. MOD-DEV-103A-R1, cierre de QA local:**
  - `TERMINAL_REPORTED`: commit `df85c4f4dc3e7537523d548da25313ef0c23d67a` (padre `a284e62954ec3a8caaf736008e2f0d577bc6499e`, MOD-DEV-103A). `backend/research/quote_value_bridge`: puente sintético cuota elegible → cuota justa y EV con los motores existentes. Corrige D-103A-1: una línea Over/Under con exponente extremo (`1E+30`, `9E+999999999`) lanzaba una excepción de `decimal` sin capturar; ahora es `UNSUPPORTED_LINE`.
  - `VERIFIED_INDEPENDENT_QA`: 101/101 passed en `test_quote_value_bridge.py`.
  - **Gestión:** `LOCAL_QA_CLOSED` aceptado formalmente por MODULAR_PRINCIPAL, según lo comunicado. Código congelado en local.
  - **NO publicado en staging** (`df85c4f` no es ancestro de `edcf3cd`). El versionado de cara a consumidores públicos requiere una decisión aparte.
- **C. MOD-DEV-104A-R1, QA pendiente:**
  - `TERMINAL_REPORTED`: MOD-DEV-104A `846f5eb9852363bc58e50b81f734b75051e9e3e7` (padre `edcf3cd`): el emisor solo reutiliza una predicción o un LATE guardados desde una lectura del registro verificada entera. Hijo correctivo R1 `96f2100f27121816bfc171cdedc8f81f38800606` (padre `846f5eb`): D-104A-1, un registro anidado en profundidad daba `RecursionError` en el hash canónico; ahora es `RegistryError`. 734/734 passed en `test_m59_contracts.py`, según el terminal propietario.
  - `PENDING`: QA independiente de PRIMARY sobre `96f2100` activa / pendiente en este punto. `LOCAL_QA_CLOSED` de la dirección NO establecido.
  - **NO publicado en staging** (ni `846f5eb` ni `96f2100` son ancestros de `edcf3cd`).
- **D. Límites y ownership que se mantienen:**
  - MOD-DEV-061 y MOD-DEV-062 siguen en HOLD. M5.10C/D/E, sin relación con esto, quedan excluidos de staging.
  - No hay primer escritor ni instante de emisión autenticados de forma independiente; ni recepción auténtica de cuotas del proveedor ni mapeos certificados; ni emisor prospectivo de modelos verificado; ni garantía atómica con varios escritores del registro; ni TRUE_PIT ni EV certificado.
  - Data Integrity es dueña de la implementación del adapter de cuotas. El DTO normalizado de cuotas V1 de DI sigue solo como diseño (`LOGICAL_DIRECTION_CLOSED`, NO congelado), según lo comunicado. No hay ninguna capacidad verificada del proveedor 5Dollar.
- **E. Próximas puertas (`PENDING`; documentarlas NO autoriza ninguna):**
  1. QA independiente de PRIMARY sobre el hijo exacto 104A-R1 `96f2100`.
  2. Decisión de la dirección sobre el cierre local de 104A.
  3. Posible composición local selectiva, aparte, de 103A y 104A revisados más esta documentación.
  4. QA independiente del candidato compuesto.
  5. Evaluación de topología por el Integration Coordinator.
  6. Una NUEVA aprobación de un solo uso del Chief si más adelante se pide publicar en el staging compartido.
- **Alcance de este addendum:** registra el estado al 2026-10-21. No modifica las entradas anteriores ni las convierte en estado actual.

**Cierre de M5.7 (cerrado para desarrollo Modular; sin integrar en `main` ni desplegar):**

- **Readiness:** apta para experimentación en backtest retrospectivo y marcado (`HISTORICAL_BACKTEST` con H fijo; ~73 % de features utilizables). No apta para simular conocimiento operativo estricto hasta que haya evidencia de fixtures en producción.
- **Limitaciones aceptadas:** mappings sin historia; enumeración desde el estado actual de `fixtures` (los hechos salen de la evidencia); disponibilidad sintética del backfill (marcada como retrospectiva); exclusión de AET/PEN (0,5 %); xG con pocas muestras (tratarla como opcional).
- **Bloqueo para modelar en régimen estricto:** el bootstrap de DI-A6 (todo lo anterior es `UNKNOWN_AT_T`).
- **Corrección dentro de Modular, con autorización operativa:** catch-up de stats (temporadas previas y backlog actual) para subir la cobertura.
- **Gates productivos pendientes (fuera de Modular):** primera escritura normal posterior a `0008` (diferida); activación del scheduler C6 y piloto de 24 h; catch-up; DI-A5D/`0009`; integración en `main`. La aceptación de DI-A6 en producción y la migración `0008` ya están cerradas (ver arriba).

El resto de este documento conserva el estado anterior a este ciclo.

Rama anterior `feature/modular-data-m43`, checkpoint conocido `6952425` (`6952425e0e84bf78d6bc414df85a4b3263d9ff89`). Ver [workstreams.md](workstreams.md) y [agent-rules.md](agent-rules.md), teniendo en cuenta la deuda documental de ownership registrada abajo.

## Estado actual

- **M4.2: completado e integrado.** La integración y validación originales se conservan como antecedente histórico abajo, no como una promoción pendiente ni como el siguiente paso operativo.
- **M4.3: completado.** Backfills reales de Premier League 2025 (380 fixtures) y Liga MX 2025 (337 fixtures).
- **M4.4A: completado.** Discovery de 34 pares P0: 2 ya `completed` y 32 dry-runs realizados (runs #5–#36, todos `dry_run_completed`), sin cambios de dominio.
- **M4.4 real: PAUSADO** hasta validar el hardening de `season_teams` bajo Modular Principal.

El estado operativo y los conteos de M4.3/M4.4A aquí documentados son los comunicados por el usuario; esta actualización documental no ejecuta tests, consultas de BD ni llamadas a proveedores.

## Coordinación: Modular Principal y Modular Auxiliar

- **Modular Principal = ChatGPT + Claude.** Tiene ownership del hardening de `season_teams`, membership/backfill, Q16 y tests directamente relacionados. Para ese trabajo tiene reservados:
  - `backend/app/services/history_backfill_service.py`;
  - `backend/app/services/season_membership.py`: **ruta prevista si Principal decide crearla**; el archivo todavía no existe;
  - Q16 y los history quality checks relacionados en `backend/app/services/history_quality_checks.py`;
  - los tests directamente relacionados con membership/backfill, incluidos los existentes `backend/tests/test_history_backfill.py`, `backend/tests/test_history_quality_checks.py` y `backend/tests/test_history_stale_runs.py`, y los nuevos que Principal cree para ese alcance.
- **Modular Auxiliar = ChatGPT + OpenCode.** No toca esos archivos ni tests mientras Principal trabaja en ellos. OpenCode puede realizar investigaciones, documentación y tareas pequeñas expresamente delegadas; no asume cambios de código ni ownership por iniciativa propia.

Las rutas existentes del servicio, quality checks y tests anteriores se han verificado en el repositorio; Q16 y el nuevo módulo de membership forman parte del hardening pendiente, no se dan por implementados aquí.

### Deuda documental de ownership

[workstreams.md](workstreams.md), [agent-rules.md](agent-rules.md) y [data-integrity-status.md](data-integrity-status.md) mantienen el ownership antiguo de backfills asociado a Data Integrity / Sync. No reflejan todavía la división Principal/Auxiliar ni la reserva actual de membership/backfill para Principal. Se registra esta deuda sin modificar esos documentos; su actualización requiere una tarea expresamente autorizada.

## Módulos completados

Componentes presentes en la rama (código comprobado en el repositorio):

- **Catálogo:** competiciones, temporadas y equipos (`catalog_sync_service.py`, `catalog_repository.py`, `api/routes/catalog.py`).
- **Partidos:** sync de fixtures desde API-Football (`fixture_sync_service.py`, `fixture_repository.py`, `api/routes/fixtures.py`).
- **Proveedores y mapeos:** tablas `providers` y `*_provider_mappings`, columnas `fulltime_*` (migración `0004`).
- **Upsert de fixtures por pares** (`3084996`, trabajo previo que M4.2 reutiliza): marcadores por pares completos y tests de casos límite del adapter. La política final de conservación/limpieza por estado se fijó en el checkpoint y está descrita en [data-integrity-status.md](data-integrity-status.md).
- **M4.2:** backfill histórico (detalle abajo).

M1–M4.1 son antecedentes completados del roadmap. El desarrollo actual ya está en M4.4; este documento no reconstruye ni atribuye detalles de implementación a cada una de esas fases anteriores.

## M4.2: antecedente histórico completado e integrado

El detalle siguiente conserva únicamente como antecedente la implementación y validación del checkpoint histórico `checkpoint/m42-data-integrity @ ebcb076`.

### Objetivo

Importar de forma auditada el histórico de fixtures y resultados de **un par competición-temporada** cerrado, sin dejar nunca un par importado a medias y sin corregir anomalías del proveedor: se detectan y se registran.

### Implementación

Flujo de `run_backfill` (`history_backfill_service.py`):

1. **Registro del run** (`status=running`) en su propia transacción, confirmada enseguida, para que sobreviva aunque se deshagan las escrituras de dominio.
2. **Precondiciones**, sin escribir datos de dominio:
   - la temporada existe en Prediktia (no se crea);
   - no es la temporada actual (la gestiona el sync vivo);
   - no hay ya un backfill `completed` del par, salvo `--refresh`.
3. **Descarga** de la temporada con `RecordingApiFootballProvider`, que además guarda la liga y temporada que declara el proveedor para cada partido.
4. **Análisis solo con lecturas:** contadores nuevo/existente/cambiaría/sin cambios, equipos y `season_teams` que faltarían, y checks previos.
5. **Check bloqueante** → rollback y `blocked`, sin escrituras de dominio.
6. **Dry-run** → rollback y `dry_run_completed`. El run queda registrado.
7. **Ejecución real en una sola transacción:** `ensure_teams`, `upsert_fixtures`, mapeos y `link_teams_to_season`. Después, checks sobre el estado resultante y comprobación de paridad entre lo previsto y lo guardado. Si algo bloquea → rollback y `blocked`. Error de BD (`IntegrityError`, `DataError`, `OperationalError`) → rollback y `failed`. Si todo pasa → commit y `completed`.
8. **Cierre del run** (contadores, checks, mensaje) en otra transacción. Cualquier excepción no prevista marca el run `failed` y se relanza: un run nunca se queda en `running` por un error capturable.

**Quality checks Q1–Q15** (`history_quality_checks.py`). Detectan, nunca corrigen.

| Check | Severidad | Qué detecta |
| --- | --- | --- |
| Q1 | blocking | 0 partidos recibidos, o fuera del rango `--expected-min/--expected-max` si se indica |
| Q2 | blocking | El conteo global de fixtures disminuye |
| Q3 | blocking | `(provider, external_id)` duplicados en `fixture_provider_mappings` |
| Q4 | blocking | Mapeos de fixtures sin fixture |
| Q5 | blocking | Partidos sin `kickoff_at` |
| Q6 | warning | FT/AET/PEN sin `goals` válidos (se guardan con `goals` NULL) |
| Q7 | warning | FT/AET/PEN sin `fulltime` (no se inventa) |
| Q8 | blocking | `fulltime` parcial o negativo |
| Q9 | warning | AET/PEN con `fulltime` mayor que el resultado final |
| Q10 | warning | FT con `fulltime` distinto de `goals` |
| Q11 | warning | `halftime` mayor que `fulltime` |
| Q12 | blocking | Liga/temporada de la respuesta distinta de la pedida, o `external_id` ya guardado en otra season |
| Q13 | blocking | Partidos recibidos sin mapeo del proveedor tras escribir |
| Q14 | warning | Temporada cerrada con partidos pasados en estado no final |
| Q15 | blocking | Local y visitante son el mismo equipo |

Además existe un check `PARITY` (warning) si el estado guardado no coincide con el que predijo el análisis. La predicción usa la política central de `fixture_repository.py` (`predict_score_values`), la misma que aplica el upsert.

**Recuperación de runs abandonados** (`fail_stale_run`): operación administrativa explícita y separada.

- Bloquea la fila `running` del par con `FOR UPDATE`, de modo que dos recuperaciones simultáneas no se pisan.
- Solo actúa si `started_at` es anterior a `now() - stale_after_minutes` (120 min por defecto, mínimo 1).
- Marca el run `failed` con un mensaje de recuperación. No lo borra, no lo marca `completed` y no lanza ningún backfill.
- Resultados posibles: `recovered`, `not_found` o `too_recent`.

### Archivos / componentes relevantes

| Archivo | Papel |
| --- | --- |
| `backend/alembic/versions/0005_season_backfill_runs.py` | Migración de la tabla de runs |
| `backend/app/models/backfill.py` | Modelo `SeasonBackfillRun` |
| `backend/app/models/__init__.py` | Registra `SeasonBackfillRun` para Alembic |
| `backend/app/schemas/backfill.py` | `CheckResult`, `BackfillResult`, `StaleRunRecovery` |
| `backend/app/repositories/backfill_repository.py` | Registro de runs y lecturas de estado (fixtures existentes, equipos, mapeos, conteos, bloqueo de runs) |
| `backend/app/services/history_backfill_service.py` | `run_backfill`, `predict_stored_values`, `fail_stale_run` |
| `backend/app/services/history_quality_checks.py` | Q1–Q15 y su severidad centralizada |
| `backend/app/integrations/football/api_football_history.py` | `RecordingApiFootballProvider`: hereda el adapter sin cambiar el parseo |
| `backend/app/jobs/history_backfill.py` | CLI |
| `backend/tests/test_history_backfill.py` | Tests DB del flujo completo |
| `backend/tests/test_history_stale_runs.py` | Tests DB de recuperación de runs y Q1 |
| `backend/tests/test_history_quality_checks.py` | Tests unitarios de checks, predicción y CLI |
| `backend/tests/test_migration_0005.py` | Tests DB de upgrade/downgrade y constraints |
| `backend/tests/test_fixture_upsert.py` | Tests DB de la política de upsert por pares, incluida la matriz de paridad `predict_score_values` ↔ `ON CONFLICT` |

### Base de datos / migraciones

- **Migración `0005_season_backfill_runs.py`** (revisión `0005`, `down_revision` `0004`). Tabla nueva y aislada: no modifica tablas existentes ni migra datos. El downgrade elimina la tabla (se pierde el historial de runs, no datos deportivos).
- **Tabla `season_backfill_runs`:**
  - Claves: `competition_id` → `competitions` (CASCADE), `season_id` → `seasons` (CASCADE, NULL solo si la temporada no existe), `provider` → `providers.code` (RESTRICT).
  - Estado: `requested_year`, `status`, `is_dry_run`, `is_refresh`, `started_at`, `finished_at`.
  - Contadores: `received`, `new`, `existing`, `changed`, `unchanged`, `new_teams`, `new_season_teams`, `warning`, `blocking` (todos `>= 0`).
  - `checks` (JSONB) y `error_message`.
- **Constraints:**
  - `ck_season_backfill_runs_status`: `running`, `completed`, `dry_run_completed`, `blocked`, `failed`.
  - `ck_season_backfill_runs_finished`: `finished_at` existe si y solo si el run no está `running`.
  - `ck_season_backfill_runs_order`: `finished_at >= started_at`.
  - `ck_season_backfill_runs_dry_run`: un dry-run nunca figura como `completed`, ni al revés.
  - `ck_season_backfill_runs_counters`.
- **Índices:** `ix_season_backfill_runs_competition_year`, `ix_season_backfill_runs_season_status` y el único parcial `uq_season_backfill_runs_one_running` (nunca dos runs `running` del mismo par).
- **Validación de `0005`:** validada sobre `0004` en el checkpoint. `test_migration_0005.py` (upgrade/downgrade, constraints, FK, unicidad del run `running` y JSON de checks): 11 passed. La cadena `0004` → `0005` es única.

### Endpoints o servicios

- **Sin endpoints HTTP nuevos.** M4.2 no toca `api/routes`.
- **Servicios:** `run_backfill(db, competition_id, year, *, dry_run, refresh, provider, expected_range, now)` y `fail_stale_run(db, competition_id, year, stale_after_minutes)`.
- **CLI** (desde `backend/`):

  ```
  python -m app.jobs.history_backfill --competition-id <id interno> --season <año> [--dry-run] [--refresh] [--expected-min N --expected-max M]
  python -m app.jobs.history_backfill --competition-id <id interno> --season <año> --fail-stale-run [--stale-after-minutes 120]
  ```

  Hace 1 petición al proveedor por invocación y escribe en la BD de `DATABASE_URL`. Códigos de salida: `0` completed/dry_run_completed/recuperado; `1` blocked o recuperación rechazada/sin run; `2` failed o error de uso.

### Tests y verificación

**Validación histórica del checkpoint (`checkpoint/m42-data-integrity`, merge `ebcb076`): completa.** No es una nueva ejecución ni valida el hardening pendiente de M4.4.

- **Entorno:** PostgreSQL 18.6 local y desechable (cluster creado solo para la validación, escuchando únicamente en `127.0.0.1`, eliminado al terminar).
- **Contrato de la guarda:** `TEST_DATABASE_URL` más `TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>:<puerto>/<bd>`, con el destino exacto.
- **`pytest -q`:** **489 passed, 0 failed, 0 skipped.** Los **220 tests de BD se ejecutaron realmente**; ninguno se saltó.
- **Tests dirigidos:**

  | Test | Resultado |
  | --- | --- |
  | `test_migration_0004.py::test_upgrade_backfill` | passed en ~0,9 s; **el bloqueo anterior no se reprodujo** |
  | `test_migration_0005.py` | 11 passed (`0005` validada sobre `0004`) |
  | `test_fixture_upsert.py` | 120 passed (política de upsert y matriz de paridad) |
  | `test_history_backfill.py` | 16 passed (flujo completo y `PARITY` validado) |
  | `test_history_stale_runs.py` | 14 passed |
  | `test_sync_idempotency.py` | 11 passed |

- **Neon no se tocó:** no se modificó `.env` y no se usó ninguna URL de Neon para conectarse.

**Antecedente:** en `feature/modular-data`, un intento anterior con PostgreSQL desechable (Docker) se quedó detenido en `test_migration_0004.py::test_upgrade_backfill` sin causa determinada. Ese intento no constituyó validación; queda sustituido por la validación del checkpoint, en la que el test pasa.

### Decisiones técnicas

- **Unidad de trabajo:** un par competición-temporada = una transacción de dominio. El registro del run va en transacciones separadas para que sobreviva a los rollbacks.
- **No se crean temporadas** desde el backfill, y la temporada actual se excluye: la gestiona el sync vivo.
- **Un par ya `completed`** solo se reevalúa con `--refresh`.
- **Los checks detectan y no corrigen.** Severidad centralizada en `SEVERITY`. Q1 es blocking (0 partidos o fuera de rango indica que la temporada no es la esperada). Q6 es warning, para no perder una temporada entera por un partido sin marcador válido.
- **Q12 compara la identidad declarada por el proveedor** (`league.id`, `league.season`) con la pedida, porque `FixtureData` lleva la liga/temporada pedidas. Por eso existe `RecordingApiFootballProvider`, que no cambia el parseo.
- **Predicción de la política de upsert** (`predict_stored_values`) para contar "cambiaría / sin cambios" sin escribir, más una comprobación de paridad después de escribir. Desde el checkpoint, los marcadores se predicen con `fixture_repository.predict_score_values`, que sale de la misma tabla de reglas que el `ON CONFLICT`: el servicio de backfill no tiene una copia propia de la política.
- **La migración `0005` es aditiva**; no se tocó `0004`.
- **La recuperación de runs abandonados es manual y explícita:** nunca automática, nunca lanza un backfill.
- **Las muestras de los checks** se limitan a 5 `external_id`; no se guardan dumps de fixtures.

### Riesgos y pendientes

- **Q2, Q3 y Q4 son globales** (cuentan toda la tabla), no solo el par. Una anomalía previa ajena al par bloquea cualquier backfill. Es una decisión conservadora; su impacto operativo está **Pendiente de verificación** con datos reales.
- **Q12 sin identidad:** si el proveedor no informa la liga/temporada, Q12 bloquea la temporada entera.
- **Pares completos incoherentes con el estado** (por ejemplo, `penalty` completo en un FT): el upsert los guarda tal como llegan. Ningún check del backfill los señala todavía; es una auditoría posterior al checkpoint.

### Resuelto en el checkpoint histórico M4.2

- **Conflictos con Data Integrity** en `api_football.py`, `fixture_repository.py`, `fixture_sync_service.py`, `conftest.py`, `test_api_football_adapter.py` y `test_sync_idempotency.py`: resueltos en `ebcb076`.
- **Acoplamiento con `fixture_repository.py`:** `predict_stored_values` ya no replica la política; importa `_FIXTURE_COLUMNS` y `predict_score_values`. La paridad está cubierta por la matriz de `test_fixture_upsert.py` y por `PARITY` en `test_history_backfill.py`.
- **Guarda de tests destructivos:** unificada con la de Data Integrity (`TEST_DATABASE_URL` + `TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>:<puerto>/<bd>`), más la regla de Modular que bloquea un servidor remoto compartido con desarrollo. `test_conftest_guard.py` usa ese contrato.
- **Migraciones:** cadena `0004` → `0005` única; Data Integrity no creó ninguna migración.
- **Semántica de marcadores al revertir el estado y de `extratime_*`:** política final fijada en el checkpoint (ver [data-integrity-status.md](data-integrity-status.md)).

## M4.3: backfills reales completados

| Par competición-temporada | Fixtures reales | Estado |
| --- | --- | --- |
| Premier League 2025 | 380 | `completed` |
| Liga MX 2025 | 337 | `completed` |

Estos son los 2 pares ya `completed` del conjunto P0 de M4.4A.

## M4.4A: discovery P0 completado

- **Alcance:** 34 pares P0; 2 ya `completed` y 32 dry-runs realizados.
- **Runs:** #5–#36, todos con estado `dry_run_completed`.
- **API-Football:** 32 requests, 0 retries, 0 errores y 0 rate limits.
- **5Dollar:** 0 llamadas.
- **Dominio:** cero cambios; los dry-runs registraron sus runs sin escribir datos de dominio.

### Conteos conocidos tras discovery

| Tabla | Conteo |
| --- | --- |
| `teams` | 617 |
| `season_teams` | 895 |
| `fixtures` | 7643 |
| `fixture_provider_mappings` | 7643 |
| `season_backfill_runs` | 36 |

Son los conteos conocidos tras discovery, no una lectura nueva de la BD en esta tarea.

### Hallazgos pendientes de M4.4A

- **Q14:** Bolivia 2025, Colombia 2025, Ecuador 2025 y Perú 2025.
- Status `Canc` minúsculo detectado en Bolivia 2025; PST/no-final en temporadas cerradas.
- Fixtures posteriores a `season.end_date` donde corresponda.
- Estructuras/rounds complejos o ambiguos; MLS con playoffs best-of-three variables; Argentina 2024/2025 y la deuda conocida de Copa de la Liga.
- `PARITY` incompleto/no explícito.
- Q2/Q3/Q4 globales y duplicación pre/post de Q3/Q4.
- Código de salida ante excepción inesperada.
- Transacción T2 abierta durante la llamada HTTP.

Son hallazgos pendientes de investigación/hardening, no resueltos en esta actualización. Auxiliar **NO los corrige automáticamente** ni cruza las reservas de Principal; cualquier corrección requiere autorización expresa.

## Próximo trabajo modular

### Principal

Hardening de `season_teams` + Q16 + tests directamente relacionados con membership/backfill, bajo su reserva.

### Auxiliar

Siguiente investigación autorizada: **OA-02 — Auditoría de Q14 en Bolivia/Colombia/Ecuador/Perú 2025**. No se ejecuta dentro de OA-01/OA-01B ni autoriza correcciones automáticas o modificaciones de archivos reservados.

### M4.4 real

**PAUSADO hasta validar el hardening de Principal.** No existen nuevas escrituras reales autorizadas todavía; el discovery completado no las habilita.

## Referencias históricas de M4.2

- **Commits de Modular integrados en aquel checkpoint:** `3084996` ("avance 4.2"), `6e3aff1` ("feat: add historical backfill infrastructure"), `ff26513` ("docs: add shared development coordination") y `456c556` ("docs: add modular data status"), sobre la base común `298b2f3` ("mudar a neon 004").
- **`feature/modular-data`:** rama original congelada en `456c556`; no continúa desarrollándose.
