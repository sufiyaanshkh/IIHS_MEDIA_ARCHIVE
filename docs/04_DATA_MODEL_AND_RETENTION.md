# 04 — Data Model and Retention

## 1. Principles

- Stable IDs are primary references; filenames and paths are not identities.
- Preserve provenance: source, timestamp, process version, and reviewer decision.
- Separate original media, derivatives, AI suggestions, and human-confirmed annotations.
- Enforce uniqueness/idempotency at the database level where practical.
- Use migrations for schema changes.
- Do not store secrets in application tables or logs.
- Avoid putting large binary media directly into ordinary relational rows.

## 2. Logical entities

These are logical requirements, not a claim that the current schema already has these exact tables. Reconcile with existing SQLAlchemy models and migrations before implementation.

### MediaAsset
- `id`: stable UUID or equivalent.
- `media_type`: image/video/document only if the type is supported.
- `source_id`: approved source definition.
- `source_relative_key`: normalized key relative to configured root; avoid exposing full host paths to ordinary users.
- `original_filename`: untrusted display metadata, safely encoded in UI.
- `mime_type_detected`: server-detected type.
- `size_bytes`.
- `checksum_algorithm` and `checksum`.
- `created_at`, `ingested_at`.
- `metadata_status`, `processing_status`.
- `storage_key` or controlled path reference.
- `deleted_at` / retention state where applicable.

### MediaMetadata
- Asset ID.
- Width, height, duration, codec, capture timestamp, EXIF-derived values, and other permitted fields.
- Provenance and extraction version.
- Sensitive metadata must be minimized and access controlled.

### Derivative
- Asset ID.
- Derivative type (thumbnail, preview, sampled frame).
- Storage key, dimensions, checksum, creation time, generation version, status.

### Tag / ObjectClass
- Canonical label, description, taxonomy or category, active state, and creation provenance.
- Avoid duplicate labels caused by inconsistent casing/spacing.

### Annotation
- Asset ID.
- Tag or object reference.
- Source (`model`, `human`, `imported`).
- Status (`suggested`, `accepted`, `rejected`, `superseded`).
- Confidence only if supplied by the model and meaningful.
- Model name/version and pipeline version for machine suggestions.
- Reviewer ID and review timestamp for human decisions.
- Optional region/bounding box with validated coordinate convention.

### FaceEnrollment (restricted)
- Person record ID and approved display name.
- Enrollment consent/authorization reference if applicable under institutional policy.
- Image asset references.
- Face detection quality/number-of-faces review outcome.
- Embedding version, model version, and restricted embedding storage reference.
- Created/updated by, timestamps, and deactivation/deletion state.
- Access must be restricted and audited.

### VideoFrame
- Parent video asset ID.
- Timestamp in a consistent unit (recommend integer milliseconds).
- Derivative storage key.
- Sampling strategy/version.
- Optional annotations associated with that timestamp.

### Job
- ID, job type, parameters in validated schema, requesting user, state, timestamps.
- Progress counters and current phase.
- Worker lease/heartbeat if applicable.
- Retry count, cancellation request, checkpoint, summary, and error reference.
- Avoid storing unbounded logs in a single row.

### AuditEvent
- Actor, action, target type/ID, timestamp, result, request/correlation ID.
- Do not log credentials, session tokens, full biometric vectors, or unnecessary sensitive content.
- Audit events should be protected against ordinary user modification.

### SourceDefinition
- Administrator-configured identifier, approved root, read-only policy, enabled state, allowed media types, and audit timestamps.
- Full path details visible only to authorized operators.
- The backend must verify every requested source is within an approved root.

## 3. Database integrity

- Use foreign keys for relationships.
- Use constraints for enumerated states and non-negative sizes/progress.
- Use transactions for related metadata and job-state changes.
- Use unique constraints for idempotency keys and suitable checksum/source combinations.
- Add indexes based on measured query patterns, not guesswork.
- Use vector extensions only after confirming installation, backup, migration, and operational support.
- Plan schema migrations with rollback or forward-fix instructions.

## 4. Retention and deletion

Define separate policies for:
- Original source media.
- Thumbnails and sampled frames.
- AI annotations.
- Human decisions and audit events.
- Face embeddings and enrollment references.
- Logs and temporary files.
- Backups and exports.

Deleting a database record must not accidentally leave untracked copies of sensitive derivatives, nor should a failed database transaction remove the only source file. Use a staged deletion process and reconciliation job.

For biometric-related records, establish institutional approval, retention period, access list, deletion procedure, and backup deletion policy before enabling the feature.

## 5. Metadata trust

EXIF, filenames, folder names, OCR, transcripts, and AI outputs are input data, not trusted instructions. Escape them in the UI and avoid using them to form SQL, filesystem paths, HTML, or shell commands without safe APIs and validation.
