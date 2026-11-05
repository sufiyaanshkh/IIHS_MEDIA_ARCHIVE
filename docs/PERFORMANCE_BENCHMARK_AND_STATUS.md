# IIHS Media Archive – Performance Benchmark Report & Implementation Status

## 1. Workstation Hardware & Test Environment

- **OS**: Windows 10/11 (win32, Python 3.13.5, Docker Desktop 29.8.2, Compose v5.5.1)
- **GPU**: NVIDIA GeForce RTX 4070 (12,282 MB VRAM, Driver 610.88, CUDA available)
- **Database**: PostgreSQL 16 + `pgvector` (HNSW vector index + GIN FTS index)

---

## 2. Representative Mixed-Media Benchmark Results

A representative mixed-media benchmark (`backend/tests/test_remaining_backend.py`) was executed across all 4 modalities (images, video with keyframe extraction + dHash deduplication + transcript windows, audio with timestamped transcript windows, multi-page PDF, and multi-row XLSX spreadsheet) plus all 11 supported document formats (`PDF`, `DOCX`, `ODT`, `RTF`, `HTML`, `TXT`, `MD`, `XLSX`, `ODS`, `PPTX`, `ODP`):

| Metric | Measured Result |
|---|---|
| **Document Extraction (All 11 Formats)** | `0.18s` total (`~61 files/sec`) |
| **Recursive Crawler + IIHS Vocabulary Enrichment** | `0.03s` for multi-folder hierarchy (`~100+ files/sec`) |
| **Multimodal Ingestion Pipeline (5 Mixed-Media Assets -> 16 Segments)** | `0.42s` (`~11.9 files/sec` with portable 768-D embeddings, dHash frame deduplication, and SQLite/PostgreSQL persistence) |
| **Incremental Re-Scan (4 Unchanged + 1 Renamed/Moved + 1 Duplicate)** | `0.06s` (`0` visual re-embeddings triggered for unchanged/moved/duplicate files) |
| **Hybrid RRF Search Latency (11 Institutional Queries)** | `< 15 ms` per query across semantic + lexical + IIHS vocabulary channels |

### Statement on 3–4 TB Workload Testing
- **Has the full 3–4 TB external HDD workload been executed in this session?**
  - **No.** The full 3–4 TB physical external HDD dataset was not attached to this environment during automated testing.
  - All 3–4 TB architectural requirements—read-only source referencing (`COPY_SOURCE_ORIGINALS=false`), streaming 1 MB SHA-256 hashing, incremental `SourceFileRecord` fast-skip by `(size_bytes, mtime_ns)`, move/rename detection without re-embedding, perceptual video frame dHash deduplication, bounded batch embedding, real-time throughput/ETA telemetry, and resumable crash recovery—have been implemented and verified on synthetic mixed-media archives.

---

## 3. Completed Implementation Checklist vs. Remaining Limitations

### Completed Items
- [x] **Frontend Preserved 100%**: Zero files modified under `frontend/`.
- [x] **Intelligent Recursive Archive Crawler (`backend/app/services/crawler.py`)**: Propagates parent folder context, groups sibling files (`related_group_key`), skips Windows system folders (`$RECYCLE.BIN`, `System Volume Information`), symlinks, and NTFS junctions/reparse points (`0x0400`), and supports `resume_after_key`.
- [x] **IIHS Controlled Vocabulary & Aliases (`backend/app/services/contextual_metadata.py`)**: Full phrase/alias matching across underscores, hyphens, camelCase, and abbreviations (`UFP`, `UPP`, `DBL`, `SES`, `SSI`, `SHD`, `SED`, `SoG`, `Word Labs`, `Media Labs`, `City Campus`, `Kengeri Campus`) with `is_visual_claim: False`.
- [x] **Persistent Segment-Level Indexing (`search_segments` + Migration `0006`)**: Stores page numbers, timestamps (`start_ms`, `end_ms`), frame references, model IDs/revisions, content hashes, pgvector HNSW index, and Postgres FTS GIN index.
- [x] **All 4 Modalities & 11 Document Formats**: End-to-end ingestion and segment indexing for Images, Videos, Audio (`.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.aac`), and Documents (`.pdf`, `.docx`, `.odt`, `.rtf`, `.html`, `.txt`, `.md`, `.xlsx`, `.ods`, `.pptx`, `.odp`).
- [x] **Reciprocal Rank Fusion (RRF) Hybrid Search (`backend/app/services/hybrid_search.py`)**: Combines content vectors, lexical segment hits, filename/path matches, IIHS vocabulary matches, and temporal cues with explainable `matched_evidence`.
- [x] **Incremental Sync (`source_file_records`)**: Skips unchanged files, updates moved/renamed files without re-embedding, preserves multiple source paths for identical content hashes, and reconciles deleted source files only on complete non-truncated scans.
- [x] **Portable Windows ML Runtime (`local_ml_service.py`) & PowerShell Scripts**: Removed macOS `/Applications/DigUp.app` dependency; added CUDA/CPU detection, VRAM reporting, 4000-char text chunk support, and `check_environment.ps1`, `setup_windows.ps1`, `start_windows.ps1`, `stop_windows.ps1`.

### Remaining Operational Limitations / Notes for Production Cutover
1. **Whisper Model Weights Cache**: To enable live audio/video speech-to-text transcription offline on the Windows workstation, ensure `%USERPROFILE%\.cache\whisper\small.pt` is present on the host. When absent, audio/video assets still ingest and index via keyframes and contextual metadata while marking `transcription.status = "unavailable"`.
2. **External HDD Volume Mount**: When connecting the physical 3–4 TB HDD on Windows, map its drive letter as a read-only volume (`:ro`) in `docker-compose.yml` and list the container mount path in `APPROVED_SOURCE_ROOTS` in `.env`.

