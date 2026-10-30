"""Widen media size_bytes columns to 64-bit bigint for unrestricted import sizes.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE media_assets ALTER COLUMN size_bytes TYPE bigint")
    op.execute("ALTER TABLE job_candidates ALTER COLUMN size_bytes TYPE bigint")


def downgrade():
    op.execute("ALTER TABLE job_candidates ALTER COLUMN size_bytes TYPE integer")
    op.execute("ALTER TABLE media_assets ALTER COLUMN size_bytes TYPE integer")

