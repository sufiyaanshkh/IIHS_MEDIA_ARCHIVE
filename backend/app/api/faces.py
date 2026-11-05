from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import csrf_check, roles
from app.models import (
    DetectedArchiveFace,
    EnrolledReferencePhoto,
    FaceCandidateMatch,
    FaceReferenceEmbedding,
    MediaAsset,
    PersonProfile,
    User,
)
from app.services.face_recognition import (
    BiometricGovernanceError,
    confirm_person_enrollment,
    get_face_engine,
    reassign_reference_photo,
    review_face_candidate_match,
    revoke_person_enrollment,
    scan_and_enroll_profile_folder,
    update_person_profile_name,
)

router = APIRouter(prefix="/api/faces", tags=["face recognition and biometrics"])


class EnrollmentScanRequest(BaseModel):
    subpath: str | None = Field(default=None, max_length=240)
    min_det_score: float | None = Field(default=None, ge=0.1, le=0.99)
    auto_confirm: bool = False


class PersonNameUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class PhotoReassignRequest(BaseModel):
    person_name: str = Field(min_length=1, max_length=200)


class ConfirmEnrollmentRequest(BaseModel):
    consent_reference: str | None = Field(default=None, max_length=255)


class RevokeEnrollmentRequest(BaseModel):
    delete_profile_record: bool = False
    reason: str | None = Field(default=None, max_length=500)


class CandidateVerificationRequest(BaseModel):
    decision: str = Field(pattern=r"^(accepted|rejected)$")
    notes: str | None = Field(default=None, max_length=1000)


@router.get("/status")
def biometric_status(
    user: Annotated[User, Depends(roles("admin", "archivist", "operator"))],
    db: Annotated[Session, Depends(get_db)],
):
    return {
        "biometric_processing_enabled": bool(settings.face_feature_enabled and settings.face_institutional_authorization),
        "face_feature_enabled": settings.face_feature_enabled,
        "face_institutional_authorization": settings.face_institutional_authorization,
        "model_pack": settings.face_model_pack,
        "profile_photos_root": str(settings.face_profile_photos_root),
        "match_threshold": settings.face_match_threshold,
        "high_confidence_threshold": settings.face_high_confidence_threshold,
        "min_det_score": settings.face_min_det_score,
        "people_total": db.scalar(select(func.count()).select_from(PersonProfile)) or 0,
        "people_active": db.scalar(select(func.count()).select_from(PersonProfile).where(PersonProfile.status == "active")) or 0,
        "people_pending_confirmation": db.scalar(select(func.count()).select_from(PersonProfile).where(PersonProfile.status == "pending_confirmation")) or 0,
        "reference_photos_total": db.scalar(select(func.count()).select_from(EnrolledReferencePhoto)) or 0,
        "reference_photos_needs_review": db.scalar(
            select(func.count()).select_from(EnrolledReferencePhoto).where(
                EnrolledReferencePhoto.status.in_(["needs_review", "unassigned_review", "failed_detection", "low_quality"])
            )
        ) or 0,
        "candidate_matches_unverified": db.scalar(
            select(func.count()).select_from(FaceCandidateMatch).where(FaceCandidateMatch.status == "unverified")
        ) or 0,
        "candidate_matches_accepted": db.scalar(
            select(func.count()).select_from(FaceCandidateMatch).where(FaceCandidateMatch.status == "accepted")
        ) or 0,
    }


