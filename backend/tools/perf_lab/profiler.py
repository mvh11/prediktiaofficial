"""DI-A3C: atribución del tiempo del upsert real (ensure_teams -> upsert_fixtures -> mappings -> commit).

Solo instrumenta el proceso del laboratorio, sin tocar producción:
- eventos públicos del engine (before/after_execute, before/after_cursor_execute);
- envoltorios temporales de ensure_teams, upsert_fixtures y upsert_origin_mappings en el
  módulo del repositorio, que llaman a la función original y se restauran al terminar.
No cambia SQL, repositorios, granularidad transaccional ni configuración del servidor.

Las marcas de cada muestra forman una línea de tiempo: los segmentos entre marcas
consecutivas teselan el intervalo medido, así que su suma es exactamente el wall time
antes de commit y ningún tramo queda sin etiqueta. Lo que un segmento contiene por
dentro (p. ej. fetch frente a construcción de dicts) solo se puede INFERIR con cProfile.
"""

import cProfile
import functools
import gc
import pstats
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext

from sqlalchemy import Engine, delete, event, text
from sqlalchemy.engine import default as sa_default
from sqlalchemy.orm import Session

from tools.perf_lab.benchmark import distribution, payload_groups, repository_write, verify_write
from tools.perf_lab.dataset import DatasetConfig, analyze

PROFILE_MODES = ("update", "unchanged", "insert")
WRAPPED = ("ensure_teams", "upsert_fixtures", "upsert_origin_mappings")
_CACHE_STATUS = {
    getattr(sa_default, name): name
    for name in ("CACHE_HIT", "CACHE_MISS", "CACHING_DISABLED", "NO_CACHE_KEY", "NO_DIALECT_SUPPORT")
}
_SQL_HEAD = re.compile(r"\s*(INSERT INTO|SELECT|SET LOCAL|SET|UPDATE|DELETE FROM)\s+([^\s(]+)", re.I)
_FROM = re.compile(r"\sFROM\s+([^\s(]+)", re.I)
# Funciones cuyo tiempo acumulado se reporta siempre (archivo contiene, nombre de función)
CPROFILE_PROBES = {
    "pydantic model_dump": ("pydantic", "model_dump"),
    "sqlalchemy cache key": ("sqlalchemy", "_generate_cache_key"),
    "sqlalchemy compile (SQLCompiler)": ("sqlalchemy/sql/compiler.py", "__init__"),
    "sqlalchemy construct_params": ("sqlalchemy/sql/compiler.py", "construct_params"),
    "sqlalchemy _init_compiled": ("sqlalchemy/engine/default.py", "_init_compiled"),
    "sqlalchemy _execute_context": ("sqlalchemy/engine/base.py", "_execute_context"),
    "orm Session.execute": ("sqlalchemy/orm/session.py", "execute"),
    "orm autoflush": ("sqlalchemy/orm/session.py", "_autoflush"),
    "orm flush": ("sqlalchemy/orm/session.py", "flush"),
    "insert().values": ("sqlalchemy/sql/dml.py", "values"),
    "psycopg cursor.execute": ("psycopg/cursor.py", "execute"),
    "result .all()": ("sqlalchemy/engine/result.py", "all"),
    "canonical_external_id": ("provider_mapping_repository.py", "canonical_external_id"),
    "_fixture_update_values": ("fixture_repository.py", "_fixture_update_values"),
    "sqlalchemy multi-VALUES compile": ("sqlalchemy/sql/crud.py", "_extend_values_for_multiparams"),
    "sqlalchemy coercions.expect": ("sqlalchemy/sql/coercions.py", "expect"),
    "orm _get_orm_crud_kv_pairs": ("sqlalchemy/orm/bulk_persistence.py", "_get_orm_crud_kv_pairs"),
    "psycopg _query2pg_nocache": ("psycopg/_queries.py", "_query2pg_nocache"),
    "psycopg _split_query": ("psycopg/_queries.py", "_split_query"),
    "socket wait select.select": ("~", "<built-in method select.select>"),
}


