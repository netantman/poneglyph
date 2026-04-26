"""Application configuration loaded from environment / .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All settings with sensible defaults; override via .env or env vars."""

    # Anthropic
    anthropic_api_key: str = ""

    # Semantic Scholar (optional — higher rate limits with a key)
    semantic_scholar_api_key: str = ""

    # Database
    database_path: str = str(ROOT_DIR / "data" / "poneglyph.db")

    # PDF storage — base folder containing subfolders (e.g. Public-Academia, GS, etc.)
    pdf_base_dir: str = r"C:\Users\zhong\OneDrive\Papers, Presentation, Reports and Slides"
    pdf_scouting_subfolder: str = "poneglygh_processing"

    # Legacy (kept for backward compat)
    pdf_dir: str = str(ROOT_DIR / "data" / "pdfs")

    # Model identifiers — override in .env when Anthropic releases new versions
    haiku_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-4-6"
    opus_model: str = "claude-opus-4-7"

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    model_config = {"env_file": str(ROOT_DIR / ".env"), "env_file_encoding": "utf-8", "env_ignore_empty": True}


settings = Settings()
