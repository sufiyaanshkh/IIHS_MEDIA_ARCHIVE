"""Add local CLIP embeddings for semantic media search.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE TABLE asset_embeddings (id varchar(36) PRIMARY KEY, asset_id varchar(36) NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE, frame_id varchar(36) REFERENCES video_frames(id) ON DELETE CASCADE, embedding vector(768) NOT NULL, modality varchar(24) NOT NULL CHECK(modality IN ('image','image_frame','transcript','translation')), model_id varchar(160) NOT NULL, model_revision varchar(64) NOT NULL, created_at timestamptz NOT NULL)")
    op.execute("CREATE INDEX ix_asset_embeddings_asset_id ON asset_embeddings (asset_id)")
    op.execute("CREATE INDEX ix_asset_embeddings_vector ON asset_embeddings USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    op.execute("DROP TABLE asset_embeddings")
