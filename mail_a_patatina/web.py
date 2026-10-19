"""The viewer website: magic-link login, feed, calendar, and one page per moment.
Likes and comments are read from and written to Immich, as the signed-in relative.

Every page lives under /<feed id>/, its sign-in cookie is limited to that path, and every request is
checked against that feed only, so families sharing one installation never see each other."""

from __future__ import annotations

import calendar
import datetime as dt
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import mailer
from .config import ChatSpec, Settings, clean_email
from .core import Core, Feed
from .i18n import from_header, install
from .i18n import t as translate  # `t` is the magic-link parameter here
from .immich import LoginFailed
from .library import Entry, now
from .links import LIKE, make_session, make_token, read_photo_ticket, read_session, read_token
from .people import Member
from .setup import ASSET_VERSION, EMAIL
from .setup import router as setup_router

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

HERE = Path(__file__).parent
TOKEN_COOKIE, GUEST_NAME_COOKIE = "map_t", "map_n"
COOKIE_AGE = 400 * 24 * 3600  # the guest-name cookie; the sign-in cookie lives SESSION_DAYS
RENEW_SECONDS = 600  # a fresh link is emailed to the same person at most this often
JOIN_REQUESTS_MAX = 20  # open "Not you?" requests per family
PAGE_SIZE = 20
GUEST_COMMENT = re.compile(r"^\[(?P<name>[^\]\n]{1,60})\] (?P<body>.*)$", re.S)

core: Core  # set in lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global core
    s = Settings.from_env()
    core = Core(s)
    app.state.core = core
    if s.admin_api_key:
        log.warning("IMMICH_ADMIN_API_KEY is set, but only the setup commands need it. Remove it from .env and "
                    "pass it just for those commands: docker compose run --rm -e IMMICH_ADMIN_API_KEY=… app python -m …")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            v = (await http.get(f"{s.immich_url}/api/server/version")).json()
        log.info("Connected to Immich %s.%s.%s, serving feeds: %s", v["major"], v["minor"], v["patch"],
                 ", ".join(core.feeds) or "none yet (set them up at /setup/)")
    except Exception:
        log.exception("Immich not reachable yet")
    await core.sync_all()
    core.scheduler = AsyncIOScheduler()
    for feed in core.feeds.values():
        core.schedule(feed)
    core.scheduler.start()
    yield
    core.scheduler.shutdown(wait=False)
    await core.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(setup_router)  # before the /{feed_id} routes, so /setup is never taken for a feed
templates = Jinja2Templates(directory=HERE / "templates")
install(templates.env)
templates.env.globals["asset_version"] = ASSET_VERSION


# --- which feed, and who is looking ------------------------------------------


def get_feed(feed_id: str) -> Feed:
    feed = core.feeds.get(feed_id)
    if feed is None:
        raise HTTPException(404)
    return feed


@dataclass
class Viewer:
    feed: Feed
    # The Immich account likes and comments are posted as: the person themselves, or the feed's bot
    # account for visitors from a group chat. None if group-chat posting isn't set up.
    account: Member | None
    chat: ChatSpec | None = None  # set for group-chat visitors
    guest_name: str | None = None

    @property
    def is_guest(self) -> bool:
        return self.chat is not None

    @property
    def name(self) -> str | None:
        return self.guest_name if self.is_guest else self.account.name

    @property
    def needs_name(self) -> bool:
        return self.is_guest and not self.guest_name


def viewer_for(feed: Feed, kind: str, ident: str, request: Request | None = None) -> Viewer | None:
    """Who a link or sign-in cookie stands for, as long as they're still part of the album."""
    if kind == "u":
        member = feed.members.get(ident)  # removed from the album in Immich = link stops working
        if member is None or member.email == feed.cfg.bot_email:
            return None
        return Viewer(feed, member)
    chat = next((c for c in feed.cfg.chats if c.ref == ident), None)
    if chat is None:
        return None
    name = unquote(request.cookies.get(GUEST_NAME_COOKIE, "")).strip() if request else ""
    return Viewer(feed, feed.bot, chat, name or None)


