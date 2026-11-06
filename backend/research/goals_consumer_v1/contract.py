"""Contrato V1 del consumidor de investigación de goles FT90: constantes, errores y frontera de entrada (puro).

La frontera admite SOLO tipos nativos exactos (una subclase se rechaza, nunca se convierte) y describe lo rechazado sin
ejecutar código del llamador: ningún mensaje llama a __repr__, __str__, __format__, __eq__ ni __hash__ de un objeto que no
sea de un tipo exacto admitido. La documentación completa de la interfaz está en research.goals_consumer_v1.
"""

import math
from collections import Counter
from datetime import datetime, timedelta, timezone

from research.goals_calibration_uncertainty import UncertaintyConfig
from research.goals_markets.evaluation import HALF_LINE, INTEGER_LINE, ProbabilityEvidenceError, validate_binary_probabilities
from research.goals_markets.integer_evaluation import validate_integer_probabilities
from research.m58.goal_labels import BTTS_NO, BTTS_YES, OVER, PUSH, SUPPORTED_LINE_TEXTS, UNDER
from research.quote_temporal_eligibility import BTTS, ONE_X_TWO, OVER_UNDER

CONSUMER_ID = "GOALS_RESEARCH_CONSUMER"
CONSUMER_VERSION = "1"
CONTRACT_VERSION = "GOALS_RESEARCH_CONSUMER_V1"  # valor exacto de config["contract_version"]
ESTIMATE, NO_ESTIMATE = "ESTIMATE", "NO_ESTIMATE"

FIXTURE_ID_DECIMAL_DIGITS = 640  # sys.int_info.str_digits_check_threshold: el límite configurable de CPython nunca es menor
FIXTURE_ID_LIMIT = 10 ** FIXTURE_ID_DECIMAL_DIGITS  # fixture_id en [1, 10**640): su decimal (<= 640 cifras) siempre es serializable
GOALS_LIMIT = 10 ** 6  # |fulltime_home|, |fulltime_away| < 10**6 (un negativo es fulltime_invalid en la liquidación publicada)
IDENTIFIER_MAX = 128
IDENTIFIER_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")  # sin "@": model_id inequívoco
MODEL_ID_SEPARATOR = "@"

CONFIG_FIELDS = ("contract_version", "producer_id", "producer_version", "market", "line", "origins", "holdout_length",
                 "calibration_length", "embargo", "reliability")
OBSERVATION_FIELDS = ("fixture_id", "kickoff_at", "cutoff_at", "feature_observed_at", "market", "line", "status", "probabilities",
                      "outcome")
OUTCOME_FIELDS = ("status_short", "fulltime_home", "fulltime_away", "observed_at")

MESSAGE_LIMIT = 400
DESCRIBE_TEXT_LIMIT = 40
_DESCRIBE_INT_BOUND = 10 ** 18
_DESCRIBE_ITEMS = 3
_TYPE_NAME = type.__dict__["__name__"]  # el descriptor de type: ignora un __name__ redefinido en una metaclase


# ------------------------------------------------------------------------------------------------------------ errores

class GoalsConsumerError(ValueError):
    """Base del espacio de errores versionado del consumidor V1. El mensaje es determinista y acotado."""

    def __init__(self, message):
        text = message if type(message) is str else "<mensaje no textual>"
        if str.__len__(text) > MESSAGE_LIMIT:
            text = str.__getitem__(text, slice(0, MESSAGE_LIMIT)) + "..."
        super().__init__(text)


class ConsumerInputError(GoalsConsumerError):
    """La entrada no cumple el contrato V1 en la frontera del consumidor (error del llamador)."""


class ConsumerConfigError(ConsumerInputError):
    """La configuración del consumidor no tiene la forma pedida."""


class ConsumerTargetError(ConsumerInputError):
    """Mercado o línea fuera del objetivo admitido (1X2, líneas de cuarto, texto no canónico...)."""


class ConsumerObservationError(ConsumerInputError):
    """Una observación o la colección no tienen la forma pedida."""


class ConsumerTemporalError(ConsumerObservationError):
    """Un instante no es un datetime exacto con un datetime.timezone exacto."""


class ConsumerProbabilityError(ConsumerObservationError):
    """Probabilidades ausentes, sobrantes, de tipo no exacto, no finitas, fuera de [0, 1] o fuera del símplex."""


