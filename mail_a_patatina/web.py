"""The viewer website: magic-link login, feed, calendar, and one page per moment.
Likes and comments are read from and written to Immich, as the signed-in relative.

Every page lives under /<feed id>/, its sign-in cookie is limited to that path, and every request is
checked against that feed only, so families sharing one installation never see each other."""

from __future__ import annotations

import calendar
import datetime as dt
import logging
import re
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

from .config import ChatSpec, Settings
from .core import Core, Feed
from .immich import LoginFailed
from .library import Entry
from .links import LIKE, make_token, nice_date, read_photo_ticket, read_token
from .people import Member
from .setup import router as setup_router

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

HERE = Path(__file__).parent
TOKEN_COOKIE, GUEST_NAME_COOKIE = "map_t", "map_n"
COOKIE_AGE = 400 * 24 * 3600
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
templates.env.filters["nice_date"] = nice_date


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


def viewer_for_token(feed: Feed, token: str | None, request: Request | None = None) -> Viewer | None:
    parsed = read_token(core.settings, token) if token else None
    if parsed is None:
        return None
    feed_id, kind, ident = parsed
    if feed_id != feed.id:  # a genuine link, but for another family
        return None
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


def require_viewer(request: Request, feed_id: str) -> Viewer:
    viewer = viewer_for_token(get_feed(feed_id), request.cookies.get(TOKEN_COOKIE), request)
    if viewer is None:
        raise HTTPException(403)
    return viewer


def set_cookie(response: Response, feed: Feed, name: str, value: str) -> None:
    response.set_cookie(
        name, value, max_age=COOKIE_AGE, path=f"/{feed.id}", httponly=True, samesite="lax",
        secure=core.settings.secure_cookies,
    )


@app.middleware("http")
async def magic_link(request: Request, call_next):
    """`?t=<token>` on any page signs the visitor in, then redirects to the same URL without the token,
    so a link copied from the address bar never carries someone's identity."""
    token = request.query_params.get("t")
    parts = request.url.path.strip("/").split("/")
    if not token or request.method != "GET" or (len(parts) > 1 and parts[1] == "unsubscribe"):
        return await call_next(request)
    feed = core.feeds.get(parts[0])
    new = viewer_for_token(feed, token) if feed else None
    if new is None:
        return message_page(request, "This link is no longer valid", "Ask the family for a fresh one.", 403)
    params = {k: v for k, v in request.query_params.items() if k != "t"}
    response = RedirectResponse(request.url.path + (f"?{urlencode(params)}" if params else ""), status_code=303)
    current = viewer_for_token(feed, request.cookies.get(TOKEN_COOKIE), request)
    # Keep a personal identity when the same person also opens a group-chat link.
    if current is None or not new.is_guest or current.is_guest:
        set_cookie(response, feed, TOKEN_COOKIE, token)
    return response


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


def render(request: Request, template: str, viewer: Viewer | None, status: int = 200, **ctx) -> HTMLResponse:
    feed = viewer.feed if viewer else ctx.pop("feed", None)
    ctx |= {
        "viewer": viewer, "like": LIKE,
        "site_title": feed.cfg.title if feed else "Mail-A-Patatina",
        "base": f"/{feed.id}" if feed else "",
    }
    return templates.TemplateResponse(request, template, ctx, status_code=status)


def message_page(request: Request, title: str, text: str, status: int = 200) -> HTMLResponse:
    return render(request, "message.html", None, status, title=title, text=text)


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
    if recipient is None or viewer_for_token(feed, make_token(core.settings, feed.id, *recipient)) is None:
        raise HTTPException(404)
    shared_entry(feed, asset_id)
    data = await core.media.jpeg(feed.curator, asset_id, "preview")
    return Response(data, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, noimageindex", "Referrer-Policy": "no-referrer"})


@app.get("/{feed_id}/video/{asset_id}")
async def video(request: Request, feed_id: str, asset_id: str):
    feed = require_viewer(request, feed_id).feed
    if shared_entry(feed, asset_id).type != "VIDEO":
        raise HTTPException(404)
    upstream = await feed.curator.open_video(asset_id, request.headers.get("range"))
    keep = ("content-type", "content-length", "content-range", "accept-ranges")
    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() in keep},
        background=BackgroundTask(upstream.aclose),
    )


# --- unsubscribe (one-click capable, RFC 8058) — stored as the member's Immich email preference ---


def _member_for_unsubscribe(feed_id: str, token: str) -> tuple[Feed, Member]:
    feed = get_feed(feed_id)
    viewer = viewer_for_token(feed, token)
    if viewer is None or viewer.is_guest:
        raise HTTPException(404)
    return feed, viewer.account


@app.get("/{feed_id}/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_confirm(request: Request, feed_id: str, t: str = ""):
    feed, member = _member_for_unsubscribe(feed_id, t)
    return render(request, "unsubscribe.html", None, feed=feed, email=member.email, token=t)


@app.post("/{feed_id}/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(request: Request, feed_id: str, t: str = ""):
    feed, member = _member_for_unsubscribe(feed_id, t)
    await core.people.run_as(member, lambda c: c.update_preferences({"emailNotifications": {"enabled": False}}))
    return render(request, "message.html", None, feed=feed, title="Unsubscribed",
                  text="You won't get these emails anymore. Your links still open the album.")
