"""Diagrama de fiabilidad de probabilidades binarias de goles a 90' (MOD-DEV-099). Puro: sin BD, red ni disco.

Responde, para un evento binario fijo (BTTS YES/NO u Over/Under en una línea .5), a: «de las
predicciones cuya probabilidad cae en el bin B, ¿qué probabilidad media se predijo y con qué frecuencia
ocurrió el evento?». Es un diagnóstico de evaluación: no ajusta, recalibra ni recomienda nada, y no
sustituye a Brier/log-loss (research.goals_markets.evaluation), que no tocan esta interfaz. Tampoco
calcula ECE ni ningún otro escalar de calibración, intervalos de confianza o certificados.

Objetivo (target): market, line, event. BTTS: line None, event YES | NO. OVER_UNDER: line con el texto
canónico de una línea .5 aprobada en research.m58.goal_labels ("0.5" ... "6.5"), event OVER | UNDER.
Las líneas enteras no son binarias (PUSH) y se rechazan. Todos los registros de una llamada deben ser de
ese objetivo: un registro de otro mercado, línea o evento es RecordError (nunca se descarta en silencio),
así no se mezclan eventos complementarios ni mercados distintos en una misma tabla.

Registro (Mapping con exactamente estas claves; los registros van en una lista o tupla y no se modifican):
  fixture_id     int >= 1 (bool no)
  prediction_id  str no vacío y sin espacios en los extremos: identidad de la instancia de predicción
  market, line, event   la especificación del evento predicho (igual al objetivo)
  probability    P(event): int o float (bool no), finito, en [0, 1]. Sin recorte, redondeo ni imputación;
                 un str, Decimal, Fraction u otro tipo es ProbabilityError (no se convierte)
  predicted_at   datetime con zona horaria: cuándo se emitió la predicción (lo declara el llamador)
  kickoff_at     datetime con zona horaria: inicio del partido (lo declara el llamador)
  outcome        Mapping con exactamente fixture_id, market, line, result y observed_at
El resultado retrospectivo (outcome.result) lo aporta el llamador para el mismo partido y mercado:
outcome.fixture_id/market/line distintos de los del registro son RecordError. result debe ser uno de
los dos resultados del mercado (YES | NO, OVER | UNDER; PUSH, minúsculas, bool o None no). Aquí no se
liquida nada desde marcadores ni se aplica ninguna política FT/AET/PEN: la exactitud histórica y de
origen del resultado queda fuera de la autoridad de este diagnóstico. observed = 1 si result == event.

Tiempo (TemporalError): las tres marcas son datetime con utcoffset (no date, no naive), representables
en UTC, y predicted_at < kickoff_at < observed_at, estrictamente. Una predicción emitida en el saque o después,
o un resultado observado antes del partido, se rechazan. Las marcas no se autentican: que una predicción
sea anterior al partido según el llamador no la convierte en PIT histórico verificado (TRUE_PIT).

Identidad y duplicados: clave (fixture_id, prediction_id, market, line, event). Dos registros con la
misma clave y los mismos valores (las marcas se comparan como instantes; 1 y 1.0 son la misma
probabilidad) cuentan una vez (duplicates_collapsed). Misma clave con cualquier valor distinto es
DuplicateConflictError. Claves distintas del mismo partido (otra prediction_id) son predicciones
distintas y cuentan las dos; distinct_fixtures lo deja a la vista (pueden no ser independientes).

Fail-closed: cualquier registro no válido lanza una excepción (subclase de RecordError) y no hay
resultado parcial; por eso no existe un recuento de rechazados: total_input = accepted +
duplicates_collapsed.

Bins (BINNING_VERSION): 10 bins de igual anchura, fijos y en orden de índice 0..9. Los bordes son los
double LOWER_EDGES[k] = k / 10 (el double más cercano a k/10, igual que el literal 0.k) y la comparación
es exacta entre doubles, sin multiplicar: bin k = [LOWER_EDGES[k], LOWER_EDGES[k+1]) para k = 0..8 y
bin 9 = [0.9, 1.0], cerrado por arriba. Así p = 0 cae en el bin 0, p = 1 en el bin 9 y un valor en un
borde pertenece siempre al bin que empieza en él (0.3 -> bin 3; el double anterior a 0.3 -> bin 2).

Por bin: count, positives, mean_predicted = fsum(p) / count (fsum: suma exactamente redondeada, no
depende del orden) y observed_fraction = positives / count. Un bin vacío tiene count 0 y
mean_predicted = observed_fraction = None (nunca 0). support: EMPTY | BELOW_MIN_SUPPORT (0 < count <
MIN_BIN_SUPPORT) | MEETS_MIN_SUPPORT. Los valores numéricos se informan también con poco soporte.
sample_status del total aceptado: NO_DATA (0) | BELOW_MIN_TOTAL (< MIN_TOTAL_SUPPORT) | MEETS_MIN_TOTAL.
Los umbrales son de presentación, no un contraste estadístico: alcanzarlos no certifica calibración.

Determinismo: el resultado solo depende del multiconjunto de registros (no del orden, de hashes ni del
entorno); los registros se validan y ordenan por clave antes de deduplicar.
"""

