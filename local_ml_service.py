import base64
import hmac
import json
import os
import struct
import subprocess
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

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


EXECUTABLE = os.getenv("DIGUP_EXECUTABLE", "/Applications/DigUp.app/Contents/Helpers/digup")
TOKEN = _load_token()
worker = None
worker_lock = threading.Lock()
worker_info = None
whisper_model = None
WHISPER_ID = "openai-whisper/small"
WHISPER_REVISION = "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794"


def _read_json(stream):
    while line := stream.readline():
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("DigUp worker stopped before returning a response.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker, worker_info
    if len(TOKEN) < 32:
        raise RuntimeError("Set LOCAL_ML_TOKEN to a random value of at least 32 characters.")
    worker = subprocess.Popen([EXECUTABLE, "worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    ready = _read_json(worker.stdout)
    if not ready.get("ready") or ready.get("info", {}).get("dim") != 768:
        worker.terminate()
        raise RuntimeError("DigUp embedding worker did not report the expected 768-dimension model.")
    worker_info = ready["info"]
    yield
    worker.terminate()
    worker.wait(timeout=5)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class TextRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=32)


def _authorize(value: str | None):
    if value is None or not hmac.compare_digest(value, f"Bearer {TOKEN}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _embed(op: str, inputs: list[str]):
    if worker is None or worker.poll() is not None:
        raise HTTPException(status_code=503, detail="Local embedding model is unavailable.")
    with worker_lock:
        try:
            worker.stdin.write(json.dumps({"id": "request", "op": op, "inputs": inputs}) + "\n")
            worker.stdin.flush()
            result = _read_json(worker.stdout)
            if not result.get("ok") or result.get("count") != len(inputs):
                raise RuntimeError("Embedding worker returned no vector.")
            flat = struct.unpack(f"<{768 * len(inputs)}f", base64.b64decode(result["vectors"], validate=True))
            return [[float(item) for item in flat[i * 768:(i + 1) * 768]] for i in range(len(inputs))]
        except Exception:
            raise HTTPException(status_code=503, detail="Local embedding failed.") from None


@app.get("/healthz")
def healthz():
    return {"status": "ready" if worker is not None and worker.poll() is None else "unavailable", "model": worker_info, "transcription_model": WHISPER_ID, "transcription_loaded": whisper_model is not None}


@app.post("/embed/text")
def embed_text(body: TextRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    if any(not text or len(text) > 500 for text in body.texts):
        raise HTTPException(status_code=422, detail="Each text input must be 1–500 characters.")
    return {"embeddings": _embed("text", body.texts), "model_id": worker_info["model"], "model_revision": worker_info["revision"]}


@app.post("/embed/image")
def embed_image(content: bytes = Body(..., media_type="application/octet-stream"), authorization: str | None = Header(default=None), image_suffix: str = Header(default=".jpg", alias="X-Image-Suffix")):
    _authorize(authorization)
    if not content:
        raise HTTPException(status_code=413, detail="Image payload is empty.")
    if image_suffix not in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
        raise HTTPException(status_code=415, detail="Unsupported image type.")
    image_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=image_suffix, delete=False) as image:
            image.write(content)
            image_path = Path(image.name)
        vector = _embed("image", [str(image_path)])[0]
        return {"embedding": vector, "model_id": worker_info["model"], "model_revision": worker_info["revision"]}
    finally:
        if image_path:
            image_path.unlink(missing_ok=True)


@app.post("/transcribe")
def transcribe(content: bytes = Body(..., media_type="audio/mpeg"), authorization: str | None = Header(default=None)):
    global whisper_model
    _authorize(authorization)
    if not content:
        raise HTTPException(status_code=413, detail="Audio payload is empty.")
    cache = Path.home() / ".cache" / "whisper"
    if not (cache / "small.pt").is_file():
        raise HTTPException(status_code=503, detail="The cached Whisper small model is unavailable.")
    if whisper_model is None:
        try:
            import torch
            import whisper

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            with worker_lock:
                if whisper_model is None:
                    whisper_model = whisper.load_model("small", device=device, download_root=str(cache))
        except Exception:
            raise HTTPException(status_code=503, detail="The local transcription model could not be loaded.") from None
    audio_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as audio:
            audio.write(content)
            audio_path = Path(audio.name)
        with worker_lock:
            fp16 = next(whisper_model.parameters()).device.type == "mps"
            transcript = whisper_model.transcribe(str(audio_path), task="transcribe", fp16=fp16, verbose=False)
            translation = whisper_model.transcribe(str(audio_path), task="translate", fp16=fp16, verbose=False)
        return {"text": transcript["text"].strip(), "language": transcript["language"], "english_translation": translation["text"].strip(), "model_id": WHISPER_ID, "model_revision": WHISPER_REVISION}
    except Exception:
        raise HTTPException(status_code=503, detail="Local transcription failed.") from None
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("LOCAL_ML_BIND", "0.0.0.0"), port=int(os.getenv("LOCAL_ML_PORT", "8765")), log_level="warning")
