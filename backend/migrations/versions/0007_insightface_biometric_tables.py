"""Create InsightFace buffalo_l biometric enrollment, detection, matching and verification tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30 17:10:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "person_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("folder_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_confirmation"),
        sa.Column("consent_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("consent_reference", sa.String(length=255), nullable=True),
        sa.Column("photo_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_embedding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSON_VALUE, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending_confirmation','active','revoked')", name="ck_person_profile_status"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name"),
    )
    op.create_index("ix_person_profiles_display_name", "person_profiles", ["display_name"])
    op.create_index("ix_person_profiles_normalized_name", "person_profiles", ["normalized_name"], unique=True)

    op.create_table(
        "enrolled_reference_photos",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=True),
        sa.Column("folder_person_name", sa.String(length=200), nullable=True),
        sa.Column("source_relative_path", sa.Text(), nullable=False),
        sa.Column("source_full_path", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_confirmation"),
        sa.Column("review_reason", sa.String(length=120), nullable=True),
        sa.Column("detected_face_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("det_score", sa.Float(), nullable=True),
        sa.Column("bbox_json", JSON_VALUE, nullable=True),
        sa.Column("reviewed_by", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSON_VALUE, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending_confirmation','enrolled','needs_review','unassigned_review','failed_detection','low_quality','duplicate','rejected')",
            name="ck_enrolled_reference_photo_status",
        ),
        sa.ForeignKeyConstraint(["person_id"], ["person_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_relative_path", name="uq_enrolled_reference_photo_path"),
    )
    op.create_index("ix_enrolled_reference_photos_person_id", "enrolled_reference_photos", ["person_id"])
    op.create_index("ix_enrolled_reference_photos_sha256", "enrolled_reference_photos", ["sha256"])

    op.create_table(
        "face_reference_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("reference_photo_id", sa.String(length=36), nullable=False),
        sa.Column("model_pack", sa.String(length=32), nullable=False, server_default="buffalo_l"),
        sa.Column("recognition_model", sa.String(length=64), nullable=False, server_default="w600k_r50.onnx"),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("encrypted_embedding", sa.Text(), nullable=False),
        sa.Column("embedding_sha256", sa.String(length=64), nullable=False),
        sa.Column("det_score", sa.Float(), nullable=False),
        sa.Column("bbox_json", JSON_VALUE, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("landmarks_json", JSON_VALUE, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("model_pack = 'buffalo_l'", name="ck_face_ref_model_pack"),
        sa.CheckConstraint("embedding_dim > 0", name="ck_face_ref_embedding_dim"),
        sa.ForeignKeyConstraint(["person_id"], ["person_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reference_photo_id"], ["enrolled_reference_photos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_face_reference_embeddings_person_id", "face_reference_embeddings", ["person_id"])
    op.create_index("ix_face_reference_embeddings_reference_photo_id", "face_reference_embeddings", ["reference_photo_id"])
    op.create_index("ix_face_reference_embeddings_embedding_sha256", "face_reference_embeddings", ["embedding_sha256"])

    op.create_table(
        "detected_archive_faces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("frame_id", sa.String(length=36), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("timestamp_ms", sa.Integer(), nullable=True),
        sa.Column("frame_dhash", sa.String(length=32), nullable=True),
        sa.Column("face_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bbox_json", JSON_VALUE, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("det_score", sa.Float(), nullable=False),
        sa.Column("landmarks_json", JSON_VALUE, nullable=True),
        sa.Column("model_pack", sa.String(length=32), nullable=False, server_default="buffalo_l"),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("encrypted_embedding", sa.Text(), nullable=False),
        sa.Column("original_file_reference", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("model_pack = 'buffalo_l'", name="ck_detected_face_model_pack"),
        sa.CheckConstraint("embedding_dim > 0", name="ck_detected_face_embedding_dim"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["frame_id"], ["video_frames.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_detected_archive_faces_asset_id", "detected_archive_faces", ["asset_id"])
    op.create_index("ix_detected_archive_faces_frame_id", "detected_archive_faces", ["frame_id"])
    op.create_index("ix_detected_archive_faces_asset_frame", "detected_archive_faces", ["asset_id", "frame_id"])
    op.create_index("ix_detected_archive_faces_dhash", "detected_archive_faces", ["asset_id", "frame_dhash"])

    op.create_table(
        "face_candidate_matches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("detected_face_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("best_reference_photo_id", sa.String(length=36), nullable=True),
        sa.Column("aggregate_similarity", sa.Float(), nullable=False),
        sa.Column("max_similarity", sa.Float(), nullable=False),
        sa.Column("mean_top_similarity", sa.Float(), nullable=False),
        sa.Column("matched_reference_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("threshold_used", sa.Float(), nullable=False),
        sa.Column("high_confidence", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('unverified','accepted','rejected')", name="ck_face_candidate_match_status"),
        sa.ForeignKeyConstraint(["detected_face_id"], ["detected_archive_faces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["person_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["best_reference_photo_id"], ["enrolled_reference_photos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("detected_face_id", "person_id", name="uq_face_candidate_match_face_person"),
    )
    op.create_index("ix_face_candidate_matches_detected_face_id", "face_candidate_matches", ["detected_face_id"])
    op.create_index("ix_face_candidate_matches_asset_id", "face_candidate_matches", ["asset_id"])
    op.create_index("ix_face_candidate_matches_person_id", "face_candidate_matches", ["person_id"])
    op.create_index("ix_face_candidate_matches_person_status", "face_candidate_matches", ["person_id", "status"])
    op.create_index("ix_face_candidate_matches_asset_status", "face_candidate_matches", ["asset_id", "status"])

    op.create_table(
        "face_verification_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_match_id", sa.String(length=36), nullable=False),
        sa.Column("detected_face_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("decision IN ('accepted','rejected')", name="ck_face_verification_decision"),
        sa.ForeignKeyConstraint(["candidate_match_id"], ["face_candidate_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["detected_face_id"], ["detected_archive_faces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["person_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_face_verification_decisions_candidate_match_id", "face_verification_decisions", ["candidate_match_id"])
    op.create_index("ix_face_verification_decisions_detected_face_id", "face_verification_decisions", ["detected_face_id"])
    op.create_index("ix_face_verification_decisions_person_id", "face_verification_decisions", ["person_id"])


def downgrade() -> None:
    op.drop_table("face_verification_decisions")
    op.drop_table("face_candidate_matches")
    op.drop_table("detected_archive_faces")
    op.drop_table("face_reference_embeddings")
    op.drop_table("enrolled_reference_photos")
    op.drop_table("person_profiles")

