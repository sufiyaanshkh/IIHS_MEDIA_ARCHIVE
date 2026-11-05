"""Searchable semantic profiles combining visual keywords and verified identities.

The profile is a text embedding stored alongside (not instead of) the original
image/frame vectors. AI suggestions remain explicitly marked as unverified;
person names are included only after an authorised human face-match decision.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select

from app.config import settings
from app.models import (
    Annotation,
    AssetEmbedding,
    FaceCandidateMatch,
    MediaAsset,
    PersonProfile,
    SearchSegment,
)
from app.services.embeddings import text_embeddings
from app.services.media import MediaValidationError
from app.services.search_segments import segment_content_hash

log = logging.getLogger(__name__)
PROFILE_ORDINAL = 9000


def _labels(items, key: str = "label") -> list[str]:
    labels = []
    for item in items or []:
        value = item.get(key) if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip():
            label = " ".join(value.split())
            if label.casefold() not in {v.casefold() for v in labels}:
                labels.append(label)
    return labels


def build_semantic_profile(db, asset: MediaAsset) -> tuple[str, dict]:
    meta = asset.metadata_json or {}
    visual = meta.get("visual_analysis") or {}
    context = meta.get("context") or {}

    description = (visual.get("description") or "").strip()
    suggested_objects = _labels(visual.get("object_tags"))
    suggested_keywords = _labels(visual.get("keyword_suggestions"))

    accepted_annotations = db.scalars(
        select(Annotation.label).where(
            Annotation.asset_id == asset.id,
            Annotation.status.in_(("accepted", "corrected")),
        )
    ).all()
    verified_tags = _labels(accepted_annotations)

    verified_people = db.execute(
        select(PersonProfile.id, PersonProfile.display_name)
        .join(FaceCandidateMatch, FaceCandidateMatch.person_id == PersonProfile.id)
        .where(
            FaceCandidateMatch.asset_id == asset.id,
            FaceCandidateMatch.status == "accepted",
            PersonProfile.status == "active",
        )
        .distinct()
    ).all()
    people = _labels([name for _person_id, name in verified_people])

    sections = []
    if description:
        sections.append("AI-generated visual description (unverified): " + description)
    if suggested_objects:
        sections.append("AI-suggested visible objects (unverified): " + ", ".join(suggested_objects))
    if suggested_keywords:
        sections.append("AI-suggested IIHS keywords (unverified): " + ", ".join(suggested_keywords))
    if verified_tags:
        sections.append("Human-verified visual tags: " + ", ".join(verified_tags))
    if people:
        sections.append("Human-verified people identified by authorised face review: " + ", ".join(people))

    sources = {
        "ai_visual_description": bool(description),
        "ai_object_suggestions": bool(suggested_objects),
        "ai_keyword_suggestions": bool(suggested_keywords),
        "human_verified_tags": bool(verified_tags),
        "human_verified_people": bool(people),
    }
    metadata = {
        "semantic_profile": True,
        "is_visual_claim": bool(description or suggested_objects or verified_tags),
        "contains_verified_person_identity": bool(people),
        "verified_person_names": people,
        "sources": sources,
    }
    return "\n".join(sections), metadata


def refresh_asset_semantic_profile(db, asset: MediaAsset) -> bool:
    """Replace the asset's derived semantic-profile vector without touching media vectors."""
    db.flush()
    profile_text, profile_metadata = build_semantic_profile(db, asset)

    vector = model_id = model_revision = None
    if profile_text:
        prompt = settings.embedding_document_prompt.format(
            title=f"IIHS semantic profile: {asset.original_filename}",
            text=profile_text,
        )
        try:
            vector, model_id, model_revision = text_embeddings([prompt])[0]
        except MediaValidationError:
            # Keep a lexical profile even while the local embedding bridge is offline.
            log.warning("Semantic profile embedding unavailable for asset %s; retaining text-only profile", asset.id)

    db.execute(
        delete(SearchSegment).where(
            SearchSegment.asset_id == asset.id,
            SearchSegment.segment_type == "visual_description",
            SearchSegment.segment_ordinal == PROFILE_ORDINAL,
        )
    )
    db.execute(
        delete(AssetEmbedding).where(
            AssetEmbedding.asset_id == asset.id,
            AssetEmbedding.modality == "visual_description",
        )
    )

    if not profile_text:
        return False

    content_hash = segment_content_hash("visual_description", profile_text, PROFILE_ORDINAL)
    db.add(SearchSegment(
        asset_id=asset.id,
        segment_type="visual_description",
        modality="visual_description",
        title=f"Semantic profile: {asset.original_filename}",
        text_content=profile_text,
        embedding=vector,
        model_id=model_id,
        model_revision=model_revision,
        segment_ordinal=PROFILE_ORDINAL,
        original_file_reference=asset.source_relative_key or asset.original_filename,
        content_hash=content_hash,
        metadata_json=profile_metadata,
    ))
    if vector is not None and model_id and model_revision:
        db.add(AssetEmbedding(
            asset_id=asset.id,
            embedding=vector,
            modality="visual_description",
            model_id=model_id,
            model_revision=model_revision,
        ))
    return True
