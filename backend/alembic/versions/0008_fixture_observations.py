"""Evidencia temporal de fixtures (DI-A6): fixture_observations, orden en fixtures y bootstrap

Contrato: docs/data-integrity-status.md, sección "DI-A6C".

- Función SQL fixture_state_hash_v1: única definición del hash canónico del estado observado
  (sha256 de un texto versionado 'fixture_state:v1'). Nunca se redefine: un cambio es una v2.
- fixture_observations: evidencia inmutable (solo INSERT). Identidad UNIQUE(fixture_id, evidence_id);
  lectura temporal INDEX(fixture_id, observed_at). fixture_id y provider con ON DELETE RESTRICT;
  season_id y los equipos son enteros del momento, sin FK.
- fixtures.last_observed_at / last_state_hash: clave de orden (observed_at, state_hash) de la
  observación ganadora. Se crean nullables, las rellena el bootstrap y pasan a NOT NULL.

Bootstrap (en la misma transacción que el DDL):
- Requisito operativo: syncs y backfills parados. Si algún escritor tiene bloqueada fixtures, la
  migración falla al agotar lock_timeout (5 s) y se deshace entera; se vuelve a lanzar con calma.
- Un único bootstrap_at real (reloj de la app, tomado DESPUÉS del bloqueo) y un único evidence_id.
  No usa created_at, updated_at ni kickoff_at: solo afirma "este estado existía en bootstrap_at".
- Valida que hay exactamente una observación de bootstrap por fixture antes de SET NOT NULL.
- Todo es una transacción: un fallo o una conexión cortada no deja nada a medias y la migración se
  puede volver a ejecutar desde 0007.

Downgrade: elimina la tabla (se pierde la evidencia temporal), las dos columnas y la función.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCORE_COLUMNS = [
    "home_goals",
    "away_goals",
    "halftime_home",
    "halftime_away",
    "fulltime_home",
    "fulltime_away",
    "extratime_home",
    "extratime_away",
    "penalty_home",
    "penalty_away",
]
# Orden fijo de los campos del hash v1 (no se cambia nunca)
STATE_COLUMNS = ["kickoff_at", "status_short", "season_id", "home_team_id", "away_team_id", *SCORE_COLUMNS]
INTEGER_STATE_COLUMNS = STATE_COLUMNS[2:]

# Texto canónico v1, separado por chr(31): 'fixture_state:v1', kickoff en microsegundos desde el
# epoch UTC (resta de timestamptz: no depende de la zona horaria de la sesión), status_short como
# netstring '<octetos>:<texto>', enteros en decimal y NULL como \N. sha256 sobre UTF-8 (32 bytes)
HASH_FUNCTION = r"""
CREATE FUNCTION fixture_state_hash_v1(
    kickoff_at timestamptz, status_short text, season_id integer, home_team_id integer,
    away_team_id integer, home_goals integer, away_goals integer, halftime_home integer,
    halftime_away integer, fulltime_home integer, fulltime_away integer, extratime_home integer,
    extratime_away integer, penalty_home integer, penalty_away integer
) RETURNS bytea
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
SELECT sha256(convert_to(concat_ws(chr(31),
    'fixture_state:v1',
    coalesce(((extract(epoch FROM (kickoff_at - timestamptz '1970-01-01 00:00:00+00')) * 1000000)::bigint)::text, '\N'),
    coalesce(octet_length(status_short)::text || ':' || status_short, '\N'),
    coalesce(season_id::text, '\N'),
    coalesce(home_team_id::text, '\N'),
    coalesce(away_team_id::text, '\N'),
    coalesce(home_goals::text, '\N'),
    coalesce(away_goals::text, '\N'),
    coalesce(halftime_home::text, '\N'),
    coalesce(halftime_away::text, '\N'),
    coalesce(fulltime_home::text, '\N'),
    coalesce(fulltime_away::text, '\N'),
    coalesce(extratime_home::text, '\N'),
    coalesce(extratime_away::text, '\N'),
    coalesce(penalty_home::text, '\N'),
    coalesce(penalty_away::text, '\N')
), 'UTF8'))
$$
"""
HASH_OF_FIXTURE_ROW = f"fixture_state_hash_v1({', '.join(STATE_COLUMNS)})"
INDEX_NAME = "ix_fixture_observations_fixture_observed"
LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    # Toda espera de bloqueo de la migración (FK, ADD COLUMN, LOCK) falla tras lock_timeout en vez de
    # quedarse en cola detrás de un escritor: con syncs activas, la migración se deshace entera
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(HASH_FUNCTION)
    op.create_table(
        "fixture_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id", ondelete="RESTRICT"), nullable=False),
        # Sin default: observed_at lo pone siempre la app (momento de recepción), nunca la BD
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        # Solo auditoría física: nunca interviene en el orden ni en qué entra en un backtest
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), sa.ForeignKey("providers.code", ondelete="RESTRICT")),
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_short", sa.String(10), nullable=False),
        # Valores del momento, sin FK: la historia no depende de los datos de referencia actuales
        *[sa.Column(c, sa.Integer(), nullable=False) for c in ("season_id", "home_team_id", "away_team_id")],
        *[sa.Column(c, sa.Integer()) for c in SCORE_COLUMNS],
        sa.UniqueConstraint("fixture_id", "evidence_id", name="uq_fixture_observations_fixture_evidence"),
        sa.CheckConstraint("source IN ('sync', 'backfill', 'bootstrap')", name="ck_fixture_observations_source"),
        sa.CheckConstraint(
            "(source IN ('sync', 'backfill')) = (provider IS NOT NULL)", name="ck_fixture_observations_provider"
        ),
        sa.CheckConstraint("octet_length(state_hash) = 32", name="ck_fixture_observations_state_hash"),
        # Las mismas reglas de fulltime que fixtures (ck_fixtures_fulltime_*)
        sa.CheckConstraint(
            "(fulltime_home IS NULL) = (fulltime_away IS NULL)", name="ck_fixture_observations_fulltime_pair"
        ),
        sa.CheckConstraint(
            "fulltime_home IS NULL OR status_short IN ('FT', 'AET', 'PEN')",
            name="ck_fixture_observations_fulltime_finished",
        ),
        sa.CheckConstraint(
            "fulltime_home IS NULL OR (fulltime_home >= 0 AND fulltime_away >= 0)",
            name="ck_fixture_observations_fulltime_nonneg",
        ),
    )
    op.add_column("fixtures", sa.Column("last_observed_at", sa.DateTime(timezone=True)))
    op.add_column("fixtures", sa.Column("last_state_hash", sa.LargeBinary()))
    op.create_check_constraint("ck_fixtures_last_state_hash", "fixtures", "octet_length(last_state_hash) = 32")

    # --- Bootstrap -------------------------------------------------------------------------
    # ADD COLUMN ya tomó ACCESS EXCLUSIVE sobre fixtures hasta el commit; el LOCK explícito lo deja escrito
    op.execute("LOCK TABLE fixtures IN ACCESS EXCLUSIVE MODE")
    bootstrap_at = datetime.now(timezone.utc)  # después del bloqueo: ningún escritor posterior queda antes
    evidence_id = uuid.uuid4()
    conn = op.get_bind()
    columns = ", ".join(STATE_COLUMNS)
    conn.execute(
        sa.text(
            f"INSERT INTO fixture_observations (evidence_id, fixture_id, observed_at, source, provider, state_hash, {columns}) "
            f"SELECT :evidence_id, id, :bootstrap_at, 'bootstrap', NULL, {HASH_OF_FIXTURE_ROW}, {columns} FROM fixtures"
        ),
        {"evidence_id": evidence_id, "bootstrap_at": bootstrap_at},
    )
    conn.execute(
        sa.text(f"UPDATE fixtures SET last_observed_at = :bootstrap_at, last_state_hash = {HASH_OF_FIXTURE_ROW}"),
        {"bootstrap_at": bootstrap_at},
    )
    fixtures, observations, unmatched = conn.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM fixtures), "
            "(SELECT count(*) FROM fixture_observations WHERE evidence_id = :evidence_id), "
            "(SELECT count(*) FROM fixtures f LEFT JOIN fixture_observations o "
            "   ON o.fixture_id = f.id AND o.evidence_id = :evidence_id "
            " WHERE o.id IS NULL OR o.state_hash <> f.last_state_hash OR f.last_observed_at <> :bootstrap_at)"
        ),
        {"evidence_id": evidence_id, "bootstrap_at": bootstrap_at},
    ).one()
    if fixtures != observations or unmatched:
        raise RuntimeError(
            f"Bootstrap de DI-A6 inconsistente: {fixtures} fixtures, {observations} observaciones, "
            f"{unmatched} sin su observación de bootstrap. Se deshace la migración"
        )
    op.alter_column("fixtures", "last_observed_at", nullable=False)
    op.alter_column("fixtures", "last_state_hash", nullable=False)
    # El índice de lectura temporal, tras la carga del bootstrap
    op.create_index(INDEX_NAME, "fixture_observations", ["fixture_id", "observed_at"])


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="fixture_observations")
    op.drop_table("fixture_observations")
    op.drop_constraint("ck_fixtures_last_state_hash", "fixtures", type_="check")
    op.drop_column("fixtures", "last_state_hash")
    op.drop_column("fixtures", "last_observed_at")
    op.execute(
        "DROP FUNCTION fixture_state_hash_v1(timestamptz, text, integer, integer, integer, integer, integer, "
        "integer, integer, integer, integer, integer, integer, integer, integer)"
    )
