# IIHS Hybrid Semantic Search — implementation contract

This design follows the retrieval principles in [DigUp](https://github.com/ARahim3/DigUp) (MIT), adapted to IIHS storage, Postgres/pgvector and role-protected API.

## Segment representation
Every searchable unit is a segment with: asset ID, modality, segment kind, stable ordinal, optional page number or timestamp start/end, optional text excerpt, vector model ID and revision, and content checksum. Typical segments:
- image: image vector; separate OCR text segment if present
- PDF: page text vector when prose exists; rendered page image vector for scans/slides
- office/doc files: semantically-sized text chunks with heading/sheet/slide provenance
- audio: audio embedding windows (30 seconds, 25-second hop) and transcript text segments with exact time ranges
- video: visually distinct frames with timestamp, plus soundtrack audio windows and transcript
- filename/path: exact-term-only fields; do not waste semantic vectors on opaque identifiers

## Embedding task prompts
- Query: `task: search result | query: <user query>`
- Text segment: `title: <file or segment title> | text: <segment text>`
- Code retrieval is not in default IIHS archive scope; source repositories must remain separate from cultural media.

The runtime must report model ID, immutable model revision, runtime version, precision/quantization, modality support and vector dimension. Index rows may be searched only against the identical model ID/revision/preprocessing fingerprint. Changing those values requires an explicit re-embedding migration.

## Hybrid ranking
For each query:
1. Build query text embedding.
2. Find nearest segment vectors in pgvector, constrained to the exact model revision and requested media filters.
3. Run exact token / phrase lookup over filenames, paths, accepted annotations, OCR, extracted page/chunk text and transcripts.
4. Normalize vector scores against the query's returned candidate distribution; keep raw cosine as a feature, not a confidence.
5. Fuse semantic evidence and exact-word coverage. For invoice IDs, asset IDs, exact names and number-heavy queries, weight lexical evidence more heavily.
6. Return the best segment for each asset: matched page, chunk, video frame/time or audio time range, excerpt, rank features and matched modality.
7. Apply IIHS year, collection, media type, source and review-status filters before pagination.

## Incremental indexing
Reconciliation is scoped only to explicitly approved roots. Compare path, size, modified time and, where supported, device/inode identity. Skip unchanged items; reprocess changed files; update paths for moved files without discarding their embeddings; remove missing rows only after a successful full crawl of the root (never after a truncated crawl or an unreadable/ejected root). Preserve SHA256 deduplication and audit events.

## IIHS-specific rules
- Keep media originals immutable; only derivatives and index rows are rebuilt.
- Keep public UI unchanged. All interaction stays behind existing API contracts.
- AI keyword/object suggestions are unverified until human review.
- Preserve keyword vocabulary filtering that excludes personal names/place names when the IIHS policy requires it.
- Do not enable face recognition or identify people; the existing consent/governance gate remains closed.
- Never index hidden credential folders, secret files, cloud placeholders, symlinks, generated caches or vendored directories.
- For scans, preserve both visual page embedding and OCR text if OCR quality is sufficient.
- Keep transcripts and translations linked to timestamps and source language; don't treat translation as verbatim source text.

## Phased delivery gates
A: Portable, pinned EmbeddingGemma runtime and parity tests for text/image embeddings.
B: Segment table + migration, preserving existing AssetEmbedding rows.
C: PDF and document readers, page/chunk indexing and restartable long-document continuation.
D: audio frame/window embeddings and timestamped transcript segments.
E: hybrid ranker with exact lookup fusion and location-aware search results.
F: incremental source reconciler with safe deletion rules.
G: evaluation suite (IIHS test queries, multilingual, Recall@5/10, MRR, exact-number lookup, video time-to-hit, p50/p95 latency) and deployment acceptance gates.

Do not ship a feature as ready until its corresponding tests and local inference checks pass.