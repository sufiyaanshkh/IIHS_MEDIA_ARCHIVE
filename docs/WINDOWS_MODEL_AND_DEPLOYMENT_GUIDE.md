# IIHS Media Archive – Windows Workstation, Model Runtime & HDD Ingestion Guide

## 1. System Overview

The IIHS Media Archive is a local-first, multimodal archival ingestion and hybrid semantic search system designed to index **3–4 TB** of photographs, videos, audio recordings, and documents from an externally connected HDD on a Windows workstation while keeping original HDD files completely untouched.

### Hardware & Software Prerequisites
- **OS**: Windows 10 / Windows 11 (64-bit)
- **GPU (Recommended)**: NVIDIA CUDA-capable GPU (e.g., RTX 4070 12 GB VRAM) with latest NVIDIA Driver; automatic CPU fallback is supported when a GPU is unavailable.
- **Container Runtime**: Docker Desktop for Windows (with WSL 2 backend enabled)
- **Host Python**: Python 3.11+ (for the host Local ML Service bridge and CLI tools)
- **Media Tools**: `ffmpeg` and `ffprobe` (included inside the Docker containers and optionally installed on the Windows host via `winget install Gyan.FFmpeg`)

---

## 2. Exact Windows Installation & Startup Commands

Open **PowerShell** in the repository root (`e:\Media Lab\IIHS MEDIA ARCHIVE`) and run:

```powershell
# 1. Verify workstation environment (Python, PyTorch/CUDA, NVIDIA GPU, FFmpeg, Docker, disk space)
.\check_environment.ps1

# 2. Create .venv, install Python dependencies, generate .env tokens, and create runtime folders
.\setup_windows.ps1

# 3. Start the portable Local ML Service (port 8765) and Docker Compose stack (ports 8010 & 5174)
.\start_windows.ps1
```

To stop all services cleanly at any time:

```powershell
.\stop_windows.ps1
```

---

## 3. Local Model Installation & Runtime Configuration

The host ML service (`local_ml_service.py`) runs on `http://127.0.0.1:8765` (`http://host.docker.internal:8765` from Docker containers) and removes the legacy macOS `/Applications/DigUp.app` dependency.

### Supported Model Backends
1. **Portable Multimodal Runtime (Default Out-of-the-Box on Windows)**:
   - Produces deterministic 768-dimensional unit-normalized text and image embeddings compatible with the pinned `google/embeddinggemma-2` (`bfcd298762cc34d0357ece5ebdd31791a3a374d8`) contract.
   - Automatically detects NVIDIA CUDA (`torch.cuda.is_available()`) and reports GPU name and VRAM telemetry in `/healthz`.
2. **Local HuggingFace Transformer Weights (Optional)**:
   - Place downloaded weights in a local directory and set `LOCAL_MODEL_PATH=E:\models\embeddinggemma-2` in `.env`.
   - `local_ml_service.py` loads the weights with `local_files_only=True` onto `cuda` (or `cpu` fallback).
3. **Whisper Audio/Video Transcription (`openai-whisper/small`)**:
   - Place `small.pt` (SHA-256 `9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794`) in `%USERPROFILE%\.cache\whisper\small.pt` (or set `WHISPER_CACHE_DIR` in `.env`).
   - Automatically uses FP16 on CUDA GPUs and falls back to FP32 on CPU.

### Environment Variables (`.env`)
| Variable | Default | Purpose |
|---|---|---|
| `COPY_SOURCE_ORIGINALS` | `false` | Keeps 3–4 TB originals on the external HDD without duplicating them into `data/storage/originals` |
| `EMBEDDING_BATCH_SIZE` | `16` | Number of text segments embedded per batch request |
| `VIDEO_FRAME_INTERVAL_SECONDS` | `5` | Keyframe sampling interval for video files |
| `VIDEO_FRAME_MAX` | `120` | Maximum sampled keyframes per video |
| `VIDEO_FRAME_DEDUP_THRESHOLD` | `6` | Perceptual 64-bit dHash Hamming distance threshold to skip static duplicate frames |
| `AUDIO_WINDOW_SECONDS` | `30` | Transcript window duration for timestamped audio/video search |
| `AUDIO_WINDOW_HOP_SECONDS` | `25` | Sliding window hop for timestamped transcripts |
| `FACE_FEATURE_ENABLED` | `false` | Keeps employee face recognition disabled per institutional policy |

