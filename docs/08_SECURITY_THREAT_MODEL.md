# 08 — Security Threat Model

## 1. Objective and limits

The objective is to reduce risk through layered controls, test those controls, detect incidents, and recover safely. No application can be guaranteed impossible to compromise. Security status must be evidence-based and reviewed before production.

## 2. Assets to protect

- Original photographs and videos.
- Institutional metadata and archive structure.
- Face enrollment records and embeddings.
- User credentials, sessions, and authorization state.
- PostgreSQL data and backups.
- Model weights and indexes.
- Storage credentials and application secrets.
- Job queue, audit logs, and exports.
- Server filesystem and host operating system.
- Availability of the application and storage.

## 3. Threat actors and abuse cases

- Unauthorized LAN user attempts to access the app.
- Compromised user attempts privilege escalation.
- Malicious or malformed image/video exploits a parser.
- Upload or metadata input attempts path traversal.
- Malicious filename, EXIF, OCR, transcript, or tag triggers injection/XSS.
- A user submits an arbitrary server path to read sensitive files.
- A job is replayed or retried to create duplicates or exhaust resources.
- An attacker steals secrets from a repository, log, image, or environment.
- A vulnerable dependency or model package executes code.
- A worker or container is compromised and accesses unrelated host files.
- Backup media is lost or accessed without authorization.
- An insider exports sensitive data or abuses face matching.
- Network exposure allows brute force, denial of service, or unauthorized database access.

## 4. Required controls

### Identity and authorization
- Require authentication for protected endpoints.
- Use least privilege and deny-by-default permissions.
- Enforce resource-level authorization in backend code.
- Rate-limit authentication and expensive operations.
- Set secure session cookies when cookie sessions are used: `HttpOnly`, `Secure` in HTTPS deployments, and appropriate `SameSite`.
- Protect state-changing cookie-authenticated requests against CSRF.
- Expire sessions and support revocation.
- Require stronger authentication for administrators where feasible.
- Self-registration is researcher-only and rate-limited; disable it before exposing a restricted archive beyond the trusted local host.
- Never rely on frontend route guards as the security boundary.

### Network and deployment
- Keep database, worker, model service, and admin tools off public interfaces.
- Use host firewall rules and private Docker networks.
- Expose only required ports.
- Use TLS for sessions and sensitive data across networks.
- Do not expose Adminer or database ports to the LAN/internet unless specifically required and protected.
- Do not expose development servers in production.
- Keep SSH and host administration restricted to approved operators.
- Remove any unintended public API fallback from the frontend and verify the built configuration.

### Filesystem and ingestion
- Use administrator-configured source roots only.
- Resolve and verify canonical paths and block traversal/symlink escape.
- Mount original media read-only where possible.
- Run parsers as an unprivileged user with restricted filesystem access.
- Never construct shell commands by string concatenation.
- Apply file-size, dimensions, duration, CPU, memory, and time limits.
- Validate detected file types, not just extensions.
- Store uploads outside executable web roots.
- Do not let browser users specify arbitrary host paths.

### Input and output
- Validate all API inputs with typed schemas and explicit bounds.
- Use parameterized database access.
- Escape untrusted text in HTML; sanitize only when rich HTML is intentionally supported.
- Set safe content types and `X-Content-Type-Options: nosniff`.
- Apply a restrictive Content Security Policy appropriate to the actual app.
- Limit request body sizes and upload counts.
- Prevent mass assignment by accepting explicit fields rather than arbitrary model dictionaries.

### Secrets and cryptography
- Keep secrets out of Git, frontend bundles, logs, and documentation examples.
- Use unique secrets per environment and rotate them after suspected exposure.
- Use a managed secret store or tightly permissioned environment files.
- Use supported password hashing if passwords are stored locally; never plaintext or reversible encryption.
- Use TLS and encrypted backups where risk assessment requires it.
- Do not invent a custom cryptographic scheme.

### Dependencies and models
- Pin dependencies and review lockfile changes.
- Run dependency vulnerability scanning and update critical issues.
- Generate a software bill of materials (SBOM) for release where practical.
- Verify model/weight provenance and licenses.
- Disable unnecessary runtime downloads and remote code execution.
- Keep FFmpeg, image libraries, Python packages, Node packages, OS packages, and containers patched.

### Jobs and resource abuse
- Bound concurrent jobs and per-user job creation.
- Add request/job timeouts, retry limits, and backoff.
- Enforce storage quotas and disk-pressure alerts.
- Validate job state transitions server-side.
- Protect cancellation, retry, export, and source configuration with authorization.
- Make operations idempotent where possible.

### Logging and audit
- Log authentication outcomes, permission denials, admin changes, ingestion starts/stops, exports, enrollment actions, and security-relevant failures.
- Do not log passwords, tokens, secret values, full biometric embeddings, or unnecessary image content.
- Restrict audit-log modification.
- Protect logs from unbounded growth and disk exhaustion.
- Keep timestamps consistent and attach correlation IDs.

### Backups and recovery
- Back up database, required metadata, configuration (excluding raw secrets), and approved storage data.
- Encrypt backups where appropriate and restrict access.
- Test restoration periodically.
- Define RPO and RTO with the institution; do not invent these targets.
- Consider how deleted biometric data is handled in backups.

## 5. Security release blockers

Do not load real institutional media if any of the following is true:
- Unauthenticated access to protected data is possible.
- Authorization is only enforced in the frontend.
- Database or admin ports are unintentionally exposed.
- Production secrets are committed or shipped to the browser.
- Arbitrary filesystem paths can be read.
- Ingestion can begin without explicit approval.
- Uploaded files are served unsafely.
- No reliable backup/restore process exists.
- Critical/high vulnerabilities remain without an accepted, documented exception.
- Face model licensing/governance is unresolved while face features are enabled.
- Security tests have not been run and reviewed.

## 6. Minimum security test cases

- Anonymous user cannot access private API or media.
- Read-only role cannot edit annotations or start ingestion.
- User cannot access an asset by guessing another ID.
- User cannot request files outside configured roots using `..`, encoded traversal, or symlinks.
- Uploaded spoofed file types and malformed media are rejected or isolated.
- Oversized requests are rejected.
- XSS-like filenames and OCR text display as text, not executable markup.
- CSRF protections work if cookie sessions are used.
- CORS permits only configured origins.
- Job retry/cancel actions require permission and obey valid state transitions.
- Secrets do not appear in logs or frontend bundles.
- Database is not reachable from unapproved network segments.
- Backup can be restored in a clean environment.

## 7. Incident response

Maintain a short runbook:
1. Identify and contain affected service/network.
2. Preserve logs and relevant evidence.
3. Revoke or rotate exposed credentials.
4. Determine affected data and actions.
5. Restore from known-good state where required.
6. Patch and test the root cause.
7. Notify designated institutional stakeholders according to policy.
8. Document timeline, impact, corrective actions, and follow-up tests.
