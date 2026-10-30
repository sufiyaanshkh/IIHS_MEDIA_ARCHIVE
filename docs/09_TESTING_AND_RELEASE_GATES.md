# 09 — Testing and Release Gates

## 1. Test pyramid

### Unit tests
Test validation, state transitions, path-root checks, deduplication, permission decisions, annotation transitions, and utility functions.

### Integration tests
Test FastAPI routes with a test database, migrations, storage adapters, job creation/claiming, and worker recovery. Use temporary directories and synthetic files.

### Contract tests
Check request/response schemas, status codes, pagination, authentication requirements, and frontend API service expectations.

### End-to-end tests
Exercise login, media browsing, search, review, dry-run ingestion, job monitoring, and error states in a browser when tooling is available.

### Security tests
Cover authorization bypass, path traversal, malicious metadata, unsafe file delivery, rate limits, secrets exposure, and dependency scanning.

### AI evaluation
Use a versioned labelled dataset and report task-specific precision, recall, coverage, latency, and failure categories.

## 2. Test environments

- **Unit:** no external services.
- **Integration:** isolated test database and temporary storage.
- **Demo/staging:** synthetic/sample media only, no real source roots.
- **Production:** approved local environment with controlled access and backups.

Never point a test reset or fixture loader at production. Require an explicit environment guard before destructive scripts run.

## 3. Required functional scenarios

- Fresh setup starts using documented instructions.
- Demo dashboard reports actual demo values.
- Media list pagination and filters behave correctly.
- Missing thumbnail and unavailable original states are clear.
- Search errors differ from empty results.
- Human reviewer accepts, rejects, and edits suggestions.
- Invalid state transitions are rejected.
- Dry-run ingestion reports the proposed scope without committing production ingestion.
- Confirmed demo job can be cancelled or safely retried where supported.
- Worker restart does not lose or duplicate completed work.
- One malformed file does not terminate the whole batch.
- Video frames retain timestamps.
- Face enrollment stays disabled until its prerequisites are approved.
- Unauthorized users cannot access media or job details.
- App startup never scans real archive directories.

## 4. Reliability scenarios

- Database restart during a job.
- Worker process termination.
- Disk full or read-only storage.
- Missing model files.
- Corrupted image/video.
- Duplicate source files.
- Permission denied on a file.
- Search index unavailable.
- Repeated cancellation request.
- Retry of a partially completed job.
- Application upgrade with pending database migration.

For each scenario, define expected state, recovery action, and audit trail.

## 5. Security release gates

Before real media is ingested:
- All high-risk security tests pass.
- No known critical vulnerability is left unmitigated.
- Role and object authorization tests pass.
- Path traversal and symlink escape tests pass.
- Secrets scanning passes.
- Database/network exposure is reviewed.
- Backup restoration is tested.
- Security owner reviews the threat model and signs off.
- Face recognition remains disabled unless separately approved.

A scan tool reporting zero issues does not by itself establish security.

## 6. Performance tests

Use synthetic datasets sized to expected operating conditions. The 50 TB estimate is unverified; use measured file count, average size, file-size distribution, and expected concurrency before setting performance goals.

Measure:
- API latency at realistic page sizes.
- Thumbnail response latency.
- Search latency and relevance.
- Ingestion throughput by file type/size.
- CPU, RAM, VRAM, and disk usage.
- Worker restart/recovery time.
- Database growth and index size.
- Behaviour under slow storage and a full disk.

Set numerical service targets with the product owner after a baseline measurement.

## 7. Release report

Every release should record:
- Commit ID and build artifact.
- Dependency/security scan results.
- Tests run and pass/fail summary.
- Migration status.
- Configuration changes.
- Known limitations.
- Backup and rollback/recovery plan.
- Approval owner and date.
