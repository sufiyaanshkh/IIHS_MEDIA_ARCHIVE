# 11 — Operations, Backup, and Recovery

## 1. Operational ownership

Assign named owners for:
- Application and dependency updates.
- Host OS and Docker updates.
- Database administration.
- Storage capacity and integrity.
- Model approval and evaluation.
- Security incident handling.
- Backup review and restoration testing.
- User and role administration.

Do not leave these responsibilities implicit.

## 2. Monitoring

Monitor and alert on:
- API availability and error rate.
- Worker heartbeat and oldest queued job.
- Job failure/retry rate.
- Database connectivity, disk, connections, and migration state.
- Media storage free space and write errors.
- Model load failures and inference latency.
- CPU/RAM/VRAM pressure.
- Thumbnail and index failures.
- Repeated authentication failures.
- Backup success and age.
- Log volume and disk usage.

Health endpoints should reveal only the minimum information required to the caller. Detailed health and configuration are for authorized operators.

## 3. Backups

Back up the components needed to restore the application consistently:
- PostgreSQL data.
- Original media and required derivatives, according to policy.
- Configuration templates and migration version.
- Model/index version metadata.
- Audit records.
- Any local object-store metadata needed to restore files.

Store secrets separately and restore them through an approved process. Encrypt backups where appropriate and restrict access.

A database backup without the referenced media may not be a complete archive recovery. A media backup without the catalogue may also be incomplete. Document consistency and restore order.

## 4. Restore tests

At a scheduled interval:
1. Restore into an isolated environment.
2. Apply or verify migrations.
3. Verify catalogue-to-media references.
4. Verify thumbnails and a sample of original media.
5. Verify job records and audit events.
6. Run a search smoke test.
7. Record restore duration and discrepancies.
8. Update the runbook.

Set RPO (acceptable data loss) and RTO (acceptable recovery time) with institutional stakeholders.

## 5. Upgrade procedure

- Review release notes and dependency changes.
- Back up before schema changes.
- Test migrations on a copy of staging data.
- Verify compatibility between API, worker, schema, and model indexes.
- Stop or drain jobs safely if required.
- Deploy the application and worker versions in a controlled order.
- Run health and smoke tests.
- Keep a documented rollback or forward-fix plan.

Do not roll back application code across an incompatible database migration without an explicit plan.

## 6. Incident handling

If unauthorized access, data loss, credential exposure, or suspicious job activity is suspected:
- Restrict access or isolate the affected service.
- Preserve logs and evidence.
- Revoke exposed credentials and sessions.
- Determine what data and operations may be affected.
- Restore from known-good backups if necessary.
- Patch and verify the root cause.
- Notify designated stakeholders under institutional policy.
- Document corrective actions and tests.

## 7. Storage capacity

Before ingestion, measure actual archive size and file counts. Do not treat the approximate 50 TB estimate as verified. Plan separately for originals, thumbnails, sampled video frames, embeddings/indexes, database growth, temporary processing files, and backup capacity.

Define warning and stop thresholds. Stop ingestion safely before the filesystem becomes full.
