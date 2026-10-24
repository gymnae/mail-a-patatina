"""Travel journals: trips written as Markdown files, their photos and videos in Immich.

    JOURNAL_DIR/<feed id>/<trip>/trip.md      the trip: title, its Immich album, an optional intro
    JOURNAL_DIR/<feed id>/<trip>/<entry>.md   one entry: date, title, place, text, photos as ![…](immich:<asset id>)

The text lives only in these files: the family writes them with the editor on the setup page (or any text
editor), and the website reads them. Each trip is one Immich album: the people it is shared with may read the
trip, and likes and comments on its photos are that album's Immich activity, kept apart from the family feed's.
Rebuilt from the files and Immich on every sync, like the feed's library. Entries with `draft: true` are only
shown in the editor."""

from __future__ import annotations

import datetime as dt
import html
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from markdown_it import MarkdownIt

from .immich import AlbumActivity

if TYPE_CHECKING:
    from .core import Feed
    from .people import Member

log = logging.getLogger(__name__)

FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
# A photo or video: ![what it shows](immich:<asset id>), optionally with a title ("IMG_4412.HEIC").
REF = re.compile(r'!\[(?P<alt>[^\]\n]*)\]\(immich:(?P<id>[0-9a-fA-F-]{36})(?:[ \t]+"(?P<title>[^"\n]*)")?\)')
ONLY_REFS = re.compile(rf"^\s*(?:{REF.pattern}\s*)+$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")  # trip folders and entry files, as they appear in URLs
DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Written by the family, read by relatives: Markdown, but no raw HTML.
_markdown = MarkdownIt("commonmark", {"html": False, "breaks": True})


@dataclass
class Media:
    asset_id: str
    caption: str  # the alt text written in the entry
    type: str = "IMAGE"  # IMAGE | VIDEO, from Immich
    taken_at: str = ""  # local wall-clock time, YYYY-MM-DDTHH:MM:SS, from Immich
    live_video_id: str = ""
    updated_at: str = ""


@dataclass
class Post:
    slug: str
    when: str  # YYYY-MM-DDTHH:MM:SS, local time where it was written
    title: str
    place: str
    # ("text", html) or ("media", [Media, …]) in the order they were written; a line of photos is one block
    blocks: list[tuple[str, object]]

    @property
    def taken_at(self) -> str:
        return self.when

    @property
    def day(self) -> str:
        return self.when[:10]

    @property
    def media(self) -> list[Media]:
        return [m for kind, block in self.blocks if kind == "media" for m in block]

    @property
    def excerpt(self) -> str:
        """The start of its text, for the trip's list of entries."""
        first = next((block for kind, block in self.blocks if kind == "text"), "")
        text = re.sub(r"<[^>]+>", "", re.sub(r"<br ?/?>|</(?:p|li|h\d|blockquote)>", " ", first))
        text = " ".join(html.unescape(text).split())
        return text if len(text) <= 200 else text[:199].rsplit(" ", 1)[0] + "…"


@dataclass
class Trip:
    slug: str
    title: str
    album_id: str
    intro: str  # html
    posts: list[Post]  # oldest first
    cover_id: str = ""  # asset id from trip.md, else the first photo
    members: dict[str, Member] = field(default_factory=dict)  # who the album is shared with, by user id
    activity: AlbumActivity | None = None

    def readable_by(self, member: Member | None) -> bool:
        return member is not None and member.id in self.members

    def post(self, slug: str) -> Post | None:
        return next((p for p in self.posts if p.slug == slug), None)

    def neighbours(self, post: Post) -> tuple[Post | None, Post | None]:
        """(earlier, later)"""
        i = self.posts.index(post)
        return (self.posts[i - 1] if i > 0 else None), (self.posts[i + 1] if i + 1 < len(self.posts) else None)

    @property
    def cover(self) -> tuple[Post, Media] | None:
        pairs = [(p, m) for p in self.posts for m in p.media]
        return next((pm for pm in pairs if pm[1].asset_id == self.cover_id), pairs[0] if pairs else None)

    @property
    def first_day(self) -> str:
        return self.posts[0].day if self.posts else ""

    @property
    def start(self) -> dt.date | None:
        return dt.date.fromisoformat(self.first_day) if self.first_day else None


def _frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m[1]) or {}
    return (meta if isinstance(meta, dict) else {}), text[m.end():]


def _when(value, fallback: str) -> str | None:
    """`date:` as YAML gives it (a date, a datetime, or text like 2026-08-04T18:30), else the date the file
    name starts with -> YYYY-MM-DDTHH:MM:SS."""
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return f"{value.isoformat()}T00:00:00"
    text = str(value or "").strip().replace(" ", "T") or (m[1] if (m := DATE_PREFIX.match(fallback)) else "")
    try:
        return dt.datetime.fromisoformat(text).replace(tzinfo=None).isoformat(timespec="seconds")
    except ValueError:
        return None


