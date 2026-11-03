"""Evaluación walk-forward fuera de muestra de probabilidades de goles a 90' (MOD-DEV-109A). Puro y determinista: sin BD,
red, disco, proveedores, variables de entorno ni reloj.

Puntúa probabilidades YA EMITIDAS por un productor externo (p. ej. la referencia Poisson de tasas por sede) sobre
observaciones históricas que aporta el llamador, y las compara con una tasa base histórica ajustada solo con lo conocido
antes de la frontera de cada pliegue. No ajusta ni recalibra modelos y no calcula ECE, intervalos ni fiabilidad (paquete
aparte). No acredita la procedencia histórica: evidence_status es siempre UNVERIFIED_RESEARCH_ONLY y source_status
WAIT_REAL_SOURCE; no existe ningún campo del llamador que los eleve (una clave de más en la configuración es ConfigError).

Interfaz:
  evaluate_goals_oos(observations, config) -> dict   (informe JSON-serializable con report_digest)

Configuración (Mapping con exactamente estas claves):
  model_id            str no vacío y sin espacios en los extremos: identidad DECLARADA del productor de probabilidades
  origins             lista o tupla no vacía de datetime con zona, estrictamente crecientes: origen O_k de cada pliegue
  holdout_length      timedelta > 0
  calibration_length  timedelta >= 0
  embargo             timedelta >= 0
Pliegue k, fronteras explícitas e intervalos semiabiertos sobre kickoff_at:
  train        kickoff < train_end = O_k − embargo − calibration_length − embargo
  calibration  [O_k − embargo − calibration_length, O_k − embargo)    reservado: en esta versión ningún comparador lo usa
  holdout      [O_k, O_k + holdout_length)
Lo que cae en los dos huecos de `embargo` es EMBARGOED. Los holdout de pliegues sucesivos no se solapan
(O_{k+1} >= O_k + holdout_length; si no, ConfigError). La pertenencia depende SOLO de kickoff_at, nunca del resultado,
del estado del productor ni de las probabilidades: la cohorte no se elige mirando resultados posteriores.

Observación (Mapping con exactamente estas claves; una por identidad (fixture_id, market, line)):
  fixture_id           int >= 1 (bool no)
  kickoff_at           datetime con zona
  cutoff_at            T nombrado de la predicción: datetime con zona, cutoff_at < kickoff_at
  feature_observed_at  datetime con zona: el observed_at más reciente de las entradas que usó el productor (strict_knowledge,
                       observed_at <= T). feature_observed_at > cutoff_at es LeakageError, también por un segundo
  market               str. BTTS_FT90 y OVER_UNDER_FT90 se evalúan; cualquier otro texto es UNSUPPORTED_MARKET
  line                 None en BTTS; str en Over/Under: texto canónico de research.m58.goal_labels ("0.5" ... "6.5"),
                       cualquier otro texto (p. ej. "2.25") es UNSUPPORTED_LINE
  status               ESTIMATE | NO_ESTIMATE (abstención del productor: se conserva y se cuenta, nunca se rellena)
  probabilities        con ESTIMATE, Mapping con exactamente las clases del mercado: YES/NO (BTTS), OVER/UNDER (línea .5),
                       OVER/UNDER/PUSH (línea entera); se validan con las funciones publicadas de research.goals_markets
                       (int o float finito, bool no, en [0, 1], suma 1 con tolerancia 1e-9), sin recortar ni renormalizar.
                       Con NO_ESTIMATE, None. En filas UNSUPPORTED no se leen ni entran en la huella.
  outcome              Mapping con exactamente status_short (str), fulltime_home, fulltime_away (int o None) y observed_at
                       (datetime con zona, o None = sin evidencia del resultado). Igual en todas las filas del partido.
Un resultado final retrospectivo puede observarse después de cutoff_at: es la etiqueta, no una entrada del productor, y
no se rechaza por eso. Sí es TemporalError un estado FT/AET/PEN observado en su kickoff o antes.

Identidad: (fixture_id, market, line) repetida es DuplicateObservationError (también si las filas son idénticas). Las
filas de un mismo partido deben coincidir en kickoff_at (como instante) y en outcome; si no, FixtureConflictError, de modo
que un partido nunca cae en dos conjuntos del mismo pliegue. Todo error de forma, tiempo o probabilidad lanza una
subclase de OOSInputError sin resultado parcial (fail-closed).

Liquidación: research.m58.goal_labels (marcador fulltime a 90'; FT/AET/PEN). Motivos de exclusión de un holdout:
outcome_observed_at_missing y los canónicos (status_not_finished, fulltime_incomplete, fulltime_invalid). La tasa base de
un grupo (market, line) usa solo filas de train del mismo grupo cuya etiqueta se observó en <= train_end
(label_not_known_at_train_end si no) y es la frecuencia empírica sin suavizar de cada clase (PUSH incluida en líneas
enteras); sin etiquetas, UNAVAILABLE.

Puntuación, siempre con las funciones publicadas (sin fórmulas propias):
  BTTS y líneas .5: research.goals_markets.evaluation (Brier de dos componentes 0-2 y log loss)
  líneas enteras:   research.goals_markets.integer_evaluation (tres clases OVER/UNDER/PUSH; PUSH puntúa con p_PUSH)
Por grupo: model (filas ESTIMATE liquidadas), base_rate_on_model_set (la tasa base sobre EXACTAMENTE esas filas) y
base_rate_on_all_settled (todas las liquidadas, NO_ESTIMATE incluidas), con coverage para que la diferencia de
cobertura quede a la vista. Nunca se mezclan líneas ni mercados en una métrica.

Determinismo: las filas se validan y ordenan por identidad; el informe solo depende del multiconjunto de observaciones y
de la configuración. dataset_fingerprint, config_digest y report_digest son SHA-256 de su JSON canónico
(research.m59.canonical).
"""

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from research.goals_markets.evaluation import (BTTS_OUTCOMES, HALF_LINE, INTEGER_LINE, OVER_UNDER_OUTCOMES, ProbabilityEvidenceError,
                                               evaluate_btts, evaluate_over_under, validate_binary_probabilities)
