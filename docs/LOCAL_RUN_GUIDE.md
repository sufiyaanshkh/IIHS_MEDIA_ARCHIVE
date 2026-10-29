# Local full-stack run guide

This guide starts the **current development scaffold** on a local machine. It does not imply that every planned AI model is integrated.

## 1. Prerequisites

- Docker Desktop / Docker Engine with Compose
- Node.js 20+ and npm
- Git
- Enough free disk space for your small test corpus, PostgreSQL volume, thumbnails, and any model weights you add

## 2. Get the code

```bash
git clone https://github.com/sufiyaanshkh/IIHS_MEDIA_ARCHIVE.git
cd IIHS_MEDIA_ARCHIVE
git checkout feature/local-backend-pipeline
cp backend/.env.example backend/.env
mkdir -p approved-archive data/people data/storage data/derivatives models
```

Put a **small, non-sensitive test set** into `approved-archive/` first. The archive mount is read-only in the containers. Do not point this scaffold at the estimated institutional archive until throughput, backup, recovery, permissions, and model quality have been validated.

## 3. Start API, worker and PostgreSQL

```bash
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs -f api worker
```

The compose file binds the API to `127.0.0.1:8000` and PostgreSQL to `127.0.0.1:5433`. The worker polls queued ingestion jobs.

Health check:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/sources
```

Open [FastAPI docs](http://127.0.0.1:8000/docs) after the API is running.

## 4. Start the frontend

In a second terminal at the repository root:

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The frontend uses the local API at `http://127.0.0.1:8000`.

## 5. Ingest a test folder

1. Confirm `/api/v1/sources` returns the approved `/archive` source.
2. Open **Ingest** and choose **Select source**.
3. Select the approved source number shown by the API.
4. Inspect the dry-run counts before continuing.
5. Choose **Start ingestion** to queue the job.
6. Check progress at [`/api/v1/ingest/jobs`](http://127.0.0.1:8000/api/v1/ingest/jobs) and in `docker compose logs -f worker`.
7. Return to **Archive** and refresh if needed. Ingested assets are indexed immediately; review is optional.
8. Open an asset and use **Download original**, **Add a tag**, or **Send to review**. The **Review queue** should show only assets with status `review_requested`.

The browser cannot scan arbitrary folders on your Mac directly. The current UI intentionally submits configured server-side archive roots; the source must be mounted into Docker and listed in the approved roots configuration.

## 6. Current model/task availability

| Capability | Current state |
|---|---|
| PostgreSQL asset, tag, and ingest-job records | Implemented in the scaffold |
| Hash-based duplicate skipping | Implemented in the ingestion worker |
| Image metadata and thumbnail generation | Implemented for supported images |
| Basic PDF text extraction | Implemented |
| FFprobe video metadata | Implemented when FFprobe is available in the container |
| Lexical search | Implemented |
| Download original / add manual tag / request review | API actions are present; verify on your machine |
| Review queue | Optional status and listing endpoint; full approve/reject audit workflow remains incomplete |
| Excel keyword-sheet mapping | Not yet connected to the UI/API; locate and confirm the actual workbook and its columns first |
| Official IIHS logo asset | Brand colours are aligned; logo file/permission still needs confirmation before embedding |
| EmbeddingGemma 2 vector embeddings and semantic retrieval | Not end-to-end; disabled by default and needs model/runtime/vector-index integration |
| Qwen vision captioning/tagging, YOLO detection, OCR, sampled video frames/transcription | Not wired end-to-end in this repository |
| Employee face recognition/verified asset associations | Not enabled; employee profiles are only a basic registry |
| Authentication, roles, production hardening, robust multi-worker scaling | Not complete |

Do not enable a model merely by placing arbitrary weights in `models/`. Install its runtime, verify the model's license and input/output contract, wire inference into the worker, persist outputs and provenance, and run representative evaluation tests first.

## 7. Verify and shut down

```bash
npm run build
docker compose logs --tail=200 api worker
docker compose down
```

Do **not** use `docker compose down -v` unless you intentionally want to delete the PostgreSQL volume. The build and end-to-end tests must be run on your machine; they have not been run as part of these GitHub edits.
