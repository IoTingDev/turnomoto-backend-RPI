"""Configuración central del backend. Override vía .env o variables de entorno."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": permite variables en .env que no pertenecen a esta clase
    # (ej. PIN_MECANICO, PIN_GERENCIA, leídas directamente por app/auth.py vía os.getenv)
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Base de datos ---
    database_url: str = "sqlite:///./data/turnomoto.db"

    # --- Lector NFC ---
    nfc_poll_interval_seconds: float = 0.3
    nfc_debounce_seconds: float = 1.5

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # En desarrollo deja "*". En producción restringe al origen del frontend.
    cors_origins: list[str] = ["*"]

    # --- Identidad del concesionario ---
    concesionario_nombre: str = "Suzuki - Concesionario Cali"


settings = Settings()

# Asegurar que el directorio de persistencia exista antes de que SQLAlchemy intente escribir
Path("./data").mkdir(parents=True, exist_ok=True)
