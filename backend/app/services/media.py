import hashlib
from io import BytesIO
import json
import os
import shutil
import subprocess
import tempfile
import warnings
import wave
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image, ImageFile
from PIL import Image as PILImage

from app.config import settings
from app.services.document_readers import DOCUMENT_MIMES, SUPPORTED_DOCUMENT_EXTENSIONS

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = False
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
IMAGE_MIMES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "TIFF": "image/tiff",
    "BMP": "image/bmp",
    "GIF": "image/gif",
}
VIDEO_MIMES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}
AUDIO_MIMES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
}


class MediaValidationError(Exception):
    pass


def resolve_under_root(root: Path, relative_key: str) -> Path:
    normalized_key = relative_key.replace("\\", "/")
    relative = PurePosixPath(normalized_key)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} or ":" in part for part in relative.parts):
        raise MediaValidationError("invalid path")
    root = root.resolve(strict=True)
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise MediaValidationError("symlink skipped")
        try:
            st = os.lstat(candidate)
            if getattr(st, "st_file_attributes", 0) & 0x0400:
                raise MediaValidationError("symlink skipped")
        except MediaValidationError:
            raise
        except OSError:
            pass
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
            mime = VIDEO_MIMES.get(suffix, "video/mp4")
            return {"media_type": "video", "mime": mime, "size": size, "width": width, "height": height, "duration_ms": max(1, int(duration * 1000))}
        except MediaValidationError:
            raise
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            raise MediaValidationError("video inspection failed") from None

    if suffix in AUDIO_EXTENSIONS:
        return _inspect_audio(path, suffix, size)

    if suffix in SUPPORTED_DOCUMENT_EXTENSIONS:
        return _inspect_document(path, suffix, size)

    raise MediaValidationError("unsupported file type")


def _inspect_audio(path: Path, suffix: str, size: int) -> dict:
    mime = AUDIO_MIMES.get(suffix, "audio/mpeg")
    if suffix == ".wav":
        try:
            with wave.open(str(path), "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                if rate <= 0 or frames <= 0:
                    raise MediaValidationError("invalid audio")
                duration_ms = max(1, int((frames / rate) * 1000))
                return {
                    "media_type": "audio",
                    "mime": mime,
                    "size": size,
                    "width": None,
                    "height": None,
                    "duration_ms": duration_ms,
                    "audio_channels": channels,
                    "sample_rate": rate,
                }
        except MediaValidationError:
            raise
        except Exception:
            pass

    executable = shutil.which(settings.ffprobe_path)
    if executable:
        try:
            result = subprocess.run(
                [executable, "-protocol_whitelist", "file,pipe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,sample_rate,channels,codec_name,duration", "-of", "json", str(path)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False, env={"PATH": os.getenv("PATH", ""), "LC_ALL": "C"},
            )
            if result.returncode == 0 and result.stdout:
                info = json.loads(result.stdout)
                audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
                raw_duration = info.get("format", {}).get("duration")
                if raw_duration in (None, "N/A") and audio:
                    raw_duration = audio.get("duration")
                duration = float(raw_duration or 0)
                if audio and duration > 0:
                    return {
                        "media_type": "audio",
                        "mime": mime,
                        "size": size,
                        "width": None,
                        "height": None,
                        "duration_ms": max(1, int(duration * 1000)),
                        "audio_channels": int(audio.get("channels") or 1),
                        "sample_rate": int(audio.get("sample_rate") or 16000),
                        "codec": audio.get("codec_name"),
                    }
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            pass

    # Fallback header check for non-WAV audio when ffprobe is unavailable
    with path.open("rb") as f:
        header = f.read(16)
    if suffix == ".mp3" and (header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0)):
        est_duration_ms = max(1000, int((size / 16000) * 1000))
        return {"media_type": "audio", "mime": mime, "size": size, "width": None, "height": None, "duration_ms": est_duration_ms}
    if suffix == ".flac" and header.startswith(b"fLaC"):
        est_duration_ms = max(1000, int((size / 64000) * 1000))
        return {"media_type": "audio", "mime": mime, "size": size, "width": None, "height": None, "duration_ms": est_duration_ms}
    if suffix == ".ogg" and header.startswith(b"OggS"):
        est_duration_ms = max(1000, int((size / 16000) * 1000))
        return {"media_type": "audio", "mime": mime, "size": size, "width": None, "height": None, "duration_ms": est_duration_ms}
    if suffix == ".m4a" and b"ftyp" in header:
        est_duration_ms = max(1000, int((size / 16000) * 1000))
        return {"media_type": "audio", "mime": mime, "size": size, "width": None, "height": None, "duration_ms": est_duration_ms}
    raise MediaValidationError("invalid audio")


def _inspect_document(path: Path, suffix: str, size: int) -> dict:
    mime = DOCUMENT_MIMES.get(suffix, "application/octet-stream")
    if suffix == ".pdf":
        with path.open("rb") as f:
            head = f.read(1024)
        if b"%PDF-" not in head:
            raise MediaValidationError("invalid PDF document")
    elif suffix in {".docx", ".odt", ".xlsx", ".ods", ".pptx", ".odp"}:
        if not zipfile.is_zipfile(path):
            raise MediaValidationError("invalid office document package")
    return {
        "media_type": "document",
        "mime": mime,
        "size": size,
        "width": None,
        "height": None,
        "duration_ms": None,
    }


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
    if source.suffix.lower() in {".mp3", ".wav"} and source.stat().st_size <= 64 * 1024 * 1024:
        executable = shutil.which(settings.ffmpeg_path)
        if not executable:
            return source.read_bytes()
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
