"""Signing in with magic links on the website: expiry, fresh links by email, "Not you?"."""

import time

from mail_a_patatina import links


def expired_link(settings, oma):
    payload = f"u.falks.{oma.id}.{int(time.time()) - 5}"
    return f"{payload}.{links._sign(settings, payload)}"


def test_link_signs_in_with_a_session_not_the_link(client, settings, oma):
    token = links.make_token(settings, "falks", "u", oma.id)
    r = client.get(f"/falks/?t={token}")
    assert r.status_code == 303 and r.headers["location"] == "/falks/"
    cookie = client.cookies.get("map_t")
    assert cookie != token and links.read_session(settings, cookie).ident == oma.id
    r = client.get("/falks/")
    assert r.status_code == 200 and "Signed in as <b>Oma Erika</b>" in r.text


def test_expired_link_signs_nobody_in_and_offers_a_new_one(client, settings, oma):
    r = client.get(f"/falks/e/abc?t={expired_link(settings, oma)}")
    assert r.status_code == 403
    assert "Email me a new link" in r.text and "o***@example.com" in r.text
    assert "map_t" not in client.cookies


def test_new_link_goes_only_to_the_owner_and_not_too_often(client, family, settings, oma):
    expired = expired_link(settings, oma)
    r = client.post("/falks/renew", data={"t": expired, "next": "/falks/e/abc"})
    assert r.status_code == 200 and len(family.sent) == 1
    assert family.sent[0]["To"] == oma.email
    assert "http://testserver/falks/e/abc?t=" in family.sent[0].get_content()
    client.post("/falks/renew", data={"t": expired})
    assert len(family.sent) == 1


def test_new_link_never_points_elsewhere(client, family, settings, oma):
    client.post("/falks/renew", data={"t": expired_link(settings, oma), "next": "//evil.example/x"})
    assert "http://testserver/falks/?t=" in family.sent[0].get_content()


def test_expired_link_on_a_signed_in_device_just_goes_in(client, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    r = client.get(f"/falks/?t={expired_link(settings, oma)}")
    assert r.status_code == 303 and r.headers["location"] == "/falks/"


def test_forged_link(client, settings, oma):
    token = links.make_token(settings, "falks", "u", oma.id)
    r = client.get(f"/falks/?t={token[:-2]}zz")
    assert r.status_code == 403 and "no longer valid" in r.text


def test_link_of_someone_removed_from_the_album(client, family, settings, oma):
    token = links.make_token(settings, "falks", "u", oma.id)
    family.feed.members.clear()
    assert client.get(f"/falks/?t={token}").status_code == 403


def test_cookie_from_before_sessions_is_replaced(make_client, settings, oma):
    payload = f"u.falks.{oma.id}"
    old = make_client(cookies={"map_t": f"{payload}.{links._sign(settings, payload)}"})
    r = old.get("/falks/")
    assert r.status_code == 200 and "map_t=" in r.headers.get("set-cookie", "")


def test_link_token_is_no_cookie(make_client, settings, oma):
    client = make_client(cookies={"map_t": links.make_token(settings, "falks", "u", oma.id)})
    assert client.get("/falks/").status_code == 403


def test_unsubscribe_works_with_an_expired_link(client, settings, oma):
    r = client.get(f"/falks/unsubscribe?t={expired_link(settings, oma)}")
    assert r.status_code == 200 and oma.email in r.text


def test_not_me_signs_out_and_asks_the_family(client, family, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    assert "Not Oma Erika?" in client.get("/falks/not-me").text
    r = client.post("/falks/not-me", data={"name": "Tante Ute", "email": "Ute@Example.com"})
    assert r.status_code == 200
    request = family.saved[-1]["join_requests"][0]
    assert request["email"] == "ute@example.com" and request["via"] == "Oma Erika"
    assert "map_t" not in client.cookies
    assert client.get("/falks/").status_code == 403


def test_not_me_just_signing_out(client, family, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    client.post("/falks/not-me", data={})
    assert not family.saved and "map_t" not in client.cookies
