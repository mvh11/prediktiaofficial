"""Sincroniza los partidos (y sus resultados) desde el proveedor de fútbol.

Usa las competiciones y temporadas ya guardadas por la sync del catálogo.
Coste en peticiones: 1 llamada a /fixtures por competición elegible (26 con la lista por defecto,
menos las temporadas "dormant", ver polling_eligibility).

Transacciones, por competición:
1. Lectura de la temporada actual y de su elegibilidad; la transacción se cierra ANTES de llamar
   al proveedor (no se mantiene ninguna transacción de BD abierta durante el HTTP).
2. Llamada al proveedor.
3. Nueva transacción: se revalida que esa temporada sigue siendo la actual (la sync del catálogo
   pudo cambiarla durante la descarga); si no, no se escribe nada (skipped).
4. Equipos, partidos y mapeos de esa competición y commit: cada competición es atómica y una
   competición que falla no afecta a las demás. Con los partidos va su evidencia temporal
   (fixture_observations, DI-A6), en la misma transacción: una respuesta = un FixtureEvidence
   (source='sync'), creado al recibirla, entre los pasos 2 y 3.

Ciclo de vida del proveedor: es del nivel del run, nunca de cada competición. Sin proveedor
inyectado la sync abre el suyo con `async with` (un cliente HTTP compartido por todas las
peticiones, que se cierra al terminar); un proveedor inyectado es de quien lo creó, que abre y
cierra su run (ver app.jobs.live_sync).
"""

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import NamedTuple

from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.exceptions import ProviderError
from app.integrations.football.base import FootballDataProvider
from app.integrations.request_stats import describe, stats_of
from app.repositories import catalog_repository, fixture_repository
from app.schemas.fixture import CompetitionFixtureSyncResult, FixtureSyncResult
from app.schemas.fixture_evidence import FixtureEvidence
from app.services.polling_eligibility import polling_eligibility
from app.services.provider_service import get_football_provider
from app.services.sync_failures import (
    SyncAction,
    classify_db_error,
    db_failure_action,
    provider_abort_reason,
    provider_failure_action,
)

logger = logging.getLogger(__name__)

# Errores de BD al guardar UNA competición: se deshace esa competición y se decide con
# classify_db_error (conexión perdida corta el run; el resto, como una consulta cancelada, solo
# falla esa competición). Los errores de programación (SQL mal construido, esquema
# desalineado...) no se capturan: fallarían igual en todas y deben verse.
_ISOLATED_DB_ERRORS = (IntegrityError, DataError, OperationalError)

# Se llama dentro de la transacción de cada competición, justo antes de su commit (o en su propia
# transacción si la competición no escribe): permite auditar la competición de forma atómica con
# sus datos (ver app.jobs.live_sync).
CompetitionHook = Callable[[Session, CompetitionFixtureSyncResult], None]


class _CompetitionRef(NamedTuple):
    id: int
    external_id: int
    name: str


async def sync_fixtures(
    db: Session,
    competition_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    *,
    provider: FootballDataProvider | None = None,
    on_competition: CompetitionHook | None = None,
    today: date | None = None,
) -> FixtureSyncResult:
    """Guarda los partidos de la temporada actual de cada competición (o de una sola).

    Con date_from/date_to solo se piden los partidos de ese rango, útil para
    actualizar resultados recientes sin descargar toda la temporada.
    Si falla una competición (proveedor o BD) se anota el error en su resultado y se sigue
    con las demás; un error de BD deshace solo los cambios de esa competición.
    Excepción: si el proveedor sigue limitando las peticiones tras los reintentos o la cuota
    diaria está agotada (ProviderRateLimitError), si rechaza las credenciales
    (ProviderAuthError: HTTP 401/403 o errors.token), si no está configurado o si se pierde la
    conexión con la BD al guardar, las competiciones restantes no se piden y quedan con el
    motivo en su error (ver sync_failures).
    Solo se sincronizan las competiciones de TRACKED_LEAGUE_IDS (IDs de API-Football) cuya
    temporada actual es elegible para el polling (las "dormant" quedan como skipped).
    Registra en el log la duración y las peticiones (con reintentos) de cada competición y del total.
    """
    if provider is not None:
        return await _sync_fixtures(db, provider, competition_id, date_from, date_to, on_competition, today)
    async with get_football_provider() as owned:
        return await _sync_fixtures(db, owned, competition_id, date_from, date_to, on_competition, today)


