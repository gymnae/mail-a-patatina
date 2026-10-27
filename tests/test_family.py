"""The owner and the co-parents on the family's website: signed in from the setup page, liking and commenting
in their own name, and told about new comments by an email that looks like the daily one."""

import asyncio
from types import SimpleNamespace as NS

import httpx
import pytest

from mail_a_patatina import links, setup, web
from mail_a_patatina.core import Feed, NotConnected
from mail_a_patatina.library import Entry

PHOTO = "a" * 8 + "-1111-2222-3333-444444444444"
mama = NS(id="user-mama", name="Mama", email="mama@example.com", role="owner", managed=False)
papa = NS(id="user-papa", name="Papa", email="papa@example.com", role="editor", managed=False)


class Client:
    """An Immich connection that records what is posted through it."""

    def __init__(self, name, refuse=False):
        self.name, self.refuse, self.posted = name, refuse, []

    async def add_activity(self, album_id, asset_id, kind, comment=None):
        if self.refuse:
            request = httpx.Request("POST", "http://immich/api/activities")
            raise httpx.HTTPStatusError("no", request=request, response=httpx.Response(403, request=request))
        self.posted.append((asset_id, kind, comment))
        return {"id": "act-1"}


def bare_feed(writers, people):
    feed = Feed.__new__(Feed)
    feed.cfg = NS(bot_email="bot@x.invalid")
    feed.writers, feed.unkeyed, feed.curator = writers, set(), writers.get(mama.id)
    feed.core = NS(people=people)
    return feed


def test_everyone_posts_as_themselves(oma):
    owner, papas = Client("owner key"), Client("papa's key")
    relatives = []

    async def run_as(member, action):
        relatives.append(member.id)
        return await action(Client("oma's key"))

    feed = bare_feed({mama.id: owner, papa.id: papas}, NS(run_as=run_as))
    comment = lambda c: c.add_activity("album", PHOTO, "comment", "Hej")  # noqa: E731
    asyncio.run(feed.act_as(mama, comment))
    asyncio.run(feed.act_as(papa, comment))
    asyncio.run(feed.act_as(NS(**{**vars(oma), "managed": True}), comment))
    assert owner.posted and papas.posted and relatives == [oma.id]


def test_a_co_parent_without_a_key_that_may_comment_is_told_so():
    feed = bare_feed({mama.id: Client("owner key", refuse=True)}, NS())
    with pytest.raises(NotConnected):  # not connected here at all
        asyncio.run(feed.act_as(papa, lambda c: c.add_activity("album", PHOTO, "like")))
    with pytest.raises(NotConnected):  # a key from before it was allowed to comment
        asyncio.run(feed.act_as(mama, lambda c: c.add_activity("album", PHOTO, "like")))


@pytest.fixture
def home(family, settings, oma, monkeypatch, tmp_path):
    """The Falks' feed with Mama (owner), Papa (co-parent) and Oma; one photo shared; Mama gets comment emails."""
    feed = family.feed
    feed.members = {m.id: m for m in (mama, papa, oma)}
    feed.by_email = lambda email: next((m for m in feed.members.values() if m.email == email), None)
    feed.is_family = lambda m: m.role in ("owner", "editor")
    feed.is_coparent = lambda m: m.role == "editor"
    feed.others = lambda: [m for m in feed.members.values() if m.role != "owner"]
    raw = {"id": "falks", "owner_email": mama.email}
    web.core.store = NS(get=lambda feed_id: raw if feed_id == "falks" else None, save=family.saved.append,
                        owned_by=lambda email: [])
    feed.cfg.admin_emails = [mama.email]
    feed.cfg.title = "Die Falks"
    feed.library.replace([Entry(PHOTO, mama.id, "IMAGE", "2026-10-20T09:00:00", "Kürbisse schnitzen", "1")])
    posted = []

    async def act_as(member, action):
        posted.append(member.id)
        return await action(Client(member.name))

    async def jpeg(_curator, asset_id, size):
        return b"\xff\xd8jpeg"

    feed.act_as, feed.curator = act_as, None
    feed.activities_changed = lambda: None
    web.core.media = NS(jpeg=jpeg)
    web.core.feeds = {"falks": feed}
    settings.link_days, settings.default_language, settings.oidc_issuer = 7, "en", ""
    monkeypatch.setattr(web.app.state, "core", web.core, raising=False)
    return NS(feed=feed, posted=posted)


