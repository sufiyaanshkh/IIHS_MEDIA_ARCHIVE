"""Build the IIHS keyword vocabulary used for AI keyword suggestions.

Reads IIHS_photo_tag_keywords_extracted_from_Institutional_book.xlsx (columns:
Category, Keyword, Notes) and writes backend/app/data/iihs_keywords.json.
City, state, country, region and other place-name keywords are excluded so the
model never suggests a location it cannot see in the image.

Usage: python3 scripts/build_keyword_vocabulary.py [path/to/workbook.xlsx]
Only the Python standard library is required.
"""
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = ROOT / "IIHS_photo_tag_keywords_extracted_from_Institutional_book.xlsx"
OUTPUT = ROOT / "backend" / "app" / "data" / "iihs_keywords.json"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Whole categories that are place names.
EXCLUDED_CATEGORIES = {
    "Geographies - International",
    "Geographies - Indian Cities & Towns",
    "Geographies - Indian States & UTs",
    "Regions & Global Framings",
}
# Individual place names found inside otherwise thematic categories.
EXCLUDED_KEYWORDS = {
    "sadashivanagar", "kengeri", "bengaluru", "chennai", "delhi", "mumbai", "trichy",
    "ramasagara lake", "sulikere forest", "western ghats", "eastern himalayas",
    "nilgiri biosphere reserve", "hindu kush himalaya",
}
# Notes that mark a row as a place even if a new category is added later.
PLACE_NOTE = re.compile(r"\b(city or town where|indian state or union territory|country of partnership)\b", re.I)


def _text(node):
    return "".join(t.text or "" for t in node.iter(NS + "t"))


def read_rows(path: Path):
    with zipfile.ZipFile(path) as workbook:
        shared = [_text(item) for item in ET.fromstring(workbook.read("xl/sharedStrings.xml")).findall(NS + "si")]
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    for row in sheet.iter(NS + "row"):
        values = {}
        for cell in row.findall(NS + "c"):
            column = re.match(r"[A-Z]+", cell.get("r", "")).group()
            value = cell.find(NS + "v")
            if value is None:
                inline = cell.find(NS + "is")
                values[column] = _text(inline) if inline is not None else ""
            else:
                values[column] = shared[int(value.text)] if cell.get("t") == "s" else (value.text or "")
        yield values


def build(path: Path):
    keywords, seen, excluded = [], set(), 0
    for index, row in enumerate(read_rows(path)):
        category, keyword, notes = (row.get(c, "").strip() for c in ("A", "B", "C"))
        if index == 0 or not keyword:
            continue
        key = " ".join(keyword.split()).casefold()
        if category in EXCLUDED_CATEGORIES or key in EXCLUDED_KEYWORDS or PLACE_NOTE.search(notes):
            excluded += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        keywords.append({"keyword": keyword, "category": category, "notes": notes})
    return keywords, excluded


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WORKBOOK
    items, skipped = build(source)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"source": source.name, "excluded_place_names": skipped, "keywords": items}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(items)} keywords to {OUTPUT.relative_to(ROOT)} ({skipped} place-name rows excluded).")