async def _sync_fixtures(
    db: Session,
    provider: FootballDataProvider,
    competition_id: int | None,
    date_from: date | None,
    date_to: date | None,
    on_competition: CompetitionHook | None,
    today: date | None,
) -> FixtureSyncResult:
    sync_started = time.perf_counter()
    stats = stats_of(provider)
    stats_at_start = stats.snapshot() if stats else None
    today = today or datetime.now(timezone.utc).date()
    tracked = set(get_settings().tracked_league_ids)
    # Valores planos, no objetos ORM: la sesión se cierra (commit sin escrituras) antes de cada
    # HTTP y el rollback de una competición fallida expira los objetos de la sesión; releerlos
    # necesitaría la BD, que puede ser justo lo que se ha perdido
    competitions = [
        _CompetitionRef(c.id, c.external_id, c.name)
        for c in catalog_repository.list_competitions(db)
        if competition_id is None or c.id == competition_id
    ]

    results: list[CompetitionFixtureSyncResult] = []
    stopped: str | None = None  # motivo por el que ya no se llama al proveedor
    for comp in competitions:
        result = CompetitionFixtureSyncResult(competition_id=comp.id, name=comp.name)
        if comp.external_id not in tracked:
            if competition_id is not None:  # pedida explícitamente: se explica por qué no se sincroniza
                result.error = "No está en TRACKED_LEAGUE_IDS"
                results.append(result)
            continue
        results.append(result)
        if stopped:
            result.error = stopped
            _report(db, on_competition, result)
            continue

        competition_started = time.perf_counter()
        stats_before = stats.snapshot() if stats else None
        crashed = False
        try:
            # 1. Lecturas y cierre de la transacción antes del HTTP
            season = catalog_repository.get_season(db, comp.id, None)
            if season is None:
                result.error = "Sin temporada actual (ejecuta antes POST /sync/catalog)"
                _report(db, on_competition, result)
                continue
            season_id, season_year = season.id, season.year
            result.season = season_year
            eligibility = polling_eligibility(db, season_id, today)
            # Commit sin escrituras: termina la transacción de lectura (no deshace nada del llamador)
            db.commit()
            if not eligibility.eligible:
                result.skipped = eligibility.reason
                _report(db, on_competition, result)
                continue

            # 2. Proveedor, sin transacción de BD abierta
            try:
                fixtures = await provider.get_fixtures(comp.external_id, season_year, date_from, date_to)
            except ProviderError as exc:
                logger.warning("No se pudieron obtener los partidos de %s: %s", comp.name, exc)
                result.error = exc.message
                if provider_failure_action(exc) is SyncAction.ABORT_PROVIDER_RUN:
                    stopped = f"No sincronizada: {provider_abort_reason(exc)}"
                    logger.warning("%s: no se piden las competiciones restantes", exc.__class__.__name__)
                _report(db, on_competition, result)
                continue
            # La respuesta acaba de llegar (reintentos incluidos): esta es su identidad y su observed_at
            # (DI-A6), capturados antes de abrir la transacción de escritura
            evidence = FixtureEvidence.received("sync", provider.name)

            # 3. Revalidación y 4. escritura atómica de la competición
            try:
                current = catalog_repository.get_season(db, comp.id, None)
                if current is None or current.id != season_id:
                    db.rollback()
                    result.skipped = f"season changed: la temporada {season_year} dejó de ser la actual durante la descarga"
                    _report(db, on_competition, result)
                    continue
                teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
                team_external_ids = list({t.external_id for t in teams})
                existing_teams = fixture_repository.count_existing_teams(db, team_external_ids)
                team_ids = fixture_repository.ensure_teams(db, teams, provider.name)
                counts = fixture_repository.upsert_fixtures(db, season_id, fixtures, team_ids, provider.name, evidence)
                result.fixtures, result.created, result.updated, result.unchanged = (
                    counts.received, counts.created, counts.updated, counts.unchanged,
                )
                result.teams_created = len(team_external_ids) - existing_teams
                if on_competition is not None:
                    on_competition(db, result)
                db.commit()
            except _ISOLATED_DB_ERRORS as exc:
                db.rollback()
                kind = classify_db_error(exc)
                logger.exception("Error de BD (%s) guardando los partidos de %s: se deshacen sus cambios", kind, result.name)
                result.fixtures = result.created = result.updated = result.unchanged = result.teams_created = 0
                result.error = f"Error de BD al guardar los partidos ({exc.__class__.__name__}: {kind}); cambios deshechos"
                if db_failure_action(kind) is SyncAction.ABORT_PROVIDER_RUN:
                    stopped = "No sincronizada: se detuvo la sync porque se perdió la conexión con la BD"
                    logger.warning("Conexión con la BD perdida: no se piden las competiciones restantes")
                _report(db, on_competition, result)
                continue
        except BaseException:
            crashed = True  # error no previsto: se propaga, pero el log no debe decir "ok"
            raise
        finally:
            logger.info(
                "Sync fixtures · %s: %s · %d partidos · %.0f ms · %s",
                comp.name,
                _outcome(result, crashed),
                result.fixtures,
                (time.perf_counter() - competition_started) * 1000,
                describe(stats.since(stats_before) if stats else None),
            )

    failed = sum(1 for r in results if r.error is not None)
    logger.info(
        "Sync fixtures · total: %d competiciones (%d con error) · %d partidos · %.0f ms · %s",
        len(results),
        failed,
        sum(r.fixtures for r in results),
        (time.perf_counter() - sync_started) * 1000,
        describe(stats.since(stats_at_start) if stats else None),
    )
    return FixtureSyncResult(
        fixtures_synced=sum(r.fixtures for r in results),
        competitions=results,
    )


def _outcome(result: CompetitionFixtureSyncResult, crashed: bool) -> str:
    if crashed:
        return "excepción no controlada"
    if result.error is not None:
        return f"error ({result.error})"
    if result.skipped is not None:
        return f"skipped ({result.skipped})"
    return "ok"


def _report(db: Session, on_competition: CompetitionHook | None, result: CompetitionFixtureSyncResult) -> None:
    """Auditoría de una competición que no escribe datos (error o skipped), en su propia transacción."""
    if on_competition is None:
        return
    on_competition(db, result)
    db.commit()
