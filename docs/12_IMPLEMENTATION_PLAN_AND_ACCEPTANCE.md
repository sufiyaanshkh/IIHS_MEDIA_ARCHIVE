# 12 — Implementation Plan and Acceptance

## Phase 0 — Baseline audit
- Confirm current branch and commit.
- Inventory frontend routes, backend routers, models, migrations, environment variables, containers, and tests.
- Run baseline checks.
- Mark each requirement as implemented, partial, missing, or unverified.
- Identify existing public endpoints/fallback URLs and insecure defaults.

**Exit:** an evidence-based implementation matrix exists.

## Phase 1 — Local demo foundation
- Verify reproducible local setup.
- Add environment examples and safe configuration checks.
- Isolate demo database/storage.
- Ensure app startup cannot scan real source roots.
- Add safe health/readiness checks.

**Exit:** a developer can run a demo workflow from documented instructions.

## Phase 2 — Design system and UI
- Centralize design tokens.
- Improve application shell and navigation.
- Implement consistent loading, empty, error, and success states.
- Improve media grid, review, search, and job views.
- Verify responsive and reduced-motion behaviour.

**Exit:** all existing routes have a consistent, usable experience and no core workflow has been removed.

## Phase 3 — API and data integrity
- Reconcile schemas and migrations.
- Validate API contracts.
- Add constraints, idempotency, and permission checks.
- Ensure errors do not leak internals.
- Add audit events for sensitive actions.

**Exit:** API contract and database integrity tests pass.

## Phase 4 — Durable ingestion jobs
- Implement dry-run proposal.
- Configure approved roots through an administrator workflow.
- Add durable job state, checkpointing, retry, cancellation, and per-file outcomes.
- Add safe media validation and derivative handling.
- Keep original media read-only.

**Exit:** restart, duplicate, malformed-file, and disk-error scenarios behave as specified.

## Phase 5 — AI tagging and semantic search
- Record model/index versions and licenses.
- Build a repeatable evaluation set.
- Separate suggestions from confirmed annotations.
- Add reviewer feedback flow.
- Benchmark current embedding baseline and index strategy.

**Exit:** quality and performance are measured; model output is never silently promoted to truth.

## Phase 6 — Video processing
- Add FFprobe metadata extraction.
- Add bounded frame sampling and timestamp links.
- Add local transcript support only if approved.
- Test malformed and resource-intensive videos.

**Exit:** video jobs are restart-safe and preserve timestamp provenance.

## Phase 7 — Face enrollment (separate gate)
- Complete privacy/legal/governance review.
- Confirm model and weight license.
- Add restricted enrollment and review.
- Test ambiguous and poor-quality cases.
- Add retention and deletion controls.

**Exit:** feature remains disabled unless all prerequisites are signed off.

## Phase 8 — Security and operations
- Apply security controls in document 08.
- Complete threat-driven tests.
- Run dependency and secrets scans.
- Review network exposure and container permissions.
- Test backup restoration.
- Create operator runbook and incident response contacts.

**Exit:** security and operations owners sign off before real archive ingestion.

## Phase 9 — Pilot and production approval
- Product owner approves UI and workflows.
- Administrator configures one approved source.
- Run dry-run and compare counts.
- Ingest a small pilot batch.
- Review metadata, tags, duplicates, errors, and resource use.
- Increase scale only after written approval.

## Definition of done

A feature is complete only when:
- Code exists and is integrated.
- Validation and authorization are enforced server-side.
- Loading, empty, success, and failure states exist.
- Relevant automated tests pass.
- Documentation matches actual behaviour.
- Logs and errors are safe.
- Recovery behaviour is understood.
- Any limitations are documented.
- A reviewer can reproduce the result from the recorded commit.

“Build succeeded” is not equivalent to “feature verified.”
