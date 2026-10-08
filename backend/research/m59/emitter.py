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
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.schemas.prematch_features import FeatureStatus, SampleStatus, TeamRecentForm
from app.services.prematch_features import fingerprint
from research.m58.dataset import SIDES, _side_columns
from research.m59.canonical import sha256_hex
from research.m59.manifest import FrozenModel
from research.m59.registry import Registry, prediction_id

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

    def emit(self, fixture_id: int) -> dict:
        now = self._now()
        horizon = now
        fact = self.knowledge(fixture_id, horizon)
        if fact.status == UNKNOWN:
            return self._no_emission(fixture_id, FIXTURE_UNKNOWN, now)
        if fact.status == AMBIGUOUS:
            return self._no_emission(fixture_id, FIXTURE_AMBIGUOUS, now)
        cutoff = fact.kickoff_at - CUTOFF_BEFORE_KICKOFF
        if now > cutoff:
            return self._no_emission(fixture_id, LATE, now, cutoff, fact)
        if None in (fact.home_team_id, fact.away_team_id, fact.competition_id):
            return self._no_emission(fixture_id, IDENTITY_INVALID, now, cutoff, fact)
        pid = prediction_id(self.model_set_sha, fixture_id, cutoff.isoformat())
        for rec in self.registry.records():  # reinicio / duplicado: la primera emisión manda
            if rec["prediction_id"] == pid:
                return rec

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