from research.goals_markets.integer_evaluation import (INTEGER_OUTCOMES, THREE_CLASS, evaluate_integer_over_under,
                                                       validate_integer_probabilities)
from research.m58.goal_labels import SUPPORTED_LINE_TEXTS, label_btts, label_over_under
from research.m58.labels import LABELLED_STATUSES
from research.m59.canonical import sha256_hex
from research.quote_temporal_eligibility import BTTS, OVER_UNDER
from research.venue_goal_rates.baseline import ESTIMATE, NO_ESTIMATE

EVALUATOR_ID = "GOALS_OOS_WALK_FORWARD"
EVALUATOR_VERSION = "1"
UNVERIFIED_RESEARCH_ONLY = "UNVERIFIED_RESEARCH_ONLY"
WAIT_REAL_SOURCE = "WAIT_REAL_SOURCE"
CLASSIFICATIONS = ("RESEARCH_ONLY", "DATASET_CALLER_SUPPLIED", "POISSON_REFERENCE_NOT_CALIBRATED", "NOT_CERTIFIED", WAIT_REAL_SOURCE)
LIMITATIONS = (
    "NO_DATA_INTEGRITY_SOURCE_ADMISSION",
    "TIMESTAMPS_AND_OUTCOMES_SUPPLIED_BY_CALLER_NOT_AUTHENTICATED",
    "COHORT_COMPLETENESS_NOT_VERIFIED",
    "PRODUCER_FREEZE_AT_FOLD_BOUNDARY_NOT_VERIFIED",
    "BASE_RATE_IS_UNSMOOTHED_EMPIRICAL_FREQUENCY",
    "NO_MODEL_FITTING_RECALIBRATION_ECE_INTERVALS_OR_RELIABILITY",
    "NOT_A_CALIBRATION_CERTIFICATE",
    "NO_BETTING_VALUE_OR_PRODUCTION_ELIGIBILITY",
)
COMPONENTS = (
    "research.goals_markets.evaluation.evaluate_btts",
    "research.goals_markets.evaluation.evaluate_over_under",
    "research.goals_markets.integer_evaluation.evaluate_integer_over_under",
    "research.m58.goal_labels",
)

