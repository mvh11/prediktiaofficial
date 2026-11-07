"""Monitor de investigación V1 de probabilidades de goles FT90 sobre informes de research.goals_consumer_v1: resúmenes por
cohorte, comparaciones entre cohortes disjuntas y secuencias en orden temporal. Puro y determinista: sin BD, red, disco,
proveedores, variables de entorno ni reloj. Interno, RESEARCH_ONLY, SYNTHETIC_CALLER_SUPPLIED, NOT_CERTIFIED,
WAIT_REAL_SOURCE, HOLD_MODEB_CODE. El consumidor, 109A y 109B se usan tal cual (sus funciones públicas), sin modificarlos.

Interfaz (versión MONITORING_VERSION = "1", contrato CONTRACT_VERSION = "GOALS_RESEARCH_MONITORING_V1"):
  summarize_goals_cohort(report, config) -> dict                    documento COHORT_SUMMARY
  compare_goals_cohorts(reference, candidate, config) -> dict       documento COHORT_COMPARISON
  monitor_goals_cohorts(reports, config) -> dict                    documento MONITORING_SEQUENCE
  verify_monitoring_document(document) -> bool                      ¿conserva su document_digest?
Todo documento es JSON nativo con la misma cabecera (monitoring_id, monitoring_version, contract_version, document,
evidence_status UNVERIFIED_RESEARCH_ONLY, source_status WAIT_REAL_SOURCE, calibration_claim, interpretation,
governance_tags, limitations, config) y document_digest = sha256 del JSON canónico del consumidor (claves ordenadas, sin
espacios, ASCII, sin NaN) del documento sin ese campo. Errores: GoalsMonitoringError (ValueError) y sus subclases.

Configuración (dict EXACTO): {"contract_version": "GOALS_RESEARCH_MONITORING_V1", "min_support": int en [1, 10**6]}.
min_support es el soporte mínimo (filas de la población de la medida) que exige una comparación en CADA cohorte.

Entrada: el dict que devuelve run_goals_research_consumer (o su JSON releído). Solo JSON nativo de tipos EXACTOS (dict
con claves str, list, tuple, str, int con |n| < 10**640, float finito, bool, None), como mucho 64 niveles, 5 000 000 de
nodos y textos de 1 000 000 de caracteres; si no, MonitoringInputError sin ejecutar código del llamador (ni __repr__,
__str__, __format__, __eq__ ni __hash__). Se trabaja sobre una copia; la entrada nunca se modifica.

Evidencia de una cohorte (en este orden; el primer fallo decide y la cohorte queda REJECTED con su motivo):
  1. Forma de informe completo del consumidor (un agregado, un resumen o un informe sin libro mayor no es evidencia):
     EVIDENCE_NOT_AUDITABLE; otra versión del consumidor: CONSUMER_VERSION_MISMATCH.
  2. Instantes: holdout_start / holdout_end de cada pliegue y kickoff_at de cada fila tienen que ser el texto UTC canónico
     del consumidor ("YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00"). Otra forma (sin segundos, otra fracción, sin zona, "Z"):
     TIMESTAMP_PRECISION_UNKNOWN; otro desfase o texto no canónico: TIMESTAMP_NOT_CANONICAL_UTC. Ventanas vacías,
     invertidas, solapadas o desordenadas, o una fila fuera del holdout de su pliegue: TEMPORAL_EVIDENCE_INCONSISTENT.
  3. Auditoría completa con la función publicada aggregate_goals_consumer_reports([informe]) del consumidor: report_digest,
     identidades de 109A y 109B, libro mayor fila a fila, 109B re-ejecutado y cuerpo reconstruido. Sus motivos de rechazo
     se conservan tal cual (EVIDENCE_DIGEST_MISMATCH, SOURCE_IDENTITY_MISMATCH, EVIDENCE_NOT_AUDITABLE...).
  4. Conciliación ANTES de cualquier estadística, con un recuento propio fila a fila del libro mayor: denominadores
     agregados y por pliegue = los del informe; outcome_counts de 109A de cada pliegue = TODAS las liquidadas del pliegue
     (ESTIMATE y NO_ESTIMATE); outcome_counts de 109B = SOLO las puntuadas; coverage de 109B = total, puntuadas,
     NO_ESTIMATE y ESTIMATE sin etiqueta. Si un informe ya auditado no cuadra: MonitoringIntegrityError (sin resultado).
  5. Error del modelo: Brier y log loss se reconstruyen fila a fila con las fórmulas publicadas que usa 109A y se exige
     igualdad EXACTA con el model.brier / model.log_loss de 109A en cada pliegue. Si no: NOT_RECONCILED y se omiten.
Los digest prueban consistencia, no autenticidad, cronología histórica auténtica ni admisión de la fuente: la evidencia
sintética aportada por el llamador nunca es PIT admisible.

Denominadores (los del consumidor; nunca se mezclan):
  input_total = estimate_input + no_estimate_input                         filas del holdout
  label_missing_input = label_missing_estimate + label_missing_no_estimate  sin etiqueta final (motivo por fila)
  settled_total = scored_settled + abstained_settled                       con etiqueta final (PUSH incluido)
  scored_settled: ESTIMATE con etiqueta (las únicas que miden el modelo); abstained_settled: NO_ESTIMATE con etiqueta.
PUSH es una clase liquidada propia de las líneas enteras, nunca VOID; un partido no terminado (CANC, ABD, PST...) es
etiqueta ausente (status_not_finished). Una NO_ESTIMATE sin etiqueta no está liquidada ni puntuada. Las etiquetas
ausentes y las abstenciones nunca son errores del modelo.

Medidas (cada una es una media sobre las filas de UNA población de UNA cohorte; nunca una media de medias, de pliegues
ni de informes):
  estimate_coverage        ESTIMATE / input_total. Cuánto se pronuncia el productor; no dice nada de su acierto.
  label_missing_rate       label_missing_input / input_total. Completitud de las etiquetas; no distingue un aplazamiento
                           definitivo de un resultado aún no observado.
  abstention_rate_settled  abstained_settled / settled_total.
  settled_share.<clase>    liquidadas de la clase / settled_total (semántica 109A: ESTIMATE y NO_ESTIMATE). Composición
                           observada de resultados; un cambio no es por sí solo un cambio del modelo ni de la fuente.
  scored_share.<clase>     puntuadas de la clase / scored_settled (semántica 109B).
  brier_mean               media fila a fila del Brier publicado sobre las puntuadas: binario de dos componentes (0-2) en
                           BTTS y líneas .5; tres clases OVER/UNDER/PUSH en líneas enteras. No comparable entre bases.
  log_loss_mean            media fila a fila de -log(max(p_observada, 1e-15)) sobre las puntuadas.
  ece (calibration)        el ECE de 109B de la cohorte sobre sus propias filas, con sus percentiles de bootstrap tal cual.
Las medidas que dependen del resultado (todas salvo estimate_coverage y label_missing_rate) exigen etiquetas completas
para comparar: con alguna etiqueta ausente en cualquiera de las dos cohortes son NOT_COMPARABLE (INCOMPLETE_LABELS).

Comparación (compare_goals_cohorts y cada par consecutivo de monitor_goals_cohorts):
  NOT_COMPARABLE  evidencia rechazada (EVIDENCE_REJECTED), el mismo informe (DUPLICATE_REPORT), fixtures compartidos
                  (OVERLAPPING_FIXTURES), otro productor o versión, otro mercado, línea o definición del objetivo (BTTS
                  binario nunca frente a O/U de tres clases), otra configuración de 109B, ventanas de holdout que se
                  solapan (TEMPORAL_OVERLAP) o una candidata que no empieza cuando la referencia ya terminó
                  (TEMPORAL_ORDER_INVALID: orden imposible o fuga del periodo posterior a la línea base).
  NO_COMPARISON   alguna cohorte sin filas de holdout (EMPTY_COHORT), o ninguna medida con datos en las dos.
  INSUFFICIENT_SUPPORT  ninguna medida alcanza min_support en las dos cohortes (o el soporte mínimo de 109B en el ECE).
  COMPARED        al menos una medida comparada. Cada medida lleva su propio estado y motivos con el mismo vocabulario.
Diferencia = candidata - referencia, descriptiva. Su incertidumbre es un bootstrap por bloques de semana: cada réplica r
de la configuración compartida de 109B (seed, iterations) remuestrea con reemplazo las semanas de cada cohorte por
separado con block_draw(seed, r, j, W) publicado por 109B (referencia j = 0..Wr-1, candidata j = Wr..Wr+Wc-1) y
recalcula las dos medidas; se publican los percentiles P10/P50/P90 por rango más cercano de la diferencia y si P10-P90
excluyen el cero. Son percentiles EXPLORATORIOS, NO un intervalo de confianza ni un contraste: sin corrección por
multiplicidad, no prueban un cambio del modelo, de la fuente ni una relación causal. Estados: EXPLORATORY_BOOTSTRAP,
INSUFFICIENT_WEEKS (menos de min_weeks de 109B en alguna cohorte), SHARED_WEEKS_NOT_INDEPENDENT (las cohortes comparten
una semana ISO), UNDEFINED_IN_REPLICATE (una réplica sin población: nunca se descarta en silencio).
El ECE se compara solo si las dos cohortes cumplen el soporte total de 109B y tienen más de una clase observada; su
diferencia NO tiene incertidumbre emparejada (las réplicas de 109B son de cada cohorte y se publican ordenadas).

No se calcula (intencionadamente): medias de ECE, Brier o log loss entre informes; agregación de cohortes; las
comparaciones con la tasa base de 109A (sus ajustes son por pliegue); umbrales, alarmas o certificados de deriva;
p-valores o intervalos de confianza; ECE por clase; cualquier pronóstico, selección, valor esperado o stake.

Secuencia: las cohortes VERIFIED se ordenan por el inicio de su primera ventana (y report_digest), las REJECTED detrás por
input_digest; se compara cada una con la anterior. Fixtures repetidos o informes duplicados en cualquier par, una
cohorte rechazada o un par NOT_COMPARABLE hacen NOT_COMPARABLE la secuencia; con menos de dos cohortes verificadas es
NO_COMPARISON. Cualquier permutación de la entrada da el mismo documento y el mismo digest.

Errores (espacio propio, versionado; el mensaje es determinista y acotado a 400 caracteres):
  GoalsMonitoringError(ValueError)
    MonitoringInputError       la entrada no es JSON nativo exacto y acotado, o no tiene la forma de la llamada
      MonitoringConfigError    la configuración no tiene la forma pedida
    MonitoringIntegrityError   un informe auditado no concilia con el recuento del monitor (fail-closed)
"""

