"""Consumidor de investigación V1 de probabilidades de goles FT90: integra la evaluación OOS walk-forward (MOD-DEV-109A,
research.goals_oos_evaluation) y la fiabilidad / incertidumbre por bloques de semana (MOD-DEV-109B,
research.goals_calibration_uncertainty) sobre la MISMA población de holdout, con denominadores conciliados contra las dos
fuentes. Puro y determinista: sin BD, red, disco, proveedores, variables de entorno ni reloj. Interno, RESEARCH_ONLY,
SYNTHETIC_CALLER_SUPPLIED, NOT_CERTIFIED, WAIT_REAL_SOURCE. Las dos fuentes se consumen tal cual, sin modificarlas.

Interfaz (versión CONSUMER_VERSION = "1", contrato CONTRACT_VERSION = "GOALS_RESEARCH_CONSUMER_V1"):
  run_goals_research_consumer(observations, config) -> dict      informe integrado con report_digest
  aggregate_goals_consumer_reports(reports) -> dict              agregado de informes disjuntos con aggregate_digest
Errores: GoalsConsumerError (ValueError) y sus subclases (ver «Errores»).

Configuración (dict EXACTO con exactamente estas claves):
  contract_version     "GOALS_RESEARCH_CONSUMER_V1" (otro valor: ConsumerConfigError; no hay negociación implícita)
  producer_id          str exacto [A-Za-z0-9_.-]{1,128}: identidad declarada del productor de probabilidades
  producer_version     ídem: versión declarada. A 109A llega model_id = "<producer_id>@<producer_version>" ("@" no cabe en
                       ninguno de los dos, así que el model_id es inequívoco y se comprueba en el informe de 109A)
  market, line         objetivo único de la llamada: BTTS_FT90 con line None, u OVER_UNDER_FT90 con el texto canónico de una
                       línea aprobada ("0.5" ... "6.5"). 1X2_FT90, líneas de cuarto ("2.25"), texto no canónico ("2.50"),
                       float o Decimal: ConsumerTargetError
  origins              list o tuple exacta no vacía de instantes (ver «Tipos»): pliegues de 109A
  holdout_length, calibration_length, embargo    timedelta exactos; su semántica y validación son las de 109A
  reliability          research.goals_calibration_uncertainty.UncertaintyConfig exacto; lo valida 109B. seed admite
                       cualquier int que admita 109B (también 10**5000): el tope de fixture_id NO se aplica a la semilla

Observación (dict EXACTO con exactamente las claves de 109A): fixture_id, kickoff_at, cutoff_at, feature_observed_at,
market, line, status (ESTIMATE | NO_ESTIMATE), probabilities, outcome {status_short, fulltime_home, fulltime_away,
observed_at}. Además de lo que exige 109A, en la frontera del consumidor:
  fixture_id    int EXACTO en [1, 10**640) (bool no). Tope SOLO del consumidor: con <= 640 cifras su decimal es siempre
                serializable (sys.int_info.str_digits_check_threshold), y 109A lo serializa en decimal. 109B por sí solo
                sigue admitiendo cualquier int (INT_HEX_V1); el consumidor no cambia esa regla.
  market, line  iguales al objetivo de la configuración (si no, error: nunca se descarta una fila en silencio)
  probabilities ESTIMATE: dict exacto con exactamente las clases del objetivo (YES/NO; OVER/UNDER en línea .5;
                OVER/UNDER/PUSH en línea entera), valores int o float EXACTOS (bool, subclases de float, NaN, inf, fuera de
                [0, 1]: ConsumerProbabilityError) y suma 1 con las reglas publicadas (validate_binary_probabilities /
                validate_integer_probabilities). Sin recortar, renormalizar ni imputar. NO_ESTIMATE: None.
  fulltime_*    int exacto con |n| < 10**6, o None (un negativo es fulltime_invalid en la liquidación publicada)
  fixture_id    único en la llamada (ConsumerDuplicateFixtureError): con un objetivo único es la identidad completa.
Tipos: todo instante es un datetime EXACTO con tzinfo datetime.timezone EXACTO (offset fijo; ni tzinfo del llamador ni
ZoneInfo); textos str exactos; colecciones list o tuple exactas. Nada se convierte implícitamente. El orden temporal
(cutoff_at < kickoff_at, feature_observed_at <= cutoff_at, resultado final observado después del kickoff) y la forma de
los pliegues los exige 109A: su rechazo llega como ConsumerSourceError con el error original.

Población: el libro mayor (ledger) tiene una fila por observación cuyo kickoff_at cae en el holdout [O_k, O_k + holdout)
de un pliegue k (los holdout de 109A no se solapan). Cada fila: sample_id "fixture:<fixture_id>", fold_index, kickoff_at
(ISO UTC), week = semana ISO de kickoff_at en UTC (bloque del bootstrap de 109B), estimate_status, probabilities (float),
label y label_missing_reason. La etiqueta es la liquidación de 109A con las funciones publicadas de research.m58.goal_labels
(marcador a 90'; sin outcome.observed_at no hay etiqueta auditable: outcome_observed_at_missing).
PUSH: en una línea entera, un total igual a la línea es PUSH, un resultado LIQUIDADO de tres clases (cuenta en settled y,
si es ESTIMATE, en scored). Un partido no terminado (CANC, ABD, PST... "void") NO es PUSH: es status_not_finished, sin
etiqueta (label_missing). En BTTS y líneas .5 no existe PUSH.

Denominadores (por pliegue y agregados; contados fila a fila en el libro mayor, nunca deducidos de agregados):
  input_total = estimate_input + no_estimate_input = settled_total + label_missing_input
  label_missing_input = label_missing_estimate + label_missing_no_estimate = suma de label_missing_by_reason
  settled_total = scored_settled + abstained_settled        (abstained_settled = NO_ESTIMATE con etiqueta)
  estimate_input = scored_settled + label_missing_estimate; no_estimate_input = abstained_settled + label_missing_no_estimate
  settled_by_class[c] = scored_by_class[c] + abstained_by_class[c], y cada familia suma su total
Conciliación con las fuentes (cualquier diferencia es ConsumerIntegrityError, sin resultado):
  109A grupo del objetivo: holdout_rows, holdout_estimate, holdout_no_estimate, holdout_settled, holdout_excluded_by_reason,
       model.scored, coverage.{settled, model_scored, abstained_settled}; outcome_counts = settled_by_class (109A cuenta
       TODAS las liquidadas, NO_ESTIMATE incluidas)
  109B: coverage.total_input, coverage.no_estimate (todas las NO_ESTIMATE), coverage.label_missing = label_missing_estimate
       (solo ESTIMATE sin etiqueta), coverage.scored; outcome_counts = scored_by_class (109B cuenta SOLO las puntuadas)
Los dos outcome_counts NO son equivalentes y nunca se usan uno por otro (compatibility.count_semantics lo declara).

Resultado (por pliegue, agregado del informe y agregado entre informes): SCORED | NO_ESTIMATE (NO_HOLDOUT_ROWS,
NO_SETTLED_OUTCOMES o ALL_ABSTAINED) | NON_COMPARABLE (solo entre informes). Las métricas de 109A (Brier, log loss, tasa
base) se informan por pliegue tal cual; nunca se promedian ni se agregan entre pliegues o informes. Lo agregado son cuentas
de poblaciones disjuntas verificadas y la fiabilidad de 109B re-ejecutada sobre la unión de filas (nunca una media de ECE).
ECE y fiabilidad son descriptivos: calibration_claim lo dice y ningún valor establece que el modelo esté calibrado.

Procedencia e identidad: producer {producer_id, producer_version, model_id}, target {market, line, line_type, classes,
settlement_convention FT90_V1, week_derivation, sample_id_prefix}, consumer_id / consumer_version / contract_version.
oos_report es el informe de 109A tal cual (report_digest, config_digest y dataset_fingerprint sin tocar, recalculados para
verificarlos); reliability_reports.{folds, pooled} son el canonical_report_json de 109B tal cual (report_identity,
config_identity, dataset_identity y la codificación INT_HEX_V1 sin tocar, report_identity recalculado para verificarlo).
Nada se reescribe, normaliza ni renombra. report_digest = sha256 del JSON canónico del consumidor (claves ordenadas,
ASCII, sin NaN) del informe sin ese campo. Las identidades son de integridad, no de autenticidad.

Agregación entre informes (aggregate_goals_consumer_reports): cada informe se audita entero (JSON nativo exacto, versión,
report_digest, identidades de las fuentes, libro mayor revalidado, 109B re-ejecutado con la misma identidad y el cuerpo
reconstruido idéntico); luego mismo productor y versión, mercado, línea, versiones de fuente y configuración de 109B, y
fixture_id disjuntos. Si algo falta o no cuadra: NON_COMPARABLE con motivos explícitos (EVIDENCE_NOT_AUDITABLE,
EVIDENCE_DIGEST_MISMATCH, SOURCE_IDENTITY_MISMATCH, CONSUMER_VERSION_MISMATCH, SOURCE_VERSION_MISMATCH, PRODUCER_MISMATCH,
PRODUCER_VERSION_MISMATCH, MARKET_MISMATCH, LINE_MISMATCH, RELIABILITY_CONFIG_MISMATCH, DUPLICATE_REPORT,
OVERLAPPING_FIXTURES) y sin denominadores ni fiabilidad. Una estructura que no es JSON nativo exacto es ConsumerInputError.
Límite: 109A no puede re-ejecutarse desde un informe (el libro mayor no lleva las filas de train ni los marcadores); su
informe se verifica por su digest y por la conciliación de cuentas con el libro mayor.

Errores (GoalsConsumerError, mensajes deterministas de como mucho ~400 caracteres que nunca ejecutan __repr__, __str__,
__format__, __eq__ ni __hash__ de un objeto del llamador que no sea de un tipo exacto admitido):
  ConsumerInputError > ConsumerConfigError, ConsumerTargetError, ConsumerObservationError > ConsumerTemporalError,
      ConsumerProbabilityError, ConsumerIdentityError > ConsumerDuplicateFixtureError
  ConsumerSourceError    109A o 109B rechazaron la entrada: source_id, source_version, source_error_type y source_error
                         (la excepción original, también en __cause__)
  ConsumerIntegrityError una conciliación o identidad de fuente no cuadra (fail-closed)
Determinismo: el informe y el agregado solo dependen del multiconjunto de observaciones / informes y de la configuración;
cualquier permutación válida de la entrada da el mismo resultado y el mismo digest.
"""

