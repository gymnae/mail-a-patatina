"""Self-service setup at /setup/: Immich users sign in through the OIDC provider (e.g. Pocket ID) and
set up their own family feed: album, sending rules, chats and relatives.

Signing in only proves who someone is; access to their Immich account comes from the API key they
create in Immich and paste here. The key must belong to the same email address that signed in, have
exactly what the feed needs, and nothing that administers Immich or deletes anything.

New relatives wait for the installation admin's approval (`python -m mail_a_patatina approve`), so the
running service never holds admin rights."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import time
import zoneinfo
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import dispatch, mailer
from .config import CHAT_KINDS, clean_email
from .immich import (ACTIVITY_PERMISSIONS, CONTRIBUTOR_PERMISSIONS, KEY_NAME, OWNER_PERMISSIONS, RELATIVE_PERMISSIONS,
                     Immich, LoginFailed)
from .library import now
from .i18n import from_header, install
from .links import INVITE_DAYS, LIKE, invite_url, make_token, read_invite
from .people import save_key
from .whatsapp import Gateway, GatewayError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/setup")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
install(templates.env)  # English here, except on the invite pages relatives and co-parents open
# Appended to the stylesheet and script links, so browsers fetch them again after an update.
ASSET_VERSION = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted((Path(__file__).parent / "static").glob("*.[cj]s*")))
).hexdigest()[:10]
templates.env.globals["asset_version"] = ASSET_VERSION

COOKIE = "map_setup"
IDLE_SECONDS = 3600
LOGIN_SECONDS = 600
# Printable ASCII only (! to ~, without @): anything else in an address is almost always a stray character.
EMAIL = re.compile(r"^[!-?A-~]+@[!-?A-~]+\.[!-?A-~]+$")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TIMEZONES = sorted(zoneinfo.available_timezones())


@dataclass
class SetupSession:
    email: str
    name: str
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    seen: float = field(default_factory=time.monotonic)
    draft_key: str | None = None  # a checked album-owner key, between the wizard's steps


SESSIONS: dict[str, SetupSession] = {}
# WhatsApp device -> (kind "qr" | "code", PNG data URI or pairing code, monotonic expiry), while a family links its account
LINKING: dict[str, tuple[str, str, float]] = {}
PHONE = re.compile(r"^\+?[0-9 ()/-]{6,24}$")
PENDING_LOGINS: dict[str, tuple[str, float]] = {}  # OIDC state -> (PKCE verifier, started)
# Immich OAuth state -> (PKCE verifier, what binds it to the browser that started it: the setup session's csrf,
# or the invite cookie; feed id, "" for a new feed, or "join:<invite token>"; started)
IMMICH_LOGINS: dict[str, tuple[str, str, str, float]] = {}
JOIN_COOKIE = "map_join"
_discovery: dict | None = None


def core(request: Request):
    return request.app.state.core


def settings(request: Request):
    return core(request).settings


# --- sign-in -------------------------------------------------------------------


async def oidc_config(request: Request) -> dict:
    global _discovery
    if _discovery is None:
        s = settings(request)
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(f"{s.oidc_issuer}/.well-known/openid-configuration")
            r.raise_for_status()
            _discovery = r.json()
    return _discovery


def current(request: Request) -> SetupSession | None:
    sid = request.cookies.get(COOKIE)
    session = SESSIONS.get(sid) if sid else None
    if session is None:
        return None
    if time.monotonic() - session.seen > IDLE_SECONDS:
        SESSIONS.pop(sid, None)
        return None
    session.seen = time.monotonic()
    return session


def require(request: Request, csrf: str | None = None) -> SetupSession:
    session = current(request)
    if session is None:
        raise HTTPException(303, headers={"Location": "/setup/"})
    if csrf is not None and not secrets.compare_digest(csrf, session.csrf):
        raise HTTPException(400, "Form expired, please go back and try again")
    return session


def start_session(request: Request, email: str, name: str) -> Response:
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = SetupSession(clean_email(email), name or email)
    response = RedirectResponse("/setup/", status_code=303)
    response.set_cookie(COOKIE, sid, path="/setup", httponly=True, samesite="lax",
                        secure=settings(request).secure_cookies)
    return response


@router.get("/login")
async def login(request: Request):
    s = settings(request)
    if not s.oidc_issuer:
        return RedirectResponse("/setup/", status_code=303)
    for state, (_, started) in list(PENDING_LOGINS.items()):
        if time.monotonic() - started > LOGIN_SECONDS:
            PENDING_LOGINS.pop(state, None)
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    PENDING_LOGINS[state] = (verifier, time.monotonic())
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {
        "response_type": "code", "client_id": s.oidc_client_id, "redirect_uri": f"{s.public_url}/setup/callback",
        "scope": "openid email profile", "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{(await oidc_config(request))['authorization_endpoint']}?{urlencode(params)}", 303)


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    s = settings(request)
    pending = PENDING_LOGINS.pop(state, None)
    if error or not code or pending is None or time.monotonic() - pending[1] > LOGIN_SECONDS:
        return page(request, "setup/message.html", None, title="Sign-in didn't work", text="Please try again.")
    oidc = await oidc_config(request)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": f"{s.public_url}/setup/callback",
            "code_verifier": pending[0], "client_id": s.oidc_client_id}
    async with httpx.AsyncClient(timeout=30) as http:
        auth = (s.oidc_client_id, s.oidc_client_secret) if s.oidc_client_secret else None
        token = await http.post(oidc["token_endpoint"], data=form, auth=auth)
        token.raise_for_status()
        # Straight from the provider over TLS, so the userinfo endpoint is trusted for the profile.
        info = await http.get(oidc["userinfo_endpoint"],
                              headers={"Authorization": f"Bearer {token.json()['access_token']}"})
        info.raise_for_status()
    profile = info.json()
    if not profile.get("email"):
        return page(request, "setup/message.html", None, title="No email address",
                    text=f"{s.oidc_name} didn't share an email address, which is needed to match your Immich account.")
    return start_session(request, profile["email"], profile.get("name") or profile.get("preferred_username", ""))


@router.post("/login/key")
async def login_with_key(request: Request, api_key: str = Form()):
    """Without an OIDC provider, an Immich API key of the person is the sign-in."""
    s = settings(request)
    if s.oidc_issuer:
        raise HTTPException(404)
    client = Immich(s.immich_url, api_key=api_key.strip())
    try:
        me = await client.me()
    except httpx.HTTPStatusError:
        return page(request, "setup/login.html", None, error="That key wasn't accepted by Immich.")
    finally:
        await client.aclose()
    return start_session(request, me["email"], me["name"])


@router.post("/logout")
async def logout(request: Request, csrf: str = Form()):
    require(request, csrf)
    SESSIONS.pop(request.cookies.get(COOKIE), None)
    response = RedirectResponse("/setup/", status_code=303)
    response.delete_cookie(COOKIE, path="/setup")
    return response


# --- pages -----------------------------------------------------------------------


def page(request: Request, template: str, session: SetupSession | None, status: int = 200, **ctx) -> HTMLResponse:
    s = settings(request)
    ctx |= {"session": session, "viewer": None, "site_title": "Mail-A-Patatina", "base": "/setup", "like": LIKE,
            "s": s, "oidc": bool(s.oidc_issuer)}
    return templates.TemplateResponse(request, template, ctx, status_code=status)


def owned(request: Request, session: SetupSession, feed_id: str) -> dict:
    raw = core(request).store.get(feed_id) if re.match(r"^[a-z0-9-]{1,32}$", feed_id) else None
    if raw is None or clean_email(raw.get("owner_email", "")) != session.email:
        raise HTTPException(404)
    return raw


@router.get("", include_in_schema=False)
async def no_slash():
    return RedirectResponse("/setup/", status_code=308)


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    session = current(request)
    if session is None:
        return page(request, "setup/login.html", None)
    feeds = core(request).store.owned_by(session.email)
    owned_ids = {f["id"] for f in feeds}
    family = family_feeds(request, session.email)
    return page(request, "setup/home.html", session, feeds=feeds, can_open={f.id for f in family},
                helping=[f for f in family if f.id not in owned_ids])


def family_feeds(request: Request, email: str) -> list:
    """The feeds whose album this person owns or adds photos to as a co-parent: they see those as themselves."""
    out = []
    for feed in core(request).feeds.values():
        member = feed.by_email(email)
        if member is not None and feed.is_family(member):
            out.append(feed)
    return sorted(out, key=lambda f: f.cfg.title.casefold())


@router.get("/f/{feed_id}/open")
async def open_feed(request: Request, feed_id: str):
    """Signs the owner or a co-parent in on the family's website as themselves, like a relative's email link
    would: they see the feed, and like and comment in their own name."""
    from .web import Viewer, sign_in  # the website, which includes this router

    session = require(request)
    feed = core(request).feeds.get(feed_id)
    member = feed.by_email(session.email) if feed else None
    if member is None or not feed.is_family(member):
        raise HTTPException(404)
    response = RedirectResponse(f"/{feed_id}/", status_code=303)
    sign_in(response, feed, Viewer(feed, member))
    return response


async def lacks(client: Immich, needed: list[str]) -> list[str]:
    """What a key here isn't allowed to do of `needed` (keys made before it was needed). [] if unknown."""
    try:
        perms = set((await client.api_key_info()).get("permissions", []))
    except httpx.HTTPError:
        return []
    return [] if "all" in perms else [p for p in needed if p not in perms]


