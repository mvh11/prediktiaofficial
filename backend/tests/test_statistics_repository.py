"""Repository de estadísticas (M5.2) contra PostgreSQL: versionado de observaciones por hash,
upsert normalizado con contadores materiales, precisión, NULL frente a 0 y transacciones."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Fixture, FixtureStatisticsObservation, FixtureTeamStatistics
from app.repositories import fixture_repository
from app.repositories import statistics_repository as repo
from app.schemas.statistics import TeamStatisticValues
from tests.conftest import load_json, make_competition, make_fixture_data

pytestmark = pytest.mark.db

PROVIDER = "api-football"
KICKOFF = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
REAL = next(i for i in load_json("api_football/statistics/fixtures_ids_recorded.json")["response"] if i["fixture"]["id"] == 1557402)
PAYLOAD = REAL["statistics"]


@pytest.fixture
def match(db_session):
    _, sid = make_competition(db_session, 39, name="Premier League")
    data = [make_fixture_data(1557402, home=63, away=34, status="FT", kickoff_at=KICKOFF, home_goals=2, away_goals=1, fulltime_home=2, fulltime_away=1)]
    team_ids = fixture_repository.ensure_teams(db_session, [data[0].home_team, data[0].away_team], PROVIDER)
    fixture_repository.upsert_fixtures(db_session, sid, data, team_ids, PROVIDER)
    return db_session.execute(select(Fixture.id, Fixture.home_team_id, Fixture.away_team_id)).one()


def _observe(db, fixture_id, payload=PAYLOAD, observed_at=T1, **kw):
    values = dict(source="backfill", availability="available", teams_returned=2, available_at=KICKOFF + timedelta(hours=6))
    values.update(kw)
    return repo.record_observation(db, fixture_id=fixture_id, provider=PROVIDER, provider_fixture_id="1557402",
                                   payload=payload, observed_at=observed_at, **values)


def _observations(db, fixture_id):
    db.expire_all()
    return list(db.scalars(select(FixtureStatisticsObservation).where(FixtureStatisticsObservation.fixture_id == fixture_id).order_by(FixtureStatisticsObservation.id)))


def _values(**kw) -> TeamStatisticValues:
    base = dict(shots_on_goal=6, shots_off_goal=4, shots_total=15, shots_blocked=5, corners=5, fouls=8, yellow_cards=1,
                possession_pct=Decimal("50"), expected_goals=Decimal("2.08"))
    base.update(kw)
    return TeamStatisticValues(**base)


def _upsert(db, fixture_id, team_id, observation_id, values=None, side="home", version=1):
    return repo.upsert_team_statistics(db, fixture_id=fixture_id, team_id=team_id, provider=PROVIDER, side=side,
                                       observation_id=observation_id, values=values or _values(), normalizer_version=version)


def _row(db, fixture_id, team_id):
    db.expire_all()
    return repo.get_team_statistics(db, fixture_id, team_id, PROVIDER)


# --- Hash canónico --------------------------------------------------------------------------


def test_hash_is_canonical_for_object_key_order():
    a = [{"team": {"id": 63, "name": "X"}, "statistics": [{"type": "Fouls", "value": 8}]}]
    b = [{"statistics": [{"value": 8, "type": "Fouls"}], "team": {"name": "X", "id": 63}}]
    assert repo.payload_hash(a) == repo.payload_hash(b)
    assert len(repo.payload_hash(a)) == 64 and repo.payload_hash(a) == repo.payload_hash(a)


@pytest.mark.parametrize(
    "changed",
    [
        [{"team": {"id": 63, "name": "X"}, "statistics": [{"type": "Fouls", "value": 9}]}],  # valor
        [{"team": {"id": 63, "name": "X"}, "statistics": [{"type": "Fouls", "value": None}]}],  # 8 → null
        [{"team": {"id": 63, "name": "X"}, "statistics": [{"type": "Fouls", "value": "8"}]}],  # tipo JSON
        [{"team": {"id": 63, "name": "X"}, "statistics": [{"type": "Fouls", "value": 8}, {"type": "Offsides", "value": 1}]}],
        [],
    ],
)
def test_hash_changes_with_real_changes(changed):
    base = [{"team": {"id": 63, "name": "X"}, "statistics": [{"type": "Fouls", "value": 8}]}]
    assert repo.payload_hash(base) != repo.payload_hash(changed)


def test_hash_respects_array_order():
    one, two = {"team": {"id": 1}, "statistics": []}, {"team": {"id": 2}, "statistics": []}
    assert repo.payload_hash([one, two]) != repo.payload_hash([two, one])


def test_hash_matches_a_reserialized_real_payload():
    import json

    assert repo.payload_hash(PAYLOAD) == repo.payload_hash(json.loads(json.dumps(PAYLOAD, indent=3)))


# --- Observaciones ------------------------------------------------------------------------


def test_first_observation(db_session, match):
    result = _observe(db_session, match.id, fixture_status_at_fetch="FT")
    assert result.outcome == "created" and result.previous_observation_id is None
    (obs,) = _observations(db_session, match.id)
    assert obs.id == result.observation_id and obs.is_latest
    assert obs.payload == PAYLOAD  # raw intacto
    assert obs.payload_hash == repo.payload_hash(PAYLOAD)
    assert obs.observed_at == obs.last_observed_at == T1
    assert obs.available_at == KICKOFF + timedelta(hours=6)  # disponibilidad sintética de backfill
    assert (obs.source, obs.availability, obs.teams_returned, obs.fixture_status_at_fetch) == ("backfill", "available", 2, "FT")


def test_same_hash_only_advances_last_observed_at(db_session, match):
    first = _observe(db_session, match.id)
    later = T1 + timedelta(days=2)
    again = _observe(db_session, match.id, payload=[dict(e) for e in PAYLOAD], observed_at=later, available_at=later)
    assert (again.outcome, again.observation_id) == ("unchanged", first.observation_id)
    (obs,) = _observations(db_session, match.id)
    assert obs.observed_at == T1 and obs.last_observed_at == later
    assert obs.available_at == KICKOFF + timedelta(hours=6)  # se conserva la de la versión original
    # Una observación repetida más antigua no hace retroceder last_observed_at
    _observe(db_session, match.id, observed_at=T1)
    assert _observations(db_session, match.id)[0].last_observed_at == later


def test_changed_hash_rotates_latest_and_keeps_history(db_session, match):
    first = _observe(db_session, match.id)
    changed_payload = [{**PAYLOAD[0], "statistics": PAYLOAD[0]["statistics"][:-1]}, PAYLOAD[1]]
    later = T1 + timedelta(hours=48)
    second = _observe(db_session, match.id, payload=changed_payload, observed_at=later, source="live", available_at=later)
    assert second.outcome == "changed" and second.previous_observation_id == first.observation_id
    old, new = _observations(db_session, match.id)
    assert (old.is_latest, new.is_latest) == (False, True)
    assert old.payload == PAYLOAD and new.payload == changed_payload  # historial preservado
    assert repo.get_latest_observation(db_session, match.id, PROVIDER).id == new.id
    # Vuelve el contenido original: es una versión nueva (A → B → A), no se reutiliza la vieja
    third = _observe(db_session, match.id, observed_at=later + timedelta(hours=1), available_at=later)
    assert third.outcome == "changed" and len(_observations(db_session, match.id)) == 3


def test_out_of_order_changed_observation_is_rejected(db_session, match):
    _observe(db_session, match.id, observed_at=T1)
    with pytest.raises(ValueError, match="fuera de orden"):
        _observe(db_session, match.id, payload=[], observed_at=T1 - timedelta(hours=1))


def test_empty_and_partial_observations(db_session, match):
    empty_payload = [{"team": {"id": 63}, "statistics": []}, {"team": {"id": 34}, "statistics": []}]
    result = _observe(db_session, match.id, payload=empty_payload, availability="empty", teams_returned=0)
    assert result.outcome == "created"
    partial = _observe(db_session, match.id, payload=[PAYLOAD[0]], availability="partial", teams_returned=1,
                       observed_at=T1 + timedelta(hours=1))
    assert partial.outcome == "changed"
    assert [(o.availability, o.teams_returned) for o in _observations(db_session, match.id)] == [("empty", 0), ("partial", 1)]


def test_observations_are_per_provider(db_session, match):
    a = _observe(db_session, match.id)
    b = repo.record_observation(db_session, fixture_id=match.id, provider="5dollarfootballapi", provider_fixture_id="x",
                                payload=PAYLOAD, source="manual", availability="available", teams_returned=2,
                                observed_at=T1, available_at=T1)
    assert a.outcome == b.outcome == "created" and a.observation_id != b.observation_id


# --- Estado normalizado ---------------------------------------------------------------------


def test_normalized_two_team_insert(db_session, match):
    obs = _observe(db_session, match.id).observation_id
    assert _upsert(db_session, match.id, match.home_team_id, obs).outcome == "created"
    assert _upsert(db_session, match.id, match.away_team_id, obs, _values(shots_total=7), side="away").outcome == "created"
    home, away = _row(db_session, match.id, match.home_team_id), _row(db_session, match.id, match.away_team_id)
    assert (home.side, away.side, home.observation_id, away.observation_id) == ("home", "away", obs, obs)
    assert (home.shots_total, away.shots_total, home.normalizer_version) == (15, 7, 1)


def test_normalized_update_is_material_change(db_session, match):
    obs = _observe(db_session, match.id).observation_id
    _upsert(db_session, match.id, match.home_team_id, obs)
    before = _row(db_session, match.id, match.home_team_id)
    result = _upsert(db_session, match.id, match.home_team_id, obs, _values(corners=6))
    assert result == repo.RowResult("updated")
    after = _row(db_session, match.id, match.home_team_id)
    assert after.corners == 6 and after.id == before.id and after.created_at == before.created_at


def test_normalized_unchanged(db_session, match):
    obs = _observe(db_session, match.id).observation_id
    _upsert(db_session, match.id, match.home_team_id, obs)
    assert _upsert(db_session, match.id, match.home_team_id, obs) == repo.RowResult("unchanged", relinked=False)


def test_new_observation_with_identical_values_relinks_without_counting_update(db_session, match):
    first = _observe(db_session, match.id).observation_id
    _upsert(db_session, match.id, match.home_team_id, first)
    # Cambia el raw (p. ej. un tipo solo-raw) pero no los valores normalizados
    second = _observe(db_session, match.id, payload=PAYLOAD + [], observed_at=T1 + timedelta(hours=1),
                      fixture_status_at_fetch="FT").observation_id
    assert second == first  # mismo contenido → misma observación
    changed_raw = [{**PAYLOAD[0], "statistics": PAYLOAD[0]["statistics"] + [{"type": "Nuevo", "value": 1}]}, PAYLOAD[1]]
    third = _observe(db_session, match.id, payload=changed_raw, observed_at=T1 + timedelta(hours=2)).observation_id
    result = _upsert(db_session, match.id, match.home_team_id, third)
    assert result == repo.RowResult("unchanged", relinked=True)
    assert _row(db_session, match.id, match.home_team_id).observation_id == third
    # Nueva versión del normalizador con los mismos valores: también solo re-enlace
    assert _upsert(db_session, match.id, match.home_team_id, third, version=2) == repo.RowResult("unchanged", relinked=True)
    assert _row(db_session, match.id, match.home_team_id).normalizer_version == 2


def test_side_change_is_material(db_session, match):
    obs = _observe(db_session, match.id).observation_id
    _upsert(db_session, match.id, match.home_team_id, obs)
    assert _upsert(db_session, match.id, match.home_team_id, obs, side="away").outcome == "updated"


def test_decimal_precision_round_trip_without_false_updates(db_session, match):
    obs = _observe(db_session, match.id).observation_id
    values = _values(possession_pct=Decimal("47.5"), passes_pct=Decimal("84"), expected_goals=Decimal("0.125"))
    _upsert(db_session, match.id, match.home_team_id, obs, values)
    row = _row(db_session, match.id, match.home_team_id)
    assert (row.possession_pct, row.passes_pct, row.expected_goals) == (Decimal("47.50"), Decimal("84.00"), Decimal("0.125"))
    # Mismo valor con otra representación decimal: no es un cambio
    same = _values(possession_pct=Decimal("47.50"), passes_pct=Decimal("84.0"), expected_goals=Decimal("0.1250"))
    assert _upsert(db_session, match.id, match.home_team_id, obs, same).outcome == "unchanged"
    # Más decimales que la columna: se redondea igual que PostgreSQL y no da falsos updates
    finer = _values(possession_pct=Decimal("47.5"), passes_pct=Decimal("84"), expected_goals=Decimal("0.1251"))
    assert _upsert(db_session, match.id, match.home_team_id, obs, finer).outcome == "unchanged"
    assert _upsert(db_session, match.id, match.home_team_id, obs, _values(expected_goals=Decimal("2.0835"))).outcome == "updated"
    assert _row(db_session, match.id, match.home_team_id).expected_goals == Decimal("2.084")


def test_null_and_zero_are_distinct(db_session, match):
    obs = _observe(db_session, match.id).observation_id
    _upsert(db_session, match.id, match.home_team_id, obs, _values(red_cards=None, offsides=0))
    row = _row(db_session, match.id, match.home_team_id)
    assert row.red_cards is None and row.offsides == 0 and row.passes_total is None
    # NULL → 0 y 0 → NULL son cambios materiales
    assert _upsert(db_session, match.id, match.home_team_id, obs, _values(red_cards=0, offsides=0)).outcome == "updated"
    assert _upsert(db_session, match.id, match.home_team_id, obs, _values(red_cards=0, offsides=None)).outcome == "updated"
    row = _row(db_session, match.id, match.home_team_id)
    assert (row.red_cards, row.offsides) == (0, None)


def test_all_null_values_are_storable(db_session, match):
    obs = _observe(db_session, match.id).observation_id
    assert _upsert(db_session, match.id, match.home_team_id, obs, TeamStatisticValues()).outcome == "created"
    row = _row(db_session, match.id, match.home_team_id)
    assert all(getattr(row, f) is None for f in TeamStatisticValues.model_fields)


# --- Transacciones ------------------------------------------------------------------------


def test_repository_never_commits(db_session, match, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("el repository no debe hacer commit ni rollback")

    monkeypatch.setattr(db_session, "commit", forbidden)
    monkeypatch.setattr(db_session, "rollback", forbidden)
    obs = _observe(db_session, match.id).observation_id
    _observe(db_session, match.id, payload=[], availability="empty", teams_returned=0, observed_at=T1 + timedelta(hours=1))
    _upsert(db_session, match.id, match.home_team_id, obs)
    _upsert(db_session, match.id, match.home_team_id, obs, _values(corners=1))


def test_rollback_discards_observations_and_rows(db_session, match):
    savepoint = db_session.begin_nested()
    obs = _observe(db_session, match.id).observation_id
    _upsert(db_session, match.id, match.home_team_id, obs)
    savepoint.rollback()
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(FixtureStatisticsObservation)) == 0
    assert db_session.scalar(select(func.count()).select_from(FixtureTeamStatistics)) == 0
    # Y se puede volver a empezar limpio
    assert _observe(db_session, match.id).outcome == "created"