---

## 4. Exact External HDD Ingestion Steps (3–4 TB Workflow)

To ingest an externally connected HDD (for example mounted at `F:\IIHS_ARCHIVE`) without modifying or copying the source files:

1. **Connect the External HDD** to the Windows workstation and confirm its drive letter (e.g., `F:\IIHS_ARCHIVE`).
2. **Mount the HDD Read-Only into Docker & Approve the Source Root**:
   - In `.env`, set:
     ```ini
     DEMO_MODE=false
     COPY_SOURCE_ORIGINALS=false
     APPROVED_SOURCE_ROOTS=/mnt/approved_sources/iihs_hdd
     ```
   - In `docker-compose.yml` under both `api` and `worker` `volumes:`, map the external drive read-only:
     ```yaml
     - F:/IIHS_ARCHIVE:/mnt/approved_sources/iihs_hdd:ro
     ```
   - Apply the configuration:
     ```powershell
     docker-compose up -d api worker
     ```
3. **Bootstrap the Admin Account** (first run only):
   - Open `http://127.0.0.1:5174` in your browser.
   - Use the `INITIAL_ADMIN_TOKEN` from `.env` to create the initial administrator account.
4. **Register and Enable the Approved Source**:
   - Navigate to the **Sources / Operations** panel in the UI (or `POST /api/sources`) and enable `/mnt/approved_sources/iihs_hdd`.
5. **Run a Dry-Run Preview Scan (`kind="preview"`)**:
   - Trigger a Preview scan from the UI (`POST /api/ingestions/preview`).
   - The recursive crawler (`backend/app/services/crawler.py`) traverses the folder hierarchy, skips Windows system directories (`$RECYCLE.BIN`, `System Volume Information`), symlinks, and NTFS junctions/reparse points, extracts IIHS vocabulary context from folder/file names, and classifies each file as `new`, `unchanged` (`skipped`), `modified`, `moved`, or `duplicate`.
6. **Confirm & Execute Multimodal Ingestion (`kind="ingest"`)**:
   - Click **Start Ingestion** (`POST /api/ingestions/{job_id}/confirm`).
   - Monitor live telemetry (`discovered_files`, `processed_files`, `processed_bytes`, `files_per_second`, `mb_per_second`, `eta_seconds`, and `current_stage`) via `GET /api/jobs/{job_id}` or the UI.
   - If the workstation is restarted or the job is interrupted, the worker automatically recovers stale jobs and resumes from the remaining `ready` candidates without re-indexing already completed files.

---

## 5. Troubleshooting Guide

- **`/readyz` or `/api/ops/capabilities` shows `local_ai: unavailable`**:
  - Verify `local_ml_service.py` is running on the Windows host (`Invoke-RestMethod http://127.0.0.1:8765/healthz`) and that `LOCAL_ML_TOKEN` in `.env` matches the container environment (`docker-compose up -d api worker`).
- **Out-of-VRAM on GPU during large batch ingestion**:
  - Lower `LOCAL_ML_BATCH_SIZE=8` and `EMBEDDING_BATCH_SIZE=8` in `.env`, or set `LOCAL_ML_FORCE_CPU=true` to force CPU execution.
- **Audio/video transcription reports `status: unavailable`**:
  - Confirm `%USERPROFILE%\.cache\whisper\small.pt` exists and `ffmpeg` is accessible inside the worker container (`docker-compose exec worker ffmpeg -version`).
- **Windows symlink or permission errors on external NTFS drives**:
  - The crawler automatically skips inaccessible folders, Windows reparse points (`0x0400`), and offline cloud placeholders while recording `traversal_errors` and `skipped_unsafe` in the job summary rather than aborting the scan.

