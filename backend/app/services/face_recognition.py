"""Dedicated InsightFace ('buffalo_l') facial recognition, folder-based profile enrollment,
archive face detection, multi-reference similarity aggregation, human verification,
and biometric governance service.

Strictly uses InsightFace with the 'buffalo_l' model pack (never FaceNet, DeepFace, or dlib).
Keeps InsightFace embeddings completely separate from EmbeddingGemma's 768-D semantic embeddings.
Biometric processing remains disabled by default until IIHS authorisation and consent are confirmed.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import struct
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    AuditEvent,
    DetectedArchiveFace,
    EnrolledReferencePhoto,
    FaceCandidateMatch,
    FaceReferenceEmbedding,
    FaceVerificationDecision,
    MediaAsset,
    PersonProfile,
    VideoFrame,
)
from app.services.crawler import IMAGE_EXTENSIONS, is_excluded_directory, is_excluded_filename, is_reparse_or_symlink
from app.services.media import MediaValidationError, sha256_file
from app.services.search_segments import compute_frame_dhash, hamming_distance

log = logging.getLogger("iihs.face_recognition")

REQUIRED_MODEL_PACK = "buffalo_l"
AMBIGUOUS_FOLDER_NAMES = {
    "", ".", "..", "unassigned", "_unassigned", "unknown", "misc", "miscellaneous",
    "review", "needs_review", "ambiguous", "temp", "tmp", "root", "unsorted",
}


class BiometricGovernanceError(PermissionError):
    """Raised when biometric operations are invoked while governance gates are disabled."""


def is_biometric_governance_authorized() -> bool:
    """Return True only when InsightFace buffalo_l is configured and both governance flags are enabled."""
    return bool(
        settings.face_model_pack == REQUIRED_MODEL_PACK
        and settings.face_feature_enabled
        and settings.face_institutional_authorization
    )


def ensure_biometric_governance_authorized(*, allow_isolated_enrollment: bool = False) -> None:
    """Enforce that production biometric matching is disabled until IIHS authorization is confirmed."""
    if settings.face_model_pack != REQUIRED_MODEL_PACK:
        raise BiometricGovernanceError("Only the InsightFace 'buffalo_l' model pack is permitted.")
    if allow_isolated_enrollment:
        return
    if not (settings.face_feature_enabled and settings.face_institutional_authorization):
        raise BiometricGovernanceError(
            "Biometric face recognition is disabled by default until IIHS institutional authorisation "
            "and consent requirements are confirmed (FACE_FEATURE_ENABLED=true and FACE_INSTITUTIONAL_AUTHORIZATION=true)."
        )


def _normalize_person_name(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


_CAMERA_OR_EVENT_FOLDER_RE = re.compile(
    r"^(?:0F7A|382A|R\d+A|SC\s*\d+(?:\s*Edit)?|\d{6,8}(?:\s+IIHS.*)?|IMG_\d+|DSC_?\d+)$",
    re.IGNORECASE,
)


def _clean_folder_person_name(raw_folder_name: str) -> str | None:
    """Extract clean person display name from immediate subfolder name, or return None if ambiguous."""
    cleaned = " ".join(raw_folder_name.strip().split())
    if not cleaned or cleaned.casefold() in AMBIGUOUS_FOLDER_NAMES:
        return None
    if _CAMERA_OR_EVENT_FOLDER_RE.match(cleaned):
        return None
    # Strip accidental trailing image extension on folder names (e.g. 'Yashima Jain.jpg')
    cleaned = re.sub(r"\.(?:jpe?g|png|webp|tiff?|bmp)$", "", cleaned, flags=re.IGNORECASE).strip()
    # Strip UFP cohort prefixes/suffixes (e.g. 'UFP 2023 24 Aditya Sunil Satake Photos' -> 'Aditya Sunil Satake')
    cleaned = re.sub(r"^UFP\s+20\d{2}[\s_-]*\d{2}\s+(?:Fellow\s+)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+Photos$", "", cleaned, flags=re.IGNORECASE).strip()
    # Strip batch/count suffixes like '(1 of 2)', '(1 of 1)', or trailing '1x'
    cleaned = re.sub(r"\s*\(\d+\s*of\s*\d+\)$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+\d+x$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned or cleaned.casefold() in AMBIGUOUS_FOLDER_NAMES or _CAMERA_OR_EVENT_FOLDER_RE.match(cleaned):
        return None
    return cleaned


def _derive_encryption_keys() -> tuple[bytes, bytes]:
    secret_material = (
        settings.face_encryption_key
        or settings.local_ml_token
        or settings.initial_admin_token
        or "iihs-default-local-biometric-key-material"
    ).encode("utf-8")
    enc_key = hashlib.pbkdf2_hmac("sha256", secret_material, b"iihs-face-enc-v1", 10000, dklen=32)
    mac_key = hashlib.pbkdf2_hmac("sha256", secret_material, b"iihs-face-mac-v1", 10000, dklen=32)
    return enc_key, mac_key


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    blocks: list[bytes] = []
    counter = 0
    while sum(len(b) for b in blocks) < length:
        ctr_bytes = struct.pack("<Q", counter)
        blocks.append(hmac.new(enc_key, nonce + ctr_bytes, hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:length]


def encrypt_face_embedding(vector: list[float]) -> str:
    """Authenticated encryption-at-rest for facial biometric embeddings."""
    if not vector or not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in vector):
        raise ValueError("Invalid facial embedding vector.")
    raw_bytes = struct.pack(f"<{len(vector)}f", *(float(v) for v in vector))
    enc_key, mac_key = _derive_encryption_keys()
    nonce = secrets.token_bytes(16)
    stream = _keystream(enc_key, nonce, len(raw_bytes))
    ciphertext = bytes(a ^ b for a, b in zip(raw_bytes, stream))
    header = struct.pack("<I", len(vector))
    tag = hmac.new(mac_key, header + nonce + ciphertext, hashlib.sha256).digest()
    payload = header + nonce + tag + ciphertext
    return "v1:" + base64.b64encode(payload).decode("ascii")


def decrypt_face_embedding(token: str) -> list[float]:
    """Verify HMAC tag and decrypt a stored facial biometric embedding."""
    if not token or not token.startswith("v1:"):
        raise ValueError("Unsupported encrypted embedding format.")
    payload = base64.b64decode(token[3:].encode("ascii"), validate=True)
    if len(payload) < 4 + 16 + 32:
        raise ValueError("Corrupted encrypted embedding payload.")
    (dim,) = struct.unpack("<I", payload[:4])
    nonce = payload[4:20]
    tag = payload[20:52]
    ciphertext = payload[52:]
    if len(ciphertext) != dim * 4:
        raise ValueError("Encrypted embedding dimension mismatch.")
    enc_key, mac_key = _derive_encryption_keys()
    expected_tag = hmac.new(mac_key, payload[:4] + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Encrypted biometric embedding integrity check failed.")
    stream = _keystream(enc_key, nonce, len(ciphertext))
    raw_bytes = bytes(a ^ b for a, b in zip(ciphertext, stream))
    return [float(v) for v in struct.unpack(f"<{dim}f", raw_bytes)]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(float(a) * float(a) for a in vec_a))
    norm_b = math.sqrt(sum(float(b) * float(b) for b in vec_b))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 0.0
    return float(dot / (norm_a * norm_b))


@dataclass
class DetectedFaceResult:
    bbox: list[float]
    det_score: float
    normed_embedding: list[float]
    embedding_dim: int
    landmarks: list[list[float]] | None = None
    model_pack: str = REQUIRED_MODEL_PACK
    recognition_model: str = "w600k_r50.onnx"


def select_execution_providers(
    available_providers: list[str] | None = None,
    *,
    force_cpu: bool = False,
) -> tuple[list[str], int]:
    """Detect ONNX Runtime providers so CUDA is prioritized when available and CPU fallback works cleanly."""
    if available_providers is None:
        try:
            import onnxruntime as ort

            available_providers = list(ort.get_available_providers())
        except Exception:
            available_providers = ["CPUExecutionProvider"]

    if not force_cpu and "CUDAExecutionProvider" in available_providers:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"], 0
    return ["CPUExecutionProvider"], -1


class InsightFaceBuffaloEngine:
    """Dedicated InsightFace ('buffalo_l') face detection and recognition runtime."""

    def __init__(
        self,
        model_pack: str = REQUIRED_MODEL_PACK,
        root_dir: Path | str | None = None,
        det_size: tuple[int, int] = (640, 640),
        force_cpu: bool = False,
    ):
        if model_pack != REQUIRED_MODEL_PACK:
            raise ValueError("Only the InsightFace 'buffalo_l' model pack is permitted.")
        self.model_pack = REQUIRED_MODEL_PACK
        self.det_size = det_size
        self.force_cpu = force_cpu
        self._lock = threading.Lock()
        self._app = None
        self._embedding_dim: int | None = None
        self._providers_used: list[str] = []
        self._ctx_id: int = -1
        self._root_dir = self._resolve_root_dir(root_dir)

    @staticmethod
    def _resolve_root_dir(configured_root: Path | str | None) -> Path:
        candidates: list[Path] = []
        if configured_root:
            candidates.append(Path(configured_root))
        candidates.append(Path(settings.insightface_root))
        candidates.append(Path(__file__).resolve().parents[3] / "data" / "models" / "insightface")
        try:
            candidates.append(Path.home() / ".insightface")
        except Exception:
            pass
        for cand in candidates:
            try:
                if (cand / "models" / REQUIRED_MODEL_PACK).is_dir():
                    return cand
            except OSError:
                continue
        return Path(configured_root or settings.insightface_root)

    def ensure_loaded(self) -> None:
        if self._app is not None:
            return
        with self._lock:
            if self._app is not None:
                return
            from insightface.app import FaceAnalysis

            providers, ctx_id = select_execution_providers(force_cpu=self.force_cpu)
            fa = FaceAnalysis(
                name=self.model_pack,
                root=str(self._root_dir),
                allowed_modules=["detection", "recognition"],
                providers=providers,
            )
            fa.prepare(ctx_id=ctx_id, det_size=self.det_size)
            if "recognition" not in fa.models:
                raise RuntimeError("InsightFace 'buffalo_l' recognition model was not loaded.")
            rec_model = fa.models["recognition"]
            out_shape = getattr(rec_model, "output_shape", None)
            if not out_shape or not isinstance(out_shape[-1], int) or int(out_shape[-1]) <= 0:
                raise RuntimeError("Could not validate embedding dimension from loaded InsightFace recognition model.")
            self._embedding_dim = int(out_shape[-1])
            self._providers_used = providers
            self._ctx_id = ctx_id
            self._app = fa

    @property
    def embedding_dim(self) -> int:
        self.ensure_loaded()
        assert self._embedding_dim is not None
        return self._embedding_dim

    def runtime_info(self) -> dict[str, Any]:
        try:
            self.ensure_loaded()
            return {
                "status": "ready",
                "library": "insightface",
                "model_pack": self.model_pack,
                "embedding_dim": self._embedding_dim,
                "providers": self._providers_used,
                "device": "cuda" if "CUDAExecutionProvider" in self._providers_used else "cpu",
                "det_size": list(self.det_size),
                "root": str(self._root_dir),
            }
        except Exception as exc:
            try:
                req = Request(
                    settings.local_ml_url + "/face/healthz",
                    headers={"Authorization": f"Bearer {settings.local_ml_token}"},
                )
                with urlopen(req, timeout=10) as resp:
                    bridge_info = json.loads(resp.read())
                if bridge_info.get("status") == "ready" and bridge_info.get("model_pack") == REQUIRED_MODEL_PACK:
                    return bridge_info | {"runtime_mode": "host_bridge"}
            except Exception:
                pass
            return {
                "status": "unavailable",
                "library": "insightface",
                "model_pack": self.model_pack,
                "error": str(exc),
            }

    def detect_and_embed(self, image_input: Path | bytes | Any) -> list[DetectedFaceResult]:
        """Detect faces and return validated normed_embedding vectors using InsightFace buffalo_l."""
        try:
            self.ensure_loaded()
        except Exception:
            # If running inside a slim container without local ONNX weights, delegate to host local_ml_service
            return self._delegate_to_host_bridge(image_input)

        import numpy as np

        bgr, scale_x, scale_y = self._load_bgr_image_with_scale(image_input)
        with self._lock:
            raw_faces = self._app.get(bgr)

        expected_dim = self.embedding_dim
        results: list[DetectedFaceResult] = []
        # Sort faces deterministically from left-to-right, top-to-bottom
        sorted_faces = sorted(
            raw_faces,
            key=lambda f: (float(f.bbox[0]) if getattr(f, "bbox", None) is not None else 0.0, float(f.bbox[1]) if getattr(f, "bbox", None) is not None else 0.0),
        )
        for face in sorted_faces:
            normed = getattr(face, "normed_embedding", None)
            if normed is None:
                continue
            vec = [float(x) for x in np.asarray(normed, dtype=np.float32).reshape(-1).tolist()]
            if len(vec) != expected_dim or not all(math.isfinite(x) for x in vec):
                raise ValueError(f"InsightFace normed_embedding dimension mismatch: expected {expected_dim}, got {len(vec)}")
            norm = math.sqrt(sum(x * x for x in vec))
            if norm <= 1e-6:
                continue
            # Ensure strict unit normalization
            vec = [float(x / norm) for x in vec]
            raw_bbox = np.asarray(face.bbox, dtype=np.float32).reshape(-1).tolist()[:4]
            bbox = [
                round(float(raw_bbox[0]) * scale_x, 2),
                round(float(raw_bbox[1]) * scale_y, 2),
                round(float(raw_bbox[2]) * scale_x, 2),
                round(float(raw_bbox[3]) * scale_y, 2),
            ]
            det_score = round(float(getattr(face, "det_score", 0.0)), 4)
            kps = getattr(face, "kps", None)
            landmarks = (
                [[round(float(pt[0]) * scale_x, 2), round(float(pt[1]) * scale_y, 2)] for pt in np.asarray(kps, dtype=np.float32).tolist()]
                if kps is not None
                else None
            )
            results.append(
                DetectedFaceResult(
                    bbox=bbox,
                    det_score=det_score,
                    normed_embedding=vec,
                    embedding_dim=expected_dim,
                    landmarks=landmarks,
                    model_pack=self.model_pack,
                )
            )
        return results

    @staticmethod
    def _load_bgr_image_with_scale(image_input: Path | bytes | Any, max_dim: int = 1280) -> tuple[Any, float, float]:
        import numpy as np

        if isinstance(image_input, np.ndarray):
            return image_input, 1.0, 1.0
        if isinstance(image_input, (bytes, bytearray)):
            from io import BytesIO

            with Image.open(BytesIO(image_input)) as img:
                orig_w, orig_h = img.size
                try:
                    img.draft("RGB", (max_dim, max_dim))
                except Exception:
                    pass
                img = ImageOps.exif_transpose(img)
                if max(img.size) > max_dim:
                    img.thumbnail((max_dim, max_dim))
                new_w, new_h = img.size
                rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
        else:
            with Image.open(Path(image_input)) as img:
                orig_w, orig_h = img.size
                try:
                    img.draft("RGB", (max_dim, max_dim))
                except Exception:
                    pass
                img = ImageOps.exif_transpose(img)
                if max(img.size) > max_dim:
                    img.thumbnail((max_dim, max_dim))
                new_w, new_h = img.size
                rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
        # Convert RGB -> BGR as expected by InsightFace FaceAnalysis.get()
        scale_x = float(orig_w) / float(max(1, new_w))
        scale_y = float(orig_h) / float(max(1, new_h))
        return rgb[:, :, ::-1].copy(), scale_x, scale_y

    def _delegate_to_host_bridge(self, image_input: Path | bytes | Any, max_dim: int = 1280) -> list[DetectedFaceResult]:
        if not settings.local_ml_token:
            raise MediaValidationError("InsightFace local runtime and bridge token are unavailable.")
        from io import BytesIO

        scale_x = 1.0
        scale_y = 1.0
        if isinstance(image_input, Path):
            with Image.open(image_input) as img:
                orig_w, orig_h = img.size
                try:
                    img.draft("RGB", (max_dim, max_dim))
                except Exception:
                    pass
                img = ImageOps.exif_transpose(img)
                if max(img.size) > max_dim:
                    img.thumbnail((max_dim, max_dim))
                new_w, new_h = img.size
                scale_x = float(orig_w) / float(max(1, new_w))
                scale_y = float(orig_h) / float(max(1, new_h))
                buf = BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=90)
                payload = buf.getvalue()
                suffix = ".jpg"
        elif isinstance(image_input, (bytes, bytearray)):
            payload = bytes(image_input)
            suffix = ".jpg"
        else:
            buf = BytesIO()
            Image.fromarray(image_input[:, :, ::-1]).save(buf, format="JPEG", quality=90)
            payload = buf.getvalue()
            suffix = ".jpg"

        req = Request(
            settings.local_ml_url + "/face/detect",
            data=payload,
            headers={
                "Authorization": f"Bearer {settings.local_ml_token}",
                "Content-Type": "application/octet-stream",
                "X-Image-Suffix": suffix,
            },
        )
        try:
            with urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
        except (HTTPError, URLError, OSError, ValueError) as exc:
            raise MediaValidationError("Local InsightFace service is unavailable.") from exc

        if data.get("model_pack") != REQUIRED_MODEL_PACK:
            raise MediaValidationError("Incompatible face recognition model pack from bridge.")
        dim = int(data.get("embedding_dim") or 0)
        if dim <= 0:
            raise MediaValidationError("Invalid face embedding dimension from bridge.")
        out: list[DetectedFaceResult] = []
        for item in data.get("faces", []):
            vec = item.get("normed_embedding") or []
            if len(vec) != dim:
                raise MediaValidationError("Bridge face embedding length mismatch.")
            raw_bbox = [float(v) for v in item.get("bbox", [0, 0, 0, 0])[:4]]
            bbox = [
                round(raw_bbox[0] * scale_x, 2),
                round(raw_bbox[1] * scale_y, 2),
                round(raw_bbox[2] * scale_x, 2),
                round(raw_bbox[3] * scale_y, 2),
            ]
            out.append(
                DetectedFaceResult(
                    bbox=bbox,
                    det_score=float(item.get("det_score", 0.0)),
                    normed_embedding=[float(v) for v in vec],
                    embedding_dim=dim,
                    landmarks=item.get("landmarks"),
                    model_pack=REQUIRED_MODEL_PACK,
                )
            )
        return out


_default_engine: InsightFaceBuffaloEngine | None = None
_engine_lock = threading.Lock()


def get_face_engine(force_cpu: bool = False) -> InsightFaceBuffaloEngine:
    global _default_engine
    with _engine_lock:
        if _default_engine is None or _default_engine.force_cpu != force_cpu:
            det = int(getattr(settings, "face_det_size", 640))
            _default_engine = InsightFaceBuffaloEngine(
                model_pack=settings.face_model_pack,
                root_dir=settings.insightface_root,
                det_size=(det, det),
                force_cpu=force_cpu,
            )
        return _default_engine


# ---------------------------------------------------------------------------
# Folder-Hierarchy Profile Photo Enrollment Pipeline
# ---------------------------------------------------------------------------
def scan_and_enroll_profile_folder(
    db: Session,
    profile_root: Path | None = None,
    *,
    actor_id: str | None = None,
    engine: InsightFaceBuffaloEngine | None = None,
    min_det_score: float | None = None,
    auto_confirm: bool = False,
) -> dict[str, Any]:
    """Recursively scan a folder-organized profile-photo dataset:
        IIHS_Profile_Photos/
          Mohammed Ahmed/
            photo1.jpg
            photo2.jpg
          John Smith/
            profile.jpg
    Uses the immediate subfolder name as the person's display name (never the image filename).
    Flags multi-face images and root/unassigned images for manual review, skips duplicates,
    and stores encrypted InsightFace buffalo_l reference embeddings.
    """
    ensure_biometric_governance_authorized(allow_isolated_enrollment=True)
    root = (profile_root or settings.face_profile_photos_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Profile photo directory does not exist: {root}")

    face_engine = engine or get_face_engine()
    det_threshold = float(min_det_score if min_det_score is not None else settings.face_min_det_score)
    now = datetime.now(timezone.utc)
    retention_expiry = now + timedelta(days=int(getattr(settings, "face_retention_days", 365)))

    # Discover all supported image files safely
    discovered_files: list[tuple[Path, PurePosixPath]] = []
    for current_dir, dirnames, filenames in os.walk(root, followlinks=False):
        dir_path = Path(current_dir)
        safe_dirs: list[str] = []
        for dname in sorted(dirnames):
            if is_excluded_directory(dname):
                continue
            cand_dir = dir_path / dname
            if is_reparse_or_symlink(cand_dir):
                continue
            safe_dirs.append(dname)
        dirnames[:] = safe_dirs

        for fname in sorted(filenames):
            if is_excluded_filename(fname):
                continue
            fpath = dir_path / fname
            if is_reparse_or_symlink(fpath):
                continue
            if fpath.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            rel = PurePosixPath(fpath.resolve().relative_to(root).as_posix())
            discovered_files.append((fpath.resolve(), rel))

    # Track hashes already enrolled in DB and hashes seen in this scan
    existing_photos_by_path = {
        p.source_relative_path: p
        for p in db.scalars(select(EnrolledReferencePhoto)).all()
    }
    seen_hashes: set[str] = {
        p.sha256
        for p in existing_photos_by_path.values()
        if p.status in {"pending_confirmation", "enrolled"}
    }

    person_stats: dict[str, dict[str, Any]] = {}
    unassigned_queue: list[dict[str, Any]] = []
    totals = {
        "total_photographs_scanned": 0,
        "successful_detections": 0,
        "failed_detections": 0,
        "multi_face_flagged": 0,
        "low_quality_flagged": 0,
        "unassigned_root_files": 0,
        "duplicate_files": 0,
    }

    for fpath, rel in discovered_files:
        totals["total_photographs_scanned"] += 1
        rel_str = rel.as_posix()
        st = fpath.stat()
        file_sha = sha256_file(fpath)

        # Check if already processed at this exact relative path with identical sha256
        existing_at_path = existing_photos_by_path.get(rel_str)
        if existing_at_path and existing_at_path.sha256 == file_sha and existing_at_path.status in {"pending_confirmation", "enrolled"}:
            folder_label = existing_at_path.folder_person_name or "Unassigned"
            pstat = person_stats.setdefault(folder_label, _empty_person_stat(folder_label, existing_at_path.person_id))
            pstat["photographs_scanned"] += 1
            pstat["duplicate_files"] += 1
            totals["duplicate_files"] += 1
            continue

        # Rule 7: Images directly inside root folder or with missing/ambiguous folder names -> unassigned review queue
        cleaned_person_name = _clean_folder_person_name(rel.parts[0]) if len(rel.parts) >= 2 else None
        if len(rel.parts) < 2 or cleaned_person_name is None:
            photo_row = _upsert_reference_photo(
                db,
                existing=existing_at_path,
                person_id=None,
                folder_person_name=rel.parts[0] if len(rel.parts) >= 2 else None,
                source_relative_path=rel_str,
                source_full_path=str(fpath),
                original_filename=fpath.name,
                sha256=file_sha,
                size_bytes=int(st.st_size),
                status="unassigned_review",
                review_reason="image_in_root_or_ambiguous_folder",
                detected_face_count=0,
                det_score=None,
                bbox_json=None,
            )
            totals["unassigned_root_files"] += 1
            unassigned_queue.append({
                "photo_id": photo_row.id,
                "source_relative_path": rel_str,
                "original_filename": fpath.name,
                "status": photo_row.status,
                "review_reason": photo_row.review_reason,
            })
            continue

        # Rule 2 & 3: Immediate subfolder name is the person's display name (never the image filename)
        folder_person_name = cleaned_person_name
        norm_name = _normalize_person_name(folder_person_name)

        person = db.scalar(select(PersonProfile).where(PersonProfile.normalized_name == norm_name))
        if person is None:
            person = PersonProfile(
                display_name=folder_person_name,
                normalized_name=norm_name,
                folder_name=rel.parts[0],
                status="active" if auto_confirm else "pending_confirmation",
                consent_confirmed=auto_confirm,
                confirmed_by=actor_id if auto_confirm else None,
                confirmed_at=now if auto_confirm else None,
                retention_expires_at=retention_expiry,
                created_by=actor_id,
                metadata_json={"source_root": str(root)},
            )
            db.add(person)
            db.flush()
        elif person.status == "revoked":
            # Re-enrollment after revocation starts in pending_confirmation
            person.status = "active" if auto_confirm else "pending_confirmation"
            person.folder_name = rel.parts[0]

        pstat = person_stats.setdefault(folder_person_name, _empty_person_stat(folder_person_name, person.id, person.status))
        pstat["person_id"] = person.id
        pstat["photographs_scanned"] += 1

        # Rule 8: Detect duplicate photographs and avoid unnecessary embedding generation
        if file_sha in seen_hashes:
            _upsert_reference_photo(
                db,
                existing=existing_at_path,
                person_id=person.id,
                folder_person_name=folder_person_name,
                source_relative_path=rel_str,
                source_full_path=str(fpath),
                original_filename=fpath.name,
                sha256=file_sha,
                size_bytes=int(st.st_size),
                status="duplicate",
                review_reason="duplicate_sha256",
                detected_face_count=0,
                det_score=None,
                bbox_json=None,
            )
            pstat["duplicate_files"] += 1
            totals["duplicate_files"] += 1
            continue

        # Detect faces using InsightFace buffalo_l
        try:
            detected_faces = face_engine.detect_and_embed(fpath)
        except Exception as exc:
            _upsert_reference_photo(
                db,
                existing=existing_at_path,
                person_id=person.id,
                folder_person_name=folder_person_name,
                source_relative_path=rel_str,
                source_full_path=str(fpath),
                original_filename=fpath.name,
                sha256=file_sha,
                size_bytes=int(st.st_size),
                status="failed_detection",
                review_reason=f"corrupted_or_unreadable_image: {type(exc).__name__}",
                detected_face_count=0,
                det_score=None,
                bbox_json=None,
            )
            pstat["failed_detections"] += 1
            totals["failed_detections"] += 1
            continue

        if len(detected_faces) == 0:
            _upsert_reference_photo(
                db,
                existing=existing_at_path,
                person_id=person.id,
                folder_person_name=folder_person_name,
                source_relative_path=rel_str,
                source_full_path=str(fpath),
                original_filename=fpath.name,
                sha256=file_sha,
                size_bytes=int(st.st_size),
                status="failed_detection",
                review_reason="no_face_detected",
                detected_face_count=0,
                det_score=None,
                bbox_json=None,
            )
            pstat["failed_detections"] += 1
            totals["failed_detections"] += 1
            continue

        # Rule 6: If a photograph contains multiple faces, flag it for manual review instead of blindly associating every face
        if len(detected_faces) > 1:
            photo_row = _upsert_reference_photo(
                db,
                existing=existing_at_path,
                person_id=person.id,
                folder_person_name=folder_person_name,
                source_relative_path=rel_str,
                source_full_path=str(fpath),
                original_filename=fpath.name,
                sha256=file_sha,
                size_bytes=int(st.st_size),
                status="needs_review",
                review_reason="multiple_faces_detected",
                detected_face_count=len(detected_faces),
                det_score=max(f.det_score for f in detected_faces),
                bbox_json=[f.bbox for f in detected_faces],
            )
            pstat["multi_face_flagged"] += 1
            totals["multi_face_flagged"] += 1
            unassigned_queue.append({
                "photo_id": photo_row.id,
                "person_id": person.id,
                "folder_person_name": folder_person_name,
                "source_relative_path": rel_str,
                "original_filename": fpath.name,
                "status": photo_row.status,
                "review_reason": photo_row.review_reason,
                "detected_face_count": len(detected_faces),
            })
            continue

        face = detected_faces[0]
        if face.det_score < det_threshold:
            _upsert_reference_photo(
                db,
                existing=existing_at_path,
                person_id=person.id,
                folder_person_name=folder_person_name,
                source_relative_path=rel_str,
                source_full_path=str(fpath),
                original_filename=fpath.name,
                sha256=file_sha,
                size_bytes=int(st.st_size),
                status="low_quality",
                review_reason=f"det_score_below_threshold ({face.det_score:.3f} < {det_threshold:.2f})",
                detected_face_count=1,
                det_score=face.det_score,
                bbox_json=face.bbox,
            )
            pstat["low_quality_flagged"] += 1
            totals["low_quality_flagged"] += 1
            continue

        # Valid single-face reference photo!
        photo_status = "enrolled" if (auto_confirm or person.status == "active") else "pending_confirmation"
        photo_row = _upsert_reference_photo(
            db,
            existing=existing_at_path,
            person_id=person.id,
            folder_person_name=folder_person_name,
            source_relative_path=rel_str,
            source_full_path=str(fpath),
            original_filename=fpath.name,
            sha256=file_sha,
            size_bytes=int(st.st_size),
            status=photo_status,
            review_reason=None,
            detected_face_count=1,
            det_score=face.det_score,
            bbox_json=face.bbox,
        )
        db.flush()

        # Replace any prior embedding for this photo_row and store encrypted normed_embedding
        db.execute(delete(FaceReferenceEmbedding).where(FaceReferenceEmbedding.reference_photo_id == photo_row.id))
        enc_vec = encrypt_face_embedding(face.normed_embedding)
        emb_sha = hashlib.sha256(struct.pack(f"<{len(face.normed_embedding)}f", *face.normed_embedding)).hexdigest()
        ref_emb = FaceReferenceEmbedding(
            person_id=person.id,
            reference_photo_id=photo_row.id,
            model_pack=REQUIRED_MODEL_PACK,
            recognition_model=face.recognition_model,
            embedding_dim=face.embedding_dim,
            encrypted_embedding=enc_vec,
            embedding_sha256=emb_sha,
            det_score=face.det_score,
            bbox_json=face.bbox,
            landmarks_json=face.landmarks,
            is_active=bool(auto_confirm or person.status == "active"),
        )
        db.add(ref_emb)
        seen_hashes.add(file_sha)
        pstat["successful_detections"] += 1
        totals["successful_detections"] += 1
        person.photo_count = int(person.photo_count or 0) + 1
        if auto_confirm or person.status == "active":
            person.active_embedding_count = int(person.active_embedding_count or 0) + 1
        if totals["total_photographs_scanned"] % 50 == 0:
            db.commit()

    db.flush()

    # Update per-person photo_count and active_embedding_count in DB
    all_persons = db.scalars(select(PersonProfile)).all()
    for p in all_persons:
        p.photo_count = db.scalar(
            select(func.count()).select_from(EnrolledReferencePhoto).where(
                EnrolledReferencePhoto.person_id == p.id,
                EnrolledReferencePhoto.status.in_(["pending_confirmation", "enrolled"]),
            )
        ) or 0
        p.active_embedding_count = db.scalar(
            select(func.count()).select_from(FaceReferenceEmbedding).where(
                FaceReferenceEmbedding.person_id == p.id,
                FaceReferenceEmbedding.is_active.is_(True),
            )
        ) or 0
        if p.display_name in person_stats:
            person_stats[p.display_name]["status"] = p.status
            person_stats[p.display_name]["active_embeddings"] = p.active_embedding_count

    report = {
        "root_path": str(root),
        "model_pack": REQUIRED_MODEL_PACK,
        "persons_discovered": len(person_stats),
        **totals,
        "people": sorted(person_stats.values(), key=lambda x: x["person_name"].casefold()),
        "review_queue": unassigned_queue,
        "generated_at": now.isoformat(),
    }
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action="face_enrollment_scan_completed",
            target_type="biometric_enrollment",
            detail={k: v for k, v in report.items() if k not in {"people", "review_queue"}},
        )
    )
    db.commit()
    return report


def _empty_person_stat(name: str, person_id: str | None, status: str = "pending_confirmation") -> dict[str, Any]:
    return {
        "person_id": person_id,
        "person_name": name,
        "status": status,
        "photographs_scanned": 0,
        "successful_detections": 0,
        "failed_detections": 0,
        "multi_face_flagged": 0,
        "low_quality_flagged": 0,
        "duplicate_files": 0,
        "active_embeddings": 0,
    }


def _upsert_reference_photo(
    db: Session,
    *,
    existing: EnrolledReferencePhoto | None,
    person_id: str | None,
    folder_person_name: str | None,
    source_relative_path: str,
    source_full_path: str,
    original_filename: str,
    sha256: str,
    size_bytes: int,
    status: str,
    review_reason: str | None,
    detected_face_count: int,
    det_score: float | None,
    bbox_json: list | None,
) -> EnrolledReferencePhoto:
    now = datetime.now(timezone.utc)
    if existing is None:
        row = EnrolledReferencePhoto(
            person_id=person_id,
            folder_person_name=folder_person_name,
            source_relative_path=source_relative_path,
            source_full_path=source_full_path,
            original_filename=original_filename,
            sha256=sha256,
            size_bytes=size_bytes,
            status=status,
            review_reason=review_reason,
            detected_face_count=detected_face_count,
            det_score=det_score,
            bbox_json=bbox_json,
        )
        db.add(row)
        db.flush()
        return row
    existing.person_id = person_id
    existing.folder_person_name = folder_person_name
    existing.source_full_path = source_full_path
    existing.original_filename = original_filename
    existing.sha256 = sha256
    existing.size_bytes = size_bytes
    existing.status = status
    existing.review_reason = review_reason
    existing.detected_face_count = detected_face_count
    existing.det_score = det_score
    existing.bbox_json = bbox_json
    existing.updated_at = now
    db.flush()
    return existing


# ---------------------------------------------------------------------------
# Administrator Review, Name Correction, Confirmation & Complete Deletion
# ---------------------------------------------------------------------------
def update_person_profile_name(
    db: Session,
    person_id: str,
    new_display_name: str,
    *,
    actor_id: str | None = None,
) -> PersonProfile:
    """Allow administrator to correct a person's display name before or after activating enrollment."""
    person = db.get(PersonProfile, person_id)
    if person is None:
        raise LookupError("Person profile not found.")
    cleaned = " ".join(new_display_name.strip().split())
    if not cleaned:
        raise ValueError("Display name cannot be empty.")
    norm = _normalize_person_name(cleaned)
    conflict = db.scalar(select(PersonProfile).where(PersonProfile.normalized_name == norm, PersonProfile.id != person_id))
    if conflict is not None:
        raise ValueError("Another person profile already uses that name.")
    old_name = person.display_name
    person.display_name = cleaned
    person.normalized_name = norm
    person.updated_at = datetime.now(timezone.utc)
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action="person_profile_renamed",
            target_type="person_profile",
            target_id=person.id,
            detail={"from": old_name, "to": cleaned},
        )
    )
    db.commit()
    db.refresh(person)
    return person


