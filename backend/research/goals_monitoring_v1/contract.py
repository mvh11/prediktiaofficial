"""Contrato V1 del monitor de investigación de goles FT90: constantes, errores, configuración y frontera de entrada (puro).

La frontera admite SOLO JSON nativo de tipos exactos (una subclase se rechaza, nunca se convierte), lo copia sin ejecutar
código del llamador y describe lo rechazado sin llamar a __repr__, __str__, __format__, __eq__ ni __hash__ de un objeto
que no sea de un tipo exacto admitido. La documentación completa de la interfaz está en research.goals_monitoring_v1.
"""

import math
import re
from datetime import datetime

from research.goals_consumer_v1.consumer import iso
from research.goals_consumer_v1.contract import FIXTURE_ID_LIMIT, describe, exact_keys

MONITORING_ID = "GOALS_RESEARCH_MONITORING"
MONITORING_VERSION = "1"
CONTRACT_VERSION = "GOALS_RESEARCH_MONITORING_V1"  # valor exacto de config["contract_version"]
CONFIG_FIELDS = ("contract_version", "min_support")
MIN_SUPPORT_LIMIT = 10 ** 6

MESSAGE_LIMIT = 400
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 5_000_000
MAX_TEXT_LENGTH = 1_000_000

GOVERNANCE_TAGS = ("RESEARCH_ONLY", "SYNTHETIC_CALLER_SUPPLIED", "NOT_CERTIFIED", "WAIT_REAL_SOURCE", "HOLD_MODEB_CODE")
EVIDENCE_STATUS = "UNVERIFIED_RESEARCH_ONLY"
SOURCE_STATUS = "WAIT_REAL_SOURCE"
CALIBRATION_CLAIM = "NONE_MONITORING_IS_DESCRIPTIVE_NOT_CALIBRATION_OR_DRIFT_PROOF"
INTERPRETATION = "DESCRIPTIVE_DIFFERENCE_ONLY_NOT_CAUSAL_NOT_PROOF_OF_SOURCE_OR_DATA_DRIFT"
LIMITATIONS = (
    "SYNTHETIC_CALLER_SUPPLIED_EVIDENCE_NOT_PIT_ADMISSIBLE_OR_DI_ADMITTED",
    "DIGESTS_PROVE_CONSISTENCY_NOT_AUTHENTICITY_OR_HISTORICAL_SOURCE_ADMISSION",
    "DESCRIPTIVE_DIFFERENCES_NOT_CAUSAL_AND_NOT_PROOF_OF_SOURCE_DRIFT",
    "WEEK_BLOCK_BOOTSTRAP_PERCENTILES_EXPLORATORY_NOT_CONFIDENCE_INTERVALS_OR_TESTS",
    "ROWS_WITHIN_A_WEEK_NOT_ASSUMED_INDEPENDENT",
    "NO_MULTIPLICITY_ADJUSTMENT_ACROSS_MEASURES_OR_COHORTS",
    "METRICS_NEVER_AVERAGED_OR_POOLED_ACROSS_REPORTS",
    "OUTCOME_DEPENDENT_MEASURES_REQUIRE_COMPLETE_LABELS",
    "ECE_DIFFERENCE_HAS_NO_PAIRED_UNCERTAINTY",
    "109A_BASE_RATE_COMPARATORS_NOT_MONITORED_IN_V1",
    "NO_DRIFT_ALARM_THRESHOLD_OR_CERTIFICATION",
    "NO_BETTING_VALUE_PICKS_OR_STAKE_ADVICE",
)

