"""Integrated Multimodal Search Engine with Query Intent Classification,
Isolated Retrieval Channels, and Intent-Aware Rank Fusion.

Architecture & Channels:
1. Visual Embedding Channel (image & video-frame vectors with query-relative z-gating)
2. Caption & Scene Description Channel (VLM captions, visual profiles, scene understanding)
3. Object & Tag Channel (verified annotations, AI object tags, IIHS taxonomy keywords)
4. OCR & Multi-format Document Channel (text in images, PDF pages, office documents, transcripts)
5. Metadata & Lexical Channel (filename, folder hierarchy, project context, temporal years)
6. Authorized Face Biometrics Channel (human-verified InsightFace matches)

Enforces institutional archival rules:
- Visual searches are driven primarily by visual evidence and scene understanding.
- Folder-derived contextual metadata is never treated as visual detection or proof of visual content.
- Unverified AI suggestions are clearly labeled and separated from human-verified annotations.
- Authorized face recognition requires active enrollment and human verification.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models import (
    Annotation,
    AssetEmbedding,
    DetectedArchiveFace,
    FaceCandidateMatch,
    MediaAsset,
    PersonProfile,
    SearchSegment,
    VideoFrame,
)
from app.services.contextual_metadata import (
    _contains_term,
    _match_item_in_text,
    _normalize_text,
    load_vocabulary,
)
from app.services.embeddings import text_embedding
from app.services.media import MediaValidationError
from app.services.query_intent import (
    QueryIntent,
    classify_query_intent,
    is_identifier_query,
)

RRF_K = 60
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "near", "of", "on", "or", "show", "showing", "that", "the", "to",
    "with", "files", "file", "associated", "particular", "mentioning", "activities",
    "images", "image", "photos", "photo", "photographs", "videos", "video",
    "documents", "document", "audio", "recordings",
}

VISUAL_SEGMENT_TYPES = {"image_visual", "video_frame", "visual_description"}
CONTENT_TEXT_SEGMENT_TYPES = {
    "pdf_page", "office_chunk", "spreadsheet_sheet", "presentation_slide",
    "ocr_text", "audio_transcript_window",
}


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(float(a) * float(a) for a in vec_a)) or 1.0
    norm_b = math.sqrt(sum(float(b) * float(b) for b in vec_b)) or 1.0
    return dot / (norm_a * norm_b)


def parse_query_intent(query: str) -> dict:
    """Backward-compatible helper returning query parsing dictionary."""
    intent = classify_query_intent(query)
    return {
        "raw": intent.raw,
        "normalized": intent.normalized,
        "tokens": intent.content_tokens,
        "years": intent.years,
        "matched_vocab": intent.matched_vocab,
        "modality_hint": intent.modality_hint,
        "intent_type": intent.intent_type,
        "channel_weights": intent.channel_weights,
        "is_exact_lookup": intent.is_exact_lookup,
    }


def _semantic_z_scores(scores: list[float]) -> list[float]:
    """Normalise similarities per query; raw cosine scores are not probabilities."""
    if not scores:
        return []
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores) / len(scores)
    std = math.sqrt(variance)
    if std < 1e-8:
        return [0.0 for _ in scores]
    return [(score - mean) / std for score in scores]


def _semantic_gate(scores: list[float], z_gate: float = 1.5) -> list[bool]:
    """DigUp-inspired query-relative gate, with a small absolute floor.

    It rejects uniformly weak visual matches while avoiding a fixed cosine
    threshold that behaves differently for every query.
    """
    if not scores:
        return []
    z_scores = _semantic_z_scores(scores)
    best = max(scores)
    if best < 0.12:
        return [False for _ in scores]
    return [z >= z_gate or (score >= best - 0.035 and score >= 0.20)
            for score, z in zip(scores, z_scores)]


def _token_coverage(text: str, tokens: list[str]) -> float:
    """Fraction of meaningful query tokens represented in indexed text."""
    if not tokens:
        return 0.0
    normalized = _normalize_text(text)
    matched = sum(1 for token in tokens if _contains_term(normalized, token))
    return matched / len(tokens)


def _is_identifier_query(query: str) -> bool:
    """Backward-compatible identifier query check."""
    return is_identifier_query(query)


def _year_bounds(year: int | None):
    if year is None:
        return None
    return datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)


# ==============================================================================
# CHANNEL 1: Visual Embedding Retrieval (Image & Video Frames)
# ==============================================================================

def _VisualEmbeddingChannel(
    db: Session,
    query_vector: list[float],
    model_id: str,
    model_revision: str,
    media_type: str | None,
    added_year: int | None,
    candidate_limit: int = 200,
) -> dict[str, dict]:
    """Retrieve visual embeddings (image vectors and video keyframes)."""
    visual_hits: dict[str, dict] = {}
    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"

    # Search persistent SearchSegment rows of visual types
    seg_query = (
        select(SearchSegment)
        .join(MediaAsset, MediaAsset.id == SearchSegment.asset_id)
        .where(
            SearchSegment.embedding.is_not(None),
            SearchSegment.segment_type.in_(("image_visual", "video_frame")),
            SearchSegment.model_id == model_id,
            SearchSegment.model_revision == model_revision,
        )
    )
    if media_type:
        seg_query = seg_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        seg_query = seg_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    if is_postgres:
        dist_expr = SearchSegment.embedding.cosine_distance(query_vector)
        rows = db.execute(
            select(SearchSegment, dist_expr.label("distance"))
            .join(MediaAsset, MediaAsset.id == SearchSegment.asset_id)
            .where(
                SearchSegment.embedding.is_not(None),
                SearchSegment.segment_type.in_(("image_visual", "video_frame")),
                SearchSegment.model_id == model_id,
                SearchSegment.model_revision == model_revision,
                *([MediaAsset.media_type == media_type] if media_type else []),
                *([MediaAsset.created_at >= _year_bounds(added_year)[0], MediaAsset.created_at < _year_bounds(added_year)[1]] if added_year else []),
            )
            .order_by(dist_expr.asc())
            .limit(candidate_limit * 2)
        ).all()
        scored_segments = [(row[0], max(-1.0, min(1.0, 1.0 - float(row[1])))) for row in rows]
    else:
        segments = db.scalars(seg_query).all()
        scored_segments = [
            (seg, _cosine_similarity(list(seg.embedding), query_vector))
            for seg in segments
            if seg.embedding is not None
        ]
        scored_segments.sort(key=lambda item: item[1], reverse=True)

    for seg, sim in scored_segments:
        existing = visual_hits.get(seg.asset_id)
        if existing is None or sim > existing["score"]:
            visual_hits[seg.asset_id] = {
                "score": round(sim, 4),
                "segment_id": seg.id,
                "segment_type": seg.segment_type,
                "modality": seg.modality,
                "title": seg.title,
                "excerpt": f"Visual match ({round(sim * 100)}%)",
                "page_number": None,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "frame_id": seg.frame_id,
                "is_visual_claim": True,
            }

    # Supplement from AssetEmbedding if SearchSegments were not yet populated
    emb_query = (
        select(AssetEmbedding)
        .join(MediaAsset, MediaAsset.id == AssetEmbedding.asset_id)
        .where(
            AssetEmbedding.model_id == model_id,
            AssetEmbedding.model_revision == model_revision,
            AssetEmbedding.modality.in_(("image", "image_frame")),
        )
    )
    if media_type:
        emb_query = emb_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        emb_query = emb_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    if is_postgres:
        dist_expr = AssetEmbedding.embedding.cosine_distance(query_vector)
        emb_rows = db.execute(
            select(AssetEmbedding, dist_expr.label("distance"))
            .join(MediaAsset, MediaAsset.id == AssetEmbedding.asset_id)
            .where(
                AssetEmbedding.model_id == model_id,
                AssetEmbedding.model_revision == model_revision,
                AssetEmbedding.modality.in_(("image", "image_frame")),
                *([MediaAsset.media_type == media_type] if media_type else []),
                *([MediaAsset.created_at >= _year_bounds(added_year)[0], MediaAsset.created_at < _year_bounds(added_year)[1]] if added_year else []),
            )
            .order_by(dist_expr.asc())
            .limit(candidate_limit * 2)
        ).all()
        scored_embs = [(row[0], max(-1.0, min(1.0, 1.0 - float(row[1])))) for row in emb_rows]
    else:
        embs = db.scalars(emb_query).all()
        scored_embs = [
            (emb, _cosine_similarity(list(emb.embedding), query_vector))
            for emb in embs
            if emb.embedding is not None
        ]
        scored_embs.sort(key=lambda item: item[1], reverse=True)

    frame_ids = {emb.frame_id for emb, _ in scored_embs if emb.frame_id}
    frames_by_id = {}
    if frame_ids:
        frames_by_id = {f.id: f for f in db.scalars(select(VideoFrame).where(VideoFrame.id.in_(frame_ids))).all()}

    for emb, sim in scored_embs:
        existing = visual_hits.get(emb.asset_id)
        frame = frames_by_id.get(emb.frame_id) if emb.frame_id else None
        if existing is None or sim > existing["score"]:
            visual_hits[emb.asset_id] = {
                "score": round(sim, 4),
                "segment_id": None,
                "segment_type": "video_frame" if frame else "image_visual",
                "modality": emb.modality,
                "title": None,
                "excerpt": f"Visual match ({round(sim * 100)}%)",
                "page_number": None,
                "start_ms": frame.timestamp_ms if frame else None,
                "end_ms": frame.timestamp_ms if frame else None,
                "frame_id": frame.id if frame else None,
                "is_visual_claim": True,
            }

    return visual_hits


# ==============================================================================
# CHANNEL 2: Caption & Scene Description Retrieval
# ==============================================================================

def _CaptionDescriptionChannel(
    db: Session,
    query_vector: list[float] | None,
    intent: QueryIntent,
    media_type: str | None,
    added_year: int | None,
    candidate_limit: int = 150,
) -> dict[str, dict]:
    """Retrieve visual descriptions, VLM scene captions, and semantic profiles."""
    caption_hits: dict[str, dict] = {}
    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"

    # Search visual_description segments
    seg_query = (
        select(SearchSegment)
        .join(MediaAsset, MediaAsset.id == SearchSegment.asset_id)
        .where(
            SearchSegment.segment_type == "visual_description",
        )
    )
    if media_type:
        seg_query = seg_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        seg_query = seg_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    if query_vector is not None and is_postgres:
        dist_expr = SearchSegment.embedding.cosine_distance(query_vector)
        rows = db.execute(
            select(SearchSegment, dist_expr.label("distance"))
            .join(MediaAsset, MediaAsset.id == SearchSegment.asset_id)
            .where(
                SearchSegment.segment_type == "visual_description",
                SearchSegment.embedding.is_not(None),
                *([MediaAsset.media_type == media_type] if media_type else []),
                *([MediaAsset.created_at >= _year_bounds(added_year)[0], MediaAsset.created_at < _year_bounds(added_year)[1]] if added_year else []),
            )
            .order_by(dist_expr.asc())
            .limit(candidate_limit)
        ).all()
        for row in rows:
            seg = row[0]
            sim = max(-1.0, min(1.0, 1.0 - float(row[1])))
            caption_hits[seg.asset_id] = {
                "score": round(sim, 4),
                "segment_id": seg.id,
                "segment_type": "visual_description",
                "modality": "visual_description",
                "excerpt": (seg.text_content or "")[:200],
                "is_visual_claim": True,
            }
    else:
        # Lexical and cosine evaluation for caption segments
        segments = db.scalars(seg_query.limit(candidate_limit * 3)).all()
        for seg in segments:
            sim = 0.0
            if query_vector is not None and seg.embedding is not None:
                sim = _cosine_similarity(list(seg.embedding), query_vector)
            text_cov = _token_coverage(seg.text_content or "", intent.content_tokens)
            combined = max(sim, text_cov)
            if combined > 0.20:
                caption_hits[seg.asset_id] = {
                    "score": round(combined, 4),
                    "segment_id": seg.id,
                    "segment_type": "visual_description",
                    "modality": "visual_description",
                    "excerpt": (seg.text_content or "")[:200],
                    "is_visual_claim": True,
                }

    return caption_hits


# ==============================================================================
# CHANNEL 3: Object & Tag Retrieval
# ==============================================================================

def _ObjectAndTagChannel(
    db: Session,
    intent: QueryIntent,
    media_type: str | None,
    added_year: int | None,
) -> dict[str, dict]:
    """Retrieve assets matching query objects, tags, and IIHS visual concepts."""
    object_hits: dict[str, dict] = {}
    content_tokens = intent.content_tokens
    object_terms = intent.object_terms
    if not content_tokens and not object_terms:
        return {}

    asset_query = select(MediaAsset).options(joinedload(MediaAsset.annotations))
    if media_type:
        asset_query = asset_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        asset_query = asset_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    assets = db.scalars(asset_query).unique().all()
    for asset in assets:
        matched_labels = []
        score = 0.0

        # 1. Verified human & model annotations
        for ann in asset.annotations:
            if ann.status in {"accepted", "corrected"}:
                ann_lower = (ann.label or "").lower()
                for token in (object_terms or content_tokens):
                    if _contains_term(ann_lower, token):
                        matched_labels.append(f"{ann.label} (verified)")
                        score += 2.0

        # 2. AI visual analysis suggestions from metadata_json
        meta = asset.metadata_json or {}
        visual = meta.get("visual_analysis") or {}
        ai_objs = visual.get("object_tags") or []
        for obj in ai_objs:
            label = (obj.get("label") if isinstance(obj, dict) else str(obj)).lower()
            conf = float(obj.get("score", 0.7)) if isinstance(obj, dict) else 0.7
            for token in (object_terms or content_tokens):
                if _contains_term(label, token):
                    matched_labels.append(f"{label} (suggested)")
                    score += 1.2 * conf

        if score > 0:
            object_hits[asset.id] = {
                "score": round(min(1.0, score / 4.0), 4),
                "matched_labels": matched_labels[:4],
                "excerpt": "Objects: " + ", ".join(matched_labels[:4]),
                "is_visual_claim": True,
            }

    return object_hits


# ==============================================================================
# CHANNEL 4: OCR & Multi-Format Document Text Retrieval
# ==============================================================================

def _OCRAndDocumentChannel(
    db: Session,
    query_vector: list[float] | None,
    intent: QueryIntent,
    media_type: str | None,
    added_year: int | None,
    candidate_limit: int = 150,
) -> dict[str, dict]:
    """Retrieve text inside OCR, PDF pages, office documents, and transcripts."""
    doc_hits: dict[str, dict] = {}
    content_tokens = intent.content_tokens
    raw_query = intent.normalized

    seg_query = (
        select(SearchSegment)
        .join(MediaAsset, MediaAsset.id == SearchSegment.asset_id)
        .where(
            SearchSegment.segment_type.in_(CONTENT_TEXT_SEGMENT_TYPES),
        )
    )
    if media_type:
        seg_query = seg_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        seg_query = seg_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    segments = db.scalars(seg_query.limit(candidate_limit * 2)).all()
    for seg in segments:
        text = f"{seg.title or ''} {seg.text_content or ''}"
        cov = _token_coverage(text, content_tokens)
        has_phrase = raw_query and _contains_term(text, raw_query)
        if cov >= 0.40 or has_phrase:
            score = 1.0 if has_phrase else round(cov, 4)
            existing = doc_hits.get(seg.asset_id)
            if existing is None or score > existing["score"]:
                doc_hits[seg.asset_id] = {
                    "score": score,
                    "segment_id": seg.id,
                    "segment_type": seg.segment_type,
                    "modality": seg.modality,
                    "excerpt": (seg.text_content or "")[:200],
                    "page_number": seg.page_number,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "is_visual_claim": False,
                }

    # Also check unsegmented metadata_json (text_extraction / transcription)
    asset_query = select(MediaAsset)
    if media_type:
        asset_query = asset_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        asset_query = asset_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    assets = db.scalars(asset_query).all()
    for asset in assets:
        meta = asset.metadata_json or {}
        ocr_text = (meta.get("text_extraction") or {}).get("text") or ""
        transcript_text = (meta.get("transcription") or {}).get("text") or ""
        for raw_text, seg_kind in ((ocr_text, "ocr_text"), (transcript_text, "audio_transcript_window")):
            if not raw_text:
                continue
            cov = _token_coverage(raw_text, content_tokens)
            has_phrase = raw_query and _contains_term(raw_text, raw_query)
            if cov >= 0.40 or has_phrase:
                score = 1.0 if has_phrase else round(cov, 4)
                existing = doc_hits.get(asset.id)
                if existing is None or score > existing["score"]:
                    doc_hits[asset.id] = {
                        "score": score,
                        "segment_id": None,
                        "segment_type": seg_kind,
                        "modality": "ocr" if seg_kind == "ocr_text" else "transcript",
                        "excerpt": raw_text[:200],
                        "page_number": None,
                        "start_ms": None,
                        "end_ms": None,
                        "is_visual_claim": False,
                    }

    return doc_hits


# ==============================================================================
# CHANNEL 5: Metadata & Lexical Retrieval (Path, Filename, Context, Years)
# ==============================================================================

def _MetadataAndLexicalChannel(
    db: Session,
    intent: QueryIntent,
    media_type: str | None,
    added_year: int | None,
) -> dict[str, dict]:
    """Retrieve metadata matches: filenames, folder hierarchy, project names, years."""
    meta_hits: dict[str, dict] = {}
    tokens = intent.content_tokens
    raw_phrase = intent.normalized
    query_years = set(intent.years)
    query_vocab = {v["keyword"].casefold() for v in intent.matched_vocab}

    asset_query = select(MediaAsset)
    if media_type:
        asset_query = asset_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        asset_query = asset_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    assets = db.scalars(asset_query).all()
    for asset in assets:
        meta = asset.metadata_json or {}
        ctx = meta.get("context") or {}
        rel_path = asset.source_relative_key or ""
        filename = asset.original_filename or ""

        ctx_score = 0.0
        reasons = []

        # 1. Exact phrase in filename or folder path
        if raw_phrase and (_contains_term(filename, raw_phrase) or _contains_term(rel_path, raw_phrase)):
            in_fn = _contains_term(filename, raw_phrase)
            ctx_score += 3.0 if in_fn else 1.8
            reasons.append({
                "source": "filename" if in_fn else "folder_context",
                "excerpt": filename if in_fn else rel_path,
                "is_visual_claim": False,
            })

        # 2. Token coverage in path and filename
        matched_tokens = [tok for tok in tokens if _contains_term(rel_path + " " + filename, tok)]
        if matched_tokens:
            coverage = len(matched_tokens) / max(1, len(tokens))
            if coverage >= 0.5:
                ctx_score += 1.5 * (coverage ** 1.5)
                if not reasons:
                    reasons.append({
                        "source": "folder_context",
                        "excerpt": rel_path or filename,
                        "is_visual_claim": False,
                    })

        # 3. Contextual vocabulary overlap
        asset_vocab_kws = {item.get("keyword", "").casefold(): item for item in (ctx.get("matched_keywords") or [])}
        vocab_overlap = query_vocab & set(asset_vocab_kws.keys())
        if vocab_overlap:
            ctx_score += 2.0 * len(vocab_overlap)
            for kw_lower in vocab_overlap:
                kw_info = asset_vocab_kws[kw_lower]
                reasons.append({
                    "source": "iihs_keyword",
                    "excerpt": f"{kw_info.get('keyword')} ({kw_info.get('category')})",
                    "is_visual_claim": False,
                })

        # 4. Temporal year match
        asset_years = set(ctx.get("extracted_years") or [])
        if query_years and (query_years & asset_years):
            ctx_score += 1.5
            reasons.append({
                "source": "temporal_context",
                "excerpt": f"Year {', '.join(str(y) for y in sorted(query_years & asset_years))}",
                "is_visual_claim": False,
            })

        if ctx_score > 0:
            meta_hits[asset.id] = {
                "score": round(ctx_score, 4),
                "evidence": reasons,
                "excerpt": reasons[0]["excerpt"] if reasons else rel_path,
                "is_visual_claim": False,
            }

    return meta_hits


# ==============================================================================
# CHANNEL 6: Authorized Face Biometrics Channel
# ==============================================================================

def _VerifiedFaceChannel(
    db: Session,
    q: str,
    explicit_person_ids: list[str] | None,
    media_type: str | None,
    added_year: int | None,
) -> tuple[dict[str, dict], dict[str, list[dict]], set[str], str]:
    """Evaluate human-verified ('accepted') InsightFace matches for active enrolled people."""
    active_people = db.scalars(
        select(PersonProfile).where(PersonProfile.status == "active")
    ).all()
    active_by_id = {p.id: p for p in active_people}

    required_person_ids: set[str] = set()
    if explicit_person_ids:
        for pid in explicit_person_ids:
            if pid and pid.strip():
                required_person_ids.add(pid.strip())

    remaining_q = q or ""
    if q and q.strip():
        for person in sorted(active_people, key=lambda p: len(p.display_name or ""), reverse=True):
            if person.display_name and _contains_term(q, person.display_name):
                required_person_ids.add(person.id)
                pattern = re.compile(re.escape(person.display_name), re.IGNORECASE)
                remaining_q = pattern.sub(" ", remaining_q)

    if required_person_ids and not required_person_ids.issubset(set(active_by_id.keys())):
        return {}, {}, required_person_ids, remaining_q.strip()

    stmt = (
        select(FaceCandidateMatch, DetectedArchiveFace, PersonProfile)
        .join(DetectedArchiveFace, DetectedArchiveFace.id == FaceCandidateMatch.detected_face_id)
        .join(PersonProfile, PersonProfile.id == FaceCandidateMatch.person_id)
        .join(MediaAsset, MediaAsset.id == FaceCandidateMatch.asset_id)
        .where(
            FaceCandidateMatch.status == "accepted",
            PersonProfile.status == "active",
        )
    )
    if media_type:
        stmt = stmt.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        stmt = stmt.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    rows = db.execute(stmt).all()
    all_verified_faces_by_asset: dict[str, list[dict]] = {}
    per_asset_by_person: dict[str, dict[str, dict]] = {}

    for match, det_face, person in rows:
        face_entry = {
            "match_id": match.id,
            "detected_face_id": det_face.id,
            "person_id": person.id,
            "person_name": person.display_name,
            "aggregate_similarity": round(float(match.aggregate_similarity), 4),
            "max_similarity": round(float(match.max_similarity), 4),
            "bbox": det_face.bbox_json,
            "det_score": round(float(det_face.det_score), 4),
            "frame_id": det_face.frame_id,
            "timestamp_ms": det_face.timestamp_ms,
            "original_file_reference": det_face.original_file_reference,
            "verification_status": match.status,
        }
        all_verified_faces_by_asset.setdefault(match.asset_id, []).append(face_entry)
        person_map = per_asset_by_person.setdefault(match.asset_id, {})
        existing = person_map.get(person.id)
        if existing is None or face_entry["aggregate_similarity"] > existing["aggregate_similarity"]:
            person_map[person.id] = face_entry

    face_hits: dict[str, dict] = {}
    if required_person_ids:
        for asset_id, person_map in per_asset_by_person.items():
            if not required_person_ids.issubset(set(person_map.keys())):
                continue
            matched_entries = [person_map[pid] for pid in required_person_ids]
            matched_entries.sort(key=lambda item: item["aggregate_similarity"], reverse=True)
            avg_sim = sum(item["aggregate_similarity"] for item in matched_entries) / len(matched_entries)
            first_video_entry = next((item for item in matched_entries if item.get("frame_id") or item.get("timestamp_ms") is not None), None)
            face_hits[asset_id] = {
                "score": round(avg_sim, 4),
                "faces": matched_entries,
                "frame_id": first_video_entry["frame_id"] if first_video_entry else None,
                "timestamp_ms": first_video_entry["timestamp_ms"] if first_video_entry else None,
            }

    return face_hits, all_verified_faces_by_asset, required_person_ids, remaining_q.strip()


# ==============================================================================
# INTENT-AWARE MULTIMODAL RANK FUSION
# ==============================================================================

def execute_hybrid_search(
    db: Session,
    q: str = "",
    mode: str = "semantic",
    media_type: str | None = None,
    added_year: int | None = None,
    limit: int = 30,
    person_ids: list[str] | None = None,
) -> list[tuple[MediaAsset, dict]]:
    """Execute intent-classified, channel-isolated multimodal media search."""
    active_people = db.scalars(select(PersonProfile.display_name).where(PersonProfile.status == "active")).all()
    face_hits, all_verified_faces_by_asset, required_person_ids, remaining_q = _VerifiedFaceChannel(
        db, q, person_ids, media_type, added_year
    )

    effective_q = remaining_q if (required_person_ids and remaining_q) else q
    intent = classify_query_intent(effective_q or "", active_people=active_people)

    # In "keyword" mode, force exact/lexical priority
    if mode == "keyword":
        intent.channel_weights["metadata_lexical"] = max(2.0, intent.channel_weights.get("metadata_lexical", 1.0))
        intent.channel_weights["ocr_text"] = max(1.8, intent.channel_weights.get("ocr_text", 1.0))
        intent.channel_weights["visual_embedding"] = 0.0

    # 1. Retrieve across isolated channels
    query_vector = None
    if mode in {"semantic", "hybrid"} and intent.channel_weights.get("visual_embedding", 0) > 0 and (effective_q or "").strip():
        try:
            query_vector, model_id, model_revision = text_embedding(effective_q)
        except MediaValidationError:
            if mode == "semantic" and not required_person_ids:
                raise
            query_vector = None

    visual_hits = {}
    if query_vector is not None and intent.channel_weights.get("visual_embedding", 0) > 0:
        visual_hits = _VisualEmbeddingChannel(
            db, query_vector, model_id, model_revision, media_type, added_year, candidate_limit=max(100, limit * 3)
        )

    caption_hits = {}
    if intent.channel_weights.get("caption_description", 0) > 0 and (effective_q or "").strip():
        caption_hits = _CaptionDescriptionChannel(
            db, query_vector, intent, media_type, added_year, candidate_limit=max(60, limit * 2)
        )

    object_hits = {}
    if intent.channel_weights.get("object_tag", 0) > 0 and (effective_q or "").strip():
        object_hits = _ObjectAndTagChannel(db, intent, media_type, added_year)

    doc_hits = {}
    if intent.channel_weights.get("ocr_text", 0) > 0 and (effective_q or "").strip():
        doc_hits = _OCRAndDocumentChannel(db, query_vector, intent, media_type, added_year)

    meta_hits = {}
    if intent.channel_weights.get("metadata_lexical", 0) > 0 and (effective_q or "").strip():
        meta_hits = _MetadataAndLexicalChannel(db, intent, media_type, added_year)

    # 2. Query-relative z-gating for visual embedding matches
    visual_order = sorted(visual_hits, key=lambda aid: visual_hits[aid]["score"], reverse=True)
    visual_scores = [visual_hits[aid]["score"] for aid in visual_order]
    visual_accepted = _semantic_gate(visual_scores)
    gated_visual_ids = [aid for aid, ok in zip(visual_order, visual_accepted) if ok]

    # 3. Intent-Aware Candidate Qualification
    # For visual_description / object_search queries, candidate MUST have visual/caption/object evidence.
    # Metadata/folder alone CANNOT qualify an asset for a visual description query.
    if required_person_ids:
        candidate_ids = set(face_hits.keys())
        has_additional = bool(intent.content_tokens or intent.years)
        if has_additional and candidate_ids:
            other_ids = set(gated_visual_ids) | set(caption_hits.keys()) | set(object_hits.keys()) | set(meta_hits.keys())
            candidate_ids &= other_ids
    elif intent.intent_type in {"visual_description", "scene_activity"}:
        # Visual evidence is mandatory
        candidate_ids = set(gated_visual_ids) | set(caption_hits.keys()) | set(object_hits.keys())
    elif intent.intent_type == "object_search":
        candidate_ids = set(object_hits.keys()) | set(gated_visual_ids) | set(caption_hits.keys())
    elif intent.intent_type == "exact_identifier":
        candidate_ids = set(meta_hits.keys()) | set(doc_hits.keys())
    elif intent.intent_type == "ocr_document":
        candidate_ids = set(doc_hits.keys()) | set(caption_hits.keys())
    else:
        # Mixed or general query
        candidate_ids = set(gated_visual_ids) | set(caption_hits.keys()) | set(object_hits.keys()) | set(doc_hits.keys()) | set(meta_hits.keys())

    if not candidate_ids:
        return []

    assets = db.scalars(
        select(MediaAsset).options(joinedload(MediaAsset.annotations)).where(MediaAsset.id.in_(candidate_ids))
    ).unique().all()
    by_id = {a.id: a for a in assets}

    # Channel ranks for Reciprocal Rank Fusion
    visual_pos = {aid: rank for rank, aid in enumerate(gated_visual_ids, start=1)}
    caption_pos = {aid: rank for rank, aid in enumerate(sorted(caption_hits, key=lambda a: caption_hits[a]["score"], reverse=True), start=1)}
    object_pos = {aid: rank for rank, aid in enumerate(sorted(object_hits, key=lambda a: object_hits[a]["score"], reverse=True), start=1)}
    doc_pos = {aid: rank for rank, aid in enumerate(sorted(doc_hits, key=lambda a: doc_hits[a]["score"], reverse=True), start=1)}
    meta_pos = {aid: rank for rank, aid in enumerate(sorted(meta_hits, key=lambda a: meta_hits[a]["score"], reverse=True), start=1)}
    face_pos = {aid: rank for rank, aid in enumerate(sorted(face_hits, key=lambda a: face_hits[a]["score"], reverse=True), start=1)}

    w = intent.channel_weights
    fused_results: list[tuple[float, str, dict]] = []

    for aid in candidate_ids:
        asset = by_id.get(aid)
        if asset is None:
            continue

        score = 0.0
        evidence_list: list[dict] = []
        pill_text = "Match"
        is_visual_hit = False

        # Channel 6: Face biometrics
        if aid in face_pos:
            fhit = face_hits[aid]
            score += w.get("face_biometrics", 2.0) * (1.0 / (RRF_K + face_pos[aid])) + 0.02 * fhit["score"]
            names = ", ".join(f["person_name"] for f in fhit["faces"])
            pill_text = f"Verified: {names}"
            is_visual_hit = True
            evidence_list.append({
                "source": "verified_face_match",
                "modality": "face_recognition",
                "score": fhit["score"],
                "excerpt": f"Verified person: {names}",
                "frame_id": fhit["frame_id"],
                "start_ms": fhit["timestamp_ms"],
                "end_ms": fhit["timestamp_ms"],
                "is_visual_claim": True,
            })

        # Channel 1: Visual embedding
        if aid in visual_pos:
            vhit = visual_hits[aid]
            score += w.get("visual_embedding", 1.5) * (1.0 / (RRF_K + visual_pos[aid])) + 0.015 * vhit["score"]
            is_visual_hit = True
            if pill_text == "Match":
                pill_text = f"Visual similarity {round(vhit['score'] * 100)}%"
            evidence_list.append({
                "source": vhit["segment_type"],
                "modality": vhit["modality"],
                "score": vhit["score"],
                "excerpt": vhit["excerpt"],
                "frame_id": vhit["frame_id"],
                "start_ms": vhit["start_ms"],
                "end_ms": vhit["end_ms"],
                "is_visual_claim": True,
            })

        # Channel 2: Caption & scene description
        if aid in caption_pos:
            chit = caption_hits[aid]
            score += w.get("caption_description", 1.4) * (1.0 / (RRF_K + caption_pos[aid])) + 0.012 * chit["score"]
            is_visual_hit = True
            if pill_text == "Match":
                pill_text = "Scene description match"
            evidence_list.append({
                "source": "visual_description",
                "modality": "caption",
                "score": chit["score"],
                "excerpt": chit["excerpt"],
                "is_visual_claim": True,
            })

        # Channel 3: Object & tag
        if aid in object_pos:
            ohit = object_hits[aid]
            score += w.get("object_tag", 1.2) * (1.0 / (RRF_K + object_pos[aid])) + 0.010 * ohit["score"]
            is_visual_hit = True
            if pill_text == "Match":
                pill_text = ohit["excerpt"]
            evidence_list.append({
                "source": "object_tags",
                "modality": "objects",
                "score": ohit["score"],
                "excerpt": ohit["excerpt"],
                "is_visual_claim": True,
            })

        # Channel 4: OCR & Document
        if aid in doc_pos:
            dhit = doc_hits[aid]
            score += w.get("ocr_text", 1.0) * (1.0 / (RRF_K + doc_pos[aid])) + 0.008 * dhit["score"]
            if pill_text == "Match":
                pill_text = "OCR / Document match"
            evidence_list.append({
                "source": dhit["segment_type"],
                "modality": dhit["modality"],
                "score": dhit["score"],
                "excerpt": dhit["excerpt"],
                "page_number": dhit.get("page_number"),
                "start_ms": dhit.get("start_ms"),
                "end_ms": dhit.get("end_ms"),
                "is_visual_claim": False,
            })

        # Channel 5: Metadata & Lexical
        if aid in meta_pos:
            mhit = meta_hits[aid]
            score += w.get("metadata_lexical", 0.1) * (1.0 / (RRF_K + meta_pos[aid])) + 0.004 * min(6.0, mhit["score"])
            if pill_text == "Match":
                pill_text = "Filename / Folder match"
            evidence_list.extend(mhit["evidence"])

        # Modality preference boost if query explicitly requested images/videos/docs
        if intent.modality_hint and asset.media_type == intent.modality_hint and not media_type:
            score *= 1.20

        matched_frame_id = None
        matched_timestamp_ms = None
        matched_timestamp_end_ms = None
        matched_page = None
        for ev in evidence_list:
            if ev.get("frame_id") and matched_frame_id is None:
                matched_frame_id = ev["frame_id"]
                matched_timestamp_ms = ev.get("start_ms")
            if ev.get("start_ms") is not None and matched_timestamp_ms is None:
                matched_timestamp_ms = ev["start_ms"]
                matched_timestamp_end_ms = ev.get("end_ms")
            if ev.get("page_number") is not None and matched_page is None:
                matched_page = ev["page_number"]

        ctx_meta = (asset.metadata_json or {}).get("context") or {}
        payload = {
            "search_score": round(min(1.0, score * 24.0), 4),
            "semantic_score": visual_hits.get(aid, {}).get("score", round(min(1.0, score * 24.0), 4)),
            "evidence_pill": pill_text,
            "intent_type": intent.intent_type,
            "is_visual_claim": is_visual_hit,
            "matched_evidence": evidence_list[:5],
            "source_path": asset.source_relative_key,
            "project_context": {
                "inferred_project": ctx_meta.get("inferred_project"),
                "folder_hierarchy": ctx_meta.get("folder_hierarchy", []),
                "matched_keywords": [k.get("keyword") for k in (ctx_meta.get("matched_keywords") or [])],
                "matched_categories": ctx_meta.get("matched_categories", []),
                "extracted_years": ctx_meta.get("extracted_years", []),
                "related_group_key": ctx_meta.get("related_group_key"),
                "is_visual_claim": False,
            },
        }
        if matched_frame_id is not None:
            payload["matched_frame_id"] = matched_frame_id
        if matched_timestamp_ms is not None:
            payload["matched_timestamp_ms"] = int(matched_timestamp_ms)
            payload["matched_timestamp_start_ms"] = int(matched_timestamp_ms)
        if matched_timestamp_end_ms is not None:
            payload["matched_timestamp_end_ms"] = int(matched_timestamp_end_ms)
        if matched_page is not None:
            payload["matched_page"] = int(matched_page)
        if aid in face_hits:
            payload["matched_faces"] = face_hits[aid]["faces"]
        elif aid in all_verified_faces_by_asset:
            payload["matched_faces"] = all_verified_faces_by_asset[aid]

        fused_results.append((score, aid, payload))

    fused_results.sort(key=lambda item: item[0], reverse=True)

    # Ratio gating (DigUp strong vs weaker matches, ratio = 0.5)
    top_score = fused_results[0][0] if fused_results else 0.0
    for s, _aid, pl in fused_results:
        pl["match_tier"] = "strong" if (top_score > 0 and s >= top_score * 0.50) else "weaker"

    return [(by_id[aid], payload) for _score, aid, payload in fused_results[:limit]]
