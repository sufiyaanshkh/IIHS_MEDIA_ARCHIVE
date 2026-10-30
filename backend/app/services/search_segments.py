"""DigUp-inspired bounded overlapping chunk preparation."""
from __future__ import annotations

import re


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
