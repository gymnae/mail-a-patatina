"""Stateless magic links (HMAC-signed) and small formatting helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from .config import Settings

LIKE = "❤️"  # Immich has exactly one reaction: like


def _sign(s: Settings, payload: str) -> str:
    mac = hmac.new(s.secret_key.encode(), f"link:{s.link_version}:{payload}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:24]


@dataclass(frozen=True)
class Token:
    feed_id: str
    kind: str  # 'u' = an Immich user id, 'c' = a chat ref
    ident: str
    expires: int  # unix time; 0 for links from before links expired
    expired: bool


def make_token(s: Settings, feed_id: str, kind: str, ident: str, days: int | None = None) -> str:
    """The token in a magic link. The feed is part of what's signed, so a link from one family's email can
    never open another family's album. It expires after LINK_DAYS (CHAT_LINK_DAYS for chats), so a
    forwarded email or an old message stops signing anyone in."""
    if days is None:
        days = s.chat_link_days if kind == "c" else s.link_days
    payload = f"{kind}.{feed_id}.{ident}.{int(time.time()) + days * 86400}"
    return f"{payload}.{_sign(s, payload)}"


def read_token(s: Settings, token: str) -> Token | None:
    """None if the token isn't genuine. An expired one still says whose it was, so that person can be
    emailed a fresh link (and can still unsubscribe)."""
    parts = token.split(".")
    if len(parts) == 4:  # from before links expired: genuine, but expired
        kind, feed_id, ident, sig = parts
        expires, payload = "0", f"{kind}.{feed_id}.{ident}"
    elif len(parts) == 5:
        kind, feed_id, ident, expires, sig = parts
        payload = f"{kind}.{feed_id}.{ident}.{expires}"
    else:
        return None
    if kind not in ("u", "c") or not expires.isdigit() or not hmac.compare_digest(sig, _sign(s, payload)):
        return None
    return Token(feed_id, kind, ident, int(expires), int(expires) < time.time())


def make_session(s: Settings, feed_id: str, kind: str, ident: str) -> str:
    """What the sign-in cookie holds once a link was opened: signed for this purpose only, so a link token
    can't be used as one, and valid for SESSION_DAYS, renewed while the browser keeps visiting."""
    payload = f"{kind}.{feed_id}.{ident}.{int(time.time()) + s.session_days * 86400}"
    return f"{payload}.{_sign(s, f'session.{payload}')}"


def read_session(s: Settings, value: str) -> Token | None:
    """None unless genuine and unexpired. Cookies from before sessions existed hold an old link token:
    accepted, and replaced by a session right away (expires 0 makes it due for renewal)."""
    parts = value.split(".")
    if len(parts) == 4:
        old = read_token(s, value)
        return Token(old.feed_id, old.kind, old.ident, 0, False) if old else None
    if len(parts) != 5:
        return None
    kind, feed_id, ident, expires, sig = parts
    if (kind not in ("u", "c") or not expires.isdigit() or int(expires) < time.time()
            or not hmac.compare_digest(sig, _sign(s, f"session.{kind}.{feed_id}.{ident}.{expires}"))):
        return None
    return Token(feed_id, kind, ident, int(expires), False)


def photo_url(s: Settings, feed_id: str, asset_id: str, token: str) -> str:
    """An image link for one recipient's email. Unlike their magic link it signs nobody in, and it stops
    working after PHOTO_LINK_DAYS, or as soon as the photo or the recipient leaves the album."""
    kind, _, ident = token.split(".")[:3]
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


