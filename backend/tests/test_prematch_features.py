"""team_recent_form_v1 (M5.7B) extremo a extremo contra PostgreSQL: matriz de leakage, regímenes
estricto/backtest, identidad fail-closed y reproducibilidad. Solo lecturas en lo que se prueba;
el escenario se construye con los escritores de siempre dentro de la transacción del test."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, select, update

from app.models import Fixture, FixtureStatisticsObservation, TeamProviderMapping
from app.repositories import fixture_repository
from app.repositories import statistics_repository as repo
from app.schemas.prematch_features import FeatureStatus as F
from app.schemas.prematch_features import MetricSide
from app.schemas.prematch_features import SampleStatus as S
from app.schemas.statistics_knowledge import KnowledgeRegime, StatisticsProvenance
from app.services.prematch_features import fingerprint, team_recent_form_v1
from tests.conftest import make_competition, make_evidence, make_fixture_data

pytestmark = pytest.mark.db

PROVIDER = "api-football"
TEAM = 63  # id del proveedor del equipo analizado
K = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)  # kickoff del objetivo
T = K - timedelta(hours=1)  # corte prepartido, fijado por el llamador
EARLY = datetime(2026, 8, 1, tzinfo=timezone.utc)  # evidencia de fixtures y mappings
TARGET = 9000
# (id externo, rival, local?, días antes de K, estado)
HISTORY = [(9001, 70, True, 1, "FT"), (9002, 71, False, 4, "FT"), (9003, 72, True, 8, "AET"),
           (9004, 73, False, 11, "FT"), (9005, 74, True, 15, "FT"), (9006, 75, False, 18, "FT"), (9007, 76, True, 22, "FT")]


def _kickoff(days):
    return K - timedelta(days=days)


def _fixture_data(ext, rival, home, days, status, **kw):
    h, a = (TEAM, rival) if home else (rival, TEAM)
    goals = {} if status == "NS" else dict(home_goals=1, away_goals=0, fulltime_home=1, fulltime_away=0)
    return make_fixture_data(ext, home=h, away=a, status=status, kickoff_at=kw.pop("kickoff_at", _kickoff(days)), **goals, **kw)


@pytest.fixture
def scenario(db_session):
    _, sid = make_competition(db_session, 39, name="Premier League")
    data = [_fixture_data(TARGET, 80, True, 0, "NS")] + [_fixture_data(*h) for h in HISTORY]
    team_ids = fixture_repository.ensure_teams(db_session, [t for d in data for t in (d.home_team, d.away_team)], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, sid, data, team_ids, PROVIDER, make_evidence(EARLY, provider=PROVIDER))
    db_session.execute(update(TeamProviderMapping).values(created_at=EARLY))
    ids = {int(e): i for e, i in db_session.execute(select(Fixture.external_id, Fixture.id))}
    return {"sid": sid, "ids": ids, "team_ids": team_ids, "team": team_ids[TEAM]}


def _stats(home_pid, away_pid, home=None, away=None):
    def entry(pid, v):
        if v is None:
            return {"team": {"id": pid}, "statistics": []}
        return {"team": {"id": pid}, "statistics": [
            {"type": "Total Shots", "value": v.get("shots", 10)}, {"type": "Shots on Goal", "value": v.get("sog", 4)},
            {"type": "Corner Kicks", "value": v.get("corners", 5)}, {"type": "Ball Possession", "value": v.get("poss", "50%")},
            *([{"type": "expected_goals", "value": v["xg"]}] if "xg" in v else []),
        ]}
    return [entry(home_pid, home), entry(away_pid, away)]


def _observe(db, s, ext, *, observed_at, source="live", available_at=None, team_vals=None, rival_vals=None, raw=None):
    rival, home = next((r, h) for e, r, h, *_ in HISTORY + [(TARGET, 80, True, 0, "NS")] if e == ext)
    tv = {"shots": 12} if team_vals is None else team_vals
    rv = {"shots": 6} if rival_vals is None else rival_vals
    payload = raw if raw is not None else (_stats(TEAM, rival, tv, rv) if home else _stats(rival, TEAM, rv, tv))
    with_stats = sum(1 for e in payload if e["statistics"])
    return repo.record_observation(
        db, fixture_id=s["ids"][ext], provider=PROVIDER, provider_fixture_id=str(ext), payload=payload, source=source,
        availability=("empty", "partial", "available")[with_stats], teams_returned=with_stats, observed_at=observed_at,
        available_at=observed_at if available_at is None else available_at,
    ).observation_id


def _live_all(db, s, exts=None, **kw):
    """Estadísticas live de cada partido, recibidas 3 h después de su kickoff."""
    for ext, _, _, days, _ in HISTORY:
        if exts is None or ext in exts:
            _observe(db, s, ext, observed_at=_kickoff(days) + timedelta(hours=3), **kw)


def _form(db, s, cutoff=T, horizon=None, **kw):
    return team_recent_form_v1(db, team_id=s["team"], target_fixture_id=s["ids"][TARGET], provider=PROVIDER,
                               cutoff=cutoff, horizon=horizon, **kw)


def _window(form):
    return [(m.fixture_id, m.sample) for m in form.window]


# --- Caso base ----------------------------------------------------------------------------------


def test_window_is_the_last_five_played_matches_with_typed_metrics(db_session, scenario):
    s = scenario
    _live_all(db_session, s)
    form = _form(db_session, s)
    assert form.status is F.OK and form.regime is KnowledgeRegime.OPERATIONAL_STRICT and not form.is_retrospective
    assert form.target_kickoff == K and form.target_known_at == EARLY
    assert [m.fixture_id for m in form.window] == [s["ids"][e] for e in (9001, 9002, 9003, 9004, 9005)]
    assert [m.venue for m in form.window] == ["home", "away", "home", "away", "home"]
    assert form.sample_counts()[S.USED] == 4 and form.sample_counts()[S.EXCLUDED_EXTRA_TIME] == 1  # 9003 es AET
    assert form.metric("shots_total") == form.metric("shots_total", MetricSide.FOR)
    assert (form.metric("shots_total").value, form.metric("shots_total").samples) == (Decimal("12"), 4)
    assert form.metric("shots_total", MetricSide.AGAINST).value == Decimal("6")
    assert form.metric("possession_pct").value == Decimal("50")
    xg = form.metric("expected_goals")
    assert (xg.value, xg.samples) == (None, 0)  # ausente en el raw: ni ceros ni media
    assert form.provenance_counts()[StatisticsProvenance.OPERATIONAL] == 4


def test_minimum_samples_per_metric(db_session, scenario):
    s = scenario
    _live_all(db_session, s, exts={9001, 9002})
    form = _form(db_session, s)
    assert form.metric("shots_total") .samples == 2 and form.metric("shots_total").value is None
    assert form.sample_counts()[S.UNKNOWN_AT_T] == 2  # 9004 y 9005 sin estadísticas: no se saltan


def test_missing_values_are_never_zeros(db_session, scenario):
    s = scenario
    _live_all(db_session, s, team_vals={"shots": None, "xg": "1.50"})
    form = _form(db_session, s)
    assert (form.metric("shots_total").value, form.metric("shots_total").samples) == (None, 0)
    assert (form.metric("expected_goals").value, form.metric("expected_goals").samples) == (Decimal("1.5"), 4)


# --- Objetivo y corte ---------------------------------------------------------------------------


def test_own_fixture_is_never_in_its_window(db_session, scenario):
    s = scenario
    _live_all(db_session, s)
    _observe(db_session, s, TARGET, observed_at=T - timedelta(minutes=1))  # anomalía: stats del objetivo antes de T
    assert s["ids"][TARGET] not in [m.fixture_id for m in _form(db_session, s).window]


def test_same_kickoff_match_is_not_context(db_session, scenario):
    s = scenario
    data = [_fixture_data(9100, 81, False, 0, "FT", kickoff_at=K)]
    ids = fixture_repository.ensure_teams(db_session, [data[0].home_team, data[0].away_team], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, s["sid"], data, ids, PROVIDER, make_evidence(EARLY + timedelta(seconds=1), provider=PROVIDER))
    _live_all(db_session, s)
    form = _form(db_session, s, cutoff=K)  # T = K: el partido simultáneo tampoco es anterior al corte
    assert all(m.kickoff_at < K for m in form.window) and len(form.window) == 5


def test_cutoff_after_kickoff_fails_closed(db_session, scenario):
    form = _form(db_session, scenario, cutoff=K + timedelta(minutes=1))
    assert form.status is F.CUTOFF_AFTER_KICKOFF and form.window == () and form.metrics == ()


def test_target_kickoff_is_the_one_known_at_the_cutoff(db_session, scenario):
    """Cambio de kickoff conocido después de T: el régimen estricto usa el K conocido en T y el
    corte se valida contra él, no se deriva de él."""
    s = scenario
    moved = [_fixture_data(TARGET, 80, True, 0, "NS", kickoff_at=K - timedelta(hours=3))]
    fixture_repository.upsert_fixtures(db_session, s["sid"], moved, s["team_ids"], PROVIDER, make_evidence(T + timedelta(minutes=5), provider=PROVIDER))
    strict = _form(db_session, s)
    assert strict.status is F.OK and strict.target_kickoff == K
    backtest = _form(db_session, s, horizon=T + timedelta(hours=1))
    assert backtest.status is F.CUTOFF_AFTER_KICKOFF and backtest.target_retrospective


def test_target_unknown_mismatched_or_ambiguous_fails_closed(db_session, scenario):
    s = scenario
    assert _form(db_session, s, cutoff=EARLY - timedelta(days=1)).status is F.TARGET_UNKNOWN  # antes del bootstrap/evidencia
    other = team_recent_form_v1(db_session, team_id=s["team_ids"][70], target_fixture_id=s["ids"][TARGET],
                                provider=PROVIDER, cutoff=T)
    assert other.status is F.TARGET_TEAM_MISMATCH
    at = T - timedelta(hours=2)
    for kickoff in (K, K + timedelta(hours=1)):  # dos estados distintos en el mismo observed_at
        fixture_repository.upsert_fixtures(db_session, s["sid"], [_fixture_data(TARGET, 80, True, 0, "NS", kickoff_at=kickoff)],
                                           s["team_ids"], PROVIDER, make_evidence(at, provider=PROVIDER))
    assert _form(db_session, s).status is F.TARGET_AMBIGUOUS


def test_strict_regime_before_the_evidence_has_no_samples(db_session, scenario):
    s = scenario
    _live_all(db_session, s)
    form = _form(db_session, s, cutoff=EARLY - timedelta(seconds=1))
    assert form.status is F.TARGET_UNKNOWN and form.window == ()


# --- Hechos de partidos ---------------------------------------------------------------------------


def test_fixture_unknown_at_the_cutoff_is_not_context(db_session, scenario):
    s = scenario
    late = [_fixture_data(9200, 82, True, 0.5, "FT")]  # jugado antes de T, pero conocido después de T
    ids = fixture_repository.ensure_teams(db_session, [late[0].home_team, late[0].away_team], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, s["sid"], late, ids, PROVIDER, make_evidence(T + timedelta(hours=2), provider=PROVIDER))
    _live_all(db_session, s)
    fid = db_session.scalar(select(Fixture.id).where(Fixture.external_id == 9200))
    assert fid not in [m.fixture_id for m in _form(db_session, s).window]
    backtest = _form(db_session, s, horizon=T + timedelta(days=1))
    first = backtest.window[0]
    assert first.fixture_id == fid and first.fixture_retrospective(T) and backtest.is_retrospective


def test_current_fixture_state_never_supplies_historical_facts(db_session, scenario):
    """Resultado/estado retrospectivo: tocar el estado ACTUAL de fixtures no cambia nada."""
    s = scenario
    _live_all(db_session, s)
    before = fingerprint(_form(db_session, s))
    db_session.execute(update(Fixture).where(Fixture.id == s["ids"][9001]).values(status_short="AET", home_goals=7))
    db_session.execute(update(Fixture).where(Fixture.id == s["ids"][9002]).values(kickoff_at=K + timedelta(days=3)))
    assert fingerprint(_form(db_session, s)) == before


def test_tied_kickoffs_at_the_window_frontier_fail_closed(db_session, scenario):
    s = scenario
    tie = [_fixture_data(9300, 83, True, 15, "FT")]  # mismo kickoff que 9005 (5.º de la ventana)
    ids = fixture_repository.ensure_teams(db_session, [tie[0].home_team, tie[0].away_team], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, s["sid"], tie, ids, PROVIDER, make_evidence(EARLY + timedelta(seconds=1), provider=PROVIDER))
    assert _form(db_session, s).status is F.AMBIGUOUS_ORDER


# --- Estadísticas: correcciones, horizonte y refresh ----------------------------------------------


def test_correction_received_after_the_cutoff_is_invisible_in_strict_regime(db_session, scenario):
    s = scenario
    _live_all(db_session, s)
    _observe(db_session, s, 9001, observed_at=T + timedelta(minutes=10), team_vals={"shots": 40})
    strict = _form(db_session, s)
    assert strict.metric("shots_total").value == Decimal("12")
    # en backtest tampoco: available_at (= recepción) es posterior a T
    assert _form(db_session, s, horizon=T + timedelta(days=30)).metric("shots_total").value == Decimal("12")


def test_horizon_bounds_synthetic_backfill(db_session, scenario):
    s = scenario
    received = T + timedelta(days=10)  # backfill real, días después del corte
    for ext, _, _, days, _ in HISTORY:
        _observe(db_session, s, ext, observed_at=received, source="backfill", available_at=_kickoff(days) + timedelta(hours=6))
    strict = _form(db_session, s)
    assert strict.sample_counts()[S.UNKNOWN_AT_T] == 4  # sintético disponible, pero no recibido en T
    exact = _form(db_session, s, horizon=received)
    assert exact.regime is KnowledgeRegime.HISTORICAL_BACKTEST and exact.sample_counts()[S.USED] == 4
    assert exact.provenance_counts()[StatisticsProvenance.HISTORICAL_SYNTHETIC] == 4
    assert all(m.statistics_retrospective(T) for m in exact.window if m.sample is S.USED) and exact.is_retrospective
    assert _form(db_session, s, horizon=received - timedelta(microseconds=1)).sample_counts()[S.USED] == 0


def test_late_historical_refresh_does_not_change_a_fixed_horizon(db_session, scenario):
    s = scenario
    h = T + timedelta(days=10)
    for ext, _, _, days, _ in HISTORY:
        _observe(db_session, s, ext, observed_at=h - timedelta(days=1), source="backfill", available_at=_kickoff(days) + timedelta(hours=6))
    before = fingerprint(_form(db_session, s, horizon=h))
    for ext, _, _, days, _ in HISTORY:  # refresh tardío con otros valores y la misma disponibilidad sintética
        _observe(db_session, s, ext, observed_at=h + timedelta(days=5), source="backfill", available_at=_kickoff(days) + timedelta(hours=6), team_vals={"shots": 30})
    assert fingerprint(_form(db_session, s, horizon=h)) == before
    assert _form(db_session, s, horizon=h + timedelta(days=5)).metric("shots_total").value == Decimal("30")


# --- Calidad e identidad ------------------------------------------------------------------------


def test_blocked_and_empty_statistics_are_absent_samples_without_shifting_the_window(db_session, scenario):
    s = scenario
    _live_all(db_session, s, exts={9001, 9004, 9005})
    _observe(db_session, s, 9002, observed_at=_kickoff(4) + timedelta(hours=3), team_vals={"poss": "130%"})  # BLOCKING
    _observe(db_session, s, 9004, observed_at=_kickoff(11) + timedelta(hours=4), raw=_stats(73, TEAM))  # EMPTY después
    form = _form(db_session, s)
    assert _window(form) == [(s["ids"][9001], S.USED), (s["ids"][9002], S.BLOCKED), (s["ids"][9003], S.EXCLUDED_EXTRA_TIME),
                             (s["ids"][9004], S.EMPTY), (s["ids"][9005], S.USED)]
    assert form.metric("shots_total").samples == 2


def test_mapping_created_after_the_observation_is_identity_unverified(db_session, scenario):
    s = scenario
    _live_all(db_session, s)
    db_session.execute(update(TeamProviderMapping).where(TeamProviderMapping.external_id == "71").values(created_at=T))
    form = _form(db_session, s)
    assert dict(_window(form))[s["ids"][9002]] is S.IDENTITY_UNVERIFIED  # rival de 9002
    assert form.metric("shots_total").samples == 3


def test_partial_without_the_team_side_is_a_missing_sample(db_session, scenario):
    s = scenario
    _live_all(db_session, s, exts={9002, 9004, 9005})
    _observe(db_session, s, 9001, observed_at=_kickoff(1) + timedelta(hours=3), raw=_stats(TEAM, 70, None, {"shots": 6}))
    form = _form(db_session, s)
    assert dict(_window(form))[s["ids"][9001]] is S.TEAM_STATISTICS_MISSING


# --- Reproducibilidad y solo lectura --------------------------------------------------------------


def test_ingest_order_does_not_change_the_feature(db_session, scenario):
    s = scenario
    prints = []
    for order in (HISTORY, list(reversed(HISTORY))):
        savepoint = db_session.begin_nested()
        for ext, _, _, days, _ in order:
            _observe(db_session, s, ext, observed_at=_kickoff(days) + timedelta(hours=3), team_vals={"shots": 10 + days})
        form = _form(db_session, s)
        prints.append((fingerprint(form), [m.statistics_observation_id for m in form.window]))
        savepoint.rollback()
    assert prints[0][0] == prints[1][0] and prints[0][1] != prints[1][1]  # mismo contenido, otros ids


def test_repeated_reconstruction_is_identical_and_read_only(db_session, scenario):
    s = scenario
    _live_all(db_session, s)
    db_session.flush()
    before = sorted(map(tuple, db_session.execute(select(FixtureStatisticsObservation.__table__)).all()))
    statements = []
    connection = db_session.connection()

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(connection, "before_cursor_execute", capture)
    try:
        first, second = _form(db_session, s), _form(db_session, s, horizon=T + timedelta(days=1))
        third = _form(db_session, s)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert first == third and fingerprint(first) == fingerprint(third) and fingerprint(first) != fingerprint(second)
    assert set(statements) == {"SELECT"}
    db_session.expire_all()
    assert sorted(map(tuple, db_session.execute(select(FixtureStatisticsObservation.__table__)).all())) == before


def test_invalid_parameters(db_session, scenario):
    with pytest.raises(ValueError):
        _form(db_session, scenario, horizon=T - timedelta(seconds=1))  # H < T
    with pytest.raises(ValueError):
        _form(db_session, scenario, cutoff=datetime(2026, 9, 20))  # sin zona horaria
    with pytest.raises(ValueError):
        _form(db_session, scenario, window=0)
