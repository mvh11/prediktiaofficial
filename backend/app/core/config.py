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


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (se lee una sola vez y se reutiliza)."""
    return Settings()  # type: ignore[call-arg]
