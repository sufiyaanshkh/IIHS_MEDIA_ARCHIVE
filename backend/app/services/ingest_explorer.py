"""Windows Explorer drive selection and ingestion service for IIHS Media Archive.
Enables selecting whole drives (e.g. D:\\, E:\\) or folders directly via native
Windows Explorer, registering them as approved sources, and initiating preview/ingestion.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import IngestionJob, JobState, SourceDefinition, User

log = logging.getLogger("iihs.ingest_explorer")


def get_system_drives() -> list[dict[str, Any]]:
    """Query live Windows drives from local_ml_service or local ctypes fallback."""
    # Try local_ml_service on port 8765 first
    try:
        url = f"{settings.local_ml_url}/explorer/drives"
        headers = {}
        if settings.local_ml_token:
            headers["Authorization"] = f"Bearer {settings.local_ml_token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data.get("drives", [])
    except Exception as e:
        log.debug("local_ml_service drives query failed: %s", e)

    # Local ctypes fallback (when running directly on Windows host)
    try:
        import ctypes
        import string

        kernel32 = getattr(ctypes, "windll", None) and getattr(ctypes.windll, "kernel32", None)
        if kernel32:
            results = []
            type_names = {2: "Removable/USB", 3: "Fixed HDD/SSD", 4: "Network", 5: "CD/DVD", 6: "RAMDisk"}
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                dtype = kernel32.GetDriveTypeW(root)
                if dtype <= 1:
                    continue
                vol_name = ctypes.create_unicode_buffer(261)
                fs_name = ctypes.create_unicode_buffer(261)
                free_bytes = ctypes.c_ulonglong(0)
                total_bytes = ctypes.c_ulonglong(0)
                total_free_bytes = ctypes.c_ulonglong(0)
                ok = kernel32.GetVolumeInformationW(root, vol_name, 260, None, None, None, fs_name, 260)
                if ok:
                    kernel32.GetDiskFreeSpaceExW(root, ctypes.byref(free_bytes), ctypes.byref(total_bytes), ctypes.byref(total_free_bytes))
                    results.append({
                        "drive": root,
                        "letter": letter,
                        "type": type_names.get(dtype, "Unknown"),
                        "label": vol_name.value or "Local Disk",
                        "filesystem": fs_name.value,
                        "total_gb": round(total_bytes.value / (1024**3), 2),
                        "free_gb": round(free_bytes.value / (1024**3), 2),
                        "ready": True,
                    })
            return results
    except Exception:
        pass
    return []


def browse_drive_from_explorer(title: str = "Select Drive or Folder for IIHS Media Archive Ingestion") -> dict[str, Any]:
    """Launch the native Windows Explorer folder/drive picker dialog on the desktop."""
    try:
        url = f"{settings.local_ml_url}/explorer/browse"
        headers = {"Content-Type": "application/json"}
        if settings.local_ml_token:
            headers["Authorization"] = f"Bearer {settings.local_ml_token}"
        req = urllib.request.Request(url, data=json.dumps({"title": title}).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.warning("Could not open Explorer dialog via local_ml_service: %s", e)

    # Local Tkinter fallback (if running on host outside Docker)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title=title)
        root.destroy()
        if selected:
            norm = os.path.normpath(selected)
            return {"cancelled": False, "path": norm, "name": f"Folder ({norm})"}
    except Exception:
        pass

    return {"cancelled": True, "path": None, "error": "Explorer dialog was cancelled or unavailable."}


def sync_active_drive_env(path: str) -> bool:
    """Notify local_ml_service or update .env so HDD_HOST_PATH targets the selected drive."""
    try:
        url = f"{settings.local_ml_url}/explorer/set-active-drive"
        headers = {"Content-Type": "application/json"}
        if settings.local_ml_token:
            headers["Authorization"] = f"Bearer {settings.local_ml_token}"
        req = urllib.request.Request(url, data=json.dumps({"path": path}).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return True
    except Exception as e:
        log.debug("Could not sync active drive env via local_ml_service: %s", e)

    # Local fallback
    try:
        env_file = Path(".env").resolve()
        if env_file.is_file():
            lines = env_file.read_text(encoding="utf-8").splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("HDD_HOST_PATH="):
                    new_lines.append(f"HDD_HOST_PATH={path}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"HDD_HOST_PATH={path}")
            env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def register_drive_source(
    db: Session,
    drive_path: str,
    name: str | None = None,
    user_id: str | None = None,
) -> SourceDefinition:
    """Register or update a drive/folder path as an approved ingestion source."""
    norm_path = os.path.normpath(drive_path)
    # Check if this path maps to Docker approved source mount
    effective_path = norm_path
    if not os.path.exists(effective_path) and os.path.exists("/mnt/approved_sources/IIHS_External_HDD"):
        # Running inside Docker container
        effective_path = "/mnt/approved_sources/IIHS_External_HDD"

    # Generate stable source_id based on the original path
    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"iihs-source:{norm_path}"))
    source_name = name or f"Drive ({norm_path})"

    existing = db.get(SourceDefinition, source_id)
    if existing:
        existing.name = source_name
        existing.root_path = effective_path
        existing.enabled = True
        db.commit()
        db.refresh(existing)
        sync_active_drive_env(norm_path)
        return existing

    new_source = SourceDefinition(
        id=source_id,
        name=source_name,
        root_path=effective_path,
        is_demo=False,
        enabled=True,
    )
    db.add(new_source)
    db.commit()
    db.refresh(new_source)
    sync_active_drive_env(norm_path)
    return new_source