def link_viewer(feed: Feed, token: str | None) -> tuple[Viewer, bool] | None:
    """-> (who a magic link stands for, whether it has expired), or None if it isn't a genuine link of this feed."""
    link = read_token(core.settings, token) if token else None
    if link is None or link.feed_id != feed.id:  # forged, or a genuine link for another family
        return None
    viewer = viewer_for(feed, link.kind, link.ident)
    return (viewer, link.expired) if viewer else None


def viewer_for_session(feed: Feed, request: Request) -> Viewer | None:
    session = read_session(core.settings, request.cookies.get(TOKEN_COOKIE) or "")
    if session is None or session.feed_id != feed.id:
        return None
    return viewer_for(feed, session.kind, session.ident, request)


def require_viewer(request: Request, feed_id: str) -> Viewer:
    viewer = viewer_for_session(get_feed(feed_id), request)
    if viewer is None:
        raise HTTPException(403)
    return viewer


def set_cookie(response: Response, feed: Feed, name: str, value: str) -> None:
    response.set_cookie(
        name, value, max_age=COOKIE_AGE, path=f"/{feed.id}", httponly=True, samesite="lax",
        secure=core.settings.secure_cookies,
    )


def sign_in(response: Response, feed: Feed, viewer: Viewer) -> None:
    kind, ident = ("c", viewer.chat.ref) if viewer.is_guest else ("u", viewer.account.id)
    response.set_cookie(
        TOKEN_COOKIE, make_session(core.settings, feed.id, kind, ident), max_age=core.settings.session_days * 86400,
        path=f"/{feed.id}", httponly=True, samesite="lax", secure=core.settings.secure_cookies,
    )


@app.middleware("http")
async def magic_link(request: Request, call_next):
    """`?t=<token>` on any page signs the visitor in, then redirects to the same URL without the token,
    so a link copied from the address bar never carries someone's identity. An expired link signs nobody in;
    it offers to email its owner a fresh one instead."""
    token = request.query_params.get("t")
    parts = request.url.path.strip("/").split("/")
    feed = core.feeds.get(parts[0])
    if feed is None or request.method != "GET" or (len(parts) > 1 and parts[1] == "unsubscribe"):
        return await call_next(request)
    if not token:
        response = await call_next(request)
        # Keep a browser that keeps visiting signed in: renew its cookie at most once a day.
        session = read_session(core.settings, request.cookies.get(TOKEN_COOKIE) or "")
        if (session and session.feed_id == feed.id and session.expires < time.time() + (core.settings.session_days - 1) * 86400
                and (viewer := viewer_for_session(feed, request))):
            sign_in(response, feed, viewer)
        return response
    found = link_viewer(feed, token)
    if found is None:
        return message_page(request, "This link is no longer valid", "Ask the family for a fresh one.", 403)
    new, expired = found
    params = {k: v for k, v in request.query_params.items() if k != "t"}
    target = request.url.path + (f"?{urlencode(params)}" if params else "")
    current = viewer_for_session(feed, request)
    # Keep a personal identity when the same person also opens a group-chat link.
    keep_current = current is not None and (new.is_guest or not current.is_guest)
    if expired and not keep_current:
        return render(request, "expired.html", None, 403, feed=feed, person=new.account if not new.is_guest else None,
                      chat=new.chat, token=token, next=request.url.path, masked=_masked(new),
                      link_days=core.settings.link_days)
    response = RedirectResponse(target, status_code=303)
    if not expired and not (keep_current and new.is_guest):
        sign_in(response, feed, new)
    return response


def _masked(viewer: Viewer) -> str:
    """o***@example.com: enough for the right person to recognise their address, little for anyone else."""
    if viewer.is_guest or not viewer.account.email:
        return ""
    local, _, domain = viewer.account.email.partition("@")
    return f"{local[:1]}***@{domain}"


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    return message_page(request, "Private family album", "Open the link from your email or chat to get in.", 403)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return message_page(request, "Not found", "This moment isn't shared (anymore).", 404)


@app.exception_handler(LoginFailed)
async def login_failed(request: Request, exc):
    return message_page(
        request, "Can't post right now", "Your Immich account isn't set up for this yet. The family has been told.", 503
    )


@app.exception_handler(httpx.HTTPError)
async def immich_unavailable(request: Request, exc):
    log.warning("Immich request failed: %s", exc)
    return message_page(request, "Back in a moment", "The photo library is not reachable right now.", 502)


