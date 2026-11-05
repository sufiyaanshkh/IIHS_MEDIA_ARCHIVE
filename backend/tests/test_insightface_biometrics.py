"""Comprehensive acceptance tests for IIHS Media Archive — Face Recognition Using InsightFace (buffalo_l).

Covers:
1. Model & runtime verification: InsightFace FaceAnalysis(name="buffalo_l") loads 512-D recognition model
   with dynamic output_shape validation and CUDA -> CPU execution provider fallback.
2. Folder-hierarchy profile photo enrollment (IIHS_Profile_Photos/<Person Name>/<photo>.jpg):
   - Folder name used as person display name (never image filename)
   - Multiple reference photos per person
   - Multi-face reference photos flagged for manual review (status="needs_review")
   - Root-level and ambiguous-folder photos placed in unassigned review queue (status="unassigned_review")
   - Failed face detections and low-quality detections recorded
   - Duplicate reference photos detected by SHA-256 without redundant embedding generation
   - Enrollment report counts and provenance verification
3. Administrator review controls:
   - Rename/correct person display name
   - Reassign unassigned photo to a person
   - Confirm person enrollment to activate matching
4. Archive face recognition & matching against a controlled labelled dataset:
   - Single-face and multi-face archive images
   - Aggregated similarity across multiple reference photos per person
   - Unknown/unenrolled individuals rejected
5. Human verification workflow & Hybrid Search integration:
   - Unverified and rejected matches excluded from confirmed search results
   - Accepted matches searchable by person_ids and by person name
   - Co-occurrence search for photographs containing multiple selected enrolled people
   - Combined search: person identity + IIHS keyword + project + year
6. Video timestamp face matching & visually identical frame deduplication via dHash.
7. Biometric governance & complete cascade deletion of biometric records upon enrollment revocation.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database import Base
from app.models import (
    DetectedArchiveFace,
    EnrolledReferencePhoto,
    FaceCandidateMatch,
    FaceReferenceEmbedding,
    FaceVerificationDecision,
    MediaAsset,
    PersonProfile,
    VideoFrame,
)
from app.services.contextual_metadata import infer_path_context
from app.services.face_recognition import (
    BiometricGovernanceError,
    DetectedFaceResult,
    InsightFaceBuffaloEngine,
    REQUIRED_MODEL_PACK,
    confirm_person_enrollment,
    decrypt_face_embedding,
    encrypt_face_embedding,
    process_archive_asset_faces,
    reassign_reference_photo,
    review_face_candidate_match,
    revoke_person_enrollment,
    scan_and_enroll_profile_folder,
    select_execution_providers,
    update_person_profile_name,
)
from app.services.hybrid_search import execute_hybrid_search


def _unit_vec(seed_index: int, noise: float = 0.0, dim: int = 512) -> list[float]:
    """Create a deterministic L2-normalized 512-D unit vector for a synthetic identity."""
    vec = [0.0] * dim
    base_slot = (seed_index * 37) % dim
    second_slot = (seed_index * 37 + 1) % dim
    third_slot = (seed_index * 37 + 2) % dim
    vec[base_slot] = 1.0
    vec[second_slot] = 0.15 + noise
    vec[third_slot] = -0.08 * noise
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _save_test_image(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (240, 240)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color=color)
    # Draw simple deterministic pattern so dHash differs when color differs
    for x in range(0, size[0], 16):
        for y in range(0, size[1], 16):
            if ((x // 16) + (y // 16) + color[0]) % 2 == 0:
                img.putpixel((x, y), (255 - color[0], color[1], 255 - color[2]))
    img.save(path, format="JPEG", quality=95)
    return path


class ControlledBuffaloEngine(InsightFaceBuffaloEngine):
    """Controlled deterministic InsightFace buffalo_l engine wrapper for labelled dataset testing."""

    def __init__(self, manifest: dict[str, list[DetectedFaceResult]]):
        super().__init__(model_pack=REQUIRED_MODEL_PACK, det_size=(640, 640))
        self._manifest = manifest
        self._active_providers = ["CPUExecutionProvider"]
        self._embedding_dim = 512
        self._recognition_model_name = "w600k_r50.onnx"

    def ensure_loaded(self) -> None:
        return None

    def detect_and_embed(self, image_path: Path) -> list[DetectedFaceResult]:
        name = image_path.name
        if name == "corrupted.jpg":
            raise ValueError("Unable to decode corrupted image")
        return list(self._manifest.get(name, []))


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with SessionLocal() as session:
        yield session


def test_real_insightface_buffalo_l_runtime_and_provider_fallback(tmp_path: Path):
    """Verify real InsightFace buffalo_l model loads 512-D recognition model and provider fallback works."""
    # 1. Verify CUDA -> CPU fallback when only CPUExecutionProvider is available
    providers, ctx_id = select_execution_providers(["CPUExecutionProvider"])
    assert providers == ["CPUExecutionProvider"]
    assert ctx_id == -1

    both_providers, cuda_ctx_id = select_execution_providers(["CUDAExecutionProvider", "CPUExecutionProvider"])
    assert both_providers == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert cuda_ctx_id == 0

    # 2. Verify non-buffalo_l model pack is rejected
    with pytest.raises(ValueError, match="buffalo_l"):
        InsightFaceBuffaloEngine(model_pack="Facenet")

    # 3. Load real InsightFace buffalo_l from workspace data/models/insightface
    engine = InsightFaceBuffaloEngine(
        model_pack="buffalo_l",
        det_size=(640, 640),
        root_dir=Path("data/models/insightface"),
    )
    info = engine.runtime_info()
    assert info["status"] == "ready"
    assert info["model_pack"] == "buffalo_l"
    assert info["embedding_dim"] == 512
    assert "CPUExecutionProvider" in info["providers"]

    # Run real inference on a blank synthetic image (verifying detector + runtime pipeline executes cleanly)
    blank_img = _save_test_image(tmp_path / "blank_real_test.jpg", (120, 140, 160))
    detections = engine.detect_and_embed(blank_img)
    assert isinstance(detections, list)


def test_governance_gate_and_embedding_encryption(monkeypatch):
    """Verify biometric processing is blocked by default until authorized, and embeddings are encrypted at rest."""
    import dataclasses
    mock_settings = dataclasses.replace(settings, face_feature_enabled=False, face_institutional_authorization=False)
    monkeypatch.setattr("app.config.settings", mock_settings)
    monkeypatch.setattr("app.services.face_recognition.settings", mock_settings)
    assert mock_settings.face_feature_enabled is False
    assert mock_settings.face_institutional_authorization is False
    with pytest.raises(BiometricGovernanceError):
        from app.services.face_recognition import ensure_biometric_governance_authorized
        ensure_biometric_governance_authorized(allow_isolated_enrollment=False)

    vec = _unit_vec(1, noise=0.02, dim=512)
    token = encrypt_face_embedding(vec)
    assert isinstance(token, str) and token.startswith("v1:")
    restored = decrypt_face_embedding(token)
    assert len(restored) == 512
    assert math.isclose(sum(a * b for a, b in zip(vec, restored)), 1.0, rel_tol=1e-5)


def test_folder_hierarchy_enrollment_admin_review_matching_search_and_deletion(db_session: Session, tmp_path: Path):
    """End-to-end test covering folder-hierarchy enrollment, multi-face & root-file flagging,
    duplicate detection, admin review/rename/reassign/confirm, archive image & video matching,
    human verification, multi-person co-occurrence hybrid search, and complete biometric deletion.
    """
    profile_root = tmp_path / "IIHS_Profile_Photos"

    # Build controlled folder hierarchy matching the specification:
    # IIHS_Profile_Photos/
    #   Mohammed Ahmed/
    #     photo1.jpg          (valid single face #1)
    #     photo2.jpg          (valid single face #1, slight variation)
    #     photo1_dup.jpg      (exact binary duplicate of photo1.jpg)
    #     group_office.jpg    (2 faces -> must be flagged needs_review / multiple_faces_detected)
    #   John Smith/
    #     IMG_9081.jpg        (valid single face #2; filename is IMG_9081.jpg, folder name is John Smith!)
    #     no_face_scenery.jpg (0 faces -> failed_detection)
    #     blurry_low.jpg      (1 face with det_score=0.32 -> low_quality)
    #   Fatima Khan/
    #     image1.png          (valid single face #3)
    #     image2.jpg          (valid single face #3, second angle)
    #   Unknown/
    #     mystery.jpg         (ambiguous folder name -> unassigned_review)
    #   root_stray_photo.jpg  (directly in root -> unassigned_review)

    p1_photo1 = _save_test_image(profile_root / "Mohammed Ahmed" / "photo1.jpg", (10, 20, 30))
    _save_test_image(profile_root / "Mohammed Ahmed" / "photo2.jpg", (15, 25, 35))
    # Exact binary copy for duplicate detection test
    (profile_root / "Mohammed Ahmed" / "photo1_dup.jpg").write_bytes(p1_photo1.read_bytes())
    _save_test_image(profile_root / "Mohammed Ahmed" / "group_office.jpg", (20, 30, 40))

    _save_test_image(profile_root / "John Smith" / "IMG_9081.jpg", (40, 50, 60))
    _save_test_image(profile_root / "John Smith" / "no_face_scenery.jpg", (45, 55, 65))
    _save_test_image(profile_root / "John Smith" / "blurry_low.jpg", (48, 58, 68))

    _save_test_image(profile_root / "Fatima Khan" / "image1.png", (70, 80, 90))
    _save_test_image(profile_root / "Fatima Khan" / "image2.jpg", (75, 85, 95))

    _save_test_image(profile_root / "Unknown" / "mystery.jpg", (100, 110, 120))
    _save_test_image(profile_root / "root_stray_photo.jpg", (130, 140, 150))

    # Identity vectors (512-D L2-normalized)
    vec_mohammed_1 = _unit_vec(1, noise=0.01)
    vec_mohammed_2 = _unit_vec(1, noise=0.03)
    vec_john_1 = _unit_vec(2, noise=0.01)
    vec_john_2 = _unit_vec(2, noise=0.04)
    vec_fatima_1 = _unit_vec(3, noise=0.01)
    vec_fatima_2 = _unit_vec(3, noise=0.02)
    vec_stranger = _unit_vec(9, noise=0.01)

    def _face(vec: list[float], score: float = 0.92, bbox: list[float] | None = None) -> DetectedFaceResult:
        return DetectedFaceResult(
            bbox=bbox or [24.0, 30.0, 160.0, 195.0],
            det_score=score,
            landmarks=[[50.0, 70.0], [120.0, 70.0], [85.0, 110.0], [60.0, 150.0], [110.0, 150.0]],
            normed_embedding=vec,
            embedding_dim=512,
            model_pack="buffalo_l",
            recognition_model="w600k_r50.onnx",
        )

    manifest = {
        # Enrollment profile photos
        "photo1.jpg": [_face(vec_mohammed_1, 0.96)],
        "photo2.jpg": [_face(vec_mohammed_2, 0.94)],
        "group_office.jpg": [_face(vec_mohammed_1, 0.93), _face(vec_john_1, 0.91, [180.0, 35.0, 290.0, 195.0])],
        "IMG_9081.jpg": [_face(vec_john_1, 0.95)],
        "no_face_scenery.jpg": [],
        "blurry_low.jpg": [_face(vec_john_2, 0.32)],
        "image1.png": [_face(vec_fatima_1, 0.97)],
        "image2.jpg": [_face(vec_fatima_2, 0.95)],
        "mystery.jpg": [_face(vec_john_2, 0.90)],
        "root_stray_photo.jpg": [_face(vec_john_2, 0.93)],
        # Archive test assets
        "archive_mohammed_lecture.jpg": [_face(_unit_vec(1, noise=0.02), 0.94)],
        "archive_mohammed_solar.jpg": [_face(_unit_vec(1, noise=0.015), 0.93)],
        "archive_cooccurrence_panel.jpg": [
            _face(_unit_vec(1, noise=0.02), 0.95, [20.0, 30.0, 120.0, 170.0]),
            _face(_unit_vec(3, noise=0.015), 0.96, [150.0, 32.0, 250.0, 172.0]),
        ],
        "archive_unknown_guest.jpg": [_face(vec_stranger, 0.95)],
        "video_frame_0ms.jpg": [],
        "video_frame_5000ms.jpg": [_face(_unit_vec(3, noise=0.02), 0.94, [40.0, 40.0, 180.0, 200.0])],
        "video_frame_10000ms_dup.jpg": [_face(_unit_vec(3, noise=0.02), 0.94, [40.0, 40.0, 180.0, 200.0])],
    }
    engine = ControlledBuffaloEngine(manifest)

    # --- 1. Run Folder-Hierarchy Enrollment Scan ---
    report = scan_and_enroll_profile_folder(
        db_session,
        profile_root=profile_root,
        engine=engine,
        auto_confirm=False,
    )

    assert report["model_pack"] == "buffalo_l"
    assert report["persons_discovered"] == 3
    assert report["total_photographs_scanned"] == 11
    assert report["successful_detections"] == 5  # Mohammed (2), John (1), Fatima (2)
    assert report["duplicate_files"] == 1        # photo1_dup.jpg
    assert report["multi_face_flagged"] == 1     # group_office.jpg
    assert report["failed_detections"] == 1      # no_face_scenery.jpg
    assert report["low_quality_flagged"] == 1    # blurry_low.jpg
    assert report["unassigned_root_files"] == 2  # root_stray_photo.jpg + Unknown/mystery.jpg

    # Verify person names come strictly from folder names (never from IMG_9081.jpg or photo1.jpg)
    people_names = {p["person_name"] for p in report["people"]}
    assert people_names == {"Mohammed Ahmed", "John Smith", "Fatima Khan"}
    for p in report["people"]:
        assert p["status"] == "pending_confirmation"

    # Verify multi-face photo was NOT enrolled as an embedding
    multi_photo = db_session.scalar(
        select(EnrolledReferencePhoto).where(EnrolledReferencePhoto.original_filename == "group_office.jpg")
    )
    assert multi_photo is not None
    assert multi_photo.status == "needs_review"
    assert multi_photo.review_reason == "multiple_faces_detected"
    assert multi_photo.detected_face_count == 2
    multi_emb_count = db_session.scalar(
        select(func.count()).select_from(FaceReferenceEmbedding).where(
            FaceReferenceEmbedding.reference_photo_id == multi_photo.id
        )
    )
    assert multi_emb_count == 0

    # --- 2. Administrator Review: Rename, Reassign Root Photo & Confirm Enrollment ---
    john = db_session.scalar(select(PersonProfile).where(PersonProfile.display_name == "John Smith"))
    mohammed = db_session.scalar(select(PersonProfile).where(PersonProfile.display_name == "Mohammed Ahmed"))
    fatima = db_session.scalar(select(PersonProfile).where(PersonProfile.display_name == "Fatima Khan"))
    assert john and mohammed and fatima

    # Rename John Smith -> Dr. John Smith, then back to John Smith
    renamed = update_person_profile_name(db_session, john.id, "Dr. John Smith")
    assert renamed.display_name == "Dr. John Smith"
    update_person_profile_name(db_session, john.id, "John Smith")

    # Reassign root_stray_photo.jpg from unassigned queue to John Smith
    stray_photo = db_session.scalar(
        select(EnrolledReferencePhoto).where(EnrolledReferencePhoto.original_filename == "root_stray_photo.jpg")
    )
    assert stray_photo is not None and stray_photo.status == "unassigned_review"
    reassigned = reassign_reference_photo(db_session, stray_photo.id, "John Smith", engine=engine)
    assert reassigned.person_id == john.id
    assert reassigned.status == "pending_confirmation"

    # Confirm all 3 people so their reference embeddings become active
    confirm_person_enrollment(db_session, mohammed.id, consent_reference="IIHS-HR-2026-001")
    confirm_person_enrollment(db_session, john.id, consent_reference="IIHS-HR-2026-002")
    confirm_person_enrollment(db_session, fatima.id, consent_reference="IIHS-HR-2026-003")

    active_people = db_session.scalars(select(PersonProfile)).all()
    active_counts = {p.display_name: p.active_embedding_count for p in active_people}
    assert active_counts == {"Mohammed Ahmed": 2, "John Smith": 2, "Fatima Khan": 2}

    # --- 3. Archive Image Ingestion & Matching Against Controlled Dataset ---
    img_lecture_path = _save_test_image(tmp_path / "archive_mohammed_lecture.jpg", (11, 22, 33))
    img_solar_path = _save_test_image(tmp_path / "archive_mohammed_solar.jpg", (12, 23, 34))
    img_group_path = _save_test_image(tmp_path / "archive_cooccurrence_panel.jpg", (22, 44, 66))
    img_unknown_path = _save_test_image(tmp_path / "archive_unknown_guest.jpg", (99, 88, 77))

    asset_lecture = MediaAsset(
        id="asset-lecture-2024",
        source_relative_key="2024/Urban_Fellows_Batch8/archive_mohammed_lecture.jpg",
        original_filename="archive_mohammed_lecture.jpg",
        media_type="image",
        detected_mime="image/jpeg",
        size_bytes=1024,
        sha256="a" * 64,
        storage_key="aa/lecture.jpg",
        created_at=datetime(2024, 6, 15, tzinfo=timezone.utc),
        metadata_json={"context": infer_path_context("2024/Urban_Fellows_Batch8/archive_mohammed_lecture.jpg")},
    )
    asset_solar = MediaAsset(
        id="asset-solar-2022",
        source_relative_key="2022/Solar_Decathlon_India/archive_mohammed_solar.jpg",
        original_filename="archive_mohammed_solar.jpg",
        media_type="image",
        detected_mime="image/jpeg",
        size_bytes=1024,
        sha256="b" * 64,
        storage_key="bb/solar.jpg",
        created_at=datetime(2022, 4, 10, tzinfo=timezone.utc),
        metadata_json={"context": infer_path_context("2022/Solar_Decathlon_India/archive_mohammed_solar.jpg")},
    )
    asset_group = MediaAsset(
        id="asset-group-2024",
        source_relative_key="2024/Urban_Fellows_Batch8/archive_cooccurrence_panel.jpg",
        original_filename="archive_cooccurrence_panel.jpg",
        media_type="image",
        detected_mime="image/jpeg",
        size_bytes=1024,
        sha256="c" * 64,
        storage_key="cc/group.jpg",
        created_at=datetime(2024, 6, 16, tzinfo=timezone.utc),
        metadata_json={"context": infer_path_context("2024/Urban_Fellows_Batch8/archive_cooccurrence_panel.jpg")},
    )
    asset_unknown = MediaAsset(
        id="asset-unknown-2024",
        source_relative_key="2024/Visitors/archive_unknown_guest.jpg",
        original_filename="archive_unknown_guest.jpg",
        media_type="image",
        detected_mime="image/jpeg",
        size_bytes=1024,
        sha256="d" * 64,
        storage_key="dd/unknown.jpg",
        created_at=datetime(2024, 7, 1, tzinfo=timezone.utc),
        metadata_json={"context": infer_path_context("2024/Visitors/archive_unknown_guest.jpg")},
    )
    db_session.add_all([asset_lecture, asset_solar, asset_group, asset_unknown])
    db_session.commit()

    res_lecture = process_archive_asset_faces(db_session, asset_lecture, img_lecture_path, engine=engine, allow_isolated_test=True)
    res_solar = process_archive_asset_faces(db_session, asset_solar, img_solar_path, engine=engine, allow_isolated_test=True)
    res_group = process_archive_asset_faces(db_session, asset_group, img_group_path, engine=engine, allow_isolated_test=True)
    res_unknown = process_archive_asset_faces(db_session, asset_unknown, img_unknown_path, engine=engine, allow_isolated_test=True)
    db_session.commit()

    assert res_lecture == {"faces_detected": 1, "candidate_matches": 1, "skipped_identical_frame": False}
    assert res_solar == {"faces_detected": 1, "candidate_matches": 1, "skipped_identical_frame": False}
    assert res_group == {"faces_detected": 2, "candidate_matches": 2, "skipped_identical_frame": False}
    # Unknown visitor face was detected, but matched 0 enrolled people!
    assert res_unknown == {"faces_detected": 1, "candidate_matches": 0, "skipped_identical_frame": False}

    # --- 4. Unverified Matches Must NOT Appear in Hybrid Search ---
    unverified_search = execute_hybrid_search(db_session, q="Mohammed Ahmed", mode="keyword")
    assert unverified_search == []
    unverified_by_id = execute_hybrid_search(db_session, q="", mode="keyword", person_ids=[mohammed.id])
    assert unverified_by_id == []

    # --- 5. Human Verification: Accept & Reject Candidate Matches ---
    all_candidates = db_session.scalars(select(FaceCandidateMatch)).all()
    assert len(all_candidates) == 4
    for cand in all_candidates:
        assert cand.status == "unverified"
        assert cand.matched_reference_count == 2  # Aggregated across 2 reference photos!

    # Accept Mohammed on asset_lecture, reject Mohammed on asset_solar initially, accept both on asset_group
    cand_lecture = db_session.scalar(select(FaceCandidateMatch).where(FaceCandidateMatch.asset_id == asset_lecture.id))
    cand_solar = db_session.scalar(select(FaceCandidateMatch).where(FaceCandidateMatch.asset_id == asset_solar.id))
    cands_group = db_session.scalars(select(FaceCandidateMatch).where(FaceCandidateMatch.asset_id == asset_group.id)).all()

    review_face_candidate_match(db_session, cand_lecture.id, actor_id="archivist-1", decision="accepted", notes="Verified lecturer")
    review_face_candidate_match(db_session, cand_solar.id, actor_id="archivist-1", decision="rejected", notes="Rejected for test")
    for cg in cands_group:
        review_face_candidate_match(db_session, cg.id, actor_id="archivist-1", decision="accepted", notes="Verified group panel")

    # Search for Mohammed Ahmed: should return asset_lecture and asset_group, NOT rejected asset_solar!
    mohammed_results = execute_hybrid_search(db_session, q="Mohammed Ahmed", mode="keyword")
    mohammed_asset_ids = {asset.id for asset, _ in mohammed_results}
    assert mohammed_asset_ids == {"asset-lecture-2024", "asset-group-2024"}

    # Now accept cand_solar too so we can test project/keyword/year filtering with Mohammed Ahmed
    review_face_candidate_match(db_session, cand_solar.id, actor_id="archivist-1", decision="accepted", notes="Re-verified solar")

    # Combine person identity with IIHS keyword & year ("Mohammed Ahmed Urban Fellows 2024")
    combined_results = execute_hybrid_search(db_session, q="Mohammed Ahmed Urban Fellows 2024", mode="keyword")
    combined_ids = {asset.id for asset, _ in combined_results}
    assert combined_ids == {"asset-lecture-2024", "asset-group-2024"}
    assert "asset-solar-2022" not in combined_ids

    # Co-occurrence search: find photographs containing BOTH Mohammed Ahmed AND Fatima Khan
    cooccur_by_ids = execute_hybrid_search(
        db_session,
        q="",
        mode="keyword",
        person_ids=[mohammed.id, fatima.id],
    )
    assert [asset.id for asset, _ in cooccur_by_ids] == ["asset-group-2024"]
    group_meta = cooccur_by_ids[0][1]
    assert {f["person_name"] for f in group_meta["matched_faces"]} == {"Mohammed Ahmed", "Fatima Khan"}

    cooccur_by_query = execute_hybrid_search(
        db_session,
        q="Mohammed Ahmed and Fatima Khan",
        mode="keyword",
    )
    assert [asset.id for asset, _ in cooccur_by_query] == ["asset-group-2024"]

    # --- 6. Video Timestamp Face Matching & Static Frame Deduplication ---
    video_asset = MediaAsset(
        id="asset-video-2024",
        source_relative_key="2024/Urban_Fellows_Batch8/panel_recording.mp4",
        original_filename="panel_recording.mp4",
        media_type="video",
        detected_mime="video/mp4",
        size_bytes=4096,
        sha256="e" * 64,
        storage_key="ee/panel.mp4",
        duration_ms=15000,
        created_at=datetime(2024, 6, 17, tzinfo=timezone.utc),
        metadata_json={"context": infer_path_context("2024/Urban_Fellows_Batch8/panel_recording.mp4")},
    )
    f0 = VideoFrame(id="frame-0", asset_id=video_asset.id, timestamp_ms=0, storage_key="ee/f0.webp")
    f5 = VideoFrame(id="frame-5000", asset_id=video_asset.id, timestamp_ms=5000, storage_key="ee/f5.webp")
    f10 = VideoFrame(id="frame-10000", asset_id=video_asset.id, timestamp_ms=10000, storage_key="ee/f10.webp")
    db_session.add_all([video_asset, f0, f5, f10])
    db_session.commit()

    f0_path = _save_test_image(tmp_path / "video_frame_0ms.jpg", (10, 10, 10))
    f5_path = _save_test_image(tmp_path / "video_frame_5000ms.jpg", (80, 120, 200))
    # Visually identical frame to f5_path (same pixel pattern) to test dHash deduplication
    f10_path = _save_test_image(tmp_path / "video_frame_10000ms_dup.jpg", (80, 120, 200))

    v_res_0 = process_archive_asset_faces(db_session, video_asset, f0_path, frame=f0, engine=engine, allow_isolated_test=True)
    v_res_5 = process_archive_asset_faces(db_session, video_asset, f5_path, frame=f5, engine=engine, allow_isolated_test=True)
    v_res_10 = process_archive_asset_faces(db_session, video_asset, f10_path, frame=f10, engine=engine, allow_isolated_test=True)
    db_session.commit()

    assert v_res_0["faces_detected"] == 0
    assert v_res_5 == {"faces_detected": 1, "candidate_matches": 1, "skipped_identical_frame": False}
    assert v_res_10["skipped_identical_frame"] is True

    video_cand = db_session.scalar(select(FaceCandidateMatch).where(FaceCandidateMatch.asset_id == video_asset.id))
    assert video_cand is not None
    review_face_candidate_match(db_session, video_cand.id, actor_id="archivist-1", decision="accepted")

    fatima_video_hits = execute_hybrid_search(
        db_session,
        q="Fatima Khan",
        mode="keyword",
        media_type="video",
    )
    assert len(fatima_video_hits) == 1
    vid_asset, vid_meta = fatima_video_hits[0]
    assert vid_asset.id == "asset-video-2024"
    assert vid_meta["matched_frame_id"] == "frame-5000"
    assert vid_meta["matched_timestamp_ms"] == 5000

    # --- 7. Complete Biometric Record Deletion Upon Revocation ---
    del_summary = revoke_person_enrollment(
        db_session,
        fatima.id,
        actor_id="admin-1",
        delete_profile_record=False,
        reason="Consent withdrawn by individual",
    )
    assert del_summary["status"] == "revoked"
    assert del_summary["deleted_embeddings"] == 2
    assert del_summary["deleted_reference_photos"] == 2
    assert del_summary["deleted_candidate_matches"] == 2
    assert del_summary["deleted_verification_decisions"] == 2

    # Verify zero biometric records remain for Fatima Khan across all tables
    assert db_session.scalar(select(func.count()).select_from(FaceReferenceEmbedding).where(FaceReferenceEmbedding.person_id == fatima.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(EnrolledReferencePhoto).where(EnrolledReferencePhoto.person_id == fatima.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(FaceCandidateMatch).where(FaceCandidateMatch.person_id == fatima.id)) == 0
    assert db_session.scalar(select(func.count()).select_from(FaceVerificationDecision).where(FaceVerificationDecision.person_id == fatima.id)) == 0

    # Verify Fatima Khan no longer returns any search results
    assert execute_hybrid_search(db_session, q="Fatima Khan", mode="keyword") == []
    assert execute_hybrid_search(db_session, q="", mode="keyword", person_ids=[fatima.id]) == []
