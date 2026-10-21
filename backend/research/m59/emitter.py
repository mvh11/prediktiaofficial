"""Emisor prospectivo SIMULADO con reloj inyectable (sin scheduler ni producción).

Contrato temporal (M5.9A):
- El partido y su kickoff K se leen con conocimiento estricto en H = instante de la emisión.
  UNKNOWN o AMBIGUOUS -> no se emite. T = K − 1 h. Si la emisión es posterior a T -> no se emite
  (LATE); jamás se emite con retraso ni se rellena con conocimiento posterior.
- Features: team_recent_form_v1 con corte = H y horizonte = H (OPERATIONAL_STRICT, H <= T): lo
  que se sabía al emitir, nunca lo que llegue entre la emisión y T.
- Identidad del objetivo no válida (sin equipos, sin competición, el equipo no está en la
  evidencia) -> no se emite. Ventanas sin muestras o historia ambigua -> se emiten B0/B1 marcados
  como DEGRADED y B2 queda en null (no se inventa).
- Idempotente: si el prediction_id (modelos + partido + T) ya está registrado, no se recalcula.
  Una predicción registrada se devuelve también pasado T; sin ella, el primer LATE de esos mismos
  (modelos, partido, T) es definitivo y se reutiliza. Los demás motivos se pueden reintentar.
  Solo para un único escritor secuencial: no hay exclusión entre procesos concurrentes.
- fixture_id: un int >= 1 (bool no); si no, ValueError antes de leer el reloj, el conocimiento o el
  registro. Un LATE guardado solo se reutiliza si prueba ser el que escribiría _no_emission para esos
  (modelos, partido, T); uno que lo afirme sin probarlo es RegistryError (MOD-DEV-055-R3).
- Una predicción o un LATE guardados solo se reutilizan desde una lectura del registro con la cadena
  verificada entera (Registry.verified_records); una cadena rota o una línea ilegible es RegistryError
  antes de calcular features o escribir (MOD-DEV-104A).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.schemas.prematch_features import FeatureStatus, SampleStatus, TeamRecentForm
from app.services.prematch_features import fingerprint
from research.m58.dataset import SIDES, _side_columns
from research.m59.canonical import sha256_hex
from research.m59.manifest import FrozenModel
from research.m59.registry import Registry, RegistryError, prediction_id

CUTOFF_BEFORE_KICKOFF = timedelta(hours=1)
REGISTRY_VERSION = 1
KNOWN, UNKNOWN, AMBIGUOUS = "KNOWN", "UNKNOWN_AT_T", "TEMPORAL_AMBIGUITY"
# Motivos de no emisión
FIXTURE_UNKNOWN, FIXTURE_AMBIGUOUS, LATE, IDENTITY_INVALID = "FIXTURE_UNKNOWN", "FIXTURE_AMBIGUOUS", "LATE", "IDENTITY_INVALID"
# Motivos de degradación (se emiten B0/B1)
NO_FEATURE_SAMPLES, AMBIGUOUS_TEAM_HISTORY = "NO_FEATURE_SAMPLES", "AMBIGUOUS_TEAM_HISTORY"
_TARGET_FAILURES = {FeatureStatus.TARGET_UNKNOWN, FeatureStatus.TARGET_AMBIGUOUS, FeatureStatus.TARGET_TEAM_MISMATCH, FeatureStatus.CUTOFF_AFTER_KICKOFF}


@dataclass(frozen=True)
class FixtureFact:
    status: str
    known_at: datetime | None = None
    kickoff_at: datetime | None = None
    home_team_id: int | None = None
    away_team_id: int | None = None
    competition_id: int | None = None


def _cutoff_iso(cutoff: datetime) -> str:
    """T en ISO-8601 UTC para la identidad: el mismo instante da el mismo prediction_id sea cual sea
    su desplazamiento. Un instante sin zona se deja tal cual: nunca se le supone la hora local."""
    return (cutoff if cutoff.utcoffset() is None else cutoff.astimezone(timezone.utc)).isoformat()


def _instant(value) -> datetime | None:
    """El instante de un texto ISO-8601 con zona horaria, o None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _canonical_instant(value) -> datetime | None:
    """El instante solo si el texto es su forma guardada por canonical_json (ISO-8601 en UTC), o None."""
    parsed = _instant(value)
    try:
        return parsed if parsed is not None and parsed.astimezone(timezone.utc).isoformat() == value else None
    except OverflowError:
        return None


