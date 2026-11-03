"""Fiabilidad (ECE por bins) e incertidumbre por bloques de semana de probabilidades de goles FT90 (MOD-DEV-109B).
Puro: sin BD, red, disco ni app. Diagnóstico exploratorio: no ajusta, recalibra, certifica ni recomienda nada.

Interfaz:
  goals_reliability_uncertainty(rows, *, market, line, config) -> ReliabilityUncertaintyReport
  canonical_report_json(report) -> str          (serialización determinista; report_identity es su sha256 sin ese campo)
  block_draw(seed, replicate, draw, weeks) -> int  (el sorteo de semanas del bootstrap, ver abajo)

Objetivo (una llamada = un mercado y una línea; vocabulario de research.quote_temporal_eligibility y research.m58.goal_labels):
  BTTS_FT90        line None; clases YES, NO; binario.
  OVER_UNDER_FT90  line con el texto canónico de una de las 13 líneas aprobadas ("0.5" ... "6.5"; "2.50", 2.5 o Decimal no).
                   Línea .5: clases OVER, UNDER; binario. Línea entera (n.0): clases OVER, UNDER y PUSH; tres clases,
                   la masa PUSH se conserva y nunca se convierte en binario. Líneas de cuarto, 1X2 u otro mercado:
                   UnsupportedTargetError. Liquidación FT90_V1 (marcador a 90'); aquí no se liquida nada: la etiqueta la
                   aporta el llamador ya adjudicada.

Fila (Mapping con exactamente estas claves; las filas van en una list o tuple y no se modifican):
  sample_id        str no vacío y sin espacios en los extremos: identidad de la observación
  fixture_id       int >= 1 (bool no)
  week             semana ISO "YYYY-Www" (p. ej. "2026-W09"), válida en el calendario ISO; el bloque del bootstrap
  market, line     iguales al objetivo (si no, RowError: nunca se descarta una fila en silencio)
  estimate_status  ESTIMATE | NO_ESTIMATE
  probabilities    ESTIMATE: Mapping con exactamente las clases del objetivo. Binario: la regla de
                   research.goals_markets.evaluation.validate_binary_probabilities (int o float finitos en [0, 1], bool
                   no, |suma - 1| <= 1e-9); tres clases: la de research.goals_markets.integer_evaluation.
                   validate_integer_probabilities (misma regla con math.fsum). Sin recorte, renormalización ni imputación.
                   NO_ESTIMATE: None (una abstención con probabilidades es contradictoria: ProbabilityError).
  label            clase FT90 adjudicada por el llamador, o None si falta. Nunca se infiere. PUSH en un objetivo binario
                   es InconsistentPushLabelError; otra clase ajena al objetivo, LabelError.

Identidad: sample_id único y un solo sample_id por fixture_id en la llamada (DuplicateSampleError, también si las dos
filas son idénticas: no se colapsan). Cualquier fila no válida lanza una subclase de InputError y no hay resultado parcial.

Cobertura: total_input = no_estimate + label_missing + scored. NO_ESTIMATE es una abstención explícita (aunque traiga
etiqueta); ESTIMATE sin etiqueta es LABEL_MISSING; solo las filas ESTIMATE con etiqueta se puntúan. abstention_rate =
no_estimate / total_input y scored_coverage = scored / total_input (None sin filas).

Bins (BINNING_VERSION): config.bin_count bins de igual anchura con bordes los double k / bin_count, comparación exacta
entre doubles: bin k = [k/B, (k+1)/B) y el último [(B-1)/B, 1.0], cerrado. p = 0 cae en el bin 0 y p = 1 en el último.

ECE (sumas exactas con Fraction; el resultado es el double más cercano, sin depender del orden):
  Por clase c (uno contra el resto): ECE_c = sum_b (n_b / N) * |media_b(p_c) - frec_b(y == c)| = sum_b |S_p,b - S_y,b| / N,
  con N las filas puntuadas, n_b las del bin, S_p,b la suma de p_c y S_y,b los aciertos de c en el bin. Bins vacíos no
  aportan. Binario (ECE_METHOD_BINARY): solo la clase del evento (YES en BTTS, OVER en Over/Under .5); su complemento es
  la misma información y no se promedia. Tres clases (ECE_METHOD_THREE_CLASS): ECE de cada una de OVER, UNDER y PUSH con
  sus propios bins, y ece = media aritmética sin pesos de las tres (ECE por clases). Un ECE binario y uno de tres clases
  no son comparables entre sí. No existe umbral de aprobado: ningún valor certifica calibración.

Incertidumbre (BOOTSTRAP_METHOD): bootstrap por bloques de semana. Se remuestrean semanas enteras, nunca filas: con W
semanas puntuadas distintas (orden ascendente), cada réplica r = 0..iterations-1 toma W semanas con reemplazo, la j-ésima
block_draw(seed, r, j, W) = sha256("<BOOTSTRAP_METHOD>|seed|r|j") como entero big-endian módulo W (sin random ni estado
global; el sesgo del módulo es < W / 2**256). Cada réplica recalcula el ece con las filas de sus semanas (repetidas según
la multiplicidad). Se informan las réplicas ordenadas, su mínimo y máximo y los percentiles P10, P50 y P90 por rango más
cercano (el valor ordenado en la posición ceil(q * iterations)). Son percentiles exploratorios, NO un intervalo de
confianza (no se emite ningún IC del 95 %) ni una certificación; no se supone independencia dentro de una semana.
Estado: NO_SCORED_SAMPLES | INSUFFICIENT_WEEKS (menos de config.min_weeks semanas puntuadas, p. ej. una sola) |
SINGLE_OBSERVED_CLASS (todas las etiquetas puntuadas iguales, todo PUSH incluido) | EXPLORATORY_BOOTSTRAP. Solo el
último ejecuta réplicas.

Soporte (reglas de presentación, no contrastes): bin EMPTY | BELOW_MIN_SUPPORT | MEETS_MIN_SUPPORT (config.min_bin_support);
total NO_DATA | INSUFFICIENT_SUPPORT | MEETS_MIN_SUPPORT (config.min_total_support).

Reproducibilidad: las filas se ordenan por sample_id; config_identity = sha256 de la configuración y los métodos;
dataset_identity = sha256 del objetivo y todas las filas normalizadas (también las abstenciones y sin etiqueta);
report_identity = sha256 de canonical_report_json sin ese campo. Cambiar una fila relevante o la configuración cambia el
informe y su identidad. Nada de esto prueba admisión por Data Integrity, cronología histórica auténtica, emisor ni recibo
original: semana y etiqueta las declara el llamador. PIT_STATUS = NOT_CERTIFIED.

Enteros grandes (MOD-DEV109B-R1): fixture_id y seed admiten cualquier int válido, también 10**5000. La serialización nunca
pasa por la conversión int -> decimal de CPython (límite sys.get_int_max_str_digits, configurable y como mínimo 640
dígitos) para valores grandes, y no se toca ese límite global. Un int con |n| < 10**DECIMAL_INT_DIGITS (640 dígitos o
menos) se escribe como siempre (número JSON decimal y su texto decimal en block_draw), así que las identidades de entradas
ordinarias no cambian. Uno mayor se escribe con la codificación INT_ENCODING: en el JSON canónico, el objeto
{"INT_HEX_V1": "<signo><hex>"} (signo "+" o "-", dígitos hexadecimales en minúscula sin 0x ni ceros a la izquierda); en
block_draw, el texto "INT_HEX_V1:<signo><hex>". Ninguna de las dos formas puede coincidir con la decimal (un objeto no es un
número y ":" no es un dígito), y la hexadecimal es biyectiva: enteros grandes distintos dan identidades distintas.

Errores (MOD-DEV109B-R1): toda entrada rechazada lanza una subclase de InputError con un mensaje determinista y acotado
(cientos de caracteres) que nunca llama a __repr__, __str__, __format__, __eq__ ni __hash__ de un objeto del llamador
que no sea de un tipo exacto admitido: los textos se recortan a DESCRIBE_TEXT_LIMIT caracteres, un int grande se describe
por su número de bits y cualquier otro objeto por el nombre (recortado) de su tipo. Por eso los textos, etiquetas,
mercados, líneas, semanas e identificadores tienen que ser exactamente str (una subclase de str se rechaza) y las
probabilidades exactamente int o float (bool no; una subclase, p. ej. de float, se rechaza). Límite conocido: un Mapping o
una colección del llamador se recorren con su propio __iter__ / __getitem__ / __len__, y isinstance puede consultar su
__class__; ese código del llamador se ejecuta (no se protege contra un Mapping hostil funcional).
"""

