"""Per-asset insights for the media card: source provenance, camera metadata,
zero-shot object labels, and relevant IIHS keywords.

Object labels and keywords are ranked by cosine similarity between the asset's
stored EmbeddingGemma image vector(s) and text vectors for each label. They are
AI suggestions, not verified detections, and carry no bounding boxes.
"""
import hashlib
import json
import logging
import math
import threading
from functools import lru_cache
from pathlib import Path

from PIL import ExifTags, Image, UnidentifiedImageError

from app.config import settings
from app.services.embeddings import text_embeddings
from app.services.media import MediaValidationError

log = logging.getLogger("iihs.insights")
VOCABULARY_PATH = Path(__file__).resolve().parents[1] / "data" / "iihs_keywords.json"
KEYWORD_MODEL_NAME = "iihs-keyword-ranker/embeddinggemma-2"
OBJECT_MODEL_NAME = "object-label-ranker/embeddinggemma-2"
TOP_KEYWORDS = 12
TOP_OBJECTS = 10

# Everyday objects (COCO classes) plus items that recur in IIHS photographs.
OBJECT_LABELS = [
    "person", "group of people", "child", "bicycle", "car", "motorcycle", "bus", "truck", "auto rickshaw", "train",
    "boat", "traffic light", "road sign", "bench", "bird", "cat", "dog", "cow", "goat", "horse", "backpack", "umbrella",
    "handbag", "bottle", "cup", "plate of food", "fruit", "vegetables", "chair", "table", "sofa", "potted plant", "bed",
    "laptop", "computer monitor", "mobile phone", "keyboard", "book", "bookshelf", "clock", "microphone", "podium",
    "projector screen", "whiteboard", "banner", "poster", "camera", "tripod", "building", "house", "construction site",
    "brick wall", "scaffolding", "tree", "flowers", "grass field", "water body", "pond", "river", "road", "street",
    "market stall", "solar panel", "water tank", "tractor", "farm field", "butterfly", "map", "document", "signboard",
]

_vector_lock = threading.Lock()
_vector_cache: dict[str, list[list[float]]] = {}


@lru_cache(maxsize=1)
def keyword_vocabulary():
    data = json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))
    return data["keywords"], int(data.get("excluded_place_names", 0))


def _normalise(vector):
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _label_vectors(name: str, texts: list[str]):
    """Embed label texts once per process, persisted on disk per model revision."""
    digest = hashlib.sha256(("\n".join(texts) + settings.embedding_revision).encode()).hexdigest()[:16]
    key = f"{name}-{digest}"
    with _vector_lock:
        if key in _vector_cache:
            return _vector_cache[key]
        cache_file = settings.storage_root / "cache" / f"{key}.json"
        try:
            vectors = json.loads(cache_file.read_text())
            if len(vectors) != len(texts):
                raise ValueError("stale cache")
        except (OSError, ValueError):
            vectors = []
            for offset in range(0, len(texts), 32):
                vectors.extend(_normalise(vector) for vector, _model, _revision in text_embeddings(texts[offset:offset + 32]))
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(vectors))
            except OSError:
                log.warning("Label vector cache could not be written.")
        _vector_cache[key] = vectors
        return vectors


def _rank(image_vector, label_vectors, limit):
    scores = [(sum(a * b for a, b in zip(image_vector, vector)), index) for index, vector in enumerate(label_vectors)]
    scores.sort(reverse=True)
    return scores[:limit]


def mean_image_vector(vectors: list[list[float]]):
    if not vectors:
        return None
    total = [0.0] * len(vectors[0])
    for vector in vectors:
        for index, value in enumerate(_normalise(list(vector))):
            total[index] += value
    return _normalise(total)


def rank_objects(image_vector):
    if image_vector is None:
        return {"status": "not_indexed", "items": []}
    try:
        vectors = _label_vectors("objects", [f"a photo of a {label}" for label in OBJECT_LABELS])
    except MediaValidationError:
        return {"status": "unavailable", "items": []}
    return {"status": "ready", "model": OBJECT_MODEL_NAME, "items": [{"label": OBJECT_LABELS[index], "score": round(score, 4)} for score, index in _rank(image_vector, vectors, TOP_OBJECTS)]}


def rank_keywords(image_vector):
    keywords, excluded = keyword_vocabulary()
    base = {"vocabulary_size": len(keywords), "excluded_place_names": excluded, "items": []}
    if image_vector is None:
        return base | {"status": "not_indexed"}
    try:
        vectors = _label_vectors("iihs-keywords", [f"{item['keyword']}: {item['notes']}"[:500] if item["notes"] else item["keyword"] for item in keywords])
    except MediaValidationError:
        return base | {"status": "unavailable"}
    items = [{"label": keywords[index]["keyword"], "category": keywords[index]["category"], "notes": keywords[index]["notes"], "score": round(score, 4)} for score, index in _rank(image_vector, vectors, TOP_KEYWORDS)]
    return base | {"status": "ready", "model": KEYWORD_MODEL_NAME, "items": items}


_EXIF_FIELDS = {
    "Model": "Camera model", "LensModel": "Lens", "DateTimeOriginal": "Date taken",
    "DateTime": "File modified", "Software": "Software", "Artist": "Artist",
    "ImageDescription": "Description", "FNumber": "Aperture", "ExposureTime": "Exposure", "ISOSpeedRatings": "ISO",
    "FocalLength": "Focal length", "Orientation": "Orientation",
}


