"""Acceso a la BD para los mapeos entre entidades de Prediktia e IDs de proveedores.

Funciona con cualquiera de las tablas de mapeo (competition/team/fixture), que
comparten columnas a través de ProviderMappingMixin.
"""

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.provider_mapping import ProviderMappingMixin


def canonical_external_id(value: int | str) -> str:
    """Forma canónica de un ID externo: los numéricos sin ceros a la izquierda ni espacios."""
    if isinstance(value, int):
        return str(value)
    value = value.strip()
    return str(int(value)) if value.isascii() and value.isdigit() else value


def upsert_origin_mappings(
    db: Session,
    model: type[ProviderMappingMixin],
    provider: str,
    rows: list[tuple[int, int | str, str | None]],
) -> None:
    """Registra los IDs de un proveedor del que se crearon/actualizaron las entidades.

    rows: (id interno, id externo, nombre en el proveedor o None).
    Una sola sentencia por lote. Si el mapeo ya existe solo se actualiza last_seen_at
    (y raw_name); verified_at, match_method y la entidad enlazada no se tocan.
    """
    if not rows:
        return
    has_raw_name = hasattr(model, "raw_name")
    by_external: dict[str, dict] = {}
    for internal_id, external_id, raw_name in rows:
        key = canonical_external_id(external_id)
        row = {
            model.entity_column: internal_id,
            "provider": provider,
            "external_id": key,
            "match_method": "origin",
            "confidence": 1,
            "last_seen_at": func.now(),
        }
        if has_raw_name:
            row["raw_name"] = raw_name
        by_external[key] = row  # ON CONFLICT no admite la misma fila dos veces

    stmt = insert(model).values(list(by_external.values()))
    set_ = {"last_seen_at": func.now()}
    if has_raw_name:
        set_["raw_name"] = stmt.excluded.raw_name
    stmt = stmt.on_conflict_do_update(constraint=f"uq_{model.__tablename__}_provider_external", set_=set_)
    db.execute(stmt)
