"""Sincroniza los partidos (y sus resultados) desde el proveedor de fútbol.

Usa las competiciones y temporadas ya guardadas por la sync del catálogo.
Coste en peticiones: 1 llamada a /fixtures por competición (26 con la lista por defecto).
"""

import logging
import time
from datetime import date
from typing import NamedTuple

from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.exceptions import ProviderError
from app.integrations.football.base import FootballDataProvider
from app.integrations.request_stats import describe, stats_of
from app.repositories import catalog_repository, fixture_repository
from app.schemas.fixture import CompetitionFixtureSyncResult, FixtureSyncResult
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


class _CompetitionRef(NamedTuple):
    id: int
    external_id: int
    name: str


async def sync_fixtures(
    db: Session,
    competition_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
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
    Solo se sincronizan las competiciones de TRACKED_LEAGUE_IDS (IDs de API-Football).
    Registra en el log la duración y las peticiones (con reintentos) de cada competición y del total.
    Todas las peticiones de la sync comparten un cliente HTTP, que se cierra al terminar.
    """
    async with get_football_provider() as provider:
        return await _sync_fixtures(db, provider, competition_id, date_from, date_to)


async def _sync_fixtures(
    db: Session,
    provider: FootballDataProvider,
    competition_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> FixtureSyncResult:
    sync_started = time.perf_counter()
    stats = stats_of(provider)
    stats_at_start = stats.snapshot() if stats else None
    tracked = set(get_settings().tracked_league_ids)
    # Valores planos, no objetos ORM: el rollback de una competición fallida expira los objetos
    # de la sesión y releerlos necesitaría la BD, que puede ser justo lo que se ha perdido
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
            continue

        competition_started = time.perf_counter()
        stats_before = stats.snapshot() if stats else None
        crashed = False
        try:
            season = catalog_repository.get_season(db, comp.id, None)
            if season is None:
                result.error = "Sin temporada actual (ejecuta antes POST /sync/catalog)"
                continue
            result.season = season.year

            try:
                fixtures = await provider.get_fixtures(comp.external_id, season.year, date_from, date_to)
            except ProviderError as exc:
                logger.warning("No se pudieron obtener los partidos de %s: %s", comp.name, exc)
                result.error = exc.message
                if provider_failure_action(exc) is SyncAction.ABORT_PROVIDER_RUN:
                    stopped = f"No sincronizada: {provider_abort_reason(exc)}"
                    logger.warning("%s: no se piden las competiciones restantes", exc.__class__.__name__)
                continue

            try:
                team_ids = fixture_repository.ensure_teams(
                    db, [f.home_team for f in fixtures] + [f.away_team for f in fixtures], provider.name
                )
                saved = fixture_repository.upsert_fixtures(db, season.id, fixtures, team_ids, provider.name)
                db.commit()
            except _ISOLATED_DB_ERRORS as exc:
                db.rollback()
                kind = classify_db_error(exc)
                logger.exception("Error de BD (%s) guardando los partidos de %s: se deshacen sus cambios", kind, result.name)
                result.error = f"Error de BD al guardar los partidos ({exc.__class__.__name__}: {kind}); cambios deshechos"
                if db_failure_action(kind) is SyncAction.ABORT_PROVIDER_RUN:
                    stopped = "No sincronizada: se detuvo la sync porque se perdió la conexión con la BD"
                    logger.warning("Conexión con la BD perdida: no se piden las competiciones restantes")
                continue
            result.fixtures = saved
        except BaseException:
            crashed = True  # error no previsto: se propaga, pero el log no debe decir "ok"
            raise
        finally:
            logger.info(
                "Sync fixtures · %s: %s · %d partidos · %.0f ms · %s",
                comp.name,
                "excepción no controlada" if crashed else ("ok" if result.error is None else f"error ({result.error})"),
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