def reassign_reference_photo(
    db: Session,
    photo_id: str,
    target_person_name: str,
    *,
    actor_id: str | None = None,
    engine: InsightFaceBuffaloEngine | None = None,
) -> EnrolledReferencePhoto:
    """Allow administrator to assign/reassign an unassigned or mislabelled single-face photo to a person."""
    photo = db.get(EnrolledReferencePhoto, photo_id)
    if photo is None:
        raise LookupError("Reference photo not found.")
    cleaned = " ".join(target_person_name.strip().split())
    if not cleaned:
        raise ValueError("Target person name cannot be empty.")
    norm = _normalize_person_name(cleaned)
    person = db.scalar(select(PersonProfile).where(PersonProfile.normalized_name == norm))
    now = datetime.now(timezone.utc)
    if person is None:
        person = PersonProfile(
            display_name=cleaned,
            normalized_name=norm,
            status="pending_confirmation",
            created_by=actor_id,
            retention_expires_at=now + timedelta(days=int(getattr(settings, "face_retention_days", 365))),
        )
        db.add(person)
        db.flush()

    old_person_id = photo.person_id
    photo.person_id = person.id
    photo.reviewed_by = actor_id
    photo.reviewed_at = now
    photo.updated_at = now

    # If the photo had not been embedded yet (e.g., was in unassigned_review at root), detect & embed now
    existing_embs = db.scalars(select(FaceReferenceEmbedding).where(FaceReferenceEmbedding.reference_photo_id == photo.id)).all()
    if not existing_embs and photo.source_full_path and Path(photo.source_full_path).is_file():
        face_engine = engine or get_face_engine()
        faces = face_engine.detect_and_embed(Path(photo.source_full_path))
        if len(faces) == 1:
            f = faces[0]
            photo.detected_face_count = 1
            photo.det_score = f.det_score
            photo.bbox_json = f.bbox
            photo.status = "enrolled" if person.status == "active" else "pending_confirmation"
            photo.review_reason = None
            enc_vec = encrypt_face_embedding(f.normed_embedding)
            emb_sha = hashlib.sha256(struct.pack(f"<{len(f.normed_embedding)}f", *f.normed_embedding)).hexdigest()
            db.add(
                FaceReferenceEmbedding(
                    person_id=person.id,
                    reference_photo_id=photo.id,
                    model_pack=REQUIRED_MODEL_PACK,
                    recognition_model=f.recognition_model,
                    embedding_dim=f.embedding_dim,
                    encrypted_embedding=enc_vec,
                    embedding_sha256=emb_sha,
                    det_score=f.det_score,
                    bbox_json=f.bbox,
                    landmarks_json=f.landmarks,
                    is_active=(person.status == "active"),
                )
            )
        elif len(faces) > 1:
            photo.detected_face_count = len(faces)
            photo.status = "needs_review"
            photo.review_reason = "multiple_faces_detected"
        else:
            photo.detected_face_count = 0
            photo.status = "failed_detection"
            photo.review_reason = "no_face_detected"
    else:
        for emb in existing_embs:
            emb.person_id = person.id
            emb.is_active = (person.status == "active")
        if existing_embs:
            photo.status = "enrolled" if person.status == "active" else "pending_confirmation"
            photo.review_reason = None

    db.flush()
    for pid in {old_person_id, person.id}:
        if pid:
            p = db.get(PersonProfile, pid)
            if p:
                p.photo_count = db.scalar(
                    select(func.count()).select_from(EnrolledReferencePhoto).where(
                        EnrolledReferencePhoto.person_id == p.id,
                        EnrolledReferencePhoto.status.in_(["pending_confirmation", "enrolled"]),
                    )
                ) or 0
                p.active_embedding_count = db.scalar(
                    select(func.count()).select_from(FaceReferenceEmbedding).where(
                        FaceReferenceEmbedding.person_id == p.id,
                        FaceReferenceEmbedding.is_active.is_(True),
                    )
                ) or 0

    db.add(
        AuditEvent(
            actor_id=actor_id,
            action="reference_photo_reassigned",
            target_type="enrolled_reference_photo",
            target_id=photo.id,
            detail={"from_person_id": old_person_id, "to_person_id": person.id, "person_name": person.display_name},
        )
    )
    db.commit()
    db.refresh(photo)
    return photo


