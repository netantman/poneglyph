"""Application configuration loaded from environment / .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All settings with sensible defaults; override via .env or env vars."""

    # Anthropic (needed in later phases)
    anthropic_api_key: str = ""

    # Database
    database_path: str = str(ROOT_DIR / "data" / "poneglyph.db")

    # PDF storage
    pdf_dir: str = str(ROOT_DIR / "data" / "pdfs")

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    model_config = {"env_file": str(ROOT_DIR / ".env"), "env_file_encoding": "utf-8"}


settings = Settings()