BINARY_TWO_COMPONENT = "BINARY_TWO_COMPONENT"  # base de métricas de BTTS y líneas .5 (no la de tres clases)
UNSUPPORTED_MARKET, UNSUPPORTED_LINE = "UNSUPPORTED_MARKET", "UNSUPPORTED_LINE"
OUTCOME_OBSERVED_AT_MISSING = "outcome_observed_at_missing"
LABEL_NOT_KNOWN_AT_TRAIN_END = "label_not_known_at_train_end"
AVAILABLE, UNAVAILABLE = "AVAILABLE", "UNAVAILABLE"
NO_TRAIN_LABELS_KNOWN_AT_BOUNDARY = "NO_TRAIN_LABELS_KNOWN_AT_BOUNDARY"
# estado de un grupo en un pliegue
NO_HOLDOUT_ROWS, NO_SETTLED_OUTCOMES, ALL_ABSTAINED, BASE_RATE_UNAVAILABLE, COMPARED = (
    "NO_HOLDOUT_ROWS", "NO_SETTLED_OUTCOMES", "ALL_ABSTAINED", "BASE_RATE_UNAVAILABLE", "COMPARED")

CONFIG_FIELDS = frozenset({"model_id", "origins", "holdout_length", "calibration_length", "embargo"})
OBSERVATION_FIELDS = frozenset({"fixture_id", "kickoff_at", "cutoff_at", "feature_observed_at", "market", "line", "status",
                                "probabilities", "outcome"})
OUTCOME_FIELDS = frozenset({"status_short", "fulltime_home", "fulltime_away", "observed_at"})


class OOSInputError(ValueError):
    """Configuración u observaciones mal formadas o incoherentes (error del llamador)."""


class ConfigError(OOSInputError):
    """La configuración no tiene la forma pedida o sus pliegues no son válidos."""


class ObservationError(OOSInputError):
    """Una observación o la colección no tienen la forma pedida."""


class TemporalError(OOSInputError):
    """Una marca de tiempo no es un instante con zona o su orden es imposible."""


class LeakageError(TemporalError):
    """Una entrada del productor se observó después del T nombrado de la predicción."""


class DuplicateObservationError(ObservationError):
    """Dos observaciones con la misma identidad (fixture_id, market, line)."""


class FixtureConflictError(ObservationError):
    """Filas de un mismo partido con kickoff u outcome distintos."""


class ProbabilityInputError(ObservationError):
    """Las probabilidades de una observación ESTIMATE faltan o no son válidas."""


def _instant(value, what: str, error=TemporalError) -> datetime:
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise error(f"{what}: se espera un datetime con zona horaria, no {type(value).__name__}")
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:  # tzinfo inválido o instante fuera del rango de datetime en UTC
        if isinstance(exc, OOSInputError):
            raise
        raise error(f"{what}: instante no representable en UTC") from None


def _span(value, what: str, *, positive: bool) -> timedelta:
    if not isinstance(value, timedelta) or (value <= timedelta(0) if positive else value < timedelta(0)):
        raise ConfigError(f"{what}: se espera un timedelta {'> 0' if positive else '>= 0'}")
    return value


def _exact_fields(obj, fields: frozenset, what: str, error) -> None:
    if not isinstance(obj, Mapping):
        raise error(f"{what}: se espera un Mapping, no {type(obj).__name__}")
    keys = set(obj)
    if keys != fields:
        raise error(f"{what}: faltan {sorted(map(str, fields - keys))}, sobran {sorted(map(str, keys - fields))}")


