"""Elegibilidad temporal de cuotas sintéticas para una predicción emitida (MOD-DEV-102C). Puro: sin BD, red, disco ni app.

Todo lo que entra es SINTÉTICO y lo aporta el llamador: no está verificado ni autenticado por ningún
proveedor, no se lee de provider_mappings ni de fixture_observations y no está certificado PIT. Que una
cuota salga ELIGIBLE solo dice que la evidencia suministrada cumple este contrato, no que Prediktia la
conociera de verdad en ese momento. No calcula EV, cuota justa, CLV, ROI, picks ni recomendaciones.

Interfaz:
  evaluate_quote_eligibility(context, quotes) -> EligibilityReport
context es un EvaluationContext (predicción emitida, kickoff conocido, política de antigüedad máxima y
la cuota objetivo QuoteTarget); quotes, una list o tuple de QuoteObservation (nunca un iterable de un
solo uso). Un contexto o una colección mal formados lanzan EligibilityInputError (sin resultado
parcial). Cada cuota recibe un QuoteEligibility, en el orden de entrada: ELIGIBLE sin motivos, o
INELIGIBLE con todos sus motivos en el orden fijo de REASONS. Ninguna cuota se descarta en silencio.

Tiempos: todos con zona horaria; se comparan y se informan en UTC. No hay timestamps por defecto ni
reloj implícito. El conocimiento lo fija received_at (cuándo recibió Prediktia la cuota, suministrado
por el llamador): ELIGIBLE exige received_at <= issued_at < kickoff_at e issued_at - received_at <=
max_quote_age. provider_quoted_at / provider_updated_at son afirmaciones del proveedor, opcionales:
nunca adelantan el conocimiento ni intervienen en la antigüedad; solo se comprueba que no contradigan
la recepción (posteriores a received_at) ni entre sí (actualización anterior a la cotización).

Identidad: una evaluación es de UNA cuota objetivo (proveedor, bookmaker, evento del proveedor,
fixture, mercado, selección y línea): nunca se combinan bookmakers, eventos, mercados, selecciones ni
líneas distintos. El fixture sale de la procedencia del mapeo (MappingProvenance), que tiene que
coincidir con el proveedor y el evento de la cuota y usar un método aprobado (no auto_fuzzy).
Mercados FT90 (marcador a 90'; convención FT90_V1, sin prórroga ni penaltis):
  1X2_FT90 HOME/DRAW/AWAY y BTTS_FT90 YES/NO sin línea; OVER_UNDER_FT90 OVER/UNDER con una de las 13
  líneas aprobadas como texto canónico ("2.5"; las de research.m58.goal_labels).
Precio: Decimal finito > 1 (ni float, ni int, ni str); se compara por su valor exacto (2.10 == 2.1).
Estado: solo OPEN es admisible; SUSPENDED y CLOSED se rechazan y cualquier otro valor es desconocido.

Igualdad y duplicados: la clave de una cuota es (proveedor, bookmaker, evento, mercado, selección,
línea, received_at en UTC); su contenido, (precio, estado, convención, timestamps del proveedor,
mapeo). Dos cuotas con la misma clave y el mismo contenido son equivalentes (da igual su evidence_id) y
se evalúan igual. Con la misma clave y contenido distinto, TODAS reciben QUOTE_TEMPORAL_AMBIGUITY:
nunca se elige una por orden ni por valor. Un campo de contenido no válido cuenta como distinto de
cualquier valor válido (fail-closed). La clave solo exige proveedor, bookmaker, evento, mercado,
selección, línea y received_at válidos: un evidence_id no válido no oculta el conflicto (MOD-DEV-102C-R1).

Evidencia (MOD-DEV-102C-R1): un evidence_id válido es una respuesta lógica y puede llevar varias cuotas
(otros bookmakers, eventos, mercados o selecciones). Si aparece en más de una observación, todas tienen
que probar la misma respuesta: un único proveedor válido, un único received_at válido, un solo
contenido por clave y un solo mapeo por evento del proveedor. Si no (también si en alguna falta, es
naive o no se puede normalizar received_at), TODAS las observaciones de ese evidence_id reciben
EVIDENCE_ID_INCONSISTENT; nunca se elige una ganadora. Un evidence_id no válido no enlaza nada: solo da
IDENTITY_INVALID a su propia observación. El resultado no depende del orden de entrada.

Procedencia: INPUT_BASIS y LIMITATIONS lo dejan explícito; PIT_STATUS = NOT_CERTIFIED.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

EVALUATOR_ID = "QUOTE_TEMPORAL_ELIGIBILITY"
EVALUATOR_VERSION = "1"
SETTLEMENT_CONVENTION_FT90 = "FT90_V1"

ONE_X_TWO, OVER_UNDER, BTTS = "1X2_FT90", "OVER_UNDER_FT90", "BTTS_FT90"
MARKET_SELECTIONS = {ONE_X_TWO: ("HOME", "DRAW", "AWAY"), OVER_UNDER: ("OVER", "UNDER"), BTTS: ("YES", "NO")}
SUPPORTED_LINE_TEXTS = ("0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0", "5.5", "6.0", "6.5")
APPROVED_MAPPING_METHODS = ("origin", "manual", "auto_exact")  # los de provider_mapping.MATCH_METHODS sin auto_fuzzy
OPEN, SUSPENDED, CLOSED = "OPEN", "SUSPENDED", "CLOSED"
ELIGIBLE, INELIGIBLE = "ELIGIBLE", "INELIGIBLE"

# Motivos de rechazo, en el orden en que se informan
IDENTITY_INVALID = "IDENTITY_INVALID"
PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
BOOKMAKER_MISMATCH = "BOOKMAKER_MISMATCH"
EVENT_MISMATCH = "EVENT_MISMATCH"
MAPPING_PROVENANCE_MISSING = "MAPPING_PROVENANCE_MISSING"
MAPPING_PROVENANCE_INVALID = "MAPPING_PROVENANCE_INVALID"
MAPPING_INCONSISTENT = "MAPPING_INCONSISTENT"
MAPPING_METHOD_NOT_APPROVED = "MAPPING_METHOD_NOT_APPROVED"
FIXTURE_MISMATCH = "FIXTURE_MISMATCH"
MARKET_UNSUPPORTED = "MARKET_UNSUPPORTED"
SELECTION_INVALID = "SELECTION_INVALID"
LINE_INVALID = "LINE_INVALID"
MARKET_MISMATCH = "MARKET_MISMATCH"
SELECTION_MISMATCH = "SELECTION_MISMATCH"
LINE_MISMATCH = "LINE_MISMATCH"
SETTLEMENT_CONVENTION_UNSUPPORTED = "SETTLEMENT_CONVENTION_UNSUPPORTED"
QUOTE_STATUS_UNKNOWN = "QUOTE_STATUS_UNKNOWN"
QUOTE_STATUS_NOT_ADMISSIBLE = "QUOTE_STATUS_NOT_ADMISSIBLE"
PRICE_INVALID = "PRICE_INVALID"
RECEIVED_AT_INVALID = "RECEIVED_AT_INVALID"
PROVIDER_TIMESTAMP_INVALID = "PROVIDER_TIMESTAMP_INVALID"
PROVIDER_TIMESTAMP_AFTER_RECEIPT = "PROVIDER_TIMESTAMP_AFTER_RECEIPT"
PROVIDER_TIMESTAMPS_INCONSISTENT = "PROVIDER_TIMESTAMPS_INCONSISTENT"
ISSUED_AT_NOT_BEFORE_KICKOFF = "ISSUED_AT_NOT_BEFORE_KICKOFF"
QUOTE_NOT_KNOWN_AT_ISSUE = "QUOTE_NOT_KNOWN_AT_ISSUE"
QUOTE_STALE = "QUOTE_STALE"
EVIDENCE_ID_INCONSISTENT = "EVIDENCE_ID_INCONSISTENT"
QUOTE_TEMPORAL_AMBIGUITY = "QUOTE_TEMPORAL_AMBIGUITY"
REASONS = (
    IDENTITY_INVALID, PROVIDER_MISMATCH, BOOKMAKER_MISMATCH, EVENT_MISMATCH, MAPPING_PROVENANCE_MISSING,
    MAPPING_PROVENANCE_INVALID, MAPPING_INCONSISTENT, MAPPING_METHOD_NOT_APPROVED, FIXTURE_MISMATCH, MARKET_UNSUPPORTED,
    SELECTION_INVALID, LINE_INVALID, MARKET_MISMATCH, SELECTION_MISMATCH, LINE_MISMATCH, SETTLEMENT_CONVENTION_UNSUPPORTED,
    QUOTE_STATUS_UNKNOWN, QUOTE_STATUS_NOT_ADMISSIBLE, PRICE_INVALID, RECEIVED_AT_INVALID, PROVIDER_TIMESTAMP_INVALID,
    PROVIDER_TIMESTAMP_AFTER_RECEIPT, PROVIDER_TIMESTAMPS_INCONSISTENT, ISSUED_AT_NOT_BEFORE_KICKOFF,
    QUOTE_NOT_KNOWN_AT_ISSUE, QUOTE_STALE, EVIDENCE_ID_INCONSISTENT, QUOTE_TEMPORAL_AMBIGUITY,
)

INPUT_BASIS = "SYNTHETIC_CALLER_SUPPLIED_QUOTE_EVIDENCE"
PIT_STATUS = "NOT_CERTIFIED"
LIMITATIONS = (
    "SYNTHETIC_INPUT_NOT_PROVIDER_VERIFIED_OR_AUTHENTICATED",
    "RECEIVED_AT_CALLER_SUPPLIED_NOT_PROVEN_KNOWLEDGE_TIME",
    "PROVIDER_TIMESTAMPS_ARE_UNVERIFIED_CLAIMS",
    "MAPPING_PROVENANCE_CALLER_SUPPLIED_NOT_READ_FROM_PROVIDER_MAPPINGS",
    "NO_SELECTION_AMONG_ELIGIBLE_QUOTES_AT_DIFFERENT_TIMES",
    "NO_EV_FAIR_ODDS_CLV_ROI_OR_RECOMMENDATION",
    "NOT_TRUE_PIT_CERTIFIED",
)

_INVALID = object()  # contenido no válido: distinto de cualquier valor válido


class EligibilityInputError(ValueError):
    """El contexto o la colección de cuotas no tienen la forma pedida (error del llamador)."""


@dataclass(frozen=True)
class MappingProvenance:
    """Enlace documentado evento del proveedor -> fixture, tal como lo aporta el llamador."""

    mapping_ref: str
    method: str  # origin | manual | auto_exact
    provider: str
    provider_event_id: str
    fixture_id: int


@dataclass(frozen=True)
class QuoteTarget:
    """La única cuota a la que se refiere la predicción: nunca se mezclan otras."""

    provider: str
    bookmaker_id: str
    provider_event_id: str
    fixture_id: int
    market: str
    selection: str
    line: str | None


@dataclass(frozen=True)
class EvaluationContext:
    prediction_id: str
    issued_at: datetime  # emisión inmutable de la predicción
    kickoff_at: datetime  # kickoff conocido del fixture
    max_quote_age: timedelta  # política explícita del llamador: issued_at - received_at <= max_quote_age
    target: QuoteTarget


@dataclass(frozen=True)
class QuoteObservation:
    evidence_id: str  # respuesta lógica en la que llegó la cuota
    provider: str
    bookmaker_id: str
    provider_event_id: str
    mapping: MappingProvenance | None
    market: str
    selection: str
    line: str | None
    settlement_convention: str
    decimal_odds: Decimal
    quote_status: str
    provider_quoted_at: datetime | None  # afirmación del proveedor; nunca es conocimiento
    provider_updated_at: datetime | None  # afirmación del proveedor; nunca es conocimiento
    received_at: datetime  # cuándo la recibió Prediktia (suministrado por el llamador)


@dataclass(frozen=True)
class QualifiedQuote:
    evidence_id: str
    provider: str
    bookmaker_id: str
    provider_event_id: str
    fixture_id: int
    mapping_ref: str
    market: str
    selection: str
    line: str | None
    decimal_odds: Decimal
    received_at: datetime  # UTC


@dataclass(frozen=True)
class QuoteEligibility:
    position: int  # índice de la cuota en la entrada
    status: str  # ELIGIBLE | INELIGIBLE
    reasons: tuple[str, ...]  # vacío si y solo si ELIGIBLE
    qualified: QualifiedQuote | None  # solo en ELIGIBLE


@dataclass(frozen=True)
class EligibilityReport:
    evaluator_id: str
    evaluator_version: str
    settlement_convention: str
    prediction_id: str
    target: QuoteTarget
    issued_at: datetime  # UTC
    kickoff_at: datetime  # UTC
    max_quote_age: timedelta
    outcomes: tuple[QuoteEligibility, ...]  # en el orden de entrada
    input_basis: str
    pit_status: str
    limitations: tuple[str, ...]

    @property
    def eligible(self) -> tuple[QualifiedQuote, ...]:
        return tuple(o.qualified for o in self.outcomes if o.status == ELIGIBLE)


def _text(value) -> bool:
    return isinstance(value, str) and value != "" and value == value.strip()


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _utc(value) -> datetime | None:
    """El instante en UTC, o None si no es un datetime con zona horaria que se pueda pasar a UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        return None if value.utcoffset() is None else value.astimezone(timezone.utc)
    except (ArithmeticError, TypeError, ValueError):  # desplazamiento fuera de rango o desbordamiento al pasar a UTC
        return None