from research.goals_consumer_v1.aggregate import (AGGREGATE_ID, CONSUMER_VERSION_MISMATCH, DUPLICATE_REPORT, EVIDENCE_DIGEST_MISMATCH,
                                                  EVIDENCE_NOT_AUDITABLE, LINE_MISMATCH, MARKET_MISMATCH, OVERLAPPING_FIXTURES,
                                                  PRODUCER_MISMATCH, PRODUCER_VERSION_MISMATCH, REJECTED, RELIABILITY_CONFIG_MISMATCH,
                                                  SOURCE_IDENTITY_MISMATCH, SOURCE_VERSION_MISMATCH, VERIFIED,
                                                  aggregate_goals_consumer_reports)
from research.goals_consumer_v1.consumer import (ALL_ABSTAINED, CALIBRATION_CLAIM, CLASSIFICATIONS, COUNT_SEMANTICS,
                                                 LABEL_MISSING_REASONS, LIMITATIONS, NO_HOLDOUT_ROWS, NO_SETTLED_OUTCOMES,
                                                 RESULT_NO_ESTIMATE, RESULT_NON_COMPARABLE, RESULT_SCORED, SAMPLE_ID_PREFIX,
                                                 VERIFIED_DISJOINT, WEEK_DERIVATION, run_goals_research_consumer)
