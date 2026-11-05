from __future__ import annotations

import io
import os
import struct
import zlib

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.test_chat import png_bytes


def jpeg_with_exif() -> bytes:
    img = Image.new("RGB", (300, 200), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SpyCam"  # Make
    exif[0x8825] = {2: (1.0, 2.0, 3.0)}  # GPS IFD
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def png_bomb(width=20000, height=20000) -> bytes:
    """Tiny file declaring a 400 MP image (decompression bomb)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 1024)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# Minimal but structurally valid container headers.
OGG_OPUS = b"OggS\x00\x02" + b"\x00" * 20 + b"\x01\x13" + b"OpusHead\x01\x01"
WEBM = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\x82\x84webm"


async def _up(client, data: bytes, kind: str = "image", name: str = "f.png", **form):
    return await client.post(
        "/api/v1/uploads",
        data={"kind": kind, **form},
        files={"file": (name, data, "application/octet-stream")},
    )


async def test_image_reencoded_exif_stripped_random_name(env, services):
    buyer, _ = await env.login("buyer")
    r = await _up(buyer, jpeg_with_exif(), name="../../etc/passwd.jpg")
    assert r.status_code == 200, r.text
    att = r.json()
    assert att["mime"] == "image/jpeg" and (att["width"], att["height"]) == (300, 200)
    key = await env.scalar("SELECT storage_key FROM attachments WHERE id=:i", i=att["id"])
    stored = services.upload_dir / key[:2] / key
    assert stored.is_file() and "passwd" not in str(stored)
    data = stored.read_bytes()
    assert data[:3] == b"\xff\xd8\xff" and b"SpyCam" not in data and b"Exif" not in data
    assert not Image.open(io.BytesIO(data)).getexif()
    f = await buyer.get(att["url"])
    assert f.status_code == 200 and f.content == data
    assert f.headers["x-content-type-options"] == "nosniff"
    assert f.headers["content-security-policy"] == "sandbox"
    assert f.headers["content-disposition"] == "inline"
    assert "private" in f.headers["cache-control"]


async def test_large_image_is_downscaled(env):
    buyer, _ = await env.login("buyer")
    r = await _up(buyer, png_bytes(size=(3000, 1000)))
    assert r.status_code == 200
    assert (r.json()["width"], r.json()["height"]) == (2048, 683)


async def test_rejections(env):
    buyer, _ = await env.login("buyer")
    cases = [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "image"),
        (b"<html><script>alert(1)</script></html>", "image"),
        (b"\x89PNG\r\n\x1a\n" + os.urandom(200), "image"),  # right magic, garbage body
        (png_bomb(), "image"),
        (b"MZ\x90\x00" + os.urandom(100), "voice"),
        (png_bytes(), "video"),
    ]
    for data, kind in cases:
        r = await _up(buyer, data, kind=kind, duration_ms="1000")
        assert r.status_code == 422, (kind, data[:8], r.text)
        assert r.json()["error"]["code"] == "upload_rejected"
    assert await env.scalar("SELECT count(*) FROM attachments") == 0


async def test_size_limits(env):
    buyer, _ = await env.login("buyer")
    big = b"\xff\xd8\xff" + b"\x00" * (8 * 1024 * 1024 + 10)
    r = await _up(buyer, big)
    assert r.status_code in (413, 422) and r.json()["error"]["code"] == "upload_rejected"
    voice = b"OggS" + b"\x00" * (1024 * 1024 + 10)
    r = await _up(buyer, voice, kind="voice", duration_ms="1000")
    assert r.status_code == 422 and r.json()["error"]["code"] == "upload_rejected"


async def test_voice_upload(env):
    buyer, _ = await env.login("buyer")
    ogg = OGG_OPUS + os.urandom(2000)
    r = await _up(buyer, ogg, kind="voice", name="v.ogg", duration_ms="4200")
    assert r.status_code == 200, r.text
    assert r.json()["mime"] == "audio/ogg" and r.json()["duration_ms"] == 4200
    webm = WEBM + os.urandom(100)
    r = await _up(buyer, webm, kind="voice", duration_ms="60000")
    assert r.json()["mime"] == "audio/webm"
    mp3 = b"\xff\xfb\x90\x64" + os.urandom(300)
    assert (await _up(buyer, mp3, kind="voice", duration_ms="900")).json()["mime"] == "audio/mpeg"
    for dur in ("60001", "0"):
        r = await _up(buyer, ogg, kind="voice", duration_ms=dur)
        assert r.status_code == 422
    r = await _up(buyer, ogg, kind="voice")
    assert r.status_code == 422


async def test_upload_requires_auth(env):
    r = await _up(env.client(), png_bytes())
    assert r.status_code == 401


async def test_decode_budget_and_jpeg_downscale(env):
    buyer, _ = await env.login("buyer")
    r = await _up(buyer, png_bytes(size=(3000, 3000)))  # 9 MP PNG: no reduced decode
    assert r.status_code == 422 and r.json()["error"]["code"] == "upload_rejected"
    buf = io.BytesIO()
    Image.new("RGB", (4032, 3024), (9, 9, 9)).save(buf, "JPEG", quality=80)
    r = await _up(buyer, buf.getvalue(), name="photo.jpg")
    assert r.status_code == 200, r.text
    assert (r.json()["width"], r.json()["height"]) == (2016, 1512)


async def test_alpha_image_becomes_webp_without_metadata(env):
    buyer, _ = await env.login("buyer")
    img = Image.new("RGBA", (40, 40), (255, 0, 0, 128))
    info = PngInfo()
    info.add_text("Comment", "secret-location")
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=info)
    r = await _up(buyer, buf.getvalue())
    assert r.status_code == 200 and r.json()["mime"] == "image/webp"
    data = (await buyer.get(r.json()["url"])).content
    assert data[:4] == b"RIFF" and b"secret-location" not in data
