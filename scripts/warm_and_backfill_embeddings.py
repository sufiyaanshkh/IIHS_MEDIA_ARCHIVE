"""Pre-warm object and IIHS keyword label embeddings, backfill any unindexed
assets and 5-second video frames, and re-queue any ingestion jobs that failed
while the ML bridge was offline.
"""
import uuid
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AssetEmbedding, IngestionJob, JobCandidate, JobState, MediaAsset, VideoFrame
from app.services.embeddings import image_embedding, model_health
from app.services.insights import OBJECT_LABELS, _label_vectors, keyword_vocabulary
from app.services.media import MediaValidationError, video_frame


def main():
    health = model_health()
    print("ML bridge health:", health)
    if health.get("status") != "ready":
        raise SystemExit("Local ML bridge is not ready.")

    print("Warming object label vectors...")
    obj_vecs = _label_vectors("objects", [f"a photo of a {label}" for label in OBJECT_LABELS])
    print(f"Cached {len(obj_vecs)} object vectors.")

    keywords, excluded = keyword_vocabulary()
    print(f"Warming {len(keywords)} IIHS keyword vectors (excluded {excluded} place names)...")
    kw_vecs = _label_vectors(
        "iihs-keywords",
        [f"{item['keyword']}: {item['notes']}"[:500] if item["notes"] else item["keyword"] for item in keywords],
    )
    print(f"Cached {len(kw_vecs)} IIHS keyword vectors.")

    root = settings.storage_root.resolve()
    interval_ms = settings.video_frame_interval_seconds * 1000
    max_frames = settings.video_frame_max

    with SessionLocal() as db:
        assets = db.scalars(select(MediaAsset)).all()
        backfilled = 0
        for asset in assets:
            if asset.media_type == "image":
                existing = db.scalar(
                    select(AssetEmbedding.id).where(
                        AssetEmbedding.asset_id == asset.id,
                        AssetEmbedding.modality == "image",
                    )
                )
                if not existing:
                    target = root / (asset.thumbnail_key or asset.storage_key)
                    if target.is_file():
                        vec, mod_id, mod_rev = image_embedding(target)
                        db.add(AssetEmbedding(asset_id=asset.id, embedding=vec, modality="image", model_id=mod_id, model_revision=mod_rev))
                        db.commit()
                        backfilled += 1
            else:
                original = root / asset.storage_key
                duration = asset.duration_ms or 0
                existing_frames = {
                    f.timestamp_ms: f
                    for f in db.scalars(select(VideoFrame).where(VideoFrame.asset_id == asset.id)).all()
                }
                timestamps = [step * interval_ms for step in range(max_frames) if step * interval_ms < duration]
                for ts in timestamps:
                    frame = existing_frames.get(ts)
                    if frame is None and original.is_file():
                        key = f"{asset.sha256[:2]}/{asset.sha256}-frame-{ts}.webp"
                        try:
                            video_frame(original, ts, key)
                            frame = VideoFrame(id=str(uuid.uuid4()), asset_id=asset.id, timestamp_ms=ts, storage_key=key)
                            db.add(frame)
                            db.flush()
                            existing_frames[ts] = frame
                        except MediaValidationError:
                            db.rollback()
                            continue
                    if frame is not None:
                        has_emb = db.scalar(
                            select(AssetEmbedding.id).where(
                                AssetEmbedding.asset_id == asset.id,
                                AssetEmbedding.frame_id == frame.id,
                            )
                        )
                        if not has_emb:
                            target = root / frame.storage_key
                            if target.is_file():
                                vec, mod_id, mod_rev = image_embedding(target)
                                db.add(
                                    AssetEmbedding(
                                        asset_id=asset.id,
                                        frame_id=frame.id,
                                        embedding=vec,
                                        modality="image_frame",
                                        model_id=mod_id,
                                        model_revision=mod_rev,
                                    )
                                )
                                db.commit()
                                backfilled += 1
        print(f"Backfilled {backfilled} image/video-frame embeddings across {len(assets)} total assets.")

        requeued = 0
        failed_jobs = db.scalars(
            select(IngestionJob).where(IngestionJob.state.in_([JobState.failed.value, JobState.partially_completed.value]))
        ).all()
        for job in failed_jobs:
            failed_candidates = db.scalars(
                select(JobCandidate).where(JobCandidate.job_id == job.id, JobCandidate.state == "failed")
            ).all()
            if failed_candidates:
                for cand in failed_candidates:
                    cand.state = "ready"
                    cand.message = None
                job.state = JobState.queued.value
                job.error_summary = None
                requeued += 1
        db.commit()
        print(f"Re-queued {requeued} previously failed ingestion jobs.")


if __name__ == "__main__":
    main()