class ConsumerIdentityError(ConsumerObservationError):
    """fixture_id que no es un int exacto en [1, 10**640)."""


class ConsumerDuplicateFixtureError(ConsumerIdentityError):
    """Dos observaciones del mismo fixture_id en la llamada (el objetivo es único: también la identidad completa)."""


class ConsumerSourceError(GoalsConsumerError):
    """Un módulo publicado (109A o 109B) rechazó la entrada. Conserva el error original sin reinterpretarlo:
    source_id, source_version, source_error_type (módulo.clase) y source_error (la excepción, también en __cause__)."""

    def __init__(self, source_id: str, source_version: str, error: BaseException):
        self.source_id, self.source_version, self.source_error = source_id, source_version, error
        kind = type(error)
        self.source_error_type = f"{kind.__module__}.{kind.__qualname__}"
        detail = error.args[0] if error.args and type(error.args[0]) is str else ""
        super().__init__(f"{source_id} v{source_version} rechazó la entrada ({kind.__name__}): {detail}")


class ConsumerIntegrityError(GoalsConsumerError):
    """Una conciliación de denominadores o una identidad de fuente no cuadra: no hay resultado (fail-closed)."""


# --------------------------------------------------------------------------------------------- descripciones seguras

def _short_text(value: str) -> str:
    size = str.__len__(value)
    head = str.__getitem__(value, slice(0, DESCRIBE_TEXT_LIMIT))
    return head if size <= DESCRIBE_TEXT_LIMIT else f"{head}... ({size} caracteres)"


def describe(value) -> str:
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
        return f"<{_TYPE_NAME.__get__(kind)} de {len(value)} elementos>"
    return f"<{_short_text(_TYPE_NAME.__get__(kind))}>"


def _describe_keys(keys: list) -> str:
    texts = sorted(k for k in keys if type(k) is str)
    others = sum(1 for k in keys if type(k) is not str)
    parts = [describe(k) for k in texts[:_DESCRIBE_ITEMS]]
    if len(texts) > _DESCRIBE_ITEMS:
        parts.append(f"+{len(texts) - _DESCRIBE_ITEMS} str más")
    if others:
        parts.append(f"{others} no str")
    return f"[{', '.join(parts)}]"


def exact_keys(mapping: dict, expected, what: str, error) -> None:
    """Un dict EXACTO con exactamente las claves `expected` (str), sin hashear ni comparar claves que no sean str exactos."""
    if type(mapping) is not dict:
        raise error(f"{what}: se espera un dict exacto, no {describe(mapping)}")
    keys = list(mapping)
    texts = {k for k in keys if type(k) is str}
    if len(texts) != len(keys) or texts != set(expected):
        missing = sorted(set(expected) - texts)
        extra = [k for k in keys if type(k) is not str or k not in expected]
        raise error(f"{what}: faltan {missing}, sobran {len(extra)} {_describe_keys(extra)}")


def sequence(value, what: str, error) -> list | tuple:
    if type(value) is not list and type(value) is not tuple:
        raise error(f"{what}: se espera una list o tuple exacta, no {describe(value)}")
    return value


# --------------------------------------------------------------------------------------------------------- objetivo

def target_classes(market, line, what: str) -> tuple[str | None, tuple[str, ...]]:
    """(line_type, clases) de un objetivo FT90 admitido, o ConsumerTargetError."""
    if type(market) is not str:
        raise ConsumerTargetError(f"{what}: market tiene que ser un str exacto: {describe(market)}")
    if market == ONE_X_TWO:
        raise ConsumerTargetError(f"{what}: {ONE_X_TWO} no está admitido por el consumidor V1 (solo {BTTS} y {OVER_UNDER})")
    if market not in (BTTS, OVER_UNDER):
        raise ConsumerTargetError(f"{what}: mercado no admitido (solo {BTTS} y {OVER_UNDER}): {describe(market)}")
    if market == BTTS:
        if line is not None:
            raise ConsumerTargetError(f"{what}: {BTTS} no tiene línea: {describe(line)}")
        return None, (BTTS_YES, BTTS_NO)
    if type(line) is not str or line not in SUPPORTED_LINE_TEXTS:
        raise ConsumerTargetError(f"{what}: línea Over/Under no admitida (solo el texto canónico aprobado .0/.5, sin cuartos): "
                                  f"{describe(line)}")
    return (INTEGER_LINE, (OVER, UNDER, PUSH)) if line.endswith(".0") else (HALF_LINE, (OVER, UNDER))


