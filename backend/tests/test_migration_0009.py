"""Migración 0009 (DI-A5D): esquema de sync_unit_states y provider_incidents. Solo BD de tests.

Contrato: docs/data-integrity-status.md, "DI-A5D DESIGN: FROZEN". A5D es solo esquema: aquí se
comprueban la migración, las claves, las FK y los CHECK que protegen los estados válidos. Ningún
servicio usa estas tablas todavía (A5E).
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import alembic_run, make_competition

pytestmark = pytest.mark.db

APP_DIR = Path(__file__).resolve().parents[1] / "app"
T0 = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
SUS = "sync_unit_states"
INC = "provider_incidents"


def _version(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


# --- Migración y grafo ----------------------------------------------------------------------


def test_upgrade_downgrade_upgrade_0009(migrated_db):
    from app.db.database import engine

    try:
        alembic_run("downgrade", "0008")
        with engine.connect() as conn:
            tables = inspect(conn).get_table_names()
            assert SUS not in tables and INC not in tables
            assert {"fixture_observations", "providers", "seasons"} <= set(tables)  # 0008 intacta
            assert _version(conn) == "0008"
        alembic_run("upgrade", "0009")
        with engine.connect() as conn:
            tables = inspect(conn).get_table_names()
            assert SUS in tables and INC in tables
            assert conn.execute(text(f"SELECT (SELECT count(*) FROM {SUS}) + (SELECT count(*) FROM {INC})")).scalar_one() == 0
            assert _version(conn) == "0009"
    finally:
        alembic_run("upgrade", "head")


def test_downgrade_drops_existing_recovery_rows(migrated_db):
    """El downgrade elimina las tablas aunque tengan filas (se pierde el estado: documentado)."""
    from app.db.database import engine

    try:
        with engine.begin() as conn:
            conn.execute(text(f"INSERT INTO {INC} (provider, kind, last_failure_at, last_error_class) "
                              "VALUES ('api-football', 'auth', statement_timestamp(), 'ProviderAuthError')"))
        alembic_run("downgrade", "0008")
        with engine.connect() as conn:
            assert INC not in inspect(conn).get_table_names()
    finally:
        alembic_run("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {INC}"))


def test_alembic_graph_single_head_0009():
    from alembic.script import ScriptDirectory

    from tests.conftest import _alembic_config

    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == ["0009"]
    assert script.get_revision("0009").down_revision == "0008"
    assert [r.revision for r in script.walk_revisions()] == [
        "0009", "0008", "0007", "0006", "0005", "0004", "0003", "0002", "0001",
    ]


# --- Esquema --------------------------------------------------------------------------------


def test_sync_unit_states_columns_keys_and_indexes(migrated_db):
    from app.db.database import engine

    with engine.connect() as conn:
        insp = inspect(conn)
        cols = {c["name"]: c for c in insp.get_columns(SUS)}
        assert list(cols) == [
            "unit_kind", "provider", "season_id", "last_attempt_started_at", "last_outcome", "last_outcome_at",
            "last_success_at", "consecutive_failures", "last_failure_at", "last_error_scope", "last_error_class",
            "last_error_status", "last_error_message", "created_at", "updated_at",
        ]
        for c in ("unit_kind", "provider", "season_id", "consecutive_failures", "created_at", "updated_at"):
            assert not cols[c]["nullable"], c
        for c in set(cols) - {"unit_kind", "provider", "season_id", "consecutive_failures", "created_at", "updated_at"}:
            assert cols[c]["nullable"], c
        # last_success_at lo pone la transacción de dominio (A5E), nunca un default
        assert cols["last_success_at"]["default"] is None
        assert "statement_timestamp()" in cols["created_at"]["default"]
        assert "statement_timestamp()" in cols["updated_at"]["default"]
        pk = insp.get_pk_constraint(SUS)
        assert (pk["name"], pk["constrained_columns"]) == ("pk_sync_unit_states", ["unit_kind", "provider", "season_id"])
        indexes = {i["name"]: (i["column_names"], i["unique"]) for i in insp.get_indexes(SUS)}
        assert indexes == {"ix_sync_unit_states_season": (["season_id"], False)}
        fks = {tuple(fk["constrained_columns"]): (fk["referred_table"], fk["options"].get("ondelete")) for fk in insp.get_foreign_keys(SUS)}
        assert fks == {("provider",): ("providers", "RESTRICT"), ("season_id",): ("seasons", "CASCADE")}
        assert {c["name"] for c in insp.get_check_constraints(SUS)} == {
            "ck_sync_unit_states_unit_kind", "ck_sync_unit_states_outcome", "ck_sync_unit_states_error_scope",
            "ck_sync_unit_states_consecutive_failures", "ck_sync_unit_states_error_status",
            "ck_sync_unit_states_error_message", "ck_sync_unit_states_outcome_at", "ck_sync_unit_states_succeeded",
            "ck_sync_unit_states_failed", "ck_sync_unit_states_failure_metadata", "ck_sync_unit_states_outcome_order",
        }
        # Nada de A5F ni de orquestación
        assert not {"lease_until", "claimed_by", "next_due_at", "priority_class"} & set(cols)


def test_provider_incidents_columns_keys_and_indexes(migrated_db):
    from app.db.database import engine

    with engine.connect() as conn:
        insp = inspect(conn)
        cols = {c["name"]: c for c in insp.get_columns(INC)}
        assert list(cols) == [
            "id", "provider", "kind", "opened_at", "last_failure_at", "closed_at", "close_reason", "failure_count",
            "affected_units", "last_error_class", "last_status_code", "last_endpoint", "retry_after_until",
        ]
        required = {"id", "provider", "kind", "opened_at", "last_failure_at", "failure_count", "affected_units", "last_error_class"}
        for c in cols:
            assert cols[c]["nullable"] is (c not in required), c
        assert str(cols["id"]["type"]) == "BIGINT" and cols["id"]["identity"]["always"]
        assert "statement_timestamp()" in cols["opened_at"]["default"]
        assert insp.get_pk_constraint(INC)["constrained_columns"] == ["id"]
        indexes = {i["name"]: i for i in insp.get_indexes(INC)}
        assert set(indexes) == {"uq_provider_incidents_open", "ix_provider_incidents_provider_opened"}
        assert indexes["uq_provider_incidents_open"]["column_names"] == ["provider", "kind"]
        assert indexes["uq_provider_incidents_open"]["unique"]
        assert indexes["uq_provider_incidents_open"]["dialect_options"]["postgresql_where"] == "(closed_at IS NULL)"
        assert indexes["ix_provider_incidents_provider_opened"]["column_names"] == ["provider", "opened_at"]
        assert not indexes["ix_provider_incidents_provider_opened"]["unique"]
        fks = {tuple(fk["constrained_columns"]): (fk["referred_table"], fk["options"].get("ondelete")) for fk in insp.get_foreign_keys(INC)}
        assert fks == {("provider",): ("providers", "RESTRICT")}
        assert {c["name"] for c in insp.get_check_constraints(INC)} == {
            "ck_provider_incidents_kind", "ck_provider_incidents_close_reason", "ck_provider_incidents_closed",
            "ck_provider_incidents_last_failure_order", "ck_provider_incidents_closed_order",
            "ck_provider_incidents_failure_count", "ck_provider_incidents_affected_units",
            "ck_provider_incidents_status_code", "ck_provider_incidents_endpoint",
        }
        assert not {"api_key", "headers", "payload", "next_probe_at"} & set(cols)


# --- sync_unit_states: estados válidos e inválidos -----------------------------------------


@pytest.fixture
def season_id(db_session) -> int:
    _, sid = make_competition(db_session, 265)
    db_session.flush()
    return sid


def _unit(db, season: int, **overrides) -> None:
    values = {"unit_kind": "fixtures_season", "provider": "api-football", "season_id": season}
    values.update(overrides)
    cols = ", ".join(values)
    db.execute(text(f"INSERT INTO {SUS} ({cols}) VALUES ({', '.join(':' + c for c in values)})"), values)


SUCCEEDED = {"last_attempt_started_at": T0, "last_outcome": "succeeded", "last_outcome_at": T0 + timedelta(seconds=2),
             "last_success_at": T0 + timedelta(seconds=2)}
FAILED = {"last_attempt_started_at": T0, "last_outcome": "failed", "last_outcome_at": T0 + timedelta(seconds=2),
          "last_failure_at": T0 + timedelta(seconds=2), "consecutive_failures": 1, "last_error_scope": "unit",
          "last_error_class": "ProviderResponseError", "last_error_status": 500, "last_error_message": "HTTP 500"}


@pytest.mark.parametrize(
    "state",
    [
        pytest.param({}, id="row_without_attempt"),
        pytest.param({"last_attempt_started_at": T0}, id="interrupted_attempt"),
        pytest.param(SUCCEEDED, id="succeeded"),
        pytest.param(FAILED, id="failed_unit"),
        pytest.param({**FAILED, "last_error_scope": "provider", "last_error_status": None, "consecutive_failures": 3}, id="failed_provider"),
        pytest.param({**FAILED, "last_error_scope": "database", "last_error_status": None, "last_error_message": None}, id="failed_database"),
        pytest.param({"last_attempt_started_at": T0, "last_outcome": "skipped", "last_outcome_at": T0 + timedelta(seconds=1)}, id="skipped"),
        pytest.param({**SUCCEEDED, "last_attempt_started_at": T0 + timedelta(minutes=5)}, id="interrupted_after_success"),
        pytest.param({**FAILED, "last_success_at": T0 - timedelta(days=1)}, id="failed_after_older_success"),
        pytest.param({**SUCCEEDED, "unit_kind": "catalog_season_teams"}, id="catalog_season_teams"),
    ],
)
def test_valid_unit_states_are_accepted(db_session, season_id, state):
    _unit(db_session, season_id, **state)
    db_session.flush()


@pytest.mark.parametrize(
    "state, constraint",
    [
        ({"unit_kind": "fixtures_range"}, "ck_sync_unit_states_unit_kind"),
        ({"unit_kind": "statistics"}, "ck_sync_unit_states_unit_kind"),
        ({**SUCCEEDED, "last_outcome": "running"}, "ck_sync_unit_states_outcome"),
        ({**FAILED, "last_error_scope": "competition"}, "ck_sync_unit_states_error_scope"),
        ({"consecutive_failures": -1}, "ck_sync_unit_states_consecutive_failures"),
        ({**FAILED, "last_error_status": 99}, "ck_sync_unit_states_error_status"),
        ({**FAILED, "last_error_status": 600}, "ck_sync_unit_states_error_status"),
        ({**FAILED, "last_error_message": "x" * 501}, "ck_sync_unit_states_error_message"),
        ({"last_outcome": "skipped"}, "ck_sync_unit_states_outcome_at"),
        ({"last_outcome_at": T0}, "ck_sync_unit_states_outcome_at"),
        ({**SUCCEEDED, "last_success_at": None}, "ck_sync_unit_states_succeeded"),
        ({**SUCCEEDED, "last_success_at": T0}, "ck_sync_unit_states_succeeded"),
        ({**SUCCEEDED, **{k: FAILED[k] for k in ("last_failure_at", "last_error_scope", "last_error_class")}, "consecutive_failures": 1},
         "ck_sync_unit_states_succeeded"),
        ({**FAILED, "consecutive_failures": 0}, "ck_sync_unit_states_failed"),
        ({**FAILED, "last_failure_at": T0}, "ck_sync_unit_states_failed"),
        ({**FAILED, "last_error_class": None}, "ck_sync_unit_states_failed"),
        ({**FAILED, "last_error_scope": None}, "ck_sync_unit_states_failed"),
        ({**SUCCEEDED, "last_error_class": "ProviderError"}, "ck_sync_unit_states_failure_metadata"),
        ({**SUCCEEDED, "last_error_message": "boom"}, "ck_sync_unit_states_failure_metadata"),
        ({**SUCCEEDED, "last_error_status": 500}, "ck_sync_unit_states_failure_metadata"),
        ({"consecutive_failures": 2}, "ck_sync_unit_states_failure_metadata"),
        ({"last_success_at": T0}, "ck_sync_unit_states_outcome_order"),
        ({**FAILED, "last_success_at": FAILED["last_outcome_at"] + timedelta(seconds=1)}, "ck_sync_unit_states_outcome_order"),
        ({"season_id": 999_999_999}, "sync_unit_states_season_id_fkey"),
        ({"provider": "no-such-provider"}, "sync_unit_states_provider_fkey"),
    ],
)
def test_invalid_unit_states_are_rejected(db_session, season_id, state, constraint):
    with pytest.raises(IntegrityError) as excinfo:
        _unit(db_session, season_id, **state)
        db_session.flush()
    assert constraint in str(excinfo.value)


def test_unit_identity_is_provider_kind_and_season(db_session, season_id):
    _unit(db_session, season_id)
    _unit(db_session, season_id, unit_kind="catalog_season_teams")  # otra unidad de la misma temporada
    _unit(db_session, season_id, provider="5dollarfootballapi")  # otro proveedor
    with pytest.raises(IntegrityError) as excinfo:
        _unit(db_session, season_id)
    assert "pk_sync_unit_states" in str(excinfo.value)


def test_deleting_a_season_cascades_its_unit_state(db_session, season_id):
    _unit(db_session, season_id, **SUCCEEDED)
    _unit(db_session, season_id, unit_kind="catalog_season_teams")
    db_session.execute(text("DELETE FROM seasons WHERE id = :s"), {"s": season_id})
    assert db_session.execute(text(f"SELECT count(*) FROM {SUS}")).scalar_one() == 0


def test_provider_with_unit_state_cannot_be_deleted(db_session, season_id):
    _unit(db_session, season_id, provider="5dollarfootballapi")
    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(text("DELETE FROM providers WHERE code = '5dollarfootballapi'"))
    assert "sync_unit_states_provider_fkey" in str(excinfo.value)


def test_success_rolls_back_with_its_domain_transaction(db_session, season_id):
    """El éxito y su dominio son una transacción: deshacerla también deshace last_success_at."""
    db_session.flush()
    savepoint = db_session.begin_nested()
    db_session.execute(text("UPDATE seasons SET is_current = false WHERE id = :s"), {"s": season_id})
    _unit(db_session, season_id, **SUCCEEDED)
    savepoint.rollback()
    assert db_session.execute(text(f"SELECT count(*) FROM {SUS}")).scalar_one() == 0
    assert db_session.execute(text("SELECT is_current FROM seasons WHERE id = :s"), {"s": season_id}).scalar_one()


def test_statement_timestamp_is_per_statement_not_transaction_start(db_session, season_id):
    """El default es statement_timestamp(): dentro de UNA transacción, cada sentencia tiene su instante
    (now()/CURRENT_TIMESTAMP darían el mismo valor). Es la semántica congelada de last_success_at."""
    tx_start = db_session.execute(text("SELECT now()")).scalar_one()
    db_session.execute(text("SELECT pg_sleep(0.05)"))
    _unit(db_session, season_id)
    created = db_session.execute(text(f"SELECT created_at FROM {SUS}")).scalar_one()
    assert created > tx_start
    stamped = db_session.execute(text("SELECT statement_timestamp()")).scalar_one()
    _unit(db_session, season_id, unit_kind="catalog_season_teams", last_outcome="succeeded",
          last_outcome_at=stamped, last_success_at=stamped)
    assert db_session.execute(text(f"SELECT last_success_at > :t FROM {SUS} WHERE unit_kind = 'catalog_season_teams'"),
                              {"t": tx_start}).scalar_one()


# --- provider_incidents ---------------------------------------------------------------------


def _incident(db, **overrides) -> int:
    values = {"provider": "api-football", "kind": "auth", "last_failure_at": T0 + timedelta(hours=1),
              "opened_at": T0, "last_error_class": "ProviderAuthError"}
    values.update(overrides)
    cols = ", ".join(values)
    return db.execute(text(f"INSERT INTO {INC} ({cols}) VALUES ({', '.join(':' + c for c in values)}) RETURNING id"), values).scalar_one()


@pytest.mark.parametrize("kind", ["auth", "quota", "rate_limit", "outage"])
def test_valid_incident_kinds_are_accepted(db_session, kind):
    _incident(db_session, kind=kind)


def test_incident_opened_at_defaults_to_statement_timestamp(db_session):
    row = db_session.execute(text(
        f"INSERT INTO {INC} (provider, kind, last_failure_at, last_error_class) "
        "VALUES ('api-football', 'quota', statement_timestamp(), 'ProviderQuotaExceededError') "
        "RETURNING opened_at, failure_count, affected_units, closed_at, close_reason")).one()
    assert row.opened_at is not None and (row.failure_count, row.affected_units, row.closed_at, row.close_reason) == (1, 0, None, None)


@pytest.mark.parametrize(
    "overrides, constraint",
    [
        ({"kind": "not_configured"}, "ck_provider_incidents_kind"),
        ({"kind": "timeout"}, "ck_provider_incidents_kind"),
        ({"failure_count": 0}, "ck_provider_incidents_failure_count"),
        ({"affected_units": -1}, "ck_provider_incidents_affected_units"),
        ({"last_status_code": 99}, "ck_provider_incidents_status_code"),
        ({"last_status_code": 600}, "ck_provider_incidents_status_code"),
        ({"last_endpoint": "/fixtures?league=265&season=2026"}, "ck_provider_incidents_endpoint"),
        ({"last_endpoint": "https://v3.football.api-sports.io/fixtures"}, "ck_provider_incidents_endpoint"),
        ({"last_endpoint": "fixtures"}, "ck_provider_incidents_endpoint"),
        ({"closed_at": T0 + timedelta(hours=2)}, "ck_provider_incidents_closed"),
        ({"close_reason": "manual"}, "ck_provider_incidents_closed"),
        ({"closed_at": T0 + timedelta(hours=2), "close_reason": "probe_ok"}, "ck_provider_incidents_close_reason"),
        ({"last_failure_at": T0 - timedelta(seconds=1)}, "ck_provider_incidents_last_failure_order"),
        ({"closed_at": T0 - timedelta(seconds=1), "close_reason": "manual", "last_failure_at": T0}, "ck_provider_incidents_closed_order"),
        ({"provider": "no-such-provider"}, "provider_incidents_provider_fkey"),
        ({"last_error_class": None}, "last_error_class"),
        ({"opened_at": None}, "opened_at"),
    ],
)
def test_invalid_incidents_are_rejected(db_session, overrides, constraint):
    with pytest.raises(IntegrityError) as excinfo:
        _incident(db_session, **overrides)
    assert constraint in str(excinfo.value)


def test_valid_incident_metadata_is_accepted(db_session):
    _incident(db_session, kind="rate_limit", last_status_code=429, last_endpoint="/fixtures",
              retry_after_until=T0 + timedelta(minutes=1), failure_count=3, affected_units=26)
    _incident(db_session, kind="quota", closed_at=T0 + timedelta(hours=3), close_reason="provider_recovered")


def test_at_most_one_open_incident_per_provider_and_kind(db_session):
    _incident(db_session)
    _incident(db_session, kind="quota")  # otro tipo
    _incident(db_session, provider="5dollarfootballapi")  # otro proveedor
    _incident(db_session, closed_at=T0 + timedelta(hours=2), close_reason="manual")  # cerrado: historia
    _incident(db_session, closed_at=T0 + timedelta(hours=3), close_reason="provider_recovered")
    with pytest.raises(IntegrityError) as excinfo:
        _incident(db_session)
    assert "uq_provider_incidents_open" in str(excinfo.value)


def test_open_incident_upsert_continues_the_same_row(db_session):
    """El upsert congelado (ON CONFLICT sobre el índice parcial) continúa el incidente abierto en vez
    de duplicarlo, y uno cerrado no se reabre: el siguiente fallo crea otra fila."""
    upsert = text(
        f"INSERT INTO {INC} (provider, kind, last_failure_at, last_error_class) "
        "VALUES ('api-football', 'auth', statement_timestamp(), 'ProviderAuthError') "
        "ON CONFLICT (provider, kind) WHERE closed_at IS NULL DO UPDATE SET "
        f"failure_count = {INC}.failure_count + 1, last_failure_at = greatest({INC}.last_failure_at, excluded.last_failure_at) "
        "RETURNING id, failure_count"
    )
    first = db_session.execute(upsert).one()
    second = db_session.execute(upsert).one()
    assert second.id == first.id and (first.failure_count, second.failure_count) == (1, 2)
    db_session.execute(text(f"UPDATE {INC} SET closed_at = statement_timestamp(), close_reason = 'provider_recovered' WHERE id = :i"),
                       {"i": first.id})
    third = db_session.execute(upsert).one()
    assert third.id != first.id and third.failure_count == 1
    assert db_session.execute(text(f"SELECT count(*) FROM {INC}")).scalar_one() == 2


def test_provider_with_incident_history_cannot_be_deleted(db_session):
    _incident(db_session, provider="5dollarfootballapi", closed_at=T0 + timedelta(hours=2), close_reason="manual")
    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(text("DELETE FROM providers WHERE code = '5dollarfootballapi'"))
    assert "provider_incidents_provider_fkey" in str(excinfo.value)


# --- Alcance de A5D: solo esquema ------------------------------------------------------------


def _app_sources() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in APP_DIR.rglob("*.py")}


def test_outage_kind_has_no_producer():
    """'outage' está reservado: fuera de la definición del modelo ningún código lo usa."""
    users = [p.relative_to(APP_DIR).as_posix() for p, src in _app_sources().items() if re.search(r"\boutage\b", src)]
    assert users == ["models/sync_recovery.py"]


def test_recovery_tables_are_not_wired_into_services_yet():
    """A5D es solo esquema: ningún servicio, job ni repositorio lee o escribe estas tablas (A5E)."""
    pattern = re.compile(r"\b(SyncUnitState|ProviderIncident|sync_unit_states|provider_incidents)\b")
    users = sorted(p.relative_to(APP_DIR).as_posix() for p, src in _app_sources().items() if pattern.search(src))
    assert users == ["models/__init__.py", "models/sync_recovery.py"]