def language(request: Request, viewer: Viewer | None = None) -> str:
    """The visitor's browser language. A relative's is remembered, so their emails can be in it too."""
    lang = from_header(request.headers.get("accept-language"))
    if lang and viewer is not None and viewer.account is not None and not viewer.is_guest:
        core.languages.remember(viewer.account.id, lang)
    return lang or core.settings.default_language


def render(request: Request, template: str, viewer: Viewer | None, status: int = 200, **ctx) -> HTMLResponse:
    feed = viewer.feed if viewer else ctx.pop("feed", None)
    ctx |= {
        "lang": language(request, viewer),
        "viewer": viewer, "like": LIKE,
        "site_title": feed.cfg.title if feed else "Mail-A-Patatina",
        "base": f"/{feed.id}" if feed else "",
        # A family that keeps its photos on the server also doesn't want them saved from the pages with one tap.
        "protect": bool(feed and feed.cfg.keep_photos_on_server),
    }
    return templates.TemplateResponse(request, template, ctx, status_code=status)


def message_page(request: Request, title: str, text: str, status: int = 200, feed: Feed | None = None,
                 **values) -> HTMLResponse:
    """A page with a title and a line of text, both translated (English text, with {placeholders} from values)."""
    return render(request, "message.html", None, status, feed=feed, title=title, text=text, values=values)


def shared_entry(feed: Feed, asset_id: str) -> Entry:
    entry = feed.library.get(asset_id)  # only this feed's album counts
    if entry is None:
        raise HTTPException(404)
    return entry


# --- likes and comments, straight from Immich ---------------------------------


@dataclass
class Activity:
    likes: list[str]  # names
    my_like_id: str | None
    comments: list[dict]


def _for_asset(feed: Feed, activities: list[dict], asset_id: str, viewer: Viewer | None) -> Activity:
    bot, people = feed.bot, core.people
    likes, my_like, comments = [], None, []
    for a in activities:
        if a.get("assetId") != asset_id:
            continue
        user = a["user"]
        mine = viewer is not None and viewer.account is not None and user["id"] == viewer.account.id
        if a["type"] == "like":
            likes.append(people.display_name(user["name"]))
            if mine:
                my_like = a["id"]
            continue
        name, body = people.display_name(user["name"]), a.get("comment") or ""
        if bot and user["id"] == bot.id and (m := GUEST_COMMENT.match(body)):
            name, body = m["name"], m["body"]
        # Guests share the bot account, so they can't delete each other's comments.
        comments.append({"id": a["id"], "name": name, "body": body, "mine": mine and not viewer.is_guest})
    return Activity(likes, my_like, comments)


def require_account(viewer: Viewer) -> Member:
    if viewer.account is None:
        raise HTTPException(403)
    return viewer.account


# --- pages ------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    raise HTTPException(403)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/{feed_id}", include_in_schema=False)
async def feed_without_slash(feed_id: str):
    get_feed(feed_id)
    return RedirectResponse(f"/{feed_id}/", status_code=308)


@app.get("/{feed_id}/", response_class=HTMLResponse)
async def feed_page(request: Request, feed_id: str, before: str | None = None):
    viewer = require_viewer(request, feed_id)
    feed = viewer.feed
    page = feed.library.newest_before(before, PAGE_SIZE + 1)
    more = len(page) > PAGE_SIZE
    page = page[:PAGE_SIZE]
    activities = await feed.activities()
    cards = [(e, _for_asset(feed, activities, e.asset_id, viewer)) for e in page]
    return render(request, "feed.html", viewer, cards=cards, older=page[-1].asset_id if more else None)


