"""Shared fixtures: settings and a stubbed core (one family, one relative), so the website can be tested
without Immich or SMTP."""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from mail_a_patatina import web
from mail_a_patatina.library import Library


@pytest.fixture
def settings():
    return NS(secret_key="x" * 40, link_version="1", link_days=7, chat_link_days=30, session_days=90,
              public_url="http://testserver", secure_cookies=False, smtp_host="smtp", photo_link_days=30)


@pytest.fixture
def oma():
    return NS(id="user-oma", name="Oma Erika", email="oma@example.com", role="viewer")


@pytest.fixture
def family(settings, oma, monkeypatch):
    """The stubbed core behind the website. `.sent` collects emails, `.saved` what the setup store saved."""
    async def no_activities():
        return []

    cfg = NS(id="falks", title="Die Falks", keep_photos_on_server=True, chats=[], bot_email="bot@x.invalid",
             smtp_from="Die Falks <f@example.com>", admin_emails=[], album_id="album")
    feed = NS(id="falks", cfg=cfg, members={oma.id: oma}, bot=None, library=Library(), activities=no_activities,
              by_email=lambda email: oma if email == oma.email else None)
    saved, sent = [], []

    async def send(_settings, msg):
        sent.append(msg)

    store = NS(get=lambda feed_id: {"id": feed_id, "owner_email": "mama@example.com"}, save=saved.append)
    monkeypatch.setattr(web, "core", NS(settings=settings, feeds={"falks": feed}, store=store,
                                        people=NS(display_name=lambda name: name)), raising=False)
    monkeypatch.setattr(web.mailer, "send", send)
    monkeypatch.setattr(web, "RENEWED", {})
    return NS(feed=feed, saved=saved, sent=sent)


@pytest.fixture
def client(family):
    return TestClient(web.app, follow_redirects=False)


@pytest.fixture
def make_client(family):
    return lambda **kwargs: TestClient(web.app, follow_redirects=False, **kwargs)
