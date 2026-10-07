"""Hash canónico v1 del estado observado (fixture_state_hash_v1, migración 0008). Solo BD de tests.

La referencia en Python vive SOLO aquí, para comprobar la especificación del contrato DI-A6C
(sección A). La aplicación nunca calcula el hash: lo hace siempre la función SQL. Los vectores de
oro congelan v1: si alguno cambia, la v1 se ha roto (un cambio de formato exige fixture_state_hash_v2).
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.repositories.fixture_repository import STATE_COLUMNS, state_hashes

pytestmark = pytest.mark.db

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
SCORES = STATE_COLUMNS[5:]


def reference_hash(state: dict) -> bytes:
    """Especificación v1: campos en orden fijo separados por chr(31), kickoff en µs UTC, netstring, \\N."""

    def encode(column: str, value) -> str:
        if value is None:
            return "\\N"
        if column == "kickoff_at":
            return str((value - EPOCH) // timedelta(microseconds=1))
        if column == "status_short":
            return f"{len(value.encode('utf-8'))}:{value}"
        return str(value)

    canonical = "\x1f".join(["fixture_state:v1", *(encode(c, state.get(c)) for c in STATE_COLUMNS)])
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def state(**overrides) -> dict:
    values = {"kickoff_at": KICKOFF, "status_short": "NS", "season_id": 7, "home_team_id": 11, "away_team_id": 12}
    values.update({c: None for c in SCORES})
    values.update(overrides)
    return values


PEN = dict(
    home_goals=1, away_goals=1, halftime_home=0, halftime_away=1, fulltime_home=1, fulltime_away=1,
    extratime_home=0, extratime_away=0, penalty_home=4, penalty_away=3,
)
# Congelados: no se regeneran. Cambiar uno significa que la v1 dejó de ser la v1
GOLDEN = [
    ("ns_all_null", state(), "f8b32c979c81f3e7ff6d7d5da98135b7a425f5d797d0d07edccbc62c4437be59"),
    ("ns_zero_goals", state(home_goals=0, away_goals=0), "49252c809df1f9ea5d96c4fcdafd7c583dc9ca9b75b8a91d956b25b7d7586ade"),
    ("pen_complete", state(status_short="PEN", **PEN), "873a91f089ecc046b29b77c2ca3b9bf0139393f4c4218b246ab608def6d10103"),
    ("odd_status", state(status_short="A|\x1f:é\\N"), "3fbb7a1253cd1a7064571edcdaab6d9cd93a643a051a0c428be0f0e6565a5314"),
    ("sub_second_kickoff", state(kickoff_at=datetime(1999, 12, 31, 23, 59, 59, 123456, tzinfo=timezone.utc)), "fbb22e0df255a9b0440bb15212cd3e32940e8668815deecae3ff70e473310a04"),
    ("negative_ids_scores", state(season_id=-1, home_goals=-2, away_goals=10), "5a3aa9d68c020ce37f44aa84c57e4851ca69163db1bef75913aa4c9255284b0d"),
]


def sql_hash(db, values: dict) -> bytes:
    return state_hashes(db, [values])[0]


@pytest.mark.parametrize("name,values,expected", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_vectors(db_session, name, values, expected):
    assert sql_hash(db_session, values).hex() == expected
    assert reference_hash(values).hex() == expected


def test_canonical_text_written_by_hand(db_session):
    """El texto v1 escrito a mano (sin la referencia): 2026-09-01T20:00Z = 1788292800000000 µs."""
    canonical = b"fixture_state:v1\x1f1788292800000000\x1f2:NS\x1f7\x1f11\x1f12" + b"\x1f\\N" * 10
    assert sql_hash(db_session, state()) == hashlib.sha256(canonical).digest()


def test_sql_matches_reference_on_many_states(db_session):
    states = [state(home_goals=g, away_goals=g + 1, status_short=s) for g in range(5) for s in ("NS", "1H", "FT")]
    states.append(state(kickoff_at=datetime(1960, 1, 1, 0, 0, 0, 1, tzinfo=timezone.utc)))  # antes del epoch
    assert state_hashes(db_session, states) == [reference_hash(s) for s in states]


def test_batch_keeps_input_order(db_session):
    states = [state(home_goals=i, away_goals=0) for i in range(30)]
    hashes = state_hashes(db_session, states)
    assert len(set(hashes)) == 30
    assert hashes == [sql_hash(db_session, s) for s in states]


def test_null_and_zero_are_different(db_session):
    assert sql_hash(db_session, state()) != sql_hash(db_session, state(home_goals=0, away_goals=0))
    assert sql_hash(db_session, state(home_goals=0, away_goals=None)) != sql_hash(db_session, state(home_goals=None, away_goals=0))


@pytest.mark.parametrize("column", list(STATE_COLUMNS))
def test_every_field_changes_the_hash(db_session, column):
    base = state(status_short="FT", **{c: 1 for c in SCORES})
    changed = dict(base)
    if column == "kickoff_at":
        changed[column] = KICKOFF + timedelta(microseconds=1)
    elif column == "status_short":
        changed[column] = "AET"
    else:
        changed[column] = base[column] + 1
    assert sql_hash(db_session, base) != sql_hash(db_session, changed)


def test_field_boundaries_cannot_be_shifted(db_session):
    """Separador y netstring: mover un valor de un campo a otro nunca da el mismo texto."""
    a = state(status_short="1", season_id=23)
    b = state(status_short="12", season_id=3)
    assert sql_hash(db_session, a) != sql_hash(db_session, b)


def test_same_instant_any_offset_or_session_timezone_same_hash(db_session):
    plus3 = KICKOFF.astimezone(timezone(timedelta(hours=3)))
    reference = sql_hash(db_session, state())
    assert sql_hash(db_session, state(kickoff_at=plus3)) == reference
    for zone in ("America/Sao_Paulo", "Asia/Tokyo", "UTC"):
        db_session.execute(text(f"SET LOCAL TIME ZONE '{zone}'"))
        assert sql_hash(db_session, state()) == reference
        literal = db_session.execute(
            text(
                "SELECT fixture_state_hash_v1(timestamptz '2026-09-01 20:00:00+00', 'NS', 7, 11, 12, "
                "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
            )
        ).scalar_one()
        assert bytes(literal) == reference


def test_hash_is_32_bytes_and_compares_bytewise(db_session):
    """bytea se compara byte a byte (memcmp), sin collation: el orden SQL coincide con el de bytes."""
    hashes = state_hashes(db_session, [state(home_goals=i, away_goals=i) for i in range(20)])
    assert all(len(h) == 32 for h in hashes)
    ordered = db_session.execute(
        text("SELECT h FROM unnest(CAST(:hs AS bytea[])) AS t(h) ORDER BY h"), {"hs": hashes}
    ).scalars().all()
    assert [bytes(h) for h in ordered] == sorted(hashes)


def test_function_is_immutable(db_session):
    volatility = db_session.execute(
        text("SELECT provolatile FROM pg_proc WHERE proname = 'fixture_state_hash_v1'")
    ).scalar_one()
    assert volatility == "i"