@app.get("/{feed_id}/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, feed_id: str, m: str | None = None):
    viewer = require_viewer(request, feed_id)
    try:
        month = dt.date.fromisoformat(f"{m}-01") if m else dt.date.today().replace(day=1)
    except ValueError:
        raise HTTPException(404) from None
    prev_month = (month - dt.timedelta(days=1)).replace(day=1)
    next_month = (month + dt.timedelta(days=32)).replace(day=1)
    return render(
        request, "calendar.html", viewer, month=month, weeks=calendar.Calendar().monthdatescalendar(month.year, month.month),
        by_day=viewer.feed.library.month(f"{month:%Y-%m}"), prev_month=f"{prev_month:%Y-%m}",
        next_month=f"{next_month:%Y-%m}",
    )


@app.get("/{feed_id}/day/{day}", response_class=HTMLResponse)
async def day_page(request: Request, feed_id: str, day: str):
    viewer = require_viewer(request, feed_id)
    feed = viewer.feed
    try:
        date = dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404) from None
    entries = feed.library.day(date.isoformat())
    if not entries:
        raise HTTPException(404)
    activities = await feed.activities()
    cards = [(e, _for_asset(feed, activities, e.asset_id, viewer)) for e in entries]
    prev_day, next_day = feed.library.days_around(date.isoformat())
    return render(request, "feed.html", viewer, cards=cards, day=date, prev_day=prev_day, next_day=next_day)


@app.get("/{feed_id}/e/{asset_id}", response_class=HTMLResponse)
async def entry_page(request: Request, feed_id: str, asset_id: str, like: str | None = None):
    viewer = require_viewer(request, feed_id)
    feed = viewer.feed
    entry = shared_entry(feed, asset_id)
    newer, older = feed.library.neighbours(entry)
    activity = _for_asset(feed, await feed.activities(), asset_id, viewer)
    can_post = viewer.account is not None
    return render(
        request, "entry.html", viewer, entry=entry, activity=activity, newer=newer, older=older, can_post=can_post,
        auto_like=bool(like) and can_post and activity.my_like_id is None,
    )


@app.post("/{feed_id}/e/{asset_id}/like")
async def toggle_like(request: Request, feed_id: str, asset_id: str, mode: str = Form("toggle")):
    viewer = require_viewer(request, feed_id)
    feed, account = viewer.feed, require_account(viewer)
    shared_entry(feed, asset_id)
    album = feed.cfg.album_id
    current = _for_asset(feed, await feed.curator.activities(album), asset_id, viewer).my_like_id
    if current is None:
        await core.people.run_as(account, lambda c: c.add_activity(album, asset_id, "like"))
    elif mode == "toggle" and not viewer.is_guest:  # a group's shared like isn't one visitor's to take back
        await core.people.run_as(account, lambda c: c.delete_activity(current))
    feed.activities_changed()
    return RedirectResponse(f"/{feed.id}/e/{asset_id}#reactions", status_code=303)


@app.post("/{feed_id}/e/{asset_id}/comment")
async def comment(
    request: Request, background: BackgroundTasks, feed_id: str, asset_id: str, body: str = Form(),
    name: str = Form(""),
):
    viewer = require_viewer(request, feed_id)
    feed, account = viewer.feed, require_account(viewer)
    entry = shared_entry(feed, asset_id)
    body = body.strip()[:2000]
    response = RedirectResponse(f"/{feed.id}/e/{asset_id}#comments", status_code=303)
    if viewer.needs_name and name.strip():
        viewer.guest_name = name.strip().replace("]", ")")[:60]
        set_cookie(response, feed, GUEST_NAME_COOKIE, quote(viewer.guest_name))
    if not body:
        return response
    text = f"[{viewer.guest_name or 'Guest'}] {body}" if viewer.is_guest else body
    await core.people.run_as(account, lambda c: c.add_activity(feed.cfg.album_id, asset_id, "comment", text))
    feed.activities_changed()
    who = viewer.name or "Someone"
    background.add_task(
        feed.notify_admins,
        f"{feed.cfg.title}: {who} commented",
        f"{who} on “{entry.caption}”:\n\n{body}\n\n{core.settings.public_url}/{feed.id}/e/{asset_id}",
    )
    return response


@app.post("/{feed_id}/comments/{activity_id}/delete")
async def delete_comment(request: Request, feed_id: str, activity_id: str):
    viewer = require_viewer(request, feed_id)
    feed, account = viewer.feed, require_account(viewer)
    if viewer.is_guest:
        raise HTTPException(403)
    activity = next((a for a in await feed.curator.activities(feed.cfg.album_id) if a["id"] == activity_id), None)
    if activity is None or activity["user"]["id"] != account.id:
        raise HTTPException(404)
    await core.people.run_as(account, lambda c: c.delete_activity(activity_id))
    feed.activities_changed()
    return RedirectResponse(f"/{feed.id}/e/{activity['assetId']}#comments", status_code=303)


