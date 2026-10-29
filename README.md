# IIHS Media Archive

A local-first media archive that brings together the strongest ideas from **DigUp** (on-device semantic search with Google EmbeddingGemma 2) and **IIHS Visual Intelligence** (visual tagging, human review, and dataset workflows).

## Product direction

- Beautiful, editorial archive UI designed for research teams and institutional collections.
- Search by meaning with locally generated EmbeddingGemma 2 vectors; keep keyword search available when embeddings are unavailable.
- Ingest selected files, folders, or administrator-approved server mounts through a safe, auditable pipeline.
- Preserve originals and provenance; generate metadata, thumbnails, AI suggestions, and review tasks.
- Keep human review and dataset export as first-class workflows.
- Keep processing local-first. No cloud AI API is required for the target architecture.

## Status

The `feature/local-backend-pipeline` branch includes a local FastAPI/PostgreSQL ingestion scaffold, archive API integration, optional review-request status, and a review queue for explicitly submitted assets. Ingestion does **not** require human approval; indexed assets are visible immediately. This remains a development scaffold, not a complete production AI archive.

## Run the UI

For the full local stack setup, ingestion smoke test, and current model-readiness matrix, see [docs/LOCAL_RUN_GUIDE.md](docs/LOCAL_RUN_GUIDE.md).

Requirements: Node.js 20+ and npm.

```bash
npm install
npm run dev
```

For a production build:

```bash
npm run build
npm run preview
```

## Reference projects

- [DigUp](https://github.com/ARahim3/DigUp) — local-first semantic search over files with EmbeddingGemma 2.
- [IIHS Visual Intelligence](https://github.com/sufiyaanshkh/IIHS-Visual-Intelligence) — visual tagging, human-in-the-loop review, and dataset workflows.

This project is an integration target inspired by their capabilities; it does not copy either project wholesale. Respect each project's license and model-weight terms when integrating implementation or assets.

## Planned architecture

```text
React UI
  ├─ Archive browse / filters / item detail
  ├─ Semantic search UI
  ├─ Ingestion wizard + dry-run review
  └─ Human review + dataset workspace
          │ HTTPS / typed API
          ▼
FastAPI API + authentication
  ├─ Ingestion jobs (durable queue and persisted progress)
  ├─ Media validation, metadata and thumbnail workers
  ├─ Local tagging / OCR / sampled video frames
  ├─ Local EmbeddingGemma 2 embedding worker
  └─ Review and dataset APIs
          │
          ├─ Media storage (originals + derived assets)
          ├─ PostgreSQL (assets, tags, jobs, users, audit trail)
          └─ Vector index (embedding storage and similarity search)
```

## Important ingestion boundary

A web browser cannot silently enumerate every drive on a user's computer. Browser selection is appropriate for selected files and supported folder pickers. Whole-drive scanning must be performed by a trusted local agent, or by a server worker against explicitly configured and permission-checked mount roots. The UI must never accept arbitrary filesystem paths from untrusted clients.

## License

No license has been added yet. Set a project license before redistributing this repository.


## Local backend (development scaffold)

Prerequisites: Docker Desktop / Docker Engine with Compose, and Node.js 20+ if running the frontend outside Docker.

1. Clone and enter the repository:
   ```bash
   git clone https://github.com/sufiyaanshkh/IIHS_MEDIA_ARCHIVE.git
   cd IIHS_MEDIA_ARCHIVE
   git checkout feature/local-backend-pipeline
   ```
2. Create your local environment file:
   ```bash
   cp backend/.env.example backend/.env
   mkdir -p approved-archive data/people data/storage data/derivatives models
   ```
3. Put a **small test corpus** into `approved-archive/` first. The compose mount is read-only inside the containers.
4. Start the database, API and worker:
   ```bash
   docker compose up --build -d
   docker compose logs -f api worker
   ```
5. Check health and interactive API docs:
   - `http://127.0.0.1:8000/health`
   - `http://127.0.0.1:8000/docs`
6. Stop services with `docker compose down`. Do not use `docker compose down -v` unless you intend to delete the database volume.

### People / employee profile images

The local-only folder is `data/people/`. Example structure:

```text
data/people/
  EMP001/
    profile.jpg
  EMP002/
    profile.png
```

Do not commit actual staff images. Use local employee identifiers and maintain appropriate authorization, notice/consent where required, retention and access controls. The API's employee-profile registration requires a consent/authorization flag. This is currently an administrative profile registry only: face embeddings and biometric identity matching are **not enabled**.

### Configure the approved archive source

The Compose file mounts `IIHS_ARCHIVE_MOUNT` (defaults to `./approved-archive`) read-only at `/archive` and registers only that mount for ingestion. To use a different directory, set `IIHS_ARCHIVE_MOUNT` in your shell or a local untracked environment file before starting Compose. Inspect the final resolved mounts with `docker compose config` before starting any scan.

### Current backend scope — read before using real data

The backend currently provides PostgreSQL-backed asset/tag/job/profile tables, source-root validation, a dry-run directory preview, a separate polling worker, chunked SHA-256 hashing, duplicate skipping, image metadata and thumbnails, basic PDF text extraction, FFprobe video metadata, lexical search, and a local embedding adapter boundary.

**This is not yet a complete production archive pipeline.** In particular:
- Video frame sampling and speech transcription are not implemented.
- OCR, AI image captioning/tagging, Qwen3-VL, YOLO inference, and actual vector similarity search are not wired end-to-end.
- EmbeddingGemma 2 is disabled by default; the local runtime, task-prefix handling, durable vector index and relevance tests must be completed before enabling semantic search.
- Browser upload batches, employee folder auto-enrollment, authentication/roles, Alembic migrations, cancellation/retry, and robust multi-worker job leasing are not yet implemented.
- The polling worker currently handles one job at a time and must be stress-tested before very large corpora. Begin with a small representative sample, monitor disk space, and verify backups.
- The frontend is not yet connected to these API endpoints.

Never point this scaffold at the full institutional archive until the remaining features, access controls, backups, throughput and recovery tests are completed.
