"""Trips: Markdown files for the text, the trip's Immich album for the photos, the readers, likes and comments."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from mail_a_patatina import journal, links, web
from mail_a_patatina.journal import Journal

A1, A2, A3 = (f"{n}" * 8 + "-1111-2222-3333-444444444444" for n in "abc")
OUTSIDE = "d" * 8 + "-1111-2222-3333-444444444444"  # a photo that isn't in the trip's album


class FakeImmich:
    """The album owner's connection, and the relatives' (whatever they post lands in `activities`)."""

    def __init__(self, album_users, assets):
        self.album_users, self.assets = album_users, assets
        self.activities_by_album: dict[str, list[dict]] = {}
        self.calls = []

    async def album(self, album_id):
        return {"albumUsers": self.album_users}

    async def search(self, albumIds):
        for a in self.assets:
            yield a

    async def activities(self, album_id):
        return self.activities_by_album.get(album_id, [])

    def acting_as(self, member):
        immich = self

        class Relative:
            async def add_activity(self, album_id, asset_id, kind, comment=None):
                immich.calls.append(("add", album_id, asset_id, kind, comment))
                immich.activities_by_album.setdefault(album_id, []).append({
                    "id": f"act-{len(immich.calls)}", "assetId": asset_id, "type": kind, "comment": comment,
                    "user": {"id": member.id, "name": member.name}})

            async def delete_activity(self, activity_id):
                immich.calls.append(("delete", activity_id))
                for items in immich.activities_by_album.values():
                    items[:] = [a for a in items if a["id"] != activity_id]

        return Relative()


def asset(asset_id, kind="IMAGE", live=""):
    return {"id": asset_id, "type": kind, "localDateTime": "2026-08-04T18:30:00.000Z", "updatedAt": "u1",
            "livePhotoVideoId": live}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def lapland(tmp_path):
    root = tmp_path / "falks"
    write(root / "2026-08-lapland/trip.md", "---\ntitle: Lapland\nalbum_id: trip-album\n---\nTwo weeks up north.\n")
    write(root / "2026-08-lapland/2026-08-04-reindeer.md", f"""---
date: 2026-08-04T18:30
title: Reindeer farm
place: Kuusamo
---
The kids fed the **reindeer**.
Until the lichen ran out.

![Feeding time]({"immich:" + A1}) ![](immich:{A2} "IMG_4412.HEIC")

<script>alert(1)</script> Dinner was salmon soup.