# --- media (proxied, so Immich itself never has to be reachable from outside) ---


@app.get("/{feed_id}/media/{asset_id}/{size}")
async def media(request: Request, feed_id: str, asset_id: str, size: str):
    feed = require_viewer(request, feed_id).feed
    shared_entry(feed, asset_id)
    if size not in ("thumb", "preview"):
        raise HTTPException(404)
    data = await core.media.jpeg(feed.curator, asset_id, size)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/{feed_id}/p/{asset_id}/{ticket}.jpg")
async def emailed_photo(feed_id: str, asset_id: str, ticket: str):
    """A photo in the email of a family that keeps its photos on the server. Checked on every load: the
    ticket is genuine and not expired, its recipient is still in the album, the photo is still shared."""
    feed = get_feed(feed_id)
    recipient = read_photo_ticket(core.settings, feed.id, asset_id, ticket)
    if recipient is None or viewer_for(feed, *recipient) is None:
        raise HTTPException(404)
    shared_entry(feed, asset_id)
    data = await core.media.jpeg(feed.curator, asset_id, "preview")
    return Response(data, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, noimageindex", "Referrer-Policy": "no-referrer"})


@app.get("/{feed_id}/video/{asset_id}")
async def video(request: Request, feed_id: str, asset_id: str):
    feed = require_viewer(request, feed_id).feed
    entry = shared_entry(feed, asset_id)
    # A video, or the motion part of a live photo (a hidden asset, so never looked up by its own id).
    video_id = entry.asset_id if entry.type == "VIDEO" else entry.live_video_id
    if not video_id:
        raise HTTPException(404)
    upstream = await feed.curator.open_video(video_id, request.headers.get("range"))
    keep = ("content-type", "content-length", "content-range", "accept-ranges")
    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() in keep},
        background=BackgroundTask(upstream.aclose),
    )


# --- unsubscribe (one-click capable, RFC 8058) — stored as the member's Immich email preference ---


def _member_for_unsubscribe(feed_id: str, token: str) -> tuple[Feed, Member]:
    feed = get_feed(feed_id)
    found = link_viewer(feed, token)  # unsubscribing works with expired links too
    if found is None or found[0].is_guest:
        raise HTTPException(404)
    return feed, found[0].account