def blocks(body: str) -> list[tuple[str, object]]:
    """Text and photos in their order. Photos count where they stand on lines of their own."""
    out: list[tuple[str, object]] = []
    text: list[str] = []

    def flush():
        if "".join(text).strip():
            out.append(("text", _markdown.render("\n".join(text))))
        text.clear()

    for line in body.splitlines():
        if ONLY_REFS.match(line):
            flush()
            out.append(("media", [Media(m["id"].lower(), m["alt"].strip()) for m in REF.finditer(line)]))
        else:
            text.append(line)
    flush()
    return out


def read_post(path: Path) -> Post | None:
    meta, body = _frontmatter(path.read_text(encoding="utf-8"))
    if meta.get("draft"):
        return None
    when = _when(meta.get("date"), path.stem)
    if when is None:
        log.warning("Journal: %s has no date (date: in its front matter, or a name starting YYYY-MM-DD)", path)
        return None
    return Post(path.stem, when, str(meta.get("title") or ""), str(meta.get("place") or ""), blocks(body))


def read_trip(folder: Path) -> Trip | None:
    index = folder / "trip.md"
    meta, intro = _frontmatter(index.read_text(encoding="utf-8")) if index.is_file() else ({}, "")
    album_id = str(meta.get("album_id") or "").strip()
    if not album_id:
        log.warning("Journal: %s needs album_id: in its trip.md (the trip's Immich album)", folder)
        return None
    posts = []
    for path in sorted(folder.glob("*.md")):
        if path.name == "trip.md":
            continue
        if not NAME.match(path.stem):
            log.warning("Journal: skipping %s, its name can't be part of a link (letters, digits, - and _)", path)
            continue
        if post := read_post(path):
            posts.append(post)
    posts.sort(key=lambda p: (p.when, p.slug))
    return Trip(folder.name, str(meta.get("title") or folder.name), album_id,
                _markdown.render(intro) if intro.strip() else "", posts, str(meta.get("cover") or "").lower())


def read_trips(root: Path) -> list[Trip]:
    trips = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if not NAME.match(folder.name):
            log.warning("Journal: skipping %s, its name can't be part of a link (letters, digits, - and _)", folder)
            continue
        try:
            trip = read_trip(folder)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            log.warning("Journal: can't read %s: %s", folder, e)
            continue
        if trip:
            trips.append(trip)
    return trips


class Journal:
    """One family's trips. Empty when the family has no folder in JOURNAL_DIR."""

    def __init__(self, root: Path | None):
        self.root = root
        self.trips: dict[str, Trip] = {}

    def visible_to(self, member: Member | None) -> list[Trip]:
        """The trips this person may read, the latest first."""
        trips = [t for t in self.trips.values() if t.readable_by(member)]
        return sorted(trips, key=lambda t: (t.first_day, t.slug), reverse=True)

    async def refresh(self, feed: Feed) -> None:
        if self.root is None or not self.root.is_dir():
            self.trips = {}
            return
        trips = {}
        for trip in read_trips(self.root):
            try:
                await self._resolve(feed, trip)
            except Exception as e:
                log.warning("Feed %s: trip %s left out, its Immich album %s can't be read: %s",
                            feed.id, trip.slug, trip.album_id, e)
                continue
            trips[trip.slug] = trip
        self.trips = trips

    async def _resolve(self, feed: Feed, trip: Trip) -> None:
        """Who may read the trip, and what Immich knows about its photos. Photos that aren't in the trip's
        album are left out: nobody could like or comment on them, and they aren't shared."""
        album = await feed.curator.album(trip.album_id)
        trip.members = feed.core.people.members(album.get("albumUsers") or [])
        trip.activity = AlbumActivity(feed.curator, trip.album_id)
        assets = {}
        async for a in feed.curator.search(albumIds=[trip.album_id]):
            if not a.get("isTrashed") and a.get("type") in {"IMAGE", "VIDEO"}:
                assets[a["id"]] = a
        before = {m.asset_id: m.updated_at for t in self.trips.values() for p in t.posts for m in p.media}
        for post in trip.posts:
            written, kept = len(post.media), []
            for kind, block in post.blocks:
                if kind == "media":
                    block = [m for m in block if m.asset_id in assets]
                    for m in block:
                        a = assets[m.asset_id]
                        m.type, m.taken_at = a["type"], a["localDateTime"][:19]
                        m.live_video_id, m.updated_at = a.get("livePhotoVideoId") or "", a.get("updatedAt", "")
                        if before.get(m.asset_id, m.updated_at) != m.updated_at:
                            feed.core.media.purge(m.asset_id)
                    if not block:
                        continue
                kept.append((kind, block))
            post.blocks = kept
            if missing := written - len(post.media):
                log.warning("Feed %s: %d photo(s) of %s/%s aren't in the trip's album and are left out",
                            feed.id, missing, trip.slug, post.slug)


