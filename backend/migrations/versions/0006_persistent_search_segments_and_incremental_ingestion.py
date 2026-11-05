"""Add persistent search_segments, source_file_records, and ingestion telemetry columns.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    # Widen media_assets.media_type check constraint for image, video, audio, document
    op.execute("ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS media_assets_media_type_check")
    op.execute("ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS ck_media_type")
    op.execute("ALTER TABLE media_assets ADD CONSTRAINT ck_media_type CHECK(media_type IN ('image','video','audio','document'))")

    # Widen asset_embeddings.modality column and check constraint
    op.execute("ALTER TABLE asset_embeddings DROP CONSTRAINT IF EXISTS ck_asset_embedding_modality")
    op.execute("ALTER TABLE asset_embeddings DROP CONSTRAINT IF EXISTS asset_embeddings_modality_check")
    op.execute("ALTER TABLE asset_embeddings ALTER COLUMN modality TYPE varchar(32)")
    op.execute(
        "ALTER TABLE asset_embeddings ADD CONSTRAINT ck_asset_embedding_modality "
        "CHECK(modality IN ('image','image_frame','ocr','transcript','translation','context',"
        "'document_text','document_page','document_document','document_sheet','document_slide',"
        "'document_chunk','audio_transcript','visual_description'))"
    )

    # Widen job_candidates.state check constraint for incremental ingestion states
    op.execute("ALTER TABLE job_candidates DROP CONSTRAINT IF EXISTS job_candidates_state_check")
    op.execute("ALTER TABLE job_candidates DROP CONSTRAINT IF EXISTS ck_candidate_state")
    op.execute(
        "ALTER TABLE job_candidates ADD CONSTRAINT ck_candidate_state "
        "CHECK(state IN ('ready','duplicate','invalid','ingested','failed','skipped','moved','modified'))"
    )

    # Extend ingestion_jobs with progress, byte throughput, stage, and timing columns
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS discovered_files integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS processed_files integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS total_bytes bigint NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS processed_bytes bigint NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS failed_files integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS skipped_files integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS current_stage varchar(64) NOT NULL DEFAULT 'queued'")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS started_at timestamptz")
    op.execute("ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS completed_at timestamptz")

    # Create persistent search_segments table
    op.execute(
        "CREATE TABLE IF NOT EXISTS search_segments ("
        "id varchar(36) PRIMARY KEY, "
        "asset_id varchar(36) NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE, "
        "frame_id varchar(36) REFERENCES video_frames(id) ON DELETE SET NULL, "
        "segment_type varchar(40) NOT NULL CHECK(segment_type IN ("
        "'pdf_page','office_chunk','spreadsheet_sheet','presentation_slide','ocr_text',"
        "'audio_transcript_window','video_frame','contextual_metadata','image_visual','visual_description')), "
        "modality varchar(32) NOT NULL, "
        "title text, "
        "text_content text, "
        "embedding vector(768), "
        "model_id varchar(160), "
        "model_revision varchar(64), "
        "segment_ordinal integer NOT NULL DEFAULT 0 CHECK(segment_ordinal >= 0), "
        "page_number integer, "
        "start_ms integer, "
        "end_ms integer, "
        "original_file_reference text, "
        "content_hash varchar(64) NOT NULL, "
        "metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb, "
        "created_at timestamptz NOT NULL"
        ")"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_search_segments_asset_id ON search_segments (asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_search_segments_asset_ordinal ON search_segments (asset_id, segment_ordinal)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_search_segments_type_modality ON search_segments (segment_type, modality)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_search_segments_model ON search_segments (model_id, model_revision)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_search_segments_content_hash ON search_segments (content_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_search_segments_vector ON search_segments USING hnsw (embedding vector_cosine_ops)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_segments_fts ON search_segments "
        "USING gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(text_content, '')))"
    )

    # Create source_file_records table for incremental scanning and HDD reconciliation
    op.execute(
        "CREATE TABLE IF NOT EXISTS source_file_records ("
        "id varchar(36) PRIMARY KEY, "
        "source_id varchar(36) NOT NULL REFERENCES source_definitions(id) ON DELETE CASCADE, "
        "source_relative_key text NOT NULL, "
        "asset_id varchar(36) REFERENCES media_assets(id) ON DELETE SET NULL, "
        "original_filename text NOT NULL, "
        "media_type varchar(16) NOT NULL, "
        "size_bytes bigint NOT NULL, "
        "mtime_ns bigint NOT NULL, "
        "device_inode varchar(64), "
        "sha256 varchar(64) NOT NULL, "
        "status varchar(24) NOT NULL DEFAULT 'active' CHECK(status IN ('active','moved','missing_from_source','failed')), "
        "last_seen_job_id varchar(36), "
        "last_seen_at timestamptz NOT NULL, "
        "created_at timestamptz NOT NULL, "
        "updated_at timestamptz NOT NULL, "
        "CONSTRAINT uq_source_file_record_key UNIQUE(source_id, source_relative_key)"
        ")"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_file_records_source_id ON source_file_records (source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_file_records_asset_id ON source_file_records (asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_file_records_sha256 ON source_file_records (source_id, sha256)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS source_file_records")
    op.execute("DROP TABLE IF EXISTS search_segments")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS completed_at")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS started_at")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS current_stage")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS skipped_files")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS failed_files")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS processed_bytes")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS total_bytes")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS processed_files")
    op.execute("ALTER TABLE ingestion_jobs DROP COLUMN IF EXISTS discovered_files")
