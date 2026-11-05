"""Integrated Hybrid Semantic + Lexical Search Pipeline with Reciprocal Rank Fusion (RRF).

Combines:
1. Semantic vector retrieval (SearchSegment + AssetEmbedding fallback)
2. PostgreSQL full-text / lexical keyword retrieval
3. Filename and folder path matching
4. IIHS controlled vocabulary and alias matching
5. OCR and multi-format document segment retrieval (page/sheet/slide/chunk)
6. Visual tags, annotations and visual descriptions
7. Timestamped audio/video transcript retrieval
8. Sampled video frame retrieval with timestamp localization

Enforces the IIHS archival rule: folder-derived contextual metadata is never treated
as visual detection or proof of visual content.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
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
from app.services.contextual_metadata import _contains_term, _match_item_in_text, _normalize_text, load_vocabulary
from app.services.embeddings import text_embedding
from app.services.media import MediaValidationError

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
    """Extract query tokens, years, IIHS vocabulary terms, and modality hints."""
    norm = _normalize_text(query)
    raw_tokens = [t for t in norm.split() if t]
    content_tokens = [t for t in raw_tokens if t not in STOPWORDS and len(t) >= 2]
    if not content_tokens:
        content_tokens = raw_tokens

    years = sorted({int(m.group(1)) for m in re.finditer(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)", query)})

    matched_vocab = []
    for item in load_vocabulary():
        kw = item["keyword"]
        hit, via_alias = _match_item_in_text(query, kw)
        if hit:
            matched_vocab.append({"keyword": kw, "category": item["category"], "via_alias": via_alias})

    modality_hint = None
    if re.search(r"\b(images?|photos?|photographs?|pictures?)\b", norm):
        modality_hint = "image"
    elif re.search(r"\b(videos?|clips?|footage|movies?)\b", norm):
        modality_hint = "video"
    elif re.search(r"\b(documents?|pdfs?|reports?|spreadsheets?|slides?|presentations?)\b", norm):
        modality_hint = "document"
    elif re.search(r"\b(audio|recordings?|podcasts?|interviews?)\b", norm):
        modality_hint = "audio"

    return {
        "raw": query.strip(),
        "normalized": norm,
        "tokens": content_tokens,
        "years": years,
        "matched_vocab": matched_vocab,
        "modality_hint": modality_hint,
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
    """Detect likely archive identifiers, filenames and codes where lexical precision matters."""
    q = (query or "").strip()
    return bool(q and (re.search(r"(?i)\b[A-Z]{2,}[-_/]?\d{2,}\b", q)
                       or re.search(r"\b\d{4,}\b", q)
                       or re.search(r"\.[A-Za-z0-9]{2,5}$", q)))


def _year_bounds(year: int | None):
    if year is None:
        return None
    return datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)


def _SemanticChannel(
    db: Session,
    query_vector: list[float],
    model_id: str,
    model_revision: str,
    media_type: str | None,
    added_year: int | None,
    candidate_limit: int = 200,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (content_semantic_hits, context_semantic_hits) keyed by asset_id."""
    content_hits: dict[str, dict] = {}
    context_hits: dict[str, dict] = {}
    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"

    # 1. Search persistent SearchSegment rows
    seg_query = select(SearchSegment).join(MediaAsset, MediaAsset.id == SearchSegment.asset_id).where(
        SearchSegment.embedding.is_not(None),
        SearchSegment.model_id == model_id,
        SearchSegment.model_revision == model_revision,
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
                SearchSegment.model_id == model_id,
                SearchSegment.model_revision == model_revision,
                *([MediaAsset.media_type == media_type] if media_type else []),
                *([MediaAsset.created_at >= _year_bounds(added_year)[0], MediaAsset.created_at < _year_bounds(added_year)[1]] if added_year else []),
            )
            .order_by(dist_expr.asc())
            .limit(candidate_limit * 3)
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
        target = context_hits if seg.segment_type == "contextual_metadata" or seg.modality == "context" else content_hits
        existing = target.get(seg.asset_id)
        if existing is None or sim > existing["score"]:
            target[seg.asset_id] = {
                "score": round(sim, 4),
                "segment_id": seg.id,
                "segment_type": seg.segment_type,
                "modality": seg.modality,
                "title": seg.title,
                "excerpt": (seg.text_content or "")[:240] or None,
                "page_number": seg.page_number,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "frame_id": seg.frame_id,
                "is_visual_claim": seg.segment_type in VISUAL_SEGMENT_TYPES,
            }

    # 2. Fallback/supplement from AssetEmbedding for any assets without SearchSegment rows
    emb_query = select(AssetEmbedding).join(MediaAsset, MediaAsset.id == AssetEmbedding.asset_id).where(
        AssetEmbedding.model_id == model_id,
        AssetEmbedding.model_revision == model_revision,
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
        target = context_hits if emb.modality == "context" else content_hits
        existing = target.get(emb.asset_id)
        frame = frames_by_id.get(emb.frame_id) if emb.frame_id else None
        if existing is None or sim > existing["score"]:
            target[emb.asset_id] = {
                "score": round(sim, 4),
                "segment_id": None,
                "segment_type": "video_frame" if frame else ("contextual_metadata" if emb.modality == "context" else "image_visual"),
                "modality": emb.modality,
                "title": None,
                "excerpt": None,
                "page_number": None,
                "start_ms": frame.timestamp_ms if frame else None,
                "end_ms": frame.timestamp_ms if frame else None,
                "frame_id": frame.id if frame else None,
                "is_visual_claim": emb.modality in {"image", "image_frame"},
            }
        elif frame and existing.get("frame_id") is None and emb.modality == "image_frame":
            existing["frame_id"] = frame.id
            existing["start_ms"] = frame.timestamp_ms

    return content_hits, context_hits


def _LexicalAndContextChannels(
    db: Session,
    intent: dict,
    media_type: str | None,
    added_year: int | None,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Evaluate lexical content matches (OCR, docs, transcripts, tags) and contextual matches (path, filename, IIHS vocab, years)."""
    asset_query = select(MediaAsset).options(joinedload(MediaAsset.annotations), joinedload(MediaAsset.segments))
    if media_type:
        asset_query = asset_query.where(MediaAsset.media_type == media_type)
    if bounds := _year_bounds(added_year):
        asset_query = asset_query.where(MediaAsset.created_at >= bounds[0], MediaAsset.created_at < bounds[1])

    assets = db.scalars(asset_query).unique().all()
    lexical_hits: dict[str, dict] = {}
    context_hits: dict[str, dict] = {}

    tokens = intent["tokens"]
    raw_phrase = intent["normalized"]
    query_years = set(intent["years"])
    query_vocab = {v["keyword"].casefold() for v in intent["matched_vocab"]}

    for asset in assets:
        meta = asset.metadata_json or {}
        ctx = meta.get("context") or {}
        rel_path = asset.source_relative_key or ""
        filename = asset.original_filename or ""

        # --- Context / Path / Filename / IIHS Vocab / Year evaluation ---
        ctx_score = 0.0
        ctx_reasons: list[dict] = []

        if raw_phrase and (_contains_term(filename, raw_phrase) or _contains_term(rel_path, raw_phrase)):
            in_fn = _contains_term(filename, raw_phrase)
            ctx_score += 3.0 if in_fn else 2.2
            ctx_reasons.append({
                "source": "filename" if in_fn else "folder_context",
                "modality": "path",
                "score": 0.95 if in_fn else 0.85,
                "excerpt": filename if in_fn else rel_path,
                "is_visual_claim": False,
            })

        matched_path_tokens = [tok for tok in tokens if _contains_term(rel_path + " " + filename, tok)]
        if matched_path_tokens:
            coverage = len(matched_path_tokens) / max(1, len(tokens))
            ctx_score += 1.8 * coverage
            if not ctx_reasons:
                ctx_reasons.append({
                    "source": "folder_context",
                    "modality": "path",
                    "score": round(0.75 * coverage, 4),
                    "excerpt": rel_path or filename,
                    "is_visual_claim": False,
                })

        asset_vocab_kws = {item.get("keyword", "").casefold(): item for item in (ctx.get("matched_keywords") or [])}
        vocab_overlap = query_vocab & set(asset_vocab_kws.keys())
        if vocab_overlap:
            ctx_score += 2.5 * len(vocab_overlap)
            for kw_lower in vocab_overlap:
                kw_info = asset_vocab_kws[kw_lower]
                ctx_reasons.append({
                    "source": "iihs_keyword",
                    "modality": "contextual_metadata",
                    "score": float(kw_info.get("confidence", 0.9)),
                    "excerpt": f"{kw_info.get('keyword')} ({kw_info.get('category')})",
                    "is_visual_claim": False,
                })

        # Ingestion timestamp is not the date depicted or described by the asset.
        asset_years = set(ctx.get("extracted_years") or [])
        if query_years and (query_years & asset_years):
            ctx_score += 1.5
            ctx_reasons.append({
                "source": "temporal_context",
                "modality": "year",
                "score": 0.9,
                "excerpt": f"Year {', '.join(str(y) for y in sorted(query_years & asset_years))}",
                "is_visual_claim": False,
            })

        if ctx_score > 0:
            context_hits[asset.id] = {
                "score": round(ctx_score, 4),
                "evidence": ctx_reasons,
            }

        # --- Lexical Content Evaluation (Segments, OCR, Docs, Transcripts, Annotations) ---
        lex_score = 0.0
        best_segment_evidence: dict | None = None

        # Check human & model annotations / visual tags
        accepted_labels = [a.label for a in asset.annotations if a.status in {"accepted", "corrected"}]
        for label in accepted_labels:
            if (raw_phrase and _contains_term(label, raw_phrase)) or _token_coverage(label, tokens) >= 0.5:
                lex_score += 2.5
                best_segment_evidence = {
                    "source": "visual_tag",
                    "segment_type": "visual_description",
                    "modality": "annotation",
                    "score": 0.95,
                    "excerpt": f"Tag: {label}",
                    "page_number": None,
                    "start_ms": None,
                    "end_ms": None,
                    "frame_id": None,
                    "is_visual_claim": True,
                }

        # Check persistent SearchSegments on the asset
        for seg in asset.segments:
            if seg.segment_type == "contextual_metadata":
                continue
            seg_text = f"{seg.title or ''} {seg.text_content or ''}".strip()
            if not seg_text:
                continue
            seg_hit_score = 0.0
            if raw_phrase and _contains_term(seg_text, raw_phrase):
                seg_hit_score += 3.5
            coverage = _token_coverage(seg_text, tokens)
            if coverage >= 0.5:
                seg_hit_score += 2.2 * coverage
            if seg_hit_score > 0:
                if seg_hit_score > lex_score or best_segment_evidence is None:
                    best_segment_evidence = {
                        "source": seg.segment_type,
                        "segment_type": seg.segment_type,
                        "modality": seg.modality,
                        "score": round(min(1.0, seg_hit_score / 4.0), 4),
                        "excerpt": (seg.text_content or seg.title or "")[:240],
                        "page_number": seg.page_number,
                        "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms,
                        "frame_id": seg.frame_id,
                        "is_visual_claim": seg.segment_type in VISUAL_SEGMENT_TYPES,
                    }
                lex_score = max(lex_score, seg_hit_score)

        # Also check metadata_json text_extraction / transcription / visual_analysis if segments were not yet populated
        for meta_field, seg_kind, mod_name in (
            ((meta.get("text_extraction") or {}).get("text") or "", "ocr_text" if asset.media_type == "image" else "office_chunk", "ocr" if asset.media_type == "image" else "document"),
            ((meta.get("transcription") or {}).get("text") or "", "audio_transcript_window", "transcript"),
            ((meta.get("transcription") or {}).get("english_translation") or "", "audio_transcript_window", "translation"),
            ((meta.get("visual_analysis") or {}).get("description") or "", "visual_description", "visual_description"),
        ):
            if not meta_field:
                continue
            m_score = 0.0
            if raw_phrase and _contains_term(meta_field, raw_phrase):
                m_score += 3.0
            coverage = _token_coverage(meta_field, tokens)
            if coverage >= 0.5:
                m_score += 2.0 * coverage
            if m_score > lex_score:
                lex_score = m_score
                if best_segment_evidence is None:
                    best_segment_evidence = {
                        "source": seg_kind,
                        "segment_type": seg_kind,
                        "modality": mod_name,
                        "score": round(min(1.0, m_score / 4.0), 4),
                        "excerpt": meta_field[:240],
                        "page_number": None,
                        "start_ms": None,
                        "end_ms": None,
                        "frame_id": None,
                        "is_visual_claim": seg_kind == "visual_description",
                    }

        if lex_score > 0 and best_segment_evidence is not None:
            lexical_hits[asset.id] = {
                "score": round(lex_score, 4),
                "evidence": best_segment_evidence,
            }

    return lexical_hits, context_hits


def _VerifiedFaceChannel(
    db: Session,
    q: str,
    explicit_person_ids: list[str] | None,
    media_type: str | None,
    added_year: int | None,
) -> tuple[dict[str, dict], dict[str, list[dict]], set[str], str]:
    """Evaluate human-verified ('accepted') InsightFace matches for active enrolled people.

    Returns:
        (face_hits, all_verified_faces_by_asset, required_person_ids, remaining_query_text)
    """
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
        # Match active enrolled person display names in query text (longest names first)
        for person in sorted(active_people, key=lambda p: len(p.display_name or ""), reverse=True):
            if person.display_name and _contains_term(q, person.display_name):
                required_person_ids.add(person.id)
                pattern = re.compile(re.escape(person.display_name), re.IGNORECASE)
                remaining_q = pattern.sub(" ", remaining_q)

    # If any explicitly requested person_id is not an active PersonProfile, no asset can satisfy co-occurrence
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


def execute_hybrid_search(
    db: Session,
    q: str = "",
    mode: str = "semantic",
    media_type: str | None = None,
    added_year: int | None = None,
    limit: int = 30,
    person_ids: list[str] | None = None,
) -> list[tuple[MediaAsset, dict]]:
    """Run integrated RRF hybrid search and return ranked (MediaAsset, search_metadata) pairs."""
    face_hits, all_verified_faces_by_asset, required_person_ids, remaining_q = _VerifiedFaceChannel(
        db, q, person_ids, media_type, added_year
    )

    effective_q = remaining_q if (required_person_ids and remaining_q) else q
    intent = parse_query_intent(effective_q or "")
    remaining_intent = parse_query_intent(remaining_q) if required_person_ids else intent
    meaningful_remaining_tokens = [t for t in remaining_intent["tokens"] if t not in STOPWORDS]

    content_sem_hits: dict[str, dict] = {}
    context_sem_hits: dict[str, dict] = {}

    if mode in {"semantic", "hybrid"} and (effective_q or "").strip():
        try:
            query_vector, model_id, model_revision = text_embedding(effective_q)
            content_sem_hits, context_sem_hits = _SemanticChannel(
                db, query_vector, model_id, model_revision, media_type, added_year, candidate_limit=max(100, limit * 3)
            )
        except MediaValidationError:
            if mode == "semantic" and not required_person_ids:
                # Preserve existing contract when caller explicitly requires semantic bridge and no fallback is requested
                raise

    lexical_hits: dict[str, dict] = {}
    context_lex_hits: dict[str, dict] = {}
    if (effective_q or "").strip() and (not required_person_ids or meaningful_remaining_tokens or remaining_intent["years"] or remaining_intent["matched_vocab"]):
        lexical_hits, context_lex_hits = _LexicalAndContextChannels(
            db, remaining_intent if required_person_ids else intent, media_type, added_year
        )

    # Build ranked lists per channel for Reciprocal Rank Fusion (RRF)
    semantic_order = sorted(content_sem_hits, key=lambda aid: content_sem_hits[aid]["score"], reverse=True)
    semantic_scores = [content_sem_hits[aid]["score"] for aid in semantic_order]
    semantic_accepted = _semantic_gate(semantic_scores)
    content_sem_ranked = [
        aid for aid, accepted in zip(semantic_order, semantic_accepted) if accepted
    ]
    context_sem_ranked = [
        aid for aid, _ in sorted(context_sem_hits.items(), key=lambda x: x[1]["score"], reverse=True)
        if context_sem_hits[aid]["score"] > 0.15
    ]
    lexical_ranked = [
        aid for aid, _ in sorted(lexical_hits.items(), key=lambda x: x[1]["score"], reverse=True)
    ]
    context_lex_ranked = [
        aid for aid, _ in sorted(context_lex_hits.items(), key=lambda x: x[1]["score"], reverse=True)
    ]
    face_ranked = [
        aid for aid, _ in sorted(face_hits.items(), key=lambda x: x[1]["score"], reverse=True)
    ]

    if required_person_ids:
        # Strict identity & co-occurrence enforcement: only verified ('accepted') matches for all required people qualify
        candidate_ids = set(face_ranked)
        has_additional_constraints = bool(
            meaningful_remaining_tokens or remaining_intent["years"] or remaining_intent["matched_vocab"]
        )
        if has_additional_constraints and candidate_ids:
            other_channel_ids = set(lexical_ranked) | set(context_lex_ranked)
            if other_channel_ids:
                candidate_ids &= other_channel_ids
            elif mode in {"semantic", "hybrid"} and (content_sem_ranked or context_sem_ranked):
                candidate_ids &= (set(content_sem_ranked) | set(context_sem_ranked))
            else:
                candidate_ids = set()
    else:
        candidate_ids = set(lexical_ranked) | set(context_lex_ranked) | set(face_ranked)
        if mode in {"semantic", "hybrid"}:
            candidate_ids |= set(content_sem_ranked) | set(context_sem_ranked)

    if not candidate_ids:
        return []

    assets = db.scalars(
        select(MediaAsset)
        .options(joinedload(MediaAsset.annotations))
        .where(MediaAsset.id.in_(candidate_ids))
    ).unique().all()
    by_id = {a.id: a for a in assets}

    content_sem_pos = {aid: rank for rank, aid in enumerate(content_sem_ranked, start=1)}
    context_sem_pos = {aid: rank for rank, aid in enumerate(context_sem_ranked, start=1)}
    lexical_pos = {aid: rank for rank, aid in enumerate(lexical_ranked, start=1)}
    context_lex_pos = {aid: rank for rank, aid in enumerate(context_lex_ranked, start=1)}
    face_pos = {aid: rank for rank, aid in enumerate(face_ranked, start=1)}

    fused_results: list[tuple[float, str, dict]] = []
    for aid in candidate_ids:
        asset = by_id.get(aid)
        if asset is None:
            continue

        rrf_score = 0.0
        evidence_list: list[dict] = []

        if aid in face_pos:
            fhit = face_hits[aid]
            rrf_score += (1.55 / (RRF_K + face_pos[aid])) + (0.02 * max(0.0, fhit["score"]))
            evidence_list.append({
                "source": "verified_face_match",
                "modality": "face_recognition",
                "score": fhit["score"],
                "excerpt": "Verified person: " + ", ".join(f["person_name"] for f in fhit["faces"]),
                "page_number": None,
                "start_ms": fhit["timestamp_ms"],
                "end_ms": fhit["timestamp_ms"],
                "frame_id": fhit["frame_id"],
                "is_visual_claim": True,
            })

        if aid in content_sem_pos:
            hit = content_sem_hits[aid]
            # Visual semantics remain primary for descriptive queries. Exact identifiers
            # deliberately favour lexical evidence, as in DigUp.
            semantic_weight = 0.65 if _is_identifier_query(effective_q) else 1.35
            direct_weight = 0.008 if _is_identifier_query(effective_q) else 0.015
            rrf_score += (semantic_weight / (RRF_K + content_sem_pos[aid])) + (direct_weight * max(0.0, hit["score"]))
            evidence_list.append({
                "source": hit["segment_type"],
                "modality": hit["modality"],
                "score": hit["score"],
                "excerpt": hit["excerpt"],
                "page_number": hit["page_number"],
                "start_ms": hit["start_ms"],
                "end_ms": hit["end_ms"],
                "frame_id": hit["frame_id"],
                "is_visual_claim": hit["is_visual_claim"],
            })

        if aid in lexical_pos:
            lhit = lexical_hits[aid]
            lex_quality = min(1.0, lhit["score"] / 2.2)
            lexical_weight = 1.75 if _is_identifier_query(effective_q) else 1.25
            lexical_direct = 0.009 if _is_identifier_query(effective_q) else 0.006
            rrf_score += (lexical_weight * lex_quality) / (RRF_K + lexical_pos[aid]) + (lexical_direct * min(6.0, lhit["score"]))
            evidence_list.append(lhit["evidence"])

        if aid in context_lex_pos:
            chit = context_lex_hits[aid]
            ctx_quality = min(1.0, chit["score"] / 2.2)
            # Folder/filename/IIHS vocabulary match boosts relevance without claiming visual content
            rrf_score += (1.25 * ctx_quality) / (RRF_K + context_lex_pos[aid]) + (0.006 * min(8.0, chit["score"]))
            evidence_list.extend(chit["evidence"])

        if aid in context_sem_pos:
            cshit = context_sem_hits[aid]
            # Bounded weight for context embedding so folder names alone don't outrank strong visual/lexical evidence
            rrf_score += (0.55 / (RRF_K + context_sem_pos[aid])) + (0.005 * max(0.0, cshit["score"]))
            if aid not in context_lex_pos:
                evidence_list.append({
                    "source": "folder_context_semantic",
                    "modality": "contextual_metadata",
                    "score": cshit["score"],
                    "excerpt": cshit["excerpt"],
                    "page_number": None,
                    "start_ms": None,
                    "end_ms": None,
                    "frame_id": None,
                    "is_visual_claim": False,
                })

        # Apply subtle modality preference boost if user explicitly asked for "images", "documents", "videos", "audio"
        if intent["modality_hint"] and asset.media_type == intent["modality_hint"] and not media_type:
            rrf_score *= 1.18

        # Determine best location metadata (page, frame, timestamp) across matched evidence
        matched_frame_id = None
        matched_timestamp_ms = None
        matched_timestamp_end_ms = None
        matched_frame_score = None
        matched_page = None

        for ev in evidence_list:
            if ev.get("frame_id") and matched_frame_id is None:
                matched_frame_id = ev["frame_id"]
                matched_timestamp_ms = ev.get("start_ms")
                matched_frame_score = ev.get("score")
            if ev.get("start_ms") is not None and matched_timestamp_ms is None:
                matched_timestamp_ms = ev["start_ms"]
                matched_timestamp_end_ms = ev.get("end_ms")
            if ev.get("page_number") is not None and matched_page is None:
                matched_page = ev["page_number"]

        # Also check best video frame if this is a video matched semantically
        if asset.media_type == "video" and matched_frame_id is None and aid in content_sem_hits:
            c_hit = content_sem_hits[aid]
            if c_hit.get("frame_id"):
                matched_frame_id = c_hit["frame_id"]
                matched_timestamp_ms = c_hit["start_ms"]
                matched_frame_score = c_hit["score"]

        sem_score = (
            content_sem_hits[aid]["score"]
            if aid in content_sem_hits
            else (context_sem_hits[aid]["score"] if aid in context_sem_hits else None)
        )
        ctx_meta = (asset.metadata_json or {}).get("context") or {}

        payload = {
            "search_score": round(min(1.0, rrf_score * 22.0), 4),
            "semantic_score": sem_score if sem_score is not None else round(min(1.0, rrf_score * 22.0), 4),
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
        if matched_frame_score is not None:
            payload["matched_frame_score"] = matched_frame_score
        if matched_page is not None:
            payload["matched_page"] = int(matched_page)
        if aid in face_hits:
            payload["matched_faces"] = face_hits[aid]["faces"]
        elif aid in all_verified_faces_by_asset:
            payload["matched_faces"] = all_verified_faces_by_asset[aid]

        fused_results.append((rrf_score, aid, payload))

    fused_results.sort(key=lambda item: (item[0], item[2].get("semantic_score") or 0.0, item[1]), reverse=True)
    return [(by_id[aid], payload) for _score, aid, payload in fused_results[:limit]]
