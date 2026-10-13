"""Fetches Immich renditions once, converts them to email-safe JPEGs and caches them on disk."""

from __future__ import annotations

import asyncio
import io
from collections import defaultdict
from pathlib import Path

from PIL import Image

from .immich import Immich

# our size name -> (Immich rendition, longest edge in px)
SIZES = {"thumb": ("thumbnail", 480), "preview": ("preview", 1600)}


def _to_jpeg(raw: bytes, max_edge: int) -> bytes:
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, optimize=True, progressive=True)
        return out.getvalue()


class MediaCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def jpeg(self, immich: Immich, asset_id: str, size: str) -> bytes:
        """`immich` must be allowed to see the asset (its feed's curator); callers check feed membership."""
        immich_size, max_edge = SIZES[size]
        path = self.root / f"{asset_id}-{size}.jpg"
        async with self._locks[path.name]:
            if path.exists():
                return path.read_bytes()
            raw = await immich.thumbnail(asset_id, immich_size)
            data = await asyncio.to_thread(_to_jpeg, raw, max_edge)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data

    def purge(self, asset_id: str) -> None:
        for path in self.root.glob(f"{asset_id}-*.jpg"):
            path.unlink(missing_ok=True)
