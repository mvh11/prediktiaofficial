"""Identidad multi-proveedor (providers + mapeos) y marcador a 90' en fixtures

- Crea providers y siembra api-football y 5dollarfootballapi.
- Crea competition/team/fixture_provider_mappings con FK reales y hace backfill de los
  IDs actuales de API-Football (external_id) como mapeos 'origin'.
- Añade fixtures.fulltime_home/away y los rellena solo para FT (fulltime = goals).
  AET y PEN quedan NULL hasta el siguiente sync, que trae score.fulltime del proveedor.
- No borra ni modifica ninguna entidad: valida que los conteos no cambian.

Downgrade: vuelve al esquema 0003. No se pierde ningún dato de API-Football (external_id
nunca se toca), pero sí los valores de fulltime y cualquier mapeo de otros proveedores.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (tabla de mapeo, columna FK, tabla de la entidad, ¿tiene raw_name?)
MAPPING_TABLES = [
    ("competition_provider_mappings", "competition_id", "competitions", True),
    ("team_provider_mappings", "team_id", "teams", True),
    ("fixture_provider_mappings", "fixture_id", "fixtures", False),
]
ENTITY_TABLES = ["competitions", "seasons", "teams", "season_teams", "fixtures"]


def _count(table: str) -> int:
    return op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


def _create_mapping_table(table: str, fk_column: str, entity_table: str, with_raw_name: bool) -> None:
    columns = [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            fk_column,
            sa.Integer(),
            sa.ForeignKey(f"{entity_table}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.Text(),
            sa.ForeignKey("providers.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    ]
    if with_raw_name:
        columns.append(sa.Column("raw_name", sa.Text()))
    columns += [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "external_id", name=f"uq_{table}_provider_external"),
        sa.CheckConstraint(
            "external_id <> '' AND external_id = btrim(external_id) AND length(external_id) <= 64",
            name=f"ck_{table}_external_id_format",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name=f"ck_{table}_confidence_range"),
        sa.CheckConstraint(
            "match_method IN ('origin', 'manual', 'auto_exact', 'auto_fuzzy')",
            name=f"ck_{table}_match_method",
        ),
        sa.CheckConstraint(
            "match_method NOT IN ('origin', 'manual') OR confidence = 1",
            name=f"ck_{table}_origin_confidence",
        ),
    ]
    op.create_table(table, *columns)
    op.create_index(f"ix_{table}_{fk_column}", table, [fk_column, "provider"])


def upgrade() -> None:
    offline = context.is_offline_mode()
    before = {} if offline else {t: _count(t) for t in ENTITY_TABLES}

    # 1-2. Catálogo de proveedores
    op.create_table(
        "providers",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("id_scheme", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("code ~ '^[a-z0-9][a-z0-9_-]{1,39}$'", name="ck_providers_code_format"),
    )
    op.execute(
        "INSERT INTO providers (code, name, id_scheme) VALUES "
        "('api-football', 'API-Football', NULL), "
        "('5dollarfootballapi', '5DollarFootballAPI', 'public_v1')"
    )

    # 3-6. Tablas de mapeo
    for table, fk_column, entity_table, with_raw_name in MAPPING_TABLES:
        _create_mapping_table(table, fk_column, entity_table, with_raw_name)

    # 7. Backfill: los external_id actuales son IDs de API-Football ('origin').
    #    last_seen_at = última vez que un sync vio la entidad; verified_at queda NULL.
    for table, fk_column, entity_table, with_raw_name in MAPPING_TABLES:
        raw_col, raw_val = (", raw_name", ", name") if with_raw_name else ("", "")
        op.execute(
            f"INSERT INTO {table} "
            f"({fk_column}, provider, external_id, match_method, confidence, is_active{raw_col}, last_seen_at) "
            f"SELECT id, 'api-football', external_id::text, 'origin', 1, true{raw_val}, updated_at "
            f"FROM {entity_table}"
        )

    # 8. Validación: un mapeo de API-Football por entidad
    if not offline:
        for table, _fk, entity_table, _raw in MAPPING_TABLES:
            mapped = op.get_bind().execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE provider = 'api-football'")
            ).scalar_one()
            if mapped != before[entity_table]:
                raise RuntimeError(f"{table}: {mapped} mapeos para {before[entity_table]} filas de {entity_table}")

    # 9. Marcador a 90'
    op.add_column("fixtures", sa.Column("fulltime_home", sa.Integer()))
    op.add_column("fixtures", sa.Column("fulltime_away", sa.Integer()))

    # 10. Backfill solo de FT: sin prórroga, el marcador final es el de 90'.
    #     AET/PEN no se derivan de extratime (dato no fiable en el proveedor): los rellena el sync.
    op.execute(
        "UPDATE fixtures SET fulltime_home = home_goals, fulltime_away = away_goals "
        "WHERE status_short = 'FT' AND home_goals IS NOT NULL AND away_goals IS NOT NULL"
    )

    # 11. Constraints de fulltime (ya validan los datos rellenados)
    op.create_check_constraint(
        "ck_fixtures_fulltime_pair", "fixtures", "(fulltime_home IS NULL) = (fulltime_away IS NULL)"
    )
    op.create_check_constraint(
        "ck_fixtures_fulltime_finished",
        "fixtures",
        "fulltime_home IS NULL OR status_short IN ('FT', 'AET', 'PEN')",
    )
    op.create_check_constraint(
        "ck_fixtures_fulltime_nonneg",
        "fixtures",
        "fulltime_home IS NULL OR (fulltime_home >= 0 AND fulltime_away >= 0)",
    )

    # 12. Validación final: ninguna entidad ha cambiado de número
    if not offline:
        after = {t: _count(t) for t in ENTITY_TABLES}
        if after != before:
            raise RuntimeError(f"Los conteos cambiaron durante la migración: {before} -> {after}")


def downgrade() -> None:
    op.drop_constraint("ck_fixtures_fulltime_nonneg", "fixtures", type_="check")
    op.drop_constraint("ck_fixtures_fulltime_finished", "fixtures", type_="check")
    op.drop_constraint("ck_fixtures_fulltime_pair", "fixtures", type_="check")
    op.drop_column("fixtures", "fulltime_away")
    op.drop_column("fixtures", "fulltime_home")
    for table, _fk, _entity, _raw in reversed(MAPPING_TABLES):
        op.drop_table(table)
    op.drop_table("providers")