# --- new feed: 1. the key, 2. the album and rules ------------------------------------


async def check_key(request: Request, key: str, needed: list[str]) -> tuple[dict | None, str | None]:
    """-> (Immich user, None) if the key has every permission in `needed` and nothing dangerous, else (None, problem)."""
    client = Immich(settings(request).immich_url, api_key=key)
    try:
        me = await client.me()
        perms = set((await client.api_key_info()).get("permissions", []))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return None, "Immich doesn't know this key. Copy it again from Immich (it is shown only once)."
        return None, f"Immich refused this key: {e}"
    finally:
        await client.aclose()
    too_much = sorted(p for p in perms if p not in needed and (
        p == "all" or p.startswith(("admin", "system", "maintenance", "server")) or p.endswith(".delete")))
    if too_much:
        return None, ("This key can do too much: " + ", ".join(too_much)
                      + ". Create one with only the permissions listed below.")
    missing = [p for p in needed if p not in perms]
    if missing:
        return None, "This key is missing: " + ", ".join(missing) + "."
    return me, None


async def check_owner_key(request: Request, key: str, email: str) -> tuple[dict | None, str | None]:
    """-> (Immich user, None) if the key may be used as an album-owner key for `email`, else (None, problem)."""
    me, problem = await check_key(request, key, OWNER_PERMISSIONS)
    if me and clean_email(me["email"]) != email:
        return None, f"This key belongs to {me['email']}, but you signed in as {email}."
    return me, problem


@router.get("/new", response_class=HTMLResponse)
async def new_key(request: Request):
    session = require(request)
    return page(request, "setup/new_key.html", session, permissions=OWNER_PERMISSIONS, immich_oauth=await immich_oauth(request))


@router.post("/new/key")
async def new_key_post(request: Request, csrf: str = Form(), api_key: str = Form()):
    session = require(request, csrf)
    _, problem = await check_owner_key(request, api_key.strip(), session.email)
    if problem:
        return page(request, "setup/new_key.html", session, 422, permissions=OWNER_PERMISSIONS, immich_oauth=await immich_oauth(request), error=problem)
    session.draft_key = api_key.strip()
    return RedirectResponse("/setup/new/album", status_code=303)


# --- the owner's key, created by signing in to Immich (IMMICH_OAUTH_CONNECT) ----------------
#
# Instead of having people create a key and tick its permissions by hand, the setup page runs Immich's own
# OAuth login for them, uses the resulting session for a few seconds to create exactly the key the feed
# needs, and signs that session out again. Only the narrow key is kept.


_oauth: tuple[float, bool] | None = None  # (when checked, whether Immich offers OAuth login)


