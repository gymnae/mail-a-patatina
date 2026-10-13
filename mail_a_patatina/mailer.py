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
from .links import LIKE, entry_url, feed_url, nice_date, unsubscribe_url

if TYPE_CHECKING:
    from .dispatch import Target

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)
_env.filters["nice_date"] = nice_date
LOGO = (Path(__file__).parent / "static" / "logo-email.png").read_bytes()


def build_digest(
    s: Settings, feed: FeedConfig, target: Target, entries: list[Entry], images: dict[str, bytes]
) -> EmailMessage:
    first = entries[0].caption or "A new moment"
    subject = first if len(entries) == 1 else f"{first} (+{len(entries) - 1} more)"
    domain = parseaddr(feed.smtp_from)[1].rpartition("@")[2] or None
    cids = {e.asset_id: make_msgid(domain=domain) for e in entries}
    logo_cid = make_msgid(domain=domain)
    ctx = {
        "title": feed.title,
        "target": target,
        "entries": entries,
        "cids": {k: v[1:-1] for k, v in cids.items()},
        "logo_cid": logo_cid[1:-1],
        "like": LIKE,
        "entry_url": lambda e, like=False: entry_url(s, feed.id, e.asset_id, target.token, like),
        "feed_url": feed_url(s, feed.id, target.token),
        "unsubscribe_url": unsubscribe_url(s, feed.id, target.token),
    }

    msg = EmailMessage()
    msg["Subject"] = f"{feed.title}: {textwrap.shorten(subject, 90, placeholder='…')}"
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