def _format_exif(name, value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if name == "FNumber":
        return f"f/{float(value):.1f}"
    if name == "ExposureTime":
        value = float(value)
        return f"1/{round(1 / value)} s" if 0 < value < 1 else f"{value:g} s"
    if name == "FocalLength":
        return f"{float(value):g} mm"
    if isinstance(value, tuple):
        value = ", ".join(str(item) for item in value)
    return str(value).strip("\x00 ").strip()


def camera_metadata(path: Path):
    """Return readable EXIF fields. GPS coordinates are never returned."""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            merged = dict(exif)
            merged.update(exif.get_ifd(0x8769))
            gps_present = bool(exif.get_ifd(0x8825))
    except (OSError, UnidentifiedImageError, ValueError):
        return {"fields": [], "gps_present": False}
    fields = []
    for tag, value in merged.items():
        name = ExifTags.TAGS.get(tag)
        if name in _EXIF_FIELDS:
            try:
                text = _format_exif(name, value)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if text and len(text) <= 300:
                fields.append({"label": _EXIF_FIELDS[name], "value": text})
    order = list(_EXIF_FIELDS.values())
    fields.sort(key=lambda field: order.index(field["label"]))
    return {"fields": fields, "gps_present": gps_present}


def employees_status(db=None, asset=None, media_path: Path | None = None):
    if not (settings.face_feature_enabled and settings.face_institutional_authorization):
        return {
            "status": "disabled",
            "items": [],
            "message": "Employee recognition is turned off until IIHS privacy, consent and governance approval is in place. No faces are matched.",
        }
    if db is None or asset is None:
        return {
            "status": "ready",
            "model": f"insightface/{settings.face_model_pack}",
            "items": [],
            "message": "InsightFace (buffalo_l) face recognition is active.",
        }

    from sqlalchemy import func, select
    from app.models import DetectedArchiveFace, FaceCandidateMatch, PersonProfile
    from app.services.face_recognition import process_archive_asset_faces

    face_count = db.scalar(
        select(func.count()).select_from(DetectedArchiveFace).where(DetectedArchiveFace.asset_id == asset.id)
    ) or 0
    active_profile_count = db.scalar(
        select(func.count()).select_from(PersonProfile).where(PersonProfile.status == "active")
    ) or 0

    if asset.media_type == "image" and media_path is not None and media_path.is_file():
        meta_face = (asset.metadata_json or {}).get("face_recognition")
        cand_count = db.scalar(
            select(func.count()).select_from(FaceCandidateMatch).where(FaceCandidateMatch.asset_id == asset.id)
        ) or 0
        if (face_count == 0 and not meta_face) or (face_count > 0 and cand_count == 0 and active_profile_count > 0):
            try:
                res = process_archive_asset_faces(db, asset, media_path)
                asset.metadata_json = (asset.metadata_json or {}) | {
                    "face_recognition": {"status": "ready", "model_pack": settings.face_model_pack, **res}
                }
                db.commit()
                face_count = int(res.get("faces_detected", 0))
            except Exception as exc:
                log.warning("On-demand InsightFace detection failed on %s: %s", asset.id, exc)

    rows = db.execute(
        select(FaceCandidateMatch, PersonProfile)
        .join(PersonProfile, PersonProfile.id == FaceCandidateMatch.person_id)
        .where(
            FaceCandidateMatch.asset_id == asset.id,
            FaceCandidateMatch.status.in_(["accepted", "unverified"]),
            PersonProfile.status == "active",
        )
        .order_by(FaceCandidateMatch.aggregate_similarity.desc())
    ).all()

    seen_people: set[str] = set()
    items = []
    for match, person in rows:
        if person.id in seen_people:
            continue
        seen_people.add(person.id)
        badge = "Verified" if match.status == "accepted" else "Candidate · needs review"
        items.append({
            "label": f"{person.display_name} ({badge} · {round(match.aggregate_similarity * 100)}%)",
            "score": round(float(match.aggregate_similarity), 4),
            "category": match.status,
            "person_id": person.id,
            "match_id": match.id,
        })

    if items:
        msg = f"InsightFace ({settings.face_model_pack}) matched {len(items)} enrolled profile(s) across {face_count} detected face(s)."
    elif active_profile_count == 0:
        msg = (
            f"InsightFace ({settings.face_model_pack}) is enabled ({face_count} face(s) detected in this record). "
            "Add person subfolders to data/profile_photos/IIHS_Profile_Photos to enroll reference profiles."
        )
    elif face_count == 0:
        msg = f"InsightFace ({settings.face_model_pack}) active ({active_profile_count} enrolled profile(s)) — no faces detected in this media record."
    else:
        msg = f"InsightFace ({settings.face_model_pack}) detected {face_count} face(s) — no enrolled profile matched above threshold."

    return {
        "status": "ready",
        "model": f"insightface/{settings.face_model_pack}",
        "items": items,
        "message": msg,
    }


def generate_visual_description(image_vector) -> dict:
    """Build an unverified visual description and tag summary from the image embedding."""
    if image_vector is None:
        return {"status": "not_indexed", "description": "", "object_tags": [], "keyword_suggestions": []}
    objs = rank_objects(image_vector)
    kws = rank_keywords(image_vector)
    if objs.get("status") != "ready" and kws.get("status") != "ready":
        return {"status": "unavailable", "description": "", "object_tags": [], "keyword_suggestions": []}
    top_objs = [item["label"] for item in (objs.get("items") or [])[:5]]
    top_kws = [item["label"] for item in (kws.get("items") or [])[:5]]
    parts = []
    if top_objs:
        parts.append("Visual elements (AI suggestion): " + ", ".join(top_objs))
    if top_kws:
        parts.append("Related IIHS vocabulary (AI suggestion): " + ", ".join(top_kws))
    description = ". ".join(parts) + ("." if parts else "")
    return {
        "status": "ready",
        "description": description,
        "object_tags": objs.get("items") or [],
        "keyword_suggestions": kws.get("items") or [],
        "is_verified": False,
    }
