# Coding Agent Handoff — Read Before Changing Code

You are implementing IIHS Visual Intelligence in the existing repository. These documents define the target, but they are not evidence that a requirement is already implemented.

## Required approach
1. Inspect the current code, branch, migrations, tests, Docker Compose, and environment configuration.
2. Build a requirement-to-code matrix with status: verified / partial / missing / blocked.
3. Preserve existing working behaviour and API contracts unless a documented change is needed.
4. Work in small, testable changes. Do not attempt a full rewrite in one pass.
5. Use demo-only media and data until the product owner approves real archive access.
6. Do not configure production source roots or start ingestion automatically.
7. Enforce security in the backend, not just in the UI.
8. Treat media parsers, model files, filenames, EXIF, OCR, transcripts, and model outputs as untrusted.
9. Do not claim completion without running checks and reporting exact results.
10. Update docs when behaviour or contracts change.

## Before implementation
- Identify current commands from `package.json`, backend dependency/config files, and Compose.
- Run the baseline build/tests if possible and record pre-existing failures.
- Inspect the API base URL and remove any unintended public fallback for local deployment.
- Verify all secrets are ignored and not embedded in frontend bundles.
- Confirm the actual route list and database models.

## For every task
- State the intended scope.
- Change the smallest coherent set of files.
- Add/update tests.
- Run relevant checks.
- Review the diff for unrelated changes and secrets.
- Report changed files, commands run, results, and known gaps.

## Never
- Invent backend success responses.
- Add fake production metrics.
- allow arbitrary filesystem paths from browser input.
- run a destructive reset without verifying the environment.
- silently confirm face identities.
- ship pretrained weights without license review.
- expose database/admin tools to unapproved networks.
- say “secure” or “production-ready” without evidence.