def _iso(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).isoformat()


def _micros(span: timedelta) -> int:
    return span // timedelta(microseconds=1)


def _config(config) -> dict:
    _exact_fields(config, CONFIG_FIELDS, "config", ConfigError)
    model_id = config["model_id"]
    if not isinstance(model_id, str) or not model_id or model_id != model_id.strip():
        raise ConfigError("model_id: se espera un str no vacío sin espacios en los extremos")
    origins = config["origins"]
    if not isinstance(origins, (list, tuple)) or not origins:
        raise ConfigError("origins: se espera una lista o tupla no vacía de datetime con zona")
    origins = tuple(_instant(o, f"origins[{i}]", ConfigError) for i, o in enumerate(origins))
    holdout = _span(config["holdout_length"], "holdout_length", positive=True)
    calibration = _span(config["calibration_length"], "calibration_length", positive=False)
    embargo = _span(config["embargo"], "embargo", positive=False)
    folds = []
    try:
        for k, origin in enumerate(origins):
            if k and not origins[k - 1] < origin:
                raise ConfigError("origins: deben ser estrictamente crecientes")
            if k and origin < origins[k - 1] + holdout:
                raise ConfigError(f"origins[{k}]: el holdout del pliegue anterior se solaparía con este")
            calibration_end = origin - embargo
            calibration_start = calibration_end - calibration
            folds.append({"train_end": calibration_start - embargo, "calibration_start": calibration_start,
                          "calibration_end": calibration_end, "holdout_start": origin, "holdout_end": origin + holdout})
    except OverflowError:
        raise ConfigError("las fronteras de un pliegue quedan fuera del rango de datetime") from None
    identity = {"model_id": model_id, "origins": [_iso(o) for o in origins], "holdout_length_us": _micros(holdout),
                "calibration_length_us": _micros(calibration), "embargo_us": _micros(embargo)}
    return {"identity": identity, "folds": folds}


def _line_type(market: str, line) -> str | None:
    return None if market == BTTS else INTEGER_LINE if line.endswith(".0") else HALF_LINE


def _classes(market: str, line) -> tuple[str, ...]:
    if market == BTTS:
        return BTTS_OUTCOMES
    return INTEGER_OUTCOMES if _line_type(market, line) == INTEGER_LINE else OVER_UNDER_OUTCOMES


def _probabilities(probabilities, market: str, line, what: str) -> dict:
    classes = _classes(market, line)
    if not isinstance(probabilities, Mapping) or len(probabilities) != len(classes) or any(k not in probabilities for k in classes):
        raise ProbabilityInputError(f"{what}: se espera un Mapping con exactamente las clases {classes}")
    try:
        if classes == INTEGER_OUTCOMES:
            validate_integer_probabilities(probabilities)
        else:
            validate_binary_probabilities(tuple(probabilities[k] for k in classes))
    except ProbabilityEvidenceError as exc:
        raise ProbabilityInputError(f"{what}: {exc}") from None
    return {k: float(probabilities[k]) + 0.0 for k in classes}  # -0.0 -> 0.0; 1 y 1.0 son la misma probabilidad


