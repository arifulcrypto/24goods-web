"""Hostile-upload handling: magic-byte sniffing, decompression-bomb limits, re-encoding."""

from __future__ import annotations

import io
import logging
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, features

from app.core.errors import UploadRejected

log = logging.getLogger("app.uploads")

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VOICE_BYTES = 1 * 1024 * 1024
MAX_VOICE_MS = 60_000
MAX_PIXELS = 40_000_000
MAX_DIM = 2048

Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow raises DecompressionBombError at 2x this

_PIL_FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF"}


@dataclass(frozen=True)
class Processed:
    data: bytes
    mime: str
    width: int | None = None
    height: int | None = None


def sniff_image(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def sniff_voice(head: bytes) -> str | None:
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[4:8] == b"ftyp":
        return "audio/mp4"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    return None


def _webp_ok() -> bool:
    return bool(features.check("webp"))


def process_image(data: bytes) -> Processed:
    if not data:
        raise UploadRejected("Empty file")
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadRejected("Image larger than 8 MB")
    fmt = sniff_image(data[:16])
    if fmt is None:
        raise UploadRejected("Unsupported image type")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=[_PIL_FORMATS[fmt]])
            if img.width * img.height > MAX_PIXELS:
                raise UploadRejected("Image dimensions too large")
            if fmt == "jpeg":
                img.draft("RGB", (MAX_DIM, MAX_DIM))  # DCT-domain downscale: cheap on memory
            img.seek(0)  # first frame only (animated GIF/WEBP)
            img.load()
            img = ImageOps.exif_transpose(img) or img
            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )
            webp = _webp_ok()
            img = img.convert("RGBA" if has_alpha and webp else "RGB")
            img.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            # No exif/icc/xmp passed: metadata is stripped by construction.
            if webp:
                img.save(out, "WEBP", quality=82, method=4)
                mime = "image/webp"
            else:  # pragma: no cover - Pillow wheels ship libwebp
                img.save(out, "JPEG", quality=85, optimize=True)
                mime = "image/jpeg"
            width, height = img.size
    except UploadRejected:
        raise
    except Exception as exc:  # any decoder failure means a hostile or broken file
        log.info("image rejected error=%s", type(exc).__name__)
        raise UploadRejected("Image could not be decoded") from None
    return Processed(out.getvalue(), mime, width, height)


def process_voice(data: bytes, duration_ms: int | None) -> Processed:
    if not data:
        raise UploadRejected("Empty file")
    if len(data) > MAX_VOICE_BYTES:
        raise UploadRejected("Voice message larger than 1 MB")
    if duration_ms is None or not (1 <= duration_ms <= MAX_VOICE_MS):
        raise UploadRejected("duration_ms must be between 1 and 60000")
    mime = sniff_voice(data[:16])
    if mime is None:
        raise UploadRejected("Unsupported audio type")
    return Processed(data, mime)
