"""MOD-DEV-105A: cobertura y observabilidad sobre una cohorte declarada por el llamador. Sin BD, sin red, sin app.
    python -m pytest research/tests/test_prospective_cohort_diagnostics.py -q   (desde backend/)
Cohortes, estados y etiquetas sintéticos: ninguno es un preregistro auténtico, un registro del emisor ni un resultado real."""

import copy
import dataclasses
import hashlib
import itertools
import os
import subprocess
import sys

import pytest

import research.prospective_cohort_diagnostics as cohort_diagnostics
from research.prospective_cohort_diagnostics import (CERTIFICATION_STATUS, COHORT_BASIS, COHORT_CONTAINER_INVALID, COHORT_DIGEST_SCOPE,
                                                     COHORT_EMPTY, DEGRADED, DENOMINATOR_BASIS, ERROR_CODES, FIXTURE_ID_DUPLICATE,
                                                     FIXTURE_ID_INVALID, INFORMATIVE, INPUT_BASIS, LABEL_CONTAINER_INVALID,
                                                     LABEL_DUPLICATE, LABEL_FIXTURE_UNKNOWN, LIMITATIONS, NO_RECORD, NOT_EMITTED,
                                                     STATUS_CONTAINER_INVALID, STATUS_CONTRADICTORY, STATUS_DUPLICATE,
                                                     STATUS_ENTRY_INVALID, STATUS_FIXTURE_UNKNOWN, STATUS_VALUE_UNSUPPORTED, STATUSES,
                                                     CohortDiagnostics, CohortInputError, DeclaredCohort, FixtureDiagnostic,
                                                     declare_cohort, evaluate_cohort)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEN = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
SIX = [101, 102, 103, 104, 105, 106]
# 101-106 informativos y etiquetados; 107 DEGRADED, 108 NOT_EMITTED, 109 NO_RECORD explícito, 110 sin estado
TEN_STATUSES = [(101, INFORMATIVE), (102, INFORMATIVE), (103, INFORMATIVE), (104, INFORMATIVE), (105, INFORMATIVE),
                (106, INFORMATIVE), (107, DEGRADED), (108, NOT_EMITTED), (109, NO_RECORD)]
SIX_LABELS = [101, 102, 103, 104, 105, 106]


def raises(code, fn, *args, **kwargs):
    with pytest.raises(CohortInputError) as info:
        fn(*args, **kwargs)
    assert info.value.code == code
    assert isinstance(info.value, ValueError)
    return info.value


# A. Diez inscritos, seis informativos, seis etiquetas: 0.6 y 0.6, nunca 1.0

def test_a_ten_enrolled_six_informative_six_labelled_keeps_full_denominator():
    d = evaluate_cohort(TEN, TEN_STATUSES, SIX_LABELS)
    assert isinstance(d, CohortDiagnostics)
    assert d.enrolled_count == 10 and d.cohort.enrolled_count == 10
    assert d.informative_synthetic_count == 6
    assert d.informative_synthetic_coverage == 0.6
    assert d.label_observed_count == 6
    assert d.label_observability == 0.6
    assert d.informative_synthetic_coverage != 1.0 and d.label_observability != 1.0
    assert d.denominator_basis == DENOMINATOR_BASIS == "FULL_CALLER_DECLARED_ENROLLED_COHORT"
    assert dict(d.status_counts) == {INFORMATIVE: 6, DEGRADED: 1, NOT_EMITTED: 1, NO_RECORD: 2}
    assert d.supplied_status_count == 9 and d.implicit_no_record_count == 1


# B. Una cohorte de seis distinta puede dar 6/6, pero con otra identidad

def test_b_distinct_six_fixture_cohort_may_be_complete_but_has_different_identity():
    ten = evaluate_cohort(TEN, TEN_STATUSES, SIX_LABELS)
    six = evaluate_cohort(SIX, [(f, INFORMATIVE) for f in SIX], SIX_LABELS)
    assert (six.enrolled_count, six.informative_synthetic_coverage, six.label_observability) == (6, 1.0, 1.0)
    assert six.cohort.cohort_digest != ten.cohort.cohort_digest
    assert six.cohort.fixture_ids != ten.cohort.fixture_ids
    assert six.cohort != ten.cohort


# C-F. Identidad de fixture estricta