import hashlib
import json
import math
from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from fractions import Fraction
from types import MappingProxyType
from typing import NamedTuple

from research.goals_markets.evaluation import HALF_LINE, INTEGER_LINE, ProbabilityEvidenceError, validate_binary_probabilities
from research.goals_markets.integer_evaluation import THREE_CLASS, validate_integer_probabilities
from research.m58.goal_labels import BTTS_NO, BTTS_YES, OVER, PUSH, SUPPORTED_LINE_TEXTS, UNDER
from research.quote_temporal_eligibility import BTTS as BTTS_FT90
from research.quote_temporal_eligibility import OVER_UNDER as OVER_UNDER_FT90
from research.quote_temporal_eligibility import SETTLEMENT_CONVENTION_FT90

DIAGNOSTIC_ID = "GOALS_RELIABILITY_UNCERTAINTY"
DIAGNOSTIC_VERSION = "1"
BINNING_VERSION = "EQUAL_WIDTH_LEFT_CLOSED_LAST_BIN_CLOSED_V1"
BINARY_EVENT = "BINARY_EVENT"
ECE_METHOD_BINARY = "BINARY_EVENT_ECE_EQUAL_WIDTH_COUNT_WEIGHTED_ABS_GAP_V1"
ECE_METHOD_THREE_CLASS = "CLASSWISE_ONE_VS_REST_ECE_EQUAL_WIDTH_COUNT_WEIGHTED_ABS_GAP_UNWEIGHTED_MEAN_OF_3_V1"
BOOTSTRAP_METHOD = "WEEK_BLOCK_BOOTSTRAP_SHA256_COUNTER_DRAWS_V1"
QUANTILE_METHOD = "NEAREST_RANK_CEIL_Q_TIMES_ITERATIONS"
QUANTILES = (("P10", Fraction(1, 10)), ("P50", Fraction(1, 2)), ("P90", Fraction(9, 10)))
INTERVAL_SEMANTICS = "EXPLORATORY_PERCENTILES_OF_WEEK_BLOCK_BOOTSTRAP_ECE_NOT_A_CONFIDENCE_INTERVAL"

