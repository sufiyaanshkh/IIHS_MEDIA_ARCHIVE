import hashlib
from io import BytesIO
import json
import os
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path, PurePosixPath

from PIL import Image, ImageFile
from PIL import Image as PILImage

from app.config import settings

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = False
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
IMAGE_MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "TIFF": "image/tiff"}


class MediaValidationError(Exception):
    pass


def resolve_under_root(root: Path, relative_key: str) -> Path:
    relative = PurePosixPath(relative_key)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise MediaValidationError("invalid path")
    root = root.resolve(strict=True)
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise MediaValidationError("symlink skipped")
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise MediaValidationError("outside approved root")
    return candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_media(path: Path) -> dict:
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if size <= 0 or (settings.max_file_bytes and size > settings.max_file_bytes):
        raise MediaValidationError("file size outside configured limit")
    if suffix in IMAGE_EXTENSIONS:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", PILImage.DecompressionBombWarning)
                with Image.open(path) as image:
                    width, height = image.size
                    if width <= 0 or height <= 0:
                        raise MediaValidationError("image dimensions outside configured limit")
                    fmt = image.format
                    image.verify()
            mime = IMAGE_MIMES.get(fmt or "")
            if not mime:
                raise MediaValidationError("unsupported image encoding")
            return {"media_type": "image", "mime": mime, "size": size, "width": width, "height": height, "duration_ms": None}
        except MediaValidationError:
            raise
        except Exception:
            raise MediaValidationError("invalid image") from None
    if suffix in VIDEO_EXTENSIONS:
        executable = shutil.which(settings.ffprobe_path)
        if not executable:
            raise MediaValidationError("local FFprobe is unavailable")
        try:
            result = subprocess.run(
                [executable, "-protocol_whitelist", "file,pipe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,duration", "-of", "json", str(path)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False, env={"PATH": os.getenv("PATH", ""), "LC_ALL": "C"},
            )
            if result.returncode:
                raise MediaValidationError("invalid video")
            info = json.loads(result.stdout)
            video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video" and int(stream.get("width") or 0) > 0 and int(stream.get("height") or 0) > 0), None)
            raw_duration = info.get("format", {}).get("duration")
            if raw_duration in (None, "N/A") and video:
                raw_duration = video.get("duration")
            duration = float(raw_duration or 0)
            if not video or duration <= 0:
                raise MediaValidationError("video metadata outside configured limits")
            width, height = int(video.get("width", 0)), int(video.get("height", 0))
            if width <= 0 or height <= 0:
                raise MediaValidationError("video dimensions outside configured limits")
            mime = "video/quicktime" if suffix == ".mov" else "video/mp4"
            return {"media_type": "video", "mime": mime, "size": size, "width": width, "height": height, "duration_ms": max(1, int(duration * 1000))}
        except MediaValidationError:
            raise
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            raise MediaValidationError("video inspection failed") from None
    raise MediaValidationError("unsupported file type")


def _atomic_write(storage_root: Path, key: str, content: bytes):
    storage_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(storage_root).free - len(content) < settings.min_storage_free_bytes:
        raise MediaValidationError("storage capacity reached")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=storage_root, delete=False) as target:
            temp_path = Path(target.name)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        final_path = storage_root / key
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)
        return final_path
    except OSError:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise MediaValidationError("storage write failed") from None


def copy_original(source: Path, key: str) -> Path:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(settings.storage_root).free - source.stat().st_size < settings.min_storage_free_bytes:
        raise MediaValidationError("storage capacity reached")
    temp_path = None
    try:
        with source.open("rb") as reader, tempfile.NamedTemporaryFile(dir=settings.storage_root, delete=False) as writer:
            temp_path = Path(writer.name)
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    except OSError:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise MediaValidationError("storage write failed") from None
    final_path = settings.storage_root / key
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise MediaValidationError("storage write failed") from None
    return final_path


def image_thumbnail(source: Path, key: str) -> Path:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(settings.storage_root).free - 5 * 1024 * 1024 < settings.min_storage_free_bytes:
        raise MediaValidationError("storage capacity reached")
    with tempfile.NamedTemporaryFile(dir=settings.storage_root, suffix=".webp", delete=False) as output:
        temp_path = Path(output.name)
    try:
        with Image.open(source) as image:
            image.thumbnail((640, 640))
            image.convert("RGB").save(temp_path, format="WEBP", quality=78)
        final_path = settings.storage_root / key
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)
        return final_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise MediaValidationError("thumbnail generation failed") from None


def extract_image_text(source: Path) -> str | None:
    executable = shutil.which("tesseract")
    if not executable:
        raise MediaValidationError("local OCR is unavailable")
    try:
        result = subprocess.run(
            [executable, str(source), "stdout", "-l", "eng", "--psm", "6"],
            capture_output=True, text=True, timeout=20, check=False,
            env={"PATH": os.getenv("PATH", ""), "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MediaValidationError("local OCR failed") from None
    if result.returncode:
        raise MediaValidationError("local OCR failed")
    text = " ".join(result.stdout.split())[:5000]
    return text or None


def video_frame(source: Path, timestamp_ms: int, key: str) -> Path:
    executable = shutil.which(settings.ffmpeg_path)
    if not executable:
        raise MediaValidationError("local FFmpeg is unavailable")
    try:
        result = subprocess.run(
            [executable, "-nostdin", "-v", "error", "-threads", "1", "-protocol_whitelist", "file,pipe", "-ss", f"{timestamp_ms / 1000:.3f}", "-i", str(source),
             "-frames:v", "1", "-vf", "scale=640:640:force_original_aspect_ratio=decrease", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
            capture_output=True, timeout=60, check=False, env={"PATH": os.getenv("PATH", ""), "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MediaValidationError("frame extraction failed") from None
    if result.returncode or not result.stdout:
        raise MediaValidationError("frame extraction failed")
    try:
        with Image.open(BytesIO(result.stdout)) as frame_img:
            output = BytesIO()
            frame_img.convert("RGB").save(output, format="WEBP", quality=78)
            webp_bytes = output.getvalue()
    except Exception:
        raise MediaValidationError("frame extraction failed") from None
    return _atomic_write(settings.storage_root, key, webp_bytes)


def extract_audio(source: Path, duration_ms: int) -> bytes:
    if duration_ms > settings.max_transcription_seconds * 1000:
        raise MediaValidationError("video exceeds the transcription duration limit")
    executable = shutil.which(settings.ffmpeg_path)
    if not executable:
        raise MediaValidationError("local FFmpeg is unavailable")
    try:
        result = subprocess.run(
            [executable, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", "-f", "mp3", "pipe:1"],
            capture_output=True, timeout=600, check=False, env={"PATH": os.getenv("PATH", ""), "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MediaValidationError("audio extraction failed") from None
    if result.returncode or not result.stdout:
        raise MediaValidationError("audio extraction failed")
    return result.stdout