def test_c_duplicate_fixture_ids_rejected_not_deduplicated():
    raises(FIXTURE_ID_DUPLICATE, declare_cohort, [1, 1, 2])
    raises(FIXTURE_ID_DUPLICATE, evaluate_cohort, [1, 1, 2])
    raises(FIXTURE_ID_DUPLICATE, evaluate_cohort, (2, 1, 2), [(1, INFORMATIVE)])


@pytest.mark.parametrize("bad", [True, False, 1.0, 2.0])
def test_d_bool_and_float_fixture_ids_rejected(bad):
    raises(FIXTURE_ID_INVALID, declare_cohort, [bad])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [3, bad])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [1, 3], [(bad, INFORMATIVE)])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [1, 3], (), [bad])


def test_d_true_next_to_one_is_rejected_not_collapsed():
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [1, True])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [1, 2], [(1, INFORMATIVE), (True, DEGRADED)])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [1, 2], (), [1, True])


@pytest.mark.parametrize("bad", [0, -1, -101, "101", "1", b"1", None, 1 + 0j])
def test_e_zero_negative_string_and_other_fixture_ids_rejected(bad):
    raises(FIXTURE_ID_INVALID, declare_cohort, [bad])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [101, bad])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [101], [(bad, INFORMATIVE)])
    raises(FIXTURE_ID_INVALID, evaluate_cohort, [101], (), [bad])


def test_e_int_subclass_fixture_id_rejected():
    class FixtureId(int):
        pass

    raises(FIXTURE_ID_INVALID, declare_cohort, [FixtureId(5)])


@pytest.mark.parametrize("empty", [[], ()])
def test_f_empty_cohort_rejected(empty):
    raises(COHORT_EMPTY, declare_cohort, empty)
    raises(COHORT_EMPTY, evaluate_cohort, empty)


@pytest.mark.parametrize("container", [None, "101", b"\x65", {101, 102}, frozenset({101}), {101: INFORMATIVE}, range(1, 3),
                                       iter([101]), 101])
def test_cohort_container_must_be_list_or_tuple(container):
    raises(COHORT_CONTAINER_INVALID, declare_cohort, container)
    raises(COHORT_CONTAINER_INVALID, evaluate_cohort, container)


# G. Sin estado, DEGRADED, NOT_EMITTED y NO_RECORD siguen en el denominador

def test_g_noninformative_and_missing_fixtures_stay_in_denominator():
    d = evaluate_cohort([1, 2, 3, 4, 5], [(1, INFORMATIVE), (2, DEGRADED), (3, NOT_EMITTED), (4, NO_RECORD)])
    assert d.enrolled_count == 5
    assert [x.fixture_id for x in d.fixtures] == [1, 2, 3, 4, 5]
    assert d.informative_synthetic_count == 1 and d.informative_synthetic_coverage == 0.2
    assert d.fixtures[4] == FixtureDiagnostic(fixture_id=5, status=NO_RECORD, status_supplied=False, label_observed=False)
    assert d.fixtures[3] == FixtureDiagnostic(fixture_id=4, status=NO_RECORD, status_supplied=True, label_observed=False)
    assert dict(d.status_counts) == {INFORMATIVE: 1, DEGRADED: 1, NOT_EMITTED: 1, NO_RECORD: 2}
    assert d.status_counts == tuple((s, dict(d.status_counts)[s]) for s in STATUSES)
    assert (d.supplied_status_count, d.implicit_no_record_count) == (4, 1)
    assert d.label_observed_count == 0 and d.label_observability == 0.0


def test_g_no_status_at_all_is_all_no_record_with_full_denominator():
    d = evaluate_cohort(TEN)
    assert d.enrolled_count == 10 and d.informative_synthetic_count == 0 and d.informative_synthetic_coverage == 0.0
    assert dict(d.status_counts) == {INFORMATIVE: 0, DEGRADED: 0, NOT_EMITTED: 0, NO_RECORD: 10}
    assert d.implicit_no_record_count == 10 and all(not x.status_supplied for x in d.fixtures)


# H-I. Nada fuera de la cohorte

def test_h_status_for_unknown_fixture_rejected():
    raises(STATUS_FIXTURE_UNKNOWN, evaluate_cohort, TEN, TEN_STATUSES + [(999, INFORMATIVE)], SIX_LABELS)
    raises(STATUS_FIXTURE_UNKNOWN, evaluate_cohort, SIX, [(107, DEGRADED)])