def confirm_person_enrollment(
    db: Session,
    person_id: str,
    *,
    actor_id: str | None = None,
    consent_reference: str | None = None,
) -> PersonProfile:
    """Administrator confirmation to activate a person's reference embeddings for matching."""
    person = db.get(PersonProfile, person_id)
    if person is None:
        raise LookupError("Person profile not found.")
    now = datetime.now(timezone.utc)
    person.status = "active"
    person.consent_confirmed = True
    if consent_reference:
        person.consent_reference = consent_reference
    person.confirmed_by = actor_id
    person.confirmed_at = now
    person.updated_at = now

    photos = db.scalars(
        select(EnrolledReferencePhoto).where(
            EnrolledReferencePhoto.person_id == person.id,
            EnrolledReferencePhoto.status == "pending_confirmation",
        )
    ).all()
    for photo in photos:
        photo.status = "enrolled"
        photo.reviewed_by = actor_id
        photo.reviewed_at = now

    embs = db.scalars(
        select(FaceReferenceEmbedding).where(FaceReferenceEmbedding.person_id == person.id)
    ).all()
    for emb in embs:
        emb.is_active = True

    person.photo_count = len(photos) or person.photo_count
    person.active_embedding_count = len(embs)

    db.add(
        AuditEvent(
            actor_id=actor_id,
            action="person_enrollment_confirmed",
            target_type="person_profile",
            target_id=person.id,
            detail={
                "person_name": person.display_name,
                "active_embeddings": len(embs),
                "consent_reference": consent_reference,
            },
        )
    )
    db.commit()
    db.refresh(person)
    return person


