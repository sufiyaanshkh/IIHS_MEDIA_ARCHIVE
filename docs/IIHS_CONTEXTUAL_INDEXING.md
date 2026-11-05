# IIHS contextual metadata indexing

The ingestion worker reads the maintained vocabulary at `backend/app/data/iihs_keywords.json`.
During preview and ingestion it matches complete phrases in the relative folder path and filename.
Matched terms retain their vocabulary category, notes, evidence location and a path-evidence
confidence. This is contextual metadata only: a folder match is not treated as a visual
detection or proof of image contents.

At ingestion, the worker stores the context in the asset metadata and embeds a compact
context document alongside visual/OCR/transcript embeddings. This allows semantic queries
to retrieve files from project names, institutional vocabulary and folder hierarchy even
when filenames are generic (for example, IMG_001.jpg). Existing exact keyword search still
matches the original relative path.

The workbook remains the editable source of truth. Regenerate the JSON vocabulary with:

```powershell
python scripts/build_keyword_vocabulary.py
```

Current limitations: this change adds path/filename vocabulary enrichment to the existing
image/video ingestion path. It does not yet implement persistent page/frame/audio segment
records, document/audio first-class media assets, a full hybrid rank-fusion query, or a
portable Windows model runtime. Do not treat this branch as validated for a 3–4 TB production
ingestion until the remaining deployment work and workstation-scale tests are complete.
