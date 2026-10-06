"""¿Debe la sync en vivo consultar una temporada que el proveedor marca como actual?

`is_current` es el dato del proveedor y no se toca. Algunas competiciones (torneos de
selecciones, copas extintas) siguen con una edición ya cerrada como "current": consultarla en
cada pasada gasta peticiones sin aportar nada. Esta regla separa "actual según el proveedor" de
"elegible para el polling", sin nombres de competición:

Elegible si se cumple cualquiera de:
1. end_date es NULL o end_date >= hoy - END_GRACE_DAYS (margen para finales y retrasos);
2. tiene algún partido en estado no terminal (por jugar, en vivo, aplazado, suspendido...);
3. no tiene partidos y empieza dentro de START_LOOKAHEAD_DAYS (edición a punto de empezar).
Si no: "dormant" (no se consulta; la sync del catálogo sigue viendo la competición y una edición
nueva la vuelve elegible).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models import Fixture, Season
from app.schemas.fixture import FINAL_STATUSES

END_GRACE_DAYS = 14
START_LOOKAHEAD_DAYS = 30
# Estados en los que el partido ya no va a cambiar: final deportivo/administrativo, cancelado o abandonado
TERMINAL_STATUSES = FINAL_STATUSES | {"CANC", "ABD"}


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str


def polling_eligibility(db: Session, season_id: int, today: date) -> Eligibility:
    season = db.execute(select(Season.start_date, Season.end_date).where(Season.id == season_id)).one()
    if season.end_date is None:
        return Eligibility(True, "end_date desconocida")
    if season.end_date >= today - timedelta(days=END_GRACE_DAYS):
        return Eligibility(True, "temporada en curso o recién terminada")
    pending = db.scalar(
        select(exists().where(Fixture.season_id == season_id, Fixture.status_short.not_in(sorted(TERMINAL_STATUSES))))
    )
    if pending:
        return Eligibility(True, "tiene partidos sin estado final")
    has_fixtures = db.scalar(select(func.count()).select_from(Fixture).where(Fixture.season_id == season_id)) > 0
    if not has_fixtures and season.start_date is not None and today <= season.start_date <= today + timedelta(days=START_LOOKAHEAD_DAYS):
        return Eligibility(True, "edición a punto de empezar")
    return Eligibility(False, f"dormant: terminó el {season.end_date.isoformat()} y no tiene partidos pendientes")