ESTIMATE, NO_ESTIMATE = "ESTIMATE", "NO_ESTIMATE"
LABEL_MISSING = "LABEL_MISSING"
EMPTY, BELOW_MIN_SUPPORT, MEETS_MIN_SUPPORT = "EMPTY", "BELOW_MIN_SUPPORT", "MEETS_MIN_SUPPORT"
NO_DATA, INSUFFICIENT_SUPPORT = "NO_DATA", "INSUFFICIENT_SUPPORT"
NO_SCORED_SAMPLES, SINGLE_OBSERVED_CLASS, MULTIPLE_OBSERVED_CLASSES = "NO_SCORED_SAMPLES", "SINGLE_OBSERVED_CLASS", "MULTIPLE_OBSERVED_CLASSES"
INSUFFICIENT_WEEKS, EXPLORATORY_BOOTSTRAP = "INSUFFICIENT_WEEKS", "EXPLORATORY_BOOTSTRAP"

MAX_BIN_COUNT = 100
MAX_ITERATIONS = 100_000
MIN_WEEKS_FLOOR = 2  # con una semana no hay nada que remuestrear

EVIDENCE_BASIS = "SYNTHETIC_CALLER_SUPPLIED_PROBABILITIES_WEEKS_AND_FT90_LABELS"
PIT_STATUS = "NOT_CERTIFIED"
CALIBRATION_STATUS = "NOT_CERTIFIED_NO_PASS_THRESHOLD"
RECEIPT_STATUS = "NO_PROVIDER_ISSUER_OR_ORIGINAL_RECEIPT_CLAIM"
LIMITATIONS = (
    "SYNTHETIC_CALLER_SUPPLIED_PROBABILITIES_AND_LABELS",
    "WEEK_AND_FT90_LABEL_NOT_PROOF_OF_DI_ADMISSION_OR_AUTHENTIC_CHRONOLOGY",
    "NOT_TRUE_PIT_CERTIFIED",
    "NO_CALIBRATION_PASS_THRESHOLD_OR_CERTIFICATION",
    "ECE_DEPENDS_ON_BINNING_CHOICE",
    "ROWS_WITHIN_A_WEEK_NOT_ASSUMED_INDEPENDENT",
    "BOOTSTRAP_PERCENTILES_EXPLORATORY_NOT_CONFIDENCE_INTERVALS",
    "SUPPORT_THRESHOLDS_ARE_REPORTING_RULES_NOT_STATISTICAL_TESTS",
    "NO_BASELINE_COMPARISON",
    "QUARTER_LINES_AND_1X2_UNSUPPORTED",
)

ROW_FIELDS = frozenset({"sample_id", "fixture_id", "week", "market", "line", "estimate_status", "probabilities", "label"})
_KNOWN_LABELS = frozenset({BTTS_YES, BTTS_NO, OVER, UNDER, PUSH})

INT_ENCODING = "INT_HEX_V1"
DECIMAL_INT_DIGITS = 640  # sys.int_info.str_digits_check_threshold: el límite configurable nunca es menor
_DECIMAL_INT_BOUND = 10 ** DECIMAL_INT_DIGITS
DESCRIBE_TEXT_LIMIT = 40
_DESCRIBE_INT_BOUND = 10 ** 18
_DESCRIBE_ITEMS = 3
_TYPE_NAME = type.__dict__["__name__"]  # el descriptor de type: ignora un __name__ redefinido en la metaclase


def _int_hex(value: int) -> str:
    """Signo y hexadecimal de un int exacto (sin conversión decimal, sin límite de dígitos)."""
    return ("-" if value < 0 else "+") + format(-value if value < 0 else value, "x")


def _int_token(value: int) -> str:
    """El texto de un int exacto en block_draw: decimal hasta 640 dígitos, INT_HEX_V1 por encima."""
    return str(value) if -_DECIMAL_INT_BOUND < value < _DECIMAL_INT_BOUND else f"{INT_ENCODING}:{_int_hex(value)}"


def _short_text(value: str) -> str:
    size = str.__len__(value)
    head = str.__getitem__(value, slice(0, DESCRIBE_TEXT_LIMIT))  # métodos de str sin pasar por una subclase
    return head if size <= DESCRIBE_TEXT_LIMIT else f"{head}... ({size} caracteres)"


def _type_name(value) -> str:
    return _short_text(_TYPE_NAME.__get__(type(value)))


