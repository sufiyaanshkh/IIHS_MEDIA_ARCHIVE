"""Separate self-registration rate limits from sign-in failures.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE login_attempts ADD COLUMN purpose varchar(12) NOT NULL DEFAULT 'login' CHECK(purpose IN ('login','register'))")
    op.execute("DROP INDEX ix_login_attempts_ip_time")
    op.execute("CREATE INDEX ix_login_attempts_ip_purpose_time ON login_attempts (ip_hash, purpose, attempted_at)")


def downgrade():
    op.execute("DROP INDEX ix_login_attempts_ip_purpose_time")
    op.execute("ALTER TABLE login_attempts DROP COLUMN purpose")
    op.execute("CREATE INDEX ix_login_attempts_ip_time ON login_attempts (ip_hash, attempted_at)")
