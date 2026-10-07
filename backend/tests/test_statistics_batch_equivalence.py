"""M5.4D: equivalencia del camino por lotes con el camino por partido (oráculo) y número de
sentencias SQL por lote. Proveedor falso; solo BD de tests.

Cada escenario es una secuencia de ejecuciones (proveedor falso, reloj fijo, opciones). Se ejecuta
con el ORÁCULO (tests/statistics_legacy_oracle.py: el service de cbc00dd, por partido), se toma
una foto normalizada de la BD, se borran las tablas de estadísticas y se ejecuta lo mismo con el
service actual (por lotes). Las dos fotos tienen que ser idénticas.

Lo único que se normaliza son los identificadores de secuencia (ids de runs, observaciones y
filas). Los ids de partidos y equipos son los mismos en las dos ejecuciones (mismo seed).
updated_at: antes de cada ejecución se marca con un centinela; la foto registra qué filas lo
cambiaron (en un test todo now() es el mismo instante).
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest
from sqlalchemy import event, func, select, text, update

from app.integrations.exceptions import ProviderResponseError
from app.models import Fixture, FixtureStatisticsObservation, FixtureTeamStatistics, Season, StatisticsRun
from app.schemas.statistics import STATISTIC_FIELDS
from app.services import statistics_service as new_impl
from tests import statistics_legacy_oracle as old_impl
from tests.test_statistics_service import (  # noqa: F401  (season es un fixture)
    AWAY_FULL,
    HOME_FULL,
    PROVIDER,
    FakeStatsProvider,
    entry,
    full_item,
    item,
    replace,
    season,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 6, 8, 0, 0, 250000, tzinfo=timezone.utc)
SENTINEL = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass
class Step:
    items: dict[int, Any]
    at: datetime = T0
    options: dict[str, Any] = field(default_factory=dict)
    hook: Callable | None = None
    errors: list | None = None
    extra: list | None = None


# --- Foto normalizada ------------------------------------------------------------------------


def snapshot(db) -> dict:
    db.expire_all()
    runs = list(db.scalars(select(StatisticsRun).order_by(StatisticsRun.id)))
    run_index = {r.id: i for i, r in enumerate(runs)}
    obs = list(db.scalars(select(FixtureStatisticsObservation).order_by(FixtureStatisticsObservation.id)))
    obs_key = {o.id: (o.fixture_id, o.observed_at, o.payload_hash) for o in obs}
    rows = list(db.scalars(select(FixtureTeamStatistics).order_by(FixtureTeamStatistics.fixture_id, FixtureTeamStatistics.team_id)))
    skip = {"id", "started_at", "finished_at"}
    return {
        "runs": [{c.name: getattr(r, c.name) for c in StatisticsRun.__table__.columns if c.name not in skip} for r in runs],
        "observations": sorted(
            (
                o.fixture_id, o.provider, o.provider_fixture_id, run_index.get(o.run_id), o.source, o.fixture_status_at_fetch,
                o.availability, o.teams_returned, o.payload_hash, repr(o.payload), o.observed_at, o.last_observed_at,
                o.available_at, o.is_latest,
            )
            for o in obs
        ),
        "rows": [
            (r.fixture_id, r.team_id, r.provider, r.side, r.normalizer_version, obs_key[r.observation_id], tuple(getattr(r, f) for f in STATISTIC_FIELDS))
            for r in rows
        ],
    }


def wipe(db) -> None:
    db.execute(text("DELETE FROM fixture_team_statistics"))
    db.execute(text("DELETE FROM fixture_statistics_observations"))
    db.execute(text("DELETE FROM statistics_runs"))
    db.commit()


def execute(db, season, impl, steps: list[Step], restore: Callable | None) -> dict:
    """Ejecuta los pasos con una implementación y devuelve la foto + resultados por paso."""
    results = []
    for step in steps:
        db.execute(update(FixtureTeamStatistics).values(updated_at=SENTINEL))
        db.commit()
        provider = FakeStatsProvider(step.items, hook=step.hook, errors=list(step.errors or []), extra=step.extra)
        options = impl.BackfillOptions(**{"mode": "apply", **step.options})
        try:
            out = asyncio.run(impl.run_season_backfill(
                db, competition_id=season["competition_id"], season_id=season["season_id"],
                provider=provider, options=options, clock=lambda at=step.at: at))
            result = ("ok", out.status, out.stop_reason, out.counters)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            result = ("raised", type(exc).__name__, str(exc))
        db.expire_all()
        bumped = sorted((r.fixture_id, r.team_id) for r in db.scalars(select(FixtureTeamStatistics)) if r.updated_at != SENTINEL)
        results.append((result, provider.calls, bumped))
        if restore:
            restore()
    return {"results": results, **snapshot(db)}


def assert_equivalent(db, season, steps: list[Step], restore: Callable | None = None) -> dict:
    old = execute(db, season, old_impl, steps, restore)
    wipe(db)
    new = execute(db, season, new_impl, steps, restore)
    assert new["results"] == old["results"]
    assert new["observations"] == old["observations"]
    assert new["rows"] == old["rows"]
    assert new["runs"] == old["runs"]
    return new


def fulls(matches):
    return {fid: full_item(fid, h, a) for fid, h, a in matches}


def set_current(db, season):
    db.execute(update(Season).where(Season.id == season["season_id"]).values(is_current=True, end_date=None))
    db.commit()


# --- Matriz de equivalencia ------------------------------------------------------------------

T1, T2, T3 = T0 + timedelta(hours=1), T0 + timedelta(hours=2), T0 + timedelta(hours=3)
REFRESH = {"refresh": True}


def test_first_available(db_session, season):  # 1 + 19 (política histórica)
    snap = assert_equivalent(db_session, season, [Step(fulls(season["add"](3)))])
    assert len(snap["observations"]) == 3 and len(snap["rows"]) == 6


def test_same_hash_and_idempotence(db_session, season):  # 2 + 25
    items = fulls(season["add"](2))
    snap = assert_equivalent(db_session, season, [Step(items), Step(items, T1, REFRESH), Step(items, T2)])
    assert [r[0][3]["observations_unchanged"] for r in snap["results"]] == [0, 2, 0]
    assert snap["results"][2][1] == []  # sin --refresh: 0 peticiones


def test_a_b_a_versioning(db_session, season):  # 3
    (f, h, a), = season["add"](1)
    a_item, b_item = full_item(f, h, a), full_item(f, h, a, home_stats=replace(HOME_FULL, **{"Corner Kicks": 9}))
    snap = assert_equivalent(db_session, season, [Step({f: a_item}), Step({f: b_item}, T1, REFRESH), Step({f: a_item}, T2, REFRESH)])
    assert len(snap["observations"]) == 3 and sum(o[-1] for o in snap["observations"]) == 1


@pytest.mark.parametrize(
    "second",
    [
        lambda f, h, a: item(f, entry(h, HOME_FULL), entry(a, [])),  # 4 available→partial
        lambda f, h, a: item(f, entry(h, []), entry(a, [])),  # 5 available→empty
        lambda f, h, a: item(f),  # 5b available→empty `[]`
        lambda f, h, a: full_item(f, h, a, home_stats=replace(HOME_FULL, Fouls=None, **{"Total Shots": None})),  # 7 value→NULL
        lambda f, h, a: full_item(f, h, a, home_stats=replace(HOME_FULL, **{"Red Cards": 2})),  # 8 NULL→value
        lambda f, h, a: full_item(f, h, a, away_stats=replace(AWAY_FULL, **{"Corner Kicks": 0, "Ball Possession": "44.5%"})),  # 9
        lambda f, h, a: full_item(f, h, a, home_stats=HOME_FULL + [("Expected Assists", "0.3")]),  # 10 solo re-enlace
        lambda f, h, a: full_item(f, h, a, home_stats=[(t, v) for t, v in HOME_FULL if t != "Offsides"]),  # tipo ausente
    ],
)
def test_transitions_after_available(db_session, season, second):
    (f, h, a), = season["add"](1)
    assert_equivalent(db_session, season, [Step({f: full_item(f, h, a)}), Step({f: second(f, h, a)}, T1, REFRESH)])


def test_partial_then_available(db_session, season):  # 6
    (f, h, a), = season["add"](1)
    assert_equivalent(db_session, season, [Step({f: item(f, entry(h, HOME_FULL), entry(a, []))}), Step({f: full_item(f, h, a)}, T1, REFRESH)])


@pytest.mark.parametrize(
    "make",
    [
        lambda f, h, a: full_item(f, h, a, home_stats=replace(HOME_FULL, **{"Corner Kicks": -2})),  # 11 valor
        lambda f, h, a: full_item(f, h, a, away_stats=replace(AWAY_FULL, **{"Ball Possession": "120%"})),
        lambda f, h, a: item(f, entry(None, HOME_FULL), entry(a, AWAY_FULL)),  # 11 equipo sin id
        lambda f, h, a: item(f, entry(h, HOME_FULL), entry(99999, AWAY_FULL)),  # 14 equipo sin mapping
        lambda f, h, a: item(f, entry(h, HOME_FULL), entry(h, AWAY_FULL)),  # equipo duplicado
        lambda f, h, a: item(f, entry(h, HOME_FULL), entry(a, AWAY_FULL), entry(77, HOME_FULL)),  # 15 > 2 equipos
        lambda f, h, a: full_item(f, h, a, home_stats=replace(HOME_FULL, Fouls="-", **{"Total Shots": 40})),  # unparseable + coherencia
    ],
)
def test_blocked_and_quality(db_session, season, make):
    (f, h, a), (f2, h2, a2) = season["add"](2)
    assert_equivalent(db_session, season, [Step({f: make(f, h, a), f2: full_item(f2, h2, a2)})])


def test_foreign_team(db_session, season):
    (f, h, _a), (f2, h2, a2) = season["add"](2)
    assert_equivalent(db_session, season, [Step({f: item(f, entry(h, HOME_FULL), entry(h2, AWAY_FULL)), f2: full_item(f2, h2, a2)})])


def test_mapping_changed_after_http(db_session, season):  # 12
    matches = season["add"](2)
    f = matches[0][0]

    def deactivate(_ids):
        db_session.execute(text("UPDATE fixture_provider_mappings SET is_active = false WHERE external_id = :e"), {"e": str(f)})
        db_session.commit()

    def restore():
        db_session.execute(text("UPDATE fixture_provider_mappings SET is_active = true WHERE external_id = :e"), {"e": str(f)})
        db_session.commit()

    assert_equivalent(db_session, season, [Step(fulls(matches), hook=deactivate)], restore)


def test_fixture_becomes_non_final(db_session, season):  # 13
    matches = season["add"](2)
    f = matches[0][0]

    def to_ns(_ids):
        db_session.execute(update(Fixture).where(Fixture.external_id == f).values(status_short="NS"))
        db_session.commit()

    def restore():
        db_session.execute(update(Fixture).where(Fixture.external_id == f).values(status_short="FT"))
        db_session.commit()

    assert_equivalent(db_session, season, [Step(fulls(matches), hook=to_ns)], restore)


def test_missing_unexpected_and_duplicated_in_response(db_session, season):  # 16 + 17 + 18
    (f1, h, a), (f2, h2, a2), (f3, h3, a3) = season["add"](3)
    extra = [full_item(5555, h, a), full_item(f3, h3, a3)]  # 5555 no pedido; f3 llega dos veces
    assert_equivalent(db_session, season, [Step({f1: full_item(f1, h, a), f3: full_item(f3, h3, a3)}, extra=extra)])


def test_operational_policy_and_empty(db_session, season):  # 20 + 21
    matches = season["add"](3)
    set_current(db_session, season)
    (f1, h, a), (f2, h2, a2), (f3, _h3, _a3) = matches
    items = {f1: full_item(f1, h, a), f2: item(f2, entry(h2, []), entry(a2, [])), f3: item(f3)}
    changed = {**items, f1: full_item(f1, h, a, home_stats=replace(HOME_FULL, Fouls=3))}
    opts = {"allow_operational_season": True}
    snap = assert_equivalent(db_session, season, [Step(items, T1, opts), Step(changed, T2, {**opts, **REFRESH})])
    assert all(o[4] == "manual" and o[10] == o[12] for o in snap["observations"])


def test_out_of_order_observation(db_session, season):  # 22
    (f, h, a), = season["add"](1)
    changed = full_item(f, h, a, home_stats=replace(HOME_FULL, Fouls=1))
    snap = assert_equivalent(db_session, season, [Step({f: full_item(f, h, a)}, T2), Step({f: changed}, T1, REFRESH)])
    assert snap["results"][1][0][:2] == ("raised", "ValueError")


def test_budget_stop_cursor_and_resume(db_session, season):  # 23 + 24
    items = fulls(season["add"](25))
    snap = assert_equivalent(db_session, season, [
        Step(items, T1, {"stats_daily_budget": 3}),
        Step(items, T2, {"resume": True}),
    ])
    assert snap["results"][0][0][2] == "budget_exhausted" and len(snap["observations"]) == 25


def test_provider_error_and_resume(db_session, season):
    items = fulls(season["add"](25))
    assert_equivalent(db_session, season, [
        Step(items, T1, errors=[None, ProviderResponseError(PROVIDER, "502")]),
        Step(items, T2, {"resume": True}),
    ])


def test_dry_run(db_session, season):  # 26
    (f, h, a), (f2, h2, a2) = season["add"](2)
    changed = {f: full_item(f, h, a, home_stats=replace(HOME_FULL, **{"Corner Kicks": 1})), f2: full_item(f2, h2, a2)}
    assert_equivalent(db_session, season, [
        Step({f: full_item(f, h, a)}, T0, {"mode": "dry_run"}),
        Step({f: full_item(f, h, a)}, T1),
        Step(changed, T2, {"mode": "dry_run", "refresh": True}),
    ])


def test_mixed_batch_of_twenty(db_session, season):  # 27
    matches = season["add"](20)
    items = {}
    for i, (f, h, a) in enumerate(matches):
        kind = i % 7
        items[f] = [
            full_item(f, h, a),
            item(f, entry(h, HOME_FULL), entry(a, [])),
            item(f, entry(h, []), entry(a, [])),
            item(f),
            full_item(f, h, a, home_stats=replace(HOME_FULL, **{"Shots on Goal": -1})),
            item(f, entry(h, HOME_FULL), entry(424242, AWAY_FULL)),
            full_item(f, h, a, away_stats=replace(AWAY_FULL, **{"Ball Possession": "0%"})),
        ][kind]
    missing = matches[-1][0]
    del items[missing]
    second = {f: (full_item(f, h, a, home_stats=replace(HOME_FULL, Fouls=None)) if i % 2 else items.get(f, full_item(f, h, a))) for i, (f, h, a) in enumerate(matches)}
    snap = assert_equivalent(db_session, season, [Step(items, T1, extra=[full_item(9999, 1, 2)]), Step(second, T2, REFRESH)])
    assert snap["results"][0][0][3]["fixtures_attempted"] == 20


# --- Número de sentencias por lote --------------------------------------------------------------

DML = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")


@pytest.fixture
def statements(db_session):
    """Cuenta las sentencias que llegan al cursor (before_cursor_execute). En los tests los commit
    del código son RELEASE/SAVEPOINT (se cuentan aparte); en producción BEGIN/COMMIT no pasan por
    el cursor (psycopg los envía por su cuenta) y no se ven aquí."""
    log: list[str] = []
    conn = db_session.connection()

    def listener(_conn, _cursor, statement, _params, _context, executemany):
        log.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(conn, "before_cursor_execute", listener)
    yield log
    event.remove(conn, "before_cursor_execute", listener)


def _count(log, start):
    part = log[start:]
    return sum(1 for s in part if s in DML), sum(1 for s in part if s not in DML)


def _measure(db, season, impl, items, at, **options):
    db.commit()
    log = _measure.log
    start = len(log)
    provider = FakeStatsProvider(items)
    asyncio.run(impl.run_season_backfill(
        db, competition_id=season["competition_id"], season_id=season["season_id"], provider=provider,
        options=impl.BackfillOptions(**{"mode": "apply", **options}), clock=lambda: at))
    return _count(log, start)


def _fresh(db, season, n, start):
    """La temporada pasa a tener EXACTAMENTE n partidos FT (ids externos desde `start`), sin estadísticas."""
    wipe(db)
    db.execute(text("DELETE FROM fixtures WHERE season_id = :s"), {"s": season["season_id"]})
    db.commit()
    return season["add"](n, start=start)


@pytest.mark.parametrize("mode", ["apply", "dry_run"])
def test_statements_per_batch_are_constant(db_session, season, statements, mode):
    _measure.log = statements
    results = {}
    for n, start in ((1, 3001), (20, 4001), (40, 5001)):
        results[n] = _measure(db_session, season, new_impl, fulls(_fresh(db_session, season, n, start)), T1, mode=mode)
    per_batch = results[40][0] - results[20][0]  # un lote más de 20 partidos available
    limit = 16 if mode == "apply" else 10
    print(f"[{mode}] sentencias (DML/SELECT, control de tx) por run: 1 partido={results[1]} 20={results[20]} 40={results[40]}; "
          f"por lote de 20 = {per_batch}")
    assert per_batch <= limit
    assert results[20][0] == results[1][0]  # O(lotes), no O(partidos): 1 y 20 partidos = 1 lote


def test_old_path_is_n_plus_one_for_reference(db_session, season, statements):
    _measure.log = statements
    a = _measure(db_session, season, old_impl, fulls(_fresh(db_session, season, 20, 4001)), T1)
    b = _measure(db_session, season, old_impl, fulls(_fresh(db_session, season, 40, 5001)), T1)
    print(f"[oráculo apply] 20={a} 40={b}; por lote de 20={b[0] - a[0]}")
    assert b[0] - a[0] > 200  # el camino antiguo crece con los partidos (≈ 12 por partido)


def test_empty_batch_statements(db_session, season, statements):
    _measure.log = statements
    results = {}
    for n, start in ((20, 4001), (40, 5001)):
        matches = _fresh(db_session, season, n, start)
        empties = {f: (item(f) if i % 2 else item(f, entry(h, []), entry(a, []))) for i, (f, h, a) in enumerate(matches)}
        results[n] = _measure(db_session, season, new_impl, empties, T1)
    print(f"[apply empty] 20={results[20]} 40={results[40]}; por lote de 20={results[40][0] - results[20][0]}")
    assert results[40][0] - results[20][0] <= 16