# estado de una cohorte de entrada
VERIFIED, REJECTED = "VERIFIED", "REJECTED"
# estado de una comparación, de una medida comparada o de una secuencia
COMPARED = "COMPARED"
NO_COMPARISON = "NO_COMPARISON"
INSUFFICIENT_SUPPORT = "INSUFFICIENT_SUPPORT"
NOT_COMPARABLE = "NOT_COMPARABLE"
# soporte de una medida en una cohorte (vocabulario de 109B)
NO_DATA, BELOW_MIN_SUPPORT, MEETS_MIN_SUPPORT = "NO_DATA", "BELOW_MIN_SUPPORT", "MEETS_MIN_SUPPORT"
# población de una medida
HOLDOUT_INPUT, SETTLED, SCORED_SETTLED = "HOLDOUT_INPUT", "SETTLED", "SCORED_SETTLED"
RATE, MEAN = "RATE", "MEAN"
COMPLETE, LABELS_INCOMPLETE, NOT_APPLICABLE = "COMPLETE", "LABELS_INCOMPLETE", "NOT_APPLICABLE"
RECONCILED, NOT_RECONCILED = "RECONCILED_WITH_109A_PER_FOLD", "NOT_RECONCILED"
# incertidumbre de una diferencia
EXPLORATORY_BOOTSTRAP = "EXPLORATORY_BOOTSTRAP"
INSUFFICIENT_WEEKS = "INSUFFICIENT_WEEKS"
SHARED_WEEKS_NOT_INDEPENDENT = "SHARED_WEEKS_NOT_INDEPENDENT"
UNDEFINED_IN_REPLICATE = "UNDEFINED_IN_REPLICATE"
NOT_COMPUTED = "NOT_COMPUTED"

# motivos de rechazo de una cohorte (además de los del consumidor, que se conservan tal cual)
EVIDENCE_NOT_AUDITABLE = "EVIDENCE_NOT_AUDITABLE"
CONSUMER_VERSION_MISMATCH = "CONSUMER_VERSION_MISMATCH"
TIMESTAMP_PRECISION_UNKNOWN = "TIMESTAMP_PRECISION_UNKNOWN"
TIMESTAMP_NOT_CANONICAL_UTC = "TIMESTAMP_NOT_CANONICAL_UTC"
TEMPORAL_EVIDENCE_INCONSISTENT = "TEMPORAL_EVIDENCE_INCONSISTENT"
# motivos de NOT_COMPARABLE / NO_COMPARISON
EVIDENCE_REJECTED = "EVIDENCE_REJECTED"
DUPLICATE_REPORT = "DUPLICATE_REPORT"
OVERLAPPING_FIXTURES = "OVERLAPPING_FIXTURES"
PRODUCER_MISMATCH = "PRODUCER_MISMATCH"
PRODUCER_VERSION_MISMATCH = "PRODUCER_VERSION_MISMATCH"
MARKET_MISMATCH = "MARKET_MISMATCH"
LINE_MISMATCH = "LINE_MISMATCH"
TARGET_DEFINITION_MISMATCH = "TARGET_DEFINITION_MISMATCH"
RELIABILITY_CONFIG_MISMATCH = "RELIABILITY_CONFIG_MISMATCH"
TEMPORAL_OVERLAP = "TEMPORAL_OVERLAP"
TEMPORAL_ORDER_INVALID = "TEMPORAL_ORDER_INVALID"
EMPTY_COHORT = "EMPTY_COHORT"
FEWER_THAN_TWO_COHORTS = "FEWER_THAN_TWO_VERIFIED_COHORTS"
NO_MEASURE_WITH_MIN_SUPPORT = "NO_MEASURE_WITH_MIN_SUPPORT"
INCOMPLETE_LABELS = "INCOMPLETE_LABELS"
MODEL_ERROR_NOT_RECONCILED = "MODEL_ERROR_NOT_RECONCILED"
RELIABILITY_SAMPLE_INSUFFICIENT = "RELIABILITY_SAMPLE_BELOW_109B_MIN_TOTAL_SUPPORT"
SINGLE_OBSERVED_CLASS = "SINGLE_OBSERVED_CLASS"
ECE_REPLICATES_UNPAIRED = "ECE_REPLICATES_ARE_PER_COHORT_AND_UNPAIRED"


# ------------------------------------------------------------------------------------------------------------ errores

class GoalsMonitoringError(ValueError):
    """Base del espacio de errores versionado del monitor V1. El mensaje es determinista y acotado."""

    def __init__(self, message):
        text = message if type(message) is str else "<mensaje no textual>"
        if str.__len__(text) > MESSAGE_LIMIT:
            text = str.__getitem__(text, slice(0, MESSAGE_LIMIT)) + "..."
        super().__init__(text)


class MonitoringInputError(GoalsMonitoringError):
    """La entrada no es JSON nativo exacto y acotado, o no tiene la forma de la llamada (error del llamador)."""


class MonitoringConfigError(MonitoringInputError):
    """La configuración del monitor no tiene la forma pedida."""


