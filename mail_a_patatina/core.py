"""Wires settings, Immich connections and the in-memory caches together for the web app and the CLI.

A Feed is one family: its album, owner key, members, library and chats. Feeds share nothing but the
relatives' accounts (People), the image cache and the outgoing transports."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from . import dispatch, mailer, sync
from apscheduler.triggers.cron import CronTrigger

from .config import FeedConfig, Settings, feed_config, load_feeds
from .immich import Immich
from .library import Library
from .media import MediaCache
from .people import Member, People
from .store import FeedStore

log = logging.getLogger(__name__)

ACTIVITY_TTL = 15  # seconds; likes and comments are re-read from Immich this often at most


class Feed:
    def __init__(self, core: Core, cfg: FeedConfig):
        self.core = core
        self.cfg = cfg
        self.id = cfg.id
        # The album owner's key reads the album, its likes/comments and media.
        self.curator = Immich(core.settings.immich_url, api_key=cfg.api_key)
        self._contributors = [Immich(core.settings.immich_url, api_key=k) for k in cfg.contributor_keys]
        # Delivery markers can only be written by an asset's owner: user id -> that user's connection.
        self.writers: dict[str, Immich] = {}
        self.members: dict[str, Member] = {}  # album members by user id, refreshed on every sync
        self.library = Library()
        self.synced_once = False
        self.refused_keys = 0  # co-parent keys Immich no longer accepts
        self.unkeyed: set[str] = set()  # users with photos in the album but no key here (co-parents to add)
        self.lock = asyncio.Lock()  # this feed's sync and dispatch never overlap
        self._activities: tuple[float, list[dict]] | None = None

    @property
    def bot(self) -> Member | None:
        return next((m for m in self.members.values() if m.email == self.cfg.bot_email), None)

    def by_email(self, email: str) -> Member | None:
        return next((m for m in self.members.values() if m.email == email.lower()), None)

    def relatives(self) -> list[Member]:
        """Members who get the daily email: everyone the album is shared with, except its owner and the bot."""
        return [m for m in self.members.values() if m.role != "owner" and m.email != self.cfg.bot_email]

    async def identify_keys(self) -> None:
        me = await self.curator.me()
        self.curator.user_id = me["id"]
        self.writers[me["id"]] = self.curator
        self.refused_keys = 0
        for client in self._contributors:
            try:
                me = await client.me()
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (401, 403):
                    raise
                # A revoked co-parent key only costs their photos, not the whole feed.
                log.warning("Feed %s: Immich refused a co-parent key (revoked?)", self.id)
                self.refused_keys += 1
                continue
            client.user_id = me["id"]
            self.writers[me["id"]] = client

    async def refresh_members(self) -> None:
        """Just the album's member list, e.g. after the admin approved relatives from the CLI."""
        album = await self.curator.album(self.cfg.album_id)
        self.members = self.core.people.members(album.get("albumUsers") or [])

    async def sync(self) -> None:
        async with self.lock:
            await sync.run(self)

    async def dispatch(self) -> None:
        async with self.lock:
            await self.core.people.reload_keys()
            await sync.run(self)
            await dispatch.run(self)

    async def activities(self) -> list[dict]:
        if self._activities is None or time.monotonic() - self._activities[0] > ACTIVITY_TTL:
            self._activities = (time.monotonic(), await self.curator.activities(self.cfg.album_id))
        return self._activities[1]

    def activities_changed(self) -> None:
        self._activities = None

    async def notify_admins(self, subject: str, body: str) -> None:
        s = self.core.settings
        if not (self.cfg.admin_emails and s.smtp_host):
            return
        try:
            await mailer.send(s, mailer.build_simple(self.cfg.smtp_from, self.cfg.admin_emails, subject, body))
        except Exception:
            log.exception("Could not send admin notification for feed %s", self.id)

    async def aclose(self) -> None:
        for client in {id(c): c for c in [self.curator, *self._contributors]}.values():
            await client.aclose()


class Core:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.people = People(settings)
        self.media = MediaCache(settings.data_dir / "cache")
        self.http = httpx.AsyncClient(timeout=120)
        self.store = FeedStore(settings.setup_dir)
        self.feeds: dict[str, Feed] = {cfg.id: Feed(self, cfg) for cfg in load_feeds(settings)}
        self.scheduler = None  # set by the web app; the CLI runs without one

    def schedule(self, feed: Feed) -> None:
        if self.scheduler is None:
            return
        hour, minute = (int(x) for x in feed.cfg.send_time.split(":"))
        self.scheduler.add_job(feed.sync, "interval", minutes=self.settings.sync_minutes, id=f"sync-{feed.id}",
                               coalesce=True, max_instances=1, replace_existing=True)
        self.scheduler.add_job(
            feed.dispatch, CronTrigger(hour=hour, minute=minute, timezone=feed.cfg.timezone),
            id=f"dispatch-{feed.id}", coalesce=True, max_instances=1, misfire_grace_time=3600, replace_existing=True,
        )

    async def apply_stored(self, feed_id: str) -> Feed | None:
        """(Re)load one family saved by the setup page, or drop it if it was deleted."""
        old = self.feeds.pop(feed_id, None)
        if old is not None:
            if self.scheduler is not None:
                for job in (f"sync-{feed_id}", f"dispatch-{feed_id}"):
                    if self.scheduler.get_job(job):
                        self.scheduler.remove_job(job)
            await old.aclose()
        raw = self.store.get(feed_id)
        if raw is None:
            return None
        feed = Feed(self, feed_config(raw, self.settings))
        self.feeds[feed_id] = feed
        try:
            await feed.sync()
        except Exception:
            log.exception("First sync of feed %s failed", feed_id)
        self.schedule(feed)
        return feed

    def feed(self, feed_id: str | None) -> Feed:
        """The named feed, or the only one when there's just one."""
        if feed_id is None and len(self.feeds) == 1:
            return next(iter(self.feeds.values()))
        if feed_id not in self.feeds:
            raise SystemExit(f"Name a feed with --feed, one of: {', '.join(self.feeds)}")
        return self.feeds[feed_id]

    async def sync_all(self) -> None:
        for feed in self.feeds.values():
            try:
                await feed.sync()
            except Exception:
                log.exception("Sync of feed %s failed", feed.id)

    async def aclose(self) -> None:
        await self.people.aclose()
        for feed in self.feeds.values():
            await feed.aclose()
        await self.http.aclose()