async def immich_oauth(request: Request) -> bool:
    """IMMICH_OAUTH_CONNECT, and only while Immich actually signs people in through OAuth (checked every
    10 minutes), so nobody gets a button that can't work."""
    global _oauth
    s = settings(request)
    if not s.immich_oauth_connect:
        return False
    if _oauth is None or time.monotonic() - _oauth[0] > 600:
        client = Immich(s.immich_url)
        try:
            enabled = bool((await client.server_features()).get("oauth"))
        except httpx.HTTPError:
            enabled = _oauth[1] if _oauth else False
        finally:
            await client.aclose()
        _oauth = (time.monotonic(), enabled)
    return _oauth[1]


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


@router.post("/immich/connect")
async def immich_connect(request: Request, csrf: str = Form(), feed_id: str = Form("")):
    session = require(request, csrf)
    s = settings(request)
    if not await immich_oauth(request):
        raise HTTPException(404)
    if feed_id:
        owned(request, session, feed_id)
    for state, pending in list(IMMICH_LOGINS.items()):
        if time.monotonic() - pending[3] > LOGIN_SECONDS:
            IMMICH_LOGINS.pop(state, None)
    state, (verifier, challenge) = secrets.token_urlsafe(24), pkce()
    try:
        url = await Immich.oauth_start(s.immich_url, f"{s.public_url}/setup/immich-callback", state, challenge)
    except httpx.HTTPError as e:
        log.warning("Immich OAuth start failed: %s", e)
        return connect_failed(request, session, feed_id, "Immich didn't offer its sign-in. Create a key by hand instead.")
    IMMICH_LOGINS[state] = (verifier, session.csrf, feed_id, time.monotonic())
    return RedirectResponse(url, status_code=303)


@router.get("/immich-callback")
async def immich_callback(request: Request, state: str = "", error: str = ""):
    pending = IMMICH_LOGINS.pop(state, None)
    if pending is None or time.monotonic() - pending[3] > LOGIN_SECONDS:
        return page(request, "setup/message.html", current(request), title="That didn't work", text="Please start again.")
    verifier, binding, feed_id, _ = pending
    if feed_id.startswith("join:"):
        return await finish_join(request, feed_id[len("join:"):], binding, state, verifier, error)
    session = require(request)
    if binding != session.csrf:
        return page(request, "setup/message.html", session, title="That didn't work", text="Please start again.")
    if error:
        return connect_failed(request, session, feed_id, "The sign-in to Immich was cancelled.")
    s = settings(request)
    raw = owned(request, session, feed_id) if feed_id else None
    try:
        immich = await Immich.oauth_finish(s.immich_url, f"{s.public_url}/setup/immich-callback?{request.url.query}",
                                           state, verifier)
    except (httpx.HTTPError, LoginFailed) as e:
        log.warning("Immich OAuth callback failed: %s", e)
        return connect_failed(request, session, feed_id, "Immich didn't accept the sign-in. Create a key by hand instead.")
    try:
        me = await immich.me()
        if clean_email(me["email"]) != session.email:
            return connect_failed(request, session, feed_id,
                                  f"You signed in to Immich as {me['email']}, but to this page as {session.email}.")
        key = await immich.create_api_key(KEY_NAME, OWNER_PERMISSIONS)
        if raw:
            await delete_old_key(immich, raw["api_key"])
    finally:
        await immich.logout()
        await immich.aclose()
    log.info("Immich key for %s created through OAuth (%s)", session.email, feed_id or "new feed")
    if raw:
        raw["api_key"] = key
        core(request).store.save(raw)
        await core(request).apply_stored(feed_id)
        return back(feed_id, msg="Connected to Immich anew. The old key is removed.")
    session.draft_key = key
    return RedirectResponse("/setup/new/album", status_code=303)


async def delete_old_key(immich: Immich, old_key: str) -> None:
    """Remove the key a feed used before, if Immich still knows it (it may have been revoked already)."""
    old = Immich(immich.url, api_key=old_key)
    try:
        await immich.delete_api_key((await old.api_key_info())["id"])
    except httpx.HTTPError:
        pass
    finally:
        await old.aclose()


# --- invite links: co-parents and relatives with their own Immich account connect it themselves ----------
#
# The family sends one person a link. They sign in to Immich (no setup page, no key to create), and the key
# they need is created in their own account: a co-parent's lets their photos be marked as sent, a relative's
# lets them get emails and like and comment. The link works only for the album member it was made for.


def invitation(request: Request, token: str):
    """-> (role, the feed's settings, feed, member) for a valid invite, else None."""
    parsed = read_invite(settings(request), token)
    if parsed is None:
        return None
    role, feed_id, user_id = parsed
    raw = core(request).store.get(feed_id) if re.match(r"^[a-z0-9-]{1,32}$", feed_id) else None
    feed = core(request).feeds.get(feed_id)
    member = feed.members.get(user_id) if feed else None
    if raw is None or member is None or member.role == "owner":
        return None
    return role, raw, feed, member


def join_language(request: Request) -> str:
    return from_header(request.headers.get("accept-language")) or settings(request).default_language


def join_message(request: Request, title: str, text: str, **values) -> HTMLResponse:
    """A message on the invite pages, in the visitor's language (English text, {placeholders} from values)."""
    return page(request, "setup/message.html", None, title=title, text=text, values=values, home=False,
                lang=join_language(request))


@router.get("/join/{token}", response_class=HTMLResponse)
async def join_page(request: Request, token: str):
    invite = invitation(request, token)
    if invite is None or not await immich_oauth(request):
        return join_message(request, "This link doesn't work anymore", "Ask the family for a new one.")
    role, raw, _, member = invite
    return page(request, "setup/join.html", None, raw=raw, member=member, role=role, token=token,
                lang=join_language(request))


