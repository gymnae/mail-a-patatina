"""The pictures the website and the emails show, made from Immich's renditions."""

import asyncio
import io

from PIL import Image

from mail_a_patatina.media import MediaCache


def photo(width: int, height: int, kind: str = "JPEG", **exif) -> bytes:
    img = Image.new("RGB", (width, height), "orange")
    tags = Image.Exif()
    for tag, value in exif.items():
        tags[{"orientation": 0x0112, "make": 0x010F}[tag]] = value
    out = io.BytesIO()
    img.save(out, kind, exif=tags)
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

    async def original(self, asset_id):
        return await self.thumbnail(asset_id, "original")


def test_the_large_picture_is_made_from_the_original_upright_and_without_its_notes(tmp_path):
    # A phone's original: on its side, with a note to turn it, and the camera it came from.
    immich = Immich(original=photo(4000, 3000, orientation=6, make="Phone"))
    data = asyncio.run(MediaCache(tmp_path).jpeg(immich, "a1", "large"))
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (1920, 2560)  # turned upright, and no bigger than zooming needs
        assert not img.getexif()
    assert immich.asked == ["original"]


def test_an_iphones_heic_original_makes_a_large_picture_too(tmp_path):
    # Immich's own full-size picture of a HEIC is only the preview, so it isn't even asked for.
    immich = Immich(original=photo(4032, 3024, "HEIF"), fullsize=photo(1440, 1080))
    data = asyncio.run(MediaCache(tmp_path).jpeg(immich, "a1", "large"))
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (2560, 1920)
    assert immich.asked == ["original"]


def test_without_an_original_we_can_read_the_large_picture_is_immichs_full_size_or_else_the_preview(tmp_path):
    immich = Immich(original=b"a RAW file", fullsize=photo(3000, 2000))
    data = asyncio.run(MediaCache(tmp_path).jpeg(immich, "a1", "large"))
    assert Image.open(io.BytesIO(data)).size == (2560, 1707)

    immich = Immich(original=b"a RAW file", fullsize=None, preview=photo(1440, 1080))  # an older Immich
    data = asyncio.run(MediaCache(tmp_path).jpeg(immich, "a2", "large"))
    assert Image.open(io.BytesIO(data)).size == (1440, 1080)
    assert immich.asked == ["original", "fullsize", "preview"]
    # Made once, then kept.
    asyncio.run(MediaCache(tmp_path).jpeg(immich, "a2", "large"))
    assert immich.asked == ["original", "fullsize", "preview"]


def test_large_pictures_made_from_the_preview_by_an_earlier_version_are_made_again(tmp_path):
    (tmp_path / "a1-large.jpg").write_bytes(b"the preview")
    (tmp_path / "a1-preview.jpg").write_bytes(b"the preview")
    MediaCache(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a1-preview.jpg"]
