"""One-time backfill for searchable IIHS semantic profiles.

Run from the backend directory after deploying the feature:
    python rebuild_semantic_profiles.py

The local embedding bridge must be reachable from this process. Existing
original image/frame vectors are preserved.
"""
import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import MediaAsset
from app.services.semantic_profiles import refresh_asset_semantic_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("iihs.semantic-profile-backfill")


def main():
    processed = 0
    failed = 0
    with SessionLocal() as db:
        asset_ids = db.scalars(select(MediaAsset.id).order_by(MediaAsset.created_at, MediaAsset.id)).all()
    for asset_id in asset_ids:
        with SessionLocal() as db:
            asset = db.get(MediaAsset, asset_id)
            if asset is None:
                continue
            try:
                refresh_asset_semantic_profile(db, asset)
                db.commit()
                processed += 1
            except Exception:
                db.rollback()
                failed += 1
                log.exception("Could not rebuild semantic profile for %s", asset_id)
            if processed % 25 == 0:
                log.info("Processed %d/%d profiles (%d failed)", processed, len(asset_ids), failed)
    log.info("Semantic profile backfill finished: %d processed, %d failed", processed, failed)


if __name__ == "__main__":
    main()