@router.post("/join/{token}")
async def join_start(request: Request, token: str):
    """A POST, so that link scanners in mail and chat apps can't start the sign-in."""
    s = settings(request)
    if invitation(request, token) is None or not await immich_oauth(request):
        return join_message(request, "This link doesn't work anymore", "Ask the family for a new one.")
    nonce = secrets.token_urlsafe(24)
    state, (verifier, challenge) = secrets.token_urlsafe(24), pkce()
    try:
        url = await Immich.oauth_start(s.immich_url, f"{s.public_url}/setup/immich-callback", state, challenge)
    except httpx.HTTPError as e:
        log.warning("Immich OAuth start failed: %s", e)
        return join_message(request, "Immich isn't answering", "Please try again later.")
    IMMICH_LOGINS[state] = (verifier, nonce, f"join:{token}", time.monotonic())
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(JOIN_COOKIE, nonce, max_age=LOGIN_SECONDS, path="/setup", httponly=True, samesite="lax",
                        secure=s.secure_cookies)
    return response


async def finish_join(request: Request, token: str, nonce: str, state: str, verifier: str, error: str) -> Response:
    if not secrets.compare_digest(request.cookies.get(JOIN_COOKIE, ""), nonce):
        return join_message(request, "That didn't work", "Please open the link again, in this browser.")
    invite = invitation(request, token)
    if invite is None:
        return join_message(request, "This link doesn't work anymore", "Ask the family for a new one.")
    if error:
        return join_message(request, "Sign-in cancelled", "Open the link again whenever you like.")
    role, raw, feed, member = invite
    c, s = core(request), settings(request)
    try:
        immich = await Immich.oauth_finish(s.immich_url, f"{s.public_url}/setup/immich-callback?{request.url.query}",
                                           state, verifier)
    except (httpx.HTTPError, LoginFailed) as e:
        log.warning("Immich OAuth callback failed: %s", e)
        return join_message(request, "That didn't work", "Immich didn't accept the sign-in. Please try again later.")
    try:
        me = await immich.me()
        if me["id"] != member.id:
            return join_message(request, "Wrong account",
                                "This link is for {name} ({email}), but you signed in to Immich as {other}.",
                                name=member.name, email=member.email, other=me["email"])
        key = await immich.create_api_key(KEY_NAME, CONTRIBUTOR_PERMISSIONS if role == "coparent" else RELATIVE_PERMISSIONS)
        old = contributor_keys(feed).get(member.id) if role == "coparent" else c.people.keys.get(member.email)
        if old:
            await delete_old_key(immich, old)
    finally:
        await immich.logout()
        await immich.aclose()
    log.info("%s connected to %s as %s through an invite", member.email, raw["id"], role)
    if role == "coparent":
        keys = contributor_keys(feed) | {member.id: key}
        raw["contributor_keys"] = list(keys.values())
        c.store.save(raw)
        await c.apply_stored(raw["id"])
        text = ("Photos you add to the album “{title}” are shared with the family now, once you've written a description."
                if raw.get("require_caption", True) else "Photos you add to the album “{title}” are shared with the family now.")
    else:
        save_key(s.keys_file, member.email, key)
        await c.people.reload_keys()
        text = "You'll get “{title}” by email, and can like and comment on every photo."
    if role == "coparent" and (fresh := c.feeds.get(raw["id"])) and (me_there := fresh.members.get(member.id)):
        # Just proved who they are to Immich: signed in on the family's website too, to see it as themselves.
        from .web import Viewer, sign_in

        response = page(request, "setup/message.html", None, title="Thanks, {name}!", text=text, home=False,
                        values={"name": member.name, "title": raw["title"]}, lang=join_language(request),
                        link=f"/{raw['id']}/", link_label="See the feed")
        sign_in(response, fresh, Viewer(fresh, me_there))
    else:
        response = join_message(request, "Thanks, {name}!", text, name=member.name, title=raw["title"])
    response.delete_cookie(JOIN_COOKIE, path="/setup")
    return response


def connect_failed(request: Request, session: SetupSession, feed_id: str, problem: str) -> Response:
    if feed_id:
        return back(feed_id, error=problem)
    # Only reached after an Immich sign-in was tried, so the button stays (with the manual steps opened).
    return page(request, "setup/new_key.html", session, 422, permissions=OWNER_PERMISSIONS, immich_oauth=True,
                error=problem, manual=True)


async def own_albums(request: Request, key: str) -> list[dict]:
    client = Immich(settings(request).immich_url, api_key=key)
    try:
        me = await client.me()
        albums = await client.albums()
    finally:
        await client.aclose()
    used = {f.cfg.album_id for f in core(request).feeds.values()}
    return [
        a for a in albums
        if any(u["role"] == "owner" and u["user"]["id"] == me["id"] for u in a.get("albumUsers", []))
        and a["id"] not in used
    ]


@router.get("/new/album", response_class=HTMLResponse)
async def new_album(request: Request):
    session = require(request)
    if not session.draft_key:
        return RedirectResponse("/setup/new", status_code=303)
    albums = await own_albums(request, session.draft_key)
    return page(request, "setup/new_album.html", session, albums=albums, timezones=TIMEZONES)


@router.post("/new")
async def create(
    request: Request, csrf: str = Form(), album_id: str = Form(), title: str = Form(),
    send_time: str = Form("07:00"), timezone: str = Form("Europe/Berlin"), send_mode: str = Form("digest"),
    require_caption: str = Form(""), notify_me: str = Form(""), keep_photos_on_server: str = Form(""),
):
    session = require(request, csrf)
    if not session.draft_key:
        return RedirectResponse("/setup/new", status_code=303)
    albums = {a["id"]: a for a in await own_albums(request, session.draft_key)}
    problem = rules_problem(send_time, timezone, send_mode) or (None if album_id in albums else "Pick one of your albums.")
    if problem or not title.strip():
        return page(request, "setup/new_album.html", session, 422, albums=list(albums.values()), timezones=TIMEZONES,
                    error=problem or "Give the feed a title.")
    c = core(request)
    raw = {
        "id": c.store.free_id(title, set(c.feeds)),
        "title": title.strip()[:80],
        "album_id": album_id,
        "api_key": session.draft_key,
        "owner_email": session.email,
        "created_at": now(),
        "timezone": timezone, "send_time": send_time, "send_mode": send_mode,
        "require_caption": bool(require_caption),
        "keep_photos_on_server": bool(keep_photos_on_server),
        "admin_emails": [session.email] if notify_me else [],
        "chats": [],
        "pending": [],
    }
    c.store.save(raw)
    session.draft_key = None
    await c.apply_stored(raw["id"])
    log.info("Feed %s set up by %s", raw["id"], session.email)
    return RedirectResponse(f"/setup/f/{raw['id']}", status_code=303)


