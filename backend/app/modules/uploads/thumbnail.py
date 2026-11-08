"""One bounded worker for private image thumbnails; generated only from stored safe images."""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

import anyio.to_thread
from PIL import Image

from app.core.errors import Unavailable
from app.modules.uploads.storage import FileStore

_slot = asyncio.Semaphore(1)


async def small_image(store: FileStore, key: str) -> Path:
    thumb = hashlib.sha256(("thumbnail:v1:" + key).encode()).hexdigest()[:32]
    target = store.path_for(thumb)
    if target.is_file():
        return target
    try:
        await asyncio.wait_for(_slot.acquire(), timeout=0.1)
    except asyncio.TimeoutError:
        raise Unavailable("Image preview is busy; please retry") from None
    try:

        def make() -> None:
            if target.is_file():
                return
            with Image.open(store.path_for(key)) as original:
                image = original.convert("RGB")
                image.thumbnail((480, 480))
                buf = io.BytesIO()
                image.save(buf, format="WEBP", quality=65, method=2)
                try:
                    store._write_sync(thumb, buf.getvalue())
                except FileExistsError:
                    pass

        await anyio.to_thread.run_sync(make)
        return target
    finally:
        _slot.release()


async def delete_thumbnail(store: FileStore, key: str) -> None:
    await store.delete(hashlib.sha256(("thumbnail:v1:" + key).encode()).hexdigest()[:32])