# ------------------------------------------------------------------------------------------------- valores exactos

def identifier(value, what: str) -> str:
    if (type(value) is not str or not 1 <= len(value) <= IDENTIFIER_MAX or not value.isascii()
            or any(ch not in IDENTIFIER_CHARS for ch in value)):
        raise ConsumerConfigError(f"{what}: se espera un str exacto de 1 a {IDENTIFIER_MAX} caracteres [A-Za-z0-9_.-]: "
                                  f"{describe(value)}")
    return value


def instant(value, what: str, error) -> datetime:
    """Un datetime EXACTO con un datetime.timezone EXACTO (offset fijo): ni subclases ni tzinfo del llamador."""
    if type(value) is not datetime:
        raise error(f"{what}: se espera un datetime exacto con zona, no {describe(value)}")
    if type(value.tzinfo) is not timezone:
        zone = "sin zona" if value.tzinfo is None else f"con tzinfo {describe(value.tzinfo)}"
        raise error(f"{what}: se espera un datetime con un datetime.timezone exacto, no {zone}")
    return value


def span(value, what: str) -> timedelta:
    if type(value) is not timedelta:
        raise ConsumerConfigError(f"{what}: se espera un timedelta exacto, no {describe(value)}")
    return value


def fixture_id(value, what: str) -> int:
    if type(value) is not int:
        raise ConsumerIdentityError(f"{what}: fixture_id tiene que ser un int exacto (bool no): {describe(value)}")
    if not 1 <= value < FIXTURE_ID_LIMIT:
        raise ConsumerIdentityError(f"{what}: fixture_id fuera de [1, 10**{FIXTURE_ID_DECIMAL_DIGITS}): {describe(value)}")
    return value


def probabilities(value, classes: tuple[str, ...], what: str) -> dict:
    """Las probabilidades exactas de las clases (validadas con las reglas publicadas), sin recortar ni renormalizar."""
    if type(value) is not dict:
        raise ConsumerProbabilityError(f"{what}: probabilities tiene que ser un dict exacto con las claves {classes}, "
                                       f"no {describe(value)}")
    exact_keys(value, classes, f"{what}: probabilities (se esperan exactamente {classes})", ConsumerProbabilityError)
    plain = {}
    for c in classes:
        p = value[c]
        # el rango antes que la finitud: math.isfinite(10**400) desborda (OverflowError)
        if (type(p) is not int and type(p) is not float) or not 0 <= p <= 1 or not math.isfinite(p):
            raise ConsumerProbabilityError(f"{what}: probabilidad {c} no válida (int o float exacto, finito, en [0, 1]): "
                                           f"{describe(p)}")
        plain[c] = p
    try:
        if len(classes) == 2:
            validate_binary_probabilities((plain[classes[0]], plain[classes[1]]))
        else:
            validate_integer_probabilities(plain)
    except ProbabilityEvidenceError as exc:
        raise ConsumerProbabilityError(f"{what}: {exc.args[0] if exc.args and type(exc.args[0]) is str else 'símplex no válido'}") from None
    return plain


def _goals(value, what: str):
    if value is None:
        return None
    if type(value) is not int or not -GOALS_LIMIT < value < GOALS_LIMIT:
        raise ConsumerObservationError(f"{what}: se espera un int exacto con |n| < {GOALS_LIMIT} o None: {describe(value)}")
    return value


# ------------------------------------------------------------------------------------------------------ configuración

def validate_config(config) -> dict:
    exact_keys(config, CONFIG_FIELDS, "config", ConsumerConfigError)
    version = config["contract_version"]
    if type(version) is not str or version != CONTRACT_VERSION:
        raise ConsumerConfigError(f"config.contract_version: se espera {CONTRACT_VERSION!r}: {describe(version)}")
    producer_id = identifier(config["producer_id"], "config.producer_id")
    producer_version = identifier(config["producer_version"], "config.producer_version")
    market, line = config["market"], config["line"]
    line_type, classes = target_classes(market, line, "config")
    origins = sequence(config["origins"], "config.origins", ConsumerConfigError)
    if not origins:
        raise ConsumerConfigError("config.origins: se espera al menos un origen")
    origins = tuple(instant(o, f"config.origins[{i}]", ConsumerConfigError) for i, o in enumerate(origins))
    reliability = config["reliability"]
    if type(reliability) is not UncertaintyConfig:
        raise ConsumerConfigError(f"config.reliability tiene que ser un UncertaintyConfig exacto, no {describe(reliability)}")
    return {
        "producer_id": producer_id, "producer_version": producer_version,
        "model_id": f"{producer_id}{MODEL_ID_SEPARATOR}{producer_version}",
        "market": market, "line": line, "line_type": line_type, "classes": classes, "origins": origins,
        "holdout_length": span(config["holdout_length"], "config.holdout_length"),
        "calibration_length": span(config["calibration_length"], "config.calibration_length"),
        "embargo": span(config["embargo"], "config.embargo"), "reliability": reliability,
    }


