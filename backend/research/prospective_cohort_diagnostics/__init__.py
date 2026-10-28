"""Diagnóstico de cobertura de una cohorte de fixtures declarada por el llamador (MOD-DEV-105A). Puro: sin BD, red, disco ni app.

La cohorte es CALLER_DECLARED: el llamador enumera los fixtures inscritos y este módulo nunca la deduce, amplía, recorta
ni sustituye a partir de predicciones, registros del emisor o etiquetas observadas. Estados y etiquetas son SYNTHETIC_ONLY
(los aporta el llamador). NOT_CERTIFIED: no es un preregistro auténtico, ni una certificación PIT, ni evidencia de un emisor
independiente, ni una decisión de admisión. No hay umbrales de publicación ni de elegibilidad, ni marcas de tiempo.

Interfaz:
  declare_cohort(enrolled_fixture_ids) -> DeclaredCohort
  evaluate_cohort(enrolled_fixture_ids, statuses=(), labeled_fixture_ids=()) -> CohortDiagnostics
Cualquier entrada inválida lanza CohortInputError con un .code de ERROR_CODES (sin resultado parcial).

Identidad de fixture: válida solo si type(id) is int e id > 0 (se rechazan bool, float, str, cero y negativos). Los
contenedores tienen que ser list o tuple: un dict o un set colapsan claves iguales (1 == True == 1.0) antes de que la API
pueda verlas, así que se rechazan en vez de deduplicar en silencio.
  enrolled_fixture_ids: list/tuple de ids, no vacía y sin repetidos (un repetido se rechaza, no se deduplica).
  statuses: list/tuple de pares (fixture_id, estado), cada estado de STATUSES; como mucho un par por fixture (un par
    repetido es STATUS_DUPLICATE y uno con otro estado STATUS_CONTRADICTORY). Un fixture inscrito sin par cuenta como
    NO_RECORD (implicit_no_record_count) y sigue en el denominador.
  labeled_fixture_ids: list/tuple de ids inscritos cuya etiqueta sintética se observó, sin repetidos.
Un estado o una etiqueta de un fixture fuera de la cohorte se rechaza (nunca se añade a la cohorte ni se ignora).

Vocabulario de estados (sintético, mínimo):
  INFORMATIVE  hay un resultado informativo notificable.
  DEGRADED     hay registro pero no es informativo (evidencia tardía o sin anclar, resultado degradado...).
  NOT_EMITTED  no se emitió predicción.
  NO_RECORD    no hay registro del emisor (también el valor implícito de un fixture sin estado).

Denominador: SIEMPRE enrolled_count, el número completo de fixtures inscritos. informative_synthetic_coverage =
informative_synthetic_count / enrolled_count y label_observability = label_observed_count / enrolled_count. Estado y
etiqueta son independientes: un fixture puede tener etiqueta sin ser INFORMATIVE y al revés.

cohort_digest: SHA-256 de una serialización canónica de los ids ordenados (COHORT_DIGEST_ALGORITHM). Solo sirve para
comparar localmente si dos cohortes declaradas tienen exactamente los mismos miembros: no prueba cuándo se inscribieron,
que la inscripción fuera anterior a T, su autenticidad ni ningún testigo externo.

Salida inmutable y determinista: los ids se ordenan de menor a mayor (sin cambiar los miembros), así que el resultado no
depende del orden de entrada; las entradas del llamador no se modifican.
"""

import hashlib
from dataclasses import dataclass

DIAGNOSTICS_ID = "PROSPECTIVE_COHORT_DIAGNOSTICS"
DIAGNOSTICS_VERSION = "1"

INFORMATIVE, DEGRADED, NOT_EMITTED, NO_RECORD = "INFORMATIVE", "DEGRADED", "NOT_EMITTED", "NO_RECORD"
STATUSES = (INFORMATIVE, DEGRADED, NOT_EMITTED, NO_RECORD)

COHORT_DIGEST_ALGORITHM = "SHA256_SORTED_POSITIVE_INT_HEX_V1"
COHORT_DIGEST_SCOPE = "LOCAL_EQUALITY_ONLY"
DENOMINATOR_BASIS = "FULL_CALLER_DECLARED_ENROLLED_COHORT"
COHORT_BASIS = "CALLER_DECLARED"
INPUT_BASIS = "SYNTHETIC_ONLY"
CERTIFICATION_STATUS = "NOT_CERTIFIED"
LIMITATIONS = (
    "COHORT_CALLER_DECLARED_NOT_AUTHENTIC_PREREGISTRATION",
    "DIGEST_LOCAL_EQUALITY_ONLY_NOT_PROOF_OF_ENROLLMENT_TIME_OR_AUTHENTICITY",
    "NO_PRE_T_IDENTITY_OR_EXTERNAL_WITNESS",
    "STATUSES_AND_LABELS_SYNTHETIC_CALLER_SUPPLIED",
    "NO_AUTHENTICATED_ISSUANCE_OR_RECEIPT_CHRONOLOGY",
    "NO_RELEASE_THRESHOLD_ELIGIBILITY_OR_ADMISSION_DECISION",
    "NOT_TRUE_PIT_CERTIFIED",
)
_DIGEST_DOMAIN = b"PREDIKTIA/RESEARCH/PROSPECTIVE_COHORT_DIAGNOSTICS/COHORT_IDENTITY/V1\n"