def _describe(value) -> str:
    """Descripción acotada y determinista para un mensaje de error; no ejecuta código del llamador."""
    kind = type(value)
    if value is None or kind is bool:
        return "None" if value is None else ("True" if value else "False")
    if kind is int:
        if -_DESCRIBE_INT_BOUND < value < _DESCRIBE_INT_BOUND:
            return str(value)
        return f"<int {'negativo ' if value < 0 else ''}de {value.bit_length()} bits>"
    if kind is float:
        return repr(value)
    if kind is str:
        size = len(value)
        return repr(value) if size <= DESCRIBE_TEXT_LIMIT else f"{value[:DESCRIBE_TEXT_LIMIT]!r}... ({size} caracteres)"
    if kind is list or kind is tuple or kind is dict or kind is set or kind is frozenset:
        return f"<{kind.__name__} de {len(value)} elementos>"
    return f"<{_type_name(value)}>"


def _describe_keys(keys: list) -> str:
    """Claves de un Mapping (ya enumeradas): los str exactos ordenados y recortados; las demás, por su tipo."""
    texts = sorted(k for k in keys if type(k) is str)
    others = sum(1 for k in keys if type(k) is not str)
    parts = [_describe(k) for k in texts[:_DESCRIBE_ITEMS]]
    if len(texts) > _DESCRIBE_ITEMS:
        parts.append(f"+{len(texts) - _DESCRIBE_ITEMS} str más")
    if others:
        parts.append(f"{others} no str")
    return f"[{', '.join(parts)}]"


def _exact_keys(mapping, expected, what: str, error) -> None:
    """Las claves del Mapping son exactamente `expected` (str), sin hashear ni comparar claves que no sean str exactos."""
    keys = list(mapping)
    texts = {k for k in keys if type(k) is str}
    if len(texts) != len(keys) or texts != set(expected):
        missing = sorted(set(expected) - texts)
        extra = [k for k in keys if type(k) is not str or k not in expected]
        raise error(f"{what}: faltan {missing}, sobran {len(extra)} {_describe_keys(extra)}")


class InputError(ValueError):
    """El objetivo, la configuración, la colección o una fila no tienen la forma pedida (error del llamador)."""


class UnsupportedTargetError(InputError):
    """Mercado o línea fuera del vocabulario FT90 admitido (1X2, cuartos, texto no canónico...)."""


class ConfigError(InputError):
    """La configuración no es un UncertaintyConfig válido."""


class RowError(InputError):
    """Una fila no tiene la forma pedida o no es del objetivo."""


class ProbabilityError(RowError):
    """Probabilidades ausentes, sobrantes, no finitas, fuera de [0, 1] o fuera del símplex."""


class LabelError(RowError):
    """Etiqueta que no es una clase del objetivo."""


class InconsistentPushLabelError(LabelError):
    """PUSH en un objetivo binario (BTTS o línea .5), donde no puede existir."""


class WeekError(RowError):
    """Semana que no es un texto ISO "YYYY-Www" válido."""


class DuplicateSampleError(RowError):
    """sample_id repetido o dos filas del mismo fixture en el mismo objetivo."""


@dataclass(frozen=True)
class UncertaintyConfig:
    seed: int  # obligatorio: semilla del sorteo de semanas
    iterations: int  # obligatorio: réplicas del bootstrap (1..MAX_ITERATIONS)
    bin_count: int = 10
    min_bin_support: int = 30  # presentación, no estadístico
    min_total_support: int = 300  # presentación, no estadístico
    min_weeks: int = 5  # semanas puntuadas mínimas para ejecutar el bootstrap (>= 2)


@dataclass(frozen=True)
class ReliabilityBin:
    index: int
    lower: float
    upper: float
    upper_closed: bool  # solo el último bin incluye su borde superior
    interval: str  # "[0.3, 0.4)" | "[0.9, 1.0]"
    count: int
    mean_predicted: float | None  # None si el bin está vacío (nunca 0)
    observed_frequency: float | None
    abs_gap: float | None  # |mean_predicted - observed_frequency|
    support: str  # EMPTY | BELOW_MIN_SUPPORT | MEETS_MIN_SUPPORT


@dataclass(frozen=True)
class ClassReliability:
    event: str  # la clase evaluada uno contra el resto
    mean_predicted: float | None  # media de p(event) en las filas puntuadas
    observed_frequency: float | None  # frecuencia de event en las filas puntuadas
    ece: float | None
    bins: tuple[ReliabilityBin, ...]


@dataclass(frozen=True)
class Coverage:
    total_input: int
    no_estimate: int
    label_missing: int
    scored: int
    exclusions: Mapping[str, int]  # solo lectura: NO_ESTIMATE y LABEL_MISSING
    abstention_rate: float | None
    scored_coverage: float | None
    weeks_input: int  # semanas distintas en todas las filas
    weeks_scored: int  # semanas distintas con al menos una fila puntuada