from research.goals_monitoring_v1.contract import (BELOW_MIN_SUPPORT, CALIBRATION_CLAIM, COMPARED, CONFIG_FIELDS,
                                                   CONSUMER_VERSION_MISMATCH, CONTRACT_VERSION, DUPLICATE_REPORT, EMPTY_COHORT,
                                                   EVIDENCE_NOT_AUDITABLE, EVIDENCE_REJECTED, EXPLORATORY_BOOTSTRAP,
                                                   FEWER_THAN_TWO_COHORTS, GOVERNANCE_TAGS, INCOMPLETE_LABELS, INSUFFICIENT_SUPPORT,
                                                   INSUFFICIENT_WEEKS, INTERPRETATION, LIMITATIONS, LINE_MISMATCH, MARKET_MISMATCH,
                                                   MEETS_MIN_SUPPORT, MODEL_ERROR_NOT_RECONCILED, MONITORING_ID, MONITORING_VERSION,
                                                   NO_COMPARISON, NO_DATA, NO_MEASURE_WITH_MIN_SUPPORT, NOT_COMPARABLE,
                                                   NOT_RECONCILED, OVERLAPPING_FIXTURES, PRODUCER_MISMATCH,
                                                   PRODUCER_VERSION_MISMATCH, RECONCILED, REJECTED, RELIABILITY_CONFIG_MISMATCH,
                                                   RELIABILITY_SAMPLE_INSUFFICIENT, SHARED_WEEKS_NOT_INDEPENDENT,
                                                   SINGLE_OBSERVED_CLASS, TARGET_DEFINITION_MISMATCH, TEMPORAL_EVIDENCE_INCONSISTENT,
                                                   TEMPORAL_ORDER_INVALID, TEMPORAL_OVERLAP, TIMESTAMP_NOT_CANONICAL_UTC,
                                                   TIMESTAMP_PRECISION_UNKNOWN, UNDEFINED_IN_REPLICATE, VERIFIED,
                                                   GoalsMonitoringError, MonitoringConfigError, MonitoringInputError,
                                                   MonitoringIntegrityError)