class MonitoringIntegrityError(GoalsMonitoringError):
    """Un informe ya auditado no concilia con el recuento propio del monitor: no hay resultado (fail-closed)."""


# ------------------------------------------------------------------------------------------------------ configuración

def validate_config(config) -> dict:
    """{"contract_version": "GOALS_RESEARCH_MONITORING_V1", "min_support": int exacto en [1, 10**6]}."""
    exact_keys(config, CONFIG_FIELDS, "config", MonitoringConfigError)
    version = config["contract_version"]
    if type(version) is not str or version != CONTRACT_VERSION:
        raise MonitoringConfigError(f"config.contract_version: se espera {CONTRACT_VERSION!r}: {describe(version)}")
    support = config["min_support"]
    if type(support) is not int or not 1 <= support <= MIN_SUPPORT_LIMIT:
        raise MonitoringConfigError(f"config.min_support: se espera un int exacto en [1, {MIN_SUPPORT_LIMIT}] (bool no): "
                                    f"{describe(support)}")
    return {"contract_version": CONTRACT_VERSION, "min_support": support}


def sequence(value, what: str) -> list | tuple:
    if type(value) is not list and type(value) is not tuple:
        raise MonitoringInputError(f"{what}: se espera una list o tuple exacta, no {describe(value)}")
    return value


# ----------------------------------------------------------------------------------------------------- JSON nativo

def plain_json_copy(value, what: str):
    """Copia nueva de un JSON nativo EXACTO: dict (claves str exactas), list o tuple (-> list), str, int (|n| < 10**640),
    float finito, bool y None. Profundidad, nodos y longitud de texto acotados; nunca ejecuta código del llamador."""
    root = [None]
    stack, nodes = [(value, 0, root, 0)], 0
    while stack:
        item, depth, parent, slot = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH:
            raise MonitoringInputError(f"{what}: estructura demasiado profunda (más de {MAX_JSON_DEPTH} niveles)")
        if nodes > MAX_JSON_NODES:
            raise MonitoringInputError(f"{what}: estructura demasiado grande (más de {MAX_JSON_NODES} nodos)")
        kind = type(item)
        if item is None or kind is bool:
            out = item
        elif kind is str:
            if len(item) > MAX_TEXT_LENGTH:
                raise MonitoringInputError(f"{what}: texto de más de {MAX_TEXT_LENGTH} caracteres: {describe(item)}")
            out = item
        elif kind is int:
            if not -FIXTURE_ID_LIMIT < item < FIXTURE_ID_LIMIT:
                raise MonitoringInputError(f"{what}: int fuera de (-10**640, 10**640): {describe(item)}")
            out = item
        elif kind is float:
            if not math.isfinite(item):
                raise MonitoringInputError(f"{what}: float no finito: {describe(item)}")
            out = item
        elif kind is list or kind is tuple:
            out = [None] * len(item)
            stack.extend((x, depth + 1, out, i) for i, x in enumerate(item))
        elif kind is dict:
            out = {}
            for k, x in item.items():
                if type(k) is not str:
                    raise MonitoringInputError(f"{what}: clave que no es un str exacto: {describe(k)}")
                out[k] = None
                stack.append((x, depth + 1, out, k))
        else:
            raise MonitoringInputError(f"{what}: valor que no es JSON nativo exacto: {describe(item)}")
        parent[slot] = out
    return root[0]


# --------------------------------------------------------------------------------------------------------- instantes

_ISO_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?(?:[+-][0-9]{2}:[0-9]{2})")


def utc_instant(value) -> tuple[datetime | None, str | None]:
    """(instante, None) para un texto UTC canónico del consumidor (segundos o microsegundos, "+00:00"), o (None, motivo).
    Sin segundos, con otra fracción, sin zona o con "Z" la precisión no es conocida; con otro desfase no es UTC canónico."""
    if type(value) is not str:
        return None, EVIDENCE_NOT_AUDITABLE
    if _ISO_SHAPE.fullmatch(value) is None:
        return None, TIMESTAMP_PRECISION_UNKNOWN
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None, TIMESTAMP_PRECISION_UNKNOWN
    if instant.utcoffset().total_seconds() != 0 or iso(instant) != value:
        return None, TIMESTAMP_NOT_CANONICAL_UTC
    return instant, None