@dataclass(frozen=True)
class WeekBlockUncertainty:
    status: str  # NO_SCORED_SAMPLES | INSUFFICIENT_WEEKS | SINGLE_OBSERVED_CLASS | EXPLORATORY_BOOTSTRAP
    method: str
    quantile_method: str
    interval_semantics: str
    seed: int
    iterations: int  # pedidas
    replicates: int  # ejecutadas (0 salvo EXPLORATORY_BOOTSTRAP)
    weeks: int
    min_weeks: int
    rows_per_week_min: int | None
    rows_per_week_max: int | None
    point_ece: float | None  # el ece observado (sin remuestrear)
    replicate_ece_sorted: tuple[float, ...]
    replicate_min: float | None
    replicate_max: float | None
    quantiles: tuple[tuple[str, float], ...]  # (("P10", x), ("P50", x), ("P90", x)) o ()


@dataclass(frozen=True)
class ReliabilityUncertaintyReport:
    diagnostic_id: str
    diagnostic_version: str
    binning_version: str
    market: str
    line: str | None
    line_type: str | None  # HALF | INTEGER; None en BTTS
    settlement_convention: str
    metric_basis: str  # BINARY_EVENT | THREE_CLASS_OVER_UNDER_PUSH
    ece_method: str
    config: UncertaintyConfig
    config_identity: str
    dataset_identity: str
    coverage: Coverage
    sample_status: str  # NO_DATA | INSUFFICIENT_SUPPORT | MEETS_MIN_SUPPORT
    outcome_counts: Mapping[str, int]  # solo lectura, filas puntuadas por clase
    outcome_status: str  # NO_SCORED_SAMPLES | SINGLE_OBSERVED_CLASS | MULTIPLE_OBSERVED_CLASSES
    all_push: bool
    classes: tuple[ClassReliability, ...]  # binario: la del evento; tres clases: OVER, UNDER, PUSH
    ece: float | None
    uncertainty: WeekBlockUncertainty
    calibration_status: str
    evidence_basis: str
    pit_status: str
    receipt_status: str
    limitations: tuple[str, ...]
    report_identity: str


class _Row(NamedTuple):
    sample_id: str
    fixture_id: int
    week: str
    status: str
    probabilities: tuple[float, ...] | None  # en el orden de las clases del objetivo
    label: str | None


def _target(market, line) -> tuple[str | None, tuple[str, ...]]:
    """(line_type, clases) del objetivo, o UnsupportedTargetError."""
    if type(market) is not str or market not in (BTTS_FT90, OVER_UNDER_FT90):
        raise UnsupportedTargetError(f"mercado no admitido (solo {BTTS_FT90} y {OVER_UNDER_FT90}): {_describe(market)}")
    if market == BTTS_FT90:
        if line is not None:
            raise UnsupportedTargetError(f"{BTTS_FT90} no tiene línea: {_describe(line)}")
        return None, (BTTS_YES, BTTS_NO)
    if type(line) is not str or line not in SUPPORTED_LINE_TEXTS:
        raise UnsupportedTargetError(f"línea Over/Under no admitida (solo texto canónico aprobado, sin cuartos): {_describe(line)}")
    return (INTEGER_LINE, (OVER, UNDER, PUSH)) if line.endswith(".0") else (HALF_LINE, (OVER, UNDER))


def _check_config(config) -> None:
    if type(config) is not UncertaintyConfig:
        raise ConfigError(f"config tiene que ser un UncertaintyConfig, no {_describe(config)}")
    for f in fields(config):
        if type(getattr(config, f.name)) is not int:
            raise ConfigError(f"config.{f.name} tiene que ser un int (bool no): {_describe(getattr(config, f.name))}")
    if config.seed < 0:
        raise ConfigError(f"config.seed tiene que ser >= 0: {_describe(config.seed)}")
    if not 1 <= config.iterations <= MAX_ITERATIONS:
        raise ConfigError(f"config.iterations tiene que estar en [1, {MAX_ITERATIONS}]: {_describe(config.iterations)}")
    if not 1 <= config.bin_count <= MAX_BIN_COUNT:
        raise ConfigError(f"config.bin_count tiene que estar en [1, {MAX_BIN_COUNT}]: {_describe(config.bin_count)}")
    if config.min_bin_support < 1 or config.min_total_support < 1:
        raise ConfigError("config.min_bin_support y config.min_total_support tienen que ser >= 1")
    if config.min_weeks < MIN_WEEKS_FLOOR:
        raise ConfigError(f"config.min_weeks tiene que ser >= {MIN_WEEKS_FLOOR}: {_describe(config.min_weeks)}")


def _week(value, what: str) -> str:
    if (type(value) is not str or len(value) != 8 or not value.isascii() or value[4:6] != "-W"
            or not value[:4].isdigit() or not value[6:].isdigit()):
        raise WeekError(f"{what}: week tiene que ser un texto ISO 'YYYY-Www': {_describe(value)}")
    try:
        date.fromisocalendar(int(value[:4]), int(value[6:]), 1)
    except ValueError:
        raise WeekError(f"{what}: semana ISO inexistente: {_describe(value)}") from None
    return value


