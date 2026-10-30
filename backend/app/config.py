import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    demo_mode: bool = _bool("DEMO_MODE", True)
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg://iihs_demo:local-demo-only@127.0.0.1:5432/iihs_demo")
    storage_root: Path = Path(os.getenv("MEDIA_STORAGE_ROOT", "/app/storage"))
    demo_media_root: Path = Path(os.getenv("DEMO_MEDIA_ROOT", "/app/demo_media"))
    approved_source_roots: tuple[Path, ...] = tuple(Path(p).expanduser() for p in os.getenv("APPROVED_SOURCE_ROOTS", "").split(os.pathsep) if p.strip())
    cookie_secure: bool = _bool("COOKIE_SECURE", False)
    session_hours: int = int(os.getenv("SESSION_HOURS", "8"))
    initial_admin_token: str = os.getenv("INITIAL_ADMIN_TOKEN", "")
    allow_self_registration: bool = _bool("ALLOW_SELF_REGISTRATION", True)
    ffprobe_path: str = os.getenv("FFPROBE_PATH", "ffprobe")
    ffmpeg_path: str = os.getenv("FFMPEG_PATH", "ffmpeg")
    local_ml_url: str = os.getenv("LOCAL_ML_URL", "http://host.docker.internal:8765").rstrip("/")
    local_ml_token: str = os.getenv("LOCAL_ML_TOKEN", "")
    embedding_model: str = "google/embeddinggemma-2"
    embedding_revision: str = "bfcd298762cc34d0357ece5ebdd31791a3a374d8"
    digup_executable: str = os.getenv("DIGUP_EXECUTABLE", "/Applications/DigUp.app/Contents/Helpers/digup")
    video_frame_interval_seconds: int = int(os.getenv("VIDEO_FRAME_INTERVAL_SECONDS", "5"))
    video_frame_max: int = int(os.getenv("VIDEO_FRAME_MAX", "120"))
    max_transcription_seconds: int = int(os.getenv("MAX_TRANSCRIPTION_SECONDS", "10800"))
    min_storage_free_bytes: int = int(os.getenv("MIN_STORAGE_FREE_BYTES", str(1024 * 1024 * 1024)))
    face_feature_enabled: bool = _bool("FACE_FEATURE_ENABLED", False)
    # 0 means "no limit" for the three import limits below.
    max_files_per_job: int = int(os.getenv("MAX_FILES_PER_JOB", "0"))
    max_file_bytes: int = int(os.getenv("MAX_FILE_BYTES", "0"))
    max_import_bytes: int = int(os.getenv("MAX_IMPORT_BYTES", "0"))
    cors_origins: tuple[str, ...] = tuple(origin.strip().rstrip("/") for origin in os.getenv("CORS_ORIGINS", "http://127.0.0.1:5173").split(",") if origin.strip())

    def validate(self) -> None:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required.")
        if self.session_hours < 1 or self.session_hours > 24 * 30:
            raise RuntimeError("SESSION_HOURS must be between 1 and 720.")
        if self.initial_admin_token and len(self.initial_admin_token) < 32:
            raise RuntimeError("INITIAL_ADMIN_TOKEN must contain at least 32 characters.")
        if self.max_files_per_job < 0 or self.max_file_bytes < 0 or self.max_import_bytes < 0:
            raise RuntimeError("MAX_FILES_PER_JOB, MAX_FILE_BYTES and MAX_IMPORT_BYTES must be 0 (no limit) or positive.")
        if self.video_frame_interval_seconds < 1 or self.video_frame_max < 1 or self.video_frame_max > 1000:
            raise RuntimeError("Video frame sampling limits are invalid.")
        if self.max_transcription_seconds < 60 or self.max_transcription_seconds > 12 * 60 * 60:
            raise RuntimeError("MAX_TRANSCRIPTION_SECONDS must be between 60 and 43200.")
        if self.min_storage_free_bytes < 0:
            raise RuntimeError("MIN_STORAGE_FREE_BYTES cannot be negative.")
        if self.face_feature_enabled:
            raise RuntimeError("Face enrollment is disabled until its governance and licensing gates are approved.")
        if not self.demo_mode and not self.cookie_secure:
            raise RuntimeError("COOKIE_SECURE=true is required outside demo mode.")
        if not self.cors_origins:
            raise RuntimeError("At least one explicit CORS origin is required.")
        for root in self.approved_source_roots:
            if not root.is_absolute():
                raise RuntimeError("APPROVED_SOURCE_ROOTS entries must be absolute paths.")


settings = Settings()
