"""IIHS vocabulary-aware path and filename context enrichment.

Matches are suggestions grounded in folder/file names. They are never treated as
visual detections or proof of visual/location content. The source vocabulary
remains the authority for IIHS terms.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import PurePath, Path

VOCABULARY_PATH = Path(__file__).resolve().parents[1] / "data" / "iihs_keywords.json"

# Controlled institutional aliases and abbreviations mapped to canonical IIHS keywords.
INSTITUTIONAL_ALIASES: dict[str, tuple[str, ...]] = {
    "IIHS Kengeri Campus": ("kengeri campus", "kengeri site", "iihs kengeri", "kengeri campus development"),
    "IIHS Bengaluru City Campus": ("city campus", "bengaluru city campus", "sadashivanagar campus", "iihs city campus"),
    "Urban Practitioners' Programme": ("upp", "urban practitioners programme", "upp programme"),
    "Urban Fellows Programme": ("ufp", "ufp programme", "urban fellows", "urban fellowship"),
    "Digital Blended Learning Programme": ("dbl", "dbl programme", "digital blended learning"),
    "School of Governance": ("sog", "school of governance"),
    "School of Economic Development": ("soed", "sed", "school of economic development"),
    "School of Human Development": ("sohd", "shd", "school of human development"),
    "School of Systems and Infrastructure": ("sosi", "ssi", "school of systems", "systems and infrastructure"),
    "School of Environment and Sustainability": ("soes", "ses", "school of environment", "environment and sustainability"),
    "Word Lab": ("word labs",),
    "Media Lab": ("media labs",),
    "International Advisory Committee": ("iac", "advisory committee"),
    "Internet of Things Lab": ("iot lab", "internet of things"),
    "Geospatial Lab": ("gis lab", "spatial lab"),
    "Learners' Hostel": ("learners hostel", "student hostel"),
    "IIHS at 15": ("iihs 15", "iihs@15", "15 years of iihs"),
}

CATEGORY_FACET_MAP: dict[str, str] = {
    "Programmes": "programmes",
    "Courses & Learning Products": "programmes",
    "Teaching & Learning": "programmes",
    "Schools": "departments_and_schools",
    "Functions & Operations": "departments_and_schools",
    "Labs": "labs",
    "Events, Festivals & Public Engagement": "events",
    "Campuses, Offices & Sites": "campuses",
    "Campus Architecture & Construction": "projects",
    "Campus Water Systems": "projects",
    "National Missions, Programmes & Projects": "projects",
    "Urban Themes & Sectors": "projects",
}

_YEAR_PATTERN = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")
_ISO_DATE_PATTERN = re.compile(
    r"(?<!\d)(?:(19[89]\d|20[0-3]\d)[-_/.](0[1-9]|1[0-2])[-_/.](0[1-9]|[12]\d|3[01])"
    r"|(0[1-9]|[12]\d|3[01])[-_/.](0[1-9]|1[0-2])[-_/.](19[89]\d|20[0-3]\d))(?!\d)"
)
_PROJECT_CODE_PATTERN = re.compile(
    r"\b((?:IIHS|PRJ|PROJ|UPP|UFP|DBL|SOG|SOED|SOHD|SOSI|SOES|KEN|BLR)[-_][A-Z0-9]{2,12}(?:[-_][A-Z0-9]{1,12})?)\b",
    re.IGNORECASE,
)
_GENERIC_FILENAMES = re.compile(r"^(?:img|dsc|dscn|pxl|vid|mov|mvi|whatsapp|photo|image|video|audio|scan|doc|page)[-_ ]?\d*$", re.IGNORECASE)


@lru_cache(maxsize=1)
def load_vocabulary() -> list[dict]:
    payload = json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))
    return [
        item for item in payload.get("keywords", [])
        if item.get("keyword") and item.get("category")
    ]


def _normalize_text(text: str) -> str:
    """Normalize spaces, underscores, hyphens, apostrophes and camelCase into space-separated words."""
    if not text:
        return ""
    # Split camelCase transitions (e.g. CampusDevelopment -> Campus Development)
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Remove apostrophes inside words so Practitioners' -> Practitioners
    expanded = re.sub(r"(?<=\w)['\u2019](?=\w|\b)", "", expanded)
    # Replace all non-alphanumeric characters AND underscores with spaces
    cleaned = re.sub(r"[\W_]+", " ", expanded.casefold(), flags=re.UNICODE)
    return " ".join(cleaned.split())


def _contains_term(text: str, term: str) -> bool:
    """Match complete phrases across spaces, underscores, hyphens and punctuation."""
    haystack = _normalize_text(text)
    needle = _normalize_text(term)
    if not haystack or not needle:
        return False
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def _match_item_in_text(text: str, keyword: str) -> tuple[bool, bool]:
    """Return (matched, via_alias) for a keyword or its configured aliases."""
    if _contains_term(text, keyword):
        return True, False
    for alias in INSTITUTIONAL_ALIASES.get(keyword, ()):
        if _contains_term(text, alias):
            return True, True
    return False, False


def extract_temporal_and_project_cues(parts: list[str]) -> dict:
    """Extract years, normalized ISO dates, and project identifiers from path segments."""
    joined = " / ".join(parts)
    years = sorted({int(match.group(1)) for match in _YEAR_PATTERN.finditer(joined)})
    dates: list[str] = []
    for match in _ISO_DATE_PATTERN.finditer(joined):
        g = match.groups()
        if g[0]:
            iso = f"{g[0]}-{g[1]}-{g[2]}"
        else:
            iso = f"{g[5]}-{g[4]}-{g[3]}"
        if iso not in dates:
            dates.append(iso)
    codes: list[str] = []
    for match in _PROJECT_CODE_PATTERN.finditer(joined):
        code = match.group(1).upper().replace("_", "-")
        if code not in codes:
            codes.append(code)
    return {
        "years": years,
        "dates": dates,
        "project_codes": codes,
    }


def _split_relative_parts(relative_path: str) -> list[str]:
    normalized = relative_path.replace("\\", "/")
    # Strip Windows drive prefix if accidentally passed in relative_path
    normalized = re.sub(r"^[A-Za-z]:/+", "", normalized)
    return [part.strip() for part in normalized.split("/") if part.strip() not in {"", ".", ".."}]


def infer_path_context(relative_path: str, parent_context: dict | None = None) -> dict:
    """Return traceable IIHS vocabulary matches from path, filename and ancestors."""
    parts = _split_relative_parts(relative_path)
    filename = parts[-1] if parts else relative_path
    stem = PurePath(filename).stem
    folders = parts[:-1]
    path_text = " / ".join(parts)

    matches = []
    facets: dict[str, list[str]] = {
        "projects": [],
        "events": [],
        "departments_and_schools": [],
        "programmes": [],
        "labs": [],
        "campuses": [],
    }

    for item in load_vocabulary():
        keyword = item["keyword"]
        in_path, via_alias = _match_item_in_text(path_text, keyword)
        if not in_path:
            continue
        in_filename, fn_alias = _match_item_in_text(stem, keyword)
        matching_folders = [
            folder for folder in folders
            if _match_item_in_text(folder, keyword)[0]
        ]
        if in_filename:
            evidence = "filename"
            confidence = 0.94 if fn_alias else 0.98
        elif matching_folders and folders and matching_folders[-1] == folders[-1]:
            evidence = "folder"
            confidence = 0.88 if via_alias else 0.92
        else:
            evidence = "folder"
            confidence = 0.86 if via_alias else 0.90

        matches.append({
            "keyword": keyword,
            "category": item["category"],
            "notes": item.get("notes", ""),
            "evidence": evidence,
            "evidence_detail": "filename" if in_filename else ("immediate_folder" if matching_folders and matching_folders[-1] == folders[-1] else "ancestor_folder"),
            "matched_folder": matching_folders[-1] if matching_folders else None,
            "inherited_from_parent": not in_filename and bool(matching_folders),
            "via_alias": via_alias,
            "confidence": confidence,
            "is_visual_claim": False,
        })
        facet_key = CATEGORY_FACET_MAP.get(item["category"])
        if facet_key and keyword not in facets[facet_key]:
            facets[facet_key].append(keyword)

    # Merge any explicitly passed parent_context matches that were not already found
    if parent_context and parent_context.get("matched_keywords"):
        existing_kw = {m["keyword"] for m in matches}
        for inherited in parent_context["matched_keywords"]:
            if inherited["keyword"] not in existing_kw:
                matches.append({
                    **inherited,
                    "evidence": "folder",
                    "evidence_detail": "ancestor_folder",
                    "inherited_from_parent": True,
                    "confidence": round(min(float(inherited.get("confidence", 0.9)), 0.89), 2),
                    "is_visual_claim": False,
                })

    matches.sort(key=lambda item: (item["evidence"] != "filename", -len(item["keyword"])))
    categories = sorted({item["category"] for item in matches})
    context_terms = [item["keyword"] for item in matches]
    cues = extract_temporal_and_project_cues(parts)

    # Infer primary project/event folder name from folder hierarchy (excluding generic root/year folders)
    meaningful_folders = [
        f for f in folders
        if not re.fullmatch(r"(?:19|20)\d{2}|raw|photos?|videos?|audio|docs?|documents?|finals?|selects?|exports?|archive", f.strip(), re.I)
    ]
    inferred_project = meaningful_folders[0] if meaningful_folders else (folders[0] if folders else None)
    immediate_event_or_topic = meaningful_folders[-1] if len(meaningful_folders) > 1 else None

    # Compute deterministic related_group_key for linking sibling/related files by folder, date and project context
    group_Basis = "|".join([
        "/".join(folders).casefold(),
        str(cues["dates"][0] if cues["dates"] else (cues["years"][0] if cues["years"] else "")),
        (inferred_project or "").casefold(),
    ])
    related_group_key = hashlib.sha1(group_Basis.encode("utf-8")).hexdigest()[:16] if (folders or cues["years"] or cues["dates"]) else None

    context_parts = ["IIHS archive path: " + path_text]
    if context_terms:
        context_parts.append("IIHS vocabulary: " + "; ".join(context_terms))
        context_parts.append("Categories: " + "; ".join(categories))
    if cues["years"]:
        context_parts.append("Years: " + ", ".join(str(y) for y in cues["years"]))
    if cues["project_codes"]:
        context_parts.append("Project codes: " + ", ".join(cues["project_codes"]))
    context_text = ". ".join(context_parts)[:12000]

    return {
        "relative_path": relative_path,
        "normalized_path": "/".join(parts),
        "filename": filename,
        "is_generic_filename": bool(_GENERIC_FILENAMES.match(stem)),
        "folder_hierarchy": folders,
        "inferred_project": inferred_project,
        "inferred_subcontext": immediate_event_or_topic,
        "matched_keywords": matches,
        "matched_categories": categories,
        "facets": facets,
        "extracted_years": cues["years"],
        "extracted_dates": cues["dates"],
        "project_codes": cues["project_codes"],
        "related_group_key": related_group_key,
        "context_text": context_text,
        "context_source": "path_and_filename",
        "is_visual_claim": False,
    }
