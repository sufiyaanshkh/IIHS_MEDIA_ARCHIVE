from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "IIHS Media Archive API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./archive.db"
    storage_root: Path = Path("../data/storage")
    derivatives_root: Path = Path("../data/derivatives")
    people_root: Path = Path("../data/people")
    ingest_roots_json: str = "[]"
    max_upload_mb: int = 512
    max_workers: int = 2
    video_frame_interval_seconds: int = 30
    max_video_frames: int = 500
    embedding_provider: str = "disabled"
    embedding_model_path: Path = Path("../models/embeddinggemma")


settings = Settings()
