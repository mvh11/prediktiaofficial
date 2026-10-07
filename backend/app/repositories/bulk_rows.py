"""Transporte de escrituras multi-fila por arrays tipados (UNNEST). Adoptado de DI-A3F en DI-A6.

En vez de `INSERT ... VALUES (...), (...), ...` (24 parámetros por fila, una sentencia distinta para
cada tamaño de lote y una compilación cara en SQLAlchemy), cada columna viaja como UN array tipado y
la sentencia es `INSERT ... SELECT ... FROM unnest(:a1, :a2, ...)`: misma forma para cualquier lote,
un parámetro por columna. Solo cambia el transporte: mismas filas, mismas columnas, mismas
expresiones (p. ej. now()), mismos ON CONFLICT y RETURNING, y los defaults siguen siendo los de la BD.

- Los arrays se validan antes de construir el SQL: unnest() rellena con NULL los más cortos, y eso
  nunca puede ocurrir en silencio.
- Las cadenas viajan como text[]: un cast explícito a varchar(n)[] TRUNCA en PostgreSQL; con text[]
  es la asignación a la columna destino la que rechaza un valor demasiado largo, igual que VALUES.
- `order_by` fija el orden en que el INSERT procesa (y bloquea) las filas.
"""

from collections.abc import Sequence

from sqlalchemy import String, Text, bindparam, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.types import TypeEngine


def unnest_rows(columns: Sequence[tuple[str, TypeEngine]], arrays: Sequence[list], *, name: str = "rows"):
    """Tabla derivada `unnest(...) AS name(col1, col2, ...)` sobre arrays alineados y no vacíos."""
    if not columns or len(columns) != len(arrays):
        raise ValueError("columnas y arrays no coinciden")
    if len({len(a) for a in arrays}) != 1 or not arrays[0]:
        raise ValueError("los arrays tienen que tener la misma longitud, mayor que cero")
    bindings = []
    for (column, type_), values in zip(columns, arrays):
        array_type = ARRAY(Text() if isinstance(type_, String) else type_)
        bindings.append(cast(bindparam(f"{name}_{column}", list(values), type_=array_type), array_type))
    return func.unnest(*bindings).table_valued(*(column for column, _ in columns)).render_derived(name=name)


def insert_rows(model, rows: Sequence[dict], *, order_by: Sequence[str] = ()):
    """`INSERT INTO tabla (cols) SELECT ... FROM unnest(...)`, equivalente a `insert(model).values(rows)`.

    Un valor que sea una expresión SQL (p. ej. func.now()) tiene que ser la misma en todas las filas y
    se proyecta tal cual; el resto viaja en arrays. Devuelve la sentencia para añadirle ON CONFLICT y
    RETURNING como a la de VALUES.
    """
    if not rows:
        raise ValueError("insert_rows necesita filas; el llamador gestiona la entrada vacía")
    table = getattr(model, "__table__", model)
    names = list(rows[0])
    if any(set(row) != set(names) for row in rows):
        raise ValueError("las filas no tienen las mismas columnas")
    expressions = {n: rows[0][n] for n in names if isinstance(rows[0][n], ClauseElement)}
    for n in names:
        if any(isinstance(row[n], ClauseElement) != (n in expressions) for row in rows):
            raise ValueError(f"columna {n}: mezcla expresiones SQL y valores")
        if n in expressions and any(not row[n].compare(expressions[n]) for row in rows):
            raise ValueError(f"columna {n}: las expresiones SQL tienen que ser iguales en todas las filas")
    data = [n for n in names if n not in expressions]
    source = unnest_rows([(n, table.c[n].type) for n in data], [[row[n] for row in rows] for n in data])
    projection = select(*(expressions[n] if n in expressions else source.c[n] for n in names))
    if order_by:
        projection = projection.order_by(*(source.c[n] for n in order_by))
    # Las columnas omitidas (identidad, created_at, recorded_at...) conservan su default de la BD
    return insert(table).from_select(names, projection, include_defaults=False)
