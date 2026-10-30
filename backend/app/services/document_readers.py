"""Local text extraction for common documents, adapted to IIHS archive scope.

Extracted text is stored as searchable metadata; this module never modifies originals.
PDF page rendering is deliberately left to the existing media derivative pipeline.
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".rtf", ".docx", ".odt",
    ".xlsx", ".ods", ".pptx", ".odp",
}


def extract_document_text(path: Path, max_bytes: int = 64 * 1024 * 1024) -> dict:
    suffix = path.suffix.lower()
    if path.stat().st_size > max_bytes:
        raise ValueError("document exceeds extraction size limit")
    if suffix in {".txt", ".md", ".markdown"}:
        value = path.read_text(encoding="utf-8", errors="replace")
        return _result([{"kind": "document", "ordinal": 0, "title": path.name, "text": value}])
    if suffix in {".html", ".htm"}:
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", raw)
        raw = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        value = html.unescape(re.sub(r"[ \t]+", " ", raw)).strip()
        return _result([{"kind": "document", "ordinal": 0, "title": path.name, "text": value}])
    if suffix == ".rtf":
        raw = path.read_text(encoding="latin-1", errors="replace")
        value = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
        value = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", value)
        value = re.sub(r"[{}]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return _result([{"kind": "document", "ordinal": 0, "title": path.name, "text": value}])

    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        segments = []
        for page_no, page in enumerate(reader.pages[:5000], start=1):
            value = (page.extract_text() or "").strip()
            if value:
                segments.append({"kind": "page", "ordinal": page_no, "title": f"{path.name} — page {page_no}", "text": value[:20000]})
        return _result(segments)

    if suffix in {".docx", ".odt"}:
        entry = "word/document.xml" if suffix == ".docx" else "content.xml"
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read(entry))
        text_values = [node.text or "" for node in root.iter() if node.tag.endswith("}t") or node.tag in {"t", "text"}]
        value = "\n".join(v.strip() for v in text_values if v.strip())
        return _result([{"kind": "document", "ordinal": 0, "title": path.name, "text": value}])
    if suffix in {".xlsx", ".ods", ".pptx", ".odp"}:
        return _extract_office_package(path, suffix)
    raise ValueError(f"unsupported document format: {suffix}")


def _extract_office_package(path: Path, suffix: str) -> dict:
    segments = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if suffix in {".pptx", ".odp"}:
            pattern = r"(ppt/slides/slide\d+\.xml|content\.xml)"
            kind = "slide"
        else:
            pattern = r"(xl/worksheets/sheet\d+\.xml|content\.xml)"
            kind = "sheet"
        files = sorted(name for name in names if re.fullmatch(pattern, name))
        for ordinal, name in enumerate(files[:1000], start=1):
            root = ET.fromstring(archive.read(name))
            values = [node.text or "" for node in root.iter() if node.tag.endswith("}t") or node.tag in {"t", "p"}]
            value = " ".join(v.strip() for v in values if v.strip())
            if value:
                segments.append({"kind": kind, "ordinal": ordinal, "title": Path(name).stem, "text": value[:20000]})
    return _result(segments)


def _result(segments: list[dict]) -> dict:
    normalized = [item for item in segments if item.get("text", "").strip()]
    return {"text": "\n".join(item["text"] for item in normalized)[:500000], "segments": normalized}
