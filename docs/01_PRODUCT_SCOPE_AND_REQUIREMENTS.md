# 01 — Product Scope and Requirements

## 1. Product purpose

IIHS Visual Intelligence is a local-first institutional media archive tool for organizing and retrieving photographs and videos through metadata, object tags, semantic search, and carefully governed face-related workflows. It should help staff discover and review media without requiring them to know the original folder structure.

## 2. User roles

The exact identity provider and final role model must be confirmed before production. At minimum, design for these permission groups:

- **Administrator:** manages users/roles, approved source roots, system configuration, retention, and operational settings.
- **Archivist / reviewer:** searches and reviews media, edits metadata, accepts or rejects AI suggestions, and may initiate approved ingestion if granted permission.
- **Read-only researcher:** searches and views permitted records and media; cannot change tags or run ingestion.
- **Service operator:** monitors jobs and system health, with no unnecessary access to media content.
- **Development/demo user:** uses isolated synthetic data and cannot reach production source roots.

Do not rely on hiding UI buttons as authorization. The backend must enforce every permission.

## 3. Functional requirements

### FR-01 — Authentication and authorization
- Require authentication for all non-public application routes and API operations.
- Enforce server-side role and object-level permissions.
- Use secure session/cookie or token handling appropriate to the existing architecture.
- Provide logout, session expiry, and administrator-controlled access revocation.
- Audit administrative and high-impact actions.

### FR-02 — Media catalogue
- List and filter media by supported metadata.
- Display thumbnails without downloading full-resolution media unnecessarily.
- Support image and video records; show unsupported or unavailable media clearly.
- Preserve stable media identifiers independently of filenames and paths.
- Show source, ingest state, metadata provenance, and processing status.

### FR-03 — Ingestion control
- Support a dry-run / preview phase before a job is committed.
- Restrict filesystem scanning to administrator-configured roots.
- Default to demo mode and no real source roots.
- Require explicit confirmation of source, scope, and operation before production ingestion.
- Make long-running jobs observable, resumable where safe, cancellable, and auditable.
- Do not depend on a browser having direct access to arbitrary server filesystem paths.

### FR-04 — AI suggestions and human review
- Store machine suggestions separately from human-confirmed annotations.
- Preserve model/version, processing time, confidence where meaningful, and provenance.
- Permit reviewers to accept, reject, edit, or defer suggestions.
- Never silently treat an uncertain identity match as confirmed.
- Retain the reviewer and timestamp for confirmation decisions.

### FR-05 — Search
- Support keyword/metadata search and semantic search where configured.
- Return only records the authenticated user may access.
- Provide filters, sorting, pagination, clear empty states, and actionable errors.
- Preserve a search request's model/index version for debugging when practical.

### FR-06 — Video
- Extract metadata using a local tool such as FFprobe.
- Store duration, dimensions, codec/container details, and other approved metadata.
- Sample frames according to configurable rules; do not process every frame by default.
- Associate sampled frames and any timestamped transcript segments with the parent video.
- Keep video processing local unless a formal architecture change is approved.

### FR-07 — Face enrollment and matching (restricted feature)
- Face enrollment must be an explicitly authorized workflow.
- Folder names may be used as proposed display names only when the enrollment operator confirms that convention.
- File names are not identity evidence.
- Validate image quality and number of detected faces.
- Send ambiguous cases to review.
- Store face embeddings and references as sensitive biometric-related data with restricted access and retention.
- Verify the model and pretrained-weight license before institutional use.
- Do not deploy pretrained weights until license and governance review are complete.

### FR-08 — Operations
- Show job status, progress when measurable, errors, retry/cancel options where supported, and recent activity.
- Record health and storage availability.
- Keep logs free of credentials, tokens, raw biometric embeddings, and unnecessary personal data.

## 4. Non-functional requirements

- **Reliability:** restart-safe job execution, explicit failure states, idempotent operations, and documented recovery.
- **Security:** least privilege, validation, secure secrets, access controls, audit trail, backups, and dependency updates.
- **Privacy:** local processing, restricted access, minimal retention, and controlled deletion.
- **Performance:** paginated APIs, thumbnail delivery, bounded job concurrency, and no loading of the entire archive into frontend state.
- **Maintainability:** typed interfaces, migrations, tests, structured logs, and clear separation of concerns.
- **Accessibility:** keyboard support, visible focus, semantic markup, contrast, and reduced-motion support.
- **Usability:** explicit user feedback, useful errors, and a coherent design system.

## 5. Deliberate exclusions for initial approval build

Until approved and separately implemented, do not assume the system includes:
- Full 50 TB archive ingestion.
- Production face enrollment.
- Training custom models from reviewer decisions.
- Automatic deletion or rewriting of source media.
- Cloud AI inference.
- Public internet access.
- Claims of complete detection or perfect identity recognition.
- Automated mass-acceptance of AI tags.

## 6. Product approval sequence

1. Build the app with synthetic records and approved sample media.
2. Test core workflows and security boundaries.
3. Let the product owner inspect the UI and functionality.
4. Fix issues and obtain explicit approval.
5. Configure one approved source root.
6. Run a small, reviewed pilot batch.
7. Compare results against expectations and audit failures.
8. Expand ingestion gradually with monitoring and rollback/recovery procedures.
