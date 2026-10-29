from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text, Boolean, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_name: Mapped[str] = mapped_column(String(1024))
    relative_path: Mapped[str] = mapped_column(String(2048), index=True)
    media_type: Mapped[str] = mapped_column(String(100), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    thumbnail_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="indexed")
    tags: Mapped[list["Tag"]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(180), index=True)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    asset: Mapped[Asset] = relationship(back_populates="tags")


class IngestJob(Base):
    __tablename__ = "ingest_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_ref: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    discovered: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EmployeeProfile(Base):
    __tablename__ = "employee_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(250))
    department: Mapped[str | None] = mapped_column(String(250), nullable=True)
    profile_image_path: Mapped[str] = mapped_column(String(2048))
    consent_recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchDocument(Base):
    __tablename__ = "search_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), unique=True, index=True)
    text_for_embedding: Mapped[str] = mapped_column(Text, default="")
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(250), nullable=True)
    __table_args__ = (UniqueConstraint("asset_id"),)
