"""Local text and structure extraction for archive documents.

Supports PDF, DOCX, ODT, RTF, HTML, TXT, MD, XLSX, ODS, PPTX and ODP.
Produces bounded overlapping chunks with page, sheet, table and slide provenance.
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from app.services.search_segments import chunk_text

SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".markdown", ".html", ".htm", ".rtf",
    ".docx", ".odt", ".xlsx", ".ods", ".pptx", ".odp",
}

DOCUMENT_MIMES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".rtf": "application/rtf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}


def _natural_number(name: str) -> int:
    match = re.search(r"(\d+)", name)
    return int(match.group(1)) if match else 0


def _chunk_Units(
    raw_segments: list[dict],
    max_chars: int = 1200,
    overlap: int = 180,
) -> list[dict]:
    """Split each extracted page/sheet/slide/document unit into bounded overlapping chunks."""
    chunked: list[dict] = []
    ordinal = 1
    for unit in raw_segments:
        raw_text = (unit.get("text") or "").strip()
        if not raw_text:
            continue
        pieces = chunk_text(raw_text, max_chars=max_chars, overlap=overlap)
        if not pieces:
            continue
        for sub_idx, piece in enumerate(pieces, start=1):
            title = unit.get("title") or "Document"
            if len(pieces) > 1:
                title = f"{title} (part {sub_idx})"
            chunked.append({
                "kind": unit.get("kind", "document"),
                "segment_type": unit.get("segment_type", "office_chunk"),
                "ordinal": ordinal,
                "page_number": unit.get("page_number"),
                "title": title,
                "text": piece,
            })
            ordinal += 1
    return chunked


def extract_document_text(
    path: Path,
    max_bytes: int = 256 * 1024 * 1024,
    chunk_chars: int = 1200,
    chunk_overlap: int = 180,
) -> dict:
    suffix = path.suffix.lower()
    if path.stat().st_size > max_bytes:
        raise ValueError("document exceeds extraction size limit")

    if suffix in {".txt", ".md", ".markdown"}:
        value = path.read_text(encoding="utf-8", errors="replace")
        raw = [{"kind": "document", "segment_type": "office_chunk", "ordinal": 1, "page_number": 1, "title": path.name, "text": value}]
        return _result(raw, chunk_chars, chunk_overlap)

    if suffix in {".html", ".htm"}:
        raw_html = path.read_text(encoding="utf-8", errors="replace")
        raw_html = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", raw_html)
        raw_html = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", raw_html)
        raw_html = re.sub(r"(?s)<[^>]+>", " ", raw_html)
        value = html.unescape(re.sub(r"[ \t]+", " ", raw_html)).strip()
        raw = [{"kind": "document", "segment_type": "office_chunk", "ordinal": 1, "page_number": 1, "title": path.name, "text": value}]
        return _result(raw, chunk_chars, chunk_overlap)

    if suffix == ".rtf":
        raw_rtf = path.read_text(encoding="latin-1", errors="replace")
        value = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw_rtf)
        value = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", value)
        value = re.sub(r"[{}]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        raw = [{"kind": "document", "segment_type": "office_chunk", "ordinal": 1, "page_number": 1, "title": path.name, "text": value}]
        return _result(raw, chunk_chars, chunk_overlap)

    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        segments = []
        for page_no, page in enumerate(reader.pages[:5000], start=1):
            value = (page.extract_text() or "").strip()
            if value:
                segments.append({
                    "kind": "page",
                    "segment_type": "pdf_page",
                    "ordinal": page_no,
                    "page_number": page_no,
                    "title": f"{path.name} — page {page_no}",
                    "text": value[:50000],
                })
        return _result(segments, chunk_chars, chunk_overlap)

    if suffix == ".docx":
        return _extract_docx(path, chunk_chars, chunk_overlap)

    if suffix == ".odt":
        return _extract_odt(path, chunk_chars, chunk_overlap)

    if suffix == ".xlsx":
        return _extract_xlsx(path, chunk_chars, chunk_overlap)

    if suffix == ".ods":
        return _extract_ods(path, chunk_chars, chunk_overlap)

    if suffix == ".pptx":
        return _extract_pptx(path, chunk_chars, chunk_overlap)

    if suffix == ".odp":
        return _extract_odp(path, chunk_chars, chunk_overlap)

    raise ValueError(f"unsupported document format: {suffix}")


def _extract_docx(path: Path, chunk_chars: int, chunk_overlap: int) -> dict:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []
    for node in root.iter():
        if node.tag.endswith("}p") or node.tag == "p":
            texts = [t.text or "" for t in node.iter() if t.tag.endswith("}t") or t.tag == "t"]
            line = "".join(texts).strip()
            if line:
                paragraphs.append(line)
    if not paragraphs:
        texts = [t.text.strip() for t in root.iter() if (t.tag.endswith("}t") or t.tag == "t") and (t.text or "").strip()]
        paragraphs = texts
    value = "\n".join(paragraphs)
    raw = [{"kind": "document", "segment_type": "office_chunk", "ordinal": 1, "page_number": 1, "title": path.name, "text": value}]
    return _result(raw, chunk_chars, chunk_overlap)


def _extract_odt(path: Path, chunk_chars: int, chunk_overlap: int) -> dict:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    paragraphs: list[str] = []
    for node in root.iter():
        if node.tag.endswith("}p") or node.tag.endswith("}h") or node.tag in {"p", "h"}:
            line = " ".join("".join(node.itertext()).split())
            if line:
                paragraphs.append(line)
    if not paragraphs:
        paragraphs = [" ".join("".join(root.itertext()).split())]
    value = "\n".join(p for p in paragraphs if p)
    raw = [{"kind": "document", "segment_type": "office_chunk", "ordinal": 1, "page_number": 1, "title": path.name, "text": value}]
    return _result(raw, chunk_chars, chunk_overlap)


def _extract_xlsx(path: Path, chunk_chars: int, chunk_overlap: int) -> dict:
    segments: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            ss_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in ss_root.iter():
                if si.tag.endswith("}si") or si.tag == "si":
                    shared.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t") or t.tag == "t"))

        sheet_titles: list[str] = []
        if "xl/workbook.xml" in names:
            wb_root = ET.fromstring(archive.read("xl/workbook.xml"))
            for sheet_el in wb_root.iter():
                if sheet_el.tag.endswith("}sheet") or sheet_el.tag == "sheet":
                    if sheet_el.get("name"):
                        sheet_titles.append(sheet_el.get("name", ""))

        sheet_files = sorted(
            (n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
            key=_natural_number,
        )
        for idx, sheet_file in enumerate(sheet_files[:1000], start=1):
            sheet_root = ET.fromstring(archive.read(sheet_file))
            rows_text: list[str] = []
            for row_el in sheet_root.iter():
                if row_el.tag.endswith("}row") or row_el.tag == "row":
                    cell_vals: list[str] = []
                    for cell in row_el:
                        if not (cell.tag.endswith("}c") or cell.tag == "c"):
                            continue
                        cell_type = cell.get("t", "")
                        v_el = next((c for c in cell if c.tag.endswith("}v") or c.tag == "v"), None)
                        is_el = next((c for c in cell if c.tag.endswith("}is") or c.tag == "is"), None)
                        val = ""
                        if cell_type == "s" and v_el is not None and v_el.text:
                            try:
                                s_idx = int(v_el.text)
                                val = shared[s_idx] if 0 <= s_idx < len(shared) else v_el.text
                            except ValueError:
                                val = v_el.text
                        elif is_el is not None:
                            val = "".join(t.text or "" for t in is_el.iter() if t.tag.endswith("}t") or t.tag == "t")
                        elif v_el is not None and v_el.text:
                            val = v_el.text
                        if val.strip():
                            cell_vals.append(val.strip())
                    if cell_vals:
                        rows_text.append(" | ".join(cell_vals))
            if not rows_text:
                # Fallback for simplified worksheet XMLs in tests
                values = [node.text.strip() for node in sheet_root.iter() if (node.tag.endswith("}t") or node.tag in {"t", "v"}) and (node.text or "").strip()]
                if values:
                    rows_text.append(" ".join(values))
            if rows_text:
                sheet_name = sheet_titles[idx - 1] if idx - 1 < len(sheet_titles) else f"Sheet {idx}"
                segments.append({
                    "kind": "sheet",
                    "segment_type": "spreadsheet_sheet",
                    "ordinal": idx,
                    "page_number": idx,
                    "title": f"{path.name} — {sheet_name}",
                    "text": "\n".join(rows_text)[:50000],
                })
    return _result(segments, chunk_chars, chunk_overlap)


def _extract_ods(path: Path, chunk_chars: int, chunk_overlap: int) -> dict:
    segments: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    tables = [el for el in root.iter() if el.tag.endswith("}table") or el.tag == "table"]
    if tables:
        for idx, table in enumerate(tables[:1000], start=1):
            t_name = next((v for k, v in table.attrib.items() if k.endswith("name")), f"Sheet {idx}")
            rows_text: list[str] = []
            for row in table.iter():
                if row.tag.endswith("}table-row") or row.tag == "table-row":
                    cells = [" ".join("".join(c.itertext()).split()) for c in row if c.tag.endswith("}table-cell") or c.tag == "table-cell"]
                    cells = [c for c in cells if c]
                    if cells:
                        rows_text.append(" | ".join(cells))
            if not rows_text:
                text_val = " ".join("".join(table.itertext()).split())
                if text_val:
                    rows_text.append(text_val)
            if rows_text:
                segments.append({
                    "kind": "sheet",
                    "segment_type": "spreadsheet_sheet",
                    "ordinal": idx,
                    "page_number": idx,
                    "title": f"{path.name} — {t_name}",
                    "text": "\n".join(rows_text)[:50000],
                })
    else:
        text_val = " ".join("".join(root.itertext()).split())
        if text_val:
            segments.append({
                "kind": "sheet",
                "segment_type": "spreadsheet_sheet",
                "ordinal": 1,
                "page_number": 1,
                "title": f"{path.name} — Sheet 1",
                "text": text_val[:50000],
            })
    return _result(segments, chunk_chars, chunk_overlap)


def _extract_pptx(path: Path, chunk_chars: int, chunk_overlap: int) -> dict:
    segments: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        slide_files = sorted(
            (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=_natural_number,
        )
        for idx, slide_file in enumerate(slide_files[:1000], start=1):
            slide_no = _natural_number(slide_file) or idx
            slide_root = ET.fromstring(archive.read(slide_file))
            values = [node.text.strip() for node in slide_root.iter() if (node.tag.endswith("}t") or node.tag in {"t", "p"}) and (node.text or "").strip()]
            notes_file = f"ppt/notesSlides/notesSlide{slide_no}.xml"
            if notes_file in names:
                notes_root = ET.fromstring(archive.read(notes_file))
                notes_vals = [node.text.strip() for node in notes_root.iter() if (node.tag.endswith("}t") or node.tag == "t") and (node.text or "").strip()]
                if notes_vals:
                    values.append("Notes: " + " ".join(notes_vals))
            value = " ".join(values).strip()
            if value:
                segments.append({
                    "kind": "slide",
                    "segment_type": "presentation_slide",
                    "ordinal": idx,
                    "page_number": slide_no,
                    "title": f"{path.name} — slide {slide_no}",
                    "text": value[:50000],
                })
    return _result(segments, chunk_chars, chunk_overlap)


def _extract_odp(path: Path, chunk_chars: int, chunk_overlap: int) -> dict:
    segments: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    pages = [el for el in root.iter() if el.tag.endswith("}page") or el.tag == "page"]
    if pages:
        for idx, page in enumerate(pages[:1000], start=1):
            value = " ".join("".join(page.itertext()).split())
            if value:
                segments.append({
                    "kind": "slide",
                    "segment_type": "presentation_slide",
                    "ordinal": idx,
                    "page_number": idx,
                    "title": f"{path.name} — slide {idx}",
                    "text": value[:50000],
                })
    else:
        value = " ".join("".join(root.itertext()).split())
        if value:
            segments.append({
                "kind": "slide",
                "segment_type": "presentation_slide",
                "ordinal": 1,
                "page_number": 1,
                "title": f"{path.name} — slide 1",
                "text": value[:50000],
            })
    return _result(segments, chunk_chars, chunk_overlap)


def _result(raw_segments: list[dict], chunk_chars: int = 1200, chunk_overlap: int = 180) -> dict:
    chunked = _chunk_Units(raw_segments, max_chars=chunk_chars, overlap=chunk_overlap)
    return {
        "text": "\n".join(item["text"] for item in raw_segments if item.get("text", "").strip())[:500000],
        "segments": chunked,
        "page_count": len(raw_segments),
    }