def test_i_label_for_unknown_fixture_rejected():
    raises(LABEL_FIXTURE_UNKNOWN, evaluate_cohort, TEN, TEN_STATUSES, SIX_LABELS + [999])
    raises(LABEL_FIXTURE_UNKNOWN, evaluate_cohort, SIX, (), [107])


def test_i_duplicate_label_rejected():
    raises(LABEL_DUPLICATE, evaluate_cohort, TEN, (), [101, 102, 101])


@pytest.mark.parametrize("container", [None, "101", {101, 102}, {101: "HOME"}, iter([101]), 101])
def test_i_label_container_must_be_list_or_tuple(container):
    raises(LABEL_CONTAINER_INVALID, evaluate_cohort, TEN, (), container)


# J. Vocabulario de estados estricto y sin asignaciones contradictorias

@pytest.mark.parametrize("bad", ["informative", "VALID", "PENDING", "", None, 1, True, ("INFORMATIVE",)])
def test_j_unknown_status_vocabulary_rejected(bad):
    raises(STATUS_VALUE_UNSUPPORTED, evaluate_cohort, TEN, [(101, bad)])


def test_j_str_subclass_status_rejected():
    class Status(str):
        pass

    raises(STATUS_VALUE_UNSUPPORTED, evaluate_cohort, TEN, [(101, Status(INFORMATIVE))])


def test_j_contradictory_and_duplicate_status_assignments_rejected():
    raises(STATUS_CONTRADICTORY, evaluate_cohort, TEN, [(101, INFORMATIVE), (102, DEGRADED), (101, DEGRADED)])
    raises(STATUS_CONTRADICTORY, evaluate_cohort, TEN, [(101, NO_RECORD), (101, INFORMATIVE)])
    raises(STATUS_DUPLICATE, evaluate_cohort, TEN, [(101, INFORMATIVE), (101, INFORMATIVE)])


@pytest.mark.parametrize("entry", [(101,), (101, INFORMATIVE, "x"), [101, INFORMATIVE], 101, INFORMATIVE, None])
def test_j_malformed_status_entry_rejected(entry):
    raises(STATUS_ENTRY_INVALID, evaluate_cohort, TEN, [entry])


@pytest.mark.parametrize("container", [None, {101: INFORMATIVE}, {(101, INFORMATIVE)}, "INFORMATIVE", iter([(101, INFORMATIVE)])])
def test_j_status_container_must_be_list_or_tuple(container):
    # un dict {1: A, True: B} colapsaría a {1: B} antes de llegar a la API: no se acepta ningún dict
    raises(STATUS_CONTAINER_INVALID, evaluate_cohort, TEN, container)


# K. La identidad no depende de qué estados o etiquetas se observaron

def test_k_cohort_identity_independent_of_observations():
    base = declare_cohort(TEN)
    for statuses, labels in [((), ()), (TEN_STATUSES, ()), ((), SIX_LABELS), (TEN_STATUSES, SIX_LABELS),
                             ([(110, INFORMATIVE)], [110]), ([(f, NOT_EMITTED) for f in TEN], TEN)]:
        d = evaluate_cohort(TEN, statuses, labels)
        assert d.cohort == base
        assert d.enrolled_count == 10 and len(d.fixtures) == 10
        assert tuple(x.fixture_id for x in d.fixtures) == base.fixture_ids == tuple(TEN)


# L. Invariancia al orden

def test_l_permutation_invariance():
    reference = evaluate_cohort(TEN, TEN_STATUSES, SIX_LABELS)
    shuffled = [TEN[i] for i in (7, 2, 9, 0, 5, 3, 8, 1, 6, 4)]
    for ids in (shuffled, list(reversed(TEN)), tuple(shuffled)):
        assert evaluate_cohort(ids, list(reversed(TEN_STATUSES)), tuple(reversed(SIX_LABELS))) == reference
    for perm in itertools.permutations([3, 1, 2]):
        assert declare_cohort(list(perm)) == declare_cohort([1, 2, 3])


# M. Entradas intactas y repetición determinista

