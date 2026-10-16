"""Rebuild a feed's in-memory library and member list from its Immich album."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING

from .library import BACKLOG, Entry, now

if TYPE_CHECKING:
    from .core import Feed

log = logging.getLogger(__name__)
_warned_owners: set[tuple[str, str]] = set()

# Descriptions that cameras and apps write into EXIF, which Immich imports as if someone had typed them.
CAMERA_DEFAULTS = {"olympus digital camera", "sony dsc", "default", "image", "picture", "dcim", "digital camera",
                   "samsung", "lg electronics", "minolta dsc", "kodak digital still camera", "exif_jpeg_picture",
                   "screenshot"}
MACHINE_TEXT = re.compile(r"^[A-Za-z0-9+/=_-]{24,}$")  # e.g. Snapchat's base64 metadata blob


def human_caption(description: str | None) -> str:
    """The description, unless it was written by a camera or app rather than a person."""
    text = (description or "").strip()
    machine = MACHINE_TEXT.match(text) and all(re.search(c, text) for c in ("[0-9]", "[A-Z]", "[a-z]"))
    if text.casefold() in CAMERA_DEFAULTS or machine:
        return ""
    return text


async def run(feed: Feed) -> None:
    cfg = feed.cfg
    if not feed.writers:
        await feed.identify_keys()
    album = await feed.curator.album(cfg.album_id)
    feed.members = feed.core.people.members(album.get("albumUsers") or [])
    if album.get("isActivityEnabled") is False:
        log.warning("Feed %s: comments and likes are switched off for its album in Immich", feed.id)

    shareable = []
    unkeyed = set()
    async for asset in feed.curator.search(albumIds=[cfg.album_id]):
        if asset.get("isTrashed") or asset.get("type") not in {"IMAGE", "VIDEO"}:
            continue
        if asset["ownerId"] not in feed.writers:
            # Without the owner's key we couldn't remember that it was sent, and would resend it daily.
            unkeyed.add(asset["ownerId"])
            if (feed.id, asset["ownerId"]) not in _warned_owners:
                log.warning("Feed %s: skipping photos owned by user %s; add their key as co-parent on the setup page", feed.id, asset["ownerId"])
                _warned_owners.add((feed.id, asset["ownerId"]))
            continue
        caption = human_caption((asset.get("exifInfo") or {}).get("description"))
        if cfg.require_caption and not caption:
            continue
        shareable.append((asset, caption))

    known = feed.library.entries
    unknown = [a for a, _ in shareable if a["id"] not in known]
    limit = asyncio.Semaphore(8)

    async def fetch(asset: dict) -> tuple[str, dict | None]:
        async with limit:
            return asset["id"], await feed.writers[asset["ownerId"]].marker(asset["id"])

    markers = dict(await asyncio.gather(*(fetch(a) for a in unknown)))
    # First sync of an album nothing was ever sent from: what's there already is backlog.
    bootstrap = (
        not feed.synced_once and not known and not cfg.send_backlog
        and bool(markers) and all(m is None for m in markers.values())
    )
    ready = BACKLOG if bootstrap else now()

    entries: list[Entry] = []
    to_write: defaultdict[str, dict[str, dict]] = defaultdict(dict)
    for asset, caption in shareable:
        previous = known.get(asset["id"])
        marker = previous.marker if previous else markers.get(asset["id"])
        if marker is None:
            marker = {"ready": ready, "sent": {}}
            to_write[asset["ownerId"]][asset["id"]] = marker
        local = asset["localDateTime"]  # wall-clock time where the photo was taken
        entry = Entry(asset["id"], asset["ownerId"], asset["type"], local[:19], caption, asset.get("updatedAt", ""), marker,
                      asset.get("livePhotoVideoId") or "")
        if previous and previous.updated_at != entry.updated_at:
            feed.core.media.purge(entry.asset_id)
        entries.append(entry)

    for owner_id, items in to_write.items():
        await feed.writers[owner_id].set_markers(items)

    added = len(set(e.asset_id for e in entries) - set(known))
    removed = len(set(known) - set(e.asset_id for e in entries))
    feed.library.replace(entries)
    feed.unkeyed = unkeyed
    feed.synced_once = True
    if added or removed:
        log.info("Feed %s: %d new%s, %d no longer shared", feed.id, added, " (backlog)" if bootstrap else "", removed)
