"""Who gets the morning email."""

import asyncio
from types import SimpleNamespace as NS

import httpx

from mail_a_patatina import dispatch
from mail_a_patatina.people import Member


def test_one_relatives_trouble_does_not_stop_the_others(settings, tmp_path):
    oma = Member("user-oma", "Oma Erika", "oma@example.com", "viewer")
    tom = Member("user-tom", "Tom", "tom@example.com", "viewer", managed=False)  # own account, key lacks a permission

    async def wants_email(member):
        if member is tom:
            raise httpx.HTTPStatusError("403", request=httpx.Request("GET", "http://immich"), response=httpx.Response(403))
        return True

    feed = NS(id="falks", cfg=NS(chats=[]), relatives=lambda: [oma, tom],
              core=NS(settings=settings, people=NS(wants_email=wants_email), languages=NS(get=lambda _: None)))
    targets = asyncio.run(dispatch.targets(feed))
    assert [t.address for t in targets] == ["oma@example.com"]


def test_coparents_get_no_daily_email():
    from mail_a_patatina.core import Feed

    curator, alona_key = object(), object()
    owner = Member("user-anna", "Anna", "anna@example.com", "owner", managed=False)
    oma = Member("user-oma", "Oma Erika", "oma@example.com", "viewer")
    alona = Member("user-alona", "Alona", "alona@example.com", "editor", managed=False)  # connected co-parent
    waiting = Member("user-ben", "Ben", "ben@example.com", "viewer", managed=False)  # photos in the album, no key yet
    feed = NS(members={m.id: m for m in (owner, oma, alona, waiting)}, cfg=NS(bot_email="bot@x.invalid"),
              curator=curator, writers={owner.id: curator, alona.id: alona_key}, unkeyed={waiting.id})
    feed.others = lambda: Feed.others(feed)
    feed.is_coparent = lambda m: Feed.is_coparent(feed, m)
    assert [m.name for m in Feed.relatives(feed)] == ["Oma Erika"]
