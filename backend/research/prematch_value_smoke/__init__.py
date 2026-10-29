"""Demostración sintética prepartido tasas de gol -> Poisson -> BTTS/Over-Under -> valor de cuota (MOD-DEV-108A).

Puro y determinista: sin BD, red, disco, proveedores, variables de entorno ni reloj. Solo comprueba que
los componentes de investigación publicados se conectan; NO es un modelo nuevo, las probabilidades NO
están calibradas (referencia Poisson independiente) y un EV > 0 NO es una oportunidad de apuesta real.

Une, sin reimplementarlas ni modificarlas:
  research.venue_goal_rates.baseline.estimate_venue_goal_rates     (lambda_home, lambda_away o NO_ESTIMATE)
  research.m58.goal_reference.independent_poisson_reference        (64 celdas de referencia)
  research.m58.goal_projection.goals_market_probabilities          (13 líneas Over/Under y BTTS)
  research.quote_value_bridge.evaluate_quote_value                 (elegibilidad de la cuota, cuota justa y EV)

Interfaz:
  evaluate_prematch_value_smoke(*, target, history, cutoff, memberships, context, quotes) -> PrematchValueSmoke
target, history, cutoff y memberships son los del estimador; context (EvaluationContext) y quotes (list o
tuple de QuoteObservation), los del puente. Todo lo aporta el llamador ya construido.

Orden (el primer fallo decide):
  1. Forma, identidad y cronología -> PrematchSmokeInputError (sin resultado parcial): target Mapping con
     fixture_id int (no bool) igual a context.target.fixture_id; target.kickoff_at y context.kickoff_at,
     el mismo instante UTC; cutoff <= context.issued_at < context.kickoff_at, todos con zona horaria;
     quotes list/tuple de QuoteObservation; en BTTS_FT90 y OVER_UNDER_FT90, selección del mercado y BTTS
     sin línea. Es consistencia sintética entre entradas, no acreditación independiente de los instantes.
     Diagnóstico seguro (MOD-DEV108A-R1): los mensajes nunca llaman al __repr__, __str__ ni __format__ de un
     valor rechazado; solo métodos base de int, str y type (un int de más de 64 bits se describe por su signo
     y sus bits; un str, por sus primeros 40 caracteres; un datetime, por sus campos sin zona; cualquier otro
     valor, por el nombre de su tipo) y siempre son str exactos y acotados. Los instantes se leen con los
     métodos base de datetime (nunca los de una subclase); una
     Exception ordinaria del tzinfo del llamador (p. ej. un desplazamiento de 30 h) es PrematchSmokeInputError;
     un BaseException se propaga. Los errores del estimador, la referencia, la proyección y el puente no se tocan.
  2. Mercado: 1X2_FT90 -> UNSUPPORTED (ONE_X_TWO_PROJECTION_NOT_AUTHORIZED; no hay proyección 1X2) sin
     llamar a nada. Over/Under fuera de las 13 líneas canónicas (cuartos incluidos) u otro mercado ->
     el puente decide UNSUPPORTED con sus motivos (sin probabilidades: probabilities=None). Ni el
     estimador ni la referencia se ejecutan.
  3. Estimador, una sola vez. NO_ESTIMATE -> NO_ESTIMATE con sus motivos; sin probabilidades ni valor,
     nunca lambda 0 ni un relleno.
  4. Referencia Poisson y proyección con las lambdas tal cual.
  5. Selección pedida, leída de la proyección sin alterar masas (sin renormalizar):
       BTTS YES/NO y Over/Under n.5: p_win = P(selección); p_lose = p_push = None (las calcula el motor).
       Over/Under n.0: p_win = P(selección), p_lose = P(contraria), p_push = P(PUSH), explícitas.
  6. El puente evalúa elegibilidad y valor; su QuoteValueResult se conserva intacto y su estado y sus
     motivos son los del resultado (VALUE_EVALUATED o NO_VALUE), sin reinterpretarlos.

Límite de diseño (DESIGN_LIMITS): el estimador publicado filtra los partidos con resultado válido ANTES
de ordenar y elegir los 20 más recientes; no es la población fija del contrato DI N3/G2. No se corrige
aquí. Esta demostración no certifica la selección histórica real, la temporalidad independiente ni la
ausencia de leakage; la aceptación normativa G2/N3 y la evidencia histórica auténtica siguen en
WAIT_CONTRACT.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

from research import quote_value_bridge
from research.m58 import goal_projection, goal_reference
from research.m58.goal_labels import PUSH, over_under_line
from research.quote_temporal_eligibility import (BTTS, MARKET_SELECTIONS, ONE_X_TWO, OVER_UNDER, SUPPORTED_LINE_TEXTS, EvaluationContext,
                                                 QuoteObservation, QuoteTarget)
from research.quote_value_bridge import NO_VALUE, UNSUPPORTED, VALUE_EVALUATED, QuoteValueResult, SelectionProbabilities
from research.venue_goal_rates import baseline
from research.venue_goal_rates.baseline import ESTIMATE, NO_ESTIMATE, VenueGoalRates

SMOKE_ID = "PREMATCH_GOAL_TO_QUOTE_VALUE_SMOKE"
SMOKE_VERSION = "1"
STATUSES = (VALUE_EVALUATED, NO_ESTIMATE, NO_VALUE, UNSUPPORTED)  # los publicados por el puente y el estimador

ONE_X_TWO_PROJECTION_NOT_AUTHORIZED = "ONE_X_TWO_PROJECTION_NOT_AUTHORIZED"

COMPONENTS = (
    f"research.venue_goal_rates.baseline.estimate_venue_goal_rates:{baseline.POLICY}",
    "research.m58.goal_reference.independent_poisson_reference",
    "research.m58.goal_projection.goals_market_probabilities",
    f"research.quote_value_bridge.evaluate_quote_value:{quote_value_bridge.BRIDGE_ID}:{quote_value_bridge.BRIDGE_VERSION}",
)
INPUT_BASIS = "SYNTHETIC_CALLER_SUPPLIED_HISTORY_CONTEXT_AND_QUOTES"
PROBABILITY_BASIS = "INDEPENDENT_POISSON_REFERENCE_FROM_VENUE_RATES"
PIT_STATUS = "NOT_CERTIFIED"
DESIGN_LIMITS = ("PUBLISHED_BASELINE_RESULT_VALID_PRE_FILTERING_NOT_DI_N3_FIXED_COHORT",)
LIMITATIONS = (
    "RESEARCH_ONLY",
    "SYNTHETIC_CONNECTIVITY_SMOKE_NOT_A_PREDICTIVE_MODEL",
    "POISSON_REFERENCE_NOT_CALIBRATED",
    "CALLER_SUPPLIED_HISTORY_CONTEXT_AND_QUOTES",
    "CHRONOLOGY_CHECKED_FOR_SYNTHETIC_CONSISTENCY_NOT_INDEPENDENTLY_ACCREDITED",
    "HISTORICAL_SELECTION_LEAKAGE_AND_TEMPORALITY_NOT_CERTIFIED",
    "G2_N3_ACCEPTANCE_WAIT_CONTRACT",
    "NO_1X2_PROJECTION",
    "QUARTER_AND_SPLIT_LINES_UNSUPPORTED",
    "NOT_A_REAL_BETTING_OPPORTUNITY",
    "NO_STAKE_BANKROLL_PICK_OR_RECOMMENDATION",
    "NOT_CERTIFIED",
)


class PrematchSmokeInputError(ValueError):
    """Entradas mal formadas o incoherentes entre sí (identidad o cronología): error del llamador."""


@dataclass(frozen=True)
class PrematchValueSmoke:
    smoke_id: str
    smoke_version: str
    status: str  # VALUE_EVALUATED | NO_ESTIMATE | NO_VALUE | UNSUPPORTED
    reasons: tuple[str, ...]  # los del estimador (NO_ESTIMATE) o del puente, tal cual; vacío si y solo si VALUE_EVALUATED
    prediction_id: str
    fixture_id: int
    market: str
    selection: str
    line: str | None
    venue_rates: VenueGoalRates | None  # el del estimador, intacto; None si no se llegó a estimar (UNSUPPORTED)
    reference_cells: tuple[tuple, ...] | None  # las 64 celdas de la referencia, tal cual; solo con ESTIMATE
    market_probabilities: Mapping | None  # proyección publicada, solo lectura; solo con ESTIMATE
    selection_probabilities: SelectionProbabilities | None  # lo que recibe el puente; solo con ESTIMATE
    quote_value: QuoteValueResult | None  # el del puente, intacto; None si no se llamó
    components: tuple[str, ...]
    input_basis: str
    probability_basis: str
    pit_status: str
    design_limits: tuple[str, ...]
    limitations: tuple[str, ...]


_INT_MAX_BITS = 64  # un int mayor se describe por su signo y sus bits, nunca por sus dígitos
_STR_MAX_CHARS = 40
_TYPE_NAME_MAX_CHARS = 100
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DATETIME_FIELDS = tuple(getattr(datetime, n) for n in ("year", "month", "day", "hour", "minute", "second", "microsecond"))  # descriptores base


def _type_name(value) -> str:
    """Nombre del tipo, acotado y como str exacto: por el descriptor de type (una metaclase no interviene) y con
    str.__len__ / str.__getitem__ de str, así que un __name__ que sea un str derivado hostil no se ejecuta."""
    try:
        name = type.__dict__["__name__"].__get__(type(value))
        if not issubclass(type(name), str):
            return "?"
        text = str.__getitem__(name, slice(0, min(str.__len__(name), _TYPE_NAME_MAX_CHARS)))
        return text if type(text) is str else "?"
    except Exception:  # solo el diagnóstico; un BaseException se propaga
        return "?"


def _describe(value) -> str:
    """Descripción acotada de un valor rechazado, sin llamar a ninguno de sus métodos (ver el módulo)."""
    try:
        cls = type(value)
        if value is None or cls is bool:
            return "None" if value is None else ("True" if value is True else "False")
        if issubclass(cls, int):
            bits = int.bit_length(value)
            text = f"<int {'negativo ' if int.__lt__(value, 0) else ''}de {bits} bits>" if bits > _INT_MAX_BITS else int.__repr__(value)
        elif issubclass(cls, str):
            size = str.__len__(value)
            text = str.__repr__(str.__getitem__(value, slice(0, _STR_MAX_CHARS)))
            if size > _STR_MAX_CHARS:
                text = f"{text}... ({size} caracteres)"
        elif issubclass(cls, datetime):  # campos por los descriptores base; sin tzinfo (podría ejecutar código del llamador)
            y, mo, d, h, mi, s, us = (field.__get__(value) for field in _DATETIME_FIELDS)
            text = f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}.{us:06d}"
            return f"datetime {text}" if cls is datetime else f"<{_type_name(value)} {text}>"
        else:
            return f"<{_type_name(value)}>"
        return text if cls is int or cls is str else f"<{_type_name(value)} {text}>"
    except Exception:  # solo el diagnóstico; un BaseException se propaga
        return "<?>"


def _instant(value, what: str) -> datetime:
    """El instante en UTC como datetime exacto, leído con los métodos base de datetime (nunca los de una subclase)."""
    if not issubclass(type(value), datetime):
        raise PrematchSmokeInputError(f"{what}: se espera un datetime con zona horaria, no {_describe(value)}")
    try:
        offset = datetime.utcoffset(value)  # llama al tzinfo del llamador, que puede fallar (p. ej. 30 h)
        instant = None if offset is None else _EPOCH + datetime.__sub__(value, _EPOCH)
    except Exception:
        raise PrematchSmokeInputError(f"{what}: zona horaria o instante no válidos ({_describe(value)})") from None
    if instant is None:
        raise PrematchSmokeInputError(f"{what}: se espera un datetime con zona horaria, no {_describe(value)}")
    return instant


def _check(target, cutoff, context, quotes) -> None:
    if not isinstance(target, Mapping) or "fixture_id" not in target or "kickoff_at" not in target:
        raise PrematchSmokeInputError("target: se espera un Mapping con fixture_id y kickoff_at")
    if not isinstance(context, EvaluationContext) or not isinstance(context.target, QuoteTarget):
        raise PrematchSmokeInputError("context: se espera un EvaluationContext con un QuoteTarget")
    fixture, quoted = target["fixture_id"], context.target.fixture_id
    if type(fixture) is not int or type(quoted) is not int:
        raise PrematchSmokeInputError(f"fixture_id: se espera un int (no bool): {_describe(fixture)} / {_describe(quoted)}")
    if fixture != quoted:  # comparación exacta de int, sin pasar por texto
        raise PrematchSmokeInputError(f"fixture_id distinto entre target ({_describe(fixture)}) y context ({_describe(quoted)})")
    kickoff = _instant(target["kickoff_at"], "target.kickoff_at")
    if _instant(context.kickoff_at, "context.kickoff_at") != kickoff:
        raise PrematchSmokeInputError("target.kickoff_at y context.kickoff_at no son el mismo instante UTC")
    if not _instant(cutoff, "cutoff") <= _instant(context.issued_at, "context.issued_at") < kickoff:
        raise PrematchSmokeInputError("se exige cutoff <= issued_at < kickoff_at")
    if not isinstance(quotes, (list, tuple)) or not all(isinstance(q, QuoteObservation) for q in quotes):
        raise PrematchSmokeInputError("quotes: se espera una list o tuple de QuoteObservation")
    t = context.target
    if t.market in (BTTS, OVER_UNDER) and (not isinstance(t.selection, str) or t.selection not in MARKET_SELECTIONS[t.market]):
        market = BTTS if t.market == BTTS else OVER_UNDER  # la constante publicada, nunca el valor del llamador
        raise PrematchSmokeInputError(f"selección {_describe(t.selection)} no válida para {market}")
    if t.market == BTTS and t.line is not None:
        raise PrematchSmokeInputError("BTTS_FT90 no lleva línea")


def _read_only(projection: dict) -> Mapping:
    return MappingProxyType({
        "mass": projection["mass"],
        "over_under": MappingProxyType({line: MappingProxyType(dict(p)) for line, p in projection["over_under"].items()}),
        "btts": MappingProxyType(dict(projection["btts"])),
    })


def _selection(context: EvaluationContext, projection: dict) -> SelectionProbabilities:
    t = context.target
    p_lose = p_push = None
    if t.market == BTTS:
        p_win = projection["btts"][t.selection]
    else:
        line = projection["over_under"][t.line]
        p_win = line[t.selection]
        if over_under_line(t.line) % 1 == 0:  # línea entera: las tres masas, explícitas y sin inferir
            (other,) = (s for s in MARKET_SELECTIONS[OVER_UNDER] if s != t.selection)
            p_lose, p_push = line[other], line[PUSH]
    return SelectionProbabilities(prediction_id=context.prediction_id, fixture_id=t.fixture_id, market=t.market, selection=t.selection,
                                  line=t.line, p_win=p_win, p_lose=p_lose, p_push=p_push)


def _result(context, status, reasons, **parts) -> PrematchValueSmoke:
    t = context.target
    fields = {"venue_rates": None, "reference_cells": None, "market_probabilities": None, "selection_probabilities": None, "quote_value": None}
    fields.update(parts)
    return PrematchValueSmoke(
        smoke_id=SMOKE_ID, smoke_version=SMOKE_VERSION, status=status, reasons=tuple(reasons), prediction_id=context.prediction_id,
        fixture_id=t.fixture_id, market=t.market, selection=t.selection, line=t.line, components=COMPONENTS, input_basis=INPUT_BASIS,
        probability_basis=PROBABILITY_BASIS, pit_status=PIT_STATUS, design_limits=DESIGN_LIMITS, limitations=LIMITATIONS, **fields,
    )


def evaluate_prematch_value_smoke(*, target, history, cutoff, memberships, context, quotes) -> PrematchValueSmoke:
    """Encadena estimador, referencia Poisson, proyección y puente de valor sobre entradas sintéticas (ver el módulo)."""
    _check(target, cutoff, context, quotes)
    t = context.target
    if t.market == ONE_X_TWO:
        return _result(context, UNSUPPORTED, (ONE_X_TWO_PROJECTION_NOT_AUTHORIZED,))
    if not (t.market == BTTS or (t.market == OVER_UNDER and isinstance(t.line, str) and t.line in SUPPORTED_LINE_TEXTS)):
        quote_value = quote_value_bridge.evaluate_quote_value(context, quotes, None)  # semántica del objetivo: la decide el puente
        if quote_value.status != UNSUPPORTED:
            raise AssertionError(f"el puente no rechazó un objetivo fuera de la proyección: {quote_value.status}")
        return _result(context, UNSUPPORTED, quote_value.reasons, quote_value=quote_value)

    rates = baseline.estimate_venue_goal_rates(target, history, cutoff, memberships)
    if rates.status == NO_ESTIMATE:
        return _result(context, NO_ESTIMATE, rates.no_estimate_reasons, venue_rates=rates)
    if rates.status != ESTIMATE or rates.lambda_home is None or rates.lambda_away is None:
        raise AssertionError(f"estado del estimador no reconocido: {rates.status!r}")

    cells = goal_reference.independent_poisson_reference(rates.lambda_home, rates.lambda_away)
    projection = goal_projection.goals_market_probabilities(cells)
    probabilities = _selection(context, projection)
    quote_value = quote_value_bridge.evaluate_quote_value(context, quotes, probabilities)
    if quote_value.status not in (VALUE_EVALUATED, NO_VALUE):
        raise AssertionError(f"estado del puente inesperado para un objetivo soportado: {quote_value.status}")
    return _result(context, quote_value.status, quote_value.reasons, venue_rates=rates, reference_cells=tuple(cells),
                   market_probabilities=_read_only(projection), selection_probabilities=probabilities, quote_value=quote_value)
