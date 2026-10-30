import json
import math
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.config import settings
from app.services.media import MediaValidationError


def _vectors(result, field: str, expected: int):
    vectors = result.get(field)
    if result.get("model_id") != settings.embedding_model or result.get("model_revision") != settings.embedding_revision or not isinstance(vectors, list) or len(vectors) != expected or any(not isinstance(vector, list) or len(vector) != 768 or not all(isinstance(item, (int, float)) and math.isfinite(item) for item in vector) for vector in vectors):
        raise MediaValidationError("local embedding model response is incompatible")
    return vectors


def _post(path: str, content: bytes, content_type: str, extra_headers: dict[str, str] | None = None, timeout: int = 120):
    if not settings.local_ml_token:
        raise MediaValidationError("local embedding service token is not configured")
    request = Request(
        settings.local_ml_url + path,
        data=content,
        headers={"Authorization": f"Bearer {settings.local_ml_token}", "Content-Type": content_type, **(extra_headers or {})},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (OSError, URLError, ValueError):
        raise MediaValidationError("local embedding service is unavailable") from None


def image_embedding(path: Path):
    result = _post("/embed/image", path.read_bytes(), "application/octet-stream", {"X-Image-Suffix": path.suffix.lower()})
    vector = result.get("embedding")
    if result.get("model_id") != settings.embedding_model or result.get("model_revision") != settings.embedding_revision or not isinstance(vector, list) or len(vector) != 768 or not all(isinstance(item, (int, float)) and math.isfinite(item) for item in vector):
        raise MediaValidationError("local embedding model response is incompatible")
    return vector, settings.embedding_model, settings.embedding_revision


def text_embedding(text: str):
    return text_embeddings([text])[0]


def text_embeddings(texts: list[str]):
    result = _post("/embed/text", json.dumps({"texts": texts}).encode(), "application/json")
    return [(vector, settings.embedding_model, settings.embedding_revision) for vector in _vectors(result, "embeddings", len(texts))]


def transcribe_audio(content: bytes):
    result = _post("/transcribe", content, "audio/mpeg", timeout=1800)
    text = result.get("text")
    translation = result.get("english_translation")
    if result.get("model_id") != "openai-whisper/small" or result.get("model_revision") != "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794" or not isinstance(text, str) or not isinstance(translation, str) or len(text) > 500_000 or len(translation) > 500_000:
        raise MediaValidationError("local transcription response is incompatible")
    return result


def model_health():
    if not settings.local_ml_token:
        return {"status": "unavailable", "model": settings.embedding_model, "revision": settings.embedding_revision}
    try:
        with urlopen(settings.local_ml_url + "/healthz", timeout=1) as response:
            return json.loads(response.read())
    except (OSError, URLError, ValueError):
        return {"status": "unavailable", "model": settings.embedding_model, "revision": settings.embedding_revision}