def _label(value, classes: tuple[str, ...], what: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or value not in _KNOWN_LABELS:
        raise LabelError(f"{what}: etiqueta no válida: {_describe(value)}")
    if value == PUSH and PUSH not in classes:
        raise InconsistentPushLabelError(f"{what}: PUSH no existe en un objetivo binario {classes}")
    if value not in classes:
        raise LabelError(f"{what}: la etiqueta {value!r} no es una clase del objetivo {classes}")
    return value


def _probabilities(value, classes: tuple[str, ...], what: str) -> tuple[float, ...]:
    if not isinstance(value, Mapping):
        raise ProbabilityError(f"{what}: probabilities tiene que ser un Mapping con las claves {classes}, no {_describe(value)}")
    _exact_keys(value, classes, f"{what}: probabilities (se esperan exactamente {classes})", ProbabilityError)
    plain = {}
    for c in classes:  # tipo exacto y rango antes de los validadores publicados: sus mensajes solo verán int/float exactos
        p = value[c]
        # el rango antes que la finitud: math.isfinite(10**400) desborda (OverflowError)
        if (type(p) is not int and type(p) is not float) or not 0 <= p <= 1 or not math.isfinite(p):
            raise ProbabilityError(f"{what}: probabilidad {c} no válida (int o float exacto, finito, en [0, 1]): {_describe(p)}")
        plain[c] = p
    try:
        if len(classes) == 2:
            checked = validate_binary_probabilities((plain[classes[0]], plain[classes[1]]))
        else:
            mapping = validate_integer_probabilities(plain)
            checked = tuple(mapping[c] for c in classes)
    except ProbabilityEvidenceError as exc:
        raise ProbabilityError(f"{what}: {exc}") from None
    return tuple(float(p) + 0.0 for p in checked)  # -0.0 -> 0.0; 1 y 1.0 son la misma probabilidad


def _row(i: int, row, market: str, line: str | None, classes: tuple[str, ...]) -> _Row:
    what = f"fila {i}"
    if not isinstance(row, Mapping):
        raise RowError(f"{what}: se espera un Mapping, no {_describe(row)}")
    _exact_keys(row, ROW_FIELDS, what, RowError)
    sample_id, fixture_id = row["sample_id"], row["fixture_id"]
    if type(sample_id) is not str or not sample_id or sample_id != sample_id.strip():
        raise RowError(f"{what}: sample_id tiene que ser un str no vacío sin espacios en los extremos: {_describe(sample_id)}")
    if type(fixture_id) is not int or fixture_id < 1:
        raise RowError(f"{what}: fixture_id tiene que ser un int >= 1: {_describe(fixture_id)}")
    row_market, row_line = row["market"], row["line"]
    if (type(row_market) is not str or row_market != market
            or (row_line is not None if line is None else type(row_line) is not str or row_line != line)):
        raise RowError(f"{what}: ({_describe(row_market)}, {_describe(row_line)}) no es el objetivo ({market!r}, {line!r})")
    week = _week(row["week"], what)
    status = row["estimate_status"]
    if type(status) is not str or status not in (ESTIMATE, NO_ESTIMATE):
        raise RowError(f"{what}: estimate_status tiene que ser {ESTIMATE} o {NO_ESTIMATE}: {_describe(status)}")
    label = _label(row["label"], classes, what)
    if status == NO_ESTIMATE:
        if row["probabilities"] is not None:
            raise ProbabilityError(f"{what}: una abstención {NO_ESTIMATE} no lleva probabilidades")
        return _Row(sample_id, fixture_id, week, status, None, label)
    if row["probabilities"] is None:
        raise ProbabilityError(f"{what}: {ESTIMATE} sin probabilidades (no se infieren)")
    return _Row(sample_id, fixture_id, week, status, _probabilities(row["probabilities"], classes, what), label)


def _sha256(payload) -> str:
    return hashlib.sha256(_dumps(payload).encode("ascii")).hexdigest()


def _plain(value):
    if isinstance(value, int) and not isinstance(value, bool) and not -_DECIMAL_INT_BOUND < value < _DECIMAL_INT_BOUND:
        return {INT_ENCODING: _int_hex(value)}  # sin conversión decimal: ver «Enteros grandes» en el módulo
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _dumps(payload) -> str:
    return json.dumps(_plain(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_report_json(report: ReliabilityUncertaintyReport) -> str:
    """Serialización determinista del informe (claves ordenadas, floats por repr, ASCII)."""
    return _dumps(report)


def block_draw(seed: int, replicate: int, draw: int, weeks: int) -> int:
    """Índice (0..weeks-1) de la semana del sorteo `draw` de la réplica `replicate` (ver el módulo)."""
    token = "|".join(_int_token(v) for v in (seed, replicate, draw))  # decimal como antes; INT_HEX_V1 si es enorme
    digest = hashlib.sha256(f"{BOOTSTRAP_METHOD}|{token}".encode("ascii")).digest()
    return int.from_bytes(digest, "big") % weeks


def _edges(bin_count: int) -> tuple[float, ...]:
    return tuple(k / bin_count for k in range(bin_count))


def _week_stats(scored: list[_Row], positions: tuple[int, ...], edges: tuple[float, ...], classes: tuple[str, ...]) -> dict:
    """{semana: [filas, [[ [n, S_p, S_y] por bin ] por clase evaluada]]} con sumas exactas."""
    stats = {}
    for r in scored:
        week = stats.setdefault(r.week, [0, [[[0, Fraction(0), 0] for _ in edges] for _ in positions]])
        week[0] += 1
        per_class = week[1]
        for slot, ci in enumerate(positions):
            p = r.probabilities[ci]
            cell = per_class[slot][bisect_right(edges, p) - 1]
            cell[0] += 1
            cell[1] += Fraction(p)
            cell[2] += int(r.label == classes[ci])
    return stats


def _combine(stats: dict, multiplicity: Mapping[str, int], n_classes: int, n_bins: int) -> tuple[int, list]:
    total = 0
    cells = [[[0, Fraction(0), 0] for _ in range(n_bins)] for _ in range(n_classes)]
    for week, m in multiplicity.items():
        n, per_class = stats[week]
        total += m * n
        for c in range(n_classes):
            for b in range(n_bins):
                src, dst = per_class[c][b], cells[c][b]
                dst[0] += m * src[0]
                dst[1] += m * src[1]
                dst[2] += m * src[2]
    return total, cells


def _ece_exact(total: int, cells: list) -> tuple[Fraction, list[Fraction]]:
    """(ece de la métrica, ECE de cada clase evaluada), exactos."""
    per_class = [sum((abs(sp - sy) for _, sp, sy in bins), Fraction(0)) / total for bins in cells]
    return sum(per_class, Fraction(0)) / len(per_class), per_class


def _support(count: int, minimum: int) -> str:
    return EMPTY if not count else BELOW_MIN_SUPPORT if count < minimum else MEETS_MIN_SUPPORT


def _class_reliability(event: str, bins_cells: list, total: int, ece: Fraction | None, edges, config) -> ClassReliability:
    bins = []
    for k, (n, sp, sy) in enumerate(bins_cells):
        last = k == len(edges) - 1
        lower, upper = edges[k], 1.0 if last else edges[k + 1]
        bins.append(ReliabilityBin(
            index=k, lower=lower, upper=upper, upper_closed=last, interval=f"[{lower!r}, {upper!r}{']' if last else ')'}",
            count=n, mean_predicted=float(sp / n) if n else None, observed_frequency=float(Fraction(sy, n)) if n else None,
            abs_gap=float(abs(sp - sy) / n) if n else None, support=_support(n, config.min_bin_support),
        ))
    sp_all = sum((c[1] for c in bins_cells), Fraction(0))
    sy_all = sum(c[2] for c in bins_cells)
    return ClassReliability(event=event, mean_predicted=float(sp_all / total) if total else None,
                            observed_frequency=float(Fraction(sy_all, total)) if total else None,
                            ece=float(ece) if ece is not None else None, bins=tuple(bins))


def _nearest_rank(values: list[float], q: Fraction) -> float:
    return values[max(math.ceil(q * len(values)), 1) - 1]


def goals_reliability_uncertainty(rows, *, market, line, config) -> ReliabilityUncertaintyReport:
    """Fiabilidad, ECE e incertidumbre por bloques de semana de un objetivo FT90 (ver el módulo)."""
    line_type, classes = _target(market, line)
    _check_config(config)
    if not isinstance(rows, (list, tuple)):
        raise InputError(f"las filas van en una lista o tupla (no un iterable de un solo uso), no en {_describe(rows)}")
    parsed = sorted((_row(i, r, market, line, classes) for i, r in enumerate(rows)), key=lambda r: r.sample_id)
    for name, counter in (("sample_id", Counter(r.sample_id for r in parsed)), ("fixture_id", Counter(r.fixture_id for r in parsed))):
        repeated = sorted(k for k, n in counter.items() if n > 1)
        if repeated:
            shown = ", ".join(_describe(k) for k in repeated[:_DESCRIBE_ITEMS])
            more = f" y {len(repeated) - _DESCRIBE_ITEMS} más" if len(repeated) > _DESCRIBE_ITEMS else ""
            raise DuplicateSampleError(f"{name} repetido en el objetivo ({market}, {line}): {len(repeated)} valor(es): {shown}{more}")

    binary = len(classes) == 2
    positions = (0,) if binary else tuple(range(len(classes)))  # binario: solo la clase del evento
    edges = _edges(config.bin_count)
    scored = [r for r in parsed if r.status == ESTIMATE and r.label is not None]
    no_estimate = sum(1 for r in parsed if r.status == NO_ESTIMATE)
    label_missing = len(parsed) - no_estimate - len(scored)
    total_input = len(parsed)
    stats = _week_stats(scored, positions, edges, classes)
    weeks = sorted(stats)
    n_scored, cells = _combine(stats, {w: 1 for w in weeks}, len(positions), len(edges))
    point, per_class = _ece_exact(n_scored, cells) if n_scored else (None, [None] * len(positions))

    outcome_counts = Counter(r.label for r in scored)
    observed_classes = sum(1 for c in classes if outcome_counts[c])
    outcome_status = NO_SCORED_SAMPLES if not scored else SINGLE_OBSERVED_CLASS if observed_classes == 1 else MULTIPLE_OBSERVED_CLASSES
    rows_per_week = [stats[w][0] for w in weeks]

    replicates: list[float] = []
    if not scored:
        u_status = NO_SCORED_SAMPLES
    elif len(weeks) < config.min_weeks:
        u_status = INSUFFICIENT_WEEKS
    elif outcome_status == SINGLE_OBSERVED_CLASS:
        u_status = SINGLE_OBSERVED_CLASS
    else:
        u_status = EXPLORATORY_BOOTSTRAP
        for r in range(config.iterations):
            drawn = Counter(weeks[block_draw(config.seed, r, j, len(weeks))] for j in range(len(weeks)))
            replicates.append(float(_ece_exact(*_combine(stats, drawn, len(positions), len(edges)))[0]))
        replicates.sort()
    uncertainty = WeekBlockUncertainty(
        status=u_status, method=BOOTSTRAP_METHOD, quantile_method=QUANTILE_METHOD, interval_semantics=INTERVAL_SEMANTICS,
        seed=config.seed, iterations=config.iterations, replicates=len(replicates), weeks=len(weeks), min_weeks=config.min_weeks,
        rows_per_week_min=min(rows_per_week) if rows_per_week else None, rows_per_week_max=max(rows_per_week) if rows_per_week else None,
        point_ece=float(point) if point is not None else None, replicate_ece_sorted=tuple(replicates),
        replicate_min=replicates[0] if replicates else None, replicate_max=replicates[-1] if replicates else None,
        quantiles=tuple((name, _nearest_rank(replicates, q)) for name, q in QUANTILES) if replicates else (),
    )

    config_identity = _sha256({"diagnostic_id": DIAGNOSTIC_ID, "diagnostic_version": DIAGNOSTIC_VERSION, "binning_version": BINNING_VERSION,
                               "ece_methods": [ECE_METHOD_BINARY, ECE_METHOD_THREE_CLASS], "bootstrap_method": BOOTSTRAP_METHOD,
                               "quantile_method": QUANTILE_METHOD, "quantiles": [[n, str(q)] for n, q in QUANTILES], "config": config})
    dataset_identity = _sha256({"market": market, "line": line, "settlement_convention": SETTLEMENT_CONVENTION_FT90, "rows": [
        {"sample_id": r.sample_id, "fixture_id": r.fixture_id, "week": r.week, "estimate_status": r.status, "label": r.label,
         "probabilities": None if r.probabilities is None else dict(zip(classes, r.probabilities))} for r in parsed]})
    report = ReliabilityUncertaintyReport(
        diagnostic_id=DIAGNOSTIC_ID, diagnostic_version=DIAGNOSTIC_VERSION, binning_version=BINNING_VERSION, market=market, line=line,
        line_type=line_type, settlement_convention=SETTLEMENT_CONVENTION_FT90, metric_basis=BINARY_EVENT if binary else THREE_CLASS,
        ece_method=ECE_METHOD_BINARY if binary else ECE_METHOD_THREE_CLASS, config=config, config_identity=config_identity,
        dataset_identity=dataset_identity,
        coverage=Coverage(
            total_input=total_input, no_estimate=no_estimate, label_missing=label_missing, scored=len(scored),
            exclusions=MappingProxyType({LABEL_MISSING: label_missing, NO_ESTIMATE: no_estimate}),
            abstention_rate=float(Fraction(no_estimate, total_input)) if total_input else None,
            scored_coverage=float(Fraction(len(scored), total_input)) if total_input else None,
            weeks_input=len({r.week for r in parsed}), weeks_scored=len(weeks)),
        sample_status=NO_DATA if not scored else INSUFFICIENT_SUPPORT if len(scored) < config.min_total_support else MEETS_MIN_SUPPORT,
        outcome_counts=MappingProxyType({c: outcome_counts[c] for c in classes}), outcome_status=outcome_status,
        all_push=bool(scored) and outcome_counts[PUSH] == len(scored),
        classes=tuple(_class_reliability(classes[ci], cells[slot], n_scored, per_class[slot], edges, config)
                      for slot, ci in enumerate(positions)),
        ece=float(point) if point is not None else None, uncertainty=uncertainty, calibration_status=CALIBRATION_STATUS,
        evidence_basis=EVIDENCE_BASIS, pit_status=PIT_STATUS, receipt_status=RECEIPT_STATUS, limitations=LIMITATIONS,
        report_identity="",
    )
    if not (total_input == no_estimate + label_missing + len(scored) == len(rows) and n_scored == len(scored)
            and all(sum(b.count for b in c.bins) == n_scored for c in report.classes)):
        raise AssertionError("recuento incoherente en el diagnóstico de fiabilidad e incertidumbre")
    payload = _plain(report)
    del payload["report_identity"]
    return replace(report, report_identity=_sha256(payload))
