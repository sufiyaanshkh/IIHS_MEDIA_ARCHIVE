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


SEGMENT_TYPES = (
    "pdf_page",
    "office_chunk",
    "spreadsheet_sheet",
    "presentation_slide",
    "ocr_text",
    "audio_transcript_window",
    "video_frame",
    "contextual_metadata",
    "image_visual",
    "visual_description",
)


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
    __table_args__ = (
        UniqueConstraint("source_id", "source_relative_key", name="uq_media_source_key"),
        Index("ix_media_created_id", "created_at", "id"),
        CheckConstraint("media_type IN ('image','video','audio','document')", name="ck_media_type"),
        CheckConstraint("size_bytes >= 0", name="ck_media_size"),
    )
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
    segments: Mapped[list["SearchSegment"]] = relationship(back_populates="asset", cascade="all, delete-orphan")


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
    __table_args__ = (
        CheckConstraint("source IN ('model','human','imported')", name="ck_annotation_source"),
        CheckConstraint("status IN ('suggested','accepted','rejected','corrected','deferred')", name="ck_annotation_status"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_annotation_confidence"),
    )
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
    __table_args__ = (
        CheckConstraint("kind IN ('preview','ingest')", name="ck_job_kind"),
        CheckConstraint("state IN ('queued','running','preview_ready','cancel_requested','completed','partially_completed','failed','cancelled')", name="ck_job_state"),
        UniqueConstraint("requested_by", "idempotency_key", name="uq_job_actor_request"),
    )
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
    discovered_files: Mapped[int] = mapped_column(Integer, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    processed_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, default=0)
    skipped_files: Mapped[int] = mapped_column(Integer, default=0)
    current_stage: Mapped[str] = mapped_column(String(64), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    candidates: Mapped[list["JobCandidate"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobCandidate(Base):
    __tablename__ = "job_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", "source_relative_key", name="uq_job_candidate_key"),
        CheckConstraint("state IN ('ready','duplicate','invalid','ingested','failed','skipped','moved','modified')", name="ck_candidate_state"),
    )
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
    __table_args__ = (
        Index("ix_login_attempts_ip_purpose_time", "ip_hash", "purpose", "attempted_at"),
        CheckConstraint("purpose IN ('login','register')", name="ck_login_attempts_purpose"),
    )
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
    __table_args__ = (
        Index("ix_asset_embeddings_vector", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[str | None] = mapped_column(ForeignKey("video_frames.id", ondelete="CASCADE"), nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
    modality: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(160))
    model_revision: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SearchSegment(Base):
    __tablename__ = "search_segments"
    __table_args__ = (
        Index("ix_search_segments_asset_ordinal", "asset_id", "segment_ordinal"),
        Index("ix_search_segments_type_modality", "segment_type", "modality"),
        Index("ix_search_segments_model", "model_id", "model_revision"),
        Index("ix_search_segments_vector", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}),
        CheckConstraint(
            "segment_type IN ('pdf_page','office_chunk','spreadsheet_sheet','presentation_slide','ocr_text','audio_transcript_window','video_frame','contextual_metadata','image_visual','visual_description')",
            name="ck_search_segment_type",
        ),
        CheckConstraint("segment_ordinal >= 0", name="ck_search_segment_ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[str | None] = mapped_column(ForeignKey("video_frames.id", ondelete="SET NULL"), nullable=True)
    segment_type: Mapped[str] = mapped_column(String(40))
    modality: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    segment_ordinal: Mapped[int] = mapped_column(Integer, default=0)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_file_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    asset: Mapped[MediaAsset] = relationship(back_populates="segments")


class SourceFileRecord(Base):
    __tablename__ = "source_file_records"
    __table_args__ = (
        UniqueConstraint("source_id", "source_relative_key", name="uq_source_file_record_key"),
        Index("ix_source_file_records_sha256", "source_id", "sha256"),
        CheckConstraint("status IN ('active','moved','missing_from_source','failed')", name="ck_source_file_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_definitions.id", ondelete="CASCADE"), index=True)
    source_relative_key: Mapped[str] = mapped_column(Text)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    original_filename: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    device_inode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="active")
    last_seen_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class PersonProfile(Base):
    __tablename__ = "person_profiles"
    __table_args__ = (
        CheckConstraint("status IN ('pending_confirmation','active','revoked')", name="ck_person_profile_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(200), index=True)
    normalized_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    folder_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_confirmation")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_count: Mapped[int] = mapped_column(Integer, default=0)
    active_embedding_count: Mapped[int] = mapped_column(Integer, default=0)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    reference_photos: Mapped[list["EnrolledReferencePhoto"]] = relationship(back_populates="person", cascade="all, delete-orphan")
    reference_embeddings: Mapped[list["FaceReferenceEmbedding"]] = relationship(back_populates="person", cascade="all, delete-orphan")


class EnrolledReferencePhoto(Base):
    __tablename__ = "enrolled_reference_photos"
    __table_args__ = (
        UniqueConstraint("source_relative_path", name="uq_enrolled_reference_photo_path"),
        Index("ix_enrolled_reference_photos_sha256", "sha256"),
        CheckConstraint(
            "status IN ('pending_confirmation','enrolled','needs_review','unassigned_review','failed_detection','low_quality','duplicate','rejected')",
            name="ck_enrolled_reference_photo_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("person_profiles.id", ondelete="CASCADE"), nullable=True, index=True)
    folder_person_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_relative_path: Mapped[str] = mapped_column(Text)
    source_full_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending_confirmation")
    review_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detected_face_count: Mapped[int] = mapped_column(Integer, default=0)
    det_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_json: Mapped[list | None] = mapped_column(JSON_VALUE, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    person: Mapped[PersonProfile | None] = relationship(back_populates="reference_photos")
    embeddings: Mapped[list["FaceReferenceEmbedding"]] = relationship(back_populates="reference_photo", cascade="all, delete-orphan")


class FaceReferenceEmbedding(Base):
    __tablename__ = "face_reference_embeddings"
    __table_args__ = (
        CheckConstraint("model_pack = 'buffalo_l'", name="ck_face_ref_model_pack"),
        CheckConstraint("embedding_dim > 0", name="ck_face_ref_embedding_dim"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    person_id: Mapped[str] = mapped_column(ForeignKey("person_profiles.id", ondelete="CASCADE"), index=True)
    reference_photo_id: Mapped[str] = mapped_column(ForeignKey("enrolled_reference_photos.id", ondelete="CASCADE"), index=True)
    model_pack: Mapped[str] = mapped_column(String(32), default="buffalo_l")
    recognition_model: Mapped[str] = mapped_column(String(64), default="w600k_r50.onnx")
    embedding_dim: Mapped[int] = mapped_column(Integer)
    encrypted_embedding: Mapped[str] = mapped_column(Text)
    embedding_sha256: Mapped[str] = mapped_column(String(64), index=True)
    det_score: Mapped[float] = mapped_column(Float)
    bbox_json: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    landmarks_json: Mapped[list | None] = mapped_column(JSON_VALUE, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    person: Mapped[PersonProfile] = relationship(back_populates="reference_embeddings")
    reference_photo: Mapped[EnrolledReferencePhoto] = relationship(back_populates="embeddings")


class DetectedArchiveFace(Base):
    __tablename__ = "detected_archive_faces"
    __table_args__ = (
        Index("ix_detected_archive_faces_asset_frame", "asset_id", "frame_id"),
        Index("ix_detected_archive_faces_dhash", "asset_id", "frame_dhash"),
        CheckConstraint("model_pack = 'buffalo_l'", name="ck_detected_face_model_pack"),
        CheckConstraint("embedding_dim > 0", name="ck_detected_face_embedding_dim"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[str | None] = mapped_column(ForeignKey("video_frames.id", ondelete="CASCADE"), nullable=True, index=True)
    media_type: Mapped[str] = mapped_column(String(16))
    timestamp_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_dhash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    face_index: Mapped[int] = mapped_column(Integer, default=0)
    bbox_json: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    det_score: Mapped[float] = mapped_column(Float)
    landmarks_json: Mapped[list | None] = mapped_column(JSON_VALUE, nullable=True)
    model_pack: Mapped[str] = mapped_column(String(32), default="buffalo_l")
    embedding_dim: Mapped[int] = mapped_column(Integer)
    encrypted_embedding: Mapped[str] = mapped_column(Text)
    original_file_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    candidate_matches: Mapped[list["FaceCandidateMatch"]] = relationship(back_populates="detected_face", cascade="all, delete-orphan")


class FaceCandidateMatch(Base):
    __tablename__ = "face_candidate_matches"
    __table_args__ = (
        UniqueConstraint("detected_face_id", "person_id", name="uq_face_candidate_match_face_person"),
        Index("ix_face_candidate_matches_person_status", "person_id", "status"),
        Index("ix_face_candidate_matches_asset_status", "asset_id", "status"),
        CheckConstraint("status IN ('unverified','accepted','rejected')", name="ck_face_candidate_match_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    detected_face_id: Mapped[str] = mapped_column(ForeignKey("detected_archive_faces.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("person_profiles.id", ondelete="CASCADE"), index=True)
    best_reference_photo_id: Mapped[str | None] = mapped_column(ForeignKey("enrolled_reference_photos.id", ondelete="SET NULL"), nullable=True)
    aggregate_similarity: Mapped[float] = mapped_column(Float)
    max_similarity: Mapped[float] = mapped_column(Float)
    mean_top_similarity: Mapped[float] = mapped_column(Float)
    matched_reference_count: Mapped[int] = mapped_column(Integer, default=1)
    threshold_used: Mapped[float] = mapped_column(Float)
    high_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="unverified")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    detected_face: Mapped[DetectedArchiveFace] = relationship(back_populates="candidate_matches")
    decisions: Mapped[list["FaceVerificationDecision"]] = relationship(back_populates="candidate_match", cascade="all, delete-orphan")


class FaceVerificationDecision(Base):
    __tablename__ = "face_verification_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('accepted','rejected')", name="ck_face_verification_decision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_match_id: Mapped[str] = mapped_column(ForeignKey("face_candidate_matches.id", ondelete="CASCADE"), index=True)
    detected_face_id: Mapped[str] = mapped_column(ForeignKey("detected_archive_faces.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("person_profiles.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    candidate_match: Mapped[FaceCandidateMatch] = relationship(back_populates="decisions")