def _observation(i: int, obs) -> dict:
    what = f"observación {i}"
    _exact_fields(obs, OBSERVATION_FIELDS, what, ObservationError)
    fixture_id, market, line, status = obs["fixture_id"], obs["market"], obs["line"], obs["status"]
    if type(fixture_id) is not int or fixture_id < 1:
        raise ObservationError(f"{what}: fixture_id debe ser un int >= 1")
    if not isinstance(market, str) or not (line is None or isinstance(line, str)):
        raise ObservationError(f"{what}: market debe ser str y line None o str")
    if not isinstance(status, str) or status not in (ESTIMATE, NO_ESTIMATE):
        raise ObservationError(f"{what}: status debe ser {ESTIMATE} o {NO_ESTIMATE}")
    kickoff = _instant(obs["kickoff_at"], f"{what}.kickoff_at")
    cutoff = _instant(obs["cutoff_at"], f"{what}.cutoff_at")
    feature = _instant(obs["feature_observed_at"], f"{what}.feature_observed_at")
    if not cutoff < kickoff:
        raise TemporalError(f"{what}: se exige cutoff_at < kickoff_at ({_iso(cutoff)}, {_iso(kickoff)})")
    if feature > cutoff:
        raise LeakageError(f"{what}: feature_observed_at {_iso(feature)} posterior al T nombrado {_iso(cutoff)}")

    outcome = obs["outcome"]
    _exact_fields(outcome, OUTCOME_FIELDS, f"{what}.outcome", ObservationError)
    status_short, home, away = outcome["status_short"], outcome["fulltime_home"], outcome["fulltime_away"]
    if not isinstance(status_short, str) or any(not (g is None or isinstance(g, int)) for g in (home, away)):
        raise ObservationError(f"{what}.outcome: status_short debe ser str y fulltime_home/away int o None")
    observed = None if outcome["observed_at"] is None else _instant(outcome["observed_at"], f"{what}.outcome.observed_at")
    if observed is not None and status_short in LABELLED_STATUSES and observed <= kickoff:
        raise TemporalError(f"{what}: estado final {status_short} observado en su kickoff o antes")

    unsupported = None
    if market == BTTS:
        if line is not None:
            raise ObservationError(f"{what}: {BTTS} no lleva línea")
    elif market == OVER_UNDER:
        if line is None:
            raise ObservationError(f"{what}: {OVER_UNDER} exige una línea")
        if line not in SUPPORTED_LINE_TEXTS:
            unsupported = UNSUPPORTED_LINE
    else:
        unsupported = UNSUPPORTED_MARKET

    probabilities = None
    if unsupported is None:
        if status == ESTIMATE:
            probabilities = _probabilities(obs["probabilities"], market, line, what)
        elif obs["probabilities"] is not None:
            raise ObservationError(f"{what}: una observación {NO_ESTIMATE} no lleva probabilidades")
    return {"fixture_id": fixture_id, "kickoff_at": kickoff, "cutoff_at": cutoff, "feature_observed_at": feature, "market": market,
            "line": line, "status": status, "probabilities": probabilities, "unsupported_reason": unsupported,
            "outcome": {"status_short": status_short, "fulltime_home": home, "fulltime_away": away, "observed_at": observed}}


def _key(row: dict) -> tuple:
    return row["fixture_id"], row["market"], "" if row["line"] is None else row["line"]


def _observations(observations) -> list[dict]:
    if not isinstance(observations, (list, tuple)):
        raise ObservationError(f"las observaciones van en una lista o tupla, no en {type(observations).__name__}")
    rows = sorted((_observation(i, obs) for i, obs in enumerate(observations)), key=_key)
    fixtures: dict[int, dict] = {}
    for prev, row in zip([None] + rows, rows):
        if prev is not None and _key(prev) == _key(row):
            raise DuplicateObservationError(f"identidad repetida {(row['fixture_id'], row['market'], row['line'])!r}")
        first = fixtures.setdefault(row["fixture_id"], row)
        if first["kickoff_at"] != row["kickoff_at"] or first["outcome"] != row["outcome"]:
            raise FixtureConflictError(f"fixture_id {row['fixture_id']}: kickoff u outcome distintos entre sus filas")
    return rows


def _fingerprint_row(row: dict) -> dict:
    out = dict(row, kickoff_at=_iso(row["kickoff_at"]), cutoff_at=_iso(row["cutoff_at"]),
               feature_observed_at=_iso(row["feature_observed_at"]))
    observed = row["outcome"]["observed_at"]
    out["outcome"] = dict(row["outcome"], observed_at=None if observed is None else _iso(observed))
    return out


