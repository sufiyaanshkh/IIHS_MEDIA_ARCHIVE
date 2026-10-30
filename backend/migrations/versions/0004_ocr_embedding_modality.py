"""Allow OCR text vectors to be stored with the asset embeddings.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE asset_embeddings DROP CONSTRAINT asset_embeddings_modality_check")
    op.execute("ALTER TABLE asset_embeddings ADD CONSTRAINT ck_asset_embedding_modality CHECK(modality IN ('image','image_frame','ocr','transcript','translation'))")


def downgrade():
    op.execute("ALTER TABLE asset_embeddings DROP CONSTRAINT ck_asset_embedding_modality")
    op.execute("ALTER TABLE asset_embeddings ADD CONSTRAINT asset_embeddings_modality_check CHECK(modality IN ('image','image_frame','transcript','translation'))")