def test_m_inputs_not_mutated_and_repeat_calls_identical():
    ids, statuses, labels = list(reversed(TEN)), list(TEN_STATUSES), list(reversed(SIX_LABELS))
    before = copy.deepcopy((ids, statuses, labels))
    first = evaluate_cohort(ids, statuses, labels)
    second = evaluate_cohort(ids, statuses, labels)
    assert (ids, statuses, labels) == before
    assert first == second and hash(first) == hash(second)


def test_m_results_are_immutable():
    d = evaluate_cohort(TEN, TEN_STATUSES, SIX_LABELS)
    for obj, field in ((d, "enrolled_count"), (d.cohort, "fixture_ids"), (d.fixtures[0], "status")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, None)
    assert type(d.fixtures) is tuple and type(d.cohort.fixture_ids) is tuple and type(d.status_counts) is tuple
    assert type(d.limitations) is tuple


# N. Sin selección oculta según qué se predijo o etiquetó

def test_n_no_hidden_selection_from_predictions_or_labels():
    # mismas observaciones de 101-106; solo cambia cuántos fixtures declaró el llamador
    six = evaluate_cohort(SIX, TEN_STATUSES[:6], SIX_LABELS)
    ten = evaluate_cohort(TEN, TEN_STATUSES[:6], SIX_LABELS)
    assert (six.informative_synthetic_coverage, six.label_observability) == (1.0, 1.0)
    assert (ten.informative_synthetic_coverage, ten.label_observability) == (0.6, 0.6)
    # etiquetas en fixtures no informativos cuentan como observadas, pero no vuelven informativo al fixture
    d = evaluate_cohort(TEN, [(101, INFORMATIVE)], [107, 108, 109, 110])
    assert (d.informative_synthetic_count, d.label_observed_count) == (1, 4)
    assert [x.fixture_id for x in d.fixtures if x.label_observed] == [107, 108, 109, 110]
    assert [x.fixture_id for x in d.fixtures if x.status == INFORMATIVE] == [101]
    # un fixture etiquetado o informativo de fuera nunca sustituye a uno inscrito sin observación
    raises(LABEL_FIXTURE_UNKNOWN, evaluate_cohort, SIX, TEN_STATUSES[:5], [101, 102, 103, 104, 105, 107])
    raises(STATUS_FIXTURE_UNKNOWN, evaluate_cohort, SIX, TEN_STATUSES[:5] + [(107, INFORMATIVE)])


def test_n_status_and_label_observability_are_independent():
    statuses = [(f, INFORMATIVE) for f in TEN]
    for labels in ((), [101], SIX_LABELS, TEN):
        d = evaluate_cohort(TEN, statuses, labels)
        assert d.informative_synthetic_count == 10 and d.label_observed_count == len(labels)
    for k in (0, 3, 10):
        d = evaluate_cohort(TEN, [(f, INFORMATIVE) for f in TEN[:k]], SIX_LABELS)
        assert d.informative_synthetic_count == k and d.label_observed_count == 6


# O. Digest estable para los mismos miembros, distinto para otros

def test_o_digest_stable_for_identical_membership_and_distinct_otherwise():
    a, b = declare_cohort([3, 1, 2]), declare_cohort((1, 2, 3))
    assert a.cohort_digest == b.cohort_digest and len(a.cohort_digest) == 64 and int(a.cohort_digest, 16) >= 0
    others = [[1, 2], [1, 2, 4], [1, 2, 3, 4], [12, 3], [1, 23], [0x1, 0x23], [2, 3]]
    digests = {declare_cohort(x).cohort_digest for x in others} | {a.cohort_digest}
    assert len(digests) == len(others) + 1
    assert declare_cohort([2 ** 4000 + 7, 1]).cohort_digest == declare_cohort([1, 2 ** 4000 + 7]).cohort_digest


def test_o_digest_matches_documented_canonical_form():
    body = ",".join(format(f, "x") for f in [1, 10, 255]).encode("ascii")
    expected = hashlib.sha256(b"PREDIKTIA/RESEARCH/PROSPECTIVE_COHORT_DIAGNOSTICS/COHORT_IDENTITY/V1\n" + body).hexdigest()
    assert declare_cohort([255, 1, 10]).cohort_digest == expected


# Límites de confianza explícitos