def _price(value) -> Decimal | None:
    return value if isinstance(value, Decimal) and value.is_finite() and value > 1 else None


def _shape_reasons(market, selection, line) -> list[str]:
    """Mercado FT90 aprobado, selección del mercado y línea (solo en Over/Under, texto canónico)."""
    if not isinstance(market, str) or market not in MARKET_SELECTIONS:
        return [MARKET_UNSUPPORTED]
    reasons = []
    if not isinstance(selection, str) or selection not in MARKET_SELECTIONS[market]:
        reasons.append(SELECTION_INVALID)
    if not (isinstance(line, str) and line in SUPPORTED_LINE_TEXTS if market == OVER_UNDER else line is None):
        reasons.append(LINE_INVALID)
    return reasons


def _canonical(value):
    """Valor de contenido comparable y hashable; lo no válido es _INVALID."""
    if value is None or isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    return _INVALID


def _check_context(context) -> None:
    if not isinstance(context, EvaluationContext):
        raise EligibilityInputError("context tiene que ser un EvaluationContext")
    if not _text(context.prediction_id):
        raise EligibilityInputError("prediction_id tiene que ser un texto no vacío y sin espacios en los extremos")
    if _utc(context.issued_at) is None or _utc(context.kickoff_at) is None:
        raise EligibilityInputError("issued_at y kickoff_at tienen que ser datetime con zona horaria")
    age = context.max_quote_age
    if not isinstance(age, timedelta) or age <= timedelta(0):
        raise EligibilityInputError("max_quote_age tiene que ser un timedelta positivo")
    t = context.target
    if not isinstance(t, QuoteTarget):
        raise EligibilityInputError("target tiene que ser un QuoteTarget")
    if not all(_text(v) for v in (t.provider, t.bookmaker_id, t.provider_event_id)) or not _positive_int(t.fixture_id):
        raise EligibilityInputError("target: identidad no válida")
    if _shape_reasons(t.market, t.selection, t.line):
        raise EligibilityInputError("target: mercado, selección o línea FT90 no aprobados")


