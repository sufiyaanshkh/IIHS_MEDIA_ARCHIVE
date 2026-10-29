# IIHS Media Archive

A local-first media archive that brings together the strongest ideas from **DigUp** (on-device semantic search with Google EmbeddingGemma 2) and **IIHS Visual Intelligence** (visual tagging, human review, and dataset workflows).

## Product direction

- Beautiful, editorial archive UI designed for research teams and institutional collections.
- Search by meaning with locally generated EmbeddingGemma 2 vectors; keep keyword search available when embeddings are unavailable.
- Ingest selected files, folders, or administrator-approved server mounts through a safe, auditable pipeline.
- Preserve originals and provenance; generate metadata, thumbnails, AI suggestions, and review tasks.
- Keep human review and dataset export as first-class workflows.
- Keep processing local-first. No cloud AI API is required for the target architecture.

## Status

This repository started empty. The first delivery establishes the frontend foundation and interaction prototype using clearly labelled sample data. It does **not** yet claim to provide a production-connected backend, real filesystem scanning, actual embeddings, or live archive data. These require integration and testing against approved storage and model assets.

## Run the UI

Requirements: Node.js 20+ and npm.

```bash
npm install
npm run dev
```

For a production build:

```bash
npm run build
npm run preview
```

## Reference projects

- [DigUp](https://github.com/ARahim3/DigUp) — local-first semantic search over files with EmbeddingGemma 2.
- [IIHS Visual Intelligence](https://github.com/sufiyaanshkh/IIHS-Visual-Intelligence) — visual tagging, human-in-the-loop review, and dataset workflows.

This project is an integration target inspired by their capabilities; it does not copy either project wholesale. Respect each project's license and model-weight terms when integrating implementation or assets.

## Planned architecture

```text
React UI
  ├─ Archive browse / filters / item detail
  ├─ Semantic search UI
  ├─ Ingestion wizard + dry-run review
  └─ Human review + dataset workspace
          │ HTTPS / typed API
          ▼
FastAPI API + authentication
  ├─ Ingestion jobs (durable queue and persisted progress)
  ├─ Media validation, metadata and thumbnail workers
  ├─ Local tagging / OCR / sampled video frames
  ├─ Local EmbeddingGemma 2 embedding worker
  └─ Review and dataset APIs
          │
          ├─ Media storage (originals + derived assets)
          ├─ PostgreSQL (assets, tags, jobs, users, audit trail)
          └─ Vector index (embedding storage and similarity search)
```

## Important ingestion boundary

A web browser cannot silently enumerate every drive on a user's computer. Browser selection is appropriate for selected files and supported folder pickers. Whole-drive scanning must be performed by a trusted local agent, or by a server worker against explicitly configured and permission-checked mount roots. The UI must never accept arbitrary filesystem paths from untrusted clients.

## License

No license has been added yet. Set a project license before redistributing this repository.
