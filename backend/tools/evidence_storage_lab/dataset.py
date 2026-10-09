"""Staged deterministic history; preparation is separate from measured writes."""

import json
import os
import subprocess
import sys
import time

from psycopg import sql
from sqlalchemy import create_engine

from tools.evidence_storage_lab.candidates import COLUMNS, META, STATE, identifier, relation, storage
from tools.evidence_storage_lab.safety import BACKEND, BASELINE, MARKER


def migrate(target, revision):
    if revision not in ("0007", "0008"):
        raise ValueError("Only explicit existing revisions 0007/0008 allowed")
    with target.connect(initialized=False):
        pass
    target.configure_app()
    script = "from alembic.config import Config; from alembic import command; from alembic.script import ScriptDirectory; "
    script += "c=Config('alembic.ini'); c.attributes['configure_logger']=False; "
    script += "assert ScriptDirectory.from_config(c).get_heads()==['0008'], 'CROSS_LANE_CONFLICT'; "
    script += f"command.upgrade(c, '{revision}')"
    subprocess.run([sys.executable, "-B", "-c", script], cwd=BACKEND, check=True, env=os.environ.copy())


def initialize(target):
    with target.connect(initialized=False) as conn:
        occupied = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' "
                                "AND c.relkind IN ('r','p','v','m','f','S'))").fetchone()[0]
        if occupied:
            raise RuntimeError("init requires a new empty database; nothing is dropped")
    migrate(target, "0007")
    # Existing A3 generator is reused only for current reference fixtures in the disposable DB.
    from tools.perf_lab.dataset import DatasetConfig, seed_database
    from tools.perf_lab.safety import authorize, write_metadata

    os.environ.update(PERF_LAB_DATABASE_URL=target.url, PERF_LAB_ALLOW_DESTRUCTIVE=target.authorization)
    local = authorize(os.environ)
    engine = create_engine(target.url)
    with target.connect(initialized=False):
        write_metadata(engine, local, {"owner": MARKER})
        seed = seed_database(engine, local, DatasetConfig(10_000, 20261008))
    engine.dispose()
    with target.connect(initialized=False) as conn:
        lsn = conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
    started = time.perf_counter()
    migrate(target, "0008")
    elapsed = time.perf_counter() - started
    with target.connect(initialized=False) as conn:
        row = conn.execute("SELECT count(*),count(DISTINCT evidence_id),count(DISTINCT observed_at),"
                           "min(observed_at),pg_total_relation_size('fixture_observations') "
                           "FROM public.fixture_observations").fetchone()
        if row[:3] != (10_000, 1, 1):
            raise AssertionError("Bootstrap evidence/count parity failed")
        wal = conn.execute("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),%s)", (lsn,)).fetchone()[0]
        conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
            sql.Identifier(conn.info.dbname), sql.Literal(f"{MARKER}:{BASELINE}")))
    return {"seed": seed, "migration_head": "0008", "bootstrap_seconds": elapsed,
            "bootstrap_wal_bytes": int(wal), "bootstrap_observations": row[0],
            "bootstrap_at": row[3], "bootstrap_evidence_bytes": row[4],
            "no_pre_bootstrap_knowledge": True, "production_migration_changed": False}


