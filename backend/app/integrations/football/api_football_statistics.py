"""Variante del adapter de API-Football para las estadísticas de partido (M5).

Hereda el adapter tal cual (autenticación, reintentos, errores) y añade
get_fixture_statistics: una sola petición GET /fixtures?ids=A-B-... para hasta 20 partidos.
Esa respuesta incluye además events, lineups y players; aquí solo se lee el array
`statistics` de cada partido y se traduce al contrato interno (app.schemas.statistics).

Es el ÚNICO sitio que conoce los nombres de estadísticas de API-Football. Formatos observados
(M5.0): enteros como int; "Ball Possession" y "Passes %" como texto "47%"; "expected_goals" y
"goals_prevented" como texto "2.08"; cualquier valor puede venir null, y "Red Cards" suele venir
null cuando no hubo rojas: se respeta (None), nunca se convierte en 0.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.integrations.exceptions import ProviderResponseError
from app.integrations.football.api_football import ApiFootballProvider
from app.schemas.statistics import (
    DECIMAL_FIELDS,
    INTEGER_FIELDS,
    PERCENTAGE_FIELDS,
    FixtureStatisticsData,
    TeamStatisticsData,
    TeamStatisticValues,
)

MAX_IDS_PER_REQUEST = 20  # límite de la API para /fixtures?ids

# Tipo de API-Football → campo interno v1
STAT_TYPE_TO_FIELD = {
    "Shots on Goal": "shots_on_goal",
    "Shots off Goal": "shots_off_goal",
    "Total Shots": "shots_total",
    "Blocked Shots": "shots_blocked",
    "Shots insidebox": "shots_inside_box",
    "Shots outsidebox": "shots_outside_box",
    "Fouls": "fouls",
    "Corner Kicks": "corners",
    "Offsides": "offsides",
    "Ball Possession": "possession_pct",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Goalkeeper Saves": "goalkeeper_saves",
    "Total passes": "passes_total",
    "Passes accurate": "passes_accurate",
    "Passes %": "passes_pct",
    "expected_goals": "expected_goals",
}
# Tipos conocidos que v1 no normaliza: se conservan con su valor original
RAW_ONLY_TYPES = frozenset({"goals_prevented", "Free Kicks"})

_INTEGER = re.compile(r"^-?\d+$")
_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")
_PERCENTAGE = re.compile(r"^-?\d+(\.\d+)?%?$")


class Unparseable(Exception):
    """El valor no tiene el formato esperado para su campo."""


def parse_integer(value: Any) -> int | None:
    """int o texto de dígitos. None se respeta. bool, float y otros textos no son enteros."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise Unparseable
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER.match(value.strip()):
        return int(value.strip())
    raise Unparseable


