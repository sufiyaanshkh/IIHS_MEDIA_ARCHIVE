"""Initial application schema.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE users (id varchar(36) PRIMARY KEY, username varchar(120) NOT NULL UNIQUE, password_hash varchar(255) NOT NULL, role varchar(24) NOT NULL CHECK(role IN ('admin','archivist','researcher','operator')), active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL)")
    op.execute("CREATE TABLE auth_sessions (token_hash varchar(64) PRIMARY KEY, user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL)")
    op.execute("CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id)")
    op.execute("CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at)")
    op.execute("CREATE TABLE source_definitions (id varchar(36) PRIMARY KEY, name varchar(160) NOT NULL, root_path text NOT NULL, is_demo boolean NOT NULL DEFAULT false, enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL)")
    op.execute("CREATE TABLE media_assets (id varchar(36) PRIMARY KEY, source_id varchar(36) REFERENCES source_definitions(id) ON DELETE SET NULL, source_relative_key text, original_filename text NOT NULL, media_type varchar(12) NOT NULL CHECK(media_type IN ('image','video')), detected_mime varchar(120) NOT NULL, size_bytes integer NOT NULL CHECK(size_bytes >= 0), sha256 varchar(64) NOT NULL UNIQUE, storage_key varchar(240) NOT NULL UNIQUE, thumbnail_key varchar(240), width integer, height integer, duration_ms integer, metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL)")
    op.execute("CREATE INDEX ix_media_created_id ON media_assets (created_at, id)")
    op.execute("CREATE UNIQUE INDEX uq_media_source_key ON media_assets (source_id, source_relative_key)")
    op.execute("CREATE TABLE tags (id varchar(36) PRIMARY KEY, canonical_name varchar(160) NOT NULL UNIQUE, display_name varchar(160) NOT NULL, description text, active boolean NOT NULL DEFAULT true, created_by varchar(36) REFERENCES users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL)")
    op.execute("CREATE TABLE annotations (id varchar(36) PRIMARY KEY, asset_id varchar(36) NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE, tag_id varchar(36) REFERENCES tags(id) ON DELETE SET NULL, label varchar(160) NOT NULL, source varchar(16) NOT NULL CHECK(source IN ('model','human','imported')), status varchar(16) NOT NULL CHECK(status IN ('suggested','accepted','rejected','corrected','deferred')), confidence double precision CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), model_name varchar(160), model_version varchar(120), created_by varchar(36) REFERENCES users(id) ON DELETE SET NULL, reviewed_by varchar(36) REFERENCES users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL, reviewed_at timestamptz)")
    op.execute("CREATE INDEX ix_annotations_asset_id ON annotations (asset_id)")
    op.execute("CREATE TABLE review_events (id varchar(36) PRIMARY KEY, annotation_id varchar(36) NOT NULL REFERENCES annotations(id) ON DELETE CASCADE, reviewer_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT, decision varchar(16) NOT NULL CHECK(decision IN ('accept','reject','correct','defer')), previous_label varchar(160) NOT NULL, final_label varchar(160), comment text, created_at timestamptz NOT NULL)")
    op.execute("CREATE INDEX ix_review_events_annotation_id ON review_events (annotation_id)")
    op.execute("CREATE TABLE ingestion_jobs (id varchar(36) PRIMARY KEY, kind varchar(20) NOT NULL CHECK(kind IN ('preview','ingest')), state varchar(24) NOT NULL CHECK(state IN ('queued','running','preview_ready','cancel_requested','completed','partially_completed','failed','cancelled')), source_id varchar(36) NOT NULL REFERENCES source_definitions(id) ON DELETE RESTRICT, requested_by varchar(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT, idempotency_key varchar(64), request_signature varchar(64), max_files integer NOT NULL CHECK(max_files > 0), progress_done integer NOT NULL DEFAULT 0 CHECK(progress_done >= 0), progress_total integer NOT NULL DEFAULT 0 CHECK(progress_total >= 0), summary jsonb NOT NULL DEFAULT '{}'::jsonb, error_summary text, cancel_requested boolean NOT NULL DEFAULT false, heartbeat_at timestamptz, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, CONSTRAINT uq_job_actor_request UNIQUE(requested_by,idempotency_key))")
    op.execute("CREATE INDEX ix_ingestion_jobs_state ON ingestion_jobs (state)")
    op.execute("CREATE TABLE job_candidates (id varchar(36) PRIMARY KEY, job_id varchar(36) NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE, source_relative_key text NOT NULL, original_filename text NOT NULL, media_type varchar(12) NOT NULL, size_bytes integer NOT NULL, sha256 varchar(64) NOT NULL, state varchar(16) NOT NULL CHECK(state IN ('ready','duplicate','invalid','ingested','failed')), message text, metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb, CONSTRAINT uq_job_candidate_key UNIQUE(job_id, source_relative_key))")
    op.execute("CREATE INDEX ix_job_candidates_job_id ON job_candidates (job_id)")
    op.execute("CREATE TABLE audit_events (id varchar(36) PRIMARY KEY, actor_id varchar(36) REFERENCES users(id) ON DELETE SET NULL, action varchar(100) NOT NULL, target_type varchar(40) NOT NULL, target_id varchar(36), request_id varchar(64), result varchar(16) NOT NULL DEFAULT 'success', detail jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL)")
    op.execute("CREATE INDEX ix_audit_events_action ON audit_events (action)")
    op.execute("CREATE TABLE login_attempts (id varchar(36) PRIMARY KEY, ip_hash varchar(64) NOT NULL, attempted_at timestamptz NOT NULL)")
    op.execute("CREATE INDEX ix_login_attempts_ip_time ON login_attempts (ip_hash, attempted_at)")
    op.execute("CREATE TABLE video_frames (id varchar(36) PRIMARY KEY, asset_id varchar(36) NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE, timestamp_ms integer NOT NULL CHECK(timestamp_ms >= 0), storage_key varchar(240) NOT NULL UNIQUE, created_at timestamptz NOT NULL)")
    op.execute("CREATE INDEX ix_video_frames_asset_id ON video_frames (asset_id)")


def downgrade():
    op.execute("DROP TABLE video_frames")
    op.execute("DROP TABLE login_attempts")
    op.execute("DROP TABLE audit_events")
    op.execute("DROP TABLE job_candidates")
    op.execute("DROP TABLE ingestion_jobs")
    op.execute("DROP TABLE review_events")
    op.execute("DROP TABLE annotations")
    op.execute("DROP TABLE tags")
    op.execute("DROP TABLE media_assets")
    op.execute("DROP TABLE source_definitions")
    op.execute("DROP TABLE auth_sessions")
    op.execute("DROP TABLE users")