# -------------------------------------------------------------------------------------------------------- observaciones

def _observation(i: int, obs, cfg: dict) -> dict:
    what = f"observación {i}"
    exact_keys(obs, OBSERVATION_FIELDS, what, ConsumerObservationError)
    fid = fixture_id(obs["fixture_id"], what)
    market, line = obs["market"], obs["line"]
    target_classes(market, line, what)
    if market != cfg["market"] or line != cfg["line"]:
        raise ConsumerObservationError(f"{what}: ({describe(market)}, {describe(line)}) no es el objetivo "
                                       f"({cfg['market']!r}, {cfg['line']!r}); nunca se descarta una fila en silencio")
    status = obs["status"]
    if type(status) is not str or status not in (ESTIMATE, NO_ESTIMATE):
        raise ConsumerObservationError(f"{what}: status tiene que ser {ESTIMATE} o {NO_ESTIMATE}: {describe(status)}")
    kickoff = instant(obs["kickoff_at"], f"{what}.kickoff_at", ConsumerTemporalError)
    cutoff = instant(obs["cutoff_at"], f"{what}.cutoff_at", ConsumerTemporalError)
    feature = instant(obs["feature_observed_at"], f"{what}.feature_observed_at", ConsumerTemporalError)

    outcome = obs["outcome"]
    exact_keys(outcome, OUTCOME_FIELDS, f"{what}.outcome", ConsumerObservationError)
    status_short = outcome["status_short"]
    if type(status_short) is not str:
        raise ConsumerObservationError(f"{what}.outcome.status_short tiene que ser un str exacto: {describe(status_short)}")
    home = _goals(outcome["fulltime_home"], f"{what}.outcome.fulltime_home")
    away = _goals(outcome["fulltime_away"], f"{what}.outcome.fulltime_away")
    observed = outcome["observed_at"]
    if observed is not None:
        instant(observed, f"{what}.outcome.observed_at", ConsumerTemporalError)

    probs = obs["probabilities"]
    if status == ESTIMATE:
        if probs is None:
            raise ConsumerProbabilityError(f"{what}: {ESTIMATE} sin probabilidades (no se infieren)")
        probs = probabilities(probs, cfg["classes"], what)
    elif probs is not None:
        raise ConsumerProbabilityError(f"{what}: una abstención {NO_ESTIMATE} no lleva probabilidades")
    # copia nueva de tipos exactos: los módulos publicados nunca ven un objeto del llamador
    return {"fixture_id": fid, "kickoff_at": kickoff, "cutoff_at": cutoff, "feature_observed_at": feature, "market": market,
            "line": line, "status": status, "probabilities": probs,
            "outcome": {"status_short": status_short, "fulltime_home": home, "fulltime_away": away, "observed_at": observed}}


def validate_observations(observations, cfg: dict) -> list[dict]:
    seq = sequence(observations, "observaciones", ConsumerObservationError)
    rows = [_observation(i, obs, cfg) for i, obs in enumerate(seq)]
    repeated = sorted(k for k, n in Counter(r["fixture_id"] for r in rows).items() if n > 1)
    if repeated:
        shown = ", ".join(describe(k) for k in repeated[:_DESCRIBE_ITEMS])
        more = f" y {len(repeated) - _DESCRIBE_ITEMS} más" if len(repeated) > _DESCRIBE_ITEMS else ""
        raise ConsumerDuplicateFixtureError(f"fixture_id repetido en el objetivo ({cfg['market']}, {cfg['line']}): "
                                            f"{len(repeated)} valor(es): {shown}{more}")
    return rows