def test_trust_limitations_are_explicit_in_results():
    d = evaluate_cohort(TEN, TEN_STATUSES, SIX_LABELS)
    assert (d.cohort_basis, d.input_basis, d.certification_status) == ("CALLER_DECLARED", "SYNTHETIC_ONLY", "NOT_CERTIFIED")
    assert (COHORT_BASIS, INPUT_BASIS, CERTIFICATION_STATUS) == ("CALLER_DECLARED", "SYNTHETIC_ONLY", "NOT_CERTIFIED")
    assert (d.cohort.cohort_basis, d.cohort.certification_status) == ("CALLER_DECLARED", "NOT_CERTIFIED")
    assert d.cohort.digest_scope == COHORT_DIGEST_SCOPE == "LOCAL_EQUALITY_ONLY"
    assert d.limitations == LIMITATIONS
    assert {"COHORT_CALLER_DECLARED_NOT_AUTHENTIC_PREREGISTRATION", "NO_PRE_T_IDENTITY_OR_EXTERNAL_WITNESS",
            "NOT_TRUE_PIT_CERTIFIED"} <= set(LIMITATIONS)


def test_no_timestamp_threshold_or_decision_fields():
    names = {f.name for cls in (CohortDiagnostics, DeclaredCohort, FixtureDiagnostic) for f in dataclasses.fields(cls)}
    assert not {n for n in names if any(w in n for w in ("time", "_at", "date", "threshold", "eligible", "admit", "release", "pass"))}


def test_error_codes_are_distinct_and_exported():
    assert len(set(ERROR_CODES)) == len(ERROR_CODES)
    assert all(getattr(cohort_diagnostics, c) == c for c in ERROR_CODES)


def test_module_imports_no_app_database_provider_or_research_siblings():
    code = ("import sys; import research.prospective_cohort_diagnostics; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('app', 'sqlalchemy', 'psycopg', 'numpy', 'sklearn', 'requests', 'httpx',"
            " 'socket', 'urllib3') or m.startswith('research.')))")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_ALLOW_DESTRUCTIVE", "PYTHONPATH")}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0
    assert out.stdout.strip() == str(["research.prospective_cohort_diagnostics"])


# MOD-DEV-105A-R1: el mensaje de error nunca sustituye el CohortInputError por otra excepción.
# H no se pasa a texto en ningún sitio del test: ids de parametrize explícitos y aserciones sobre .code y len(mensaje).

H = 10 ** 5000  # str(H) lanza ValueError (límite de 4300 dígitos de CPython)
MAX_MESSAGE = 400


def raises_bounded(code, fn, *args):
    err = raises(code, fn, *args)
    assert len(str(err)) <= MAX_MESSAGE and str(err).startswith(f"{code}: ")
    return err


class ReprBomb:
    """repr, str y format lanzan una Exception ordinaria y cuentan las llamadas."""
    calls = 0

    def _boom(self, *args):
        type(self).calls += 1
        raise RuntimeError("repr hostil")

    __repr__ = __str__ = __format__ = _boom


class IntReprBomb(int):
    __repr__ = __str__ = __format__ = ReprBomb._boom
    calls = 0


class StrReprBomb(str):
    __repr__ = __str__ = __format__ = ReprBomb._boom
    calls = 0


class HostileMeta(type):
    """Metaclase cuyo __eq__ y __name__ lanzan: rompe `type(x) in (...)` y `type(x).__name__`."""

    def __eq__(cls, other):
        raise RuntimeError("eq hostil")

    __hash__ = type.__hash__

    @property
    def __name__(cls):
        raise RuntimeError("name hostil")


class HostileType(metaclass=HostileMeta):
    pass


