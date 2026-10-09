"""Helpers puros de tools.perf_lab.g2 (DI-A6 G2, solo laboratorio). Sin BD."""

import json

from tools.perf_lab import g2


def test_size_and_counter_deltas():
    before = {"fixtures": {"heap_bytes": 100, "index_bytes": 50, "total_bytes": 150, "rows": 10}}
    after = {"fixtures": {"heap_bytes": 180, "index_bytes": 70, "total_bytes": 250, "rows": 10},
             "fixture_observations": {"heap_bytes": 40, "index_bytes": 20, "total_bytes": 60, "rows": 3}}
    assert g2.size_delta(before, after) == {
        "fixtures": {"heap_bytes": 80, "index_bytes": 20, "total_bytes": 100, "rows": 0},
        "fixture_observations": {"heap_bytes": 40, "index_bytes": 20, "total_bytes": 60, "rows": 3},
    }
    cb = {"database": {"deadlocks": 0, "xact_commit": 5}, "tables": {"fixtures": {"n_tup_upd": 1, "n_tup_hot_upd": 1}}}
    ca = {"database": {"deadlocks": 0, "xact_commit": 9}, "tables": {"fixtures": {"n_tup_upd": 11, "n_tup_hot_upd": 10}}}
    assert g2.counters_delta(cb, ca) == {"database": {"deadlocks": 0, "xact_commit": 4},
                                         "tables": {"fixtures": {"n_tup_upd": 10, "n_tup_hot_upd": 9}}}


def test_summarize_writes_and_upgrade(tmp_path):
    dist = {"samples": 2, "min": 1.0, "p50": 2.0, "p95": 3.0, "p99": 3.0, "max": 3.0}
    writes = {
        "label": "pass-X", "pass_seconds": 1.0, "pass_counters_delta": {"database": {"deadlocks": 0}},
        "results": [
            {"operation": "write_update_380", "status": "MEDIDO", "raw_ms": {"total": [1.0, 2.0]}, "total_ms": dist,
             "commit_ms": dist, "wal_bytes": dist, "fixtures_hot_ratio": 0.5,
             "xact_inserted": {"fixture_observations": dist},
             "upsert_counts": {k: dist for k in ("created", "updated", "unchanged")}},
            {"operation": "write_older_380", "status": "NO APLICA"},
        ],
    }
    upgrade = {"bootstrap_wall_seconds": 1.5, "counts": {"fixtures": 3},
               "g2": {"version_before": "0007", "version_after": "0008", "read_probe": {"max_read_ms": 1.0},
                      "relation_size_delta": {"fixtures": {"heap_bytes": 2**20, "index_bytes": 0, "total_bytes": 2**20, "rows": 0}}}}
    (tmp_path / "w.json").write_text(json.dumps(writes), encoding="utf-8")
    (tmp_path / "u.json").write_text(json.dumps(upgrade), encoding="utf-8")
    out = g2.summarize([tmp_path / "w.json", tmp_path / "u.json"])
    assert "| write_update_380 | 2 | 2.0 | 3.0 | 3.0 |" in out and "50.0%" in out
    assert "| write_older_380 | — | NO APLICA |" in out
    assert "upgrade 0007 → 0008" in out and "fixtures: heap +1.0 MiB" in out
