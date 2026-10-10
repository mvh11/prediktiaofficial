"""DI-A6 Checkpoint C (G2, MEASUREMENT_FIRST): coste del escritor con evidencia y de la lectura temporal.

Mide el escritor REAL (ensure_teams + upsert_fixtures + mappings) con el mismo arnés sobre dos
árboles de código: la línea base pre-A6 (esquema 0007, sin evidencia) y A6 (0008). El modo de
cada árbol se detecta por la firma de upsert_fixtures; los escenarios que solo existen con
evidencia (older, tie, replay) se marcan NO APLICA en la línea base.

Por muestra, fuera del reloj: LSN de WAL antes/después, contadores de la transacción
(pg_stat_xact_user_tables, incluidos los HOT) y validaciones. Dentro del reloj, por familia de
sentencia: tiempo SQLAlchemy previo al cursor (clave de caché/compilación/parámetros) y
round-trip del cursor. Sin providers, sin UNNEST, sin cambios de producción.
"""

import random
import time
import tracemalloc
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session

from tools.perf_lab.benchmark import (
    delete_lab_fixtures, distribution, writer_takes_evidence,
)
from tools.perf_lab.dataset import (
    COMPETITION_EXTERNAL_BASE, TEAM_EXTERNAL_BASE, DatasetConfig, analyze, fixture_at,
)

A6_BATCHES = (10, 100, 380, 1000, 2000)
WRITE_MODES = ("insert", "update", "unchanged", "older", "mixed", "tie", "replay")
EVIDENCE_ONLY_MODES = {"older", "tie", "replay"}
INSERT_OFFSET = 100_000_000
MIXED_INSERT_OFFSET = 200_000_000
CEILING_OFFSET = 300_000_000
CEILING_SIZES = (10, 100, 380, 1000, 2000, 2500, 2700, 2730, 2731, 2750, 2978, 2979, 3000, 3120, 3121, 4000)
TRACKED_TABLES = ("fixtures", "fixture_observations", "fixture_provider_mappings", "team_provider_mappings", "teams")
STATE_COLUMNS = (
    "kickoff_at", "status_short", "season_id", "home_team_id", "away_team_id",
    "home_goals", "away_goals", "halftime_home", "halftime_away", "fulltime_home", "fulltime_away",
    "extratime_home", "extratime_away", "penalty_home", "penalty_away",
)


def statement_family(sql: str) -> str:
    """Familia de una sentencia del escritor (o de la lectura temporal) por su texto."""
    head = sql.lstrip()[:120].upper()
    if head.startswith("SELECT") and "fixture_state_hash_v1" in sql[:600]:
        return "hash_select"
    for prefix, family in (
        ("INSERT INTO FIXTURES ", "fixtures_upsert"), ("INSERT INTO FIXTURE_OBSERVATIONS", "observations_insert"),
        ("INSERT INTO FIXTURE_PROVIDER_MAPPINGS", "fixture_mappings_upsert"), ("INSERT INTO TEAMS ", "teams_insert"),
        ("INSERT INTO TEAM_PROVIDER_MAPPINGS", "team_mappings_upsert"),
    ):
        if head.startswith(prefix):
            return family
    if head.startswith("SELECT"):
        lowered = sql[:2000].lower()
        for table in ("fixture_observations", "fixtures", "teams"):
            if f"from {table} " in lowered or f"from {table}\n" in lowered:
                return f"select_{table}"
        return "select_other"
    return "other"


def bind_parameter_count(parameters) -> int:
    if isinstance(parameters, dict):
        return len(parameters)
    if isinstance(parameters, (list, tuple)):
        return len(parameters)
    return 0