# Códigos de CohortInputError
COHORT_CONTAINER_INVALID = "COHORT_CONTAINER_INVALID"
COHORT_EMPTY = "COHORT_EMPTY"
FIXTURE_ID_INVALID = "FIXTURE_ID_INVALID"
FIXTURE_ID_DUPLICATE = "FIXTURE_ID_DUPLICATE"
STATUS_CONTAINER_INVALID = "STATUS_CONTAINER_INVALID"
STATUS_ENTRY_INVALID = "STATUS_ENTRY_INVALID"
STATUS_VALUE_UNSUPPORTED = "STATUS_VALUE_UNSUPPORTED"
STATUS_FIXTURE_UNKNOWN = "STATUS_FIXTURE_UNKNOWN"
STATUS_DUPLICATE = "STATUS_DUPLICATE"
STATUS_CONTRADICTORY = "STATUS_CONTRADICTORY"
LABEL_CONTAINER_INVALID = "LABEL_CONTAINER_INVALID"
LABEL_FIXTURE_UNKNOWN = "LABEL_FIXTURE_UNKNOWN"
LABEL_DUPLICATE = "LABEL_DUPLICATE"
ERROR_CODES = (COHORT_CONTAINER_INVALID, COHORT_EMPTY, FIXTURE_ID_INVALID, FIXTURE_ID_DUPLICATE, STATUS_CONTAINER_INVALID,
               STATUS_ENTRY_INVALID, STATUS_VALUE_UNSUPPORTED, STATUS_FIXTURE_UNKNOWN, STATUS_DUPLICATE, STATUS_CONTRADICTORY,
               LABEL_CONTAINER_INVALID, LABEL_FIXTURE_UNKNOWN, LABEL_DUPLICATE)


