import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Enum as SqlEnum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def now_utc():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    admin = "admin"
    archivist = "archivist"
    researcher = "researcher"
    operator = "operator"


class JobState(str, enum.Enum):
    queued = "queued"
    running = "running"
    preview_ready = "preview_ready"
    cancel_requested = "cancel_requested"
    completed = "completed"
    partially_completed = "partially_completed"
    failed = "failed"
    cancelled = "cancelled"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','archivist','researcher','operator')", name="ck_users_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole, native_enum=False), default=UserRole.researcher)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    user: Mapped[User] = relationship(back_populates="sessions")


class SourceDefinition(Base):
    __tablename__ = "source_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    root_path: Mapped[str] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("source_id", "source_relative_key", name="uq_media_source_key"), Index("ix_media_created_id", "created_at", "id"), CheckConstraint("media_type IN ('image','video')", name="ck_media_type"), CheckConstraint("size_bytes >= 0", name="ck_media_size"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source_definitions.id", ondelete="SET NULL"), nullable=True)
    source_relative_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(12))
    detected_mime: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    storage_key: Mapped[str] = mapped_column(String(240), unique=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(240), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    annotations: Mapped[list["Annotation"]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    canonical_name: Mapped[str] = mapped_column(String(160), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (CheckConstraint("source IN ('model','human','imported')", name="ck_annotation_source"), CheckConstraint("status IN ('suggested','accepted','rejected','corrected','deferred')", name="ck_annotation_status"), CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_annotation_confidence"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[str | None] = mapped_column(ForeignKey("tags.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(16), default="human")
    status: Mapped[str] = mapped_column(String(16), default="suggested")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    asset: Mapped[MediaAsset] = relationship(back_populates="annotations")
    reviews: Mapped[list["ReviewEvent"]] = relationship(back_populates="annotation", cascade="all, delete-orphan")


class ReviewEvent(Base):
    __tablename__ = "review_events"
    __table_args__ = (CheckConstraint("decision IN ('accept','reject','correct','defer')", name="ck_review_decision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    annotation_id: Mapped[str] = mapped_column(ForeignKey("annotations.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(16))
    previous_label: Mapped[str] = mapped_column(String(160))
    final_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    annotation: Mapped[Annotation] = relationship(back_populates="reviews")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (CheckConstraint("kind IN ('preview','ingest')", name="ck_job_kind"), CheckConstraint("state IN ('queued','running','preview_ready','cancel_requested','completed','partially_completed','failed','cancelled')", name="ck_job_state"), UniqueConstraint("requested_by", "idempotency_key", name="uq_job_actor_request"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(24), default=JobState.queued.value, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_definitions.id", ondelete="RESTRICT"))
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_files: Mapped[int] = mapped_column(Integer)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    candidates: Mapped[list["JobCandidate"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobCandidate(Base):
    __tablename__ = "job_candidates"
    __table_args__ = (UniqueConstraint("job_id", "source_relative_key", name="uq_job_candidate_key"), CheckConstraint("state IN ('ready','duplicate','invalid','ingested','failed')", name="ck_candidate_state"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), index=True)
    source_relative_key: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(12))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="ready")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    job: Mapped[IngestionJob] = relationship(back_populates="candidates")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="success")
    detail: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_ip_purpose_time", "ip_hash", "purpose", "attempted_at"), CheckConstraint("purpose IN ('login','register')", name="ck_login_attempts_purpose"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ip_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(12), default="login")
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class VideoFrame(Base):
    __tablename__ = "video_frames"
    __table_args__ = (CheckConstraint("timestamp_ms >= 0", name="ck_video_frame_timestamp"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(240), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AssetEmbedding(Base):
    __tablename__ = "asset_embeddings"
    __table_args__ = (Index("ix_asset_embeddings_vector", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[str | None] = mapped_column(ForeignKey("video_frames.id", ondelete="CASCADE"), nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
    modality: Mapped[str] = mapped_column(String(24))
    model_id: Mapped[str] = mapped_column(String(160))
    model_revision: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
