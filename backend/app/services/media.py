from pathlib import Path
from PIL import Image, ImageOps
import hashlib
import mimetypes
import subprocess
import json


SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif",
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm",
    ".pdf", ".txt", ".md", ".csv", ".docx", ".xlsx", ".pptx",
    ".mp3", ".wav", ".m4a", ".flac"
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def file_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def image_metadata(path: Path) -> dict:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        return {"width": image.width, "height": image.height, "format": image.format}


def create_thumbnail(source: Path, target: Path, max_size: tuple[int, int] = (480, 360)) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail(max_size)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        image.save(target, format="WEBP", quality=82, method=4)


def video_metadata(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30
    )
    payload = json.loads(result.stdout)
    streams = [x for x in payload.get("streams", []) if x.get("codec_type") == "video"]
    return {"duration_seconds": float(payload.get("format", {}).get("duration", 0)), "streams": streams}


def extract_pdf_text(path: Path, max_pages: int = 200, max_chars: int = 250_000) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages[:max_pages]:
        text = page.extract_text() or ""
        parts.append(text)
        if sum(map(len, parts)) >= max_chars:
            break
    return "\n".join(parts)[:max_chars]
