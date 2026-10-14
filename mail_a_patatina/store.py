"""Families configured on the setup page: one small YAML file each in SETUP_DIR (a secrets volume).

Configuration only, the same kind of thing feeds.yml holds: album id, the owner's API key, schedule,
chats, and relatives waiting for the installation admin's approval. Photos, captions, recipients,
likes, comments and delivery markers stay in Immich."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

import yaml

from .config import FEED_ID, RESERVED_IDS


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:32].strip("-") or "family"


class FeedStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, feed_id: str) -> Path:
        if not FEED_ID.match(feed_id):
            raise ValueError(f"bad feed id {feed_id!r}")
        return self.root / f"{feed_id}.yml"

    def all(self) -> dict[str, dict]:
        if not self.root.is_dir():
            return {}
        feeds = {}
        for path in sorted(self.root.glob("*.yml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            feeds[raw.get("id", path.stem)] = raw
        return feeds

    def get(self, feed_id: str) -> dict | None:
        path = self._path(feed_id)
        return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None

    def owned_by(self, email: str) -> list[dict]:
        return [f for f in self.all().values() if str(f.get("owner_email", "")).lower() == email.lower()]

    def save(self, raw: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        path = self._path(raw["id"])
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# Written by the Mail-A-Patatina setup page. Contains an API key: keep private.\n")
            yaml.safe_dump(raw, fh, sort_keys=False, allow_unicode=True)
        tmp.replace(path)

    def delete(self, feed_id: str) -> None:
        self._path(feed_id).unlink(missing_ok=True)

    def free_id(self, wanted: str, taken: set[str]) -> str:
        base = slug(wanted)
        candidate, n = base, 2
        while candidate in taken or candidate in RESERVED_IDS or self._path(candidate).exists():
            suffix = f"-{n}"
            candidate, n = base[: 32 - len(suffix)] + suffix, n + 1
        return candidate
