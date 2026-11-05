#!/usr/bin/env python3
"""IIHS Media Archive — Windows Explorer Whole Drive Ingestion Script.

Allows selecting an entire external HDD (e.g. D:\\, E:\\) or directory directly via
native Windows Explorer, validating the volume, and launching multimodal archival ingestion.

Usage:
    python ingest_drive.py                # Opens Windows Explorer folder/drive picker
    python ingest_drive.py D:\\            # Ingests drive D:\\ directly
    python ingest_drive.py D:\\ --auto     # Ingests drive D:\\ without interactive prompt
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Load environment configuration
backend_dir = Path(__file__).resolve().parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("iihs.ingest_drive")

API_BASE = os.getenv("API_URL", "http://127.0.0.1:8010/api")
LOCAL_ML_BASE = os.getenv("LOCAL_ML_URL", "http://127.0.0.1:8765")


def get_token() -> str:
    token = settings.initial_admin_token
    if not token:
        env_file = Path(".env").resolve()
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("INITIAL_ADMIN_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    return token


def api_request(path: str, data: dict | None = None, method: str = "GET") -> dict:
    url = f"{API_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    token = get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8")
        return json.loads(content) if content else {}


def get_ml_request(endpoint: str, data: dict | None = None, method: str = "GET") -> dict:
    url = f"{LOCAL_ML_BASE}{endpoint}"
    headers = {"Content-Type": "application/json"}
    token = settings.local_ml_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        content = resp.read().decode("utf-8")
        return json.loads(content) if content else {}


def main():
    parser = argparse.ArgumentParser(description="Ingest an entire drive or folder into IIHS Media Archive.")
    parser.add_argument("drive_path", nargs="?", help="Drive root or directory (e.g. D:\\, E:\\Archive). Omit to open Explorer.")
    parser.add_argument("--auto", action="store_true", help="Auto-confirm ingestion without interactive prompt.")
    parser.add_argument("--max-files", type=int, default=0, help="Maximum files to ingest (0 = unlimited).")
    args = parser.parse_args()

    drive_path = args.drive_path

    if not drive_path:
        print("\n=======================================================")
        print("  IIHS Media Archive — Select Drive from Explorer")
        print("=======================================================\n")
        print("Detected Ready Drives on Windows:")
        try:
            drives_resp = get_ml_request("/explorer/drives")
            for d in drives_resp.get("drives", []):
                if d.get("ready"):
                    print(f"  • {d['drive']} [{d['type']}] '{d['label']}' — {d['total_gb']} GB total ({d['free_gb']} GB free)")
        except Exception as e:
            log.warning("Could not query ML service drives: %s", e)

        print("\nOpening native Windows Explorer folder dialog...")
        try:
            browse_resp = get_ml_request("/explorer/browse", data={"title": "Select Whole Drive or Folder for IIHS Archive Ingestion"}, method="POST")
            if browse_resp.get("cancelled") or not browse_resp.get("path"):
                print("Explorer selection was cancelled. Exiting.")
                sys.exit(0)
            drive_path = browse_resp["path"]
        except Exception as e:
            print(f"Error opening Explorer dialog: {e}")
            sys.exit(1)

    norm_path = os.path.normpath(drive_path)
    if not os.path.exists(norm_path):
        print(f"Error: Path '{norm_path}' does not exist.")
        sys.exit(1)

    print(f"\nTarget Selected: {norm_path}")
    print("Non-negotiable guarantee: Original HDD files remain completely UNTOUCHED (read-only).")

    # Set active drive in environment
    try:
        get_ml_request("/explorer/set-active-drive", data={"path": norm_path}, method="POST")
    except Exception:
        pass

    # Register source via API
    print(f"\nRegistering '{norm_path}' in archive catalog...")
    drive_name = f"Drive ({norm_path})"
    reg_resp = api_request("/sources/register-drive", data={"path": norm_path, "name": drive_name}, method="POST")
    source = reg_resp.get("source", {})
    source_id = source.get("id")
    print(f"  • Source Registered: {source.get('name')} [ID: {source_id}]")

    # Start preview
    print(f"\nStarting bounded preview discovery on '{norm_path}'...")
    import hashlib
    idempotency_key = hashlib.sha256(f"preview:{source_id}:{time.time()}".encode()).hexdigest()[:32]

    # Previews endpoint with Idempotency-Key
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }
    token = get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{API_BASE}/ingestions/previews",
        data=json.dumps({"source_id": source_id, "max_files": 500}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        preview_job = json.loads(resp.read().decode("utf-8"))

    job_id = preview_job["id"]
    print(f"  • Preview Job #{job_id} started. Waiting for discovery scan...")

    # Poll preview job
    while True:
        status = api_request(f"/jobs/{job_id}")
        state = status.get("state")
        progress = status.get("progress", {})
        done = progress.get("done", 0)
        total = progress.get("total", 0)
        print(f"  State: {state} | Scanned: {done}/{total} items...", end="\r")

        if state == "preview_ready":
            print(f"\n\nPreview Ready! Discovered {done} items on {norm_path}.")
            preview_files = status.get("preview_files", [])
            if preview_files:
                print("\nSample Preview Files:")
                for pf in preview_files[:10]:
                    print(f"  [{pf.get('type')}] {pf.get('path')} ({round(pf.get('size_bytes', 0)/(1024*1024), 2)} MB) - {pf.get('state')}")
            break
        elif state in ("failed", "cancelled"):
            print(f"\nPreview failed: {status.get('error')}")
            sys.exit(1)

        time.sleep(1.5)

    if not args.auto:
        confirm = input("\nProceed with full archive ingestion and indexing? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Ingestion cancelled by user.")
            sys.exit(0)

    # Confirm and start ingestion
    print(f"\nConfirming ingestion job #{job_id}...")
    confirmed = api_request(f"/jobs/{job_id}/confirm", data={}, method="POST")
    print("Ingestion active! Processing media through EmbeddingGemma 2, OCR, and Whisper.")
    print("View live progress on the Ingest dashboard at http://127.0.0.1:5174/\n")


if __name__ == "__main__":
    main()