class Emitter:
    def __init__(self, *, clock: Callable[[], datetime], knowledge: Callable[[int, datetime], FixtureFact],
                 form: Callable[[int, int, datetime, datetime], TeamRecentForm], models: dict[str, FrozenModel], registry: Registry):
        if set(models) != {"B0", "B1", "B2"}:
            raise ValueError("hacen falta B0, B1 y B2 congelados")
        self.clock, self.knowledge, self.form, self.models, self.registry = clock, knowledge, form, models, registry
        self.model_set_sha = sha256_hex({k: m.sha for k, m in sorted(models.items())})

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("reloj sin zona horaria")
        return now

    def _no_emission(self, fixture_id, reason, now, cutoff=None, fact=None):
        return self.registry.append({
            "kind": "no_emission", "prediction_id": sha256_hex({"model_set": self.model_set_sha, "fixture_id": fixture_id, "no_emission_at": now}),
            "fixture_id": fixture_id, "reason": reason, "emitted_at": now, "T": cutoff, "H": now,
            "kickoff_known": fact.kickoff_at if fact else None, "model_set_sha": self.model_set_sha, "registry_version": REGISTRY_VERSION,
        })

    def _proves_late(self, rec, fixture_id, cutoff_iso) -> bool:
        """El LATE guardado es el que escribe _no_emission para (modelos, partido, T): tipos canónicos, T = kickoff
        conocido − 1 h, H = emitted_at estrictamente posterior a T y el prediction_id de su fórmula (MOD-DEV-055-R3)."""
        t, h, kickoff = (_canonical_instant(rec.get(k)) for k in ("T", "H", "kickoff_known"))
        return (type(rec.get("fixture_id")) is int and rec["fixture_id"] == fixture_id and rec.get("model_set_sha") == self.model_set_sha
                and rec.get("T") == cutoff_iso and type(rec.get("registry_version")) is int and rec["registry_version"] == REGISTRY_VERSION
                and None not in (t, h, kickoff) and rec.get("emitted_at") == rec["H"] and h > t and kickoff - CUTOFF_BEFORE_KICKOFF == t
                and rec.get("prediction_id") == sha256_hex({"model_set": self.model_set_sha, "fixture_id": fixture_id, "no_emission_at": rec["H"]}))

    def emit(self, fixture_id: int) -> dict:
        if type(fixture_id) is not int or fixture_id < 1:  # antes del reloj, el conocimiento y el registro (MOD-DEV-055-R3)
            raise ValueError("fixture_id tiene que ser un int >= 1 (bool, float, texto o None no)")
        now = self._now()
        horizon = now
        fact = self.knowledge(fixture_id, horizon)
        if fact.status == UNKNOWN:
            return self._no_emission(fixture_id, FIXTURE_UNKNOWN, now)
        if fact.status == AMBIGUOUS:
            return self._no_emission(fixture_id, FIXTURE_AMBIGUOUS, now)
        cutoff = fact.kickoff_at - CUTOFF_BEFORE_KICKOFF
        late = now > cutoff  # un kickoff sin zona falla aquí: T no se adivina ni se busca en el registro
        cutoff_iso = _cutoff_iso(cutoff)
        pid = prediction_id(self.model_set_sha, fixture_id, cutoff_iso)
        # Decisiones terminales de (modelos, partido, T), antes de mirar el reloj: no se repiten ni se duplican.
        records = self.registry.verified_records()  # una lectura, verificada; la misma para predicción y LATE (MOD-DEV-104A)
        for rec in records:  # reinicio / duplicado: la primera emisión manda, también pasado T
            if rec.get("kind") == "prediction" and rec["prediction_id"] == pid:
                # solo es esta emisión si sus datos de identidad son los que dan pid (antes de escribir nada), con su
                # tipo canónico: True o 1.0 son == 1 en Python pero no dan el mismo id (MOD-DEV-055-R2)
                stored = (rec.get("model_set_sha"), rec.get("fixture_id"), rec.get("T"))
                if (stored != (self.model_set_sha, fixture_id, cutoff_iso) or type(stored[1]) is not int
                        or not all(isinstance(v, str) for v in (stored[0], stored[2])) or prediction_id(*stored) != pid):
                    raise RegistryError("prediction_id ya registrado con otros modelos, partido o T")
                return rec
        # sin emisión previa, el primer LATE es definitivo aunque el reloj retroceda. Uno que afirma ser de estos (modelos,
        # partido, T), aunque sea con True, 1.0 u otro desplazamiento, y no lo prueba: RegistryError, ni se devuelve ni se sustituye
        lates = [rec for rec in records if rec.get("kind") == "no_emission" and rec.get("reason") == LATE
                 and rec.get("model_set_sha") == self.model_set_sha and rec.get("fixture_id") == fixture_id and _instant(rec.get("T")) == cutoff]
        if any(not self._proves_late(rec, fixture_id, cutoff_iso) for rec in lates):
            raise RegistryError("LATE ya registrado para estos modelos, partido y T sin identidad o retraso probados")
        if lates:
            return lates[0]
        if late:
            return self._no_emission(fixture_id, LATE, now, cutoff, fact)
        if None in (fact.home_team_id, fact.away_team_id, fact.competition_id):
            return self._no_emission(fixture_id, IDENTITY_INVALID, now, cutoff, fact)

        forms = {side: self.form(team, fixture_id, horizon, horizon) for side, team in zip(SIDES, (fact.home_team_id, fact.away_team_id))}
        if any(f.status in _TARGET_FAILURES for f in forms.values()):
            return self._no_emission(fixture_id, IDENTITY_INVALID, now, cutoff, fact)
        for f in forms.values():  # sin fuga: todo anterior a H (y por tanto a T)
            assert f.horizon == horizon and f.cutoff == horizon and not f.is_retrospective
            assert all(m.kickoff_at < horizon for m in f.window)
        row = {"competition_id": fact.competition_id}
        for side, f in forms.items():
            row.update(_side_columns(side, f))
        degraded = []
        if any(f.status in (FeatureStatus.AMBIGUOUS_HISTORY, FeatureStatus.AMBIGUOUS_ORDER) for f in forms.values()):
            degraded.append(AMBIGUOUS_TEAM_HISTORY)
        if any(f.status is FeatureStatus.OK and f.sample_counts()[SampleStatus.USED] == 0 for f in forms.values()):
            degraded.append(NO_FEATURE_SAMPLES)
        probs = {"B0": self.models["B0"].predict(row), "B1": self.models["B1"].predict(row),
                 "B2": None if degraded else self.models["B2"].predict(row)}
        emitted_at = self._now()
        if emitted_at > cutoff:  # el cálculo no puede empujar la emisión más allá de T
            return self._no_emission(fixture_id, LATE, emitted_at, cutoff, fact)
        return self.registry.append({
            "kind": "prediction", "prediction_id": pid, "fixture_id": fixture_id, "competition_id": fact.competition_id,
            "kickoff_known": fact.kickoff_at, "fixture_known_at": fact.known_at, "T": cutoff, "H": horizon, "emitted_at": emitted_at,
            "model_set_sha": self.model_set_sha, "models": {k: {"sha": m.sha, "version": m.manifest["model_version"]} for k, m in sorted(self.models.items())},
            "probs": probs, "status": "DEGRADED" if degraded else "INFORMATIVE", "degraded_reasons": degraded,
            "unseen_competition": not self.models["B2"].known_competition(fact.competition_id),
            "features": {side: {"status": f.status.value, "n_used": f.sample_counts()[SampleStatus.USED], "fingerprint": fingerprint(f),
                                "provenance": {p.value: n for p, n in f.provenance_counts().items()}} for side, f in forms.items()},
            "registry_version": REGISTRY_VERSION,
        })
