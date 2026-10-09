"""LAB DDL, append gateway and complete historical read relations.

The gateway is deliberately not a production writer. Registry admission and payload
insertion commit together; replay never inserts a second payload. Archive transitions
must be quiescent (exclusive gateway lock), just as bootstrap must be quiescent.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
from psycopg import sql

SCORES = ["home_goals", "away_goals", "halftime_home", "halftime_away", "fulltime_home",
          "fulltime_away", "extratime_home", "extratime_away", "penalty_home", "penalty_away"]
STATE = ["kickoff_at", "status_short", "season_id", "home_team_id", "away_team_id", *SCORES]
COLUMNS = ["id", "evidence_id", "fixture_id", "observed_at", "recorded_at", "source", "provider", "state_hash", *STATE]
META = ["id", "fixture_id", "evidence_id", "observed_at", "recorded_at", "source", "provider", "state_hash"]
UTC = timezone.utc
ARCHIVE_BOUNDARY = datetime(2025, 1, 1, tzinfo=UTC)


def identifier(name):
    if not name.replace("_", "").isalnum() or not name.startswith("lab_"):
        raise ValueError("Only laboratory identifiers allowed")
    return sql.Identifier(name)


def relation(schema, name):
    return sql.Identifier(schema, name)


def month_at(index):
    year, month = divmod(2023 * 12 + index, 12)
    return datetime(year, month + 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Candidate:
    name: str
    months: int = 0
    registry: bool = False
    lifecycle: str = "none"
    alternative: str = "none"

    @property
    def schema(self):
        return "lab_" + self.name


CANDIDATES = (
    Candidate("control"), Candidate("registry_control", registry=True),
    Candidate("monthly", months=1, registry=True), Candidate("quarterly", months=3, registry=True),
    Candidate("archive", months=1, registry=True, lifecycle="archive"),
    Candidate("hotcold", months=1, registry=True, lifecycle="hotcold"),
    Candidate("brin", alternative="brin"), Candidate("covering", alternative="covering"),
)


def native_partition_diagnostic(conn, target):
    """Do not silently widen the logical key: native-only candidates are ineligible."""
    target.verify(conn)
    diagnostics = []
    for months in (1, 3):
        name = f"lab_native_{months}"
        with conn.transaction():
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(identifier(name)))
            conn.execute(sql.SQL("CREATE TABLE {} (LIKE public.fixture_observations INCLUDING DEFAULTS "
                                 "INCLUDING IDENTITY INCLUDING CONSTRAINTS) PARTITION BY RANGE(observed_at)")
                         .format(relation(name, "fixture_observations")))
        try:
            with conn.transaction():
                conn.execute(sql.SQL("ALTER TABLE {} ADD UNIQUE(fixture_id, evidence_id)")
                             .format(relation(name, "fixture_observations")))
        except psycopg.errors.FeatureNotSupported as exc:
            diagnostics.append({"months": months, "eligible": False, "sqlstate": exc.sqlstate,
                                "reason": str(exc), "rows_loaded": 0,
                                "weakened_logical_unique_constraint": False})
        else:
            raise AssertionError("Unexpected native global uniqueness support; reevaluate design")
    return diagnostics


def create_candidate(conn, target, candidate, fixtures=10_000):
    target.verify(conn)
    schema = candidate.schema
    identifier(schema)
    started = time.perf_counter()
    with conn.transaction():
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(identifier(schema)))
        conn.execute(sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY, last_observed_at timestamptz, "
                             "last_state_hash bytea CHECK(octet_length(last_state_hash)=32))")
                     .format(relation(schema, "fixtures")))
        conn.execute(sql.SQL("INSERT INTO {} (id) SELECT generate_series(1,%s)")
                     .format(relation(schema, "fixtures")), (fixtures,))
        conn.execute(sql.SQL("CREATE TABLE {} (code text PRIMARY KEY)").format(relation(schema, "providers")))
        conn.execute(sql.SQL("INSERT INTO {} SELECT code FROM public.providers").format(relation(schema, "providers")))
        if candidate.registry:
            conn.execute(sql.SQL("""CREATE TABLE {} (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                fixture_id integer NOT NULL REFERENCES {}(id) ON DELETE RESTRICT,
                evidence_id uuid NOT NULL, observed_at timestamptz NOT NULL,
                recorded_at timestamptz NOT NULL DEFAULT now(), source text NOT NULL,
                provider text REFERENCES {}(code) ON DELETE RESTRICT,
                state_hash bytea NOT NULL CHECK(octet_length(state_hash)=32),
                UNIQUE(fixture_id,evidence_id), UNIQUE(id,fixture_id,evidence_id,observed_at))""")
                         .format(relation(schema, "identity_registry"), relation(schema, "fixtures"),
                                 relation(schema, "providers")))
        create_payload(conn, candidate, "fixture_observations", partitioned=bool(candidate.months))
        if candidate.months:
            for start in range(0, 42, candidate.months):
                provision(conn, candidate, start)
    return {"ddl_seconds": time.perf_counter() - started,
            "initial_partitions": 42 // candidate.months if candidate.months else 0}


def create_payload(conn, candidate, name, *, partitioned=False):
    schema = candidate.schema
    base = sql.SQL("CREATE TABLE {} (LIKE public.fixture_observations INCLUDING DEFAULTS INCLUDING IDENTITY "
                   "INCLUDING CONSTRAINTS)").format(relation(schema, name))
    if partitioned:
        base += sql.SQL(" PARTITION BY RANGE(observed_at)")
    conn.execute(base)
    if candidate.registry:
        conn.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN id DROP IDENTITY").format(relation(schema, name)))
        conn.execute(sql.SQL("ALTER TABLE {} ADD FOREIGN KEY(id,fixture_id,evidence_id,observed_at) "
                             "REFERENCES {}(id,fixture_id,evidence_id,observed_at) ON DELETE RESTRICT")
                     .format(relation(schema, name), relation(schema, "identity_registry")))
    key = "id,observed_at" if partitioned else "id"
    conn.execute(sql.SQL("ALTER TABLE {} ADD PRIMARY KEY({})").format(relation(schema, name), sql.SQL(key)))
    if not partitioned:
        conn.execute(sql.SQL("ALTER TABLE {} ADD UNIQUE(fixture_id,evidence_id)").format(relation(schema, name)))
    conn.execute(sql.SQL("ALTER TABLE {} ADD FOREIGN KEY(fixture_id) REFERENCES {}(id) ON DELETE RESTRICT, "
                         "ADD FOREIGN KEY(provider) REFERENCES {}(code) ON DELETE RESTRICT")
                 .format(relation(schema, name), relation(schema, "fixtures"), relation(schema, "providers")))
    conn.execute(sql.SQL("CREATE INDEX ON {}(fixture_id, observed_at)").format(relation(schema, name)))


def provision(conn, candidate, start, parent="fixture_observations"):
    conn.execute(sql.SQL("CREATE TABLE {} PARTITION OF {} FOR VALUES FROM ({}) TO ({})")
                 .format(relation(candidate.schema, f"p_{start:03}"), relation(candidate.schema, parent),
                         sql.Literal(month_at(start)), sql.Literal(month_at(start + candidate.months))))


def add_alternative(conn, target, candidate):
    target.verify(conn)
    started = time.perf_counter()
    table = relation(candidate.schema, "fixture_observations")
    if candidate.alternative == "brin":
        conn.execute(sql.SQL("CREATE INDEX lab_observed_brin ON {} USING brin(observed_at) WITH(pages_per_range=32)")
                     .format(table))
    elif candidate.alternative == "covering":
        included = [c for c in COLUMNS if c not in ("fixture_id", "observed_at")]
        conn.execute(sql.SQL("CREATE INDEX lab_temporal_covering ON {}(fixture_id,observed_at) INCLUDE({})")
                     .format(table, sql.SQL(",").join(map(sql.Identifier, included))))
    return {"build_seconds": time.perf_counter() - started, "alternative": candidate.alternative,
            "existing_indexes_preserved": True}


def lifecycle(conn, target, candidate):
    """No row mutation, copy, deletion or compaction; detach/attach is atomic and quiescent."""
    if candidate.lifecycle == "none":
        return {"status": "NOT_APPLICABLE"}
    target.verify(conn)
    started = time.perf_counter()
    old = relation(candidate.schema, "fixture_observations")
    with conn.transaction():
        conn.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(old))
        conn.execute(sql.SQL("ALTER TABLE {} RENAME TO history").format(old))
        create_payload(conn, candidate, "late_evidence")
        if candidate.lifecycle == "hotcold":
            create_payload(conn, candidate, "cold_history", partitioned=True)
            for start in range(0, 24, candidate.months):
                leaf = relation(candidate.schema, f"p_{start:03}")
                conn.execute(sql.SQL("ALTER TABLE {} DETACH PARTITION {}")
                             .format(relation(candidate.schema, "history"), leaf))
                conn.execute(sql.SQL("ALTER TABLE {} ATTACH PARTITION {} FOR VALUES FROM ({}) TO ({})")
                             .format(relation(candidate.schema, "cold_history"), leaf, sql.Literal(month_at(start)),
                                     sql.Literal(month_at(start + candidate.months))))
            union = "SELECT * FROM {s}.history UNION ALL SELECT * FROM {s}.cold_history UNION ALL SELECT * FROM {s}.late_evidence"
        else:
            union = "SELECT * FROM {s}.history UNION ALL SELECT * FROM {s}.late_evidence"
        conn.execute(sql.SQL("CREATE VIEW {} AS " + union).format(
            relation(candidate.schema, "fixture_observations"), s=identifier(candidate.schema)))
        conn.execute(sql.SQL("CREATE TABLE {} (boundary timestamptz PRIMARY KEY, sealed_at timestamptz NOT NULL DEFAULT now())")
                     .format(relation(candidate.schema, "archive_manifest")))
        conn.execute(sql.SQL("INSERT INTO {}(boundary) VALUES(%s)")
                     .format(relation(candidate.schema, "archive_manifest")), (ARCHIVE_BOUNDARY,))
    freeze_started = time.perf_counter()
    for start in range(0, 24, candidate.months):
        conn.execute(sql.SQL("VACUUM(FREEZE, ANALYZE) {}").format(relation(candidate.schema, f"p_{start:03}")))
    return {"transition_seconds": freeze_started - started,
            "historical_freeze_seconds": time.perf_counter() - freeze_started,
            "sealed_before": ARCHIVE_BOUNDARY, "leaves_retained": 24 // candidate.months,
            "late_arrivals": "append-only overlay", "quiescence_required": True,
            "row_deletion_or_compaction": False, "same_physical_disk": True,
            "seal_enforcement": "laboratory gateway; no production triggers or policy changes"}


def physical_tables(conn, candidate):
    return [row[0] for row in conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                         "WHERE n.nspname=%s AND c.relkind='r' ORDER BY c.relname",
                                         (candidate.schema,)).fetchall()]


def maintenance(conn, target, candidate, *, freeze=False):
    target.verify(conn)
    started = time.perf_counter()
    times = {}
    for name in physical_tables(conn, candidate):
        begin = time.perf_counter()
        conn.execute(sql.SQL("VACUUM({}ANALYZE) {}").format(sql.SQL("FREEZE," if freeze else ""),
                                                           relation(candidate.schema, name)))
        times[name] = time.perf_counter() - begin
    for row in conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname=%s AND c.relkind='p'", (candidate.schema,)).fetchall():
        conn.execute(sql.SQL("ANALYZE {}").format(relation(candidate.schema, row[0])))
    return {"seconds": time.perf_counter() - started, "per_table_seconds": times,
            "freeze": freeze, "explicit_parent_analyze": True}


def storage(conn, candidate):
    rows = conn.execute("""SELECT c.relname, pg_table_size(c.oid), pg_indexes_size(c.oid),
        pg_total_relation_size(c.oid), c.relallvisible, c.relallfrozen, c.relpages
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind='r' ORDER BY c.relname""", (candidate.schema,)).fetchall()
    detail = [{"name": r[0], "heap_toast_bytes": r[1], "index_bytes": r[2], "total_bytes": r[3],
               "all_visible_pages": r[4], "all_frozen_pages": r[5], "pages": r[6]} for r in rows]
    payload = [r for r in detail if r["name"] not in ("fixtures", "providers", "identity_registry", "archive_manifest")]
    registry = [r for r in detail if r["name"] == "identity_registry"]
    count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(relation(candidate.schema, "fixture_observations"))).fetchone()[0]
    heap = sum(r["heap_toast_bytes"] for r in payload)
    indexes = sum(r["index_bytes"] for r in payload)
    registry_bytes = sum(r["total_bytes"] for r in registry)
    return {"observations": count, "payload_heap_toast_bytes": heap, "payload_index_bytes": indexes,
            "registry_bytes": registry_bytes, "evidence_total_bytes": heap + indexes + registry_bytes,
            "bytes_per_observation": (heap + indexes + registry_bytes) / count if count else None,
            "detail": detail, "indexes": conn.execute("SELECT tablename,indexname,indexdef FROM pg_indexes "
                                                       "WHERE schemaname=%s ORDER BY tablename,indexname", (candidate.schema,)).fetchall()}


class IdentityConflict(ValueError):
    pass


def payload(fixture_id, observed_at, *, evidence_id=None, goals=1, partial=False, source="sync", provider="api-football"):
    return {"fixture_id": fixture_id, "evidence_id": evidence_id or uuid.uuid4(), "observed_at": observed_at,
            "source": source, "provider": provider, "kickoff_at": datetime(2022, 8, 1, tzinfo=UTC),
            "status_short": "FT", "season_id": 1, "home_team_id": 1, "away_team_id": 2,
            "home_goals": goals, "away_goals": 0, "halftime_home": None, "halftime_away": None,
            "fulltime_home": None if partial else goals, "fulltime_away": None if partial else 0,
            "extratime_home": None, "extratime_away": None, "penalty_home": None, "penalty_away": None}


def append_batch(conn, target, candidate, rows, *, update_heads=False, verify=True):
    """Canonical hashes, atomic identity admission and optional unchanged head confirmation.

    Caller owns the transaction; conflict must propagate so its context rolls back.
    No score fusion or mappings are measured: head updates are metadata-only.
    """
    if verify:
        target.verify(conn)
    if not rows:
        return {"inserted": 0, "replayed": 0}
    if len({(r["fixture_id"], r["evidence_id"]) for r in rows}) != len(rows):
        raise ValueError("Duplicate identities inside laboratory batch")
    if len({r["fixture_id"] for r in rows}) != len(rows):
        raise ValueError("Use distinct fixture IDs within a laboratory batch")
    response_metadata = {}
    for row in rows:
        metadata = (row["observed_at"], row["source"], row["provider"])
        if row["evidence_id"] in response_metadata and response_metadata[row["evidence_id"]] != metadata:
            raise ValueError("One response UUID must share receipt metadata across fixtures")
        response_metadata[row["evidence_id"]] = metadata
    input_cols = ["fixture_id", "evidence_id", "observed_at", "source", "provider", *STATE]
    types = ["integer", "uuid", "timestamptz", "text", "text", "timestamptz", "text"] + ["integer"] * 13
    bindings = ",".join(f"%s::{t}[]" for t in types)
    input_sql = f"SELECT r.*, public.fixture_state_hash_v1({','.join('r.' + c for c in STATE)}) AS state_hash FROM unnest({bindings}) AS r({','.join(input_cols)})"
    arrays = [[r[c] for r in rows] for c in input_cols]
    computed = conn.execute(input_sql, arrays).fetchall()
    ordered = sorted([dict(zip([*input_cols, "state_hash"], r)) for r in computed], key=lambda r: r["fixture_id"])
    cols = ["fixture_id", "evidence_id", "observed_at", "source", "provider", "state_hash"]
    if not candidate.registry:
        cols += STATE
    table = "identity_registry" if candidate.registry else "fixture_observations"
    casts = ["integer", "uuid", "timestamptz", "text", "text", "bytea"]
    if not candidate.registry:
        casts += ["timestamptz", "text"] + ["integer"] * 13
    command = sql.SQL("INSERT INTO {} ({}) SELECT * FROM unnest({}) AS r({}) ORDER BY fixture_id "
                      "ON CONFLICT(fixture_id,evidence_id) DO NOTHING RETURNING id,fixture_id,evidence_id,recorded_at")
    command = command.format(relation(candidate.schema, table), sql.SQL(",").join(map(sql.Identifier, cols)),
                             sql.SQL(",".join(f"%s::{t}[]" for t in casts)), sql.SQL(",").join(map(sql.Identifier, cols)))
    inserted = {r[1]: r for r in conn.execute(command, [[r[c] for r in ordered] for c in cols]).fetchall()}
    duplicates = [r for r in ordered if r["fixture_id"] not in inserted]
    if duplicates:
        stored = conn.execute(sql.SQL("SELECT fixture_id,evidence_id,observed_at,source,provider,state_hash FROM {} "
                                      "WHERE fixture_id=ANY(%s) AND evidence_id=ANY(%s)")
                              .format(relation(candidate.schema, table)),
                              ([r["fixture_id"] for r in duplicates], [r["evidence_id"] for r in duplicates])).fetchall()
        existing = {(r[0], r[1]): (r[2], r[3], r[4], bytes(r[5])) for r in stored}
        for row in duplicates:
            if existing.get((row["fixture_id"], row["evidence_id"])) != (
                    row["observed_at"], row["source"], row["provider"], bytes(row["state_hash"])):
                raise IdentityConflict("Inconsistent reuse of (fixture_id,evidence_id)")
    if candidate.registry and inserted:
        lifecycle_active = candidate.lifecycle != "none" and conn.execute("SELECT to_regclass(%s)",
                            (candidate.schema + ".archive_manifest",)).fetchone()[0] is not None
        groups = {}
        for row in ordered:
            if row["fixture_id"] not in inserted:
                continue
            admission = inserted[row["fixture_id"]]
            stamped = {**row, "id": admission[0], "recorded_at": admission[3]}
            table = "fixture_observations"
            if lifecycle_active:
                table = "late_evidence" if row["observed_at"] < ARCHIVE_BOUNDARY else "history"
            groups.setdefault(table, []).append(stamped)
        casts = ["bigint", "uuid", "integer", "timestamptz", "timestamptz", "text", "text", "bytea",
                 "timestamptz", "text"] + ["integer"] * 13
        for table, admitted in groups.items():
            conn.execute(sql.SQL("INSERT INTO {}({}) SELECT * FROM unnest({}) AS r({})")
                         .format(relation(candidate.schema, table), sql.SQL(",").join(map(sql.Identifier, COLUMNS)),
                                 sql.SQL(",".join(f"%s::{t}[]" for t in casts)), sql.SQL(",").join(map(sql.Identifier, COLUMNS))),
                         [[r[c] for r in admitted] for c in COLUMNS])
    if update_heads:
        conn.execute(sql.SQL("SELECT id FROM {} WHERE id=ANY(%s) ORDER BY id FOR UPDATE")
                     .format(relation(candidate.schema, "fixtures")), ([r["fixture_id"] for r in ordered],)).fetchall()
        conn.execute(sql.SQL("UPDATE {} f SET last_observed_at=r.observed_at,last_state_hash=r.state_hash "
                             "FROM unnest(%s::integer[],%s::timestamptz[],%s::bytea[]) r(id,observed_at,state_hash) "
                             "WHERE f.id=r.id AND (f.last_observed_at IS NULL OR "
                             "(f.last_observed_at,f.last_state_hash)<(r.observed_at,r.state_hash))")
                     .format(relation(candidate.schema, "fixtures")),
                     ([r["fixture_id"] for r in ordered], [r["observed_at"] for r in ordered], [r["state_hash"] for r in ordered]))
    return {"inserted": len(inserted), "replayed": len(duplicates)}


def strict_sql(candidate):
    table = relation(candidate.schema, "fixture_observations")
    return sql.SQL("""SELECT o.* FROM {} o JOIN (
        SELECT fixture_id,max(observed_at) AS t_star FROM {}
        WHERE fixture_id=ANY(%s::integer[]) AND observed_at<=%s::timestamptz GROUP BY fixture_id
        ) latest ON o.fixture_id=latest.fixture_id AND o.observed_at=latest.t_star
        ORDER BY o.fixture_id,o.evidence_id""").format(table, table)


def classify(rows, ids):
    grouped = {}
    for row in rows:
        values = dict(zip(COLUMNS, row))
        values["state_hash"] = bytes(values["state_hash"])
        grouped.setdefault(values["fixture_id"], []).append(values)
    result = {}
    for fixture_id in sorted(set(ids)):
        observations = grouped.get(fixture_id, [])
        hashes = {r["state_hash"] for r in observations}
        status = "UNKNOWN_AT_T" if not observations else "KNOWN" if len(hashes) == 1 else "TEMPORAL_AMBIGUITY"
        result[fixture_id] = {"status": status, "observations": observations,
                              "state": observations[0] if status == "KNOWN" else None}
    return result
