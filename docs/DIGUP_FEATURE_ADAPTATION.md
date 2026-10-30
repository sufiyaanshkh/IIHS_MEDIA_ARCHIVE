# DigUp feature adaptation plan for IIHS Media Archive

This branch adapts DigUp's local-first semantic retrieval architecture to the existing IIHS FastAPI/PostgreSQL/pgvector system. The frontend source and styling are intentionally excluded from the change set.

## Source project and license
- Reference: https://github.com/ARahim3/DigUp
- DigUp is MIT-licensed. Its MIT copyright and permission notice must be retained for any copied or adapted source files.
- This branch adapts concepts and service contracts; it does not transplant DigUp's Swift/Tauri desktop application or UI.

## What is already present in IIHS
- EmbeddingGemma-2 text/image embedding bridge, 768-D model/version checks, pgvector HNSW index.
- Image embeddings, sampled video-frame embeddings, OCR text embeddings, Whisper video transcription/translation embeddings.
- Keyword mode and semantic mode search routes, filters and cursor pagination.
- Authenticated roles, audit events, approved source roots, duplicate checks and job worker.

## DigUp features to carry over
1. **One shared multimodal vector space** — query text, photos, PDF page images, audio windows and video frames are comparable where supported by the model. Preserve model revision/runtime/dtype metadata; never mix incompatible vector sources.
2. **Segment-level indexing** — keep page number, chunk number, timestamp start/end and modality for each searchable unit. A result should locate the exact PDF page or video/audio moment, not merely the parent file.
3. **Hybrid retrieval** — combine semantic nearest-neighbour rank with exact-term/FTS matches for names, filenames, identifiers, dates, camera terms and OCR/transcript words. Avoid treating raw cosine similarity as a calibrated confidence.
4. **Long-document incremental passes** — index an initial bounded set of PDF pages/document chunks first, then finish the rest in resumable batches. Keep per-segment provenance.
5. **Overlapping audio windows** — use 30-second windows with 25-second hop and timestamp offsets; index transcript segments separately from audio embeddings.
6. **Video keyframes and soundtrack** — frame sampling should be shot-aware or de-duplicated, timestamped; audio should use the same audio-window index path.
7. **Format extraction** — PDF page text and rendered page image for scans; DOCX/ODT/RTF/HTML; XLSX/ODS/PPTX/ODP text including sheet/slide names and notes; image EXIF; common audio/video formats.
8. **Incremental source reconciliation** — track size/mtime and preferably inode/device; unchanged items are skipped, changed items re-indexed, moved items retain embeddings, removed items are reconciled only after a successful complete crawl.
9. **Index safety** — skip secrets, symlinks, hidden/cache/vendor folders and cloud placeholders; do not index employee faces or infer identity. Respect approved IIHS roots.
10. **Evaluation** — keep a representative multilingual/IIHS dataset and report Recall@K, MRR, exact-match retrieval, per-modality quality and latency before changing ranking thresholds.

## IIHS-specific adaptation
- Preserve IIHS tags/keyword vocabulary and its human review status; AI suggestions remain suggestions.
- Do not enable employee face recognition. Existing governance gate remains.
- Keep the existing API authentication/authorization and audit event model.
- Preserve original media and source provenance; derivatives and indexes are rebuildable.
- No frontend/React/CSS changes are permitted by this implementation task.

## Current implementation boundary
This is a feature plan, not a claim that the complete DigUp engine has already been ported. DigUp's core is Swift and uses llama.cpp with Apple Metal / Vulkan runtime choices; it cannot be imported directly into the Python worker. The IIHS bridge currently launches a DigUp executable from a hard-coded macOS app path, which is not a portable deployment contract. A reproducible IIHS-local worker needs a pinned runtime/model revision and explicit text/image/audio APIs. DigUp's source worker demonstrates the JSON-lines protocol and batching/error-isolation approach.

## Phased implementation
- Phase A: remove the fixed DigUp app path dependency; define a portable local worker contract and deterministic model/runtime health.
- Phase B: add durable segment records and segment-level embeddings with migration.
- Phase C: ingest PDFs and common documents by page/chunk; add OCR/page-image embeddings.
- Phase D: ingest audio and video with overlapping audio windows, transcription segments and timestamped frames.
- Phase E: implement hybrid vector + exact-term retrieval with result locations and explainable rank features.
- Phase F: implement safe incremental folder reconciliation and background scheduling.
- Phase G: add eval set and CI regression gates before production rollout.

## Important compatibility note
EmbeddingGemma-2 is a released model; pin the exact Hugging Face revision, runtime build and quantization. Vectors created by a different model revision or preprocessing recipe must be re-embedded rather than silently combined.
