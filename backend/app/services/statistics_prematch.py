"""Selección de partidos de contexto para un partido objetivo antes de su inicio (M5.7A).

Capa distinta de la lectura as-of (statistics_as_of): aquí no se lee ninguna estadística, solo
se decide QUÉ partidos pueden aportar contexto y con qué corte. Funciones puras: el kickoff de
cada partido lo aporta el llamador (decidir su fuente, p. ej. STRICT_KNOWLEDGE de DI-A6, es
cosa de M5.7B).

Reglas:
- el corte prepartido es el kickoff del objetivo: solo cuenta lo disponible en ese instante;
- el propio partido nunca es contexto de sí mismo;
- un partido con el mismo kickoff (o posterior) no es contexto: se juega a la vez o después.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.schemas.fixture_knowledge import require_utc


@dataclass(frozen=True)
class ContextCandidate:
    fixture_id: int
    kickoff_at: datetime


def prematch_cutoff(target_kickoff: datetime) -> datetime:
    """Corte de la lectura as-of para el contexto prepartido de un partido: su kickoff."""
    require_utc(target_kickoff)
    return target_kickoff


def context_fixture_ids(target_fixture_id: int, target_kickoff: datetime, candidates: Iterable[ContextCandidate]) -> list[int]:
    """Partidos candidatos que pueden ser contexto del objetivo: kickoff estrictamente anterior y
    distintos del objetivo. Orden determinista (kickoff, id); sin duplicados."""
    require_utc(target_kickoff)
    selected = {}
    for c in candidates:
        require_utc(c.kickoff_at)
        if c.fixture_id != target_fixture_id and c.kickoff_at < target_kickoff:
            selected[c.fixture_id] = c.kickoff_at
    return [f for f, _ in sorted(selected.items(), key=lambda item: (item[1], item[0]))]