def _check_quote(q: QuoteObservation, ctx: EvaluationContext, issued: datetime):
    """(motivos propios de la cuota, received_at en UTC o None, clave o None, contenido)."""
    t, reasons = ctx.target, []
    identity_ok = all(_text(v) for v in (q.evidence_id, q.provider, q.bookmaker_id, q.provider_event_id))
    if not identity_ok:
        reasons.append(IDENTITY_INVALID)
    for value, expected, reason in ((q.provider, t.provider, PROVIDER_MISMATCH), (q.bookmaker_id, t.bookmaker_id, BOOKMAKER_MISMATCH),
                                    (q.provider_event_id, t.provider_event_id, EVENT_MISMATCH)):
        if _text(value) and value != expected:
            reasons.append(reason)

    m = q.mapping
    if m is None:
        reasons.append(MAPPING_PROVENANCE_MISSING)
    elif not isinstance(m, MappingProvenance) or not all(_text(v) for v in (m.mapping_ref, m.method, m.provider, m.provider_event_id)) \
            or not _positive_int(m.fixture_id):
        reasons.append(MAPPING_PROVENANCE_INVALID)
    else:
        if m.provider != q.provider or m.provider_event_id != q.provider_event_id:
            reasons.append(MAPPING_INCONSISTENT)
        if m.method not in APPROVED_MAPPING_METHODS:
            reasons.append(MAPPING_METHOD_NOT_APPROVED)
        if m.fixture_id != t.fixture_id:
            reasons.append(FIXTURE_MISMATCH)

    shape = _shape_reasons(q.market, q.selection, q.line)
    reasons += shape
    if not shape:
        for value, expected, reason in ((q.market, t.market, MARKET_MISMATCH), (q.selection, t.selection, SELECTION_MISMATCH),
                                        (q.line, t.line, LINE_MISMATCH)):
            if value != expected:
                reasons.append(reason)
    if q.settlement_convention != SETTLEMENT_CONVENTION_FT90:
        reasons.append(SETTLEMENT_CONVENTION_UNSUPPORTED)
    if q.quote_status in (SUSPENDED, CLOSED):
        reasons.append(QUOTE_STATUS_NOT_ADMISSIBLE)
    elif q.quote_status != OPEN:
        reasons.append(QUOTE_STATUS_UNKNOWN)
    price = _price(q.decimal_odds)
    if price is None:
        reasons.append(PRICE_INVALID)

    received = _utc(q.received_at)
    if received is None:
        reasons.append(RECEIVED_AT_INVALID)
    claims = []
    for claim in (q.provider_quoted_at, q.provider_updated_at):
        utc = _utc(claim)
        if claim is not None and utc is None:
            reasons.append(PROVIDER_TIMESTAMP_INVALID)
        claims.append(_INVALID if claim is not None and utc is None else utc)
    quoted, updated = claims
    if received is not None and any(isinstance(c, datetime) and c > received for c in claims):
        reasons.append(PROVIDER_TIMESTAMP_AFTER_RECEIPT)
    if isinstance(quoted, datetime) and isinstance(updated, datetime) and updated < quoted:
        reasons.append(PROVIDER_TIMESTAMPS_INCONSISTENT)
    if received is not None:  # el conocimiento es received_at, nunca la hora que afirma el proveedor
        if received > issued:
            reasons.append(QUOTE_NOT_KNOWN_AT_ISSUE)
        elif issued - received > ctx.max_quote_age:
            reasons.append(QUOTE_STALE)

    key = None  # sin el evidence_id: uno no válido no puede ocultar un conflicto de precio (MOD-DEV-102C-R1)
    if all(_text(v) for v in (q.provider, q.bookmaker_id, q.provider_event_id)) and not shape and received is not None:
        key = (q.provider, q.bookmaker_id, q.provider_event_id, q.market, q.selection, q.line, received)
    mapping = _INVALID if m is not None and not isinstance(m, MappingProvenance) else (
        None if m is None else tuple(_canonical(v) for v in (m.mapping_ref, m.method, m.provider, m.provider_event_id, m.fixture_id)))
    content = (_INVALID if price is None else price, _canonical(q.quote_status), _canonical(q.settlement_convention), quoted, updated, mapping)
    return reasons, received, key, content


