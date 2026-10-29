"""The pictures the website and the emails show, made from Immich's renditions."""

import asyncio
import io

from PIL import Image, ImageCms

from mail_a_patatina.media import MediaCache

GPS = 0x8825


def photo(width: int, height: int, kind: str = "JPEG", orientation: int = 1, notes: bool = False, **options) -> bytes:
    img = Image.new("RGB", (width, height), "orange")
    img.paste("purple", (0, 0, width // 3, height // 4))  # something to tell which way up it is
    exif = Image.Exif()
    if orientation != 1:
        exif[0x0112] = orientation
    if notes:
        exif[0x010F] = "Phone"
        exif.get_ifd(GPS)[2] = (52.0, 31.0, 12.0)  # where it was taken
        if kind == "JPEG":
            options["comment"] = "taken at home"
    if exif:
        options["exif"] = exif
    out = io.BytesIO()
    img.save(out, kind, **options)
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


def web(tmp_path, immich, size, asset_id="a1"):
    return asyncio.run(MediaCache(tmp_path).web(immich, asset_id, size))


def test_the_website_shows_immichs_own_preview_as_it_is(tmp_path):
    preview = photo(1440, 1080, "WEBP", quality=80)
    immich = Immich(preview=preview)
    assert web(tmp_path, immich, "preview") == (preview, "image/webp")
    assert web(tmp_path, immich, "preview") == (preview, "image/webp")  # kept
    assert immich.asked == ["preview"]


def test_emails_get_a_jpeg_of_immichs_preview(tmp_path):
    immich = Immich(preview=photo(2000, 1500, "WEBP"))
    data = asyncio.run(MediaCache(tmp_path).jpeg(immich, "a1", "preview"))
    with Image.open(io.BytesIO(data)) as img:
        assert (img.format, img.size) == ("JPEG", (1600, 1200))


def test_zooming_in_shows_the_original_unchanged_but_without_its_notes(tmp_path):
    # An original lying on its side, with where it was taken, a comment, a colour profile, and a phone's extra
    # picture appended (depth, HDR) that has notes of its own.
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    extra = photo(100, 100, notes=True)
    original = photo(400, 300, orientation=6, notes=True, icc_profile=srgb, progressive=True) + extra
    data, kind = web(tmp_path, Immich(fullsize=original), "large")
    assert kind == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img, Image.open(io.BytesIO(original)) as before:
        assert img.tobytes() == before.tobytes()  # not one pixel changed
        assert img.info["icc_profile"] == srgb
        assert dict(img.getexif()) == {0x0112: 6}  # still known to be on its side, nothing else
        assert "comment" not in img.info
    assert b"Phone" not in data and b"taken at home" not in data and not data.endswith(extra)
    assert list(tmp_path.iterdir()) == []  # not kept: the browser does


def test_zooming_in_without_a_full_size_picture_shows_the_preview(tmp_path):
    preview = photo(1440, 1080, "WEBP")
    immich = Immich(fullsize=None, preview=preview)  # an older Immich, or a key that may not download
    assert web(tmp_path, immich, "large") == (preview, "image/webp")
    assert immich.asked == ["fullsize", "preview"]


def test_other_pictures_with_notes_are_made_anew_without_them(tmp_path):
    data, kind = web(tmp_path, Immich(fullsize=photo(400, 300, "WEBP", orientation=6, notes=True)), "large")
    with Image.open(io.BytesIO(data)) as img:
        assert (kind, img.size) == ("image/jpeg", (300, 400))  # turned upright
        assert not img.getexif()


def test_big_pictures_made_by_earlier_versions_are_cleared_away(tmp_path):
    for name in ("a1-large.jpg", "a1-large-2.jpg", "a1-preview.jpg"):
        (tmp_path / name).write_bytes(b"a picture")
    MediaCache(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a1-preview.jpg"]
