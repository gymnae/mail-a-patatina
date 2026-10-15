"""Stateless magic links (HMAC-signed) and small formatting helpers."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import time
from urllib.parse import urlencode

from .config import Settings

LIKE = "❤️"  # Immich has exactly one reaction: like


def _sign(s: Settings, payload: str) -> str:
    mac = hmac.new(s.secret_key.encode(), f"link:{s.link_version}:{payload}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:24]


def make_token(s: Settings, feed_id: str, kind: str, ident: str) -> str:
    """kind 'u' = an Immich user id, kind 'c' = a chat ref. The feed is part of what's signed,
    so a link from one family's email can never open another family's album."""
    payload = f"{kind}.{feed_id}.{ident}"
    return f"{payload}.{_sign(s, payload)}"


def read_token(s: Settings, token: str) -> tuple[str, str, str] | None:
    """-> (feed_id, kind, ident), or None if the token isn't genuine."""
    try:
        kind, feed_id, ident, sig = token.split(".")
    except ValueError:
        return None
    if kind not in ("u", "c") or not hmac.compare_digest(sig, _sign(s, f"{kind}.{feed_id}.{ident}")):
        return None
    return feed_id, kind, ident


def photo_url(s: Settings, feed_id: str, asset_id: str, token: str) -> str:
    """An image link for one recipient's email. Unlike their magic link it signs nobody in, and it stops
    working after PHOTO_LINK_DAYS, or as soon as the photo or the recipient leaves the album."""
    kind, _, ident, _ = token.split(".")
    expires = int(time.time()) + s.photo_link_days * 86400
    payload = f"{kind}.{ident}.{expires}"
    return f"{s.public_url}/{feed_id}/p/{asset_id}/{payload}.{_sign(s, f'photo.{feed_id}.{asset_id}.{payload}')}.jpg"


def read_photo_ticket(s: Settings, feed_id: str, asset_id: str, ticket: str) -> tuple[str, str] | None:
    """-> (kind, ident) of the recipient, or None if the ticket is forged or expired."""
    try:
        kind, ident, expires, sig = ticket.split(".")
        expired = int(expires) < time.time()
    except ValueError:
        return None
    if expired or not hmac.compare_digest(sig, _sign(s, f"photo.{feed_id}.{asset_id}.{kind}.{ident}.{expires}")):
        return None
    return kind, ident


INVITE_DAYS = 14


def invite_url(s: Settings, feed_id: str, user_id: str, role: str) -> str:
    """A link for one album member ("coparent" or "relative") to connect their own Immich account by signing
    in, without the setup page. Only that member's account can use it; it expires after INVITE_DAYS."""
    expires = int(time.time()) + INVITE_DAYS * 86400
    payload = f"{role}.{feed_id}.{user_id}.{expires}"
    return f"{s.public_url}/setup/join/{payload}.{_sign(s, f'invite.{payload}')}"


def read_invite(s: Settings, token: str) -> tuple[str, str, str] | None:
    """-> (role, feed_id, user_id), or None if the invite is forged or expired."""
    try:
        role, feed_id, user_id, expires, sig = token.split(".")
        expired = int(expires) < time.time()
    except ValueError:
        return None
    if (expired or role not in ("coparent", "relative")
            or not hmac.compare_digest(sig, _sign(s, f"invite.{role}.{feed_id}.{user_id}.{expires}"))):
        return None
    return role, feed_id, user_id


def derived_password(s: Settings, email: str) -> str:
    """Password of a managed Immich account, derived from SECRET_KEY so it never has to be stored."""
    mac = hmac.new(s.secret_key.encode(), f"immich-password:{email.lower()}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def feed_url(s: Settings, feed_id: str, token: str) -> str:
    return f"{s.public_url}/{feed_id}/?{urlencode({'t': token})}"


def entry_url(s: Settings, feed_id: str, asset_id: str, token: str, like: bool = False) -> str:
    params = {"t": token}
    if like:
        params["like"] = "1"
    return f"{s.public_url}/{feed_id}/e/{asset_id}?{urlencode(params)}"


def unsubscribe_url(s: Settings, feed_id: str, token: str) -> str:
    return f"{s.public_url}/{feed_id}/unsubscribe?{urlencode({'t': token})}"


def clip(text: str, limit: int) -> str:
    """Shorten to `limit` characters, keeping line breaks (unlike textwrap.shorten)."""
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def nice_date(value: str) -> str:
    """'2026-10-12' or '2026-10-12T14:33:00' -> 'Monday, 12 October 2026'."""
    d = dt.date.fromisoformat(value[:10])
    return f"{d:%A}, {d.day} {d:%B %Y}"