def parse_decimal(value: Any) -> Decimal | None:
    """Número decimal ("2.08", 2, 2.08). None se respeta. El signo se conserva (lo juzga el check)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise Unparseable
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise Unparseable
        return Decimal(str(value))
    if isinstance(value, str) and _DECIMAL.match(value.strip()):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:  # pragma: no cover - el regex ya lo garantiza
            raise Unparseable from exc
    raise Unparseable


def parse_percentage(value: Any) -> Decimal | None:
    """"47%", "47", 47 o "47.5%" → Decimal. "0%" → 0. None se respeta."""
    if isinstance(value, str):
        text = value.strip()
        if not _PERCENTAGE.match(text):
            raise Unparseable
        return parse_decimal(text.removesuffix("%"))
    return parse_decimal(value)


def _parse_field(field: str, value: Any) -> int | Decimal | None:
    if field in INTEGER_FIELDS:
        return parse_integer(value)
    if field in PERCENTAGE_FIELDS:
        return parse_percentage(value)
    if field in DECIMAL_FIELDS:
        return parse_decimal(value)
    raise AssertionError(f"campo sin parser: {field}")  # pragma: no cover


def _in_range(field: str, value: int | Decimal) -> bool:
    if value < 0:
        return False
    return not (field in PERCENTAGE_FIELDS and value > 100)


def _team_id(team: Any) -> int | None:
    team_id = team.get("id") if isinstance(team, dict) else None
    return team_id if isinstance(team_id, int) and not isinstance(team_id, bool) else None


def parse_team_statistics(entry: Any) -> TeamStatisticsData:
    """Traduce una entrada {team, statistics: [{type, value}]} al contrato interno."""
    entry = entry if isinstance(entry, dict) else {}
    stats = entry.get("statistics")
    stats = stats if isinstance(stats, list) else []

    values: dict[str, int | Decimal | None] = {}
    present: set[str] = set()
    seen_raw: dict[str, Any] = {}
    unparseable: dict[str, Any] = {}
    out_of_range: dict[str, int | Decimal] = {}
    raw_only: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    duplicated: dict[str, list[Any]] = {}
    malformed = 0

    for item in stats:
        stat_type = item.get("type") if isinstance(item, dict) else None
        if not isinstance(stat_type, str):
            malformed += 1
            continue
        raw_value = item.get("value")
        field = STAT_TYPE_TO_FIELD.get(stat_type)
        if field is None:
            target = raw_only if stat_type in RAW_ONLY_TYPES else unknown
            target.setdefault(stat_type, raw_value)
            continue

        if field in present:
            # Mismo tipo repetido: si el valor coincide no pasa nada; si no, no se elige ninguno
            if raw_value != seen_raw[field] or field in duplicated:
                duplicated.setdefault(field, [seen_raw[field]]).append(raw_value)
                values[field] = None
                unparseable.pop(field, None)
                out_of_range.pop(field, None)
            continue
        present.add(field)
        seen_raw[field] = raw_value

        try:
            parsed = _parse_field(field, raw_value)
        except Unparseable:
            unparseable[field] = raw_value
            values[field] = None
            continue
        if parsed is not None and not _in_range(field, parsed):
            out_of_range[field] = parsed
            values[field] = None
            continue
        values[field] = parsed

    return TeamStatisticsData(
        provider_team_id=_team_id(entry.get("team")),
        values=TeamStatisticValues(**values),
        present=frozenset(present),
        unparseable=unparseable,
        out_of_range=out_of_range,
        raw_only=raw_only,
        unknown=unknown,
        duplicated=duplicated,
        malformed_entries=malformed,
    )


def parse_fixture_statistics(item: dict[str, Any], provider: str = "api-football") -> FixtureStatisticsData:
    """Traduce un partido de /fixtures (o de /fixtures?ids) a FixtureStatisticsData.

    Ignora events, lineups y players. Sin fixture.id entero la respuesta no se puede atribuir
    a ningún partido: se lanza ProviderResponseError en lugar de adivinar.
    """
    fixture = item.get("fixture") if isinstance(item, dict) else None
    fixture_id = fixture.get("id") if isinstance(fixture, dict) else None
    if not isinstance(fixture_id, int) or isinstance(fixture_id, bool):
        raise ProviderResponseError(provider, "Partido sin fixture.id en la respuesta de estadísticas")
    status = fixture.get("status") if isinstance(fixture.get("status"), dict) else {}
    status_short = status.get("short")
    raw = item.get("statistics")
    raw_statistics = raw if isinstance(raw, list) else None
    return FixtureStatisticsData(
        provider=provider,
        provider_fixture_id=fixture_id,
        provider_status=status_short.strip().upper() if isinstance(status_short, str) else None,
        teams=[parse_team_statistics(entry) for entry in raw_statistics or []],
        raw_statistics=raw_statistics,
    )


def normalize_fixture_ids(provider_fixture_ids: list[int]) -> list[int]:
    """IDs sin duplicados y ordenados (petición determinista). Entre 1 y 20; si no, ValueError
    antes de hacer ninguna petición."""
    ids: set[int] = set()
    for value in provider_fixture_ids:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"ID de partido no válido: {value!r}")
        ids.add(value)
    if not ids:
        raise ValueError("Hace falta al menos un ID de partido")
    if len(ids) > MAX_IDS_PER_REQUEST:
        raise ValueError(f"Como mucho {MAX_IDS_PER_REQUEST} IDs distintos por petición (recibidos {len(ids)})")
    return sorted(ids)


class ApiFootballStatisticsProvider(ApiFootballProvider):
    async def get_fixture_statistics(self, provider_fixture_ids: list[int]) -> list[FixtureStatisticsData]:
        """Estadísticas de hasta 20 partidos con UNA petición: GET /fixtures?ids=A-B-C.

        Devuelve los partidos en el orden de la respuesta. Un ID pedido que no venga en la
        respuesta simplemente no aparece (lo detecta el service comparando con lo pedido).
        """
        ids = normalize_fixture_ids(provider_fixture_ids)
        data = await self._get("/fixtures", params={"ids": "-".join(str(i) for i in ids)})
        items = data.get("response")
        if not isinstance(items, list):
            raise ProviderResponseError(self.name, "Formato inesperado en /fixtures?ids: falta la lista 'response'")
        return [parse_fixture_statistics(item, self.name) for item in items]