class CohortInputError(ValueError):
    """Entrada inválida: no hay diagnóstico parcial. .code es uno de ERROR_CODES."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class DeclaredCohort:
    fixture_ids: tuple[int, ...]  # los inscritos, de menor a mayor
    enrolled_count: int
    cohort_digest: str  # hex SHA-256; solo igualdad local
    digest_algorithm: str
    digest_scope: str
    cohort_basis: str
    certification_status: str


@dataclass(frozen=True)
class FixtureDiagnostic:
    fixture_id: int
    status: str  # uno de STATUSES
    status_supplied: bool  # False si NO_RECORD implícito
    label_observed: bool


@dataclass(frozen=True)
class CohortDiagnostics:
    diagnostics_id: str
    diagnostics_version: str
    cohort: DeclaredCohort
    enrolled_count: int  # el denominador de ambas proporciones
    denominator_basis: str
    fixtures: tuple[FixtureDiagnostic, ...]  # un elemento por fixture inscrito, en el orden de cohort.fixture_ids
    status_counts: tuple[tuple[str, int], ...]  # todos los STATUSES en su orden, también los de recuento 0
    supplied_status_count: int
    implicit_no_record_count: int
    informative_synthetic_count: int
    informative_synthetic_coverage: float  # informative_synthetic_count / enrolled_count
    label_observed_count: int
    label_observability: float  # label_observed_count / enrolled_count
    cohort_basis: str
    input_basis: str
    certification_status: str
    limitations: tuple[str, ...]


def _is_fixture_id(value) -> bool:
    return type(value) is int and value > 0


def _require_sequence(value, code: str, what: str):
    if type(value) not in (list, tuple):
        raise CohortInputError(code, f"{what} tiene que ser list o tuple, no {type(value).__name__}")


def _cohort_digest(fixture_ids: tuple[int, ...]) -> str:
    # Hexadecimal: sin el límite de dígitos de str(int) y sin ambigüedad (cada id en minúsculas sin ceros a la izquierda)
    body = ",".join(format(f, "x") for f in fixture_ids).encode("ascii")
    return hashlib.sha256(_DIGEST_DOMAIN + body).hexdigest()


def declare_cohort(enrolled_fixture_ids) -> DeclaredCohort:
    """Valida y normaliza la cohorte declarada por el llamador (ver el módulo)."""
    _require_sequence(enrolled_fixture_ids, COHORT_CONTAINER_INVALID, "enrolled_fixture_ids")
    if not enrolled_fixture_ids:
        raise CohortInputError(COHORT_EMPTY, "la cohorte declarada no tiene fixtures")
    seen = set()
    for f in enrolled_fixture_ids:
        if not _is_fixture_id(f):
            raise CohortInputError(FIXTURE_ID_INVALID, f"fixture inscrito inválido: {f!r}")
        if f in seen:
            raise CohortInputError(FIXTURE_ID_DUPLICATE, f"fixture inscrito repetido: {f}")
        seen.add(f)
    fixture_ids = tuple(sorted(seen))
    return DeclaredCohort(fixture_ids=fixture_ids, enrolled_count=len(fixture_ids), cohort_digest=_cohort_digest(fixture_ids),
                          digest_algorithm=COHORT_DIGEST_ALGORITHM, digest_scope=COHORT_DIGEST_SCOPE, cohort_basis=COHORT_BASIS,
                          certification_status=CERTIFICATION_STATUS)


def _statuses_by_fixture(statuses, enrolled: frozenset) -> dict[int, str]:
    _require_sequence(statuses, STATUS_CONTAINER_INVALID, "statuses")
    found: dict[int, str] = {}
    for entry in statuses:
        if type(entry) is not tuple or len(entry) != 2:
            raise CohortInputError(STATUS_ENTRY_INVALID, f"cada estado es un par (fixture_id, estado), no {entry!r}")
        f, status = entry
        if not _is_fixture_id(f):
            raise CohortInputError(FIXTURE_ID_INVALID, f"fixture de estado inválido: {f!r}")
        if type(status) is not str or status not in STATUSES:
            raise CohortInputError(STATUS_VALUE_UNSUPPORTED, f"estado no soportado para el fixture {f}: {status!r}")
        if f not in enrolled:
            raise CohortInputError(STATUS_FIXTURE_UNKNOWN, f"estado de un fixture fuera de la cohorte: {f}")
        if f in found:
            code = STATUS_DUPLICATE if found[f] == status else STATUS_CONTRADICTORY
            raise CohortInputError(code, f"más de un estado para el fixture {f}: {found[f]} y {status}")
        found[f] = status
    return found


def _labeled_fixtures(labeled_fixture_ids, enrolled: frozenset) -> frozenset:
    _require_sequence(labeled_fixture_ids, LABEL_CONTAINER_INVALID, "labeled_fixture_ids")
    seen = set()
    for f in labeled_fixture_ids:
        if not _is_fixture_id(f):
            raise CohortInputError(FIXTURE_ID_INVALID, f"fixture de etiqueta inválido: {f!r}")
        if f not in enrolled:
            raise CohortInputError(LABEL_FIXTURE_UNKNOWN, f"etiqueta de un fixture fuera de la cohorte: {f}")
        if f in seen:
            raise CohortInputError(LABEL_DUPLICATE, f"etiqueta repetida para el fixture {f}")
        seen.add(f)
    return frozenset(seen)


def evaluate_cohort(enrolled_fixture_ids, statuses=(), labeled_fixture_ids=()) -> CohortDiagnostics:
    """Cobertura informativa y observabilidad de etiquetas sobre la cohorte declarada completa (ver el módulo)."""
    cohort = declare_cohort(enrolled_fixture_ids)
    enrolled = frozenset(cohort.fixture_ids)
    supplied = _statuses_by_fixture(statuses, enrolled)
    labeled = _labeled_fixtures(labeled_fixture_ids, enrolled)

    fixtures = tuple(FixtureDiagnostic(fixture_id=f, status=supplied.get(f, NO_RECORD), status_supplied=f in supplied,
                                       label_observed=f in labeled) for f in cohort.fixture_ids)
    n = cohort.enrolled_count
    informative = sum(1 for d in fixtures if d.status == INFORMATIVE)
    return CohortDiagnostics(
        diagnostics_id=DIAGNOSTICS_ID, diagnostics_version=DIAGNOSTICS_VERSION, cohort=cohort, enrolled_count=n,
        denominator_basis=DENOMINATOR_BASIS, fixtures=fixtures,
        status_counts=tuple((s, sum(1 for d in fixtures if d.status == s)) for s in STATUSES),
        supplied_status_count=len(supplied), implicit_no_record_count=n - len(supplied),
        informative_synthetic_count=informative, informative_synthetic_coverage=informative / n,
        label_observed_count=len(labeled), label_observability=len(labeled) / n,
        cohort_basis=COHORT_BASIS, input_basis=INPUT_BASIS, certification_status=CERTIFICATION_STATUS, limitations=LIMITATIONS,
    )
