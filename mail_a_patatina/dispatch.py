"""Daily send of one feed: every recipient and chat gets the moments it hasn't received yet.
What was sent to whom is written back onto each asset in Immich (asset metadata)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import mailer
from .chats import SENDERS
from .library import BACKLOG, Entry, ago, now
from .i18n import nice_date, t
from .links import clip, entry_url, make_token
from .people import Member

if TYPE_CHECKING:
    from .core import Feed

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    key: str  # how deliveries to it are recorded in the asset marker
    kind: str  # email | whatsapp | signal | telegram
    name: str
    address: str
    token: str  # magic-link token for its links (only valid for this feed)
    member: Member | None  # the person behind it, if it's personal
    lang: str = "en"  # the person's language as their browser last showed it, else DEFAULT_LANGUAGE


async def targets(feed: Feed) -> list[Target]:
    s, people = feed.core.settings, feed.core.people
    lang = lambda member: (member and feed.core.languages.get(member.id)) or s.default_language  # noqa: E731
    out = []
    for m in feed.relatives():
        if await people.wants_email(m):
            out.append(Target(f"user:{m.id}", "email", m.name, m.email, make_token(s, feed.id, "u", m.id), m, lang(m)))
    for chat in feed.cfg.chats:
        person = feed.by_email(chat.person) if chat.person else None
        # A one-to-one chat signs in as its person, but its messages stay around as long as a group's.
        token = (make_token(s, feed.id, "u", person.id, s.chat_link_days) if person
                 else make_token(s, feed.id, "c", chat.ref))
        out.append(Target(f"chat:{chat.key}", chat.kind, chat.name, chat.address, token, person, lang(person)))
    return out


def pending(feed: Feed, target: Target) -> list[Entry]:
    """Settled, not yet sent to this target, and either brand new or first sent only recently
    (which retries failures and gives someone who just joined the last couple of days)."""
    s = feed.core.settings
    settled, recent = ago(s.settle_minutes), ago(s.resend_hours * 60)
    out = []
    for e in feed.library.ordered:
        ready = e.marker.get("ready", BACKLOG)
        if ready == BACKLOG or ready > settled or target.key in e.sent:
            continue
        first_sent = min(e.sent.values(), default=None)
        if first_sent and first_sent < recent:
            continue
        out.append(e)
    return out[:1] if feed.cfg.send_mode == "drip" else out


def chat_caption(feed: Feed, target: Target, entry: Entry) -> str:
    link = entry_url(feed.core.settings, feed.id, entry.asset_id, target.token)
    tail = f"\n\n📅 {nice_date(entry.taken_at, target.lang)}\n💬 {link}"
    if entry.type == "VIDEO":
        tail = "\n" + t(target.lang, "▶️ Video – watch it via the link") + tail
    # Keep the link intact; Telegram captions are limited to 1024 characters.
    return clip(entry.caption, 1000 - len(tail)) + tail


async def _record(feed: Feed, target: Target, entries: list[Entry]) -> None:
    stamp = now()
    by_owner: defaultdict[str, dict[str, dict]] = defaultdict(dict)
    for e in entries:
        e.sent[target.key] = stamp
        by_owner[e.owner_id][e.asset_id] = e.marker
    for owner_id, items in by_owner.items():
        await feed.writers[owner_id].set_markers(items)


async def send_email(feed: Feed, target: Target, entries: list[Entry], record: bool = True) -> None:
    core = feed.core
    images = {} if feed.cfg.keep_photos_on_server else {
        e.asset_id: await core.media.jpeg(feed.curator, e.asset_id, "preview") for e in entries}
    await mailer.send(core.settings, mailer.build_digest(core.settings, feed.cfg, target, entries, images))
    if record:
        await _record(feed, target, entries)


async def send_chat(feed: Feed, target: Target, entries: list[Entry]) -> None:
    core = feed.core
    send = SENDERS[target.kind]
    for entry in entries:
        jpeg = None if feed.cfg.keep_photos_on_server else await core.media.jpeg(feed.curator, entry.asset_id, "preview")
        await send(core.settings, core.http, feed.cfg, target.address, jpeg, chat_caption(feed, target, entry))
        await _record(feed, target, [entry])  # per message, so a failure halfway doesn't resend the first ones


async def run(feed: Feed) -> None:
    for target in await targets(feed):
        entries = pending(feed, target)
        if not entries:
            continue
        try:
            if target.kind == "email":
                await send_email(feed, target, entries)
            else:
                await send_chat(feed, target, entries)
            log.info("Feed %s: sent %d moment(s) to %s", feed.id, len(entries), target.name)
        except Exception:
            # Whatever was recorded stays recorded; the rest is retried next run.
            log.exception("Feed %s: sending to %s failed", feed.id, target.name)
