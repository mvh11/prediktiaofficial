"""Helpers puros de tools.perf_lab.rehearsal_0008 (ensayo operativo NO productivo de 0008). Sin BD."""

import pytest

from tools.perf_lab import g2_target as t
from tools.perf_lab import rehearsal_0008 as r


def test_migration_graph_is_single_head_0008_without_0009():
    graph = r.migration_graph()
    assert graph["heads"] == ["0008"] and not graph["has_0009"]
    assert graph["upgrade_path_0007_to_0008"] == ["0008"] and graph["0008_down_revision"] == "0007"


def test_rehearsal_guard_uses_its_own_variables_and_fails_closed():
    url = "postgresql://u:p@ep-x-pooler.region.aws.neon.tech/db?sslmode=require"
    env = {r.URL_ENV: url, r.BRANCH_ENV: "br-example-123", t.CLASSIFICATION_ENV: "NON_PRODUCTION"}
    assert t.authorize_target(env, url_env=r.URL_ENV, branch_env=r.BRANCH_ENV).branch_id == "br-example-123"
    with pytest.raises(t.TargetRefused):  # la variable de G2 no sirve para el ensayo
        t.authorize_target({t.URL_ENV: url, t.BRANCH_ENV: "br-example-123", t.CLASSIFICATION_ENV: "NON_PRODUCTION"},
                           url_env=r.URL_ENV, branch_env=r.BRANCH_ENV)


def test_smoke_payload_stays_in_reserved_lab_ranges():
    fixtures = r._smoke_fixtures(None) + r._smoke_fixtures(1)
    assert {f.external_id for f in fixtures} == {r.SMOKE_FIXTURE_BASE + i for i in range(1, r.SMOKE_FIXTURES + 1)}
    assert all(f.external_id >= t.FIXTURE_EXTERNAL_BASE for f in fixtures)
    teams = {f.home_team.external_id for f in fixtures} | {f.away_team.external_id for f in fixtures}
    assert all(t.TEAM_EXTERNAL_BASE <= e < t.FIXTURE_EXTERNAL_BASE for e in teams)
    assert t.COMPETITION_EXTERNAL_BASE <= r.SMOKE_COMPETITION_EXTERNAL_ID < t.TEAM_EXTERNAL_BASE
    assert r.SMOKE_COMPETITION_EXTERNAL_ID != t.LAB_COMPETITION_EXTERNAL_ID


def test_smoke_variants_change_state_but_not_identity():
    base, updated = r._smoke_fixtures(None), r._smoke_fixtures(1)
    assert [f.external_id for f in base] == [f.external_id for f in updated]
    assert all(a.status_short != b.status_short for a, b in zip(base, updated))
