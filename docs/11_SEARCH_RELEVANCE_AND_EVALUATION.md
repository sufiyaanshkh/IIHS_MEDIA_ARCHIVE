# Search Relevance and Evaluation

## Search channels

The archive search should keep retrieval evidence separate until ranking:

- Visual semantics: EmbeddingGemma 2 query vectors against image and sampled video-frame vectors.
- Text content: OCR, document pages/sheets/slides/chunks, transcripts and translations.
- Exact lexical data: names, filenames, source paths, codes, identifiers and IIHS aliases.
- Human-verified archive annotations: accepted/corrected tags.
- Context metadata: folder and project context, explicitly labelled as context rather than visual evidence.
- Person identity: separate InsightFace channel; only accepted matches qualify as identity matches.

## DigUp-inspired ranking behaviour

- Query-relative semantic normalisation and a semantic gate reject uniformly weak results.
- Lexical coverage rescues exact names, codes and identifiers that general embedding models may not preserve.
- Reciprocal Rank Fusion combines independently ranked candidate lists.
- OCR remains searchable as text, but should not be treated as a visual embedding signal.
- A match must retain source evidence and a page/frame/time location where available.
- Ingestion timestamps must not be confused with the year depicted in the media.

## Visual Intelligence compatibility

Keep image-to-text semantic retrieval as the visual baseline. Object and IIHS keyword suggestions can contribute lexical evidence when they are explicitly indexed as tags/annotations. Keep detector output separate from verified annotations; model-suggested tags must not be presented as human-confirmed facts.

## Query evaluation protocol

Before release, create a fixed query set using representative IIHS material:

1. Natural language scene descriptions.
2. Specific object and material names (including local/Indian vocabulary).
3. Exact people names (only against approved, accepted identity matches).
4. Event/project names, folder names and filename/asset identifiers.
5. OCR-heavy documents, transcripts and video-frame queries.
6. Multilingual and transliterated terms where aliases are available.

For each query, label relevant assets and expected segment/time locations. Report Recall@5, Precision@5, MRR@10 and nDCG@10 for:
- Visual-only retrieval.
- Lexical-only retrieval.
- Current hybrid retrieval.
- Hybrid retrieval with contextual metadata disabled.

Do not change model versions and ranking weights simultaneously. Keep a held-out query set and compare every ranking change against the same baseline. A code-level test does not establish real-archive relevance; release depends on measured results on the IIHS query set.

## Operational notes

- Re-embedding is not required for ranking-only changes.
- When model ID, revision, preprocessing or prompt template changes, rebuild affected vectors and keep the old index available until verification passes.
- OCR, transcript, context and visual channels must be independently inspectable in result evidence.
- Do not infer identity from image embeddings or context metadata.
