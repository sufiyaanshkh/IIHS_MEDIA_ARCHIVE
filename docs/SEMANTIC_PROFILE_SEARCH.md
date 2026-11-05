# Semantic profiles for person and keyword search

IIHS Media Archive keeps the original image and sampled-video-frame embeddings unchanged. In addition, each asset can have one derived text profile embedded with the configured EmbeddingGemma 2 text model.

The profile combines:
- AI-generated visual descriptions, object suggestions and IIHS keyword suggestions (explicitly labelled unverified);
- IIHS vocabulary associated with the archive context;
- accepted/corrected human annotations;
- person display names only when an InsightFace candidate match has been explicitly accepted by an authorised reviewer and the person enrollment remains active.

Unverified face candidates are never written into the profile. Rejected matches are removed from the profile. Renaming or revoking a person refreshes affected assets. The original 512-D InsightFace biometric vectors remain separate and are never mixed into EmbeddingGemma vectors.

The profile is stored as a `visual_description` SearchSegment at reserved ordinal `9000`, with source flags in `metadata_json`. The original image/frame embeddings and other text segments remain intact. Search therefore gains an extra semantic signal without replacing direct visual retrieval or changing the database schema.

## Existing archive backfill

Newly ingested assets build profiles automatically. To create profiles for assets already in the archive, run from the backend directory after deploying the code and ensuring the configured local embedding bridge is reachable:

```bash
python rebuild_semantic_profiles.py
```

The script processes one asset per transaction, preserves original image/frame vectors, and reports failures. It is safe to re-run.