def rules_problem(send_time: str, timezone: str, send_mode: str) -> str | None:
    if not TIME.match(send_time):
        return "The sending time must look like 07:00."
    if timezone not in zoneinfo.available_timezones():
        return "Pick a timezone from the list."
    if send_mode not in {"digest", "drip"}:
        return "Pick how photos are sent."
    return None


# --- one feed ----------------------------------------------------------------------


@router.get("/f/{feed_id}", response_class=HTMLResponse)
async def feed_page(request: Request, feed_id: str, msg: str = "", error: str = ""):
    session = require(request)
    raw = owned(request, session, feed_id)
    c = core(request)
    await c.people.reload_keys()  # `approve` on the CLI may have added keys since
    feed = c.feeds.get(feed_id)
    if feed is not None:
        try:
            await feed.refresh_members()  # ...and shared the album with them
        except httpx.HTTPError:
            log.exception("Reading the members of %s", feed_id)
    s = c.settings
    invites = await immich_oauth(request)
    members, waiting_coparents = [], []
    if feed is not None:
        keyed = contributor_keys(feed)
        for m in feed.others():
            if m.id in keyed:
                continue  # listed under co-parents
            if feed.is_coparent(m):
                # adds photos (or may): a co-parent still to connect
                waiting_coparents.append({"id": m.id, "name": m.name, "email": m.email,
                                          "invite": invite_url(s, feed_id, m.id, "coparent") if invites else None})
                continue
            ready = m.email in c.people.keys
            members.append({"id": m.id, "name": m.name, "email": m.email, "ready": ready, "managed": m.managed,
                            "invite": invite_url(s, feed_id, m.id, "relative") if invites and not ready and not m.managed else None})
    channels = [k for k in ("signal", "telegram") if {"signal": s.signal_url and s.signal_number,
                                                      "telegram": s.telegram_token}[k]]
    whatsapp, whatsapp_down = None, False  # the family's own linked account, with its groups
    if s.whatsapp_url and raw.get("whatsapp_device"):
        try:
            gateway = Gateway(s, c.http)
            whatsapp = await gateway.account(raw["whatsapp_device"])
            if whatsapp:
                connected = {str(ch["chat_id"]) for ch in raw.get("chats", []) if ch["channel"] == "whatsapp"}
                whatsapp["groups"] = sorted(((name or jid, jid) for name, jid in await gateway.groups(raw["whatsapp_device"])
                                             if jid not in connected), key=lambda g: g[0].casefold())
        except (httpx.HTTPError, GatewayError):
            log.exception("WhatsApp gateway for %s", feed_id)
            whatsapp_down = True
    coparents, skipped, owner_missing, outdated = [], [], [], {}
    if feed is not None:
        coparents = [feed.members.get(uid) or uid for uid in contributor_keys(feed)]
        owner_missing = await lacks(feed.curator, OWNER_PERMISSIONS)
        # Keys from before co-parents could comment: a new invite makes them one that may.
        for uid in contributor_keys(feed):
            if uid in feed.members and await lacks(feed.writers[uid], ACTIVITY_PERMISSIONS):
                outdated[uid] = invite_url(s, feed_id, uid, "coparent") if invites else None
        skipped = [feed.members[uid].name if uid in feed.members else "someone no longer in the album"
                   for uid in feed.unkeyed]
    return page(
        request, "setup/feed.html", session, raw=raw, feed=feed, members=members, channels=channels,
        coparents=coparents, skipped=skipped, coparent_permissions=CONTRIBUTOR_PERMISSIONS, immich_oauth=invites,
        owner_permissions=OWNER_PERMISSIONS, owner_missing=owner_missing, outdated=outdated,
        waiting_coparents=waiting_coparents, invite_days=INVITE_DAYS,
        relative_permissions=RELATIVE_PERMISSIONS,
        whatsapp=whatsapp, whatsapp_down=whatsapp_down,
        timezones=TIMEZONES, msg=msg, error=error, link=f"{s.public_url}/{feed_id}/",
        moments=len(feed.library.ordered) if feed else 0, bot_ready=bool(feed and feed.bot),
    )


def back(feed_id: str, msg: str = "", error: str = "") -> RedirectResponse:
    query = urlencode({k: v for k, v in (("msg", msg), ("error", error)) if v})
    return RedirectResponse(f"/setup/f/{feed_id}" + (f"?{query}" if query else ""), status_code=303)


@router.post("/f/{feed_id}/settings")
async def save_settings(
    request: Request, feed_id: str, csrf: str = Form(), title: str = Form(), send_time: str = Form(),
    timezone: str = Form(), send_mode: str = Form(), require_caption: str = Form(""), notify_me: str = Form(""),
    keep_photos_on_server: str = Form(""),
):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    problem = rules_problem(send_time, timezone, send_mode) or (None if title.strip() else "Give the feed a title.")
    if problem:
        return back(feed_id, error=problem)
    raw.update(title=title.strip()[:80], send_time=send_time, timezone=timezone, send_mode=send_mode,
               require_caption=bool(require_caption), keep_photos_on_server=bool(keep_photos_on_server),
               admin_emails=[session.email] if notify_me else [])
    core(request).store.save(raw)
    await core(request).apply_stored(feed_id)
    return back(feed_id, msg="Saved.")


@router.post("/f/{feed_id}/key")
async def replace_key(request: Request, feed_id: str, csrf: str = Form(), api_key: str = Form()):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    _, problem = await check_owner_key(request, api_key.strip(), session.email)
    if problem:
        return back(feed_id, error=problem)
    raw["api_key"] = api_key.strip()
    core(request).store.save(raw)
    await core(request).apply_stored(feed_id)
    return back(feed_id, msg="Key replaced. You can now revoke the old one in Immich.")


# --- co-parents: album members who add photos of their own ---------------------------


