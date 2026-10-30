# IIHS Visual Intelligence

A local-first media archive workspace built with React, FastAPI, PostgreSQL and a separate ingestion worker. It supports authenticated catalogue and operator workflows, explicit bounded ingestion previews, automatic local visual indexing, and video transcription with English translation. It does not scan a source on startup or page load.

## Run the local demo

Requirements: Docker with Compose.

```sh
cp .env.example .env
python3.12 -c 'import secrets; print(secrets.token_urlsafe(32))'
# Put a generated value in LOCAL_ML_TOKEN in .env.
python3.12 -c 'import secrets; print(secrets.token_urlsafe(32))'
# Put a second generated value in INITIAL_ADMIN_TOKEN in .env.
```

Start the model bridge first (steps below), then run `docker compose up --build -d`. Open <http://127.0.0.1:5174>, choose **Create admin**, and enter the `INITIAL_ADMIN_TOKEN` to create the first administrator. This one-time setup is disabled after the first account. After setup, anyone who can reach the app can choose **Create account** and sign up as a researcher; administrators can grant other roles in **Admin**. Public sign-up can be disabled with `ALLOW_SELF_REGISTRATION=false`. The CLI bootstrap remains available as an alternative. The initial schema is applied by the `migrate` service. In **Ingestion**, choose **Browse files…** to open the computer’s file picker, select the media to import, and choose **Import & index files**. The app validates and indexes each selected file automatically; no per-image review is required. The computer’s folder paths are not stored. The generated demo image is available under `demo_media/` for a local smoke run.

Before starting Compose, set the same random `LOCAL_ML_TOKEN` in `.env` and start DigUp’s installed local model bridge in a separate terminal:

```sh
cd "/Users/macbookair/Desktop/iihs_Projects/IIHS MEDIA ARCHIVE"
python3.12 -m venv .venv-ml
.venv-ml/bin/python -m pip install -r local-ml-requirements.txt
set -a; source .env; set +a
.venv-ml/bin/uvicorn local_ml_service:app --host 0.0.0.0 --port 8765
```

Install `ffmpeg` on the Mac (`brew install ffmpeg`) if it is not already present. The bridge runs `/Applications/DigUp.app/Contents/Helpers/digup worker` and uses the existing `EmbeddingGemma 2` model files in `~/Library/Application Support/DigUp/Models`; it downloads no embedding weights. Whisper `small` must exist in `~/.cache/whisper/small.pt` and is used for transcription and English translation. Keep the bearer token private and stop the bridge when the app is not in use. Docker Desktop reaches the bridge through `host.docker.internal`; the token is required and macOS firewall rules should restrict access to this local app.

In another terminal, start the app:

```sh
docker compose up --build -d
```

If using the terminal bootstrap instead, run `docker compose exec api python -m app.bootstrap_admin`.

The worker embeds each image and sampled video frame automatically and stores the model revision with the vector. Video audio is transcribed and translated into English when speech is available. Successfully ingested items become searchable immediately; there is no mandatory human review stage. Human annotation remains optional. Choose **Search** in the main menu to search by natural-language meaning or exact text, then refine by media type and added year. OCR extracts printed English text from images during ingestion; transcripts from videos are also searchable. OCR text can be wrong and is never treated as verified names or identity. **Media catalogue** is the browse-only menu.

The API is available through the same-origin web proxy at `/api`; readiness is at <http://127.0.0.1:5174/readyz>. Host ports are configurable with `API_PORT` and `WEB_PORT` in `.env`; defaults are 8010 and 5174 to avoid common local-service conflicts. Stop with `docker compose down`; named database and media volumes remain. `docker compose down -v` removes them.

## Local development checks

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements-dev.txt
DATABASE_URL=sqlite:///./test.db PYTHONPATH=backend python -m pytest backend/tests -q
cd frontend && npm ci && npm run build
cd .. && docker compose config --quiet
```

The backend dependencies include PostgreSQL, Alembic, Pillow and password hashing support. Run migrations with `cd backend && alembic upgrade head` after setting a PostgreSQL `DATABASE_URL`.

## Configuration and safety

- Compose binds published ports to loopback. Keep this stack on a trusted machine and do not expose it directly to a network.
- Self-registration creates active researcher accounts that can browse the catalogue; disable it before exposing a restricted archive to a broader network.
- `DEMO_MODE=true` is the default. In demo mode, only `DEMO_MEDIA_ROOT` is eligible for an explicitly requested preview. In restricted mode, `APPROVED_SOURCE_ROOTS` must list absolute, operator-approved roots and `COOKIE_SECURE=true` must be set behind HTTPS.
- `FACE_FEATURE_ENABLED=true` is rejected. Image and sampled-frame vectors power semantic search; printed English image text is extracted by Tesseract and indexed alongside video transcripts. The app does not infer person identities or generate descriptive visual tags. The bridge reports whether local models are reachable.
- Upload defaults allow up to 500 files per job, 100 MiB per file, and 1 GiB per batch. Adjust `MAX_FILES_PER_JOB`, `MAX_FILE_BYTES`, and `MAX_IMPORT_BYTES` together with the reverse-proxy body limit. Failed imports retain only retryable staged files; successful imports clear their staging copies.
- Configure a strong `POSTGRES_PASSWORD` and a durable protected storage volume before any non-demo use. Keep `INITIAL_ADMIN_TOKEN` private and remove it from `.env` after the first administrator is created. Do not put credentials or real archive paths in source control.

This is a local demo/staging deliverable, not a production pilot sign-off. Production use still needs institutional approval, an approved source scope and operator, security review, backup/restore verification, retention policy and operational ownership. Face workflows stay disabled.

See [the requirement matrix](docs/00_REQUIREMENT_TO_IMPLEMENTATION_MATRIX.md), [design system](docs/10_UI_UX_DESIGN_SYSTEM.md), [release gates](docs/09_TESTING_AND_RELEASE_GATES.md), and [takeover status](docs/ai_takeover_current_status.md).
