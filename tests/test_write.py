"""The journal editor: starting trips, writing entries as files, picking photos, and keeping text safe."""

import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from mail_a_patatina import journal, setup, web
from mail_a_patatina.journal import Journal

P1, P2 = (f"{n}" * 8 + "-1111-2222-3333-444444444444" for n in "ef")


class FakeImmich:
    """The family's own Immich connection, recording what the editor asks of it."""

    def __init__(self, oma):
        self.user_id = "user-mama"
        self.albums_made, self.added, self.shared, self.unshared = [], [], [], []
        self.album_users = {"feed-album": [], "old-album": [
            {"role": "owner", "user": {"id": "user-mama", "name": "Mama"}}]}
        self.permissions = ["all"]
        self.library = [
            {"id": P1, "type": "IMAGE", "localDateTime": "2026-08-04T23:30:00.000Z", "originalFileName": "IMG_1.HEIC",
             "exifInfo": {"city": "Kuusamo", "country": "Finland"}},
            {"id": P2, "type": "VIDEO", "localDateTime": "2026-08-05T00:30:00.000Z", "originalFileName": "VID_2.MOV"},
        ]

    async def create_album(self, name, users):
        self.albums_made.append((name, users))
        album_id = f"album-{len(self.albums_made)}"
        self.album_users[album_id] = [{"role": r, "user": {"id": u, "name": u}} for u, r in users]
        return {"id": album_id}

    async def albums(self):
        return [{"id": a, "albumName": a, "albumUsers": users} for a, users in self.album_users.items()]

    async def album(self, album_id):
        return {"albumUsers": self.album_users[album_id]}

    async def add_album_user(self, album_id, user_id, role="viewer"):
        self.shared.append((album_id, user_id, role))
        self.album_users[album_id].append({"role": role, "user": {"id": user_id, "name": user_id}})

    async def remove_album_user(self, album_id, user_id):
        self.unshared.append((album_id, user_id))
        self.album_users[album_id] = [u for u in self.album_users[album_id] if u["user"]["id"] != user_id]

    async def add_album_assets(self, album_id, ids):
        if "albumAsset.create" not in self.permissions and "all" not in self.permissions:
            request = httpx.Request("PUT", "http://immich/api/albums")
            raise httpx.HTTPStatusError("no", request=request, response=httpx.Response(403, request=request))
        self.added.append((album_id, ids))

    async def api_key_info(self):
        return {"permissions": self.permissions}

    async def search(self, **filters):
        for a in self.library:
            yield a

    async def search_page(self, after="", **filters):
        return self.library[::-1], ""


@pytest.fixture
def writing(client, family, settings, oma, tmp_path, monkeypatch):
    """Mama, signed in on the setup page, owns the Falks' feed and writes its journal."""
    settings.journal_dir, settings.oidc_issuer, settings.immich_oauth_connect = tmp_path, "", False
    immich = FakeImmich(oma)
    feed = family.feed
    feed.curator = immich
    feed.cfg.timezone, feed.cfg.chats = "Europe/Berlin", []
    feed.relatives = lambda: [oma]
    feed.others = lambda: [oma]
    feed.is_coparent = lambda m: False
    feed.core = NS(people=NS(members=lambda users: {u["user"]["id"]: oma for u in users if u["role"] != "owner"}),
                   media=NS(purge=lambda _: None))
    feed.journal = Journal(tmp_path / "falks")
    monkeypatch.setattr(web.app.state, "core", web.core, raising=False)
    session = setup.SetupSession("mama@example.com", "Mama")
    monkeypatch.setitem(setup.SESSIONS, "sid", session)
    client.cookies.set("map_setup", "sid", path="/setup")
    return NS(immich=immich, session=session, root=tmp_path / "falks", feed=feed)


def start_trip(client, writing, **form):
    return client.post("/setup/f/falks/trips/new", data={"csrf": writing.session.csrf, "title": "Sommer in Lappland",
                                                        "reader": "user-oma", **form})