def contributor_keys(feed) -> dict[str, str]:
    """user id -> key of the co-parents connected to a loaded feed."""
    return {uid: c.http.headers["x-api-key"] for uid, c in feed.writers.items() if c is not feed.curator}


@router.post("/f/{feed_id}/coparents/add")
async def add_coparent(request: Request, feed_id: str, csrf: str = Form(), api_key: str = Form()):
    """The owner pastes a key the co-parent created in their own Immich account (only a photo's owner
    can record that it was sent)."""
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    feed = core(request).feeds.get(feed_id)
    if feed is None or not feed.writers:
        return back(feed_id, error="The album couldn't be read yet. Try again in a moment.")
    key = api_key.strip()
    me, problem = await check_key(request, key, CONTRIBUTOR_PERMISSIONS)
    if problem:
        return back(feed_id, error=problem)
    await feed.refresh_members()
    member = feed.members.get(me["id"])
    if member is None:
        return back(feed_id, error=f"This key belongs to {me['email']}, who isn't in the album. "
                                   "Share the album with them in Immich (as editor, so they can add photos) first.")
    if member.role == "owner":
        return back(feed_id, error="That's your own key. The co-parent creates the key in their Immich account.")
    keys = contributor_keys(feed)
    replaced = keys.pop(me["id"], None)
    raw["contributor_keys"] = [*keys.values(), key]
    core(request).store.save(raw)
    await core(request).apply_stored(feed_id)
    name = core(request).people.display_name(me["name"])
    return back(feed_id, msg=f"{name}'s key replaced. They can revoke the old one in Immich." if replaced
                else f"{name} is connected. Their photos are sent too.")


@router.post("/f/{feed_id}/coparents/remove")
async def remove_coparent(request: Request, feed_id: str, csrf: str = Form(), user_id: str = Form()):
    """Forget a co-parent's key. Their place in the Immich album is left alone; their photos just aren't sent."""
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    feed = core(request).feeds.get(feed_id)
    keys = contributor_keys(feed) if feed else {}
    if user_id not in keys:
        return back(feed_id)
    del keys[user_id]
    raw["contributor_keys"] = list(keys.values())
    core(request).store.save(raw)
    await core(request).apply_stored(feed_id)
    return back(feed_id, msg="Removed. Their photos aren't sent anymore; they can revoke the key in Immich. "
                             "Their access to the album in Immich is unchanged.")


