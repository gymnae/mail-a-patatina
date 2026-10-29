"""Fetches Immich's pictures of a photo: for the website as Immich made them, for emails as email-safe JPEGs.
Both are cached on disk, except the big picture for zooming in."""

from __future__ import annotations

import asyncio
import io
import logging
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps

from .immich import Immich

log = logging.getLogger(__name__)

# For emails: our size name -> (Immich rendition, longest edge in px)
SIZES = {"thumb": ("thumbnail", 480), "preview": ("preview", 1600)}
# For the website: our size name -> Immich rendition. "full" is what Immich's own viewer loads when zooming in:
# the original of a photo browsers can show, else the full-size picture Immich made of it (of a HEIC or RAW, if
# Immich is set to make those), else the preview.
WEB = {"thumb": "thumbnail", "preview": "preview"}
SHOWN = {"JPEG", "PNG", "WEBP", "GIF"}  # what browsers show
ORIENTATION = 0x0112
APP1, APP2, APP14, COM, SOS, EOI = 0xE1, 0xE2, 0xEE, 0xFE, 0xDA, 0xD9


def _to_jpeg(raw: bytes, max_edge: int, quality: int = 85) -> bytes:
    with Image.open(io.BytesIO(raw)) as img:
        img.draft("RGB", (max_edge, max_edge))  # a big JPEG decodes at a fraction of its size, much faster
        icc = img.info.get("icc_profile")
        # A picture may still be lying on its side, with a note saying so; what's saved carries none of its
        # notes (no places or cameras), only the colour profile.
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_edge, max_edge))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True, progressive=True, icc_profile=icc)
        return out.getvalue()


def _without_notes(jpeg: bytes, orientation: int) -> bytes:
    """A JPEG's picture byte for byte, without its notes (where and with what it was taken, any pictures
    appended to it); only its colour profile and which way up it goes are kept."""
    out = bytearray(b"\xff\xd8")
    if orientation != 1:
        exif = Image.Exif()
        exif[ORIENTATION] = orientation
        body = exif.tobytes()
        out += b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
    i = 2
    while jpeg[i] == 0xFF and jpeg[i + 1] != SOS:
        marker = jpeg[i + 1]
        end = i + 2 + int.from_bytes(jpeg[i + 2:i + 4], "big")
        note = marker == COM or (APP1 <= marker <= 0xEF and marker != APP14)
        if not note or (marker == APP2 and jpeg[i + 4:i + 16] == b"ICC_PROFILE\x00"):
            out += jpeg[i:end]
        i = end
    # The picture itself runs to its end marker; past that, phones append more pictures (depth, HDR), with notes.
    start = j = i
    try:
        while True:
            j = jpeg.index(b"\xff", j)
            marker = jpeg[j + 1]
            if marker == EOI:
                j += 2
                break
            if marker in (0, 0xFF) or 0xD0 <= marker <= 0xD7:  # inside the picture's data
                j += 1
            else:  # the header of the next scan of a progressive JPEG, or its tables
                j += 2 + int.from_bytes(jpeg[j + 2:j + 4], "big")
    except (ValueError, IndexError):
        j = len(jpeg)  # cut short
    return bytes(out + jpeg[start:j])


def _for_browsers(raw: bytes) -> tuple[bytes, str]:
    """The picture as Immich gave it, unless it carries notes. -> the picture and its media type"""
    with Image.open(io.BytesIO(raw)) as img:
        kind = img.format
        notes = img.getexif()
        orientation = notes.get(ORIENTATION, 1)
        noted = bool(notes) or any(key in img.info for key in ("xmp", "XML:com.adobe.xmp"))
    if kind == "JPEG":
        return _without_notes(raw, orientation), "image/jpeg"
    if kind in SHOWN and not noted:
        return raw, Image.MIME[kind]
    return _to_jpeg(raw, 8192, quality=92), "image/jpeg"


def _media_type(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as img:
        return Image.MIME[img.format]


class MediaCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        for old in self.root.glob("*-large*.jpg"):  # big pictures for zooming in, no longer kept
            old.unlink(missing_ok=True)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def jpeg(self, immich: Immich, asset_id: str, size: str) -> bytes:
        """For emails. `immich` must be allowed to see the asset (its feed's curator); callers check feed
        membership."""
        immich_size, max_edge = SIZES[size]
        path = self.root / f"{asset_id}-{size}.jpg"
        async with self._locks[path.name]:
            if path.exists():
                return path.read_bytes()
            raw = await immich.thumbnail(asset_id, immich_size)
            data = await asyncio.to_thread(_to_jpeg, raw, max_edge)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data

    async def web(self, immich: Immich, asset_id: str, size: str) -> tuple[bytes, str]:
        """For the website: -> the picture and its media type. Same rules for `immich` as for jpeg()."""
        if size == "full":  # many megabytes, and only wanted by whoever zooms in: the browser keeps it
            try:
                raw, source = await immich.full_size(asset_id)
            except Exception as e:
                # An older Immich, or a curator's API key that may not download originals.
                log.warning("no full-size picture of %s (%s), using the preview", asset_id, e)
                return await self.web(immich, asset_id, "preview")
            data, kind = await asyncio.to_thread(_for_browsers, raw)
            with Image.open(io.BytesIO(data)) as img:
                log.info("zooming in on %s: Immich's %s, %d×%d", asset_id, source, *img.size)
            return data, kind
        path = self.root / f"{asset_id}-{size}.web"
        async with self._locks[path.name]:
            if path.exists():
                data = path.read_bytes()
                return data, _media_type(data)
            data, kind = await asyncio.to_thread(_for_browsers, await immich.thumbnail(asset_id, WEB[size]))
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data, kind

    def purge(self, asset_id: str) -> None:
        for path in self.root.glob(f"{asset_id}-*"):
            path.unlink(missing_ok=True)
