# 02 — System Architecture

## 1. Architecture principles

- Local-first: media and inference remain on approved local infrastructure.
- Separate user interface, API, durable job execution, metadata database, and media storage.
- PostgreSQL is the authoritative source for catalogue metadata, annotations, jobs, and audit events.
- Original source media is immutable by default; derived files are stored separately.
- A job must survive a backend restart without silently losing its state.
- A UI page load must never trigger scanning or ingestion.
- Demo data must not share a namespace or storage root with production data.

## 2. Logical components

### Web frontend
Known stack: React + TypeScript + Vite. It handles navigation, forms, media browsing, review, job monitoring, and API requests. It must not contain privileged filesystem access or model secrets.

### API service
Known stack: FastAPI. It authenticates requests, validates input, enforces permissions, serves metadata, creates jobs, and provides media/thumbnail access through authorized endpoints. It should not perform unbounded heavy inference inside a request handler.

### Durable worker
A separate worker process is the target for long-running ingestion and AI jobs. It claims jobs from a durable queue or database-backed job table, records checkpoints, and acknowledges completion only after state is persisted. The exact queue technology is an implementation decision; do not introduce a broker without documenting the operational cost and failure behaviour.

FastAPI `BackgroundTasks` or an in-process thread is not sufficient as the sole execution mechanism for large, restart-sensitive archive jobs.

### PostgreSQL
Stores catalogue records, job state, annotations, model provenance, permissions/audit data, and semantic vectors if a suitable extension is installed and maintained. Existing JSONB embedding storage must be evaluated for scale before selecting the final vector-search approach.

### Local media storage
Stores original media and generated derivatives such as thumbnails and sampled video frames. Use configured paths or a supported S3-compatible local service. Store storage keys and checksums in the database; do not treat user-supplied paths as trusted.

### Model cache
Stores approved local model weights and caches. Model downloads should happen only during an explicit setup process. Production inference should not silently fetch models from the internet.

### Media utilities
Use pinned, tested local FFmpeg/FFprobe versions for video metadata and frame extraction. Treat all media parsers as attack surface; run processing with restricted permissions and resource limits.

## 3. Main data flow

1. Authorized user creates an ingestion proposal.
2. API validates permission and checks that the configured source root is approved.
3. A dry-run identifies supported files, duplicates, inaccessible files, and estimated work.
4. User reviews the proposed scope and confirms the job.
5. API creates a durable job and audit event.
6. Worker claims the job, records progress, and scans only the approved source.
7. Worker validates file type and size, computes a checksum, and uses idempotent rules to avoid duplicate records.
8. Original media is copied or referenced according to the approved storage policy; source is not modified.
9. Metadata is written to PostgreSQL; derivatives are generated separately.
10. AI workers produce suggestions with model/version provenance.
11. Human reviewers confirm or correct suggestions.
12. Search indexes are updated in a retryable way.
13. Job summary and errors are persisted and shown to the user.

## 4. Deployment boundary

Recommended initial topology:
- One Linux host on a controlled institutional LAN.
- Reverse proxy or equivalent TLS termination where required.
- Frontend static assets served by the approved local web server.
- API service reachable only through intended network routes.
- Worker process with limited filesystem access.
- PostgreSQL bound to a private interface or internal container network.
- Media/model volumes mounted only into services that need them.
- Administrative interfaces not exposed to the general LAN or internet.

Docker network isolation is helpful but is not a substitute for host firewall rules, authentication, patching, and least-privilege mounts.

## 5. Trust boundaries

Treat as untrusted:
- Uploaded files and their metadata.
- Filenames, folder names, image EXIF, video metadata, OCR text, transcripts, and model-generated text.
- Any browser input.
- Any data read from source archives.
- External model repositories and model packages until reviewed.
- Export parameters and search filters.

Do not allow OCR text, transcripts, or model outputs to become executable instructions or configuration.

## 6. Failure handling

- API unavailable: frontend displays a connection error and does not claim operations succeeded.
- Worker restart: recover queued/running jobs through a documented lease/heartbeat policy.
- Database unavailable: do not mark jobs complete; avoid partial success responses.
- Storage full: stop new writes safely, record a clear error, preserve existing originals, alert operator.
- Derivative generation failure: retain the source record and report the derivative failure separately.
- Model unavailable: mark AI processing as unavailable/failed; do not fabricate tags.
- Search index update failure: queue a retry and surface stale-index status where material.
