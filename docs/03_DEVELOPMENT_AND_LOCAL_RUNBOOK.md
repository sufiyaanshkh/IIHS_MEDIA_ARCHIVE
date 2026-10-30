# 03 — Development and Local Runbook

## 1. Important note

These instructions describe the intended local workflow. Exact commands, service names, environment variable names, and ports must be checked against the current repository's `README`, `docker-compose.yml`, backend configuration, and frontend scripts before being treated as authoritative. Do not invent missing environment variables; document them from code.

## 2. Prerequisites

- Git.
- Docker Engine and Docker Compose plugin.
- A supported Node.js LTS version matching the repository lockfile/tooling.
- Python version matching backend dependency files and container image.
- Sufficient free disk space for database, model cache, derivatives, and test fixtures.
- Optional NVIDIA drivers/container runtime only if GPU inference is enabled and validated.
- Access to the repository and approved model downloads, if setup requires them.

## 3. First-time setup

1. Clone the repository and checkout the intended branch.
2. Read `README.md`, `.env.example` if present, Docker Compose configuration, backend dependency files, frontend `package.json`, and migration instructions.
3. Copy the example environment file to a local ignored `.env` file if the repository provides one.
4. Generate unique local development secrets. Never reuse example passwords in any shared environment.
5. Verify storage and model-cache paths. Use an empty demo-only storage directory.
6. Confirm no real archive root is configured.
7. Start dependencies using the repository's documented Compose command.
8. Apply database migrations using the repository's migration tool, if configured.
9. Start frontend and backend using documented scripts or containers.
10. Open the app and verify the health endpoint and demo workflows.

## 4. Required configuration behaviour

Configuration must distinguish development, test, and production. At minimum, define and document:
- Application environment.
- API base URL.
- Database connection.
- Media storage root or S3-compatible endpoint and credentials.
- Model cache root.
- Allowed ingestion roots.
- Demo mode flag.
- Authentication/session secrets.
- CORS origins.
- Logging level.
- Job concurrency and resource limits.
- Optional GPU settings.
- Optional semantic search/index settings.

Use the actual names from the codebase in the final `.env.example`. Never commit secrets. Avoid permissive defaults such as wildcard CORS, default admin credentials, or unrestricted filesystem roots.

## 5. Startup checks

Before declaring the app ready:
- Database is reachable and migrations are current.
- API health endpoint returns a healthy response without leaking secrets.
- Worker is running and can claim a demo job.
- Storage is writable by the correct service account.
- Model cache is readable and model availability is reported accurately.
- Frontend points to the intended local API, not an unrelated public endpoint.
- Demo mode is enabled and production roots are absent.
- Logs contain no secrets.

## 6. Developer checks

Run the actual commands configured in the project:
- Frontend lint.
- Frontend TypeScript check.
- Frontend production build.
- Backend lint/type checks if configured.
- Backend unit/integration tests.
- Migration consistency check.
- Compose config validation.
- Security/dependency checks.
- End-to-end smoke test if browser tooling is available.

Record command and result. Do not state a check passed if it was not run.

## 7. Resetting demo data

Provide a documented, explicit demo reset command or script. It must:
- Refuse to run unless environment is clearly marked development/test.
- Target only the demo database/schema and demo storage root.
- Print the target environment before deleting anything.
- Require confirmation for destructive actions.
- Never infer the target from a default path that could overlap production.

## 8. Troubleshooting checklist

### Frontend cannot reach API
Check API base URL, browser network errors, CORS, proxy settings, and whether a production fallback URL is still hardcoded.

### Database connection fails
Check container health, port conflicts, credentials, database host from the relevant network namespace, and migration status.

### Worker jobs remain queued
Check worker process status, queue/database connectivity, lease expiry, job errors, and concurrency configuration.

### Media thumbnails fail
Check storage keys, permissions, derivative generation logs, URL authorization, and whether the original is available.

### Model load fails
Check model path, required files, library versions, available RAM/VRAM, device selection, and licensing approval.

### Disk usage rises unexpectedly
Check model caches, logs, derivatives, temporary files, database growth, and retention rules. Never delete source media to free space without an approved retention action.

## 9. Handover requirement

A maintainer must be able to clone the repository, configure a demo environment, start the application, run a sample workflow, stop it, and restart it without losing job state. The final repo README must contain verified commands for this exact repository.