def revoke_person_enrollment(
    db: Session,
    person_id: str,
    *,
    actor_id: str | None = None,
    delete_profile_record: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Completely delete all biometric embeddings, reference photo records, candidate matches,
    and verification decisions when enrollment is revoked.
    """
    person = db.get(PersonProfile, person_id)
    if person is None:
        raise LookupError("Person profile not found.")
    person_name = person.display_name

    del_decisions = db.execute(delete(FaceVerificationDecision).where(FaceVerificationDecision.person_id == person.id)).rowcount or 0
    del_matches = db.execute(delete(FaceCandidateMatch).where(FaceCandidateMatch.person_id == person.id)).rowcount or 0
    del_embs = db.execute(delete(FaceReferenceEmbedding).where(FaceReferenceEmbedding.person_id == person.id)).rowcount or 0
    del_photos = db.execute(delete(EnrolledReferencePhoto).where(EnrolledReferencePhoto.person_id == person.id)).rowcount or 0

    if delete_profile_record:
        db.delete(person)
    else:
        person.status = "revoked"
        person.consent_confirmed = False
        person.photo_count = 0
        person.active_embedding_count = 0
        person.updated_at = datetime.now(timezone.utc)

    summary = {
        "person_id": person_id,
        "person_name": person_name,
        "status": "deleted" if delete_profile_record else "revoked",
        "deleted_embeddings": int(del_embs),
        "deleted_reference_photos": int(del_photos),
        "deleted_candidate_matches": int(del_matches),
        "deleted_verification_decisions": int(del_decisions),
        "reason": reason,
    }
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action="person_biometrics_revoked_and_deleted",
            target_type="person_profile",
            target_id=person_id,
            detail=summary,
        )
    )
    db.commit()
    return summary


# ---------------------------------------------------------------------------
# Archive Face Detection, Multi-Reference Aggregation & Human Verification
# ---------------------------------------------------------------------------
def process_archive_asset_faces(
    db: Session,
    asset: MediaAsset,
    image_path: Path,
    *,
    frame: VideoFrame | None = None,
    engine: InsightFaceBuffaloEngine | None = None,
    match_threshold: float | None = None,
    high_confidence_threshold: float | None = None,
    allow_isolated_test: bool = False,
) -> dict[str, Any]:
    """Detect all faces in an archive image or sampled video frame using InsightFace buffalo_l,
    avoid reprocessing visually identical video frames, and generate unverified candidate matches
    aggregated across each enrolled person's reference photographs.
    """
    ensure_biometric_governance_authorized(allow_isolated_enrollment=allow_isolated_test)
    face_engine = engine or get_face_engine()
    threshold = float(match_threshold if match_threshold is not None else settings.face_match_threshold)
    high_thresh = float(high_confidence_threshold if high_confidence_threshold is not None else settings.face_high_confidence_threshold)

    frame_dhash_hex: str | None = None
    if frame is not None:
        try:
            dhash_int = compute_frame_dhash(image_path)
            frame_dhash_hex = f"{dhash_int:016x}"
            # Avoid repeatedly processing visually identical frames on the same video
            existing_frame_faces = db.scalars(
                select(DetectedArchiveFace).where(
                    DetectedArchiveFace.asset_id == asset.id,
                    DetectedArchiveFace.frame_dhash.is_not(None),
                )
            ).all()
            dedup_limit = int(getattr(settings, "video_frame_dedup_threshold", 4))
            for prev in existing_frame_faces:
                if prev.frame_id == frame.id:
                    continue
                try:
                    prev_int = int(prev.frame_dhash, 16)
                    if hamming_distance(dhash_int, prev_int) <= dedup_limit:
                        return {"faces_detected": 0, "candidate_matches": 0, "skipped_identical_frame": True}
                except ValueError:
                    continue
        except Exception:
            frame_dhash_hex = None

    # Remove any previous detections for this exact (asset_id, frame_id) before re-indexing
    db.execute(
        delete(DetectedArchiveFace).where(
            DetectedArchiveFace.asset_id == asset.id,
            DetectedArchiveFace.frame_id == (frame.id if frame else None),
        )
    )
    db.flush()

    detected_faces = face_engine.detect_and_embed(image_path)
    if not detected_faces:
        return {"faces_detected": 0, "candidate_matches": 0, "skipped_identical_frame": False}

    # Load all active reference embeddings for active confirmed people
    active_refs = db.execute(
        select(FaceReferenceEmbedding, PersonProfile)
        .join(PersonProfile, PersonProfile.id == FaceReferenceEmbedding.person_id)
        .where(
            PersonProfile.status == "active",
            FaceReferenceEmbedding.is_active.is_(True),
            FaceReferenceEmbedding.model_pack == REQUIRED_MODEL_PACK,
        )
    ).all()

    refs_by_person: dict[str, list[tuple[FaceReferenceEmbedding, list[float]]]] = {}
    for ref_emb, person in active_refs:
        try:
            vec = decrypt_face_embedding(ref_emb.encrypted_embedding)
            refs_by_person.setdefault(person.id, []).append((ref_emb, vec))
        except ValueError:
            continue

    candidate_count = 0
    for idx, det in enumerate(detected_faces):
        if det.det_score < settings.face_min_det_score:
            continue
        enc_vec = encrypt_face_embedding(det.normed_embedding)
        archive_face = DetectedArchiveFace(
            asset_id=asset.id,
            frame_id=frame.id if frame else None,
            media_type="video" if frame is not None else asset.media_type,
            timestamp_ms=frame.timestamp_ms if frame is not None else None,
            frame_dhash=frame_dhash_hex,
            face_index=idx,
            bbox_json=det.bbox,
            det_score=det.det_score,
            landmarks_json=det.landmarks,
            model_pack=REQUIRED_MODEL_PACK,
            embedding_dim=det.embedding_dim,
            encrypted_embedding=enc_vec,
            original_file_reference=asset.source_relative_key or asset.original_filename,
        )
        db.add(archive_face)
        db.flush()

        # Compare against each active enrolled person's reference embeddings
        for person_id, ref_list in refs_by_person.items():
            scored_refs: list[tuple[float, str]] = []
            for ref_emb, ref_vec in ref_list:
                if len(ref_vec) != det.embedding_dim:
                    continue
                sim = cosine_similarity(det.normed_embedding, ref_vec)
                scored_refs.append((sim, ref_emb.reference_photo_id))
            if not scored_refs:
                continue
            scored_refs.sort(key=lambda item: item[0], reverse=True)
            max_sim = scored_refs[0][0]
            best_photo_id = scored_refs[0][1]
            top_k = [s for s, _ in scored_refs[:3]]
            mean_top = sum(top_k) / len(top_k)
            # Aggregate matching evidence across multiple reference photographs
            agg_sim = (0.70 * max_sim + 0.30 * mean_top) if len(scored_refs) > 1 else max_sim
            matched_refs = sum(1 for s, _ in scored_refs if s >= threshold)

            if agg_sim >= threshold or max_sim >= threshold:
                # Section 4: Never automatically treat a similarity score as proof of identity; keep unverified
                cand = FaceCandidateMatch(
                    detected_face_id=archive_face.id,
                    asset_id=asset.id,
                    person_id=person_id,
                    best_reference_photo_id=best_photo_id,
                    aggregate_similarity=round(float(agg_sim), 4),
                    max_similarity=round(float(max_sim), 4),
                    mean_top_similarity=round(float(mean_top), 4),
                    matched_reference_count=max(1, matched_refs),
                    threshold_used=threshold,
                    high_confidence=bool(agg_sim >= high_thresh),
                    status="unverified",
                )
                db.add(cand)
                candidate_count += 1

    db.flush()
    return {
        "faces_detected": len(detected_faces),
        "candidate_matches": candidate_count,
        "skipped_identical_frame": False,
    }


def review_face_candidate_match(
    db: Session,
    match_id: str,
    *,
    actor_id: str | None,
    decision: str,
    notes: str | None = None,
) -> FaceCandidateMatch:
    """Record a human verification decision ('accepted' or 'rejected') on a candidate face match."""
    if decision not in {"accepted", "rejected"}:
        raise ValueError("Decision must be 'accepted' or 'rejected'.")
    match = db.get(FaceCandidateMatch, match_id)
    if match is None:
        raise LookupError("Candidate face match not found.")
    match.status = decision
    match.updated_at = datetime.now(timezone.utc)
    rec = FaceVerificationDecision(
        candidate_match_id=match.id,
        detected_face_id=match.detected_face_id,
        person_id=match.person_id,
        actor_id=actor_id,
        decision=decision,
        notes=notes,
    )
    db.add(rec)
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action=f"face_match_{decision}",
            target_type="face_candidate_match",
            target_id=match.id,
            detail={
                "asset_id": match.asset_id,
                "person_id": match.person_id,
                "aggregate_similarity": match.aggregate_similarity,
                "notes": notes,
            },
        )
    )
    # Keep person names in semantic search only after a human accepts the match.
    # Rebuild on rejection too, so a revoked identity is removed from the profile text.
    asset = db.get(MediaAsset, match.asset_id)
    if asset is not None:
        try:
            from app.services.semantic_profiles import refresh_asset_semantic_profile
            refresh_asset_semantic_profile(db, asset)
        except Exception:
            log.exception("Could not refresh semantic profile after face verification for asset %s", match.asset_id)
    db.commit()
    db.refresh(match)
    return match
