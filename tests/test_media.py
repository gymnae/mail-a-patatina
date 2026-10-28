"""The pictures the website and the emails show, made from Immich's renditions."""

import asyncio
import io

from PIL import Image

from mail_a_patatina.media import MediaCache


def photo(width: int, height: int, **exif) -> bytes:
    img = Image.new("RGB", (width, height), "orange")
    tags = Image.Exif()
    for tag, value in exif.items():
        tags[{"orientation": 0x0112, "make": 0x010F}[tag]] = value
    out = io.BytesIO()
    img.save(out, "JPEG", exif=tags)
    return out.getvalue()


class Immich:
    def __init__(self, **renditions):
        self.renditions = renditions
        self.asked = []

    async def thumbnail(self, asset_id, size):
        self.asked.append(size)
        if self.renditions.get(size) is None:
            raise RuntimeError(f"no {size}")
        return self.renditions[size]


def test_the_large_picture_is_made_from_the_original_upright_and_without_its_notes(tmp_path):
    # A phone's original: on its side, with a note to turn it, and the camera it came from.
    immich = Immich(fullsize=photo(4000, 3000, orientation=6, make="Phone"))
    data = asyncio.run(MediaCache(tmp_path).jpeg(immich, "a1", "large"))
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (1920, 2560)  # turned upright, and no bigger than zooming needs
        assert not img.getexif()
    assert immich.asked == ["fullsize"]


def test_without_a_full_size_picture_the_large_one_is_the_preview(tmp_path):
    immich = Immich(fullsize=b"not a picture we can read", preview=photo(1440, 1080))
    data = asyncio.run(MediaCache(tmp_path).jpeg(immich, "a1", "large"))
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (1440, 1080)
    assert immich.asked == ["fullsize", "preview"]
    # Made once, then kept.
    asyncio.run(MediaCache(tmp_path).jpeg(immich, "a1", "large"))
    assert immich.asked == ["fullsize", "preview"]
