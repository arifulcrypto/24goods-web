"""Hostile-upload handling: magic-byte sniffing, decompression-bomb limits, re-encoding."""

from __future__ import annotations

import io
import logging
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, features

from app.core.errors import UploadRejected
from app.core.memtrim import trim

log = logging.getLogger("app.uploads")

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VOICE_BYTES = 1 * 1024 * 1024
MAX_VOICE_MS = 60_000
MAX_PIXELS = 40_000_000  # decompression-bomb limit (any format)
# Formats without reduced-resolution decoding are fully materialized in memory
# (4 bytes/px for RGBA): cap them at a 4K screenshot to bound peak RSS.
MAX_FULL_DECODE_PIXELS = 8_500_000
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


MP4_AUDIO_BRANDS = frozenset(
    {
        b"M4A ",
        b"M4B ",
        b"mp41",
        b"mp42",
        b"isom",
        b"iso2",
        b"iso4",
        b"iso5",
        b"iso6",
        b"dash",
        b"3gp4",
        b"3gp5",
        b"3gp6",
        b"3g2a",
        b"f4a ",
    }
)
OGG_CODECS = (b"OpusHead", b"\x01vorbis", b"Speex   ", b"\x7fFLAC")


def _is_webm(head: bytes) -> bool:
    # EBML magic + a DocType element declaring "webm" inside the (small) EBML header.
    return head[:4] == b"\x1a\x45\xdf\xa3" and b"\x42\x82" in head[4:40] and b"webm" in head[4:48]


def _is_ogg(head: bytes) -> bool:
    # "OggS", stream structure version 0, BOS page, first packet is a known codec header.
    if len(head) < 28 or head[:4] != b"OggS" or head[4] != 0 or not head[5] & 0x02:
        return False
    start = 27 + head[26]
    return any(head[start : start + len(c)] == c for c in OGG_CODECS)


def _is_mp4_audio(head: bytes) -> bool:
    if len(head) < 16 or head[4:8] != b"ftyp":
        return False
    size = int.from_bytes(head[:4], "big")
    if not 16 <= size <= 256:
        return False
    brands = [head[8:12]] + [head[i : i + 4] for i in range(16, min(size, len(head)) - 3, 4)]
    return any(b in MP4_AUDIO_BRANDS for b in brands)


def _is_mpeg_audio(head: bytes) -> bool:
    if head[:3] == b"ID3":
        return len(head) >= 10 and head[3] in (2, 3, 4) and head[4] != 0xFF
    if len(head) < 4 or head[0] != 0xFF or (head[1] & 0xE0) != 0xE0:
        return False
    version = (head[1] >> 3) & 0x03  # 01 = reserved
    layer = (head[1] >> 1) & 0x03  # 00 = reserved
    bitrate = head[2] >> 4  # 1111 = bad
    rate = (head[2] >> 2) & 0x03  # 11 = reserved
    return version != 1 and layer != 0 and bitrate != 0x0F and rate != 0x03


def sniff_voice(head: bytes) -> str | None:
    if _is_webm(head):
        return "audio/webm"
    if _is_ogg(head):
        return "audio/ogg"
    if _is_mp4_audio(head):
        return "audio/mp4"
    if _is_mpeg_audio(head):
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
            pixels = img.width * img.height
            if pixels > MAX_PIXELS:
                raise UploadRejected("Image dimensions too large")
            if fmt == "jpeg":
                # DCT-domain downscale (1/2..1/8) while the long edge stays >= MAX_DIM / 2.
                img.draft("RGB", (MAX_DIM // 2, MAX_DIM // 2))
            elif pixels > MAX_FULL_DECODE_PIXELS:
                raise UploadRejected("Image dimensions too large")
            img.seek(0)  # first frame only (animated GIF/WEBP)
            img.load()
            ImageOps.exif_transpose(img, in_place=True)
            img.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS, reducing_gap=2.0)
            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )
            out = io.BytesIO()
            # No exif/icc/xmp passed to save(): metadata is stripped by construction.
            # Opaque -> JPEG (about half the encoder memory of WEBP); alpha -> WEBP.
            if has_alpha and _webp_ok():
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                img.save(out, "WEBP", quality=82, method=4)
                mime = "image/webp"
            else:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(out, "JPEG", quality=85, optimize=True)
                mime = "image/jpeg"
            width, height = img.size
    except UploadRejected:
        raise
    except Exception as exc:  # any decoder failure means a hostile or broken file
        log.info("image rejected error=%s", type(exc).__name__)
        raise UploadRejected("Image could not be decoded") from None
    finally:
        trim()
    return Processed(out.getvalue(), mime, width, height)


def process_voice(data: bytes, duration_ms: int | None) -> Processed:
    if not data:
        raise UploadRejected("Empty file")
    if len(data) > MAX_VOICE_BYTES:
        raise UploadRejected("Voice message larger than 1 MB")
    if duration_ms is None or not (1 <= duration_ms <= MAX_VOICE_MS):
        raise UploadRejected("duration_ms must be between 1 and 60000")
    mime = sniff_voice(data[:256])
    if mime is None:
        raise UploadRejected("Unsupported audio type")
    return Processed(data, mime)
