# IIHS Visual Intelligence — Documentation Pack

**Purpose:** A practical handover pack for engineering, implementation, security review, testing, and controlled deployment of IIHS Visual Intelligence.

**Status:** This pack defines the target implementation and operating rules. It is not proof that every described feature is already implemented. The current repository must be audited against each requirement and the implementation status recorded honestly.

## Start here

1. `docs/01_PRODUCT_SCOPE_AND_REQUIREMENTS.md` — what the product must do and what is deliberately out of scope.
2. `docs/02_SYSTEM_ARCHITECTURE.md` — services, boundaries, and data flow.
3. `docs/03_DEVELOPMENT_AND_LOCAL_RUNBOOK.md` — local setup and repeatable commands.
4. `docs/04_DATA_MODEL_AND_RETENTION.md` — entities, provenance, lifecycle, and retention.
5. `docs/05_API_CONTRACT_GUIDE.md` — API conventions and contract expectations.
6. `docs/06_INGESTION_AND_MEDIA_PIPELINE.md` — safe image/video ingestion and job processing.
7. `docs/07_AI_TAGGING_SEARCH_AND_EVALUATION.md` — model use, confidence, human review, and evaluation.
8. `docs/08_SECURITY_THREAT_MODEL.md` — threats, mitigations, and security gates.
9. `docs/09_TESTING_AND_RELEASE_GATES.md` — tests and acceptance criteria.
10. `docs/10_UI_UX_DESIGN_SYSTEM.md` — product UI rules and design tokens.
11. `docs/11_OPERATIONS_BACKUP_AND_RECOVERY.md` — deployment, monitoring, backup, recovery, and incident handling.
12. `docs/12_IMPLEMENTATION_PLAN_AND_ACCEPTANCE.md` — phased work plan and definition of done.
13. `docs/13_DECISIONS_AND_OPEN_QUESTIONS.md` — decisions that are set, assumptions, and decisions that require an owner.

## Project context and important boundaries

The known project stack is React + TypeScript + Vite, FastAPI, PostgreSQL, Docker Compose, local media/model-cache storage, and local AI processing. The repository has existing image upload, image browsing/review, object/tagging-related flows, and semantic search work. These statements are a baseline to verify against the checked-out commit before implementation.

The archive estimate of approximately 50 TB is unconfirmed and must be treated as a planning estimate, not a measured inventory.

The real archive and face-enrollment images will be supplied only after the application has been reviewed and approved. Therefore:

- Development and acceptance testing must use synthetic records and approved sample media.
- Real archive source roots must not be configured by default.
- Opening the app must never start a scan or ingestion.
- Production ingestion must require explicit authorization and a reviewed scope.
- Demo and production data must be separated.
- Do not report a feature as complete until code, tests, and runtime behaviour have been verified.

## Suggested use by role

- **Product owner / institutional stakeholder:** 01, 10, 12, 13.
- **Frontend engineer:** 01, 05, 10, 12.
- **Backend / platform engineer:** 02, 03, 04, 05, 06, 11.
- **AI engineer:** 06, 07, 09, 12.
- **Security reviewer:** 08, 09, 11.
- **Operations / IT:** 03, 08, 11, 12.

## Source of truth

When these documents disagree with actual code, first record the mismatch. Do not silently assume either side is correct. The repository, migrations, tests, and deployed configuration must be inspected and reconciled. Keep this pack versioned with the code.

## Security notice

No software can honestly be promised to be “unhackable.” The objective is to reduce attack surface, apply defence in depth, test controls, monitor failures, and define a response process. A security review and an authorized deployment checklist are required before real institutional data is loaded.