def signed_in_to_setup(client, monkeypatch, who):
    session = setup.SetupSession(who.email, who.name)
    monkeypatch.setitem(setup.SESSIONS, f"sid-{who.id}", session)
    client.cookies.set("map_setup", f"sid-{who.id}", path="/setup")


def test_owner_and_co_parent_open_the_feed_from_the_setup_page(client, home, monkeypatch, oma):
    for who in (mama, papa):
        signed_in_to_setup(client, monkeypatch, who)
        r = client.get("/setup/f/falks/open")
        assert r.status_code == 303 and r.headers["location"] == "/falks/"
        assert client.get("/falks/").status_code == 200
        client.cookies.clear()
    signed_in_to_setup(client, monkeypatch, oma)  # relatives come in through their emails
    assert client.get("/setup/f/falks/open").status_code == 404


def test_co_parent_sees_the_feed_listed_on_the_setup_page(client, home, monkeypatch):
    signed_in_to_setup(client, monkeypatch, papa)
    r = client.get("/setup/")
    assert "Feeds you add photos to" in r.text and 'href="/setup/f/falks/open"' in r.text


def test_comment_email_looks_like_the_daily_one_and_signs_the_family_in(client, home, family, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    r = client.post(f"/falks/e/{PHOTO}/comment", data={"body": "So schön!\nWie im letzten Jahr."})
    assert r.status_code == 303 and home.posted == [oma.id]
    msg, to_papa = family.sent
    assert msg["Subject"] == "Die Falks: Oma Erika commented" and msg["To"] == mama.email
    assert to_papa["To"] == papa.email and "/setup/f/falks" not in to_papa.get_body(("html",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "So schön!\nWie im letzten Jahr." in html and "Kürbisse schnitzen" in html and "cid:" in html
    link = html.split('href="')[1].split('"')[0]
    assert link.startswith(f"http://testserver/falks/e/{PHOTO}?t=") and "Reply" in html
    token = link.split("?t=")[1].split("#")[0]
    assert links.read_token(settings, token).ident == mama.id  # opens the photo signed in as Mama
    kinds = [part.get_content_type() for part in msg.walk()]
    assert kinds.count("image/jpeg") == 1 and "image/png" in kinds  # the photo and the logo


def test_the_owner_replies_in_their_own_name_and_isnt_emailed_about_it(client, home, family, monkeypatch):
    signed_in_to_setup(client, monkeypatch, mama)
    client.get("/setup/f/falks/open")
    client.post(f"/falks/e/{PHOTO}/comment", data={"body": "Danke!"})
    assert home.posted == [mama.id] and [m["To"] for m in family.sent] == [papa.email]


def test_co_parent_replies_and_only_mama_is_emailed(client, home, family, monkeypatch):
    signed_in_to_setup(client, monkeypatch, papa)
    client.get("/setup/f/falks/open")
    client.post(f"/falks/e/{PHOTO}/comment", data={"body": "Hab ich geschnitzt!"})
    assert home.posted == [papa.id] and [m["To"] for m in family.sent] == [mama.email]
    assert "Papa commented" in family.sent[0]["Subject"]


def test_co_parent_stops_the_comment_emails_from_the_email(client, home, family, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    client.post(f"/falks/e/{PHOTO}/comment", data={"body": "Eins"})
    html = family.sent[1].get_body(("html",)).get_content()
    off = html.split("Stop these emails")[0].rsplit('href="', 1)[1].split('"')[0].replace("&amp;", "&")
    path = off.removeprefix("http://testserver")
    assert path.startswith("/falks/comment-emails?t=")
    r = client.get(path)  # a page to confirm on, not signed in and sent elsewhere
    assert r.status_code == 200 and "papa@example.com" in r.text
    assert client.post(path).status_code == 200 and web.core.store.get("falks")["quiet_comments"] == [papa.id]
    family.sent.clear()
    client.post(f"/falks/e/{PHOTO}/comment", data={"body": "Zwei"})
    assert [m["To"] for m in family.sent] == [mama.email]
    oma_token = links.make_token(settings, "falks", "u", oma.id)
    assert client.get(f"/falks/comment-emails?t={oma_token}").status_code == 404  # only co-parents


def test_a_co_parent_who_cant_comment_yet_is_told_how(client, home, monkeypatch):
    async def refuse(member, action):
        raise NotConnected(member.id)

    home.feed.act_as = refuse
    signed_in_to_setup(client, monkeypatch, papa)
    client.get("/setup/f/falks/open")
    r = client.post(f"/falks/e/{PHOTO}/comment", data={"body": "Hallo"})
    assert r.status_code == 503 and "ask for a new invite link" in r.text
