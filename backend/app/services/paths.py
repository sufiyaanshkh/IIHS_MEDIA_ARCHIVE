import json
from pathlib import Path
from ..config import settings


def approved_roots() -> list[Path]:
    try:
        values = json.loads(settings.ingest_roots_json)
    except json.JSONDecodeError as exc:
        raise ValueError("INGEST_ROOTS_JSON must be a JSON array of paths") from exc
    if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
        raise ValueError("INGEST_ROOTS_JSON must be a JSON array of paths")
    roots = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if path.exists() and path.is_dir():
            roots.append(path)
    return roots


def resolve_approved_path(candidate: str) -> Path:
    target = Path(candidate).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise ValueError("Source must be an existing directory")
    roots = approved_roots()
    for root in roots:
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    raise PermissionError("Source is outside administrator-approved ingest roots")