def _settle(row: dict) -> tuple[str | None, str | None]:
    o = row["outcome"]
    if o["observed_at"] is None:
        return None, OUTCOME_OBSERVED_AT_MISSING
    if row["market"] == BTTS:
        return label_btts(o["status_short"], o["fulltime_home"], o["fulltime_away"])
    return label_over_under(o["status_short"], o["fulltime_home"], o["fulltime_away"], row["line"])


def _score(market: str, line, entries: list[tuple[dict, dict]]) -> dict:
    """Métricas publicadas sobre (fila, probabilidades); las filas ya están liquidadas y en orden de identidad."""
    if not entries:
        return {"scored": 0, "brier": None, "log_loss": None}
    classes = _classes(market, line)
    integer = classes == INTEGER_OUTCOMES
    observations = [{"status_short": r["outcome"]["status_short"], "fulltime_home": r["outcome"]["fulltime_home"],
                     "fulltime_away": r["outcome"]["fulltime_away"],
                     "probabilities": dict(p) if integer else tuple(p[k] for k in classes)} for r, p in entries]
    if market == BTTS:
        evaluation = evaluate_btts(observations)
        scored = evaluation.scored
    elif integer:
        evaluation = evaluate_integer_over_under(observations, line)
        scored = evaluation.probability_scored
    else:
        evaluation = evaluate_over_under(observations, line)
        scored = evaluation.scored
    if scored != len(entries):
        raise AssertionError("el evaluador publicado no puntuó todas las filas liquidadas")
    return {"scored": scored, "brier": evaluation.brier, "log_loss": evaluation.log_loss}


def _group(market: str, line, train: list[dict], holdout: list[dict], train_end: datetime) -> dict:
    classes = _classes(market, line)
    train_excluded, labels = Counter(), Counter()
    for row in train:
        observed = row["outcome"]["observed_at"]
        if observed is not None and observed > train_end:
            train_excluded[LABEL_NOT_KNOWN_AT_TRAIN_END] += 1  # se conocerá después de la frontera: no entra
            continue
        result, reason = _settle(row)
        if result is None:
            train_excluded[reason] += 1
        else:
            labels[result] += 1
    used = sum(labels.values())
    base = {k: labels[k] / used for k in classes} if used else None

    excluded, settled, outcome_counts = Counter(), [], Counter()
    for row in holdout:
        result, reason = _settle(row)
        if result is None:
            excluded[reason] += 1
        else:
            settled.append(row)
            outcome_counts[result] += 1
    estimates = [r for r in settled if r["status"] == ESTIMATE]
    model = _score(market, line, [(r, r["probabilities"]) for r in estimates])
    on_model_set = _score(market, line, [(r, base) for r in estimates]) if base else None
    on_all = _score(market, line, [(r, base) for r in settled]) if base else None

    if not holdout:
        status = NO_HOLDOUT_ROWS
    elif not settled:
        status = NO_SETTLED_OUTCOMES
    elif not estimates:
        status = ALL_ABSTAINED
    else:
        status = COMPARED if base else BASE_RATE_UNAVAILABLE
    return {
        "market": market, "line": line, "line_type": _line_type(market, line),
        "metric_basis": THREE_CLASS if classes == INTEGER_OUTCOMES else BINARY_TWO_COMPONENT, "status": status,
        "holdout_rows": len(holdout), "holdout_estimate": sum(r["status"] == ESTIMATE for r in holdout),
        "holdout_no_estimate": sum(r["status"] == NO_ESTIMATE for r in holdout),
        "holdout_excluded_by_reason": dict(sorted(excluded.items())), "holdout_settled": len(settled),
        "outcome_counts": {k: outcome_counts[k] for k in classes},
        "base_rate": {"status": AVAILABLE if base else UNAVAILABLE, "reason": None if base else NO_TRAIN_LABELS_KNOWN_AT_BOUNDARY,
                      "fit_boundary": _iso(train_end), "train_rows": len(train), "train_labels_used": used,
                      "train_excluded_by_reason": dict(sorted(train_excluded.items())), "probabilities": base},
        "model": model, "base_rate_on_model_set": on_model_set, "base_rate_on_all_settled": on_all,
        "coverage": {"settled": len(settled), "model_scored": model["scored"], "abstained_settled": len(settled) - model["scored"],
                     "compared": model["scored"] if base else 0},
    }