@app.get("/{feed_id}/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_confirm(request: Request, feed_id: str, t: str = ""):
    feed, member = _member_for_unsubscribe(feed_id, t)
    return render(request, "unsubscribe.html", None, feed=feed, email=member.email, token=t)


@app.post("/{feed_id}/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(request: Request, feed_id: str, t: str = ""):
    feed, member = _member_for_unsubscribe(feed_id, t)
    await core.people.run_as(member, lambda c: c.update_preferences({"emailNotifications": {"enabled": False}}))
    return message_page(request, "Unsubscribed", "You won't get these emails anymore. Your links still open the album.",
                        feed=feed)


# --- expired links and "Not you?" ---------------------------------------------

RENEWED: dict[str, float] = {}  # member id -> when they were last emailed a fresh link


@app.post("/{feed_id}/renew", response_class=HTMLResponse)
async def renew(request: Request, feed_id: str, t: str = Form(), next: str = Form("")):
    """Emails a fresh link for an expired one, only ever to the address of the person it belonged to.
    Someone a forwarded email reached gets nothing out of it."""
    feed = get_feed(feed_id)
    found = link_viewer(feed, t)
    if found is None or found[0].is_guest:
        raise HTTPException(404)
    member = found[0].account
    if not core.settings.smtp_host or not member.email:
        return message_page(request, "Can't send email", "Ask the family for a fresh link.", 503)
    if time.monotonic() - RENEWED.get(member.id, float("-inf")) >= RENEW_SECONDS:
        RENEWED[member.id] = time.monotonic()
        path = next if next.startswith(f"/{feed.id}/") and "//" not in next and "\\" not in next else f"/{feed.id}/"
        link = f"{core.settings.public_url}{path}?{urlencode({'t': make_token(core.settings, feed.id, 'u', member.id)})}"
        # Whoever pressed the button may not be the owner, so their remembered language comes first.
        lang = core.languages.get(member.id) or language(request)
        body = translate(lang, "Hello {name},\n\nhere is your new link to {title}:\n\n{link}\n\n"
                               "It signs you in and works for {days} days, so please don't forward it.\n"
                               "If you didn't ask for it, you can ignore this email: nobody else got this link.",
                         name=member.name, title=feed.cfg.title, link=link, days=core.settings.link_days)
        try:
            await mailer.send(core.settings, mailer.build_simple(
                feed.cfg.smtp_from, [member.email], translate(lang, "{title}: your new link", title=feed.cfg.title), body))
        except Exception:
            log.exception("Could not email a fresh link for feed %s", feed.id)
            RENEWED.pop(member.id, None)
            return message_page(request, "Can't send email right now", "Try again in a while.", 503)
    return message_page(request, "Check your email", "A new link is on its way to {address}.", feed=feed,
                        address=_masked(found[0]))


@app.get("/{feed_id}/not-me", response_class=HTMLResponse)
async def not_me_page(request: Request, feed_id: str):
    viewer = require_viewer(request, feed_id)
    if viewer.is_guest:
        raise HTTPException(404)
    return render(request, "not_me.html", viewer)


@app.post("/{feed_id}/not-me", response_class=HTMLResponse)
async def not_me(request: Request, background: BackgroundTasks, feed_id: str, name: str = Form(""), email: str = Form("")):
    """Someone opened another person's link, e.g. from a forwarded email: sign them out, and pass their
    request for their own emails to the family, who decide on the setup page."""
    viewer = require_viewer(request, feed_id)
    if viewer.is_guest:
        raise HTTPException(404)
    feed = viewer.feed
    name, email = name.strip()[:60], clean_email(email)
    response = message_page(request, "Signed out", "You're no longer signed in as someone else on this device.",
                            feed=feed)
    response.delete_cookie(TOKEN_COOKIE, path=f"/{feed.id}")
    if not name and not email:  # only signing out
        return response
    if not name or not EMAIL.match(email):
        return render(request, "not_me.html", viewer, error="Enter your name and a valid email address.",
                      name=name, email=email)
    if feed.by_email(email):
        response = message_page(request, "You're already in", "You get your own emails already. Use the links in those.",
                                feed=feed)
        response.delete_cookie(TOKEN_COOKIE, path=f"/{feed.id}")
        return response
    raw = core.store.get(feed.id)
    request_text = f"{name} <{email}> opened {viewer.account.name}'s link and asks to get the emails themselves."
    if raw is not None:  # set up on the setup page: the family decides there
        requests = raw.setdefault("join_requests", [])
        if not any(r["email"] == email for r in requests) and len(requests) < JOIN_REQUESTS_MAX:
            requests.append({"name": name, "email": email, "via": viewer.account.name, "requested_at": now()})
            core.store.save(raw)
            background.add_task(_tell_family, feed, raw.get("owner_email"), f"{feed.cfg.title}: {name} asks to join",
                                f"{request_text}\n\nAdd or decline them on the setup page:\n"
                                f"{core.settings.public_url}/setup/f/{feed.id}")
    else:
        background.add_task(feed.notify_admins, f"{feed.cfg.title}: {name} asks to join",
                            f"{request_text}\n\nTo add them: add-relative --feed {feed.id} \"{name}\" {email}")
    response = message_page(request, "Thanks, the family is told",
                            "Once they've added you, you get your own emails. Until then you're signed out here.",
                            feed=feed)
    response.delete_cookie(TOKEN_COOKIE, path=f"/{feed.id}")
    return response


async def _tell_family(feed: Feed, owner_email: str | None, subject: str, body: str) -> None:
    to = feed.cfg.admin_emails or ([owner_email] if owner_email else [])
    if not (to and core.settings.smtp_host):
        return
    try:
        await mailer.send(core.settings, mailer.build_simple(feed.cfg.smtp_from, to, subject, body))
    except Exception:
        log.exception("Could not tell feed %s about a join request", feed.id)
