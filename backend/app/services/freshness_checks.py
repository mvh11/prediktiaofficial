"""Check de frescura de la sync en vivo. Solo lecturas: nunca escribe ni llama al proveedor.

Cada check devuelve PASS, INFO, WARNING o ERROR; el estado global es el peor de ellos (INFO no
cuenta). Solo se miran las temporadas actuales elegibles para el polling (las "dormant" no se
consultan y no pueden quedarse viejas).

Umbrales (cadencia horaria de la sync de fixtures):
- F1 partidos NS/TBD con kickoff pasado: > 3 h WARNING, > 24 h ERROR.
- F2 partidos en vivo envejecidos: > 4 h WARNING, > 24 h ERROR.
- F3 partidos suspendidos/interrumpidos (SUSP/INT): > 24 h WARNING (pueden durar días).
- F4 last_seen_at de la temporada: > 2 h WARNING, > 24 h ERROR.
- F5 temporada actual elegible sin partidos: WARNING; ERROR si es una liga ya empezada.
- F6 última sync de fixtures terminada: ninguna o > 2 h WARNING, > 24 h ERROR.
- F7 fallos del proveedor en la última sync de fixtures: credenciales ERROR; límite o alguna
  competición fallida WARNING.
- F8 PST/TBD con kickoff de hace más de 7 días: INFO (nunca error: depende del proveedor).
- F9 temporadas pendientes de conciliación histórica: INFO (no se ejecuta nada).
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories import live_sync_repository as runs
from app.services.polling_eligibility import polling_eligibility

LIVE_STATUSES = ("1H", "HT", "2H", "ET", "BT", "P", "LIVE")
SUSPENDED_STATUSES = ("SUSP", "INT")
SEVERITY_ORDER = {"PASS": 0, "INFO": 0, "WARNING": 1, "ERROR": 2}
MAX_SAMPLES = 5


def _check(check_id: str, status: str, detail: str, count: int = 0, samples: list | None = None) -> dict:
    return {"id": check_id, "status": status, "count": count, "detail": detail, "samples": (samples or [])[:MAX_SAMPLES]}


def _by_age(check_id: str, rows: list, warn_detail: str) -> dict:
    """rows: [(external_id, hours_overdue)]. ERROR si alguno pasa de 24 h, WARNING si hay alguno."""
    if not rows:
        return _check(check_id, "PASS", warn_detail + ": 0")
    worst = max(h for _, h in rows)
    status = "ERROR" if worst > 24 else "WARNING"
    return _check(check_id, status, f"{warn_detail}: {len(rows)} (el más antiguo hace {worst:.1f} h)", len(rows), [e for e, _ in rows])


def eligible_current_seasons(db: Session, today) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT s.id, s.year, s.start_date, c.name, c.type FROM seasons s JOIN competitions c ON c.id = s.competition_id "
            "WHERE s.is_current ORDER BY c.id"
        )
    ).mappings().all()
    return [dict(r) for r in rows if polling_eligibility(db, r["id"], today).eligible]


def run_checks(db: Session, now: datetime) -> dict:
    today = now.date()
    seasons = eligible_current_seasons(db, today)
    ids = [s["id"] for s in seasons] or [-1]
    q = lambda sql, **k: db.execute(text(sql), {"ids": ids, "now": now, **k}).all()  # noqa: E731
    checks = []

    overdue = q(
        "SELECT external_id, EXTRACT(EPOCH FROM (:now - kickoff_at)) / 3600 FROM fixtures "
        "WHERE season_id = ANY(:ids) AND status_short IN ('NS', 'TBD') AND kickoff_at < :now - interval '3 hours' ORDER BY kickoff_at"
    )
    checks.append(_by_age("F1_ns_overdue", [(e, float(h)) for e, h in overdue], "Partidos NS/TBD con kickoff hace más de 3 h"))

    live = q(
        "SELECT external_id, EXTRACT(EPOCH FROM (:now - kickoff_at)) / 3600 FROM fixtures "
        "WHERE season_id = ANY(:ids) AND status_short = ANY(:live) AND kickoff_at < :now - interval '4 hours' ORDER BY kickoff_at",
        live=list(LIVE_STATUSES),
    )
    checks.append(_by_age("F2_live_stale", [(e, float(h)) for e, h in live], "Partidos en vivo con kickoff hace más de 4 h"))

    suspended = q(
        "SELECT external_id FROM fixtures WHERE season_id = ANY(:ids) AND status_short = ANY(:susp) "
        "AND kickoff_at < :now - interval '24 hours' ORDER BY kickoff_at",
        susp=list(SUSPENDED_STATUSES),
    )
    checks.append(
        _check("F3_suspended", "WARNING" if suspended else "PASS", f"Partidos SUSP/INT desde hace más de 24 h: {len(suspended)}", len(suspended), [r[0] for r in suspended])
    )

    seen = q(
        "SELECT f.season_id, EXTRACT(EPOCH FROM (:now - max(m.last_seen_at))) / 3600 FROM fixtures f "
        "JOIN fixture_provider_mappings m ON m.fixture_id = f.id WHERE f.season_id = ANY(:ids) GROUP BY f.season_id"
    )
    stale = [(sid, float(h)) for sid, h in seen if h is not None and h > 2]
    checks.append(_by_age("F4_last_seen_stale", stale, "Temporadas actuales sin ver al proveedor hace más de 2 h"))

    counts = dict(q("SELECT season_id, count(*) FROM fixtures WHERE season_id = ANY(:ids) GROUP BY season_id"))
    empty = [s for s in seasons if counts.get(s["id"], 0) == 0]
    empty_errors = [s for s in empty if s["type"] == "League" and s["start_date"] is not None and s["start_date"] <= today]
    checks.append(
        _check(
            "F5_current_without_fixtures",
            "ERROR" if empty_errors else ("WARNING" if empty else "PASS"),
            f"Temporadas actuales elegibles sin partidos: {len(empty)} (ligas ya empezadas: {len(empty_errors)})",
            len(empty),
            [f"{s['name']} {s['year']}" for s in empty],
        )
    )

    last = runs.latest_finished_run(db, "fixtures")
    if last is None or last.status not in ("completed", "completed_with_errors"):
        checks.append(_check("F6_last_fixtures_sync", "WARNING", "No hay ninguna sync de fixtures terminada registrada"))
    else:
        hours = (now - last.finished_at).total_seconds() / 3600
        status = "ERROR" if hours > 24 else ("WARNING" if hours > 2 else "PASS")
        checks.append(_check("F6_last_fixtures_sync", status, f"Última sync de fixtures (run #{last.id}) hace {hours:.1f} h"))

    if last is None:
        checks.append(_check("F7_provider_failures", "PASS", "Sin sync de fixtures registrada"))
    elif last.auth_failed:
        checks.append(_check("F7_provider_failures", "ERROR", f"Run #{last.id}: el proveedor rechazó las credenciales"))
    elif last.rate_limited or last.competitions_failed:
        checks.append(
            _check("F7_provider_failures", "WARNING", f"Run #{last.id}: {last.competitions_failed} competiciones fallidas, límite={last.rate_limited}", last.competitions_failed)
        )
    else:
        checks.append(_check("F7_provider_failures", "PASS", f"Run #{last.id}: sin fallos del proveedor"))

    old_pst = q(
        "SELECT external_id FROM fixtures WHERE season_id = ANY(:ids) AND status_short IN ('PST', 'TBD') "
        "AND kickoff_at < :now - interval '7 days' ORDER BY kickoff_at"
    )
    checks.append(_check("F8_old_postponed", "INFO", f"PST/TBD con kickoff de hace más de 7 días: {len(old_pst)}", len(old_pst), [r[0] for r in old_pst]))

    pending = db.execute(
        text(
            "SELECT c.name, s.year FROM seasons s JOIN competitions c ON c.id = s.competition_id "
            "WHERE NOT s.is_current AND s.end_date < :today "
            "AND EXISTS (SELECT 1 FROM fixtures f WHERE f.season_id = s.id) "
            "AND NOT EXISTS (SELECT 1 FROM season_backfill_runs r WHERE r.competition_id = c.id AND r.requested_year = s.year AND r.status = 'completed') "
            "ORDER BY c.name, s.year"
        ),
        {"today": today},
    ).all()
    checks.append(
        _check("F9_pending_reconciliation", "INFO", f"Temporadas cerradas con datos y sin backfill completed: {len(pending)}", len(pending), [f"{n} {y}" for n, y in pending])
    )

    overall = max((c["status"] for c in checks), key=lambda s: SEVERITY_ORDER[s])
    overall = "PASS" if overall == "INFO" else overall
    return {
        "status": overall,
        "checked_at": now.isoformat(),
        "eligible_seasons": len(seasons),
        "warnings": sum(1 for c in checks if c["status"] == "WARNING"),
        "errors": sum(1 for c in checks if c["status"] == "ERROR"),
        "checks": checks,
    }
