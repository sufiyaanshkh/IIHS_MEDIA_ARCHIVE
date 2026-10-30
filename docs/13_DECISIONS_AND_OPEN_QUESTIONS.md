# 13 — Decisions and Open Questions

## Decisions already established

1. Real archive media will be shared later, after the product owner reviews and approves the application.
2. The first review must use synthetic/demo records and approved sample media.
3. Opening the app must never trigger real scanning or ingestion.
4. Real ingestion requires an explicit, reviewed operation.
5. Media processing and AI inference should remain local to approved infrastructure.
6. The UI should follow the existing IIHS-inspired design guideline: Open Sans; brick `#8C2914`; sand `#E9E6D3`; deep sand `#D8D5C4`; light sand `#F2EFDE`; ink `#222222`; rule grey `#A7A7A7`; white; restrained borders and motion.
7. AI annotations and human-confirmed annotations must remain distinct.
8. Face enrollment must be treated as a sensitive, restricted feature.
9. The approximately 50 TB archive figure is an estimate and must be measured before capacity planning.
10. Existing working routes and API contracts must be preserved unless a change is explicitly planned and tested.

## Must be confirmed before production

### Access and network
- Who may use the app?
- Is institutional SSO available, or is local account management required?
- Which LAN/VPN segments are authorized?
- Is access required from outside the institutional network?
- Who administers the host and database?

### Archive
- Actual source locations and permissions.
- Actual file counts and size distribution.
- Expected ingestion schedule and update strategy.
- Whether originals are copied or referenced.
- Required metadata and metadata restrictions.
- Backup and retention requirements.

### AI
- Exact models and immutable versions.
- Hardware availability and concurrency.
- Accepted tag taxonomy.
- Labeled evaluation dataset and reviewers.
- Search quality expectations.
- Model license approval.
- Whether OCR and speech recognition are allowed for all source material.

### Face feature
- Institutional/legal/privacy approval.
- Permitted purpose and authorized operators.
- Enrollment source and naming convention.
- Model/weight licensing.
- False-match review process.
- Retention, correction, deletion, and audit policy.

### Reliability
- Required availability.
- RPO and RTO.
- Acceptable ingestion throughput and search latency.
- Storage thresholds and alert recipients.
- Maintenance windows and update process.

## Assumptions that must not be treated as facts

- That all described routes/features are already implemented.
- That the archive is exactly 50 TB.
- That a particular model is licensed for unrestricted use.
- That local network access alone is sufficient security.
- That a model confidence score is calibrated.
- That JSONB embeddings will scale adequately.
- That Docker Compose automatically provides secure production deployment.
- That a passing build proves the application is reliable or secure.

## Decision log format

For each decision, record:
- ID and date.
- Question.
- Options considered.
- Chosen option.
- Reason.
- Owner.
- Impact on code, operations, security, and cost.
- Follow-up/review date.
