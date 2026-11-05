"""Query Intent Classification and Retrieval Channel Weight Calibration.

Analyzes natural language queries and categorizes them into distinct archival intents:
- visual_description: visual scenes, arrangements, activities, appearance
- object_search: specific objects or object combinations
- scene_activity: events, workshops, discussions, field work
- person_search: specific people / face biometrics
- exact_identifier: filenames, asset IDs, camera sequence codes
- ocr_document: text inside documents, signboards, slides
- mixed: semantic query constrained by temporal/project metadata
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.services.contextual_metadata import (
    _contains_term,
    _match_item_in_text,
    _normalize_text,
    load_vocabulary,
)

QueryIntentType = Literal[
    "visual_description",
    "object_search",
    "scene_activity",
    "person_search",
    "exact_identifier",
    "ocr_document",
    "mixed",
]

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "near", "of", "on", "or", "show", "showing", "that", "the", "to",
    "with", "files", "file", "associated", "particular", "mentioning", "activities",
    "images", "image", "photos", "photo", "photographs", "videos", "video",
    "documents", "document", "audio", "recordings", "picture", "pictures",
    "find", "look", "search", "get", "all", "some", "any",
}

# Spatial relations, actions, and scene descriptors that indicate visual scene understanding
VISUAL_ACTION_INDICATORS = {
    "sitting", "standing", "walking", "running", "discussing", "working",
    "planting", "wearing", "surrounded", "under", "beside", "near", "around",
    "outdoor", "indoor", "aerial", "elevated", "background", "foreground",
    "holding", "gathering", "participating", "attending", "talking", "eating",
    "pointing", "looking", "facing", "entering", "leaving", "building",
    "constructing", "group", "crowd", "arranged", "clustered", "view",
    "containing", "different", "types", "appearance", "depicting", "showing",
    "meeting", "conference", "workshop", "discussion", "event", "session",
    "inside", "room", "hall", "office", "taking", "place", "activities",
    "parked", "parking", "driving", "riding",
}

VISUAL_ATTRIBUTES = {
    "red", "green", "blue", "yellow", "white", "black", "orange", "purple",
    "large", "small", "tall", "wide", "round", "bright", "dark", "sunny",
    "rainy", "cloudy", "dry", "lush", "dense", "empty", "crowded",
}

COMMON_OBJECTS = {
    "person", "people", "student", "students", "worker", "workers", "child",
    "children", "tree", "trees", "plant", "plants", "bird", "birds", "animal",
    "car", "cars", "vehicle", "vehicles", "bus", "truck", "motorcycle", "bicycle",
    "auto", "rickshaw", "table", "chair", "chairs", "building", "buildings",
    "house", "helmet", "helmets", "solar", "panel", "panels", "water", "river",
    "road", "street", "laptop", "computer", "camera", "podium", "signboard",
}

OCR_INDICATORS = {
    "ocr", "text", "saying", "written", "reads", "headline", "title", "quote",
    "signboard", "banner", "poster", "heading", "slide", "page", "words",
    "phrase", "document", "documents",
}


@dataclass
class QueryIntent:
    raw: str
    normalized: str
    intent_type: QueryIntentType
    tokens: list[str] = field(default_factory=list)
    content_tokens: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    matched_vocab: list[dict] = field(default_factory=list)
    modality_hint: str | None = None
    person_names: list[str] = field(default_factory=list)
    object_terms: list[str] = field(default_factory=list)
    visual_terms: list[str] = field(default_factory=list)
    channel_weights: dict[str, float] = field(default_factory=dict)
    is_exact_lookup: bool = False


def is_identifier_query(query: str) -> bool:
    """Detect filenames, file extensions, asset UUIDs, and camera sequence IDs."""
    q = (query or "").strip()
    if not q:
        return False
    # Filename with extension (e.g. IMG_0021.JPG, report.pdf, video.mp4)
    if re.search(r"\.[A-Za-z0-9]{2,5}$", q):
        return True
    # Camera codes or archive IDs (e.g. IMG_20240517_0021, DSC_0012, IIHS_DOC_2023_01)
    if re.search(r"(?i)\b[A-Z]{2,}(?:[-_/][A-Z0-9]+)+\b", q):
        return True
    if re.search(r"(?i)\b[A-Z]{2,}[-_/]?\d{2,}\b", q):
        return True
    # Hex or UUID snippet (e.g. 97331596f62816d3)
    if re.fullmatch(r"[a-f0-9]{8,64}", q.lower()):
        return True
    # Standalone numbers >= 4 digits (e.g. order/file number)
    if re.fullmatch(r"\d{4,}", q):
        return True
    return False


def classify_query_intent(query: str, active_people: list[str] | None = None) -> QueryIntent:
    """Classify the user search query and assign retrieval channel weights."""
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

    # Modality hints
    modality_hint = None
    if re.search(r"\b(images?|photos?|photographs?|pictures?)\b", norm):
        modality_hint = "image"
    elif re.search(r"\b(videos?|clips?|footage|movies?)\b", norm):
        modality_hint = "video"
    elif re.search(r"\b(documents?|pdfs?|reports?|spreadsheets?|slides?|presentations?)\b", norm):
        modality_hint = "document"
    elif re.search(r"\b(audio|recordings?|podcasts?|interviews?)\b", norm):
        modality_hint = "audio"

    # Match active enrolled people
    matched_people = []
    for p_name in (active_people or []):
        if p_name and _contains_term(query, p_name):
            matched_people.append(p_name)

    # Detect visual actions, attributes, and objects
    detected_visual = [t for t in content_tokens if t in VISUAL_ACTION_INDICATORS or t in VISUAL_ATTRIBUTES]
    detected_objects = [t for t in content_tokens if t in COMMON_OBJECTS]

    # Intent classification logic
    exact_lookup = is_identifier_query(query)

    thematic_indicators = {"scarcity", "resilience", "governance", "policy", "development", "planning", "management", "economy", "sustainability", "climate"}
    has_thematic = bool(any(t in norm for t in thematic_indicators) or years)

    if exact_lookup:
        intent_type: QueryIntentType = "exact_identifier"
        channel_weights = {
            "visual_embedding": 0.20,
            "caption_description": 0.20,
            "object_tag": 0.10,
            "metadata_lexical": 2.20,
            "ocr_text": 0.30,
            "face_biometrics": 0.0,
        }
    elif matched_people:
        intent_type = "person_search"
        channel_weights = {
            "visual_embedding": 1.10,
            "caption_description": 1.20,
            "object_tag": 0.60,
            "metadata_lexical": 0.30,
            "ocr_text": 0.20,
            "face_biometrics": 2.50,
        }
    elif any(t in OCR_INDICATORS for t in raw_tokens):
        intent_type = "ocr_document"
        channel_weights = {
            "visual_embedding": 0.50,
            "caption_description": 0.80,
            "object_tag": 0.40,
            "metadata_lexical": 0.60,
            "ocr_text": 2.00,
            "face_biometrics": 0.0,
        }
    elif has_thematic:
        intent_type = "mixed"
        channel_weights = {
            "visual_embedding": 1.20,
            "caption_description": 1.30,
            "object_tag": 1.00,
            "metadata_lexical": 0.70,
            "ocr_text": 0.40,
            "face_biometrics": 0.0,
        }
    elif detected_visual:
        intent_type = "visual_description"
        # Natural language visual scene/activity: visual embeddings & scene captions MUST dominate.
        # Folder/filename lexical noise is strictly suppressed (0.05).
        channel_weights = {
            "visual_embedding": 1.60,
            "caption_description": 1.50,
            "object_tag": 1.10,
            "metadata_lexical": 0.05,
            "ocr_text": 0.10,
            "face_biometrics": 0.0,
        }
    elif detected_objects:
        intent_type = "object_search"
        channel_weights = {
            "visual_embedding": 1.40,
            "caption_description": 1.20,
            "object_tag": 1.60,
            "metadata_lexical": 0.10,
            "ocr_text": 0.10,
            "face_biometrics": 0.0,
        }
    elif matched_vocab:
        intent_type = "mixed"
        channel_weights = {
            "visual_embedding": 1.20,
            "caption_description": 1.30,
            "object_tag": 1.00,
            "metadata_lexical": 0.70,
            "ocr_text": 0.40,
            "face_biometrics": 0.0,
        }
    else:
        # Default natural language query
        intent_type = "visual_description" if len(content_tokens) >= 3 else "mixed"
        channel_weights = {
            "visual_embedding": 1.40,
            "caption_description": 1.30,
            "object_tag": 0.90,
            "metadata_lexical": 0.20,
            "ocr_text": 0.30,
            "face_biometrics": 0.0,
        }

    return QueryIntent(
        raw=query.strip(),
        normalized=norm,
        intent_type=intent_type,
        tokens=raw_tokens,
        content_tokens=content_tokens,
        years=years,
        matched_vocab=matched_vocab,
        modality_hint=modality_hint,
        person_names=matched_people,
        object_terms=detected_objects,
        visual_terms=detected_visual,
        channel_weights=channel_weights,
        is_exact_lookup=exact_lookup,
    )
