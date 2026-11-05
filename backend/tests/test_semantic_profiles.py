from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Annotation,
    AssetEmbedding,
    DetectedArchiveFace,
    FaceCandidateMatch,
    MediaAsset,
    PersonProfile,
    SearchSegment,
)
from app.services import semantic_profiles


def _asset(db):
    asset = MediaAsset(
        id="asset-1",
        original_filename="campus-event.jpg",
        media_type="image",
        detected_mime="image/jpeg",
        size_bytes=100,
        sha256="a" * 64,
        storage_key="aa/campus-event.jpg",
        metadata_json={
            "visual_analysis": {
                "description": "People gathered around a speaker outdoors.",
                "object_tags": [{"label": "microphone"}],
                "keyword_suggestions": [{"label": "public event"}],
            },
            "context": {"matched_keywords": [{"keyword": "Campus Life"}]},
        },
    )
    db.add(asset)
    db.flush()
    return asset


def test_semantic_profile_contains_keywords_tags_and_only_verified_people(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        asset = _asset(db)
        db.add(Annotation(asset_id=asset.id, label="podium", source="human", status="accepted"))
        verified = PersonProfile(display_name="Asha Rao", normalized_name="asha rao", status="active")
        unverified = PersonProfile(display_name="Ravi Kumar", normalized_name="ravi kumar", status="active")
        db.add_all([verified, unverified])
        db.flush()

        for index, person in enumerate((verified, unverified), start=1):
            face = DetectedArchiveFace(
                id=f"face-{index}",
                asset_id=asset.id,
                media_type="image",
                face_index=0,
                det_score=0.95,
                model_pack="buffalo_l",
                embedding_dim=512,
                encrypted_embedding="encrypted-test-value",
            )
            db.add(face)
            db.flush()
            db.add(FaceCandidateMatch(
                detected_face_id=face.id,
                asset_id=asset.id,
                person_id=person.id,
                aggregate_similarity=0.9,
                max_similarity=0.92,
                mean_top_similarity=0.9,
                matched_reference_count=1,
                threshold_used=0.45,
                high_confidence=True,
                status="accepted" if person is verified else "unverified",
            ))
        db.flush()

        profile_text, metadata = semantic_profiles.build_semantic_profile(db, asset)
        assert "Asha Rao" in profile_text
        assert "Ravi Kumar" not in profile_text
        assert "microphone" in profile_text
        assert "public event" in profile_text
        assert "Campus Life" in profile_text
        assert "podium" in profile_text
        assert metadata["contains_verified_person_identity"] is True
        assert metadata["sources"]["human_verified_tags"] is True

        monkeypatch.setattr(
            semantic_profiles,
            "text_embeddings",
            lambda prompts: [([1.0] + [0.0] * 767, "google/embeddinggemma-2", "test-revision")],
        )
        assert semantic_profiles.refresh_asset_semantic_profile(db, asset) is True
        db.flush()
        segment = db.scalar(select(SearchSegment).where(
            SearchSegment.asset_id == asset.id,
            SearchSegment.segment_ordinal == semantic_profiles.PROFILE_ORDINAL,
        ))
        assert segment is not None
        assert segment.embedding[0] == 1.0
        assert segment.metadata_json["contains_verified_person_identity"] is True
        assert db.scalar(select(AssetEmbedding).where(
            AssetEmbedding.asset_id == asset.id,
            AssetEmbedding.modality == "visual_description",
        )) is not None

        # Rejecting the match must remove that person's name from the next profile.
        accepted_match = db.scalar(select(FaceCandidateMatch).where(FaceCandidateMatch.status == "accepted"))
        accepted_match.status = "rejected"
        semantic_profiles.refresh_asset_semantic_profile(db, asset)
        db.flush()
        updated = db.scalar(select(SearchSegment).where(
            SearchSegment.asset_id == asset.id,
            SearchSegment.segment_ordinal == semantic_profiles.PROFILE_ORDINAL,
        ))
        assert "Asha Rao" not in updated.text_content
