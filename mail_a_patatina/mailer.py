"""Builds the daily email and sends it over the configured SMTP account."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import textwrap
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import FeedConfig, Settings
from .library import Entry
from .i18n import install, t
from .links import LIKE, entry_url, feed_url, photo_url, unsubscribe_url

if TYPE_CHECKING:
    from .dispatch import Target

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)
install(_env)
LOGO = (Path(__file__).parent / "static" / "logo-email.png").read_bytes()


def build_digest(
    s: Settings, feed: FeedConfig, target: Target, entries: list[Entry], images: dict[str, bytes]
) -> EmailMessage:
    lang = target.lang
    first = entries[0].caption or t(lang, "A new moment")
    subject = first if len(entries) == 1 else t(lang, "{first} (+{n} more)", first=first, n=len(entries) - 1)
    domain = parseaddr(feed.smtp_from)[1].rpartition("@")[2] or None
    cids = {asset_id: make_msgid(domain=domain) for asset_id in images}  # empty if photos stay on the server
    logo_cid = make_msgid(domain=domain)
    ctx = {
        "lang": lang,
        "title": feed.title,
        "target": target,
        "entries": entries,
        "img_src": lambda e: (f"cid:{cids[e.asset_id][1:-1]}" if e.asset_id in cids
                              else photo_url(s, feed.id, e.asset_id, target.token)),
        "logo_cid": logo_cid[1:-1],
        "like": LIKE,
        "entry_url": lambda e, like=False: entry_url(s, feed.id, e.asset_id, target.token, like),
        "feed_url": feed_url(s, feed.id, target.token),
        "unsubscribe_url": unsubscribe_url(s, feed.id, target.token),
        "link_days": s.link_days,
    }

    msg = EmailMessage()
    msg["Subject"] = t(lang, "{title}: {subject}", title=feed.title, subject=textwrap.shorten(subject, 90, placeholder="…"))
    msg["From"] = feed.smtp_from
    msg["To"] = target.address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["List-Unsubscribe"] = f"<{ctx['unsubscribe_url']}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(_env.get_template("email.txt").render(ctx))
    msg.add_alternative(_env.get_template("email.html").render(ctx), subtype="html")
    html_part = msg.get_payload()[1]
    html_part.add_related(LOGO, "image", "png", cid=logo_cid, filename="mail-a-patatina.png")
    for asset_id, cid in cids.items():
        html_part.add_related(images[asset_id], "image", "jpeg", cid=cid, filename=f"{asset_id[:8]}.jpg")
    msg["Content-Language"] = lang  # last: set_content() drops Content-* headers
    return msg


def build_comment(feed: FeedConfig, to: str, lang: str, ctx: dict, image: bytes | None) -> EmailMessage:
    """Tells the family about a new comment, in the daily email's look: the photo, who wrote what, and a
    button back to it. `ctx`: who, body, what (the photo's caption or the entry), trip, url, name, link_days,
    settings_url (each may be empty)."""
    domain = parseaddr(feed.smtp_from)[1].rpartition("@")[2] or None
    logo_cid, image_cid = make_msgid(domain=domain), make_msgid(domain=domain)
    ctx = {**ctx, "lang": lang, "title": feed.title, "logo_cid": logo_cid[1:-1],
           "image_cid": image_cid[1:-1] if image else None}
    msg = EmailMessage()
    headline = t(lang, "{name} commented", name=ctx["who"])
    msg["Subject"] = t(lang, "{title}: {subject}", title=feed.title, subject=headline)
    msg["From"] = feed.smtp_from
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(_env.get_template("comment_email.txt").render(ctx))
    msg.add_alternative(_env.get_template("comment_email.html").render(ctx), subtype="html")
    html_part = msg.get_payload()[1]
    html_part.add_related(LOGO, "image", "png", cid=logo_cid, filename="mail-a-patatina.png")
    if image:
        html_part.add_related(image, "image", "jpeg", cid=image_cid, filename="photo.jpg")
    msg["Content-Language"] = lang  # last: set_content() drops Content-* headers
    return msg


def build_likes(feed: FeedConfig, to: str, lang: str, ctx: dict, images: dict[str, bytes]) -> EmailMessage:
    """Tells the family about the day's likes in one email, in the daily email's look: each liked photo, who
    liked it, and a link to it. `ctx`: photos ({asset_id, names, url, what}), name, link_days, settings_url,
    off_url; `images`: each photo's thumbnail (empty if there's none)."""
    domain = parseaddr(feed.smtp_from)[1].rpartition("@")[2] or None
    logo_cid = make_msgid(domain=domain)
    cids = {p["asset_id"]: make_msgid(domain=domain) for p in ctx["photos"] if images.get(p["asset_id"])}
    ctx = {**ctx, "lang": lang, "title": feed.title, "logo_cid": logo_cid[1:-1], "like": LIKE,
           "photos": [{**p, "image_cid": cids[p["asset_id"]][1:-1] if p["asset_id"] in cids else None}
                      for p in ctx["photos"]]}
    msg = EmailMessage()
    msg["Subject"] = t(lang, "{title}: {subject}", title=feed.title, subject=t(lang, "New likes"))
    msg["From"] = feed.smtp_from
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(_env.get_template("likes_email.txt").render(ctx))
    msg.add_alternative(_env.get_template("likes_email.html").render(ctx), subtype="html")
    html_part = msg.get_payload()[1]
    html_part.add_related(LOGO, "image", "png", cid=logo_cid, filename="mail-a-patatina.png")
    for asset_id, cid in cids.items():
        html_part.add_related(images[asset_id], "image", "jpeg", cid=cid, filename=f"{asset_id[:8]}.jpg")
    msg["Content-Language"] = lang  # last: set_content() drops Content-* headers
    return msg


def build_simple(sender: str, to: list[str], subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    return msg


def _send_blocking(s: Settings, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    if s.smtp_security == "ssl":
        smtp = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=60)
    else:
        smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=60)
    with smtp:
        if s.smtp_security == "starttls":
            smtp.starttls(context=context)
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


async def send(s: Settings, msg: EmailMessage) -> None:
    if not s.smtp_host:
        raise RuntimeError("SMTP_HOST is not configured")
    await asyncio.to_thread(_send_blocking, s, msg)