def save(client, writing, **body):
    entry = {"csrf": writing.session.csrf, "slug": "", "version": "", "title": "Rentiere!", "date": "2026-08-04",
             "place": "", "publish": False, "blocks": [
                 {"kind": "text", "text": "Die Kinder haben die **Rentiere** gefüttert."},
                 {"kind": "photos", "photos": [{"id": P1, "caption": "Fütterung", "file": "IMG_1.HEIC"}]},
                 {"kind": "text", "text": "Abends Lachssuppe."}]}
    return client.post("/setup/f/falks/trips/sommer-in-lappland/save", json={**entry, **body})


def test_starting_a_trip_makes_its_album_and_folder(client, writing):
    r = start_trip(client, writing)
    assert r.status_code == 303 and r.headers["location"] == "/setup/f/falks/trips/sommer-in-lappland/new"
    assert writing.immich.albums_made == [("Sommer in Lappland", [("user-oma", "viewer")])]
    meta, _ = journal.trip_file(writing.root / "sommer-in-lappland")
    assert meta == {"title": "Sommer in Lappland", "album_id": "album-1"}

    r = client.get("/setup/f/falks/trips/sommer-in-lappland/new", headers={"Accept-Language": "de"})
    assert r.status_code == 200 and "Wo wart ihr?" in r.text
    data = json.loads(r.text.split('<script type="application/json" id="entry-data">')[1].split("</script>")[0])
    assert data["texts"]["Publish"] == "Veröffentlichen" and data["entry"]["blocks"] == [{"kind": "text", "text": ""}]


def test_a_trip_can_use_an_album_made_in_immich(client, writing):
    start_trip(client, writing, album_id="old-album")
    assert writing.immich.albums_made == [] and writing.immich.shared == [("old-album", "user-oma", "viewer")]
    assert journal.trip_file(writing.root / "sommer-in-lappland")[0]["album_id"] == "old-album"
    assert start_trip(client, writing, album_id="feed-album").status_code == 404  # never the feed's own album


def test_draft_then_publish(client, writing, oma):
    start_trip(client, writing)
    r = save(client, writing)
    assert r.status_code == 200 and r.json()["slug"] == "2026-08-04-rentiere" and r.json()["published"] is False
    path = writing.root / "sommer-in-lappland/2026-08-04-rentiere.md"
    text = path.read_text()
    assert text.startswith("---\ntitle: Rentiere!\ndate: '2026-08-04'\ndraft: true\n---\n\n")
    assert '\n\n![Fütterung](immich:%s "IMG_1.HEIC")\n\nAbends Lachssuppe.\n' % P1 in text
    assert writing.immich.added == []  # drafts don't put anything in the album yet

    asyncio.run(writing.feed.journal.refresh(writing.feed))
    assert writing.feed.journal.trips["sommer-in-lappland"].posts == []  # relatives don't see drafts

    r = save(client, writing, slug="2026-08-04-rentiere", version=r.json()["version"], publish=True)
    assert r.json()["published"] is True and "draft" not in path.read_text()
    assert writing.immich.added == [("album-1", [P1])]

    r = client.get("/setup/f/falks/trips/sommer-in-lappland/")
    assert "Rentiere!" in r.text and "Draft" not in r.text


def test_someone_elses_newer_save_isnt_overwritten(client, writing):
    start_trip(client, writing)
    first = save(client, writing).json()
    save(client, writing, slug=first["slug"], version=first["version"], title="Changed on the laptop")
    r = save(client, writing, slug=first["slug"], version=first["version"], title="Changed on the phone")
    assert r.status_code == 409 and "Someone else changed" in r.json()["error"]
    assert "Changed on the laptop" in (writing.root / "sommer-in-lappland" / f"{first['slug']}.md").read_text()


