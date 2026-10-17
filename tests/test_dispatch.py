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