import math
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from research.goals_markets.evaluation import BTTS, OVER_UNDER
from research.m58.goal_labels import BTTS_NO, BTTS_YES, OVER, SUPPORTED_LINE_TEXTS, UNDER

DIAGNOSTIC_ID = "GOALS_BINARY_RELIABILITY"
DIAGNOSTIC_VERSION = "1"
BINNING_VERSION = "EQUAL_WIDTH_10_LEFT_CLOSED_LAST_BIN_CLOSED_V1"
BIN_COUNT = 10
LOWER_EDGES = tuple(k / BIN_COUNT for k in range(BIN_COUNT))  # 0.0, 0.1, ..., 0.9 (doubles)
MIN_BIN_SUPPORT = 30  # umbral de presentación por bin, no estadístico
MIN_TOTAL_SUPPORT = 300  # umbral de presentación del total aceptado, no estadístico

EMPTY, BELOW_MIN_SUPPORT, MEETS_MIN_SUPPORT = "EMPTY", "BELOW_MIN_SUPPORT", "MEETS_MIN_SUPPORT"
NO_DATA, BELOW_MIN_TOTAL, MEETS_MIN_TOTAL = "NO_DATA", "BELOW_MIN_TOTAL", "MEETS_MIN_TOTAL"

EVIDENCE_BASIS = "CALLER_SUPPLIED_PREDICTIONS_AND_RETROSPECTIVE_OUTCOMES"
PIT_STATUS = "NOT_CERTIFIED"
LIMITATIONS = (
    "OUTCOMES_SUPPLIED_BY_CALLER_NOT_VERIFIED",
    "TIMESTAMPS_SUPPLIED_BY_CALLER_NOT_AUTHENTICATED",
    "NOT_TRUE_PIT_CERTIFIED",
    "NOT_A_COMPLETE_HISTORICAL_COHORT",
    "PREDICTIONS_MAY_NOT_BE_INDEPENDENT",
    "NO_CONFIDENCE_INTERVALS_OR_CALIBRATION_CERTIFICATION",
    "SUPPORT_THRESHOLDS_ARE_REPORTING_RULES_NOT_STATISTICAL_TESTS",
)

MARKET_EVENTS = {BTTS: (BTTS_YES, BTTS_NO), OVER_UNDER: (OVER, UNDER)}
HALF_LINE_TEXTS = tuple(text for text in SUPPORTED_LINE_TEXTS if text.endswith(".5"))
RECORD_FIELDS = frozenset({"fixture_id", "prediction_id", "market", "line", "event", "probability", "predicted_at", "kickoff_at",
                           "outcome"})
OUTCOME_FIELDS = frozenset({"fixture_id", "market", "line", "result", "observed_at"})


class RecordError(ValueError):
    """La colección, el objetivo o un registro no tienen la forma pedida (error del llamador)."""


class ProbabilityError(RecordError):
    """La probabilidad de un registro no es un número finito admitido en [0, 1]."""


class TemporalError(RecordError):
    """Una marca de tiempo no es un datetime con zona o el orden predicción < saque < observación falla."""


class DuplicateConflictError(RecordError):
    """Dos registros con la misma identidad de predicción y valores distintos."""


