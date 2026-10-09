"""Small logical backup/restore proof into a NEW owned database, never over existing data."""

import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from psycopg import sql
from sqlalchemy.engine import make_url

from tools.evidence_storage_lab.candidates import CANDIDATES, relation
from tools.evidence_storage_lab.safety import BASELINE, MARKER


def signatures(conn):
    return {candidate.name: conn.execute(sql.SQL(
        "SELECT count(*),md5(coalesce(string_agg(row_to_json(o)::text,'|' ORDER BY id),'')) FROM {} o")
        .format(relation(candidate.schema, "fixture_observations"))).fetchone() for candidate in CANDIDATES}


def backup_restore_gate(target, output_directory):
    owner_bin = Path(__import__("json").loads((target.cluster.parent / "owner.json").read_text(encoding="utf-8-sig"))["postgres_bin"])
    url = make_url(target.url)
    backup = Path(output_directory) / (url.database + "-recovery.dump")
    if backup.exists():
        raise RuntimeError("Recovery evidence exists; no overwrite")
    destination = url.database + "_recovery"
    common = ["-h", url.host, "-p", str(url.port), "-U", url.username]
    with target.connect() as conn:
        before = signatures(conn)
        if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)", (destination,)).fetchone()[0]:
            raise RuntimeError("Restore requires a new database; no existing data is dropped")
        target.verify(conn)
        began = time.perf_counter()
        subprocess.run([str(owner_bin / "pg_dump.exe"), *common, "-Fc", "-f", str(backup), url.database], check=True)
        dumped = time.perf_counter() - began
        target.verify(conn)
        subprocess.run([str(owner_bin / "createdb.exe"), *common, destination], check=True)
    restore_url = url.set(database=destination).render_as_string(hide_password=False)
    restore_authorization = f"{url.host}:{url.port}/{destination}"
    restore = replace(target, url=restore_url, authorization=restore_authorization)
    saved = {key: os.environ.get(key) for key in ("EVIDENCE_STORAGE_DATABASE_URL", "EVIDENCE_STORAGE_ALLOW_DESTRUCTIVE")}
    os.environ.update(EVIDENCE_STORAGE_DATABASE_URL=restore_url, EVIDENCE_STORAGE_ALLOW_DESTRUCTIVE=restore_authorization)
    try:
        with restore.connect(initialized=False) as conn:
            occupied = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                    "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'").fetchone()[0]
            if occupied:
                raise RuntimeError("Restore destination not empty")
            began = time.perf_counter()
            subprocess.run([str(owner_bin / "pg_restore.exe"), *common, "--exit-on-error", "--single-transaction",
                            "-d", destination, str(backup)], check=True)
            restored = time.perf_counter() - began
            restore.verify(conn, initialized=False)
            after = signatures(conn)
            if before != after:
                raise AssertionError("Full audit evidence changed in logical backup/restore")
            conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(destination), sql.Literal(f"{MARKER}:{BASELINE}")))
            restore.verify(conn)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {"status": "PASS", "backup": str(backup), "backup_bytes": backup.stat().st_size,
            "dump_seconds": dumped, "restore_seconds": restored, "restored_database": destination,
            "full_audit_signatures_equal": True, "single_transaction_restore": True,
            "scope": "small correctness dataset logical recovery only; large/PITR/crash recovery not benchmarked",
            "dependency": "Registry, payload leaves, overlays, manifests, views, constraints and hash function must restore together"}
