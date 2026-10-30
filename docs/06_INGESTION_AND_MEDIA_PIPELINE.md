# 06 — Ingestion and Media Pipeline

## 1. Operating modes

### Demo mode
Default for initial review. Uses synthetic records and a dedicated sample-data directory. It cannot access production roots.

### Dry-run mode
Reads only an approved source root, performs discovery and validation, and produces a proposed file list/count/error summary. It does not create production catalogue records or start expensive AI inference unless explicitly designed and approved.

### Production mode
Disabled until a named operator configures the source and confirms the scope. Every run creates a durable job and audit event.

## 2. Source-root safety

- Source roots must be configured by an administrator on the server.
- The browser must not submit arbitrary server paths.
- Canonicalize and resolve paths before use.
- Ensure each candidate path remains within the configured root after resolving symlinks.
- Define symlink behaviour explicitly; safest default is to skip symlinks.
- Mount source volumes read-only wherever possible.
- Never use shell string concatenation to invoke media utilities.
- Apply allowlists and resource limits.
- Record relative keys, not unrestricted host paths, in ordinary API responses.

## 3. File validation

For each candidate:
1. Check path and file status.
2. Check size and configured limits.
3. Detect actual file type using a trusted library or magic bytes, not just extension.
4. Apply supported-format allowlists.
5. Compute a cryptographic checksum (such as SHA-256) for identity/deduplication.
6. Capture basic metadata using safe APIs.
7. Quarantine or skip malformed/unreadable files according to policy.
8. Persist per-file outcomes so the job can resume.

The supported file list and limits must be configuration-driven and documented. Never assume every file that ends in `.jpg` or `.mp4` is safe or valid.

## 4. Idempotency and duplicates

Define a stable rule for duplicate detection. A checksum can identify byte-identical content; it does not prove two files with different bytes depict the same scene. Preserve provenance for duplicates and do not delete source files automatically.

Retrying a job must not create duplicate media rows or repeated annotations. Use database constraints and idempotency keys in addition to application checks.

## 5. Image pipeline

Suggested stages:
1. Discover.
2. Validate.
3. Hash.
4. Register source metadata.
5. Generate thumbnail/preview.
6. Extract approved metadata.
7. Run enabled local inference.
8. Persist machine suggestions with provenance.
9. Update search index.
10. Mark complete or partially complete.

Derivative and inference failures should be recorded separately from original-file discovery. One bad image must not crash an entire batch.

## 6. Video pipeline

1. Validate container and size.
2. Run pinned FFprobe in a restricted worker.
3. Persist duration, dimensions, codec, and safe metadata.
4. Apply configurable frame sampling (for example, interval- or scene-based) with a hard maximum.
5. Generate timestamp-linked frames.
6. Optionally run local speech recognition only after its model, language support, and resource cost are approved.
7. Store transcript segments with timestamps and model/version provenance.
8. Run local tagging/search embedding on approved sampled content.
9. Report partial failures per stage.

Do not extract every frame by default. Use subprocess argument arrays, not a shell command assembled from user-controlled values. Set CPU, memory, runtime, file-size, and output-count limits.

## 7. Durable jobs

A job system must persist:
- Queued, running, cancellation-requested, completed, partially-completed, failed, and cancelled states as appropriate.
- Started/updated/completed timestamps.
- Worker heartbeat or lease.
- Stage and checkpoint.
- Counts for discovered, processed, skipped, duplicate, failed.
- Retry count and safe error summary.
- Requesting user and source definition.
- Audit events for start, cancellation, retry, and completion.

A recovery process should identify abandoned jobs and resume only from a safe checkpoint. Make retry policy explicit. Avoid infinite retries and poison-file loops.

## 8. Resource protection

- Bound worker concurrency.
- Bound image dimensions and decode memory.
- Bound video duration and frame extraction.
- Use a dedicated unprivileged worker account/container.
- Apply filesystem quotas where practical.
- Monitor disk usage and stop before storage exhaustion.
- Use temporary directories with cleanup and restrictive permissions.
- Do not mount Docker socket into the worker.
- Keep the original source read-only.

## 9. Production rollout

- Begin with a small approved pilot.
- Compare file counts and checksums against the dry-run report.
- Review sample outputs and error rates.
- Confirm storage capacity, backup coverage, and processing throughput.
- Increase batch size gradually.
- Pause ingestion if error rates, disk pressure, or data integrity checks cross agreed thresholds.

## 10. Direct file-picker imports

The local application can import explicit image/video selections from the browser’s native file picker. Uploads use a server-generated private staging directory, are checked against the configured file count/size/storage limits, and use decoded media validation plus SHA-256 duplicate checks. A durable ingestion job is queued immediately; after indexing succeeds, temporary upload copies are removed. Failed items keep staged copies so an authorized operator can retry. Client-supplied file paths and content-type claims are not trusted or persisted.
