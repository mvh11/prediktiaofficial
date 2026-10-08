"""Experimento M5.8 (backend/research/m58): partes sin numpy que corren en la suite normal.
Etiquetas 1X2 a 90', splits y embargo, métricas, protocolo de test único, aislamiento de las
dependencias de investigación y el constructor del dataset contra PostgreSQL (leakage, etiquetas
desde la evidencia, sin resultados como features, huella reproducible). Los modelos (numpy y
scikit-learn) se prueban en backend/research/tests con el entorno de investigación."""

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.models import Fixture, TeamProviderMapping
from app.repositories import fixture_repository
from app.repositories import statistics_repository as repo
from research.m58 import metrics
from research.m58.dataset import COUNT_COLUMNS, FEATURE_COLUMNS, build_rows, dataset_fingerprint, leakage_violations
from research.m58.labels import EXTRA_TIME_NOT_DRAW_AT_90, FULLTIME_INCOMPLETE, NOT_FINISHED, label_1x2
from research.m58.protocol import TestGate
from research.m58.splits import EMBARGO, PURGED, TEST, TEST_START, TRAIN, VALIDATION, VALIDATION_START, assign_split, validation_halves
from tests.conftest import make_competition, make_evidence, make_fixture_data

BACKEND = Path(__file__).resolve().parents[1]
UTC = timezone.utc


# --- Etiquetas ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, home, away, expected",
    [
        ("FT", 2, 1, ("H", None)), ("FT", 1, 1, ("D", None)), ("FT", 0, 3, ("A", None)),
        ("AET", 1, 1, ("D", None)), ("PEN", 0, 0, ("D", None)),  # AET/PEN se liquidan con el 90'
        ("AET", 2, 1, (None, EXTRA_TIME_NOT_DRAW_AT_90)),  # 90' incoherente: no se adivina
        ("FT", None, 1, (None, FULLTIME_INCOMPLETE)),
        ("NS", None, None, (None, NOT_FINISHED)), ("AWD", 3, 0, (None, NOT_FINISHED)), ("WO", 3, 0, (None, NOT_FINISHED)),
    ],
)
def test_label_1x2_at_90_minutes(status, home, away, expected):
    assert label_1x2(status, home, away) == expected


# --- Splits y embargo ---------------------------------------------------------------------------


def test_splits_and_embargo_boundaries():
    assert assign_split(VALIDATION_START - EMBARGO - timedelta(seconds=1)) == TRAIN
    assert assign_split(VALIDATION_START - EMBARGO) == PURGED
    assert assign_split(VALIDATION_START - timedelta(seconds=1)) == PURGED
    assert assign_split(VALIDATION_START) == VALIDATION
    assert assign_split(TEST_START - EMBARGO) == PURGED
    assert assign_split(TEST_START) == TEST
    with pytest.raises(ValueError):
        assign_split(datetime(2025, 1, 1))


def test_validation_halves_are_chronological_and_disjoint():
    rows = [{"fixture_id": i, "kickoff_at": VALIDATION_START + timedelta(days=(i * 7) % 50)} for i in range(11)]
    a, b = validation_halves(rows)
    assert len(a) + len(b) == 11 and not {r["fixture_id"] for r in a} & {r["fixture_id"] for r in b}
    assert max(r["kickoff_at"] for r in a) <= min(r["kickoff_at"] for r in b)


# --- Métricas -----------------------------------------------------------------------------------


def test_metrics_reference_values():
    uniform = [[1 / 3] * 3] * 3
    labels = ["H", "D", "A"]
    assert math.isclose(metrics.log_loss(uniform, labels), math.log(3))
    assert math.isclose(metrics.brier(uniform, labels), 2 / 3)
    assert math.isclose(metrics.brier([[1, 0, 0]], ["H"]), 0) and math.isclose(metrics.rps([[1, 0, 0]], ["H"]), 0)
    assert math.isclose(metrics.rps([[1, 0, 0]], ["A"]), 1)  # el error más lejano en la escala ordinal
    assert metrics.rps([[0, 1, 0]], ["A"]) < metrics.rps([[1, 0, 0]], ["A"])
    with pytest.raises(ValueError):
        metrics.log_loss([[0.5, 0.5, 0.5]], ["H"])
    assert metrics.class_balance(["H", "H", "A", "D"]) == {"H": 0.5, "D": 0.25, "A": 0.25}