![Not in the album](immich:{OUTSIDE})
""")
    write(root / "2026-08-lapland/2026-08-03-arrival.md", f"We made it!\n\n![Arrival](immich:{A3})\n")
    write(root / "2026-08-lapland/notes.md", "No date anywhere: left out.\n")
    write(root / "no-album/trip.md", "---\ntitle: Forgot the album\n---\n")
    write(root / "has space/trip.md", "---\nalbum_id: x\n---\n")
    return root


def test_trips_are_read_from_markdown(lapland):
    trips = journal.read_trips(lapland)
    assert [t.slug for t in trips] == ["2026-08-lapland"]  # no album, or a name that can't be a link: left out
    trip = trips[0]
    assert trip.title == "Lapland" and trip.album_id == "trip-album" and "Two weeks up north." in trip.intro
    assert [p.slug for p in trip.posts] == ["2026-08-03-arrival", "2026-08-04-reindeer"]  # oldest first
    arrival, reindeer = trip.posts
    assert arrival.when == "2026-08-03T00:00:00"  # from the file name
    assert reindeer.when == "2026-08-04T18:30:00" and reindeer.title == "Reindeer farm" and reindeer.place == "Kuusamo"

    kinds = [kind for kind, _ in reindeer.blocks]
    assert kinds == ["text", "media", "text", "media"]  # in the order they were written
    assert "<strong>reindeer</strong>.<br />" in reindeer.blocks[0][1]
    assert [(m.asset_id, m.caption) for m in reindeer.blocks[1][1]] == [(A1, "Feeding time"), (A2, "")]
    assert "<script>" not in reindeer.blocks[2][1] and "&lt;script&gt;" in reindeer.blocks[2][1]
    assert reindeer.excerpt == "The kids fed the reindeer. Until the lichen ran out."


def test_only_photos_in_the_trips_album_are_shown(lapland, oma):
    curator = FakeImmich([{"user": {"id": oma.id}}], [asset(A1), asset(A2, "VIDEO"), asset(A3, live="lv")])
    feed = NS(id="falks", curator=curator, core=NS(people=NS(members=lambda users: {u["user"]["id"]: oma for u in users}),
                                                   media=NS(purge=lambda _: None)))
    j = Journal(lapland)
    asyncio.run(j.refresh(feed))
    trip = j.trips["2026-08-lapland"]
    reindeer = trip.post("2026-08-04-reindeer")
    assert [m.asset_id for m in reindeer.media] == [A1, A2]
    assert [kind for kind, _ in reindeer.blocks] == ["text", "media", "text"]  # the outside photo's line is gone
    assert reindeer.media[1].type == "VIDEO" and trip.post("2026-08-03-arrival").media[0].live_video_id == "lv"
    assert trip.readable_by(oma) and not trip.readable_by(NS(id="someone-else"))
    assert trip.cover[1].asset_id == A3 and j.visible_to(oma) == [trip] and j.visible_to(None) == []


@pytest.fixture
def signed_in(client, family, settings, oma, lapland, monkeypatch):
    immich = FakeImmich([{"user": {"id": oma.id}}], [asset(A1), asset(A2), asset(A3)])
    family.feed.curator = immich
    family.feed.core = NS(people=NS(members=lambda users: {u["user"]["id"]: oma for u in users}),
                          media=NS(purge=lambda _: None))
    async def notify_admins(*_):
        pass

    family.feed.notify_admins = notify_admins
    family.feed.journal = Journal(lapland)
    asyncio.run(family.feed.journal.refresh(family.feed))

    async def run_as(member, action):
        return await action(immich.acting_as(member))

    monkeypatch.setattr(web.core.people, "run_as", run_as, raising=False)
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    return immich


def test_trip_pages(client, signed_in):
    r = client.get("/falks/")
    assert 'href="/falks/trips/"' in r.text  # Oma may read a trip, so the menu offers trips

    r = client.get("/falks/trips/")
    assert "Lapland" in r.text and "August 2026" in r.text and 'href="/falks/trips/2026-08-lapland/"' in r.text

    r = client.get("/falks/trips/2026-08-lapland/")
    assert r.text.index("Monday, 3 August 2026") < r.text.index("Reindeer farm")
    assert "Two weeks up north." in r.text and "The kids fed the reindeer" in r.text

    r = client.get("/falks/trips/2026-08-lapland/2026-08-04-reindeer")
    assert r.status_code == 200 and "Kuusamo" in r.text and f'id="m-{A1}"' in r.text
    assert f"/falks/trips/2026-08-lapland/2026-08-04-reindeer/{A1}/preview" in r.text
    assert OUTSIDE not in r.text and "&lt;script&gt;" in r.text
    assert 'href="/falks/trips/2026-08-lapland/2026-08-03-arrival"' in r.text  # the entry before

    r = client.get(f"/falks/trips/2026-08-lapland/2026-08-04-reindeer/{A1}")
    assert r.status_code == 200 and "Feeding time" in r.text
    assert f'action="/falks/trips/2026-08-lapland/2026-08-04-reindeer/{A1}/comment"' in r.text


def test_trip_is_hidden_from_who_its_album_isnt_shared_with(client, signed_in, family):
    family.feed.journal.trips["2026-08-lapland"].members = {}
    assert 'href="/falks/trips/"' not in client.get("/falks/").text
    assert "Lapland" not in client.get("/falks/trips/").text
    assert client.get("/falks/trips/2026-08-lapland/").status_code == 404
    assert client.get("/falks/trips/2026-08-lapland/2026-08-04-reindeer").status_code == 404


def test_only_photos_of_the_entry_can_be_opened(client, signed_in):
    base = "/falks/trips/2026-08-lapland/2026-08-04-reindeer"
    assert client.get(f"{base}/{A3}").status_code == 404  # in the album, but part of another entry
    assert client.get(f"{base}/{OUTSIDE}/preview").status_code == 404
    assert client.get(f"{base}/{A1}/original").status_code == 404
    assert client.post(f"{base}/{OUTSIDE}/like").status_code == 404


def test_likes_and_comments_go_to_the_trips_album(client, signed_in):
    base = f"/falks/trips/2026-08-lapland/2026-08-04-reindeer/{A1}"
    r = client.post(f"{base}/like", data={"back": "post"})
    assert r.status_code == 303 and r.headers["location"] == f"/falks/trips/2026-08-lapland/2026-08-04-reindeer#m-{A1}"
    r = client.post(f"{base}/comment", data={"body": "So cute!"})
    assert r.headers["location"] == f"{base}#comments"
    assert signed_in.calls == [("add", "trip-album", A1, "like", None), ("add", "trip-album", A1, "comment", "So cute!")]

    r = client.get(base)
    assert "So cute!" in r.text and "Oma Erika" in r.text and 'aria-pressed="true"' in r.text
    assert "So cute!" not in client.get(f"/falks/trips/2026-08-lapland/2026-08-04-reindeer/{A2}").text

    comment_id = signed_in.activities_by_album["trip-album"][1]["id"]
    r = client.post(f"{base}/comments/{comment_id}/delete")
    assert r.status_code == 303 and "So cute!" not in client.get(base).text
    client.post(f"{base}/like")  # toggles it off again
    assert signed_in.activities_by_album["trip-album"] == []
