"""DigUp-inspired bounded overlapping chunk preparation, timestamped audio/video
transcript windowing, and perceptual video frame deduplication.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from PIL import Image


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 180) -> list[str]:
    if max_chars < 200 or overlap < 0 or overlap >= max_chars:
        raise ValueError("invalid chunking limits")
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    words = normalized.split(" ")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        extra = len(word) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            chunk = " ".join(current).strip()
            if chunk:
                chunks.append(chunk)
            carry: list[str] = []
            carry_len = 0
            for previous in reversed(current):
                extra_len = len(previous) + (1 if carry else 0)
                if carry and carry_len + extra_len > overlap:
                    break
                carry.insert(0, previous)
                carry_len += extra_len
            current = carry
            current_len = len(" ".join(current))
        current.append(word)
        current_len += len(word) + (1 if len(current) > 1 else 0)
    final = " ".join(current).strip()
    if final and (not chunks or final != chunks[-1]):
        chunks.append(final)
    return chunks


def document_embedding_text(title: str, text: str) -> str:
    return f"title: {title.strip() or 'none'} | text: {text.strip()}"


def segment_content_hash(segment_type: str, content: str, ordinal: int = 0) -> str:
    payload = f"{segment_type}:{ordinal}:{content}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def build_transcript_windows(
    transcript_result: dict,
    duration_ms: int | None = None,
    window_ms: int = 30000,
    hop_ms: int = 25000,
    max_chars: int = 1200,
) -> list[dict]:
    """Create overlapping timestamped transcript windows from Whisper output."""
    raw_segments = transcript_result.get("segments") or []
    language = transcript_result.get("language") or "unknown"
    windows: list[dict] = []

    if raw_segments and isinstance(raw_segments, list):
        normalized_segs = []
        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue
            seg_text = (seg.get("text") or "").strip()
            if not seg_text:
                continue
            start_ms = int(seg.get("start_ms") if "start_ms" in seg else float(seg.get("start", 0.0)) * 1000)
            end_ms = int(seg.get("end_ms") if "end_ms" in seg else float(seg.get("end", start_ms / 1000 + 5.0)) * 1000)
            normalized_segs.append({"start_ms": max(0, start_ms), "end_ms": max(start_ms, end_ms), "text": seg_text})

        if normalized_segs:
            max_end = max(s["end_ms"] for s in normalized_segs)
            win_start = 0
            ordinal = 1
            while win_start <= max_end:
                win_end = win_start + window_ms
                overlapping = [
                    s for s in normalized_segs
                    if s["end_ms"] > win_start and s["start_ms"] < win_end
                ]
                if overlapping:
                    combined = " ".join(s["text"] for s in overlapping).strip()
                    actual_start = overlapping[0]["start_ms"]
                    actual_end = overlapping[-1]["end_ms"]
                    for piece in chunk_text(combined, max_chars=max_chars, overlap=min(180, max_chars // 4)):
                        if not windows or windows[-1]["text"] != piece:
                            windows.append({
                                "ordinal": ordinal,
                                "start_ms": actual_start,
                                "end_ms": actual_end,
                                "text": piece,
                                "language": language,
                            })
                            ordinal += 1
                if win_end >= max_end:
                    break
                win_start += max(1000, hop_ms)
            return windows

    # Fallback when only full transcript text is provided without per-utterance timestamps
    full_text = (transcript_result.get("text") or "").strip()
    if not full_text:
        return []
    chunks = chunk_text(full_text, max_chars=max_chars, overlap=min(180, max_chars // 4))
    total = len(chunks)
    total_dur = max(duration_ms or (total * window_ms), 1000)
    step_ms = max(1000, total_dur // max(1, total))
    for idx, piece in enumerate(chunks):
        start_ms = idx * step_ms
        end_ms = min(total_dur, start_ms + window_ms)
        windows.append({
            "ordinal": idx + 1,
            "start_ms": start_ms,
            "end_ms": max(start_ms, end_ms),
            "text": piece,
            "language": language,
        })
    return windows


def compute_frame_dhash(image_path: Path, hash_size: int = 8) -> int:
    """Compute 64-bit perceptual difference hash (dHash) of an image for frame deduplication."""
    with Image.open(image_path) as img:
        gray = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BILINEAR)
        pixels = list(gray.getdata())
    bits = 0
    width = hash_size + 1
    for row in range(hash_size):
        row_offset = row * width
        for col in range(hash_size):
            bits = (bits << 1) | (1 if pixels[row_offset + col] > pixels[row_offset + col + 1] else 0)
    return bits


def hamming_distance(hash_a: int, hash_b: int) -> int:
    return (hash_a ^ hash_b).bit_count()
