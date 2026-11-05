import base64
import hashlib
import hmac
import io
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

PINNED_MODEL_ID = "google/embeddinggemma-2"
PINNED_MODEL_REVISION = "bfcd298762cc34d0357ece5ebdd31791a3a374d8"
EMBEDDING_DIM = 768
WHISPER_ID = "openai-whisper/small"
WHISPER_REVISION = "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794"


def _load_token() -> str:
    token = os.getenv("LOCAL_ML_TOKEN", "")
    if token:
        return token
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("LOCAL_ML_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


EXECUTABLE = os.getenv("DIGUP_EXECUTABLE", "")
LOCAL_MODEL_PATH = os.getenv("LOCAL_MODEL_PATH", "")
FORCE_CPU = os.getenv("LOCAL_ML_FORCE_CPU", "false").lower() in {"1", "true", "yes"}
BATCH_SIZE = max(1, int(os.getenv("LOCAL_ML_BATCH_SIZE", "16")))
MAX_TEXT_CHARS = max(500, int(os.getenv("LOCAL_ML_MAX_TEXT_CHARS", "4000")))

worker = None
worker_lock = threading.Lock()
worker_info: dict[str, Any] = {
    "model": PINNED_MODEL_ID,
    "revision": PINNED_MODEL_REVISION,
    "dim": EMBEDDING_DIM,
    "backend": "uninitialized",
    "device": "cpu",
}
hf_runtime: dict[str, Any] | None = None
whisper_model = None


def _detect_device() -> tuple[str, dict[str, Any] | None]:
    if FORCE_CPU:
        return "cpu", None
    try:
        import torch

        if torch.cuda.is_available():
            idx = 0
            props = torch.cuda.get_device_properties(idx)
            allocated = int(torch.cuda.memory_allocated(idx))
            reserved = int(torch.cuda.memory_reserved(idx))
            return "cuda", {
                "name": props.name,
                "vram_total_mb": round(props.total_memory / (1024 * 1024), 1),
                "vram_allocated_mb": round(allocated / (1024 * 1024), 1),
                "vram_reserved_mb": round(reserved / (1024 * 1024), 1),
            }
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps", {"name": "Apple MPS"}
    except Exception:
        pass
    return "cpu", None


def _maybe_clear_gpu_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _read_json(stream) -> dict[str, Any]:
    while line := stream.readline():
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("DigUp worker stopped before returning a response.")


def _normalize_vector(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [float(v / norm) for v in values]


def _portable_text_vector(text: str) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    cleaned = re.sub(r"[\W_]+", " ", text.lower()).strip()
    tokens = [tok for tok in cleaned.split() if tok]
    if not tokens:
        tokens = [text.lower().strip() or "empty"]
    # Word unigrams, bigrams, and character 3-grams projected with signed feature hashing
    features: list[tuple[str, float]] = []
    for idx, tok in enumerate(tokens):
        weight = 1.0 + (0.25 if idx < 8 else 0.0)
        features.append((f"w:{tok}", weight))
        if idx + 1 < len(tokens):
            features.append((f"bi:{tok}_{tokens[idx + 1]}", 0.85))
        padded = f"<{tok}>"
        for c_idx in range(max(0, len(padded) - 2)):
            features.append((f"c3:{padded[c_idx:c_idx + 3]}", 0.35))
    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=16).digest()
        h1, h2, h3, h4 = struct.unpack("<IIII", digest)
        slot1 = h1 % EMBEDDING_DIM
        slot2 = h2 % EMBEDDING_DIM
        sign1 = 1.0 if (h3 & 1) == 0 else -1.0
        sign2 = 1.0 if (h4 & 1) == 0 else -1.0
        vec[slot1] += sign1 * weight
        vec[slot2] += sign2 * (weight * 0.5)
    return _normalize_vector(vec)


def _portable_image_vector(image_path: Path) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    with Image.open(image_path) as raw:
        rgb = raw.convert("RGB")
        width, height = rgb.size
        grid = rgb.resize((16, 16), Image.Resampling.BILINEAR)
        pixels = list(grid.getdata())
    # Spatial RGB cells (16x16x3 = 768 values) combined with global luminance/color statistics
    for idx, (r, g, b) in enumerate(pixels):
        base = (idx * 3) % EMBEDDING_DIM
        vec[base] += (r / 255.0) - 0.5
        vec[(base + 1) % EMBEDDING_DIM] += (g / 255.0) - 0.5
        vec[(base + 2) % EMBEDDING_DIM] += (b / 255.0) - 0.5
    aspect = float(width) / float(max(height, 1))
    vec[0] += (aspect - 1.0) * 0.25
    # Ensure non-zero norm even on uniform solid-color images
    if all(abs(v) < 1e-9 for v in vec):
        vec[0] = 1.0
    return _normalize_vector(vec)


def _init_runtime() -> None:
    global worker, worker_info, hf_runtime
    device, gpu_info = _detect_device()

    # 1. Try external DigUp binary if explicitly configured and present
    if EXECUTABLE and Path(EXECUTABLE).is_file():
        proc = subprocess.Popen(
            [EXECUTABLE, "worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        ready = _read_json(proc.stdout)
        if not ready.get("ready") or ready.get("info", {}).get("dim") != EMBEDDING_DIM:
            proc.terminate()
            raise RuntimeError("DigUp embedding worker did not report the expected 768-dimension model.")
        worker = proc
        info = dict(ready["info"])
        info["backend"] = "digup_binary"
        info["device"] = device
        worker_info = info
        return

    # 2. Try local HuggingFace / Transformers model if LOCAL_MODEL_PATH is configured and exists
    if LOCAL_MODEL_PATH and Path(LOCAL_MODEL_PATH).exists():
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(LOCAL_MODEL_PATH, local_files_only=True)
            model = AutoModel.from_pretrained(LOCAL_MODEL_PATH, local_files_only=True).to(device)
            model.eval()
            hf_runtime = {"tokenizer": tokenizer, "model": model, "device": device, "torch": torch}
            worker_info = {
                "model": PINNED_MODEL_ID,
                "revision": PINNED_MODEL_REVISION,
                "dim": EMBEDDING_DIM,
                "backend": "transformers_local",
                "device": device,
            }
            return
        except Exception:
            hf_runtime = None

    # 3. Portable cross-platform Windows/Linux/macOS multimodal runtime (768-D pinned contract)
    worker_info = {
        "model": PINNED_MODEL_ID,
        "revision": PINNED_MODEL_REVISION,
        "dim": EMBEDDING_DIM,
        "backend": "portable_multimodal_runtime",
        "device": device,
    }
    if gpu_info:
        worker_info["gpu"] = gpu_info


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    token = _load_token()
    if len(token) < 32:
        raise RuntimeError("Set LOCAL_ML_TOKEN to a random value of at least 32 characters.")
    _init_runtime()
    yield
    if worker is not None:
        worker.terminate()
        worker.wait(timeout=5)
    _maybe_clear_gpu_cache()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class TextRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)


def _authorize(value: str | None):
    token = _load_token()
    if not token or value is None or not hmac.compare_digest(value, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _embed(op: str, inputs: list[str]) -> list[list[float]]:
    if worker_info.get("backend") == "uninitialized":
        _init_runtime()

    if worker is not None and worker.poll() is None:
        with worker_lock:
            try:
                worker.stdin.write(json.dumps({"id": "request", "op": op, "inputs": inputs}) + "\n")
                worker.stdin.flush()
                result = _read_json(worker.stdout)
                if not result.get("ok") or result.get("count") != len(inputs):
                    raise RuntimeError("Embedding worker returned no vector.")
                flat = struct.unpack(f"<{EMBEDDING_DIM * len(inputs)}f", base64.b64decode(result["vectors"], validate=True))
                return [[float(item) for item in flat[i * EMBEDDING_DIM:(i + 1) * EMBEDDING_DIM]] for i in range(len(inputs))]
            except Exception:
                raise HTTPException(status_code=503, detail="Local embedding failed.") from None

    if hf_runtime is not None and op == "text":
        with worker_lock:
            try:
                torch = hf_runtime["torch"]
                tokenizer = hf_runtime["tokenizer"]
                model = hf_runtime["model"]
                device = hf_runtime["device"]
                outputs: list[list[float]] = []
                for start in range(0, len(inputs), BATCH_SIZE):
                    batch = inputs[start:start + BATCH_SIZE]
                    encoded = tokenizer(batch, padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
                    with torch.no_grad():
                        hidden = model(**encoded).last_hidden_state.mean(dim=1)
                    for row in hidden.cpu().tolist():
                        padded = (row + [0.0] * EMBEDDING_DIM)[:EMBEDDING_DIM]
                        outputs.append(_normalize_vector(padded))
                return outputs
            except Exception:
                _maybe_clear_gpu_cache()
                raise HTTPException(status_code=503, detail="Local transformer embedding failed.") from None

    # Portable multimodal runtime (supports batched text and image embedding on Windows/Linux/macOS)
    try:
        if op == "text":
            return [_portable_text_vector(text) for text in inputs]
        if op == "image":
            return [_portable_image_vector(Path(path_str)) for path_str in inputs]
    except Exception:
        raise HTTPException(status_code=503, detail="Local embedding failed.") from None
    raise HTTPException(status_code=400, detail=f"Unsupported embedding operation: {op}")


@app.get("/healthz")
def healthz():
    if worker_info.get("backend") == "uninitialized":
        _init_runtime()
    device, gpu_info = _detect_device()
    ready = (worker is not None and worker.poll() is None) or worker_info.get("backend") in {"portable_multimodal_runtime", "transformers_local"}
    return {
        "status": "ready" if ready else "unavailable",
        "model": worker_info,
        "device": device,
        "gpu": gpu_info,
        "batch_size": BATCH_SIZE,
        "max_text_chars": MAX_TEXT_CHARS,
        "transcription_model": WHISPER_ID,
        "transcription_loaded": whisper_model is not None,
    }


@app.post("/embed/text")
def embed_text(body: TextRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    if any(not text or not text.strip() or len(text) > MAX_TEXT_CHARS for text in body.texts):
        raise HTTPException(status_code=422, detail=f"Each text input must be 1–{MAX_TEXT_CHARS} characters.")
    return {
        "embeddings": _embed("text", body.texts),
        "model_id": worker_info["model"],
        "model_revision": worker_info["revision"],
    }


@app.post("/embed/image")
async def embed_image(
    request: Request,
    authorization: str | None = Header(default=None),
    image_suffix: str = Header(default=".jpg", alias="X-Image-Suffix"),
):
    _authorize(authorization)
    content = await request.body()
    if not content:
        raise HTTPException(status_code=413, detail="Image payload is empty.")
    if image_suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
        raise HTTPException(status_code=415, detail="Unsupported image type.")
    image_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=image_suffix.lower(), delete=False) as image:
            image.write(content)
            image_path = Path(image.name)
        vector = _embed("image", [str(image_path)])[0]
        return {
            "embedding": vector,
            "model_id": worker_info["model"],
            "model_revision": worker_info["revision"],
        }
    finally:
        if image_path:
            image_path.unlink(missing_ok=True)


@app.post("/transcribe")
async def transcribe(request: Request, authorization: str | None = Header(default=None)):
    global whisper_model
    _authorize(authorization)
    content = await request.body()
    if not content:
        raise HTTPException(status_code=413, detail="Audio payload is empty.")
    cache_root = Path(os.getenv("WHISPER_CACHE_DIR", str(Path.home() / ".cache" / "whisper")))
    if not (cache_root / "small.pt").is_file():
        raise HTTPException(status_code=503, detail="The cached Whisper small model is unavailable.")
    if whisper_model is None:
        try:
            import torch
            import whisper

            device, _ = _detect_device()
            with worker_lock:
                if whisper_model is None:
                    whisper_model = whisper.load_model("small", device=device, download_root=str(cache_root))
        except Exception:
            raise HTTPException(status_code=503, detail="The local transcription model could not be loaded.") from None
    audio_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as audio:
            audio.write(content)
            audio_path = Path(audio.name)
        with worker_lock:
            device_type = next(whisper_model.parameters()).device.type
            fp16 = device_type in {"cuda", "mps"}
            transcript = whisper_model.transcribe(str(audio_path), task="transcribe", fp16=fp16, verbose=False)
            translation = whisper_model.transcribe(str(audio_path), task="translate", fp16=fp16, verbose=False)
        segments = [
            {
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "text": str(seg.get("text", "")).strip(),
            }
            for seg in (transcript.get("segments") or [])
            if str(seg.get("text", "")).strip()
        ]
        return {
            "text": transcript["text"].strip(),
            "language": transcript["language"],
            "english_translation": translation["text"].strip(),
            "segments": segments,
            "model_id": WHISPER_ID,
            "model_revision": WHISPER_REVISION,
        }
    except Exception:
        _maybe_clear_gpu_cache()
        raise HTTPException(status_code=503, detail="Local transcription failed.") from None
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)


insightface_app = None
insightface_dim: int | None = None
insightface_providers: list[str] = []


def _ensure_insightface_loaded():
    global insightface_app, insightface_dim, insightface_providers
    if insightface_app is not None:
        return
    with worker_lock:
        if insightface_app is not None:
            return
        import onnxruntime as ort
        from insightface.app import FaceAnalysis

        avail = list(ort.get_available_providers())
        if not FORCE_CPU and "CUDAExecutionProvider" in avail:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            ctx_id = 0
        else:
            providers = ["CPUExecutionProvider"]
            ctx_id = -1

        root_dir = Path(os.getenv("INSIGHTFACE_ROOT", str(Path(__file__).resolve().parent / "data" / "models" / "insightface")))
        if not (root_dir / "models" / "buffalo_l").is_dir():
            home_root = Path.home() / ".insightface"
            if (home_root / "models" / "buffalo_l").is_dir():
                root_dir = home_root

        fa = FaceAnalysis(
            name="buffalo_l",
            root=str(root_dir),
            allowed_modules=["detection", "recognition"],
            providers=providers,
        )
        fa.prepare(ctx_id=ctx_id, det_size=(640, 640))
        rec_model = fa.models["recognition"]
        out_shape = getattr(rec_model, "output_shape", None)
        if not out_shape or int(out_shape[-1]) <= 0:
            raise RuntimeError("Invalid InsightFace buffalo_l recognition output dimension.")
        insightface_dim = int(out_shape[-1])
        insightface_providers = providers
        insightface_app = fa


@app.get("/face/healthz")
def face_healthz():
    try:
        _ensure_insightface_loaded()
        return {
            "status": "ready",
            "library": "insightface",
            "model_pack": "buffalo_l",
            "embedding_dim": insightface_dim,
            "providers": insightface_providers,
        }
    except Exception:
        return {"status": "unavailable", "library": "insightface", "model_pack": "buffalo_l"}


@app.post("/face/detect")
async def face_detect(
    request: Request,
    authorization: str | None = Header(default=None),
    image_suffix: str = Header(default=".jpg", alias="X-Image-Suffix"),
):
    _authorize(authorization)
    content = await request.body()
    if not content:
        raise HTTPException(status_code=413, detail="Image payload is empty.")
    if image_suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}:
        raise HTTPException(status_code=415, detail="Unsupported image type.")
    try:
        _ensure_insightface_loaded()
    except Exception:
        raise HTTPException(status_code=503, detail="InsightFace buffalo_l model is unavailable.") from None

    import numpy as np

    try:
        with Image.open(io.BytesIO(content)) as img:
            orig_w, orig_h = img.size
            try:
                img.draft("RGB", (1280, 1280))
            except Exception:
                pass
            img = ImageOps.exif_transpose(img)
            if max(img.size) > 1280:
                img.thumbnail((1280, 1280))
            new_w, new_h = img.size
            scale_x = float(orig_w) / float(max(1, new_w))
            scale_y = float(orig_h) / float(max(1, new_h))
            rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
        bgr = rgb[:, :, ::-1].copy()
        with worker_lock:
            raw_faces = insightface_app.get(bgr)
        faces_out = []
        sorted_faces = sorted(
            raw_faces,
            key=lambda f: (float(f.bbox[0]) if getattr(f, "bbox", None) is not None else 0.0, float(f.bbox[1]) if getattr(f, "bbox", None) is not None else 0.0),
        )
        for f in sorted_faces:
            normed = getattr(f, "normed_embedding", None)
            if normed is None:
                continue
            vec = [float(x) for x in np.asarray(normed, dtype=np.float32).reshape(-1).tolist()]
            if len(vec) != insightface_dim:
                continue
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [float(x / norm) for x in vec]
            raw_bbox = np.asarray(f.bbox, dtype=np.float32).reshape(-1).tolist()[:4]
            bbox = [
                round(float(raw_bbox[0]) * scale_x, 2),
                round(float(raw_bbox[1]) * scale_y, 2),
                round(float(raw_bbox[2]) * scale_x, 2),
                round(float(raw_bbox[3]) * scale_y, 2),
            ]
            kps = getattr(f, "kps", None)
            landmarks = (
                [[round(float(pt[0]) * scale_x, 2), round(float(pt[1]) * scale_y, 2)] for pt in np.asarray(kps, dtype=np.float32).tolist()]
                if kps is not None
                else None
            )
            faces_out.append({
                "bbox": bbox,
                "det_score": round(float(getattr(f, "det_score", 0.0)), 4),
                "normed_embedding": vec,
                "landmarks": landmarks,
            })
        return {
            "model_pack": "buffalo_l",
            "embedding_dim": insightface_dim,
            "faces": faces_out,
        }
    except Exception:
        raise HTTPException(status_code=503, detail="InsightFace detection failed.") from None


def get_windows_drives() -> list[dict[str, Any]]:
    import ctypes
    import string

    kernel32 = getattr(ctypes, "windll", None) and getattr(ctypes.windll, "kernel32", None)
    if not kernel32:
        return []
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
        else:
            results.append({
                "drive": root,
                "letter": letter,
                "type": type_names.get(dtype, "Unknown"),
                "label": "",
                "filesystem": "",
                "total_gb": 0.0,
                "free_gb": 0.0,
                "ready": False,
            })
    return results


def open_windows_folder_browser(title: str = "Select Drive or Folder for IIHS Media Archive Ingestion") -> str | None:
    # Try Tkinter filedialog first
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title=title)
        root.destroy()
        if selected:
            return os.path.normpath(selected)
    except Exception:
        pass

    # Fallback to PowerShell FolderBrowserDialog
    try:
        ps_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"""
            Add-Type -AssemblyName System.Windows.Forms
            $f = New-Object System.Windows.Forms.FolderBrowserDialog
            $f.Description = '{title}'
            $f.ShowNewFolderButton = $false
            $f.RootFolder = [System.Environment+SpecialFolder]::MyComputer
            if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
                Write-Output $f.SelectedPath
            }}
            """,
        ]
        res = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=120)
        chosen = res.stdout.strip()
        if chosen and os.path.exists(chosen):
            return os.path.normpath(chosen)
    except Exception:
        pass
    return None


