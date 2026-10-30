# AI Takeover — IIHS Visual Intelligence

**Updated:** 2026-10-29 (Asia/Kolkata)  
**Project:** `/Users/macbookair/Desktop/iihs_Projects/IIHS MEDIA ARCHIVE`  
**Status:** Implemented as a local demo/staging candidate; do not call it production-ready.

## Read first

1. `docs/PACK_README.md`
2. `CODING_AGENT_HANDOFF.md`
3. `docs/00_REQUIREMENT_TO_IMPLEMENTATION_MATRIX.md`
4. The relevant design, architecture, security, ingestion, and release-gate documents.

## User decisions

- Ingested images should be automatically processed and searchable without a mandatory human review step.
- Use the installed local EmbeddingGemma model and install/use local speech transcription and translation; translate to **English only**.
- Follow `Tagging Tool Design Guideline.html` palette and layout principles. User also named DigUp as UI/UX inspiration.
- Keep a handoff document if work needs to continue in another agent.

## Implemented in this revision

- **Unrestricted import size (2026-10-29):** removed default caps on file count (`MAX_FILES_PER_JOB=0`), per-file size (`MAX_FILE_BYTES=0`), and total import payload size (`MAX_IMPORT_BYTES=0`) across `backend/app/config.py`, `backend/app/api/jobs.py`, `backend/app/services/media.py`, `backend/app/worker.py`, `docker-compose.yml`, and `.env*`. Set `client_max_body_size 0` and `proxy_request_buffering off` in `frontend/nginx.conf`, parsed multipart uploads with `request.form(max_files=sys.maxsize, max_fields=sys.maxsize)`, removed image pixel/video duration/ML payload caps, and widened `size_bytes` to 64-bit `bigint` (`backend/migrations/versions/0005_bigint_media_sizes.py`). Only the disk free-space safety threshold (`MIN_STORAGE_FREE_BYTES`) remains.
- **Media flip card (2026-10-29):** clicking a catalogue/search result opens a pop-up flip card (front: media; back: metadata). The back shows source provenance (original filename, where it was loaded from, importer, job, checksum), camera EXIF (GPS never displayed), AI-ranked objects, an "Employees detected" section (disabled — face matching remains governance-blocked), and the top relevant IIHS keywords. The old "Text & AI" tab was removed from the card. **Send to review** (`POST /api/assets/{id}/review`) adds the AI objects/keywords as `model`/`suggested` annotations; a new **Review** page (admin/archivist) accepts or rejects them. Insights come from `GET /api/assets/{id}/insights`; objects and keywords are zero-shot rankings of the stored EmbeddingGemma image vector against label text vectors (cached under `storage/cache/`), not bounding-box detection. Keyword vocabulary: `backend/app/data/iihs_keywords.json`, rebuilt from the Excel sheet with `python3 scripts/build_keyword_vocabulary.py` (700 kept, 161 city/state/country/place rows excluded). Tests: `backend/tests/test_media_card.py` (not run on this host — missing backend deps).
- Auth screen offers sign-in, open researcher registration, and a one-time setup-key-protected first-admin creation flow; registration is rate-limited and the bootstrap closes after the first user. Later role grants remain admin-only in the Admin page. Password visibility and confirmation are included.
- Auth page uses the institute-name wordmark lockup and the concise “Media Archive” title; the repository contained no official logo image asset.
- Authenticated local React/FastAPI/PostgreSQL application with user/role management, protected media, audit trail, ops, keyword/semantic search, and responsive catalogue.
- Automatic visual embedding for each ingested image and sampled video frame. Semantic search ranks with pgvector cosine distance and filters by immutable model/revision.
- Local macOS bridge uses `/Applications/DigUp.app/Contents/Helpers/digup worker` with the installed `EmbeddingGemma 2` model files from `~/Library/Application Support/DigUp/Models`.
- Video audio extraction plus local cached Whisper `small` transcription and English translation. Text is persisted and indexed as transcript/translation chunks. Transcription failure does not prevent visual search.
- Long video transcription updates the ingestion heartbeat so recovery does not re-queue a healthy long-running job.
- No mandatory post-ingest review gate. Bounded preview and explicit confirmation remain before copying source files.
- UI uses the requested brick/sand/white palette, Open Sans, flat square surfaces, frame rails, restrained mosaic bands, and reduced-motion behavior.
- README and the requirement matrix describe the current automatic AI workflow and setup.

No extra model download is needed on this Mac: the DigUp EmbeddingGemma GGUF pair and Whisper `small.pt` cache were verified at the locations discussed with the user. The local bridge was manually exercised with synthetic text, image, and speech; no archive content was used.

## Checks

- Frontend `npm run build` — passed in the target project after integration.
- Backend pytest — blocked at collection because this host Python lacks the required `pgvector` package.
- `docker compose config --quiet` — passed in staging.
- Python AST parse — passed for 23 app, migration, service, and test files.
- Backend pytest — **not run to completion**: local host Python is missing the required `pgvector` package, and network access blocked installing it. Docker dependency installation has not been checked.
- Docker image build, PostgreSQL migration, full Compose startup and user-flow smoke test — not checked because the Docker daemon is unavailable.
- Local bridge synthetic inference — passed for DigUp text/image embeddings and Whisper transcription/English translation.

## Remaining gaps and handoff

1. With Docker available, run `docker compose up --build -d`, bootstrap an administrator, and smoke-test login, ingestion preview/confirmation, automatic indexing, semantic search, transcript/translation search, and process restart.
2. Run backend tests in the project's intended dependency environment, then exercise vector migration/search against PostgreSQL + pgvector.
3. Evaluate semantic relevance with an approved dataset and inspect long-video resource use/heartbeat recovery.
4. Restrict host bridge access with the macOS firewall to trusted local clients; Compose reaches it through `host.docker.internal` and the bearer token.
5. Keep demo media synthetic, use no real archive roots until written approval and operational/security/backup gates are signed off, and keep face processing disabled.

The user-facing live development preview is served from the target project's `frontend/` directory at `http://127.0.0.1:5174/` to avoid the already-open preview on port 5173.
