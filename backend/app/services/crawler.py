"""Intelligent recursive archive crawler with IIHS vocabulary propagation,
Windows symlink/junction protection, and resumable incremental discovery.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
from typing import Callable

from app.services.contextual_metadata import infer_path_context
from app.services.document_readers import SUPPORTED_DOCUMENT_EXTENSIONS

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
ALL_SUPPORTED_EXTENSIONS = (
    IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | SUPPORTED_DOCUMENT_EXTENSIONS
)

EXCLUDED_DIRECTORY_NAMES = {
    ".git", ".svn", ".hg", ".bzr", ".env", ".ssh", ".aws", ".gnupg",
    ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    "$recycle.bin", "system volume information", "recycler", "found.000",
    ".trash", ".trashes", ".spotlight-v100", ".fseventsd", ".temporaryitems",
}

EXCLUDED_FILE_NAMES = {
    "thumbs.db", "desktop.ini", ".ds_store", ".env", "id_rsa", "id_ed25519",
}

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000


def classify_extension(suffix: str) -> str | None:
    ext = suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in SUPPORTED_DOCUMENT_EXTENSIONS:
        return "document"
    return None


def is_reparse_or_symlink(path: Path) -> bool:
    """Detect POSIX symlinks and Windows NTFS junctions/reparse points."""
    try:
        if path.is_symlink():
            return True
        st = os.lstat(path)
        file_attrs = getattr(st, "st_file_attributes", 0)
        if file_attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    except OSError:
        return True
    return False


def is_cloud_placeholder(st: os.stat_result) -> bool:
    file_attrs = getattr(st, "st_file_attributes", 0)
    return bool(file_attrs & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS))


def is_macos_xsym_alias(path: Path, st: os.stat_result) -> bool:
    """Detect macOS Final Cut Pro X / Finder 'XSym' placeholder alias stubs (~1067 bytes)."""
    if 500 <= st.st_size <= 2048:
        try:
            with open(path, "rb") as f:
                header = f.read(5)
                return header == b"XSym\n"
        except OSError:
            pass
    return False


def is_excluded_directory(name: str) -> bool:
    cleaned = name.strip().casefold()
    if not cleaned or cleaned in {".", ".."}:
        return True
    if cleaned in EXCLUDED_DIRECTORY_NAMES:
        return True
    if cleaned.startswith(".") and cleaned not in {".well-known"}:
        return True
    return False


def is_excluded_filename(name: str) -> bool:
    cleaned = name.strip().casefold()
    if not cleaned or cleaned in EXCLUDED_FILE_NAMES:
        return True
    if cleaned.startswith("~$") or cleaned.startswith("._") or cleaned.endswith(".tmp") or cleaned.endswith(".part") or cleaned.endswith(".crdownload"):
        return True
    return False


@dataclass
class DiscoveredItem:
    path: Path
    relative_key: str
    filename: str
    media_type: str
    size_bytes: int
    mtime_ns: int
    device_inode: str
    context: dict = field(default_factory=dict)


@dataclass
class CrawlResult:
    items: list[DiscoveredItem] = field(default_factory=list)
    total_bytes: int = 0
    traversal_errors: int = 0
    skipped_unsupported: int = 0
    skipped_unsafe: int = 0
    skipped_resumed: int = 0
    truncated: bool = False
    last_relative_key: str | None = None
    related_groups: dict[str, list[str]] = field(default_factory=dict)


def crawl_archive_root(
    root: Path,
    limit: int = 0,
    resume_after_key: str | None = None,
    allowed_extensions: set[str] | None = None,
    heartbeat_callback: Callable[[int, int], None] | None = None,
) -> CrawlResult:
    """Recursively crawl an approved root, propagating parent folder context to child files."""
    resolved_root = root.resolve(strict=True)
    supported = allowed_extensions if allowed_extensions is not None else ALL_SUPPORTED_EXTENSIONS
    result = CrawlResult()
    folder_context_cache: dict[str, dict] = {}
    normalized_resume = PurePosixPath(resume_after_key.replace("\\", "/")).as_posix() if resume_after_key else None

    def on_error(_err: OSError):
        result.traversal_errors += 1

    for current_dir, dirnames, filenames in os.walk(resolved_root, followlinks=False, onerror=on_error):
        dir_path = Path(current_dir)
        # Filter out excluded, hidden, symlinked or junction directories in-place in sorted order
        safe_dirs: list[str] = []
        for dname in sorted(dirnames):
            if is_excluded_directory(dname):
                result.skipped_unsafe += 1
                continue
            candidate_dir = dir_path / dname
            if is_reparse_or_symlink(candidate_dir):
                result.skipped_unsafe += 1
                continue
            try:
                resolved_sub = candidate_dir.resolve(strict=True)
                if not resolved_sub.is_relative_to(resolved_root):
                    result.skipped_unsafe += 1
                    continue
            except OSError:
                result.traversal_errors += 1
                continue
            safe_dirs.append(dname)
        dirnames[:] = safe_dirs

        try:
            rel_dir = dir_path.relative_to(resolved_root).as_posix()
        except ValueError:
            result.skipped_unsafe += 1
            continue

        parent_ctx = None
        if rel_dir and rel_dir != ".":
            if rel_dir not in folder_context_cache:
                parent_rel = PurePosixPath(rel_dir).parent.as_posix()
                ancestor_ctx = folder_context_cache.get(parent_rel) if parent_rel != "." else None
                folder_context_cache[rel_dir] = infer_path_context(rel_dir + "/_folder_", parent_context=ancestor_ctx)
            parent_ctx = folder_context_cache[rel_dir]

        for fname in sorted(filenames):
            if is_excluded_filename(fname):
                result.skipped_unsafe += 1
                continue
            candidate = dir_path / fname
            if is_reparse_or_symlink(candidate):
                result.skipped_unsafe += 1
                continue
            suffix = candidate.suffix.lower()
            if suffix not in supported:
                result.skipped_unsupported += 1
                continue
            media_type = classify_extension(suffix)
            if not media_type:
                result.skipped_unsupported += 1
                continue
            try:
                resolved_file = candidate.resolve(strict=True)
                if not resolved_file.is_relative_to(resolved_root):
                    result.skipped_unsafe += 1
                    continue
                st = resolved_file.stat()
            except OSError:
                result.traversal_errors += 1
                continue

            if is_cloud_placeholder(st) or is_macos_xsym_alias(resolved_file, st):
                result.skipped_unsafe += 1
                continue

            relative_key = resolved_file.relative_to(resolved_root).as_posix()
            if normalized_resume and relative_key <= normalized_resume:
                result.skipped_resumed += 1
                continue

            if limit > 0 and len(result.items) >= limit:
                result.truncated = True
                break

            context = infer_path_context(relative_key, parent_context=parent_ctx)
            group_key = context.get("related_group_key")
            if group_key:
                result.related_groups.setdefault(group_key, []).append(relative_key)

            device_inode = f"{getattr(st, 'st_dev', 0)}:{getattr(st, 'st_ino', 0)}"
            item = DiscoveredItem(
                path=resolved_file,
                relative_key=relative_key,
                filename=fname,
                media_type=media_type,
                size_bytes=int(st.st_size),
                mtime_ns=int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                device_inode=device_inode,
                context=context,
            )
            result.items.append(item)
            result.total_bytes += item.size_bytes
            result.last_relative_key = relative_key

            if heartbeat_callback and len(result.items) % 50 == 0:
                heartbeat_callback(len(result.items), result.total_bytes)

        if result.truncated:
            break

    # Annotate related file counts and sample sibling references on each item's context
    for item in result.items:
        gk = item.context.get("related_group_key")
        if gk and gk in result.related_groups:
            siblings = [k for k in result.related_groups[gk] if k != item.relative_key]
            item.context["related_files_count"] = len(siblings)
            item.context["related_files_sample"] = siblings[:10]
        else:
            item.context["related_files_count"] = 0
            item.context["related_files_sample"] = []

    return result
