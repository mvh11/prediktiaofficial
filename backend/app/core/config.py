"""Configuración de la aplicación leída desde variables de entorno / archivo .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Carpeta backend/ (este archivo está en backend/app/core/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    app_name: str = "Prediktia API"
    log_level: str = "INFO"

    # Base de datos (obligatoria)
    database_url: str

    # Proveedores externos (opcionales: la app arranca aunque estén vacías)
    api_football_key: SecretStr = SecretStr("")
    api_football_base_url: str = "https://v3.football.api-sports.io"

    five_dollar_football_api_key: SecretStr = SecretStr("")
    five_dollar_football_base_url: str = "https://api.5dollarfootballapi.com/v1"

    # Tiempo máximo de espera para llamadas HTTP externas (segundos)
    http_timeout_seconds: float = 10.0

    # Competiciones que Prediktia sigue (IDs de API-Football).
    # Se puede sobrescribir en el .env con una lista JSON: TRACKED_LEAGUE_IDS=[39,140]
    tracked_league_ids: list[int] = [
        # Sudamérica: primeras divisiones
        265,  # Chile - Primera División
        128,  # Argentina - Liga Profesional
        71,  # Brasil - Serie A
        239,  # Colombia - Primera A
        281,  # Perú - Liga 1
        242,  # Ecuador - Liga Pro
        299,  # Venezuela - Primera División
        344,  # Bolivia - Primera División
        # CONMEBOL
        13,  # Copa Libertadores
        11,  # Copa Sudamericana
        9,  # Copa América
        # FIFA
        21,  # Copa Confederaciones
        # UEFA
        2,  # Champions League
        3,  # Europa League
        848,  # Conference League
        5,  # Nations League
        4,  # Eurocopa
        # Europa: ligas
        140,  # España - La Liga
        39,  # Inglaterra - Premier League
        78,  # Alemania - Bundesliga
        135,  # Italia - Serie A
        61,  # Francia - Ligue 1
        88,  # Países Bajos - Eredivisie
        # Resto
        253,  # EE. UU. - MLS
        262,  # México - Liga MX
        307,  # Arabia Saudí - Pro League
    ]


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (se lee una sola vez y se reutiliza)."""
    return Settings()  # type: ignore[call-arg]