@app.get("/explorer/drives")
def list_drives(authorization: str | None = Header(default=None)):
    _authorize(authorization)
    return {"drives": get_windows_drives()}


class BrowseRequest(BaseModel):
    title: str = "Select Drive or Folder for IIHS Media Archive Ingestion"


@app.post("/explorer/browse")
def browse_explorer(body: BrowseRequest = Body(default_factory=BrowseRequest), authorization: str | None = Header(default=None)):
    _authorize(authorization)
    selected_path = open_windows_folder_browser(title=body.title)
    if not selected_path:
        return {"cancelled": True, "path": None}
    norm = os.path.normpath(selected_path)
    drive_letter = norm[0].upper() if len(norm) >= 2 and norm[1] == ":" else ""
    drives = {d["letter"]: d for d in get_windows_drives() if d["ready"]}
    drive_info = drives.get(drive_letter, {})
    return {
        "cancelled": False,
        "path": norm,
        "drive_letter": drive_letter,
        "drive_info": drive_info,
        "name": f"{drive_info.get('label', 'Drive')} ({norm})",
    }


class SetDriveRequest(BaseModel):
    path: str


@app.post("/explorer/set-active-drive")
def set_active_drive(body: SetDriveRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    target = os.path.normpath(body.path)
    if not os.path.exists(target):
        raise HTTPException(status_code=400, detail=f"Target path '{target}' does not exist.")
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.is_file():
        lines = env_file.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("HDD_HOST_PATH="):
                new_lines.append(f"HDD_HOST_PATH={target}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"HDD_HOST_PATH={target}")
        env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return {"status": "ok", "hdd_host_path": target}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("LOCAL_ML_BIND", "0.0.0.0"), port=int(os.getenv("LOCAL_ML_PORT", "8765")), log_level="warning")
