"""Fail-closed authorization, cluster ownership and immutable report provenance."""

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

from tools.perf_lab.safety import authorize

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
BASELINE = "1c06ef9c2a78a556cd8c7ded89853c19ec9a856a"
BRANCH = "lab/data-integrity-evidence-storage"
MARKER = "OPENCODE_EVIDENCE_STORAGE_LAB_V1"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def assert_workspace():
    if git("rev-parse", "HEAD") != BASELINE:
        raise RuntimeError("BASELINE_MISMATCH")
    if git("branch", "--show-current") != BRANCH:
        raise RuntimeError("WORKSPACE_OWNERSHIP_MISMATCH")
    if Path(git("rev-parse", "--show-toplevel")).resolve() != ROOT:
        raise RuntimeError("WORKSPACE_OWNERSHIP_MISMATCH")
    protected = git("diff", "--name-only", "HEAD", "--", "backend/app", "backend/alembic", "docs")
    if protected or list((BACKEND / "alembic/versions").glob("0009*")):
        raise RuntimeError("CROSS_LANE_CONFLICT: protected files/migration 0009")


@dataclass(frozen=True)
class Target:
    url: str
    cluster: Path
    system_identifier: str
    authorization: str

    @classmethod
    def from_environment(cls, environ=None):
        env = os.environ if environ is None else environ
        translated = {k: v for k, v in env.items() if k.startswith("PG")}
        translated.update(
            PERF_LAB_DATABASE_URL=env.get("EVIDENCE_STORAGE_DATABASE_URL", ""),
            PERF_LAB_ALLOW_DESTRUCTIVE=env.get("EVIDENCE_STORAGE_ALLOW_DESTRUCTIVE", ""),
        )
        local = authorize(translated)
        directory = env.get("EVIDENCE_STORAGE_CLUSTER", "")
        identifier = env.get("EVIDENCE_STORAGE_SYSTEM_IDENTIFIER", "")
        if not directory or not identifier.isdigit():
            raise ValueError("Explicit owned cluster path and system identifier required")
        cluster = Path(directory).resolve()
        if not cluster.is_dir() or not (cluster / "PG_VERSION").is_file():
            raise ValueError("Owned PostgreSQL cluster not found")
        return cls(local.url.render_as_string(hide_password=False), cluster, identifier, local.authorization)

    def verify(self, conn, *, initialized=True):
        assert_workspace()
        if Target.from_environment() != self:
            raise RuntimeError("Laboratory authorization changed")
        row = conn.execute("""SELECT current_database(), host(inet_server_addr()), inet_server_port(),
            current_setting('data_directory'), system_identifier::text,
            current_setting('fsync'), current_setting('synchronous_commit'),
            current_setting('full_page_writes'), current_setting('listen_addresses')
            FROM pg_control_system()""").fetchone()
        url = make_url(self.url)
        if row[:3] != (url.database, url.host, url.port):
            raise RuntimeError("Effective database/server identity mismatch")
        if Path(row[3]).resolve() != self.cluster or row[4] != self.system_identifier:
            raise RuntimeError("Exclusive cluster identity mismatch")
        if row[5:8] != ("on", "on", "on") or row[8] != url.host:
            raise RuntimeError("Loopback/durability settings mismatch")
        owner = json.loads((self.cluster.parent / "owner.json").read_text(encoding="utf-8-sig"))
        if owner["marker"] != MARKER or Path(owner["worktree"]).resolve() != ROOT:
            raise RuntimeError("Cluster ownership mismatch")
        if owner["baseline"] != BASELINE or str(owner["system_identifier"]) != self.system_identifier:
            raise RuntimeError("Cluster owner baseline/identifier mismatch")
        if initialized:
            comment = conn.execute("SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                                   "WHERE datname = current_database()").fetchone()[0]
            if comment != f"{MARKER}:{BASELINE}":
                raise RuntimeError("Unmarked database; operation refused")

    def connect(self, *, initialized=True, prepare_threshold=5):
        url = make_url(self.url)
        conn = psycopg.connect(host=url.host, port=url.port, dbname=url.database, user=url.username,
                               password=url.password, autocommit=True, prepare_threshold=prepare_threshold,
                               connect_timeout=3)
        try:
            self.verify(conn, initialized=initialized)
            conn.execute("SET statement_timeout = '120s'")
            conn.execute("SET lock_timeout = '10s'")
            conn.execute("SET timezone = 'UTC'")
            return conn
        except BaseException:
            conn.close()
            raise

    def configure_app(self):
        os.environ.update(DATABASE_URL=self.url, API_FOOTBALL_KEY="", FIVE_DOLLAR_FOOTBALL_API_KEY="")


def sources():
    result = {}
    for folder in ("app", "alembic", "tools/evidence_storage_lab", "tools/perf_lab"):
        for path in sorted((BACKEND / folder).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".sql", ".ps1", ".md", ".txt"}:
                result[str(path.relative_to(BACKEND))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def resources(path):
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in
            ("total_phys", "available_phys", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]

    usage = shutil.disk_usage(path)
    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    return {"disk_free_bytes": usage.free, "disk_total_bytes": usage.total,
            "available_memory_bytes": memory.available_phys, "memory_load_percent": memory.load}


def environment(target):
    with target.connect() as conn:
        settings = dict(conn.execute("SELECT name, setting FROM pg_settings WHERE name = ANY(%s)",
                                    (["shared_buffers", "work_mem", "max_wal_size", "autovacuum", "plan_cache_mode",
                                      "fsync", "synchronous_commit", "full_page_writes"],)).fetchall())
        version = conn.execute("SELECT version()").fetchone()[0]
    return {"python": sys.version, "platform": platform.platform(), "postgresql": version,
            "packages": {p: importlib.metadata.version(p) for p in ("SQLAlchemy", "psycopg", "pytest", "alembic")},
            "pip_freeze": subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True).splitlines(),
            "baseline": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
            "target": target.authorization, "cluster": str(target.cluster),
            "system_identifier": target.system_identifier, "settings": settings,
            "source_sha256": sources(), "resources": resources(target.cluster)}


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, default=str)
        stream.write("\n")
