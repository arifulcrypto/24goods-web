"""Locally generated product cover images (gradient + initials, no downloads)."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def cover_png(initials: str, start: tuple[int, int, int], end: tuple[int, int, int]) -> bytes:
    w = h = 512
    mask = Image.linear_gradient("L").resize((w, h)).rotate(45, expand=False, fillcolor=128)
    img = Image.composite(Image.new("RGB", (w, h), end), Image.new("RGB", (w, h), start), mask)
    draw = ImageDraw.Draw(img)
    draw.ellipse((96, 96, 416, 416), outline=(255, 255, 255), width=6)
    font = _font(150)
    text = initials[:2].upper()
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(((w - tw) / 2 - box[0], (h - th) / 2 - box[1]), text, fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
