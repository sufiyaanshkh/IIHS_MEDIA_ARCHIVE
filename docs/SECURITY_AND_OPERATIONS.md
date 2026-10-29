# Local-first safety and data handling

## Trust boundaries

- The browser is an untrusted client. It can submit a configured source identifier and an ingestion policy, but it cannot decide server filesystem roots.
- The API authenticates every private operation and authorizes the specific collection, mount, and job.
- Workers only traverse roots registered by an administrator. Paths are canonicalized and checked for containment; symlinks and mount boundaries require an explicit policy.
- A browser directory picker only exposes user-selected files; it cannot silently enumerate all drives.
- To index a user's full local disk, use a trusted signed local helper with explicit per-folder permission and visible controls. To index server-attached volumes, use approved mount IDs.
- Never pass user-controlled paths or filenames into shell command strings. Use argument arrays and safely handle unusual Unicode/newlines.
- Use read-only access to source roots by default; put thumbnails and transformed derivatives in a separate managed storage root.
- The app must not process the real IIHS archive until the data owner explicitly approves storage roots and retention policy.

## Minimum API controls

- Auth on source list, dry-run, ingest, job status, cancel, asset detail, review and export.
- Role checks: viewer, curator, ingestion operator, administrator.
- Request size and file size limits, request timeouts, rate limits and bounded worker concurrency.
- Validate MIME by inspection/content where feasible; don't trust extension alone. Parse PDFs, Office files, video and archives with resource limits.
- Deny or isolate malformed/corrupt files. Defend against archive bombs if archive extraction is ever supported; default to no arbitrary archive unpacking.
- Keep secrets in environment/secret manager, not git; restrict CORS to approved app origins.
- Maintain audit events for source changes, scans, manual tag edits, exports, and destructive administrative actions.
- Use parameterized SQL and scoped access to stored assets. Do not expose host paths to viewers.
- Logs should report safe file identifiers and error classes rather than dump extracted content, credentials or biometric data.

## Ingestion durability

Each ingestion job records a stable ID, source identifier, selected options, requestor, creation time, status and counters. Each file outcome records a stable file identity/hash where available, source-relative path, processing stages, retry count, and terminal status. Workers checkpoint per file or bounded batch. Re-running a job uses idempotent keys so completed work is not performed twice unnecessarily.

Cancel means "stop scheduling new work and safely checkpoint or cancel current work"; do not pretend a force-killed process can roll back all completed files. Retry only transient failure states and limit attempts. Expose errors for inaccessible paths and duplicate policies.

## Model and privacy controls

- Model files are downloaded only through an explicit installation/setup flow and verified against known artifact metadata.
- No cloud inference calls by default. If the local model isn't ready, say so and offer lexical search instead.
- Store embedding model/version, task prefix, preprocessing version, vector dimension, and document/image representation.
- Face enrollment and identity search are not part of this UI foundation. If later added, require a documented legitimate purpose, tight permissions, audit logging, retention controls, data-owner approval, and review of model licensing/accuracy. Do not infer identities from ordinary image captions.

## Frontend UX requirements

- Distinguish "selected", "dry run complete", "queued", "processing", "partial", "failed", and "completed" using real server state.
- Never show fabricated progress or success for production operations.
- Confirm high-impact changes, show source and estimated counts, and keep the original source read-only.
- Do not place credentials or raw server paths in query strings or analytics.
- Include keyboard navigation, visible focus, accessible labels, sufficient contrast, and reduced-motion handling.