@dataclass(frozen=True)
class ReliabilityBin:
    index: int
    lower: float
    upper: float
    upper_closed: bool  # solo el último bin incluye su borde superior
    interval: str  # "[0.3, 0.4)" | "[0.9, 1.0]"
    count: int
    positives: int
    mean_predicted: float | None  # None si el bin está vacío
    observed_fraction: float | None  # None si el bin está vacío (nunca 0)
    support: str  # EMPTY | BELOW_MIN_SUPPORT | MEETS_MIN_SUPPORT


@dataclass(frozen=True)
class ReliabilityDiagnostic:
    diagnostic_id: str
    diagnostic_version: str
    binning_version: str
    market: str
    line: str | None
    event: str
    total_input: int
    duplicates_collapsed: int
    accepted: int
    distinct_fixtures: int
    positives: int
    min_bin_support: int
    min_total_support: int
    sample_status: str  # NO_DATA | BELOW_MIN_TOTAL | MEETS_MIN_TOTAL
    bins: tuple[ReliabilityBin, ...]  # siempre BIN_COUNT, en orden de índice
    evidence_basis: str
    pit_status: str
    limitations: tuple[str, ...]


def bin_index(probability: float) -> int:
    """Índice del bin de una probabilidad ya validada (ver BINNING_VERSION)."""
    return bisect_right(LOWER_EDGES, probability) - 1


def _target(market, line, event) -> tuple[str, str | None, str]:
    if not isinstance(market, str) or market not in MARKET_EVENTS:
        raise RecordError(f"mercado no admitido: {market!r}")
    if market == BTTS and line is not None:
        raise RecordError(f"BTTS no tiene línea: {line!r}")
    if market == OVER_UNDER and (not isinstance(line, str) or line not in HALF_LINE_TEXTS):
        raise RecordError(f"línea Over/Under no admitida (solo texto canónico .5): {line!r}")
    if not isinstance(event, str) or event not in MARKET_EVENTS[market]:
        raise RecordError(f"evento no admitido en {market}: {event!r}")
    return market, line, event