def nested_list(depth):
    value = []
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("call, code", [
    pytest.param(lambda: declare_cohort([H, H]), FIXTURE_ID_DUPLICATE, id="A-cohort-duplicate-H"),
    pytest.param(lambda: declare_cohort([-H]), FIXTURE_ID_INVALID, id="B-cohort-negative-H"),
    pytest.param(lambda: evaluate_cohort([1], [(H, INFORMATIVE)]), STATUS_FIXTURE_UNKNOWN, id="C-status-unknown-H"),
    pytest.param(lambda: evaluate_cohort([1], (), [H]), LABEL_FIXTURE_UNKNOWN, id="D-label-unknown-H"),
    pytest.param(lambda: evaluate_cohort([1], [(-H, INFORMATIVE)]), FIXTURE_ID_INVALID, id="status-negative-H"),
    pytest.param(lambda: evaluate_cohort([1], (), [-H]), FIXTURE_ID_INVALID, id="label-negative-H"),
    pytest.param(lambda: evaluate_cohort([H], [(H, "BAD")]), STATUS_VALUE_UNSUPPORTED, id="status-value-for-H"),
    pytest.param(lambda: evaluate_cohort([1], [(1, H)]), STATUS_VALUE_UNSUPPORTED, id="status-value-is-H"),
    pytest.param(lambda: evaluate_cohort([H], [(H, INFORMATIVE), (H, DEGRADED)]), STATUS_CONTRADICTORY, id="status-contradictory-H"),
    pytest.param(lambda: evaluate_cohort([H], [(H, INFORMATIVE), (H, INFORMATIVE)]), STATUS_DUPLICATE, id="status-duplicate-H"),
    pytest.param(lambda: evaluate_cohort([H], (), [H, H]), LABEL_DUPLICATE, id="label-duplicate-H"),
    pytest.param(lambda: evaluate_cohort([1], [(1, INFORMATIVE, H)]), STATUS_ENTRY_INVALID, id="status-entry-with-H"),
    pytest.param(lambda: declare_cohort([[H]]), FIXTURE_ID_INVALID, id="cohort-entry-list-with-H"),
    pytest.param(lambda: declare_cohort(H), COHORT_CONTAINER_INVALID, id="cohort-container-is-H"),
])
def test_r1_giant_integer_keeps_typed_error_code(call, code):
    raises_bounded(code, call)


def test_r1_giant_integer_message_uses_sign_and_bit_length():
    err = raises_bounded(FIXTURE_ID_INVALID, declare_cohort, [-H])
    assert f"<int negativo de {H.bit_length()} bits>" in str(err)
    err = raises_bounded(LABEL_FIXTURE_UNKNOWN, evaluate_cohort, [1], (), [H])
    assert f"<int de {H.bit_length()} bits>" in str(err)
    err = raises_bounded(FIXTURE_ID_INVALID, declare_cohort, [-(2 ** 64)])
    assert "<int negativo de 65 bits>" in str(err)
    err = raises_bounded(FIXTURE_ID_INVALID, declare_cohort, [-(2 ** 63)])
    assert "-9223372036854775808" in str(err)  # hasta 64 bits se muestra en decimal


def test_r1_valid_giant_ids_are_not_transformed():
    c = declare_cohort([H, 1])
    assert c.fixture_ids == (1, H) and c.fixture_ids[1] is H and type(c.fixture_ids[1]) is int
    assert c.cohort_digest == declare_cohort([1, H]).cohort_digest != declare_cohort([1, H + 1]).cohort_digest
    d = evaluate_cohort([H, 1], [(H, INFORMATIVE)], [1])
    assert (d.enrolled_count, d.informative_synthetic_coverage, d.label_observability) == (2, 0.5, 0.5)
    assert [(x.fixture_id is H, x.status, x.label_observed) for x in d.fixtures] == [(False, NO_RECORD, True), (True, INFORMATIVE, False)]