def _fold(k: int, bounds: dict, supported: list[dict], groups: list[tuple]) -> dict:
    sets = {"train": [], "calibration": [], "holdout": [], "embargoed": []}
    for row in supported:
        t = row["kickoff_at"]
        if t < bounds["train_end"]:
            sets["train"].append(row)
        elif bounds["calibration_start"] <= t < bounds["calibration_end"]:
            sets["calibration"].append(row)
        elif bounds["holdout_start"] <= t < bounds["holdout_end"]:
            sets["holdout"].append(row)
        elif t < bounds["holdout_start"]:
            sets["embargoed"].append(row)
    fixture_sets = {name: {r["fixture_id"] for r in rows} for name, rows in sets.items()}
    names = list(fixture_sets)
    if any(fixture_sets[a] & fixture_sets[b] for i, a in enumerate(names) for b in names[i + 1:]):
        raise AssertionError("un partido en dos conjuntos del mismo pliegue")

    def of(rows, market, line):
        return [r for r in rows if r["market"] == market and r["line"] == line]

    return {
        "fold_index": k, "fold_id": f"FOLD_{k}", "boundaries": {name: _iso(t) for name, t in bounds.items()},
        "cohort_rows": {name: len(rows) for name, rows in sets.items()},
        "cohort_fixtures": {name: len(ids) for name, ids in fixture_sets.items()},
        "groups": [_group(m, line, of(sets["train"], m, line), of(sets["holdout"], m, line), bounds["train_end"]) for m, line in groups],
    }


def evaluate_goals_oos(observations, config) -> dict:
    """Informe walk-forward fuera de muestra sobre observaciones del llamador (ver el módulo)."""
    cfg = _config(config)
    rows = _observations(observations)
    supported = [r for r in rows if r["unsupported_reason"] is None]
    unsupported = Counter((r["market"], r["line"], r["unsupported_reason"]) for r in rows if r["unsupported_reason"] is not None)
    groups = sorted({(r["market"], r["line"]) for r in supported},
                    key=lambda g: (g[0], -1 if g[1] is None else SUPPORTED_LINE_TEXTS.index(g[1])))
    in_holdout = {id(r) for r in supported for b in cfg["folds"] if b["holdout_start"] <= r["kickoff_at"] < b["holdout_end"]}
    body = {
        "evaluator_id": EVALUATOR_ID, "evaluator_version": EVALUATOR_VERSION,
        "evidence_status": UNVERIFIED_RESEARCH_ONLY, "source_status": WAIT_REAL_SOURCE,
        "classifications": list(CLASSIFICATIONS), "limitations": list(LIMITATIONS), "components": list(COMPONENTS),
        "model_id": cfg["identity"]["model_id"], "config": cfg["identity"], "config_digest": sha256_hex(cfg["identity"]),
        "dataset_fingerprint": sha256_hex([_fingerprint_row(r) for r in rows]),
        "counts": {
            "rows": len(rows), "fixtures": len({r["fixture_id"] for r in rows}), "supported": len(supported),
            "unsupported": sum(unsupported.values()), "estimate": sum(r["status"] == ESTIMATE for r in supported),
            "no_estimate": sum(r["status"] == NO_ESTIMATE for r in supported), "supported_in_no_holdout": len(supported) - len(in_holdout),
        },
        "unsupported": [{"market": m, "line": line, "reason": reason, "rows": n}
                        for (m, line, reason), n in sorted(unsupported.items(), key=lambda e: (e[0][0], e[0][1] or "", e[0][2]))],
        "folds": [_fold(k, bounds, supported, groups) for k, bounds in enumerate(cfg["folds"])],
    }
    return {**body, "report_digest": sha256_hex(body)}
