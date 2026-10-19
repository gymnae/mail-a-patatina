"""In-memory view of what is shared, rebuilt from Immich on every sync. A cache, never a source of truth."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def ago(minutes: int) -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


# `ready` value for assets that were already in the album before the first sync:
# shown on the website, never pushed out as new.
BACKLOG = "1970-01-01T00:00:00+00:00"


@dataclass
class Entry:
    asset_id: str
    owner_id: str
    type: str  # IMAGE | VIDEO
    taken_at: str  # local wall-clock time, YYYY-MM-DDTHH:MM:SS
    caption: str
    updated_at: str
    # Stored in Immich asset metadata: {"ready": iso, "sent": {target_key: iso}}
    marker: dict = field(default_factory=dict)
    live_video_id: str = ""  # the motion part of a live photo, a hidden video asset in Immich

    @property
    def day(self) -> str:
        return self.taken_at[:10]

    @property
    def sent(self) -> dict[str, str]:
        return self.marker.setdefault("sent", {})


class Library:
    def __init__(self):
        self.entries: dict[str, Entry] = {}
        self.ordered: list[Entry] = []  # oldest first

    def replace(self, entries: list[Entry]) -> None:
        self.ordered = sorted(entries, key=lambda e: (e.taken_at, e.asset_id))
        self.entries = {e.asset_id: e for e in self.ordered}

    def get(self, asset_id: str) -> Entry | None:
        return self.entries.get(asset_id)

    def newest_before(self, before_id: str | None, n: int) -> list[Entry]:
        """Feed page: the n newest entries older than `before_id` (or the newest overall)."""
        anchor = self.entries.get(before_id) if before_id else None
        end = self.ordered.index(anchor) if anchor else len(self.ordered)
        return self.ordered[max(end - n, 0):end][::-1]

    def neighbours(self, entry: Entry) -> tuple[Entry | None, Entry | None]:
        """(newer, older)"""
        i = self.ordered.index(entry)
        newer = self.ordered[i + 1] if i + 1 < len(self.ordered) else None
        older = self.ordered[i - 1] if i > 0 else None
        return newer, older

    def day(self, yyyy_mm_dd: str) -> list[Entry]:
        """One day's moments, oldest first, like that day's dispatch."""
        return [e for e in self.ordered if e.day == yyyy_mm_dd]

    def days_around(self, yyyy_mm_dd: str) -> tuple[str | None, str | None]:
        """(previous, next) day that has moments"""
        days = sorted({e.day for e in self.ordered})
        before = [d for d in days if d < yyyy_mm_dd]
        after = [d for d in days if d > yyyy_mm_dd]
        return (before[-1] if before else None), (after[0] if after else None)

    def month(self, yyyy_mm: str) -> dict[str, list[Entry]]:
        by_day: dict[str, list[Entry]] = {}
        for e in self.ordered:
            if e.taken_at.startswith(yyyy_mm):
                by_day.setdefault(e.day, []).append(e)
        return by_day