@router.post("/enrollment/scan")
def trigger_enrollment_scan(
    body: EnrollmentScanRequest,
    request: Request,
    actor: Annotated[User, Depends(roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    base_root = settings.face_profile_photos_root.resolve()
    target_root = base_root
    if body.subpath:
        cand = (base_root / body.subpath).resolve()
        if not cand.is_relative_to(base_root):
            raise HTTPException(status_code=400, detail="Invalid profile photo subpath.")
        target_root = cand
    if not target_root.is_dir():
        raise HTTPException(status_code=404, detail=f"Profile photo directory not found: {target_root}")
    try:
        return scan_and_enroll_profile_folder(
            db,
            profile_root=target_root,
            actor_id=actor.id,
            min_det_score=body.min_det_score,
            auto_confirm=body.auto_confirm,
        )
    except (BiometricGovernanceError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/enrollment/report")
def get_enrollment_report(
    user: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
):
    people = db.scalars(select(PersonProfile).order_by(PersonProfile.display_name)).all()
    photos = db.scalars(select(EnrolledReferencePhoto).order_by(EnrolledReferencePhoto.source_relative_path)).all()

    by_person: dict[str, list[EnrolledReferencePhoto]] = {}
    review_queue = []
    for p in photos:
        if p.person_id:
            by_person.setdefault(p.person_id, []).append(p)
        if p.status in {"needs_review", "unassigned_review", "failed_detection", "low_quality"}:
            review_queue.append({
                "photo_id": p.id,
                "person_id": p.person_id,
                "folder_person_name": p.folder_person_name,
                "source_relative_path": p.source_relative_path,
                "original_filename": p.original_filename,
                "status": p.status,
                "review_reason": p.review_reason,
                "detected_face_count": p.detected_face_count,
                "det_score": p.det_score,
            })

    people_report = []
    for person in people:
        p_photos = by_person.get(person.id, [])
        people_report.append({
            "person_id": person.id,
            "person_name": person.display_name,
            "folder_name": person.folder_name,
            "status": person.status,
            "consent_confirmed": person.consent_confirmed,
            "photographs_scanned": len(p_photos),
            "successful_detections": sum(1 for item in p_photos if item.status in {"pending_confirmation", "enrolled"}),
            "failed_detections": sum(1 for item in p_photos if item.status == "failed_detection"),
            "multi_face_flagged": sum(1 for item in p_photos if item.status == "needs_review"),
            "low_quality_flagged": sum(1 for item in p_photos if item.status == "low_quality"),
            "duplicate_files": sum(1 for item in p_photos if item.status == "duplicate"),
            "active_embeddings": person.active_embedding_count,
        })

    return {
        "model_pack": settings.face_model_pack,
        "total_photographs_scanned": len(photos),
        "persons_discovered": len(people),
        "successful_detections": sum(1 for item in photos if item.status in {"pending_confirmation", "enrolled"}),
        "failed_detections": sum(1 for item in photos if item.status == "failed_detection"),
        "multi_face_flagged": sum(1 for item in photos if item.status == "needs_review"),
        "low_quality_flagged": sum(1 for item in photos if item.status == "low_quality"),
        "unassigned_root_files": sum(1 for item in photos if item.status == "unassigned_review"),
        "duplicate_files": sum(1 for item in photos if item.status == "duplicate"),
        "people": people_report,
        "review_queue": review_queue,
    }


@router.get("/people")
def list_enrolled_people(
    user: Annotated[User, Depends(roles("admin", "archivist", "researcher", "operator"))],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(default=None),
):
    stmt = select(PersonProfile).order_by(PersonProfile.display_name)
    if user.role.value in {"researcher", "operator"}:
        stmt = stmt.where(PersonProfile.status == "active")
    elif status:
        stmt = stmt.where(PersonProfile.status == status)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": p.id,
            "display_name": p.display_name,
            "folder_name": p.folder_name,
            "status": p.status,
            "consent_confirmed": p.consent_confirmed,
            "photo_count": p.photo_count,
            "active_embedding_count": p.active_embedding_count,
            "confirmed_at": p.confirmed_at,
            "created_at": p.created_at,
        }
        for p in rows
    ]


@router.post("/people/{person_id}/rename")
def rename_person(
    person_id: str,
    body: PersonNameUpdateRequest,
    request: Request,
    actor: Annotated[User, Depends(roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    try:
        person = update_person_profile_name(db, person_id, body.display_name, actor_id=actor.id)
        return {"id": person.id, "display_name": person.display_name, "status": person.status}
    except LookupError:
        raise HTTPException(status_code=404, detail="Person profile not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/photos/{photo_id}/reassign")
def reassign_photo(
    photo_id: str,
    body: PhotoReassignRequest,
    request: Request,
    actor: Annotated[User, Depends(roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    try:
        photo = reassign_reference_photo(db, photo_id, body.person_name, actor_id=actor.id)
        return {
            "photo_id": photo.id,
            "person_id": photo.person_id,
            "status": photo.status,
            "review_reason": photo.review_reason,
        }
    except LookupError:
        raise HTTPException(status_code=404, detail="Reference photo not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/people/{person_id}/confirm")
def confirm_person(
    person_id: str,
    body: ConfirmEnrollmentRequest,
    request: Request,
    actor: Annotated[User, Depends(roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    try:
        person = confirm_person_enrollment(
            db,
            person_id,
            actor_id=actor.id,
            consent_reference=body.consent_reference,
        )
        return {
            "id": person.id,
            "display_name": person.display_name,
            "status": person.status,
            "consent_confirmed": person.consent_confirmed,
            "active_embedding_count": person.active_embedding_count,
            "confirmed_at": person.confirmed_at,
        }
    except LookupError:
        raise HTTPException(status_code=404, detail="Person profile not found.") from None


@router.post("/people/{person_id}/revoke")
def revoke_person(
    person_id: str,
    body: RevokeEnrollmentRequest,
    request: Request,
    actor: Annotated[User, Depends(roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    try:
        return revoke_person_enrollment(
            db,
            person_id,
            actor_id=actor.id,
            delete_profile_record=body.delete_profile_record,
            reason=body.reason,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Person profile not found.") from None


@router.get("/matches")
def list_candidate_matches(
    user: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    status: str = Query(default="unverified", pattern=r"^(unverified|accepted|rejected|all)$"),
    person_id: str | None = Query(default=None),
    asset_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = (
        select(FaceCandidateMatch, DetectedArchiveFace, PersonProfile, MediaAsset)
        .join(DetectedArchiveFace, DetectedArchiveFace.id == FaceCandidateMatch.detected_face_id)
        .join(PersonProfile, PersonProfile.id == FaceCandidateMatch.person_id)
        .join(MediaAsset, MediaAsset.id == FaceCandidateMatch.asset_id)
    )
    if status != "all":
        stmt = stmt.where(FaceCandidateMatch.status == status)
    if person_id:
        stmt = stmt.where(FaceCandidateMatch.person_id == person_id)
    if asset_id:
        stmt = stmt.where(FaceCandidateMatch.asset_id == asset_id)
    stmt = stmt.order_by(FaceCandidateMatch.aggregate_similarity.desc()).limit(limit)

    rows = db.execute(stmt).all()
    return [
        {
            "match_id": match.id,
            "status": match.status,
            "person_id": person.id,
            "person_name": person.display_name,
            "asset_id": asset.id,
            "filename": asset.original_filename,
            "media_type": asset.media_type,
            "source_path": asset.source_relative_key,
            "frame_id": det_face.frame_id,
            "timestamp_ms": det_face.timestamp_ms,
            "bbox": det_face.bbox_json,
            "det_score": det_face.det_score,
            "aggregate_similarity": match.aggregate_similarity,
            "max_similarity": match.max_similarity,
            "mean_top_similarity": match.mean_top_similarity,
            "matched_reference_count": match.matched_reference_count,
            "high_confidence": match.high_confidence,
            "created_at": match.created_at,
        }
        for match, det_face, person, asset in rows
    ]


@router.post("/matches/{match_id}/verify")
def verify_candidate_match(
    match_id: str,
    body: CandidateVerificationRequest,
    request: Request,
    actor: Annotated[User, Depends(roles("admin", "archivist"))],
    db: Annotated[Session, Depends(get_db)],
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="iihs_csrf"),
):
    csrf_check(request, csrf_header, csrf_cookie)
    try:
        match = review_face_candidate_match(
            db,
            match_id,
            actor_id=actor.id,
            decision=body.decision,
            notes=body.notes,
        )
        return {
            "match_id": match.id,
            "status": match.status,
            "person_id": match.person_id,
            "asset_id": match.asset_id,
            "aggregate_similarity": match.aggregate_similarity,
        }
    except LookupError:
        raise HTTPException(status_code=404, detail="Candidate face match not found.") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