@pytest.mark.parametrize("call, code", [
    pytest.param(lambda: declare_cohort([ReprBomb()]), FIXTURE_ID_INVALID, id="E-cohort-entry"),
    pytest.param(lambda: evaluate_cohort([1], [(ReprBomb(), INFORMATIVE)]), FIXTURE_ID_INVALID, id="E-status-fixture"),
    pytest.param(lambda: evaluate_cohort([1], (), [ReprBomb()]), FIXTURE_ID_INVALID, id="E-label-fixture"),
    pytest.param(lambda: evaluate_cohort([1], [ReprBomb()]), STATUS_ENTRY_INVALID, id="E-status-pair-object"),
    pytest.param(lambda: evaluate_cohort([1], [(1, INFORMATIVE, ReprBomb())]), STATUS_ENTRY_INVALID, id="E-status-pair-tuple3"),
    pytest.param(lambda: evaluate_cohort([1], [[1, ReprBomb()]]), STATUS_ENTRY_INVALID, id="E-status-pair-list"),
    pytest.param(lambda: evaluate_cohort([1], [(1, ReprBomb())]), STATUS_VALUE_UNSUPPORTED, id="E-status-value"),
    pytest.param(lambda: declare_cohort([IntReprBomb(5)]), FIXTURE_ID_INVALID, id="E-int-subclass-cohort"),
    pytest.param(lambda: evaluate_cohort([1], (), [IntReprBomb(1)]), FIXTURE_ID_INVALID, id="E-int-subclass-label"),
    pytest.param(lambda: evaluate_cohort([1], [(1, StrReprBomb(INFORMATIVE))]), STATUS_VALUE_UNSUPPORTED, id="E-str-subclass-status"),
    pytest.param(lambda: declare_cohort(ReprBomb()), COHORT_CONTAINER_INVALID, id="E-cohort-container"),
    pytest.param(lambda: evaluate_cohort([1], HostileType()), STATUS_CONTAINER_INVALID, id="E-hostile-metaclass-container"),
    pytest.param(lambda: declare_cohort([HostileType()]), FIXTURE_ID_INVALID, id="E-hostile-metaclass-entry"),
])
def test_r1_hostile_repr_keeps_typed_error_code_without_calling_repr(call, code):
    ReprBomb.calls = IntReprBomb.calls = StrReprBomb.calls = 0
    raises_bounded(code, call)
    assert ReprBomb.calls == IntReprBomb.calls == StrReprBomb.calls == 0  # el mensaje no depende del repr del valor


@pytest.mark.parametrize("call, code", [
    pytest.param(lambda: declare_cohort([nested_list(100_000)]), FIXTURE_ID_INVALID, id="F-deep-cohort-entry"),
    pytest.param(lambda: evaluate_cohort([1], [nested_list(100_000)]), STATUS_ENTRY_INVALID, id="F-deep-status-entry"),
    pytest.param(lambda: evaluate_cohort([1], [(1, nested_list(100_000))]), STATUS_VALUE_UNSUPPORTED, id="F-deep-status-value"),
    pytest.param(lambda: evaluate_cohort([1], (), [nested_list(100_000)]), FIXTURE_ID_INVALID, id="F-deep-label"),
    pytest.param(lambda: declare_cohort(["x" * 1_000_000]), FIXTURE_ID_INVALID, id="F-huge-str-entry"),
    pytest.param(lambda: evaluate_cohort([1], [(1, "x" * 1_000_000)]), STATUS_VALUE_UNSUPPORTED, id="F-huge-str-status"),
    pytest.param(lambda: evaluate_cohort([1], [tuple(range(100_000))]), STATUS_ENTRY_INVALID, id="F-huge-tuple-entry"),
    pytest.param(lambda: declare_cohort([[0] * 1_000_000]), FIXTURE_ID_INVALID, id="F-huge-list-entry"),
])
def test_r1_deep_or_huge_values_render_bounded(call, code):
    raises_bounded(code, call)


def test_r1_bounded_text_rendering_is_deterministic():
    err = raises_bounded(STATUS_VALUE_UNSUPPORTED, evaluate_cohort, [1], [(1, "y" * 500)])
    assert "(500 caracteres)" in str(err) and "y" * 41 not in str(err)
    err = raises_bounded(STATUS_VALUE_UNSUPPORTED, evaluate_cohort, [1], [(1, "informative")])
    assert "'informative'" in str(err)
    err = raises_bounded(STATUS_ENTRY_INVALID, evaluate_cohort, [1], [(1, INFORMATIVE, "x")])
    assert "<tuple de 3 elementos>" in str(err)
    assert str(raises(FIXTURE_ID_INVALID, declare_cohort, [ReprBomb()])) == str(raises(FIXTURE_ID_INVALID, declare_cohort, [ReprBomb()]))


def test_r1_rendering_failure_falls_back_and_keeps_code(monkeypatch):
    def broken(value):
        raise RuntimeError("fallo de renderizado")

    monkeypatch.setattr(cohort_diagnostics, "_type_name", broken)
    err = raises_bounded(FIXTURE_ID_INVALID, declare_cohort, [object()])
    assert "<valor no representable>" in str(err)
    raises_bounded(COHORT_CONTAINER_INVALID, declare_cohort, {1})


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_r1_base_exceptions_are_not_swallowed_by_rendering(monkeypatch, exc):
    def interrupted(value):
        raise exc()

    monkeypatch.setattr(cohort_diagnostics, "_type_name", interrupted)
    with pytest.raises(exc):
        declare_cohort([object()])