from research.goals_consumer_v1.contract import (CONSUMER_ID, CONSUMER_VERSION, CONTRACT_VERSION, FIXTURE_ID_DECIMAL_DIGITS,
                                                 FIXTURE_ID_LIMIT, ConsumerConfigError, ConsumerDuplicateFixtureError,
                                                 ConsumerIdentityError, ConsumerInputError, ConsumerIntegrityError,
                                                 ConsumerObservationError, ConsumerProbabilityError, ConsumerSourceError,
                                                 ConsumerTargetError, ConsumerTemporalError, GoalsConsumerError)

__all__ = [
    "run_goals_research_consumer", "aggregate_goals_consumer_reports",
    "CONSUMER_ID", "CONSUMER_VERSION", "CONTRACT_VERSION", "AGGREGATE_ID", "FIXTURE_ID_DECIMAL_DIGITS", "FIXTURE_ID_LIMIT",
    "SAMPLE_ID_PREFIX", "WEEK_DERIVATION", "LABEL_MISSING_REASONS", "COUNT_SEMANTICS", "CLASSIFICATIONS", "LIMITATIONS",
    "CALIBRATION_CLAIM", "RESULT_SCORED", "RESULT_NO_ESTIMATE", "RESULT_NON_COMPARABLE", "NO_HOLDOUT_ROWS", "NO_SETTLED_OUTCOMES",
    "ALL_ABSTAINED", "VERIFIED_DISJOINT", "VERIFIED", "REJECTED",
    "EVIDENCE_NOT_AUDITABLE", "EVIDENCE_DIGEST_MISMATCH", "SOURCE_IDENTITY_MISMATCH", "CONSUMER_VERSION_MISMATCH",
    "SOURCE_VERSION_MISMATCH", "PRODUCER_MISMATCH", "PRODUCER_VERSION_MISMATCH", "MARKET_MISMATCH", "LINE_MISMATCH",
    "RELIABILITY_CONFIG_MISMATCH", "DUPLICATE_REPORT", "OVERLAPPING_FIXTURES",
    "GoalsConsumerError", "ConsumerInputError", "ConsumerConfigError", "ConsumerTargetError", "ConsumerObservationError",
    "ConsumerTemporalError", "ConsumerProbabilityError", "ConsumerIdentityError", "ConsumerDuplicateFixtureError",
    "ConsumerSourceError", "ConsumerIntegrityError",
]
