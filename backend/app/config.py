import os
from dataclasses import dataclass
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    for _candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent.parent / ".env"):
        if _candidate.is_file():
            load_dotenv(_candidate)
            break
except ImportError:
    pass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    demo_mode: bool = _bool("DEMO_MODE", True)
    database_url: str = os.getenv("DATABASE_URL", f"postgresql+psycopg://iihs_demo:{os.getenv('POSTGRES_PASSWORD', 'local-demo-only-change-before-use')}@127.0.0.1:5432/iihs_demo")
    storage_root: Path = Path(os.getenv("MEDIA_STORAGE_ROOT", "/app/storage"))
    demo_media_root: Path = Path(os.getenv("DEMO_MEDIA_ROOT", "/app/demo_media"))
    approved_source_roots: tuple[Path, ...] = tuple(Path(p).expanduser() for p in os.getenv("APPROVED_SOURCE_ROOTS", "").split(os.pathsep) if p.strip())
    copy_source_originals: bool = _bool("COPY_SOURCE_ORIGINALS", True)
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
    embedding_query_prompt: str = os.getenv("EMBEDDING_QUERY_PROMPT", "task: search result | query: {query}")
    embedding_document_prompt: str = os.getenv("EMBEDDING_DOCUMENT_PROMPT", "title: {title} | text: {text}")
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    worker_max_retries: int = int(os.getenv("WORKER_MAX_RETRIES", "3"))
    document_chunk_chars: int = int(os.getenv("DOCUMENT_CHUNK_CHARS", "1200"))
    document_chunk_overlap: int = int(os.getenv("DOCUMENT_CHUNK_OVERLAP", "180"))
    audio_window_seconds: int = int(os.getenv("AUDIO_WINDOW_SECONDS", "30"))
    audio_window_hop_seconds: int = int(os.getenv("AUDIO_WINDOW_HOP_SECONDS", "25"))
    video_frame_interval_seconds: int = int(os.getenv("VIDEO_FRAME_INTERVAL_SECONDS", "5"))
    video_frame_max: int = int(os.getenv("VIDEO_FRAME_MAX", "120"))
    video_frame_dedup_threshold: int = int(os.getenv("VIDEO_FRAME_DEDUP_THRESHOLD", "4"))
    max_transcription_seconds: int = int(os.getenv("MAX_TRANSCRIPTION_SECONDS", "10800"))
    min_storage_free_bytes: int = int(os.getenv("MIN_STORAGE_FREE_BYTES", str(1024 * 1024 * 1024)))
    # Biometric / InsightFace (buffalo_l) governance settings — disabled by default until IIHS authorisation is confirmed
    face_feature_enabled: bool = _bool("FACE_FEATURE_ENABLED", False)
    face_institutional_authorization: bool = _bool("FACE_INSTITUTIONAL_AUTHORIZATION", False)
    face_model_pack: str = os.getenv("FACE_MODEL_PACK", "buffalo_l")
    insightface_root: Path = Path(os.getenv("INSIGHTFACE_ROOT", "data/models/insightface"))
    face_profile_photos_root: Path = Path(os.getenv("FACE_PROFILE_PHOTOS_ROOT", "data/profile_photos/IIHS_Profile_Photos"))
    face_det_size: int = int(os.getenv("FACE_DET_SIZE", "640"))
    face_min_det_score: float = float(os.getenv("FACE_MIN_DET_SCORE", "0.50"))
    face_match_threshold: float = float(os.getenv("FACE_MATCH_THRESHOLD", "0.45"))
    face_high_confidence_threshold: float = float(os.getenv("FACE_HIGH_CONFIDENCE_THRESHOLD", "0.60"))
    face_encryption_key: str = os.getenv("FACE_ENCRYPTION_KEY", "")
    face_retention_days: int = int(os.getenv("FACE_RETENTION_DAYS", "365"))
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
        if self.face_model_pack != "buffalo_l":
            raise RuntimeError("Only the InsightFace 'buffalo_l' model pack is permitted.")
        if self.face_feature_enabled and not self.face_institutional_authorization:
            raise RuntimeError("Face recognition is disabled until IIHS institutional authorization (FACE_INSTITUTIONAL_AUTHORIZATION=true) is confirmed.")
        is_local_loopback = bool(self.cors_origins) and all(
            origin.startswith(("http://127.0.0.1", "https://127.0.0.1", "http://localhost", "https://localhost"))
            for origin in self.cors_origins
        )
        if not self.demo_mode and not self.cookie_secure and not is_local_loopback:
            raise RuntimeError("COOKIE_SECURE=true is required outside demo mode.")
        if not self.cors_origins:
            raise RuntimeError("At least one explicit CORS origin is required.")
        for root in self.approved_source_roots:
            if not root.is_absolute() and not str(root).startswith(("/", "\\")):
                raise RuntimeError("APPROVED_SOURCE_ROOTS entries must be absolute paths.")


settings = Settings()