# --- writing: what the editor on the setup page reads and saves ---------------------------------------------

ASSET_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class Draft:
    """An entry as the editor sees it, drafts included: the text as written, photos as {id, caption, file}."""

    slug: str  # "" for an entry that isn't saved yet
    meta: dict
    blocks: list[dict]  # {"kind": "text", "text": …} or {"kind": "photos", "photos": [{"id", "caption", "file"}]}
    version: str = ""  # when the file was last changed, as read: saving checks nobody changed it in between

    @property
    def when(self) -> str:
        return _when(self.meta.get("date"), self.slug) or ""

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or "")

    @property
    def place(self) -> str:
        return str(self.meta.get("place") or "")

    @property
    def is_draft(self) -> bool:
        return bool(self.meta.get("draft"))

    @property
    def photo_ids(self) -> list[str]:
        return [p["id"] for b in self.blocks if b["kind"] == "photos" for p in b["photos"]]


def raw_blocks(body: str) -> list[dict]:
    """Like `blocks`, but the text stays Markdown, for editing."""
    out: list[dict] = []
    text: list[str] = []

    def flush():
        if "".join(text).strip():
            out.append({"kind": "text", "text": "\n".join(text).strip("\n")})
        text.clear()

    for line in body.splitlines():
        if ONLY_REFS.match(line):
            flush()
            out.append({"kind": "photos", "photos": [
                {"id": m["id"].lower(), "caption": m["alt"].strip(), "file": m["title"] or ""} for m in REF.finditer(line)]})
        else:
            text.append(line)
    flush()
    return out


def _version(path: Path) -> str:
    return str(path.stat().st_mtime_ns)


def load_draft(path: Path) -> Draft:
    meta, body = _frontmatter(path.read_text(encoding="utf-8"))
    return Draft(path.stem, meta, raw_blocks(body), _version(path))


def drafts(folder: Path) -> list[Draft]:
    """All entries of a trip, drafts included, the latest first."""
    out = []
    for path in folder.glob("*.md"):
        if path.name != "trip.md" and NAME.match(path.stem):
            try:
                out.append(load_draft(path))
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
                log.warning("Journal: can't read %s: %s", path, e)
    return sorted(out, key=lambda d: (d.when, d.slug), reverse=True)


def _photo_ref(photo: dict) -> str:
    caption = " ".join(str(photo.get("caption") or "").replace("[", "(").replace("]", ")").split())
    file = " ".join(str(photo.get("file") or "").replace('"', "'").split())
    return f'![{caption}](immich:{photo["id"]}' + (f' "{file}")' if file else ")")


def entry_text(meta: dict, blocks: list[dict]) -> str:
    """The file for an entry: front matter, then its text and lines of photos, separated by blank lines."""
    parts = []
    for b in blocks:
        if b["kind"] == "text" and b["text"].strip():
            parts.append(b["text"].strip("\n"))
        elif b["kind"] == "photos" and b["photos"]:
            parts.append(" ".join(_photo_ref(p) for p in b["photos"]))
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip() if meta else ""
    return (f"---\n{front}\n---\n\n" if front else "") + "\n\n".join(parts) + "\n"


def write_file(path: Path, text: str) -> None:
    """Replace a file in one go, so nobody ever reads half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def slugify(text: str, fallback: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:48].strip("-") or fallback


def free_name(folder: Path, base: str, suffix: str = "") -> str:
    """`base`, or base-2, base-3… if a file or folder of that name exists."""
    name, n = base, 2
    while (folder / f"{name}{suffix}").exists() or name == "trip":
        name, n = f"{base}-{n}", n + 1
    return name


def trash(path: Path) -> None:
    """Deleted entries go to the trip's .trash folder: out of sight, but nothing is lost by a wrong tap."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.parent / ".trash" / f"{path.stem}-{stamp}{path.suffix}"
    target.parent.mkdir(exist_ok=True)
    os.replace(path, target)


def trip_file(folder: Path) -> tuple[dict, str]:
    index = folder / "trip.md"
    return _frontmatter(index.read_text(encoding="utf-8")) if index.is_file() else ({}, "")


def save_trip_file(folder: Path, meta: dict, intro: str) -> None:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    write_file(folder / "trip.md", f"---\n{front}\n---\n" + (f"\n{intro.strip()}\n" if intro.strip() else ""))