class StatementMeter:
    """Por familia: ns previos al cursor (before_execute -> before_cursor_execute), ns de cursor,
    sentencias y máximo de parámetros enlazados en una sentencia."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.enabled = False
        self.reset()

    def reset(self):
        self.pre_ns = defaultdict(int)
        self.cursor_ns = defaultdict(int)
        self.count = defaultdict(int)
        self.max_params = 0
        self._pre_started = None

    def before_execute(self, conn, *_args):
        if self.enabled:
            self._pre_started = time.perf_counter_ns()

    def before_cursor(self, conn, cursor, statement, parameters, context, executemany):
        if not self.enabled:
            return
        now = time.perf_counter_ns()
        family = statement_family(statement)
        if self._pre_started is not None:
            self.pre_ns[family] += now - self._pre_started
            self._pre_started = None
        self.max_params = max(self.max_params, bind_parameter_count(parameters))
        context._a6_family = family
        context._a6_started = time.perf_counter_ns()

    def after_cursor(self, conn, cursor, statement, parameters, context, executemany):
        if self.enabled and hasattr(context, "_a6_started"):
            self.cursor_ns[context._a6_family] += time.perf_counter_ns() - context._a6_started
            self.count[context._a6_family] += 1

    def __enter__(self):
        event.listen(self.engine, "before_execute", self.before_execute)
        event.listen(self.engine, "before_cursor_execute", self.before_cursor)
        event.listen(self.engine, "after_cursor_execute", self.after_cursor)
        return self

    def __exit__(self, *_):
        event.remove(self.engine, "before_execute", self.before_execute)
        event.remove(self.engine, "before_cursor_execute", self.before_cursor)
        event.remove(self.engine, "after_cursor_execute", self.after_cursor)


def maintenance_engine(engine: Engine) -> Engine:
    """Conexión aparte en autocommit para LSN, tamaños y preparación: no entra en la transacción medida."""
    return create_engine(engine.url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})


def wal_lsn(conn) -> int:
    return conn.exec_driver_sql("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), '0/0')::bigint").scalar_one()


def relation_sizes(conn) -> dict:
    sizes = {}
    for table in TRACKED_TABLES:
        exists = conn.exec_driver_sql(f"SELECT to_regclass('{table}') IS NOT NULL").scalar_one()
        if exists:
            sizes[table] = dict(conn.exec_driver_sql(
                f"SELECT pg_relation_size('{table}') AS heap_bytes, pg_indexes_size('{table}') AS index_bytes, "
                f"pg_total_relation_size('{table}') AS total_bytes"
            ).mappings().one())
    return sizes


def xact_tuple_stats(db: Session) -> dict:
    """Contadores pendientes del backend (pg_stat_xact_user_tables). Desde PG15 incluyen
    transacciones anteriores aún no volcadas, así que se leen antes y después del trabajo dentro
    de la MISMA transacción (el volcado solo ocurre con el backend inactivo) y se resta."""
    rows = db.execute(text(
        "SELECT relname, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del FROM pg_stat_xact_user_tables "
        "WHERE relname = ANY(:t)"
    ), {"t": list(TRACKED_TABLES)}).mappings()
    return {r["relname"]: {k: r[k] for k in ("n_tup_ins", "n_tup_upd", "n_tup_hot_upd", "n_tup_del")} for r in rows}


# --- Payloads ---------------------------------------------------------------------------------


def _fixture_data(config: DatasetConfig, index: int, *, offset=0, venue=None, kickoff_shift=0):
    from app.schemas.catalog import TeamData
    from app.schemas.fixture import FixtureData

    row = fixture_at(index, config)
    sid = row["season_id"]
    league = (sid - 1) // 10 + 1
    fields = {k: v for k, v in row.items() if k not in {"id", "season_id", "home_team_id", "away_team_id"}}
    fields["external_id"] += offset
    if venue is not None:
        fields["venue_name"] += f" revision {venue}"
    if kickoff_shift:
        fields["kickoff_at"] += timedelta(minutes=kickoff_shift)  # cambia el estado (y el hash)
    data = FixtureData(
        **fields, competition_external_id=COMPETITION_EXTERNAL_BASE + league, season=config.year(sid),
        home_team=TeamData(external_id=TEAM_EXTERNAL_BASE + row["home_team_id"], name=f"Equipo laboratorio {row['home_team_id']}"),
        away_team=TeamData(external_id=TEAM_EXTERNAL_BASE + row["away_team_id"], name=f"Equipo laboratorio {row['away_team_id']}"),
    )
    return sid, data


def groups_for(config: DatasetConfig, batch: int, mode: str, variant: int) -> dict:
    """{season_id: [FixtureData]}: cada grupo es una respuesta lógica (una temporada)."""
    groups = defaultdict(list)
    for index in range(batch):
        kwargs = {}
        if mode == "insert":
            kwargs["offset"] = INSERT_OFFSET
        elif mode in ("update", "older"):
            kwargs["venue"] = variant
        elif mode == "tie":
            kwargs["kickoff_shift"] = 1 + variant  # dos estados distintos en el mismo instante
        elif mode == "mixed":
            third = index % 3
            if third == 0:
                kwargs["offset"] = MIXED_INSERT_OFFSET
            elif third == 1:
                kwargs["venue"] = variant
        sid, data = _fixture_data(config, index, **kwargs)
        groups[sid].append(data)
    return dict(groups)


def new_external_ids(groups: dict) -> list[int]:
    return [f.external_id for fs in groups.values() for f in fs if f.external_id >= INSERT_OFFSET]


class EvidenceClock:
    """Instantes de evidencia del laboratorio. Más nueva = reloj real (como received()); más
    antigua = antes de cualquier last_observed_at existente; empate = un instante fijo."""

    def __init__(self, oldest_current: datetime | None):
        # FixtureEvidence exige UTC: la BD devuelve el instante en la zona de la sesión
        self.older_base = (oldest_current or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=30)
        self.older_step = 0

    @staticmethod
    def newer():
        from app.schemas.fixture_evidence import FixtureEvidence

        return FixtureEvidence.received("sync", "api-football")

    def older(self):
        from app.schemas.fixture_evidence import FixtureEvidence

        self.older_step += 1
        return FixtureEvidence("sync", "api-football", self.older_base - timedelta(microseconds=self.older_step))

    @staticmethod
    def at(instant: datetime):
        from app.schemas.fixture_evidence import FixtureEvidence

        return FixtureEvidence("sync", "api-football", instant)


def write_groups(db: Session, groups: dict, evidences: dict | None) -> dict:
    """El camino real del laboratorio: ensure_teams + upsert_fixtures por grupo, sin commit."""
    from app.repositories.fixture_repository import ensure_teams, upsert_fixtures

    totals = defaultdict(int)
    for season_id, fixtures in groups.items():
        teams = [f.home_team for f in fixtures] + [f.away_team for f in fixtures]
        ids = ensure_teams(db, teams, "api-football")
        extra = (evidences[season_id],) if evidences is not None else ()
        counts = upsert_fixtures(db, season_id, fixtures, ids, "api-football", *extra)
        for key in ("received", "created", "updated", "unchanged"):
            totals[key] += getattr(counts, key)
    return dict(totals)


# --- Escrituras -------------------------------------------------------------------------------


def stats_delta(before: dict, after: dict) -> dict:
    return {t: {k: after[t][k] - before.get(t, {}).get(k, 0) for k in after[t]} for t in after}


def _evidences(groups: dict, factory) -> dict:
    return {sid: factory() for sid in groups}


def _validate_case(maint, config: DatasetConfig, batch: int, mode: str, evidence_mode: bool, snapshot) -> dict:
    """Invariantes A6 fuera del reloj sobre los partidos del caso (y la línea base: valores)."""
    checks = {}
    ext = [fixture_at(i, config)["external_id"] for i in range(batch)]
    if evidence_mode:
        # El estado ganador de cada partido es una observación real: (last_observed_at,
        # last_state_hash) aparece en fixture_observations
        checks["winner_is_an_observation_violations"] = maint.exec_driver_sql(
            "SELECT count(*) FROM fixtures f WHERE f.external_id = ANY(%(e)s) AND NOT EXISTS ("
            "SELECT 1 FROM fixture_observations o WHERE o.fixture_id = f.id AND o.observed_at = f.last_observed_at "
            "AND o.state_hash = f.last_state_hash)", {"e": ext},
        ).scalar_one()
        assert checks["winner_is_an_observation_violations"] == 0, checks
    if mode == "older":
        after = maint.exec_driver_sql(
            "SELECT external_id, venue_name, last_observed_at FROM fixtures WHERE external_id = ANY(%(e)s) ORDER BY external_id",
            {"e": ext},
        ).all()
        checks["older_evidence_overwrote_rows"] = sum(1 for a, b in zip(after, snapshot) if tuple(a) != tuple(b))
        assert checks["older_evidence_overwrote_rows"] == 0, checks
    mapped = maint.exec_driver_sql(
        "SELECT count(*) FROM fixture_provider_mappings WHERE provider = 'api-football' AND external_id = ANY(%(e)s)",
        {"e": [str(e) for e in ext]},
    ).scalar_one()
    checks["mappings_present"] = mapped
    assert mapped == batch, checks
    return checks


def measure_write_case(engine: Engine, maint: Engine, config: DatasetConfig, batch: int, mode: str,
                       samples: int, warmup: int) -> dict:
    evidence_mode = writer_takes_evidence()
    if mode in EVIDENCE_ONLY_MODES and not evidence_mode:
        return {"operation": f"write_{mode}_{batch}", "mode": mode, "batch": batch, "status": "NO APLICA",
                "reason": "el escritor pre-A6 no tiene evidencia ni orden temporal"}
    base = groups_for(config, batch, "unchanged", 0)
    with maint.connect() as m:
        oldest = m.exec_driver_sql(
            "SELECT min(last_observed_at) FROM fixtures" if evidence_mode else "SELECT NULL::timestamptz"
        ).scalar_one()
    clock = EvidenceClock(oldest)
    # Normalización fuera del reloj: estado base con evidencia nueva
    with Session(engine) as db:
        write_groups(db, base, _evidences(base, clock.newer) if evidence_mode else None)
        db.commit()
    tie_instant = None
    replay_evidence = None
    if mode == "tie":
        tie_instant = datetime.now(timezone.utc) + timedelta(seconds=1)
        setup = groups_for(config, batch, "tie", 1)
        with Session(engine) as db:
            write_groups(db, setup, _evidences(setup, lambda: clock.at(tie_instant)))
            db.commit()
    if mode == "replay":
        replay_evidence = _evidences(base, clock.newer)
        with Session(engine) as db:
            write_groups(db, base, replay_evidence)
            db.commit()
    analyze(engine)
    snapshot = None
    if mode == "older":
        with maint.connect() as m:
            snapshot = m.exec_driver_sql(
                "SELECT external_id, venue_name, last_observed_at FROM fixtures WHERE external_id = ANY(%(e)s) ORDER BY external_id",
                {"e": [fixture_at(i, config)["external_id"] for i in range(batch)]},
            ).all()

    variants = [groups_for(config, batch, mode, v) for v in (0, 1)]
    raw = defaultdict(list)
    family_pre, family_cursor, family_count = defaultdict(list), defaultdict(list), defaultdict(list)
    xact, counts, wal_bytes, max_params = [], [], [], []
    with maint.connect() as m:
        sizes_before = relation_sizes(m)
    meter = StatementMeter(engine)
    with meter, engine.connect() as conn, maint.connect() as m:
        for i in range(warmup + samples):
            groups = variants[i % 2]
            if mode == "replay":
                evidences = replay_evidence
            elif mode == "older":
                evidences = _evidences(groups, clock.older)
            elif mode == "tie":
                evidences = _evidences(groups, lambda: clock.at(tie_instant))
            elif evidence_mode:
                evidences = _evidences(groups, clock.newer)
            else:
                evidences = None
            lsn_before = wal_lsn(m)
            with Session(bind=conn) as db:
                stats_before = xact_tuple_stats(db)  # fuera del reloj; abre la transacción medida
                meter.reset()
                meter.enabled = True
                started = time.perf_counter_ns()
                totals = write_groups(db, groups, evidences)
                before_commit = time.perf_counter_ns()
                meter.enabled = False
                stats = stats_delta(stats_before, xact_tuple_stats(db))  # fuera del reloj
                commit_started = time.perf_counter_ns()
                db.commit()
                finished = time.perf_counter_ns()
            lsn_after = wal_lsn(m)
            assert totals["received"] == batch, totals
            if mode == "replay":  # la misma respuesta no crea evidencia nueva ni toca fixtures
                assert stats.get("fixture_observations", {}).get("n_tup_ins", 0) == 0, stats
                assert stats.get("fixtures", {}).get("n_tup_upd", 0) == 0, stats
            if mode == "older":  # la evidencia antigua entra en la historia, nunca en fixtures
                assert stats.get("fixture_observations", {}).get("n_tup_ins", 0) == batch, stats
                assert stats.get("fixtures", {}).get("n_tup_upd", 0) == 0, stats
            if mode in ("insert", "mixed"):
                with engine.begin() as cleanup:  # fuera del reloj; la evidencia antes (RESTRICT)
                    delete_lab_fixtures(cleanup, new_external_ids(groups))
            if i < warmup:
                continue
            before_ms = (before_commit - started) / 1e6
            commit_ms = (finished - commit_started) / 1e6
            raw["wall_before_commit"].append(before_ms)
            raw["commit"].append(commit_ms)
            raw["total"].append(before_ms + commit_ms)
            raw["sql_cursor"].append(sum(meter.cursor_ns.values()) / 1e6)
            raw["sqlalchemy_pre_cursor"].append(sum(meter.pre_ns.values()) / 1e6)
            for family in set(meter.cursor_ns) | set(meter.pre_ns):
                family_pre[family].append(meter.pre_ns[family] / 1e6)
                family_cursor[family].append(meter.cursor_ns[family] / 1e6)
                family_count[family].append(meter.count[family])
            xact.append(stats)
            counts.append(totals)
            wal_bytes.append(lsn_after - lsn_before)
            max_params.append(meter.max_params)
    with maint.connect() as m:
        sizes_after = relation_sizes(m)
        checks = _validate_case(m, config, batch, mode, evidence_mode, snapshot)

    def per_table(key):
        return {t: distribution([float(s.get(t, {}).get(key, 0)) for s in xact]) for t in TRACKED_TABLES if any(t in s for s in xact)}

    hash_share = None
    if "hash_select" in family_cursor:
        shares = [(family_pre["hash_select"][k] + family_cursor["hash_select"][k]) / raw["wall_before_commit"][k]
                  for k in range(len(raw["wall_before_commit"]))]
        hash_share = distribution(shares)
    fixtures_upd = [float(s.get("fixtures", {}).get("n_tup_upd", 0)) for s in xact]
    fixtures_hot = [float(s.get("fixtures", {}).get("n_tup_hot_upd", 0)) for s in xact]
    return {
        "operation": f"write_{mode}_{batch}", "mode": mode, "batch": batch, "status": "MEDIDO",
        "season_groups": len(base), "evidence_mode": evidence_mode, "unit": "ms",
        **{f"{k}_ms": distribution(v) for k, v in raw.items()},
        "family_pre_cursor_ms": {f: distribution(v) for f, v in family_pre.items()},
        "family_cursor_ms": {f: distribution(v) for f, v in family_cursor.items()},
        "family_statements": {f: distribution([float(x) for x in v]) for f, v in family_count.items()},
        "hash_share_of_wall_before_commit": hash_share,
        "wal_bytes": distribution([float(w) for w in wal_bytes]),
        "xact_inserted": per_table("n_tup_ins"), "xact_updated": per_table("n_tup_upd"),
        "xact_hot_updated": per_table("n_tup_hot_upd"), "xact_deleted": per_table("n_tup_del"),
        "fixtures_hot_ratio": (sum(fixtures_hot) / sum(fixtures_upd)) if sum(fixtures_upd) else None,
        "upsert_counts": {k: distribution([float(c[k]) for c in counts]) for k in ("received", "created", "updated", "unchanged")},
        "max_bind_parameters_in_one_statement": max(max_params),
        "relation_sizes_before": sizes_before, "relation_sizes_after": sizes_after,
        "checks": checks, "raw_ms": dict(raw), "raw_wal_bytes": wal_bytes,
    }


def measure_writes(engine: Engine, config: DatasetConfig, batches, modes, samples: int, warmup: int) -> list[dict]:
    maint = maintenance_engine(engine)
    results = []
    try:
        for batch in batches:
            for mode in modes:
                if batch > config.fixtures:
                    results.append({"operation": f"write_{mode}_{batch}", "status": "NO MEDIDO", "reason": "dataset menor que lote"})
                    continue
                result = measure_write_case(engine, maint, config, batch, mode, samples, warmup)
                results.append(result)
                if result["status"] == "MEDIDO":
                    print(f"write_{mode}_{batch}: p50 total={result['total_ms']['p50']:.2f} ms "
                          f"wal p50={result['wal_bytes']['p50']:.0f} B hot={result['fixtures_hot_ratio']}", flush=True)
                else:
                    print(f"write_{mode}_{batch}: {result['status']}", flush=True)
        # Deja el dataset en el estado base para lo que venga después
        with Session(engine) as db:
            base = groups_for(config, max(b for b in batches if b <= config.fixtures), "unchanged", 0)
            write_groups(db, base, _evidences(base, EvidenceClock.newer) if writer_takes_evidence() else None)
            db.commit()
    finally:
        maint.dispose()
    return results


# --- Techo de parámetros ----------------------------------------------------------------------


def ceiling_payload(config: DatasetConfig, n: int) -> tuple[int, list]:
    """n partidos NUEVOS en UNA temporada (una sola respuesta lógica), equipos de la liga 1."""
    from app.schemas.catalog import TeamData
    from app.schemas.fixture import FixtureData

    fixtures = []
    for i in range(n):
        home = 1 + i % 20
        away = 1 + (i + 1 + (i // 20) % 19) % 20
        if away == home:
            away = 1 + home % 20
        fixtures.append(FixtureData(
            external_id=CEILING_OFFSET + i, competition_external_id=COMPETITION_EXTERNAL_BASE + 1,
            season=config.year(1), kickoff_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            status_short="FT", home_goals=1, away_goals=0, halftime_home=0, halftime_away=0,
            fulltime_home=1, fulltime_away=0, venue_name=f"Ceiling {i}",
            home_team=TeamData(external_id=TEAM_EXTERNAL_BASE + home, name=f"Equipo laboratorio {home}"),
            away_team=TeamData(external_id=TEAM_EXTERNAL_BASE + away, name=f"Equipo laboratorio {away}"),
        ))
    return 1, fixtures


def _one_ceiling_attempt(engine: Engine, meter: StatementMeter, config: DatasetConfig, n: int, trace_memory: bool) -> dict:
    sid, fixtures = ceiling_payload(config, n)
    evidences = {sid: EvidenceClock.newer()} if writer_takes_evidence() else None
    outcome = {"n": n}
    if trace_memory:
        tracemalloc.start()
    with engine.connect() as conn, Session(bind=conn) as db:
        meter.reset()
        meter.enabled = True
        started = time.perf_counter_ns()
        try:
            totals = write_groups(db, {sid: fixtures}, evidences)
            outcome.update(ok=True, created=totals["created"])
        except Exception as exc:  # el fallo es el dato que se mide
            outcome.update(ok=False, error_type=type(exc).__name__, error=str(exc).splitlines()[0][:300])
        finally:
            outcome["wall_ms"] = (time.perf_counter_ns() - started) / 1e6
            meter.enabled = False
            outcome["max_bind_parameters"] = meter.max_params
            outcome["statements"] = dict(meter.count)
            db.rollback()  # el techo se mide sin dejar rastro
    if trace_memory:
        outcome["python_peak_alloc_mib"] = tracemalloc.get_traced_memory()[1] / 2**20
        tracemalloc.stop()
    return outcome


def measure_ceiling(engine: Engine, config: DatasetConfig, sizes=CEILING_SIZES, repeats: int = 3) -> dict:
    """Experimento: lotes crecientes de UNA respuesta hasta el primer fallo; rollback siempre."""
    attempts = []
    meter = StatementMeter(engine)
    with meter:
        for n in sizes:
            timed = [_one_ceiling_attempt(engine, meter, config, n, trace_memory=False) for _ in range(repeats)]
            memory = _one_ceiling_attempt(engine, meter, config, n, trace_memory=True)
            ok = all(t["ok"] for t in timed)
            entry = {"n": n, "ok": ok, "max_bind_parameters": timed[0]["max_bind_parameters"],
                     "wall_ms": [t["wall_ms"] for t in timed], "python_peak_alloc_mib": memory.get("python_peak_alloc_mib"),
                     "statements": timed[0]["statements"]}
            if not ok:
                entry.update(error_type=timed[0].get("error_type"), error=timed[0].get("error"))
            attempts.append(entry)
            print(f"ceiling n={n}: {'OK' if ok else 'FAIL ' + str(entry.get('error_type'))} "
                  f"params={entry['max_bind_parameters']} wall={min(entry['wall_ms']):.1f} ms", flush=True)
        successes = [a["n"] for a in attempts if a["ok"]]
        failures = [a["n"] for a in attempts if not a["ok"]]
        refine = []
        if successes and failures and min(failures) > max(successes):
            low, high = max(successes), min(failures)
            while high - low > 1:  # búsqueda binaria del último lote que funciona
                mid = (low + high) // 2
                result = _one_ceiling_attempt(engine, meter, config, mid, trace_memory=False)
                refine.append({k: result.get(k) for k in ("n", "ok", "max_bind_parameters", "wall_ms", "error_type")})
                low, high = (mid, high) if result["ok"] else (low, mid)
            successes.append(low)
            failures.append(high)
    return {"status": "MEDIDO", "evidence_mode": writer_takes_evidence(), "attempts": attempts, "binary_search": refine,
            "largest_successful_batch": max(successes) if successes else None,
            "first_failing_batch": min(failures) if failures else None,
            "scope": "una sola llamada a ensure_teams + upsert_fixtures (una respuesta lógica), rollback sin commit",
            "memory_scope": "pico de asignaciones Python (tracemalloc) en un pase separado; no RSS ni memoria del servidor"}


# --- Coste del hash ---------------------------------------------------------------------------


def measure_hash(engine: Engine, sizes=(380, 1000, 10_000, 100_000), samples: int = 15, warmup: int = 3) -> dict:
    """fixture_state_hash_v1 aislado en el servidor: misma lectura con y sin el hash."""
    cols = ", ".join(STATE_COLUMNS)
    results = []
    maint = maintenance_engine(engine)
    try:
        with maint.connect() as conn:
            total = conn.exec_driver_sql("SELECT count(*) FROM fixtures").scalar_one()
            for n in sizes:
                if n > total:
                    results.append({"rows": n, "status": "NO MEDIDO", "reason": "menos fixtures que filas"})
                    continue
                source = f"(SELECT {cols} FROM fixtures ORDER BY id LIMIT {int(n)}) f"
                queries = {
                    "with_hash": f"SELECT sum(octet_length(fixture_state_hash_v1({cols}))) FROM {source}",
                    "without_hash": f"SELECT sum(octet_length(status_short) + coalesce(home_goals, 0)) FROM {source}",
                }
                server = {}
                for name, sql in queries.items():
                    times = []
                    for i in range(warmup + samples):
                        plan = conn.exec_driver_sql("EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) " + sql).scalar_one()
                        if i >= warmup:
                            times.append(plan[0]["Execution Time"])
                    server[name] = distribution(times)
                per_row_us = (server["with_hash"]["p50"] - server["without_hash"]["p50"]) * 1000 / n
                results.append({"rows": n, "status": "MEDIDO", "server_execution_ms": server,
                                "hash_us_per_row_p50_derived": per_row_us})
                print(f"hash n={n}: with={server['with_hash']['p50']:.2f} ms without={server['without_hash']['p50']:.2f} ms "
                      f"=> {per_row_us:.2f} us/row", flush=True)
    finally:
        maint.dispose()
    return {"status": "MEDIDO", "method": "EXPLAIN ANALYZE (TIMING OFF): tiempo de ejecución del servidor; "
            "coste por fila DERIVADO como diferencia de medianas con y sin el hash sobre las mismas filas",
            "results": results}


# --- Historia sintética para lecturas ---------------------------------------------------------


def augment_history(engine: Engine, per_fixture: int, ambiguity_every: int) -> dict:
    """Preparación (fuera de toda medida): per_fixture-1 observaciones más por partido, una por hora
    después de la más nueva existente, con el estado actual del partido; y cada `ambiguity_every`
    partidos, un segundo estado en el último instante (TEMPORAL_AMBIGUITY). Después fixtures apunta
    a la observación ganadora por (observed_at, state_hash), como haría el escritor."""
    cols = ", ".join(STATE_COLUMNS)
    maint = maintenance_engine(engine)
    started = time.perf_counter()
    try:
        with maint.connect() as conn:
            base = conn.exec_driver_sql("SELECT max(observed_at) FROM fixture_observations").scalar_one()
            conn.exec_driver_sql("BEGIN")
            conn.exec_driver_sql(
                f"""INSERT INTO fixture_observations (evidence_id, fixture_id, observed_at, source, provider, state_hash, {cols})
                SELECT k.e, f.id, %(base)s + k.k * interval '1 hour', 'sync', 'api-football',
                       fixture_state_hash_v1({', '.join('f.' + c for c in STATE_COLUMNS)}), {', '.join('f.' + c for c in STATE_COLUMNS)}
                FROM fixtures f CROSS JOIN (SELECT k, gen_random_uuid() AS e FROM generate_series(1, %(n)s) k) k""",
                {"base": base, "n": per_fixture - 1},
            )
            if ambiguity_every:
                shifted = ", ".join("f.kickoff_at + interval '1 minute'" if c == "kickoff_at" else "f." + c for c in STATE_COLUMNS)
                conn.exec_driver_sql(
                    f"""INSERT INTO fixture_observations (evidence_id, fixture_id, observed_at, source, provider, state_hash, {cols})
                    SELECT e.e, f.id, %(base)s + %(n)s * interval '1 hour', 'sync', 'api-football',
                           fixture_state_hash_v1({shifted}), {shifted}
                    FROM fixtures f CROSS JOIN (SELECT gen_random_uuid() AS e) e WHERE f.id %% %(every)s = 0""",
                    {"base": base, "n": per_fixture - 1, "every": ambiguity_every},
                )
            conn.exec_driver_sql(
                """UPDATE fixtures f SET last_observed_at = w.observed_at, last_state_hash = w.state_hash
                FROM (SELECT DISTINCT ON (fixture_id) fixture_id, observed_at, state_hash FROM fixture_observations
                      ORDER BY fixture_id, observed_at DESC, state_hash DESC) w
                WHERE w.fixture_id = f.id AND (f.last_observed_at, f.last_state_hash) < (w.observed_at, w.state_hash)"""
            )
            conn.exec_driver_sql("COMMIT")
            conn.exec_driver_sql("VACUUM (ANALYZE) fixture_observations")
            conn.exec_driver_sql("VACUUM (ANALYZE) fixtures")
            summary = dict(conn.exec_driver_sql(
                "SELECT count(*) AS observations, count(DISTINCT fixture_id) AS fixtures, min(observed_at) AS first, "
                "max(observed_at) AS last FROM fixture_observations"
            ).mappings().one())
    finally:
        maint.dispose()
    return {"status": "PREPARACIÓN", "per_fixture": per_fixture, "ambiguity_every": ambiguity_every,
            "base_instant": base, "seconds": time.perf_counter() - started, **summary}


# --- Lecturas temporales ----------------------------------------------------------------------


def _plan_summary(plan) -> dict:
    nodes, indexes = [], set()

    def walk(node):
        nodes.append(node["Node Type"])
        if "Index Name" in node:
            indexes.add(node["Index Name"])
        for child in node.get("Plans", []):
            walk(child)

    walk(plan[0]["Plan"])
    return {"node_types": nodes, "indexes": sorted(indexes), "seq_scan": "Seq Scan" in nodes,
            "execution_ms": plan[0]["Execution Time"], "planning_ms": plan[0]["Planning Time"]}


def measure_reads(engine: Engine, requested=(1, 10, 380, 1000, 10_000), samples: int = 30, warmup: int = 5,
                  seed: int = 20261007) -> dict:
    from app.repositories.fixture_knowledge_repository import strict_knowledge

    maint = maintenance_engine(engine)
    try:
        with maint.connect() as m:
            info = dict(m.exec_driver_sql(
                "SELECT count(*) AS observations, count(DISTINCT fixture_id) AS fixtures_with_history, "
                "min(observed_at) AS first, max(observed_at) AS last FROM fixture_observations"
            ).mappings().one())
            per_fixture = m.exec_driver_sql(
                "SELECT percentile_disc(ARRAY[0.5, 0.95, 1.0]) WITHIN GROUP (ORDER BY c) FROM "
                "(SELECT count(*) c FROM fixture_observations GROUP BY fixture_id) x"
            ).scalar_one()
            ids = [r[0] for r in m.exec_driver_sql("SELECT id FROM fixtures ORDER BY id")]
            info["observations_per_fixture_p50_p95_max"] = per_fixture
            info["fixtures"] = len(ids)
            distinct_instants = m.exec_driver_sql(
                "SELECT count(DISTINCT observed_at) FROM fixture_observations"
            ).scalar_one()
            median_instant = m.exec_driver_sql(
                "SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY observed_at) FROM fixture_observations"
            ).scalar_one()
    finally:
        maint.dispose()
    first, last = info["first"], info["last"]
    cutoffs = {"before_all_unknown_heavy": first - timedelta(seconds=1),
               "after_all_known_heavy": last + timedelta(seconds=1)}
    if distinct_instants > 2:
        # Mediana de observed_at: la mitad de la evidencia queda antes del corte
        cutoffs["median_observed_at"] = median_instant
    rng = random.Random(seed)
    results = []
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    with engine.connect() as conn:
        for label, cutoff in cutoffs.items():
            for n in requested:
                if n > len(ids):
                    continue
                chosen = [rng.sample(ids, n) for _ in range(warmup + samples)]
                wall, cursor = [], []
                meter = StatementMeter(engine)
                with meter, Session(bind=conn) as db:
                    for i, sample_ids in enumerate(chosen):
                        meter.reset()
                        meter.enabled = True
                        started = time.perf_counter_ns()
                        report = strict_knowledge(db, sample_ids, cutoff)
                        elapsed = (time.perf_counter_ns() - started) / 1e6
                        meter.enabled = False
                        if i >= warmup:
                            wall.append(elapsed)
                            cursor.append(sum(meter.cursor_ns.values()) / 1e6)
                            last_counts = {k.value: v for k, v in report.counts.items()}
                    db.rollback()
                statements.clear()
                event.listen(engine, "before_cursor_execute", capture)
                try:
                    with Session(bind=conn) as db:
                        strict_knowledge(db, chosen[-1], cutoff)
                        db.rollback()
                finally:
                    event.remove(engine, "before_cursor_execute", capture)
                # El engine real abre cada transacción con SET LOCAL statement_timeout (DI-A2)
                [(sql, params)] = [s for s in statements if "from fixture_observations" in s[0].lower()]
                plan = conn.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).scalar_one()
                conn.rollback()
                summary = _plan_summary(plan)
                results.append({"cutoff": label, "requested": n, "status": "MEDIDO",
                                "wall_ms": distribution(wall), "sql_cursor_ms": distribution(cursor),
                                "result_counts_last_sample": last_counts, "plan": summary,
                                "explain": plan, "raw_ms": {"wall": wall, "sql_cursor": cursor}})
                print(f"read {label} n={n}: p50 wall={distribution(wall)['p50']:.3f} ms "
                      f"exec={summary['execution_ms']:.3f} ms idx={summary['indexes']} {last_counts}", flush=True)
    return {"status": "MEDIDO", "history": info, "cutoffs": {k: v.isoformat() for k, v in cutoffs.items()},
            "method": "strict_knowledge() real (consulta + construcción de los tipos); fixture ids aleatorios; "
                      "EXPLAIN (ANALYZE, BUFFERS) de la sentencia capturada en un pase aparte",
            "results": results}