def statement_family(sql: str) -> str:
    """Familia de una sentencia a partir de su texto (cabecera y cláusula ON CONFLICT)."""
    match = _SQL_HEAD.match(sql)
    if not match:
        return "OTHER"
    verb, target = match.group(1).upper(), match.group(2)
    if verb == "SELECT":
        found = _FROM.search(sql[:4000])
        return f"SELECT {found.group(1) if found else '?'}"
    if verb == "INSERT INTO":
        if "ON CONFLICT" in sql:
            return f"INSERT {target} ON CONFLICT {'DO UPDATE' if 'DO UPDATE' in sql else 'DO NOTHING'}"
        return f"INSERT {target}"
    return f"{verb} {target}"


def _parameter_rows(parameters) -> int:
    if isinstance(parameters, dict):
        return 1
    return len(parameters) if isinstance(parameters, (list, tuple)) else 0


class Timeline:
    """Marcas (ns, tipo, dato) de una muestra. Tipos: start/end, enter/exit (función),
    execute/cursor/cursor_done/executed (sentencia)."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.enabled = False
        self.reset()

    def reset(self):
        self.marks = []
        self.statements = []
        self._open = []

    def mark(self, kind, data=None):
        if self.enabled:
            self.marks.append((time.perf_counter_ns(), kind, data))

    def _before_execute(self, conn, clauseelement, multiparams, params, execution_options):
        if self.enabled:
            stmt = {"family": "?"}
            self._open.append(stmt)
            self.statements.append(stmt)
            self.mark("execute", stmt)

    def _before_cursor(self, conn, cursor, statement, parameters, context, executemany):
        if self.enabled and self._open:
            stmt = self._open[-1]
            context._di_a3c = stmt
            stmt.update(
                family=statement_family(statement), executemany=bool(executemany),
                parameter_rows=_parameter_rows(parameters), sql_chars=len(statement),
                cache=_CACHE_STATUS.get(getattr(context, "cache_hit", None), "raw"),
            )
            self.mark("cursor", stmt)

    def _after_cursor(self, conn, cursor, statement, parameters, context, executemany):
        stmt = getattr(context, "_di_a3c", None)
        if self.enabled and stmt is not None:
            self.mark("cursor_done", stmt)
            stmt["rowcount"] = cursor.rowcount

    def _after_execute(self, conn, clauseelement, multiparams, params, execution_options, result):
        if self.enabled and self._open:
            self.mark("executed", self._open.pop())

    def __enter__(self):
        for name, fn in self._listeners():
            event.listen(self.engine, name, fn)
        return self

    def __exit__(self, *_):
        for name, fn in self._listeners():
            event.remove(self.engine, name, fn)

    def _listeners(self):
        return (("before_execute", self._before_execute), ("before_cursor_execute", self._before_cursor),
                ("after_cursor_execute", self._after_cursor), ("after_execute", self._after_execute))


def _describe(kind, data) -> str:
    if kind in ("enter", "exit"):
        return f"{kind}:{data}"
    if kind in ("execute", "executed"):
        return f"{kind}:{data['family']}"
    return kind


def segments(marks) -> list[tuple[str, str, str, int]]:
    """(contexto, categoría, detalle, ns) por cada par de marcas consecutivas.

    Categorías MEDIDAS como intervalo: sqlalchemy_pre_cursor (de execute a cursor: clave de
    caché, compilación si hay miss, parámetros), cursor_execute (round-trip del driver,
    incluida su adaptación de parámetros y el servidor), sqlalchemy_post_cursor (resultado)
    y python (todo lo demás entre dos fronteras; su contenido solo se infiere).
    """
    out = []
    stack = []
    for (t0, k0, d0), (t1, k1, d1) in zip(marks, marks[1:]):
        if k0 == "enter":
            stack.append(d0)
        elif k0 == "exit" and stack and stack[-1] == d0:
            stack.pop()
        context = ">".join(stack) or "repository_write"
        same = d0 is d1 and isinstance(d0, dict)
        if same and (k0, k1) == ("execute", "cursor"):
            category, detail = "sqlalchemy_pre_cursor", d0["family"]
        elif same and (k0, k1) == ("cursor", "cursor_done"):
            category, detail = "cursor_execute", d0["family"]
        elif same and (k0, k1) == ("cursor_done", "executed"):
            category, detail = "sqlalchemy_post_cursor", d0["family"]
        else:
            category, detail = "python", f"{_describe(k0, d0)} -> {_describe(k1, d1)}"
        out.append((context, category, detail, t1 - t0))
    return out


class GCMeter:
    """Pausas del recolector de Python durante la muestra (gc.callbacks)."""

    def __init__(self):
        self.enabled = False
        self.reset()

    def reset(self):
        self.nanoseconds = 0
        self.collections = Counter()
        self._started = None

    def callback(self, phase, info):
        if not self.enabled:
            return
        if phase == "start":
            self._started = time.perf_counter_ns()
        elif self._started is not None:
            self.nanoseconds += time.perf_counter_ns() - self._started
            self.collections[info.get("generation")] += 1
            self._started = None

    def __enter__(self):
        gc.callbacks.append(self.callback)
        return self

    def __exit__(self, *_):
        gc.callbacks.remove(self.callback)


@contextmanager
def wrapped_repository(timeline: Timeline):
    """Envuelve temporalmente las funciones del repositorio con marcas enter/exit."""
    from app.repositories import fixture_repository as repo

    originals = {name: getattr(repo, name) for name in WRAPPED}

    def wrap(name, fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            timeline.mark("enter", name)
            try:
                return fn(*args, **kwargs)
            finally:
                timeline.mark("exit", name)
        return wrapper

    for name, fn in originals.items():
        setattr(repo, name, wrap(name, fn))
    try:
        yield
    finally:
        for name, fn in originals.items():
            setattr(repo, name, fn)


def _set_prepare_threshold(conn, value: str) -> object:
    driver = conn.connection.driver_connection
    if value == "off":
        driver.prepare_threshold = None
    elif value != "driver":
        driver.prepare_threshold = int(value)
    return driver.prepare_threshold


def _prepared_statements(conn) -> list[dict]:
    rows = conn.exec_driver_sql(
        "SELECT statement, generic_plans, custom_plans FROM pg_prepared_statements ORDER BY prepare_time"
    ).all()
    return [{"family": statement_family(s), "generic_plans": g, "custom_plans": c} for s, g, c in rows]


def _cprofile_summary(stats: pstats.Stats, samples: int, limit: int = 25) -> dict:
    entries = []
    for (filename, line, func), (cc, nc, tt, ct, _callers) in stats.stats.items():
        path = filename.replace("\\", "/")
        short = path.split("site-packages/")[-1].split("backend/")[-1]
        entries.append({"function": f"{short}:{line}({func})", "_file": path, "_func": func, "ncalls": nc,
                        "primitive_calls": cc, "tottime_ms": tt * 1e3 / samples, "cumtime_ms": ct * 1e3 / samples})
    probes = {}
    for label, (file_part, func) in CPROFILE_PROBES.items():
        found = [e for e in entries if file_part in e["_file"] and e["_func"] == func]
        probes[label] = {"cumtime_ms": sum(e["cumtime_ms"] for e in found) if found else None,
                         "ncalls": sum(e["ncalls"] for e in found) // samples if found else 0,
                         "matches": len(found)}

    def top(key):
        ranked = sorted(entries, key=lambda e: e[key], reverse=True)[:limit]
        return [{k: v for k, v in e.items() if not k.startswith("_")} for e in ranked]

    return {"status": "MEDIDO con cProfile (inflado por el perfilador; usar solo como proporción)",
            "samples": samples, "per_sample_units": "ms y llamadas por muestra",
            "profiled_total_ms": stats.total_tt * 1e3 / samples,
            "top_cumulative": top("cumtime_ms"), "top_tottime": top("tottime_ms"), "top_ncalls": top("ncalls"),
            "probes": probes}


def _rollup(per_sample: list[dict], keys) -> dict:
    return {key: distribution([s.get(key, 0) / 1e6 for s in per_sample]) for key in keys}


def profile_case(engine: Engine, config: DatasetConfig, batch: int, mode: str, samples: int,
                 warmup: int, cprofile_samples: int, prepare_threshold: str) -> dict:
    from app.models import Fixture

    base = payload_groups(config, batch, "unchanged")
    with Session(engine) as db:  # normaliza el punto de partida, fuera del reloj
        repository_write(db, base)
        db.commit()
    analyze(engine)
    server_before = database_statistics(engine)
    build_ms = []
    variants = []
    for v in (0, 1):
        started = time.perf_counter_ns()
        variants.append(payload_groups(config, batch, mode, v))
        build_ms.append((time.perf_counter_ns() - started) / 1e6)
    external_ids = [f.external_id for fs in variants[0].values() for f in fs]

    plain = {k: [] for k in ("wall_before_commit", "commit", "total", "thread_cpu", "gc")}
    instr = {k: [] for k in ("wall_before_commit", "commit", "total", "thread_cpu", "gc")}
    per_sample_segments, per_sample_categories, per_sample_contexts = [], [], []
    statement_rows = defaultdict(list)
    gc_totals = Counter()
    sequence = []  # orden real de las muestras medidas, para series temporales

    def cleanup():
        if mode == "insert":  # misma limpieza que DI-A3B, fuera del reloj
            with engine.begin() as conn:
                conn.execute(delete(Fixture).where(Fixture.external_id.in_(external_ids)))

    with engine.connect() as conn, Timeline(engine) as timeline, GCMeter() as gc_meter:
        threshold = _set_prepare_threshold(conn, prepare_threshold)
        total_runs = warmup + 2 * samples
        for i in range(total_runs):
            groups = variants[i % 2]
            measured = i >= warmup
            instrumented = measured and (i - warmup) % 2 == 1
            target = instr if instrumented else plain
            ctx = wrapped_repository(timeline) if instrumented else nullcontext()
            with ctx, Session(bind=conn) as db:
                timeline.reset()
                gc_meter.reset()
                timeline.enabled = instrumented
                gc_meter.enabled = measured
                cpu0 = time.thread_time_ns()
                timeline.mark("start")
                started = time.perf_counter_ns()
                count = repository_write(db, groups)
                before_commit = time.perf_counter_ns()
                timeline.mark("end")
                timeline.enabled = False
                db.commit()
                finished = time.perf_counter_ns()
                cpu1 = time.thread_time_ns()
                gc_meter.enabled = False
            assert count == batch
            if measured:
                target["wall_before_commit"].append((before_commit - started) / 1e6)
                target["commit"].append((finished - before_commit) / 1e6)
                target["total"].append((finished - started) / 1e6)
                target["thread_cpu"].append((cpu1 - cpu0) / 1e6)
                target["gc"].append(gc_meter.nanoseconds / 1e6)
                gc_totals.update(gc_meter.collections)
                sequence.append({"run": i, "instrumented": instrumented,
                                 "total_ms": (finished - started) / 1e6,
                                 "thread_cpu_ms": (cpu1 - cpu0) / 1e6, "gc_ms": gc_meter.nanoseconds / 1e6})
            if instrumented:
                parts = segments(timeline.marks)
                tiled = sum(p[3] for p in parts)
                measured_window = timeline.marks[-1][0] - timeline.marks[0][0]
                assert tiled == measured_window, "los segmentos no teselan el intervalo"
                by_segment, by_category, by_context = Counter(), Counter(), Counter()
                cursor_ns = 0
                for context, category, detail, ns in parts:
                    by_segment[f"{context} | {category} | {detail}"] += ns
                    by_category[category] += ns
                    by_context[context] += ns
                    if category == "cursor_execute":
                        cursor_ns += ns
                per_sample_segments.append(by_segment)
                per_sample_categories.append(by_category)
                per_sample_contexts.append(by_context)
                sequence[-1]["cursor_ms"] = cursor_ns / 1e6
                per_family = defaultdict(list)
                for stmt in timeline.statements:
                    per_family[stmt["family"]].append(stmt)
                for family, stmts in per_family.items():
                    statement_rows[family].append(stmts)
            if i == warmup:
                verify_write(engine, groups)
            cleanup()

        profile = None
        if cprofile_samples:
            profiler = cProfile.Profile()
            for j in range(cprofile_samples):
                groups = variants[(total_runs + j) % 2]
                with Session(bind=conn) as db:
                    profiler.enable()
                    repository_write(db, groups)
                    db.commit()
                    profiler.disable()
                cleanup()
            profile = _cprofile_summary(pstats.Stats(profiler), cprofile_samples)
        prepared = _prepared_statements(conn)

    segment_keys = sorted({k for s in per_sample_segments for k in s})
    segment_table = []
    instr_wall_mean = sum(instr["wall_before_commit"]) / len(instr["wall_before_commit"])
    for key in segment_keys:
        values = [s.get(key, 0) / 1e6 for s in per_sample_segments]
        context, category, detail = key.split(" | ", 2)
        segment_table.append({"context": context, "category": category, "detail": detail,
                              "status": "MEDIDO", **distribution(values),
                              "mean": sum(values) / len(values),
                              "share_of_wall_mean": sum(values) / len(values) / instr_wall_mean})
    segment_table.sort(key=lambda r: r["mean"], reverse=True)

    statements = {}
    for family, sample_lists in statement_rows.items():
        flat = [s for stmts in sample_lists for s in stmts]
        statements[family] = {
            "per_sample_count": distribution([len(stmts) for stmts in sample_lists]),
            "executemany": sorted({s.get("executemany") for s in flat}, key=str),
            "parameter_rows": sorted({s.get("parameter_rows") for s in flat}, key=str),
            "rowcount": sorted({s.get("rowcount") for s in flat}, key=str),
            "sql_chars": sorted({s.get("sql_chars") for s in flat}, key=str),
            "cache": dict(Counter(s.get("cache") for s in flat)),
        }

    category_keys = ("python", "sqlalchemy_pre_cursor", "cursor_execute", "sqlalchemy_post_cursor")
    context_keys = sorted({k for s in per_sample_contexts for k in s})
    return {
        "operation": f"profile_{mode}_{batch}", "status": "MEDIDO", "unit": "ms", "batch": batch, "mode": mode,
        "season_groups": [len(fs) for fs in base.values()], "prepare_threshold": str(threshold),
        "payload_build_ms": {"status": "MEDIDO (fuera del reloj del path)", "variants": build_ms},
        "plain": {**{f"{k}_ms": distribution(v) for k, v in plain.items()}, "raw_ms": plain},
        "instrumented": {**{f"{k}_ms": distribution(v) for k, v in instr.items()}, "raw_ms": instr,
                         "categories_ms": _rollup(per_sample_categories, category_keys),
                         "contexts_ms": _rollup(per_sample_contexts, context_keys),
                         "segments": segment_table},
        "instrumentation_overhead_p50_ms": distribution(instr["total"])["p50"] - distribution(plain["total"])["p50"],
        "gc_collections_by_generation": {str(k): v for k, v in gc_totals.items()},
        "statements": statements, "prepared_statements_after_case": prepared,
        "sequence": sequence, "cprofile": profile,
        "server_stats": {"before_samples": server_before, "after_case": database_statistics(engine)},
    }



def measure_profile(engine: Engine, config: DatasetConfig, batches, modes, samples: int, warmup: int,
                    cprofile_samples: int, prepare_threshold: str) -> list[dict]:
    results = []
    for batch in batches:
        if batch > config.fixtures:
            results.append({"operation": f"profile_{batch}", "status": "NO MEDIDO", "reason": "dataset menor que lote"})
            continue
        for mode in modes:
            result = profile_case(engine, config, batch, mode, samples, warmup, cprofile_samples, prepare_threshold)
            results.append(result)
            print(f"{result['operation']}: p50 plain total={result['plain']['total_ms']['p50']:.3f} ms, "
                  f"instr total={result['instrumented']['total_ms']['p50']:.3f} ms", flush=True)
        with Session(engine) as db:  # deja los datos semánticamente iguales al dataset inicial
            repository_write(db, payload_groups(config, batch, "unchanged"))
            db.commit()
    return results


def database_statistics(engine: Engine) -> dict:
    """Estado del servidor tras la corrida (fuera del reloj): tuplas muertas y HOT updates."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT relname, n_live_tup, n_dead_tup, n_tup_upd, n_tup_hot_upd
            FROM pg_stat_user_tables WHERE relname IN ('fixtures', 'fixture_provider_mappings',
                                                       'teams', 'team_provider_mappings')
            ORDER BY relname
        """)).mappings().all()
    return {"status": "MEDIDO", "tables": [dict(r) for r in rows]}