def _consistent_evidence(group) -> bool:
    """Las observaciones de un mismo evidence_id prueban una sola respuesta: un proveedor y un received_at
    válidos y únicos, un contenido por clave y un mapeo por evento del proveedor (MOD-DEV-102C-R1)."""
    if any(received is None or not _text(q.provider) for q, received, _, _ in group):
        return False  # coherencia no probada
    if len({q.provider for q, *_ in group}) > 1 or len({received for _, received, _, _ in group}) > 1:
        return False
    contents, mappings = defaultdict(set), defaultdict(set)
    for q, _, key, content in group:
        if key is not None:
            contents[key].add(content)
        if _text(q.provider_event_id):
            mappings[q.provider_event_id].add(content[-1])
    return all(len(values) == 1 for values in (*contents.values(), *mappings.values()))


def evaluate_quote_eligibility(context: EvaluationContext, quotes) -> EligibilityReport:
    """Clasifica cada cuota sintética como ELIGIBLE o INELIGIBLE para la predicción del contexto (ver el módulo)."""
    _check_context(context)
    if not isinstance(quotes, (list, tuple)):
        raise EligibilityInputError("quotes tiene que ser una list o tuple (no un iterable de un solo uso)")
    if not all(isinstance(q, QuoteObservation) for q in quotes):
        raise EligibilityInputError("cada cuota tiene que ser un QuoteObservation")
    issued, kickoff = _utc(context.issued_at), _utc(context.kickoff_at)
    checked = [_check_quote(q, context, issued) for q in quotes]

    by_evidence, contents_by_key = defaultdict(list), defaultdict(set)
    for q, (_, received, key, content) in zip(quotes, checked):
        if _text(q.evidence_id):  # un evidence_id no válido no enlaza observaciones
            by_evidence[q.evidence_id].append((q, received, key, content))
        if key is not None:
            contents_by_key[key].add(content)
    inconsistent = {e for e, group in by_evidence.items() if len(group) > 1 and not _consistent_evidence(group)}

    outcomes = []
    for position, (q, (reasons, received, key, _)) in enumerate(zip(quotes, checked)):
        found = set(reasons)
        if issued >= kickoff:
            found.add(ISSUED_AT_NOT_BEFORE_KICKOFF)
        if _text(q.evidence_id) and q.evidence_id in inconsistent:
            found.add(EVIDENCE_ID_INCONSISTENT)
        if key is not None and len(contents_by_key[key]) > 1:
            found.add(QUOTE_TEMPORAL_AMBIGUITY)
        ordered = tuple(r for r in REASONS if r in found)
        qualified = None if ordered else QualifiedQuote(
            evidence_id=q.evidence_id, provider=q.provider, bookmaker_id=q.bookmaker_id, provider_event_id=q.provider_event_id,
            fixture_id=q.mapping.fixture_id, mapping_ref=q.mapping.mapping_ref, market=q.market, selection=q.selection, line=q.line,
            decimal_odds=q.decimal_odds, received_at=received,
        )
        outcomes.append(QuoteEligibility(position, INELIGIBLE if ordered else ELIGIBLE, ordered, qualified))

    return EligibilityReport(
        evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION, settlement_convention=SETTLEMENT_CONVENTION_FT90,
        prediction_id=context.prediction_id, target=context.target, issued_at=issued, kickoff_at=kickoff,
        max_quote_age=context.max_quote_age, outcomes=tuple(outcomes), input_basis=INPUT_BASIS, pit_status=PIT_STATUS,
        limitations=LIMITATIONS,
    )
