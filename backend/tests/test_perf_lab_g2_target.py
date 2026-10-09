"""Guarda y helpers puros de tools.perf_lab.g2_target (DI-A6 G2 en destino, solo laboratorio). Sin BD."""

import pytest

from tools.perf_lab import g2_target as t

REMOTE = "postgresql://lab_user:s3cret-pass@ep-example-123-pooler.region.aws.neon.tech/neondb?sslmode=require"


def env(**overrides):
    base = {t.URL_ENV: REMOTE, t.CLASSIFICATION_ENV: "NON_PRODUCTION", t.BRANCH_ENV: "br-crimson-salad-b54aki8j"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def test_authorize_accepts_classified_remote_target_and_normalizes_driver():
    spec = t.authorize_target(env())
    assert spec.url.drivername == "postgresql+psycopg"
    assert spec.branch_id == "br-crimson-salad-b54aki8j"


@pytest.mark.parametrize("overrides", [
    {t.CLASSIFICATION_ENV: None},
    {t.CLASSIFICATION_ENV: "PRODUCTION"},
    {t.BRANCH_ENV: None},
    {t.BRANCH_ENV: "production"},
    {t.URL_ENV: None},
    {t.URL_ENV: REMOTE.replace("?sslmode=require", "")},
    {t.URL_ENV: REMOTE.replace("sslmode=require", "sslmode=prefer")},
    {t.URL_ENV: REMOTE.replace("postgresql://", "mysql://")},
    {"PGHOST": "somewhere"},
])
def test_authorize_fails_closed(overrides):
    with pytest.raises(t.TargetRefused):
        t.authorize_target(env(**overrides))


def test_redact_removes_every_connection_component():
    spec = t.authorize_target(env())
    message = (f"connection to server at \"{spec.url.host}\" (10.0.0.1), port 5432 failed: password authentication failed "
               f"for user \"lab_user\" s3cret-pass neondb other.host.neon.tech")
    redacted = t.redact(message, spec.url)
    for secret in ("ep-example-123-pooler", "lab_user", "s3cret-pass", "neondb", "neon.tech"):
        assert secret not in redacted


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        assert "neon.branch_id" in sql

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row, autocommit=False):
        self.row, self.autocommit, self.rolled_back = row, autocommit, False

    def cursor(self):
        return FakeCursor(self.row)

    def rollback(self):
        self.rolled_back = True


def test_branch_check_requires_exact_branch_and_primary():
    ok = FakeConnection(("br-a", False))
    assert t.branch_matches(ok, "br-a") and ok.rolled_back
    assert not t.branch_matches(FakeConnection(("br-b", False)), "br-a")
    assert not t.branch_matches(FakeConnection((None, False)), "br-a")
    assert not t.branch_matches(FakeConnection(("br-a", True)), "br-a")


def test_remap_moves_synthetic_seasons_to_lab_season_ids():
    assert t.remap({1: ["a"], 2: ["b"]}, {1: 901, 2: 902}) == {901: ["a"], 902: ["b"]}
    with pytest.raises(KeyError):
        t.remap({3: ["c"]}, {1: 901})


def test_lab_dataset_covers_largest_batch():
    config = t.DatasetConfig(t.LAB_FIXTURES, t.LAB_SEED)
    assert config.fixtures >= 2000 and config.seasons == 10 and config.competitions == 1
