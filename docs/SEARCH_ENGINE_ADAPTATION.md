# IIHS Media Archive – Multimodal Hybrid Search & Segment Architecture

## 1. Architecture Overview

The IIHS Media Archive adapts the segment-level multimodal indexing principles from DigUp into the FastAPI + PostgreSQL + `pgvector` backend while enforcing IIHS institutional governance:

1. **Controlled IIHS Vocabulary Enrichment (`backend/app/services/contextual_metadata.py`)**:
   - Uses `backend/app/data/iihs_keywords.json` as the authoritative institutional taxonomy across Projects, Events, Schools, Programmes, Labs, and Campuses.
   - Normalizes spaces, underscores `_`, hyphens `-`, apostrophes, and camelCase, and resolves official institutional abbreviations (`UFP`, `UPP`, `DBL`, `SES`/`SOES`, `SSI`/`SOSI`, `SHD`/`SOHD`, `SED`/`SOED`, `SoG`, `Word Labs`, `Media Labs`, `City Campus`, `Kengeri Campus`).
   - Propagates ancestor folder context to child files during recursive crawling (`backend/app/services/crawler.py`) and computes a deterministic `related_group_key` linking sibling files from the same folder, date, and project.
   - **Strict Provenance Rule**: Folder-derived and filename-derived context is explicitly marked with `is_visual_claim: False` and is never presented as visual object detection or geographic proof.

2. **Persistent Segment-Level Indexing (`search_segments` Table)**:
   - Migration `0006_persistent_search_segments_and_incremental_ingestion.py` introduces the `search_segments` table with a 768-D `pgvector` HNSW cosine index (`ix_search_segments_embedding_hnsw`) and a PostgreSQL Full-Text Search GIN index (`ix_search_segments_fts`).
   - Every indexed asset produces granular `SearchSegment` records with `segment_type`, `modality`, `segment_ordinal`, `page_number`, `start_ms`, `end_ms`, `frame_id`, `original_file_reference`, and `content_hash`:
     - `image_visual`: Full-image visual embedding (`is_visual_claim: True`).
     - `visual_description`: Unverified AI visual scene/object description (`is_visual_claim: True`).
     - `ocr_text`: Image OCR text extracted via Tesseract.
     - `video_frame`: Sampled keyframes with perceptual 64-bit dHash deduplication (`compute_frame_dhash`) and millisecond timestamp offsets (`start_ms`, `end_ms`, `frame_id`).
     - `audio_transcript_window`: 30-second sliding windows (`25s` hop) over Whisper transcripts and English translations with `start_ms` and `end_ms`.
     - `pdf_page`: Page-by-page PDF chunks preserving exact 1-based `page_number`.
     - `spreadsheet_sheet`: Sheet-by-sheet `XLSX` and `ODS` extraction preserving worksheet names and tabular row structure.
     - `presentation_slide`: Slide-by-slide `PPTX` (including speaker notes) and `ODP` extraction preserving slide numbers (`page_number`).
     - `office_chunk`: Bounded overlapping chunks (`1200` chars, `180` overlap) for `DOCX`, `ODT`, `RTF`, `HTML`, `TXT`, and `MD`.
     - `contextual_metadata`: Folder/filename/IIHS vocabulary context segment (`is_visual_claim: False`).

3. **Reciprocal Rank Fusion (RRF) Hybrid Search (`backend/app/services/hybrid_search.py`)**:
   - `execute_hybrid_search` fuses four distinct ranking signals using Reciprocal Rank Fusion ($K = 60$) scaled by match quality:
     1. **Primary Content Semantic Channel**: Cosine similarity against visual (`image_visual`, `video_frame`), document (`pdf_page`, `spreadsheet_sheet`, `presentation_slide`, `office_chunk`), OCR (`ocr_text`), and transcript (`audio_transcript_window`) segment vectors.
     2. **Lexical Content Channel**: Phrase and token coverage across persistent `SearchSegment` text, OCR, transcripts, and accepted/suggested human/AI annotations.
     3. **Contextual & Institutional Vocabulary Channel**: Exact phrase, token, IIHS controlled keyword/alias, and temporal year matches across folder hierarchies and filenames.
     4. **Contextual Semantic Channel**: Bounded-weight cosine similarity against `contextual_metadata` embeddings so relevant folder context surfaces assets when filenames are generic (`IMG_001.jpg`) without overpowering direct visual or document matches.
   - Each returned asset includes:
     - `search_score` and `semantic_score`
     - `matched_evidence` (listing the top contributing segments, modalities, excerpts, and whether each is a visual claim)
     - `source_path` and `project_context` (`inferred_project`, `folder_hierarchy`, `matched_keywords`, `matched_categories`, `extracted_years`, `related_group_key`, `is_visual_claim: False`)
     - Location pointers: `matched_page`, `matched_frame_id`, `matched_timestamp_ms`, `matched_timestamp_start_ms`, and `matched_timestamp_end_ms`.