@router.post("/f/{feed_id}/people/remove")
async def remove_person(request: Request, feed_id: str, csrf: str = Form(), user_id: str = Form()):
    """Take a relative out of the album: the album's members are who gets the emails. Their Immich account
    stays (the admin can delete it). Co-parents are removed with /coparents/remove instead."""
    session = require(request, csrf)
    owned(request, session, feed_id)
    c = core(request)
    feed = c.feeds.get(feed_id)
    if feed is None:
        return back(feed_id, error="The album couldn't be read yet. Try again in a moment.")
    member = feed.members.get(user_id)
    if member is None:
        return back(feed_id)
    if (member.role == "owner" or member.email == feed.cfg.bot_email
            or user_id in contributor_keys(feed) or user_id in feed.unkeyed):  # co-parents keep their album access
        raise HTTPException(400)
    try:
        await feed.curator.remove_album_user(feed.cfg.album_id, user_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 403:
            raise
        return back(feed_id, error="Your Immich key may not remove people (albumUser.delete is missing). "
                                   "Replace it under “Immich key”, or remove them in Immich: album → ⋮ → Options.")
    await feed.refresh_members()
    if member.email in c.people.keys and not still_needed(c, member.email) and not member.managed:
        save_key(c.settings.keys_file, member.email, None)  # their own account stays; only our key goes
        await c.people.reload_keys()
    elif member.email in c.people.keys and not still_needed(c, member.email):
        # Their account and key are of no use to anyone now; deleting accounts needs the admin.
        await notify_install_admins(
            request, f"{member.name} <{member.email}> is in no family's album anymore",
            f"docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina forget {member.email}")
    return back(feed_id, msg=f"{member.name} removed. Nothing more is sent to them, and their links stop working.")


def still_needed(c, email: str) -> bool:
    """Is this person in another family's album, or waiting to be added to one? (From what the app knows;
    `forget` checks every album in Immich before deleting anything.)"""
    return (any(f.by_email(email) for f in c.feeds.values())
            or any(p["email"] == email for raw in c.store.all().values() for p in raw.get("pending", [])))


async def find_signal_group(request: Request, name: str) -> tuple[str | None, str | None]:
    """Look up the chat id of the one group with exactly this name. Other groups the installation's
    Signal number is in are never shown, because they belong to other families."""
    s = settings(request)
    wanted = name.strip().casefold()
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(f"{s.signal_url}/v1/groups/{s.signal_number}")
        r.raise_for_status()
        groups = [(g.get("name", ""), g.get("id")) for g in r.json()]
    matches = [gid for gname, gid in groups if gname.strip().casefold() == wanted and gid]
    if not matches:
        return None, "No group with exactly that name. Is the family number already a member of the group?"
    if len(matches) > 1:
        return None, "Several groups have that name. Rename yours to something unique and try again."
    return matches[0], None


@router.post("/f/{feed_id}/chats/add")
async def add_chat(request: Request, feed_id: str, csrf: str = Form(), channel: str = Form(),
                   group_name: str = Form(""), chat_id: str = Form("")):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    if channel not in CHAT_KINDS:
        return back(feed_id, error="Pick a messenger.")
    name = (group_name or chat_id).strip()
    if channel == "telegram":
        address = chat_id.strip()
        if not re.match(r"^-?\d{5,20}$", address):
            return back(feed_id, error="A Telegram chat id is a number like -1001234567890.")
    elif channel == "whatsapp":
        # Only a group of the family's own linked account can be picked.
        if not raw.get("whatsapp_device"):
            return back(feed_id, error="Link your family's WhatsApp account first.")
        try:
            groups = dict((jid, gname) for gname, jid in await Gateway(settings(request), core(request).http)
                          .groups(raw["whatsapp_device"]))
        except (httpx.HTTPError, GatewayError):
            return back(feed_id, error="The WhatsApp gateway isn't reachable right now. Try again later.")
        address = chat_id.strip()
        if address not in groups:
            return back(feed_id, error="Pick one of your WhatsApp groups.")
        name = groups[address] or address
    else:
        try:
            address, problem = await find_signal_group(request, group_name)
        except httpx.HTTPError:
            return back(feed_id, error="The Signal gateway isn't reachable right now. Try again later.")
        if problem:
            return back(feed_id, error=problem)
    chats = raw.setdefault("chats", [])
    if any(c["channel"] == channel and str(c["chat_id"]) == address for c in chats):
        return back(feed_id, msg="That group is already connected.")
    chats.append({"name": name[:80], "channel": channel, "chat_id": address})
    feed = core(request).feeds.get(feed_id)
    if not (feed and feed.bot) and not raw.get("bot_requested"):
        raw["bot_requested"] = now()  # group visitors comment through a bot account; the admin creates it
        await notify_install_admins(request, f"{raw['title']}: a group-chat account is waiting for approval")
    core(request).store.save(raw)
    await core(request).apply_stored(feed_id)
    return back(feed_id, msg="Group connected. New moments will be posted there too.")


@router.post("/f/{feed_id}/chats/remove")
async def remove_chat(request: Request, feed_id: str, csrf: str = Form(), chat_id: str = Form()):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    raw["chats"] = [c for c in raw.get("chats", []) if str(c["chat_id"]) != chat_id]
    core(request).store.save(raw)
    await core(request).apply_stored(feed_id)
    return back(feed_id, msg="Group disconnected.")


# --- the family's own WhatsApp account ------------------------------------------------


async def whatsapp_device(request: Request, raw: dict) -> str:
    """The family's device on the gateway (one per family, named after the feed), created if needed."""
    if not settings(request).whatsapp_url:
        raise HTTPException(404)
    device = raw["id"]
    await Gateway(settings(request), core(request).http).ensure_device(device)
    if raw.get("whatsapp_device") != device:
        raw["whatsapp_device"] = device
        core(request).store.save(raw)
        await core(request).apply_stored(raw["id"])
    return device


@router.post("/f/{feed_id}/whatsapp/link")
async def whatsapp_link(request: Request, feed_id: str, csrf: str = Form()):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    try:
        await whatsapp_device(request, raw)
    except (httpx.HTTPError, GatewayError):
        log.exception("WhatsApp gateway for %s", feed_id)
        return back(feed_id, error="The WhatsApp gateway isn't reachable right now. Try again later.")
    return RedirectResponse(f"/setup/f/{feed_id}/whatsapp", status_code=303)


@router.get("/f/{feed_id}/whatsapp", response_class=HTMLResponse)
async def whatsapp_page(request: Request, feed_id: str):
    """Shows a QR code (or a pairing code) until the family's phone has linked the account. The page reloads
    itself every few seconds; a new QR code is fetched only when the old one has expired."""
    session = require(request)
    raw = owned(request, session, feed_id)
    device = raw.get("whatsapp_device")
    if not device:
        return back(feed_id)
    gateway = Gateway(settings(request), core(request).http)
    try:
        account = await gateway.account(device)
        if account:
            LINKING.pop(device, None)
            return back(feed_id, msg=f"WhatsApp linked: {account['name'] or account['phone'] or 'done'}.")
        kind, value, expires = LINKING.get(device, ("", "", 0.0))
        if time.monotonic() > expires:
            png, seconds = await gateway.qr(device)
            kind, value = "qr", "data:image/png;base64," + base64.b64encode(png).decode()
            LINKING[device] = (kind, value, time.monotonic() + max(seconds - 3, 5))
    except (httpx.HTTPError, GatewayError) as e:
        log.exception("WhatsApp linking for %s", feed_id)
        return back(feed_id, error=f"Linking WhatsApp didn't work: {e}")
    return page(request, "setup/whatsapp.html", session, raw=raw, kind=kind, value=value)


@router.post("/f/{feed_id}/whatsapp/code")
async def whatsapp_code(request: Request, feed_id: str, csrf: str = Form(), phone: str = Form()):
    """For linking on the very phone that shows this page, where there is nothing to scan."""
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    if not PHONE.match(phone.strip()):
        return back(feed_id, error="Enter the phone number with country code, like +49 170 1234567.")
    try:
        device = await whatsapp_device(request, raw)
        code = await Gateway(settings(request), core(request).http).pair_code(device, re.sub(r"\D", "", phone))
    except (httpx.HTTPError, GatewayError) as e:
        log.exception("WhatsApp pairing code for %s", feed_id)
        return back(feed_id, error=f"Getting a pairing code didn't work: {e}")
    LINKING[device] = ("code", code, time.monotonic() + 160)
    return RedirectResponse(f"/setup/f/{feed_id}/whatsapp", status_code=303)


@router.post("/f/{feed_id}/whatsapp/unlink")
async def whatsapp_unlink(request: Request, feed_id: str, csrf: str = Form()):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    device = raw.pop("whatsapp_device", None)
    if device:
        LINKING.pop(device, None)
        try:
            await Gateway(settings(request), core(request).http).remove(device)
        except (httpx.HTTPError, GatewayError):
            log.exception("Removing WhatsApp device of %s", feed_id)
            return back(feed_id, error="The WhatsApp gateway isn't reachable right now. Try again later.")
    raw["chats"] = [c for c in raw.get("chats", []) if c["channel"] != "whatsapp"]
    core(request).store.save(raw)
    await core(request).apply_stored(feed_id)
    return back(feed_id, msg="WhatsApp unlinked, and its groups disconnected. "
                             "It also disappears from Linked devices on the phone.")


# --- relatives -------------------------------------------------------------------------


@router.post("/f/{feed_id}/relatives/add")
async def add_relative(request: Request, feed_id: str, csrf: str = Form(), name: str = Form(), email: str = Form()):
    session = require(request, csrf)
    return await add_person(request, owned(request, session, feed_id), feed_id, name, email)


@router.post("/f/{feed_id}/requests/accept")
async def accept_request(request: Request, feed_id: str, csrf: str = Form(), email: str = Form()):
    """Someone who opened a relative's link asked for emails of their own ("Not you?" on the pages)."""
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    wanted = next((r for r in raw.get("join_requests", []) if r["email"] == email), None)
    if wanted is None:
        return back(feed_id)
    raw["join_requests"] = [r for r in raw["join_requests"] if r["email"] != email]
    core(request).store.save(raw)
    return await add_person(request, raw, feed_id, wanted["name"], wanted["email"])


@router.post("/f/{feed_id}/requests/decline")
async def decline_request(request: Request, feed_id: str, csrf: str = Form(), email: str = Form()):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    raw["join_requests"] = [r for r in raw.get("join_requests", []) if r["email"] != email]
    core(request).store.save(raw)
    return back(feed_id, msg="Request declined.")


async def add_person(request: Request, raw: dict, feed_id: str, name: str, email: str) -> Response:
    email, name = clean_email(email), name.strip()[:60]
    if not name or not EMAIL.match(email):
        return back(feed_id, error="Enter a name and a valid email address.")
    feed = core(request).feeds.get(feed_id)
    if feed and feed.by_email(email):
        return back(feed_id, msg=f"{email} is already part of this album.")
    if feed:
        # Someone with an Immich account of their own needs no new account and so no approval: share the
        # album with it. (Only works if Immich shows other users to non-admins; else `approve` does it.)
        try:
            user = next((u for u in await feed.curator.users() if clean_email(u["email"]) == email), None)
        except httpx.HTTPError:
            user = None
        if user and not core(request).people.is_managed(user["name"]):
            try:
                await feed.curator.add_album_user(feed.cfg.album_id, user["id"], "viewer")
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 400:  # 400: already shared
                    return back(feed_id, error=f"Sharing the album with {email} didn't work: {e}")
            await feed.refresh_members()
            return back(feed_id, msg=f"{user['name']} has an Immich account already, so the album is now shared with it. "
                                     "For emails and reactions, add a key from their account under People.")
    pending = raw.setdefault("pending", [])
    if any(p["email"] == email for p in pending):
        return back(feed_id, msg=f"{email} is already waiting for approval.")
    pending.append({"name": name, "email": email, "requested_at": now()})
    core(request).store.save(raw)
    await notify_install_admins(request, f"{raw['title']}: {name} <{email}> is waiting for approval")
    return back(feed_id, msg=f"{name} is waiting for the admin's approval. You'll see them here once they're added.")


@router.post("/f/{feed_id}/relatives/key")
async def relative_key(request: Request, feed_id: str, csrf: str = Form(), user_id: str = Form(), api_key: str = Form()):
    """For relatives with an Immich account of their own: a key they created in it, so Mail-A-Patatina can
    email them and post their likes and comments, without taking over the account."""
    session = require(request, csrf)
    owned(request, session, feed_id)
    c = core(request)
    feed = c.feeds.get(feed_id)
    member = feed.members.get(user_id) if feed else None
    if member is None or member.role == "owner" or member.email == feed.cfg.bot_email:
        return back(feed_id)
    me, problem = await check_key(request, api_key.strip(), RELATIVE_PERMISSIONS)
    if problem:
        return back(feed_id, error=problem)
    if me["id"] != user_id:
        return back(feed_id, error=f"This key belongs to {me['email']}, not {member.email}.")
    save_key(c.settings.keys_file, member.email, api_key.strip())
    await c.people.reload_keys()
    return back(feed_id, msg=f"{member.name} is ready: they get the daily email and can like and comment.")


@router.post("/f/{feed_id}/relatives/cancel")
async def cancel_relative(request: Request, feed_id: str, csrf: str = Form(), email: str = Form()):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    raw["pending"] = [p for p in raw.get("pending", []) if p["email"] != email]
    core(request).store.save(raw)
    return back(feed_id, msg="Request withdrawn.")


@router.post("/f/{feed_id}/test")
async def test_email(request: Request, feed_id: str, csrf: str = Form()):
    session = require(request, csrf)
    owned(request, session, feed_id)
    feed = core(request).feeds.get(feed_id)
    entries = feed.library.ordered[-3:] if feed else []
    if not entries:
        return back(feed_id, error="Nothing to send yet: write a description on a photo in the album first.")
    owner = next((m for m in feed.members.values() if m.role == "owner"), None)
    s = settings(request)
    token = make_token(s, feed.id, "u", owner.id) if owner else ""
    target = dispatch.Target("test", "email", session.name, session.email, token, None)
    try:
        await dispatch.send_email(feed, target, entries, record=False)
    except Exception as e:
        log.exception("Test email for %s failed", feed_id)
        return back(feed_id, error=f"Sending failed: {e}")
    return back(feed_id, msg=f"Test email with {len(entries)} moment(s) sent to {session.email}.")


@router.post("/f/{feed_id}/delete")
async def delete_feed(request: Request, feed_id: str, csrf: str = Form(), confirm: str = Form("")):
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    if confirm.strip() != raw["id"]:
        return back(feed_id, error=f"Type {raw['id']} to confirm.")
    if raw.get("whatsapp_device"):
        try:
            await Gateway(settings(request), core(request).http).remove(raw["whatsapp_device"])
        except (httpx.HTTPError, GatewayError):
            log.exception("Removing WhatsApp device of %s", feed_id)
    core(request).store.delete(feed_id)
    await core(request).apply_stored(feed_id)
    log.info("Feed %s deleted by %s", feed_id, session.email)
    return page(request, "setup/message.html", session, title="Feed deleted",
                text="Nothing more will be sent. Revoke its API key in Immich (Account settings → API keys); "
                     "the photos, likes and comments stay in your album.")


APPROVE = "docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina approve"


async def notify_install_admins(request: Request, subject: str, command: str = APPROVE) -> None:
    s = settings(request)
    if not (s.install_admin_emails and s.smtp_host):
        return
    body = f"{subject}.\n\nOn the server, run:\n  {command}\n"
    try:
        await mailer.send(s, mailer.build_simple(s.smtp_from, s.install_admin_emails, f"Mail-A-Patatina: {subject}", body))
    except Exception:
        log.exception("Could not notify the installation admin")
