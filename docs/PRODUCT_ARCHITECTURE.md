# Product and Integration Architecture

## Product goal

Combine three workflows into one institutional media archive:
1. **DigUp-inspired discovery** — natural-language retrieval across a local corpus using Google EmbeddingGemma 2, with lexical fallback.
2. **IIHS Visual Intelligence workflows** — object tags, confidence-aware human review, and dataset exports.
3. **Media archive operations** — safe source selection, ingest jobs, metadata, thumbnails, collections, provenance, and lifecycle management.

The user experience should be a research archive, not a generic AI analytics dashboard. Prioritize typographic hierarchy, restrained warm neutrals, brick-red action color, muted botanical greens, image-led browsing, and a human-reviewed archive record.

## Reference projects (reviewed at repository level)

- DigUp describes itself as local-first search across photos, PDFs, documents, recordings, videos, and code using EmbeddingGemma 2. The inspected implementation uses a Swift app/CLI and separate worker processes; the Python worker communicates over JSON lines.
- IIHS Visual Intelligence README describes upload, object detection, confidence-routed review/manual annotation, versioned training datasets, and YOLO/COCO export. Its stack includes React and FastAPI, with PostgreSQL and S3-compatible storage documented elsewhere in the repository.
- IIHS_MEDIA_ARCHIVE was empty before this scaffold.

This repository should integrate capabilities and UX concepts, not copy the unrelated implementation wholesale. Confirm dependency and model-license compatibility before bringing over code or weights.

## System shape

```text
React + TypeScript UI
  ├── Archive search and image-led browse
  ├── Ingestion wizard / dry-run / durable job status
  ├── Asset detail / tags / provenance
  ├── Human review / correction
  └── Collection and dataset workspaces
             │ authenticated typed HTTP API
             ▼
FastAPI service
  ├── Auth, access control, audit events
  ├── Source registry (approved mount IDs, never arbitrary paths)
  ├── Durable ingestion job records + queue worker
  ├── Metadata / EXIF / thumbnails / FFprobe / sampled video frames
  ├── Local tagging and OCR worker
  ├── Local EmbeddingGemma 2 encoder
  └── Search / review / dataset API
             │
             ├── Media storage: originals and derivatives
             ├── PostgreSQL: assets, metadata, jobs, tags, reviews
             └── Vector index: EmbeddingGemma vectors and asset references
```

## Search design

- Keep the text query experience independent from the embedding backend so lexical search is useful during model startup or when the model is unavailable.
- Encode query text with the correct EmbeddingGemma task prefix and match it against precomputed media/text embeddings. Follow DigUp's task conventions after inspecting the exact model integration.
- Do not assume embeddings exist for images merely because a text-only worker exists. Define which inputs are embedded: captions, extracted document text, OCR, video keyframes, and/or image model outputs.
- Return explainable evidence: title, collection, date, file type, matched tags, and relevant excerpt/frame timestamp when available.
- Record model/version, input template, dimensions, normalization, generation time, and any model migration metadata.

## Ingestion requirements

1. Select explicit files or a browser-supported directory, or choose a preconfigured server mount ID.
2. Create a dry-run job that counts files, validates types, detects duplicates, and reports unsupported formats and errors.
3. Require explicit confirmation before durable ingest.
4. Persist a job and per-file outcome before worker execution; never make an in-process FastAPI background task the only copy of a job.
5. Process bounded batches; checkpoints make restart/retry safe.
6. Store originals immutably unless a separate explicit retention policy says otherwise. Generate derivatives separately.
7. Record hash, relative path, source ID, timestamps, file size, MIME, media dimensions/duration, metadata and processing outcomes.
8. Never erase or overwrite source files during indexing.

Browser file/folder selection is not the same as arbitrary whole-drive access. Whole-disk indexing of a user's machine needs a trusted local helper; server storage must be explicitly configured and permission checked. Never accept untrusted root paths or shell command fragments from the UI.

## UI status

The current branch adds a polished React/Vite UI prototype with:
- Archive browsing with sample imagery, keyword filtering, type filters, responsive grid/list view and a sample asset detail modal.
- A source-selection / settings / preview / demo-progress ingestion flow.
- Clear user-facing messages distinguishing prototype UI from real processing.
- Placeholder review, collection, and dataset workspace pages.
- Local-first messaging and an explicit note that EmbeddingGemma 2 backend integration is pending.

Current interactions are prototype-level. Real authentication, filesystem scan, embeddings, live archive data, worker orchestration and actual media upload are not implemented by the frontend scaffold.

## Implementation phases

### Phase 1 — Foundation
- [x] Initialize repository documentation and React/Vite/TypeScript scaffold.
- [x] Add responsive editorial archive and ingestion prototype.
- [ ] Install dependencies and verify typecheck/build in a real Node environment.
- [ ] Add linting, tests, accessibility checks and CI.

### Phase 2 — API and storage
- [ ] Establish FastAPI app structure and versioned OpenAPI routes.
- [ ] Establish PostgreSQL schema and migrations.
- [ ] Add local storage adapter and configure read-only source mounts.
- [ ] Add auth, user roles and audit events.
- [ ] Add durable job queue, job status, cancellation semantics and recovery.

### Phase 3 — Media pipeline
- [ ] Implement source adapters for selected file upload, supported directory picker, approved server mounts and optional local agent.
- [ ] Implement dry-run scan, hashes, duplicate policy, metadata, thumbnails, FFprobe and bounded keyframe sampling.
- [ ] Add safe OCR and local visual tagging.
- [ ] Ensure all jobs are restart safe and source files remain unchanged.

### Phase 4 — Search
- [ ] Integrate a versioned local EmbeddingGemma 2 worker with a defined JSON-line or HTTP protocol.
- [ ] Validate supported hardware/OS, quantization, model/task configuration, memory and throughput.
- [ ] Generate vectors for specified media representations; index them durably.
- [ ] Add vector similarity retrieval and lexical fallback with relevance evaluation.
- [ ] Do not claim the product is entirely offline until model assets are cached and no processing path makes external requests.

### Phase 5 — Human review and exports
- [ ] Persist predicted tags with model name, confidence, evidence and review state.
- [ ] Support accept/edit/reject with audit trail.
- [ ] Version datasets and provide integrity-checked YOLO/COCO exports.
- [ ] Add curator actions and permission checks.

### Phase 6 — Production readiness
- [ ] Test with synthetic and approved sample media before real archive connection.
- [ ] Load-test at realistic corpus sizes, not only thumbnail demo data.
- [ ] Add backup/restore drills, storage quotas, monitoring and job repair tooling.
- [ ] Verify model and dependency licenses, security scanning and accessibility.
- [ ] Connect real institutional storage only after its owner approves the source roots and policy.

## Definition of done for live ingestion

- Never starts scanning automatically on page load.
- Source is chosen explicitly and approved roots are enforced server-side.
- Dry-run and confirmation are mandatory for a large batch.
- A job survives service restart and can continue without duplicating completed work.
- Partial failures, inaccessible files, symlinks, oversized files and unsupported formats are reported safely.
- Original files are not deleted or altered.
- Progress is based on persisted task outcomes, not timer animation.
- Logs exclude credentials and sensitive extracted content by default.
- Tests cover traversal, root escapes, symlink behavior, duplicate hash handling, cancellation and retry.

## Licensing note

DigUp is listed with an MIT repository license; this alone does not mean any bundled model weights or separately downloaded model artifacts have permissive commercial terms. Inspect the exact EmbeddingGemma 2 model card and any third-party runtime/model terms before redistribution. Do not copy DigUp code into this repository without reviewing applicable license obligations.
