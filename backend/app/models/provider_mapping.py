"""Relación entre entidades de Prediktia e IDs de proveedores externos.

Una tabla por entidad (con FK real) que comparte el mismo contrato de columnas:

- UNIQUE (provider, external_id): un ID externo apunta a una sola entidad interna.
- Sin UNIQUE sobre (provider, <entidad>_id): una entidad puede tener varios IDs del
  mismo proveedor (N:1), p. ej. una competición partida en varias filas del feed o un
  partido aplazado que el proveedor vuelve a crear con otro ID.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.database import Base

# Cómo se estableció el enlace
MATCH_METHODS = ("origin", "manual", "auto_exact", "auto_fuzzy")


class ProviderMappingMixin:
    """Columnas y constraints comunes a todas las tablas de mapeo."""

    # Nombre de la columna FK hacia la entidad (lo define cada subclase)
    entity_column: str

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text)
    # origin: la entidad se creó a partir de este proveedor
    # manual: confirmado por una persona
    # auto_exact / auto_fuzzy: matching automático
    match_method: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default=text("1"))
    # False cuando el ID externo ya no es válido para la entidad (sin borrarlo)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Última vez que el ID se observó en una respuesta o sync del proveedor
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Momento en que el mapeo se confirmó explícitamente (manual o revisado). Ningún sync lo toca
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @declared_attr
    def provider(cls) -> Mapped[str]:
        return mapped_column(Text, ForeignKey("providers.code", ondelete="RESTRICT"))

    @declared_attr.directive
    def __table_args__(cls) -> tuple:
        t = cls.__tablename__
        return (
            UniqueConstraint("provider", "external_id", name=f"uq_{t}_provider_external"),
            CheckConstraint(
                "external_id <> '' AND external_id = btrim(external_id) AND length(external_id) <= 64",
                name=f"ck_{t}_external_id_format",
            ),
            CheckConstraint("confidence >= 0 AND confidence <= 1", name=f"ck_{t}_confidence_range"),
            CheckConstraint(
                "match_method IN (" + ", ".join(f"'{m}'" for m in MATCH_METHODS) + ")",
                name=f"ck_{t}_match_method",
            ),
            CheckConstraint(
                "match_method NOT IN ('origin', 'manual') OR confidence = 1",
                name=f"ck_{t}_origin_confidence",
            ),
            Index(f"ix_{t}_{cls.entity_column}", cls.entity_column, "provider"),
        )


class CompetitionProviderMapping(ProviderMappingMixin, Base):
    __tablename__ = "competition_provider_mappings"
    entity_column = "competition_id"

    competition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("competitions.id", ondelete="CASCADE")
    )
    # Nombre tal como lo da el proveedor (auditoría del matching y alias)
    raw_name: Mapped[str | None] = mapped_column(Text)


class TeamProviderMapping(ProviderMappingMixin, Base):
    __tablename__ = "team_provider_mappings"
    entity_column = "team_id"

    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id", ondelete="CASCADE"))
    raw_name: Mapped[str | None] = mapped_column(Text)


class FixtureProviderMapping(ProviderMappingMixin, Base):
    __tablename__ = "fixture_provider_mappings"
    entity_column = "fixture_id"

    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id", ondelete="CASCADE"))
