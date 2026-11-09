"""Avatar helper: a hostile upload re-encoded to a small square (no metadata, random key)."""

from __future__ import annotations

import io
from pathlib import Path

import anyio.to_thread
from fastapi import UploadFile
from PIL import Image, ImageOps

from app.core.errors import Unavailable
from app.modules.uploads import service as uploads
from app.modules.uploads.processing import Processed, process_image

MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_PX = 256


def _square(p: Processed) -> Processed:
    # ``p`` is already our own sanitised re-encode (magic bytes, bomb limits, no EXIF).
    with Image.open(io.BytesIO(p.data)) as img:
        img.load()
        fitted = ImageOps.fit(img, (AVATAR_PX, AVATAR_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        if p.mime == "image/webp":
            fitted.save(out, "WEBP", quality=82, method=4)
        else:
            fitted.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
    return Processed(out.getvalue(), p.mime, AVATAR_PX, AVATAR_PX)


async def avatar_from_upload(file: UploadFile) -> Processed:
    """<= 2 MB input, images only; output is a <= 256 px square."""
    # Same one-decode-at-a-time rule as chat images (bounded peak RSS).
    if uploads._image_slot.locked():
        raise Unavailable("Image processing is busy", headers={"Retry-After": "2"})
    async with uploads._image_slot:
        data = await uploads.read_limited(file, MAX_AVATAR_BYTES)
        processed = await anyio.to_thread.run_sync(process_image, data)
        return await anyio.to_thread.run_sync(_square, processed)


def avatar_mime(path: Path) -> str:
    """Our re-encode is JPEG or WEBP only; decide by magic bytes, never by extension."""
    with path.open("rb") as fh:
        head = fh.read(12)
    return "image/webp" if head[:4] == b"RIFF" and head[8:12] == b"WEBP" else "image/jpeg"
