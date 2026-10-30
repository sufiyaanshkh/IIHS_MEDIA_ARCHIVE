"""IIHS vocabulary-aware path and filename context enrichment.

Matches are suggestions grounded in folder/file names. They are never treated as
visual detections. The source vocabulary remains the authority for IIHS terms.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

VOCABULARY_PATH = Path(__file__).resolve().parents[1] / "data" / "iihs_keywords.json"


@lru_cache(maxsize=1)
def load_vocabulary() -> list[dict]:
    payload = json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))
    return [
        item for item in payload.get("keywords", [])
        if item.get("keyword") and item.get("category")
    ]


def _contains_term(text: str, term: str) -> bool:
    # Phrase-aware matching; punctuation/underscores in archive paths act as separators.
    haystack = re.sub(r"[^\\w]+", " ", text.casefold(), flags=re.UNICODE)
    needle = re.sub(r"[^\\w]+", " ", term.casefold(), flags=re.UNICODE).strip()
    if not needle:
        return False
    return re.search(r"(?<!\\w)" + re.escape(needle) + r"(?!\\w)", haystack) is not None


def infer_path_context(relative_path: str) -> dict:
    """Return traceable IIHS vocabulary matches from path, filename and ancestors."""
    path = Path(relative_path.replace("\\\\", "/"))
    parts = [part for part in path.parts if part not in {"", ".", ".."}]
    filename = parts[-1] if parts else relative_path
    folders = parts[:-1]
    path_text = " / ".join(parts)
    matches = []
    for item in load_vocabulary():
        keyword = item["keyword"]
        if not _contains_term(path_text, keyword):
            continue
        in_filename = _contains_term(filename, keyword)
        matching_folders = [folder for folder in folders if _contains_term(folder, keyword)]
        matches.append({
            "keyword": keyword,
            "category": item["category"],
            "notes": item.get("notes", ""),
            "evidence": "filename" if in_filename else "folder",
            "matched_folder": matching_folders[-1] if matching_folders else None,
            "confidence": 0.98 if in_filename else 0.92,
        })
    matches.sort(key=lambda item: (item["evidence"] != "filename", -len(item["keyword"])))
    categories = sorted({item["category"] for item in matches})
    context_terms = [item["keyword"] for item in matches]
    context_text = "IIHS archive path: " + path_text
    if context_terms:
        context_text += ". IIHS vocabulary: " + "; ".join(context_terms)
        context_text += ". Categories: " + "; ".join(categories)
    return {
        "relative_path": relative_path,
        "filename": filename,
        "folder_hierarchy": folders,
        "matched_keywords": matches,
        "matched_categories": categories,
        "context_text": context_text[:12000],
        "context_source": "path_and_filename",
    }