def test_block_bootstrap_is_deterministic_and_resamples_weeks():
    rows = [{"kickoff_at": datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=d), "x": d} for d in range(60)]
    stat = lambda rs: sum(r["x"] for r in rs) / len(rs)  # noqa: E731
    first = metrics.block_bootstrap(rows, stat, n_boot=200)
    assert first == metrics.block_bootstrap(rows, stat, n_boot=200)
    assert first[1] <= first[0] <= first[2]


def test_calibration_report_shape():
    out = metrics.calibration([[0.6, 0.3, 0.1], [0.2, 0.3, 0.5]], ["H", "A"])
    assert set(out) == {"H", "D", "A", "ece_mean"} and 0 <= out["ece_mean"] <= 1


# --- Protocolo: test una sola vez ---------------------------------------------------------------


def test_test_set_is_evaluated_once_with_the_frozen_config():
    gate = TestGate({"C": 0.1})
    with pytest.raises(RuntimeError):
        gate.evaluate({"C": 1.0}, lambda: "x")  # configuración no congelada
    assert gate.evaluate({"C": 0.1}, lambda: "ok") == "ok"
    with pytest.raises(RuntimeError):
        gate.evaluate({"C": 0.1}, lambda: "otra vez")


# --- Aislamiento de dependencias ----------------------------------------------------------------

RESEARCH_DEPS = ("numpy", "scipy", "scikit-learn", "sklearn", "joblib", "threadpoolctl")