def _probability(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProbabilityError(f"la probabilidad debe ser int o float, no {type(value).__name__}: {value!r}")
    if not 0 <= value <= 1 or not math.isfinite(value):  # el rango primero: math.isfinite(10**400) desborda (OverflowError)
        raise ProbabilityError(f"probabilidad fuera de [0, 1] o no finita: {value!r}")
    return float(value) + 0.0  # -0.0 -> 0.0


def _instant(value, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TemporalError(f"{name} debe ser un datetime con zona horaria: {value!r}")
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:  # p. ej. 0001-01-01 con offset positivo: el instante UTC queda fuera del rango de datetime
        raise TemporalError(f"{name} no es representable en UTC: {value!r}") from None


def _exact_fields(obj, fields: frozenset, what: str) -> None:
    if not isinstance(obj, Mapping):
        raise RecordError(f"{what}: se espera un Mapping, no {type(obj).__name__}")
    keys = set(obj)
    if keys != fields:
        raise RecordError(f"{what}: faltan {sorted(map(str, fields - keys))}, sobran {sorted(map(str, keys - fields))}")


def _record(i: int, record, target) -> tuple:
    """(clave de identidad, valores normalizados) de un registro válido."""
    what = f"registro {i}"
    _exact_fields(record, RECORD_FIELDS, what)
    fixture_id, prediction_id = record["fixture_id"], record["prediction_id"]
    if type(fixture_id) is not int or fixture_id < 1:
        raise RecordError(f"{what}: fixture_id debe ser un int >= 1: {fixture_id!r}")
    if not isinstance(prediction_id, str) or not prediction_id or prediction_id != prediction_id.strip():
        raise RecordError(f"{what}: prediction_id debe ser un str no vacío sin espacios en los extremos: {prediction_id!r}")
    spec = (record["market"], record["line"], record["event"])
    if spec != target:
        raise RecordError(f"{what}: evento {spec!r} distinto del objetivo {target!r}")
    try:
        probability = _probability(record["probability"])
    except ProbabilityError as exc:
        raise ProbabilityError(f"{what}: {exc}") from None

    outcome = record["outcome"]
    _exact_fields(outcome, OUTCOME_FIELDS, f"{what}, outcome")
    outcome_spec = (outcome["fixture_id"], outcome["market"], outcome["line"])
    if type(outcome["fixture_id"]) is not int or outcome_spec != (fixture_id, target[0], target[1]):
        raise RecordError(f"{what}: el outcome {outcome_spec!r} es de otro partido o mercado")
    result = outcome["result"]
    if not isinstance(result, str) or result not in MARKET_EVENTS[target[0]]:
        raise RecordError(f"{what}: resultado binario no válido en {target[0]}: {result!r}")

    try:
        predicted_at = _instant(record["predicted_at"], "predicted_at")
        kickoff_at = _instant(record["kickoff_at"], "kickoff_at")
        observed_at = _instant(outcome["observed_at"], "outcome.observed_at")
    except TemporalError as exc:
        raise TemporalError(f"{what}: {exc}") from None
    if not predicted_at < kickoff_at < observed_at:
        raise TemporalError(f"{what}: se exige predicted_at < kickoff_at < observed_at: "
                            f"{predicted_at.isoformat()}, {kickoff_at.isoformat()}, {observed_at.isoformat()}")
    return (fixture_id, prediction_id), (probability, predicted_at, kickoff_at, result, observed_at)


def _bin(index: int, items: list[tuple[float, int]]) -> ReliabilityBin:
    lower = LOWER_EDGES[index]
    last = index == BIN_COUNT - 1
    upper = 1.0 if last else LOWER_EDGES[index + 1]
    count = len(items)
    positives = sum(y for _, y in items)
    return ReliabilityBin(
        index=index, lower=lower, upper=upper, upper_closed=last, interval=f"[{lower!r}, {upper!r}{']' if last else ')'}",
        count=count, positives=positives,
        mean_predicted=math.fsum(p for p, _ in items) / count if count else None,
        observed_fraction=positives / count if count else None,
        support=EMPTY if not count else BELOW_MIN_SUPPORT if count < MIN_BIN_SUPPORT else MEETS_MIN_SUPPORT,
    )


def binary_reliability(records, *, market, event, line=None) -> ReliabilityDiagnostic:
    """Tabla de fiabilidad de P(event) en market/line sobre registros suministrados (ver el módulo)."""
    target = _target(market, line, event)
    if not isinstance(records, (list, tuple)):
        raise RecordError(f"los registros van en una lista o tupla, no en {type(records).__name__}")
    validated = sorted(_record(i, record, target) for i, record in enumerate(records))

    unique: dict[tuple, tuple] = {}
    for key, values in validated:
        if key in unique and unique[key] != values:
            raise DuplicateConflictError(f"identidad {key + target!r} con valores distintos")
        unique[key] = values

    buckets: list[list[tuple[float, int]]] = [[] for _ in range(BIN_COUNT)]
    for probability, _, _, result, _ in unique.values():
        buckets[bin_index(probability)].append((probability, int(result == event)))
    bins = tuple(_bin(k, items) for k, items in enumerate(buckets))

    accepted = len(unique)
    diagnostic = ReliabilityDiagnostic(
        diagnostic_id=DIAGNOSTIC_ID, diagnostic_version=DIAGNOSTIC_VERSION, binning_version=BINNING_VERSION,
        market=target[0], line=target[1], event=target[2],
        total_input=len(validated), duplicates_collapsed=len(validated) - accepted, accepted=accepted,
        distinct_fixtures=len({fixture_id for fixture_id, _ in unique}), positives=sum(b.positives for b in bins),
        min_bin_support=MIN_BIN_SUPPORT, min_total_support=MIN_TOTAL_SUPPORT,
        sample_status=NO_DATA if not accepted else BELOW_MIN_TOTAL if accepted < MIN_TOTAL_SUPPORT else MEETS_MIN_TOTAL,
        bins=bins, evidence_basis=EVIDENCE_BASIS, pit_status=PIT_STATUS, limitations=LIMITATIONS,
    )
    if sum(b.count for b in bins) != accepted or diagnostic.total_input != accepted + diagnostic.duplicates_collapsed:
        raise AssertionError("recuento incoherente en el diagnóstico de fiabilidad")
    return diagnostic