def test_signed_out_or_signed_in_again(client, writing, monkeypatch):
    start_trip(client, writing)
    r = save(client, writing, csrf="from-before")
    assert r.status_code == 409 and r.json() == {"csrf": writing.session.csrf, "retry": True}
    monkeypatch.delitem(setup.SESSIONS, "sid")
    r = save(client, writing)
    assert r.status_code == 401 and r.json()["signed_out"] and "kept on this device" in r.json()["error"]
    assert not list((writing.root / "sommer-in-lappland").glob("2026-*.md"))


def test_only_json_from_the_editor_is_accepted(client, writing):
    start_trip(client, writing)
    r = client.post("/setup/f/falks/trips/sommer-in-lappland/save", content=b'{"csrf": "x"}',
                    headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_publishing_without_album_rights_says_what_to_do(client, writing):
    start_trip(client, writing)
    writing.immich.permissions = ["album.read"]
    r = save(client, writing, publish=True)
    assert r.status_code == 403 and "Connect it with Immich again" in r.json()["error"]
    assert not list((writing.root / "sommer-in-lappland").glob("2026-*.md"))  # nothing half-published
    assert "needs to be allowed to make albums" in client.get("/setup/f/falks/trips/").text


def test_handwritten_files_survive_the_editor(tmp_path):
    text = (f"---\ntitle: Reindeer farm\ndate: 2026-08-04T18:30\nplace: Kuusamo\nmood: happy\n---\n\n"
            f"The kids fed the reindeer.\nUntil the lichen ran out.\n\n"
            f'![Feeding time](immich:{P1}) ![](immich:{P2} "VID_2.MOV")\n\n- soup\n- sauna\n')
    path = tmp_path / "2026-08-04-reindeer.md"
    path.write_text(text)
    draft = journal.load_draft(path)
    assert [b["kind"] for b in draft.blocks] == ["text", "photos", "text"]
    assert draft.blocks[1]["photos"][1] == {"id": P2, "caption": "", "file": "VID_2.MOV"}
    again = journal.entry_text(draft.meta, draft.blocks)
    assert journal.raw_blocks(again.split("---\n", 2)[2]) == draft.blocks and "mood: happy" in again


def test_deleted_entries_go_to_the_trash(client, writing):
    start_trip(client, writing)
    slug = save(client, writing).json()["slug"]
    r = client.post(f"/setup/f/falks/trips/sommer-in-lappland/e/{slug}/delete", data={"csrf": writing.session.csrf})
    assert r.status_code == 303
    assert not (writing.root / "sommer-in-lappland" / f"{slug}.md").exists()
    assert len(list((writing.root / "sommer-in-lappland/.trash").glob(f"{slug}-*.md"))) == 1


def test_picker_shows_the_photos_of_the_day_where_they_were_taken(client, writing):
    start_trip(client, writing)
    r = client.get("/setup/f/falks/trips/sommer-in-lappland/photos", params={"mode": "day", "day": "2026-08-04"})
    assert r.json()["photos"] == [{"id": P1, "type": "IMAGE", "time": "2026-08-04T23:30", "place": "Kuusamo, Finland",
                                   "file": "IMG_1.HEIC"}]
    r = client.get("/setup/f/falks/trips/sommer-in-lappland/photos", params={"mode": "recent"})
    assert [p["id"] for p in r.json()["photos"]] == [P2, P1]


def test_trip_readers_can_be_changed(client, writing):
    start_trip(client, writing)
    r = client.post("/setup/f/falks/trips/sommer-in-lappland/settings",
                    data={"csrf": writing.session.csrf, "title": "Lappland 2026"})
    assert r.status_code == 303 and writing.immich.unshared == [("album-1", "user-oma")]
    assert journal.trip_file(writing.root / "sommer-in-lappland")[0]["title"] == "Lappland 2026"


def test_only_the_feeds_owner_writes(client, writing):
    writing.session.email = "someone@example.com"
    assert client.get("/setup/f/falks/trips/").status_code == 404
    assert start_trip(client, writing).status_code == 404