from research.goals_monitoring_v1.monitor import (COHORT_COMPARISON, COHORT_SUMMARY, IDENTITY_FIELD, MONITORING_SEQUENCE,
                                                  compare_goals_cohorts, monitor_goals_cohorts, summarize_goals_cohort,
                                                  verify_monitoring_document)

__all__ = [
    "summarize_goals_cohort", "compare_goals_cohorts", "monitor_goals_cohorts", "verify_monitoring_document",
    "MONITORING_ID", "MONITORING_VERSION", "CONTRACT_VERSION", "CONFIG_FIELDS", "GOVERNANCE_TAGS", "LIMITATIONS",
    "CALIBRATION_CLAIM", "INTERPRETATION", "COHORT_SUMMARY", "COHORT_COMPARISON", "MONITORING_SEQUENCE", "IDENTITY_FIELD",
    "VERIFIED", "REJECTED", "COMPARED", "NO_COMPARISON", "INSUFFICIENT_SUPPORT", "NOT_COMPARABLE",
    "NO_DATA", "BELOW_MIN_SUPPORT", "MEETS_MIN_SUPPORT", "RECONCILED", "NOT_RECONCILED",
    "EXPLORATORY_BOOTSTRAP", "INSUFFICIENT_WEEKS", "SHARED_WEEKS_NOT_INDEPENDENT", "UNDEFINED_IN_REPLICATE",
    "EVIDENCE_NOT_AUDITABLE", "CONSUMER_VERSION_MISMATCH", "TIMESTAMP_PRECISION_UNKNOWN", "TIMESTAMP_NOT_CANONICAL_UTC",
    "TEMPORAL_EVIDENCE_INCONSISTENT", "EVIDENCE_REJECTED", "DUPLICATE_REPORT", "OVERLAPPING_FIXTURES", "PRODUCER_MISMATCH",
    "PRODUCER_VERSION_MISMATCH", "MARKET_MISMATCH", "LINE_MISMATCH", "TARGET_DEFINITION_MISMATCH", "RELIABILITY_CONFIG_MISMATCH",
    "TEMPORAL_OVERLAP", "TEMPORAL_ORDER_INVALID", "EMPTY_COHORT", "FEWER_THAN_TWO_COHORTS", "NO_MEASURE_WITH_MIN_SUPPORT",
    "INCOMPLETE_LABELS", "MODEL_ERROR_NOT_RECONCILED", "RELIABILITY_SAMPLE_INSUFFICIENT", "SINGLE_OBSERVED_CLASS",
    "GoalsMonitoringError", "MonitoringInputError", "MonitoringConfigError", "MonitoringIntegrityError",
]