def test_research_dependencies_stay_out_of_the_operational_runtime():
    for name in ("requirements.txt", "requirements-dev.txt", "requirements.lock"):
        text = (BACKEND / name).read_text(encoding="utf-8").lower()
        assert not any(re.search(rf"^{re.escape(d)}\b", text, re.M) for d in RESEARCH_DEPS), name
    research = (BACKEND / "requirements-research.txt").read_text(encoding="utf-8")
    assert all("==" in line for line in research.splitlines() if line and not line.startswith("#"))
    for path in (BACKEND / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(numpy|sklearn|scipy|research)\b", source, re.M), path
    for name in ("labels", "splits", "metrics", "dataset", "protocol"):  # partes de la suite normal: sin numpy
        source = (BACKEND / "research" / "m58" / f"{name}.py").read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(numpy|sklearn|scipy)\b", source, re.M), name


def test_no_result_columns_are_features():
    forbidden = {"fulltime", "halftime", "extratime", "penalty", "score", "result", "label", "status", "winner", "points"}

    def is_result(column):
        tokens = column.lower().split("_")
        goals = [i for i, t in enumerate(tokens) if t == "goals"]
        return bool(forbidden & set(tokens)) or any(i == 0 or tokens[i - 1] != "expected" for i in goals)

    assert not is_result("home_expected_goals_for") and is_result("home_goals") and is_result("away_fulltime")
    assert FEATURE_COLUMNS and not [c for c in FEATURE_COLUMNS + COUNT_COLUMNS if is_result(c)]


# --- Dataset contra PostgreSQL ------------------------------------------------------------------

PROVIDER = "api-football"
EARLY = datetime(2025, 1, 1, tzinfo=UTC)
H = datetime(2026, 10, 1, tzinfo=UTC)
TEAM = 63


def _stats(home_pid, away_pid, shots=12):
    def entry(pid):
        return {"team": {"id": pid}, "statistics": [{"type": "Total Shots", "value": shots}, {"type": "Shots on Goal", "value": 4},
                                                    {"type": "Corner Kicks", "value": 5}, {"type": "Ball Possession", "value": "50%"}]}
    return [entry(home_pid), entry(away_pid)]


@pytest.fixture
def season(db_session):
    _, sid = make_competition(db_session, 39, name="Premier League")
    base = datetime(2025, 3, 1, 19, tzinfo=UTC)
    spec = [  # (id externo, local, visitante, días, estado, fulltime)
        (7001, TEAM, 70, 0, "FT", (2, 0)), (7002, 71, TEAM, 7, "FT", (1, 1)), (7003, TEAM, 72, 14, "FT", (0, 1)),
        (7004, 73, TEAM, 21, "AET", (1, 1)), (7005, TEAM, 74, 28, "PEN", (2, 1)), (7006, TEAM, 75, 35, "FT", (3, 1)),
        (7007, 76, TEAM, 42, "NS", (None, None)),
    ]
    data = []
    for ext, home, away, days, status, (fh, fa) in spec:
        goals = {} if status == "NS" else dict(home_goals=fh, away_goals=fa, fulltime_home=fh, fulltime_away=fa)
        if status in ("AET", "PEN"):
            goals.update(home_goals=fh + 1, away_goals=fa, extratime_home=1, extratime_away=0)
            if status == "PEN":
                goals.update(home_goals=fh, penalty_home=4, penalty_away=3)
        data.append(make_fixture_data(ext, home=home, away=away, status=status, kickoff_at=base + timedelta(days=days), **goals))
    teams = fixture_repository.ensure_teams(db_session, [t for d in data for t in (d.home_team, d.away_team)], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, sid, data, teams, PROVIDER, make_evidence(EARLY, provider=PROVIDER))
    db_session.execute(update(TeamProviderMapping).values(created_at=EARLY))
    ids = {int(e): i for e, i in db_session.execute(select(Fixture.external_id, Fixture.id))}
    for ext, home, away, days, status, _ in spec:
        if status != "NS":
            repo.record_observation(db_session, fixture_id=ids[ext], provider=PROVIDER, provider_fixture_id=str(ext), payload=_stats(home, away),
                                    source="backfill", availability="available", teams_returned=2, observed_at=H - timedelta(days=1),
                                    available_at=base + timedelta(days=days, hours=6))
    return ids


def test_dataset_rows_labels_and_exclusions(db_session, season):
    rows, excluded = build_rows(db_session, H, list(season.values()))
    by_ext = {next(e for e, i in season.items() if i == r["fixture_id"]): r for r in rows}
    assert set(by_ext) == {7001, 7002, 7003, 7004, 7006}  # 7005 (PEN 2-1 a los 90') y 7007 (NS) fuera
    assert excluded == {EXTRA_TIME_NOT_DRAW_AT_90: 1, NOT_FINISHED: 1}
    assert [by_ext[e]["label"] for e in (7001, 7002, 7003, 7004, 7006)] == ["H", "D", "A", "D", "H"]
    r = by_ext[7006]
    assert r["regime"] == "HISTORICAL_BACKTEST" and r["is_retrospective"] and r["label_source"] == "STRICT_KNOWLEDGE(H)"
    assert r["cutoff"] == r["kickoff_at"] - timedelta(hours=1) and r["horizon"] == H
    assert r["home_n_used"] == 3 and r["home_n_excluded_extra_time"] == 2 and r["home_shots_total_for"] == 12.0 and r["home_expected_goals_for_missing"] is True
    assert r["home_expected_goals_for"] is None  # sin xG: None con indicador, nunca 0
    first = by_ext[7001]
    assert first["home_window_len"] == 0 and first["home_shots_total_for"] is None and first["home_shots_total_for_missing"]
    assert all(not leakage_violations(row) for row in rows)


def test_current_fixture_results_never_change_labels_or_features(db_session, season):
    rows, _ = build_rows(db_session, H, list(season.values()))
    before = dataset_fingerprint(rows)
    db_session.execute(update(Fixture).values(fulltime_home=0, fulltime_away=5, home_goals=0, away_goals=5).where(Fixture.status_short == "FT"))
    rows2, _ = build_rows(db_session, H, list(season.values()))
    assert dataset_fingerprint(rows2) == before
    assert sum(not r["audit_retro_label_agrees"] for r in rows2) == 3  # la discrepancia se audita, no se usa


def test_dataset_is_reproducible(db_session, season):
    a, ex_a = build_rows(db_session, H, list(season.values()))
    b, ex_b = build_rows(db_session, H, list(season.values()))
    assert dataset_fingerprint(a) == dataset_fingerprint(b) and ex_a == ex_b


def test_leakage_audit_detects_violations():
    row = {"cutoff": H, "horizon": H}
    for side in ("home", "away"):
        row.update({f"{side}_audit_window_has_target": False, f"{side}_audit_max_window_kickoff": H - timedelta(days=1),
                    f"{side}_audit_max_used_available_at": H, f"{side}_audit_max_used_observed_at": H})
    assert leakage_violations(row) == []
    row["away_audit_max_used_available_at"] = H + timedelta(seconds=1)
    row["home_audit_window_has_target"] = True
    assert len(leakage_violations(row)) == 2
