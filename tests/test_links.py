"""Magic-link tokens, sign-in sessions and photo tickets."""

import time

from mail_a_patatina import links


def signed(settings, payload, purpose=""):
    return f"{payload}.{links._sign(settings, purpose + payload)}"


def test_fresh_link(settings):
    token = links.make_token(settings, "falks", "u", "user-1")
    link = links.read_token(settings, token)
    assert link.feed_id == "falks" and link.kind == "u" and link.ident == "user-1" and not link.expired
    assert 6 * 86400 < link.expires - time.time() <= 7 * 86400


def test_chat_links_last_longer(settings):
    link = links.read_token(settings, links.make_token(settings, "falks", "c", "abc"))
    assert link.expires - time.time() > 29 * 86400


def test_expired_link_still_says_whose(settings):
    link = links.read_token(settings, signed(settings, f"u.falks.user-1.{int(time.time()) - 5}"))
    assert link.expired and link.ident == "user-1"


def test_link_from_before_expiry_counts_as_expired(settings):
    link = links.read_token(settings, signed(settings, "u.falks.user-1"))
    assert link.expired and link.ident == "user-1"


def test_forged_link(settings):
    token = links.make_token(settings, "falks", "u", "user-1")
    assert links.read_token(settings, token[:-2] + "zz") is None
    assert links.read_token(settings, token.replace("user-1", "user-2")) is None


def test_link_and_session_cannot_stand_in_for_each_other(settings):
    token = links.make_token(settings, "falks", "u", "user-1")
    session = links.make_session(settings, "falks", "u", "user-1")
    assert links.read_session(settings, token) is None
    assert links.read_token(settings, session) is None
    assert links.read_session(settings, session).expires > time.time() + 89 * 86400


def test_expired_session(settings):
    assert links.read_session(settings, signed(settings, f"u.falks.user-1.{int(time.time()) - 5}", "session.")) is None


def test_cookie_from_before_sessions_is_accepted_and_due_for_renewal(settings):
    session = links.read_session(settings, signed(settings, "u.falks.user-1"))
    assert session.ident == "user-1" and session.expires == 0


def test_photo_ticket(settings):
    url = links.photo_url(settings, "falks", "asset", links.make_token(settings, "falks", "u", "user-1"))
    ticket = url.rsplit("/", 1)[1].removesuffix(".jpg")
    assert links.read_photo_ticket(settings, "falks", "asset", ticket) == ("u", "user-1")
    assert links.read_photo_ticket(settings, "falks", "other-asset", ticket) is None