def seed_source(conn, target, observations):
    target.verify(conn)
    if observations not in (100_000, 1_000_000):
        raise ValueError("Phase 2 admits only 100k and 1M; no automatic multi-million generation")
    fixtures = 10_000
    depth = observations // fixtures
    started = time.perf_counter()
    conn.execute("CREATE SCHEMA lab_source")
    conn.execute("CREATE TABLE lab_source.fixture_observations (LIKE public.fixture_observations "
                 "INCLUDING DEFAULTS INCLUDING IDENTITY INCLUDING CONSTRAINTS)")
    # UUID identity is shared by each synthetic 380-fixture logical response, not by state hash.
    # First 380 fixtures have history only in 2023; other histories span 36 months.
    # 10% have distinct states at the same final instant; 20% persist last evidence late.
    command = f"""INSERT INTO lab_source.fixture_observations
        (evidence_id,fixture_id,observed_at,recorded_at,source,provider,state_hash,{','.join(STATE)})
        SELECT md5('evidence-lab:v1:'||j||':'||((f-1)/380)||':'||observed::text)::uuid, f, observed,
               observed + CASE WHEN f %% 5=0 THEN interval '45 days' ELSE interval '1 second' END,
               'sync','api-football',public.fixture_state_hash_v1({','.join(STATE)}),{','.join(STATE)}
        FROM (
          SELECT f,j,
            timestamptz '2023-01-01 00:00:00+00' + make_interval(months =>
              CASE WHEN f<=380 THEN LEAST(11,(j*35)/(%s-1))
                   WHEN f %% 10=0 AND j=%s-2 THEN 35 ELSE (j*35)/(%s-1) END) AS observed,
            timestamptz '2022-08-01 00:00:00+00' AS kickoff_at,'FT'::text AS status_short,
            1 AS season_id,1 AS home_team_id,2 AS away_team_id,
            CASE WHEN f %% 10=0 AND j=%s-1 THEN 2 ELSE 1 END AS home_goals, 0 AS away_goals,
            NULL::integer AS halftime_home,NULL::integer AS halftime_away,
            NULL::integer AS fulltime_home,NULL::integer AS fulltime_away,
            NULL::integer AS extratime_home,NULL::integer AS extratime_away,
            NULL::integer AS penalty_home,NULL::integer AS penalty_away
          FROM generate_series(0,%s-1) j CROSS JOIN generate_series(1,%s) f
        ) synthetic ORDER BY j,f"""
    conn.execute(command, (depth, depth, depth, depth, depth, fixtures))
    count = conn.execute("SELECT count(*) FROM lab_source.fixture_observations").fetchone()[0]
    if count != observations:
        raise AssertionError("Staged observation count mismatch")
    inconsistent = conn.execute("SELECT count(*) FROM (SELECT evidence_id FROM lab_source.fixture_observations "
                                "GROUP BY evidence_id HAVING count(DISTINCT(observed_at,source,provider))>1) bad").fetchone()[0]
    if inconsistent:
        raise AssertionError("One logical response UUID has inconsistent receipt metadata")
    conn.execute("ANALYZE lab_source.fixture_observations")
    return {"observations": count, "fixtures": fixtures, "depth": depth, "span_months": 36,
            "cold_only_fixtures": 380, "confirmation_heavy": True, "seconds": time.perf_counter()-started,
            "response_metadata_conflicts": inconsistent,
            "synthetic_timestamps": True, "not_provider_reception_or_real_historical_claim": True}


def load_candidate(conn, target, candidate):
    target.verify(conn)
    started = time.perf_counter()
    lsn = conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
    with conn.transaction():
        if candidate.registry:
            conn.execute(sql.SQL("INSERT INTO {}({}) OVERRIDING SYSTEM VALUE SELECT {} FROM lab_source.fixture_observations")
                         .format(relation(candidate.schema, "identity_registry"), sql.SQL(",").join(map(sql.Identifier, META)),
                                 sql.SQL(",").join(map(sql.Identifier, META))))
        conn.execute(sql.SQL("INSERT INTO {}({}) {} SELECT {} FROM lab_source.fixture_observations")
                     .format(relation(candidate.schema, "fixture_observations"), sql.SQL(",").join(map(sql.Identifier, COLUMNS)),
                             sql.SQL("" if candidate.registry else "OVERRIDING SYSTEM VALUE"),
                             sql.SQL(",").join(map(sql.Identifier, COLUMNS))))
        sequence_table = "identity_registry" if candidate.registry else "fixture_observations"
        conn.execute("SELECT setval(pg_get_serial_sequence(%s,'id'),(SELECT max(id) FROM lab_source.fixture_observations))",
                     (candidate.schema + "." + sequence_table,))
        conn.execute(sql.SQL("UPDATE {} f SET last_observed_at=o.observed_at,last_state_hash=o.state_hash FROM "
                             "(SELECT DISTINCT ON(fixture_id) fixture_id,observed_at,state_hash FROM {} "
                             "ORDER BY fixture_id,observed_at DESC,state_hash DESC) o WHERE f.id=o.fixture_id")
                     .format(relation(candidate.schema, "fixtures"), relation(candidate.schema, "fixture_observations")))
    elapsed = time.perf_counter() - started
    wal = int(conn.execute("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),%s)", (lsn,)).fetchone()[0])
    measured = storage(conn, candidate)
    return {"seconds": elapsed, "wal_bytes": wal, "observations_per_second": measured["observations"] / elapsed,
            "storage": measured, "scope": "bulk preparation load, all constraints/indexes retained; not application throughput"}


def full_row_parity(conn, candidate):
    table = relation(candidate.schema, "fixture_observations")
    query = sql.SQL("SELECT count(*) FROM ((SELECT * FROM {} EXCEPT ALL SELECT * FROM lab_source.fixture_observations) "
                    "UNION ALL (SELECT * FROM lab_source.fixture_observations EXCEPT ALL SELECT * FROM {})) delta").format(table, table)
    delta = conn.execute(query).fetchone()[0]
    hashes = conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE state_hash<>public.fixture_state_hash_v1({})")
                          .format(table, sql.SQL(",").join(map(sql.Identifier, STATE)))).fetchone()[0]
    if delta or hashes:
        raise AssertionError(f"Full row/hash parity failed: delta={delta},hashes={hashes}")
    return {"full_row_symmetric_difference": delta, "noncanonical_hashes": hashes, "status": "PASS"}
