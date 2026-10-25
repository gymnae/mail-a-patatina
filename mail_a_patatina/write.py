"""The journal editor at /setup/f/<feed id>/trips/: the family writes its trips, from the phone or a computer.

Behind the setup page's sign-in, for the feed's owner. Everything is saved as Markdown files in the family's
journal folder; the photos are picked from the owner's Immich library and added to the trip's album when an
entry is published, so relatives can see, like and comment on them. Unlike the rest of the setup page, it
speaks the writer's language: the people writing are the family, not whoever set up the installation."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import secrets
import zoneinfo
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import journal
from .i18n import t
from .immich import TRIP_PERMISSIONS
from .setup import SetupSession, core, current, immich_oauth, join_language, owned, page, require

log = logging.getLogger(__name__)
router = APIRouter(prefix="/setup/f")

MAX_TEXT = 50_000  # characters per text block
MAX_BLOCKS = 200
PICKER_PAGE = 60


def writer(request: Request, feed_id: str, csrf: str | None = None):
    """-> (setup session, the feed's stored settings, the running feed, its journal folder)."""
    session = require(request, csrf)
    raw = owned(request, session, feed_id)
    feed = core(request).feeds.get(feed_id)
    if feed is None:
        raise HTTPException(404)
    return session, raw, feed, core(request).settings.journal_dir / feed_id


def trip_folder(root: Path, slug: str) -> Path:
    folder = root / slug
    if not journal.NAME.match(slug) or not (folder / "trip.md").is_file():
        raise HTTPException(404)
    return folder


def show(request: Request, template: str, session: SetupSession, status: int = 200, **ctx) -> HTMLResponse:
    """An editor page: in the writer's language, with the family's name on top."""
    return page(request, template, session, status, lang=join_language(request), site_title=ctx["raw"].get("title") or "Mail-A-Patatina", **ctx)


def go(url: str, msg: str = "", error: str = "") -> RedirectResponse:
    query = urlencode({k: v for k, v in (("msg", msg), ("error", error)) if v})
    return RedirectResponse(url + (f"?{query}" if query else ""), status_code=303)


async def missing_permissions(feed) -> list[str]:
    """What the family's Immich key lacks for writing trips (keys from before trips existed)."""
    try:
        perms = set((await feed.curator.api_key_info()).get("permissions", []))
    except httpx.HTTPError:
        return []
    return [] if "all" in perms else [p for p in TRIP_PERMISSIONS if p not in perms]


def readers(feed) -> list[dict]:
    """Who a trip can be shared with: the feed's relatives, and its group chats (through the chat account)."""
    out = [{"id": m.id, "name": m.name} for m in sorted(feed.relatives(), key=lambda m: m.name.casefold())]
    if feed.cfg.chats and feed.bot:
        out.append({"id": feed.bot.id, "name": None, "chats": [c.name for c in feed.cfg.chats]})
    return out


def coparents(feed) -> list[str]:
    """User ids of the other parents: they get every trip's album as editors, to add photos of their own."""
    return [m.id for m in feed.others() if feed.is_coparent(m)]


def today(feed) -> dt.datetime:
    try:
        return dt.datetime.now(zoneinfo.ZoneInfo(feed.cfg.timezone)).replace(tzinfo=None)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return dt.datetime.now()


def _refresh_later(background: BackgroundTasks, feed) -> None:
    """Show what was just saved to the family right away, not only at the next sync."""
    async def refresh():
        try:
            await feed.journal.refresh(feed)
        except Exception:
            log.exception("Feed %s: reading its journal failed", feed.id)
    background.add_task(refresh)


# --- trips ---------------------------------------------------------------------------


@router.get("/{feed_id}/trips/", response_class=HTMLResponse)
async def trips(request: Request, feed_id: str, msg: str = "", error: str = ""):
    session, raw, feed, root = writer(request, feed_id)
    listed = []
    if root.is_dir():
        for folder in sorted((p for p in root.iterdir() if p.is_dir() and journal.NAME.match(p.name)), reverse=True):
            if (folder / "trip.md").is_file():
                meta, _ = journal.trip_file(folder)
                entries = journal.drafts(folder)
                listed.append({"slug": folder.name, "title": meta.get("title") or folder.name, "entries": len(entries),
                               "drafts": sum(e.is_draft for e in entries), "latest": entries[0].when if entries else ""})
    listed.sort(key=lambda trip: trip["latest"], reverse=True)
    return show(request, "setup/write_trips.html", session, raw=raw, trips=listed, msg=msg, error=error,
                missing=await missing_permissions(feed), immich_oauth=await immich_oauth(request))


async def free_albums(feed) -> list[dict]:
    """The owner's albums that aren't the feed's or another trip's yet, for "use an album you already have"."""
    used = {feed.cfg.album_id} | {trip.album_id for trip in feed.journal.trips.values()}
    me = feed.curator.user_id
    return [a for a in await feed.curator.albums()
            if a["id"] not in used and any(u["role"] == "owner" and u["user"]["id"] == me for u in a.get("albumUsers", []))]


@router.get("/{feed_id}/trips/new", response_class=HTMLResponse)
async def new_trip(request: Request, feed_id: str):
    session, raw, feed, _ = writer(request, feed_id)
    return show(request, "setup/write_trip_new.html", session, raw=raw, readers=readers(feed),
                albums=await free_albums(feed), missing=await missing_permissions(feed),
                immich_oauth=await immich_oauth(request))


@router.post("/{feed_id}/trips/new")
async def create_trip(request: Request, feed_id: str):
    form = await request.form()
    session, raw, feed, root = writer(request, feed_id, str(form.get("csrf", "")))
    lang = join_language(request)
    title = " ".join(str(form.get("title", "")).split())[:80]
    if not title:
        return go(f"/setup/f/{feed_id}/trips/new", error=t(lang, "Give the trip a name."))
    chosen = {str(v) for v in form.getlist("reader")}
    shared = [(r["id"], "viewer") for r in readers(feed) if r["id"] in chosen] + [(u, "editor") for u in coparents(feed)]
    try:
        root.mkdir(parents=True, exist_ok=True)
        slug = journal.free_name(root, journal.slugify(title, "trip"))
        folder = root / slug
        folder.mkdir()
    except OSError as e:
        log.error("Feed %s: can't create a trip folder in %s: %s", feed_id, root, e)
        return go(f"/setup/f/{feed_id}/trips/", error=t(lang, "The server can't save files in the journal folder. "
                                                               "Ask whoever runs Mail-A-Patatina to fix that."))
    album_id = str(form.get("album_id", ""))
    try:
        if album_id:
            if album_id not in {a["id"] for a in await free_albums(feed)}:
                raise HTTPException(404)
            members = (await feed.curator.album(album_id)).get("albumUsers") or []
            present = {u["user"]["id"] for u in members}
            for user_id, role in shared:
                if user_id not in present:
                    await feed.curator.add_album_user(album_id, user_id, role)
        else:
            album_id = (await feed.curator.create_album(title, shared))["id"]
    except (httpx.HTTPError, HTTPException):
        folder.rmdir()
        raise
    journal.save_trip_file(folder, {"title": title, "album_id": album_id}, "")
    log.info("Feed %s: trip %s started by %s", feed_id, slug, session.email)
    await feed.journal.refresh(feed)
    return RedirectResponse(f"/setup/f/{feed_id}/trips/{slug}/new", status_code=303)


@router.get("/{feed_id}/trips/{slug}/", response_class=HTMLResponse)
async def trip(request: Request, feed_id: str, slug: str, msg: str = "", error: str = ""):
    session, raw, feed, root = writer(request, feed_id)
    folder = trip_folder(root, slug)
    meta, _ = journal.trip_file(folder)
    album = await feed.curator.album(str(meta.get("album_id")))
    in_album = {u["user"]["id"]: u["user"]["name"] for u in album.get("albumUsers") or [] if u["role"] != "owner"}
    options = readers(feed)
    known = {r["id"] for r in options} | set(coparents(feed))
    others = [core(request).people.display_name(name) for uid, name in in_album.items() if uid not in known]
    return show(request, "setup/write_trip.html", session, raw=raw, slug=slug, title=meta.get("title") or slug,
                entries=journal.drafts(folder), readers=options, shared=set(in_album), others=others,
                msg=msg, error=error)


@router.post("/{feed_id}/trips/{slug}/settings")
async def trip_settings(request: Request, background: BackgroundTasks, feed_id: str, slug: str):
    form = await request.form()
    session, raw, feed, root = writer(request, feed_id, str(form.get("csrf", "")))
    lang = join_language(request)
    folder = trip_folder(root, slug)
    meta, intro = journal.trip_file(folder)
    title = " ".join(str(form.get("title", "")).split())[:80]
    if title and title != meta.get("title"):
        meta["title"] = title
        journal.save_trip_file(folder, meta, intro)
    album_id = str(meta.get("album_id"))
    present = {u["user"]["id"] for u in (await feed.curator.album(album_id)).get("albumUsers") or []}
    chosen = {str(v) for v in form.getlist("reader")}
    for r in readers(feed):  # only the feed's people: anyone shared with in Immich directly stays
        if r["id"] in chosen and r["id"] not in present:
            await feed.curator.add_album_user(album_id, r["id"])
        elif r["id"] not in chosen and r["id"] in present:
            await feed.curator.remove_album_user(album_id, r["id"])
    _refresh_later(background, feed)
    return go(f"/setup/f/{feed_id}/trips/{slug}/", msg=t(lang, "Saved."))


# --- entries -----------------------------------------------------------------------------


def editor(request: Request, session: SetupSession, raw: dict, feed, slug: str, title: str, entry: journal.Draft):
    when = entry.when or f"{today(feed):%Y-%m-%dT%H:%M:%S}"
    lang = join_language(request)
    base = f"/setup/f/{raw['id']}/trips/{slug}"
    data = {
        "csrf": session.csrf, "storage": f"map-entry:{raw['id']}/{slug}/",
        "entry": {"slug": entry.slug, "version": entry.version, "blocks": entry.blocks or [{"kind": "text", "text": ""}],
                  "draft": entry.is_draft or not entry.slug},
        "urls": {"save": f"{base}/save", "preview": f"{base}/preview", "photos": f"{base}/photos",
                 "thumb": f"/setup/f/{raw['id']}/photo",
                 "edit": f"{base}/e/", "signin": "/setup/"},
        "texts": {k: t(lang, k) for k in EDITOR_TEXTS},
    }
    return show(request, "setup/write_entry.html", session, raw=raw, slug=slug, trip_title=title, entry=entry,
                day=when[:10], data=data)


@router.get("/{feed_id}/trips/{slug}/new", response_class=HTMLResponse)
async def new_entry(request: Request, feed_id: str, slug: str):
    session, raw, feed, root = writer(request, feed_id)
    meta, _ = journal.trip_file(trip_folder(root, slug))
    return editor(request, session, raw, feed, slug, meta.get("title") or slug, journal.Draft("", {}, []))


@router.get("/{feed_id}/trips/{slug}/e/{entry_slug}", response_class=HTMLResponse)
async def edit_entry(request: Request, feed_id: str, slug: str, entry_slug: str):
    session, raw, feed, root = writer(request, feed_id)
    folder = trip_folder(root, slug)
    path = folder / f"{entry_slug}.md"
    if not journal.NAME.match(entry_slug) or entry_slug == "trip" or not path.is_file():
        raise HTTPException(404)
    meta, _ = journal.trip_file(folder)
    return editor(request, session, raw, feed, slug, meta.get("title") or slug, journal.load_draft(path))


def _clean_blocks(blocks) -> list[dict] | None:
    """The editor's blocks, checked: text, or photos given by their Immich id. None if they make no sense."""
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        return None
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            return None
        if b.get("kind") == "text":
            out.append({"kind": "text", "text": str(b.get("text") or "").replace("\r\n", "\n")[:MAX_TEXT]})
        elif b.get("kind") == "photos" and isinstance(b.get("photos"), list):
            photos = [{"id": str(p.get("id", "")).lower(), "caption": str(p.get("caption") or "")[:300],
                       "file": str(p.get("file") or "")[:200]} for p in b["photos"] if isinstance(p, dict)]
            if any(not journal.ASSET_ID.match(p["id"]) for p in photos):
                return None
            out.append({"kind": "photos", "photos": photos})
        else:
            return None
    return out


async def _entry_from_editor(request: Request, fail) -> dict | Response:
    """The entry the editor sends as JSON, or the answer to give when it can't be used."""
    # JSON only: other sites can't send that without asking first (CORS), so no form elsewhere can post here.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415)
    try:
        body = await request.json()
    except ValueError:
        return fail("That didn't work")
    if not isinstance(body, dict):
        return fail("That didn't work")
    session = current(request)
    if session is None:
        # An hour without saving, or the server restarted: the editor keeps the text on the device meanwhile.
        return fail("You were signed out. What you wrote is kept on this device. Sign in again (it opens in a new "
                    "tab), then come back here and save again.", 401, signed_out=True)
    if not secrets.compare_digest(str(body.get("csrf", "")), session.csrf):
        # Signed in again in another tab: this page's token is from the old sign-in. Same origin only, see above.
        return JSONResponse({"csrf": session.csrf, "retry": True}, status_code=409)
    blocks = _clean_blocks(body.get("blocks"))
    try:
        day = dt.date.fromisoformat(str(body.get("date", "")))
    except ValueError:
        return fail("Pick a date.")
    if blocks is None:
        return fail("That didn't work")
    return {**body, "blocks": blocks, "day": day}


@router.post("/{feed_id}/trips/{slug}/save")
async def save_entry(request: Request, background: BackgroundTasks, feed_id: str, slug: str):
    """Called by the editor with the whole entry as JSON. Answers JSON: the editor stays on the page."""
    lang = join_language(request)
    fail = lambda text, status=400, **extra: JSONResponse({"error": t(lang, text), **extra}, status_code=status)  # noqa: E731
    body = await _entry_from_editor(request, fail)
    if isinstance(body, Response):
        return body
    _, raw, feed, root = writer(request, feed_id)
    folder = trip_folder(root, slug)
    blocks, day = body["blocks"], body["day"]
    entry_slug = str(body.get("slug") or "")
    if entry_slug:
        path = folder / f"{entry_slug}.md"
        if not journal.NAME.match(entry_slug) or entry_slug == "trip" or not path.is_file():
            return fail("This entry was deleted in the meantime.", 409)
        old = journal.load_draft(path)
        if old.version != str(body.get("version", "")):
            return fail("Someone else changed this entry since you opened it. Copy your text, reload the page "
                        "and add it again.", 409)
        meta = dict(old.meta)
    else:
        meta = {}
    title = " ".join(str(body.get("title") or "").split())[:120]
    place = " ".join(str(body.get("place") or "").split())[:120]
    publish = bool(body.get("publish"))
    # Keep the time it was written at, as long as the day stays the same; a new entry for today gets the time now.
    when = journal._when(meta.get("date"), entry_slug) or ""
    now = today(feed)
    date = (meta["date"] if when[:10] == day.isoformat() and "date" in meta
            else f"{now:%Y-%m-%dT%H:%M}" if day == now.date() else day.isoformat())
    known = {k: v for k, v in meta.items() if k not in ("title", "date", "place", "draft")}
    meta = {**({"title": title} if title else {}), "date": date, **({"place": place} if place else {}),
            **({} if publish else {"draft": True}), **known}

    trip_meta, _ = journal.trip_file(folder)
    ids = list(dict.fromkeys(p["id"] for b in blocks if b["kind"] == "photos" for p in b["photos"]))
    if publish and ids:
        try:
            await feed.curator.add_album_assets(str(trip_meta.get("album_id")), ids)
        except httpx.HTTPStatusError as e:
            log.warning("Feed %s: adding photos to the album of trip %s: %s", feed_id, slug, e)
            if e.response.status_code == 403:
                return fail("Mail-A-Patatina isn't allowed to add photos to albums yet. Connect it with Immich "
                            "again on the trips page, then publish again.", 403)
            return fail("Immich isn't answering")
    if not entry_slug:
        entry_slug = journal.free_name(folder, f"{day.isoformat()}-{journal.slugify(title, 'entry')}"[:72], ".md")
        path = folder / f"{entry_slug}.md"
    try:
        await asyncio.to_thread(journal.write_file, path, journal.entry_text(meta, blocks))
    except OSError as e:
        log.error("Feed %s: can't save %s: %s", feed_id, path, e)
        return fail("The server can't save files in the journal folder. Ask whoever runs Mail-A-Patatina to fix that.",
                    500)
    log.info("Feed %s: %s %s/%s", feed_id, "published" if publish else "saved draft", slug, entry_slug)
    _refresh_later(background, feed)
    return {"slug": entry_slug, "version": journal._version(path), "published": publish}


VIDEO_FILES = (".mov", ".mp4", ".m4v", ".3gp", ".webm", ".mkv", ".avi")


@router.post("/{feed_id}/trips/{slug}/preview")
async def preview_entry(request: Request, feed_id: str, slug: str):
    """The entry as the editor has it, saved or not, on the page relatives read it on."""
    from .web import Activity, render  # the reader's pages, which include this router

    lang = join_language(request)
    fail = lambda text, status=400, **extra: JSONResponse({"error": t(lang, text), **extra}, status_code=status)  # noqa: E731
    body = await _entry_from_editor(request, fail)
    if isinstance(body, Response):
        return body
    _, raw, feed, root = writer(request, feed_id)
    folder = trip_folder(root, slug)
    meta, _ = journal.trip_file(folder)
    text = journal.entry_text({}, body["blocks"]).split("---\n", 2)[-1]
    post = journal.Post(str(body.get("slug") or "preview"), body["day"].isoformat(),
                        " ".join(str(body.get("title") or "").split())[:120],
                        " ".join(str(body.get("place") or "").split())[:120], journal.blocks(text))
    files = {p["id"]: p["file"] for b in body["blocks"] if b["kind"] == "photos" for p in b["photos"]}
    known = {m.asset_id: m for t in feed.journal.trips.values() for p in t.posts for m in p.media}
    for m in post.media:
        if m.asset_id in known:
            m.type, m.live_video_id = known[m.asset_id].type, known[m.asset_id].live_video_id
        elif files.get(m.asset_id, "").lower().endswith(VIDEO_FILES):
            m.type = "VIDEO"
    trip = journal.Trip(slug, str(meta.get("title") or slug), str(meta.get("album_id") or ""), "", [post])
    nobody = Activity([], None, [])
    return render(request, "trip_post.html", None, feed=feed, trip=trip, post=post, preview=True,
                  photo_url=f"/setup/f/{feed_id}/photo/", reactions={m.asset_id: nobody for m in post.media})


@router.post("/{feed_id}/trips/{slug}/e/{entry_slug}/delete")
async def delete_entry(request: Request, background: BackgroundTasks, feed_id: str, slug: str, entry_slug: str,
                       csrf: str = Form()):
    _, _, feed, root = writer(request, feed_id, csrf)
    path = trip_folder(root, slug) / f"{entry_slug}.md"
    if not journal.NAME.match(entry_slug) or entry_slug == "trip" or not path.is_file():
        raise HTTPException(404)
    journal.trash(path)
    _refresh_later(background, feed)
    return go(f"/setup/f/{feed_id}/trips/{slug}/", msg=t(join_language(request), "The entry is deleted."))


# --- the photo picker ----------------------------------------------------------------------


def _photo(asset: dict) -> dict:
    exif = asset.get("exifInfo") or {}
    place = ", ".join(x for x in (exif.get("city"), exif.get("country")) if x)
    return {"id": asset["id"], "type": asset.get("type"), "time": (asset.get("localDateTime") or "")[:16],
            "place": place, "file": asset.get("originalFileName") or ""}


@router.get("/{feed_id}/trips/{slug}/photos")
async def photos(request: Request, feed_id: str, slug: str, mode: str = "day", day: str = "", after: str = ""):
    """Photos to pick from: those taken on one day, the most recent ones, or those in the trip's album."""
    if current(request) is None:
        return JSONResponse({"error": "signed out", "signed_out": True}, status_code=401)
    _, _, feed, root = writer(request, feed_id)
    folder = trip_folder(root, slug)
    usable = lambda a: not a.get("isTrashed") and a.get("type") in {"IMAGE", "VIDEO"}  # noqa: E731
    if mode == "album":
        meta, _ = journal.trip_file(folder)
        found = [a async for a in feed.curator.search(albumIds=[str(meta.get("album_id"))]) if usable(a)]
        found.sort(key=lambda a: a.get("localDateTime") or "", reverse=True)
        return {"photos": [_photo(a) for a in found], "after": ""}
    if mode == "recent":
        found, nxt = await feed.curator.search_page(after, order="desc", size=PICKER_PAGE)
        return {"photos": [_photo(a) for a in found if usable(a)], "after": nxt}
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(404) from None
    # Immich filters by UTC; a day where the photos were taken can be up to a day off either way.
    found = [a async for a in feed.curator.search(
        takenAfter=f"{d - dt.timedelta(days=1)}T00:00:00.000Z", takenBefore=f"{d + dt.timedelta(days=2)}T00:00:00.000Z",
        order="asc") if usable(a) and (a.get("localDateTime") or "")[:10] == d.isoformat()]
    return {"photos": [_photo(a) for a in found], "after": ""}


@router.get("/{feed_id}/photo/{asset_id}")
async def photo(request: Request, feed_id: str, asset_id: str, size: str = "thumb"):
    """A picture of a photo from the family's library: small for the editor and the picker, big for the preview."""
    _, _, feed, _ = writer(request, feed_id)
    if not journal.ASSET_ID.match(asset_id) or size not in ("thumb", "preview"):
        raise HTTPException(404)
    data = await core(request).media.jpeg(feed.curator, asset_id, size)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# Texts the editor's script shows, translated on the page.
EDITOR_TEXTS = [
    "Saved as a draft", "Published", "Saving…", "Not saved yet", "That didn't work", "Caption",
    "Remove", "Move up", "Move down", "Add more photos here", "Write here…", "Nothing found for this day.",
    "No photos here yet.", "Add photos ({n})", "Show more", "Publish", "Save as draft", "Save changes",
    "Turn back into a draft", "Sign in again", "Restore what I wrote", "You wrote more on this device than was saved.",
    "Restored what you wrote on this device before.", "video", "Loading…", "Preview",
]
