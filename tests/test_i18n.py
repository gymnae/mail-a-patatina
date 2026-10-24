"""Languages: picked from the browser, remembered for emails, and complete for every supported language."""

import re
import time
from pathlib import Path

import pytest

from mail_a_patatina import i18n, links


@pytest.mark.parametrize("header, expected", [
    ("de-DE,de;q=0.9,en;q=0.8", "de"),
    ("fi", "fi"),
    ("fr-CH, fr;q=0.9, en;q=0.8, de;q=0.7", "fr"),
    ("ru-RU,ru;q=0.9,en-US;q=0.8", "ru"),
    ("uk-UA,uk;q=0.9,ru;q=0.8", "uk"),
    ("es-ES,es;q=0.9,de;q=0.5", "de"),  # the first language we have
    ("en;q=0.5,fi;q=0.8", "fi"),  # by weight, not position
    ("de;q=0", None),  # q=0 means "not this one"
    ("es", None),
    ("", None),
    (None, None),
])
def test_language_from_browser(header, expected):
    assert i18n.from_header(header) == expected


def test_dates():
    assert i18n.nice_date("2026-10-12T14:33:00") == "Monday, 12 October 2026"
    assert i18n.nice_date("2026-10-12", "de") == "Montag, 12. Oktober 2026"
    assert i18n.nice_date("2026-10-01", "fr") == "jeudi 1er octobre 2026"
    assert i18n.nice_date("2026-10-12", "fi") == "maanantai 12. lokakuuta 2026"
    assert i18n.nice_date("2026-10-12", "ru") == "понедельник, 12 октября 2026 г."
    assert i18n.nice_date("2026-10-16", "uk") == "п’ятниця, 16 жовтня 2026 р."
    assert i18n.month_year(i18n.dt.date(2026, 10, 1), "uk") == "Жовтень 2026"


def test_every_text_is_translated_with_the_same_placeholders():
    for english, translations in i18n._TEXTS.items():
        assert len(translations) == len(i18n.LANGUAGES) - 1, english
        placeholders = set(re.findall(r"{(\w+)}", english))
        for translated in translations:
            assert translated and set(re.findall(r"{(\w+)}", translated)) == placeholders, (english, translated)


def test_every_text_in_the_templates_has_a_translation():
    from mail_a_patatina import write

    root = Path(i18n.__file__).parent
    sources = [*root.glob("templates/*.*"), root / "templates/setup/join.html", *root.glob("templates/setup/write*.html"),
               root / "web.py", root / "setup.py", root / "mailer.py", root / "dispatch.py"]
    used = set(write.EDITOR_TEXTS)
    for path in sources:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'_\("((?:[^"\\]|\\.)*)"', text)) | set(re.findall(r"_\('((?:[^'\\]|\\.)*)'", text))
    # The editor's messages from the server: t(lang, "…" "…"), the text possibly split over several lines.
    for literals in re.findall(r't\(lang,\s*((?:"(?:[^"\\]|\\.)*"\s*)+)', (root / "write.py").read_text(encoding="utf-8")):
        used.add("".join(re.findall(r'"((?:[^"\\]|\\.)*)"', literals)))
    for literals in re.findall(r'fail\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)', (root / "write.py").read_text(encoding="utf-8")):
        used.add("".join(re.findall(r'"((?:[^"\\]|\\.)*)"', literals)))
    used = {u.replace("\\'", "'") for u in used if u not in ("title", "text", "error")}
    missing = sorted(u for u in used if u not in i18n._TEXTS)
    assert not missing


def test_page_in_the_browsers_language(client, family, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    r = client.get("/falks/", headers={"Accept-Language": "de-DE,de;q=0.9"})
    assert '<html lang="de">' in r.text and "Nicht Oma Erika?" in r.text
    assert "Kalender" in r.text and "Noch nichts geteilt." in r.text
    assert family.languages.get(oma.id) == "de"


def test_browser_without_a_language_of_ours_gets_the_default_and_nothing_is_remembered(client, family, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    r = client.get("/falks/", headers={"Accept-Language": "es"})
    assert '<html lang="en">' in r.text
    assert family.languages.get(oma.id) is None


def test_page_in_russian_and_ukrainian(client, settings, oma):
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")
    r = client.get("/falks/calendar?m=2026-10", headers={"Accept-Language": "ru"})
    assert '<html lang="ru">' in r.text and "Вы не Oma Erika?" in r.text and "Октябрь 2026" in r.text
    r = client.get("/falks/calendar?m=2026-10", headers={"Accept-Language": "uk-UA"})
    assert "Ви не Oma Erika?" in r.text and "Жовтень 2026" in r.text and ">нд<" in r.text


def test_messages_are_translated(make_client):
    r = make_client().get("/falks/", headers={"Accept-Language": "fi"})
    assert r.status_code == 403 and "Yksityinen perhealbumi" in r.text


def test_new_link_email_in_the_owners_remembered_language(client, family, settings, oma):
    family.languages.remember(oma.id, "fr")
    payload = f"u.falks.{oma.id}.{int(time.time()) - 5}"
    expired = f"{payload}.{links._sign(settings, payload)}"
    r = client.post("/falks/renew", data={"t": expired}, headers={"Accept-Language": "de"})
    assert "Ein neuer Link ist unterwegs" in r.text  # the page follows the browser
    assert family.sent[0]["Subject"] == "Die Falks : votre nouveau lien"  # the email follows Oma


def test_remembered_languages_survive_a_restart(tmp_path):
    path = tmp_path / "languages.json"
    i18n.Remembered(path).remember("user-1", "fi")
    assert i18n.Remembered(path).get("user-1") == "fi"


def test_lost_language_cache_is_harmless(tmp_path):
    (tmp_path / "languages.json").write_text("not json")
    assert i18n.Remembered(tmp_path / "languages.json").get("user-1") is None


def test_daily_email_in_the_relatives_language(settings):
    from types import SimpleNamespace as NS

    from mail_a_patatina import mailer
    from mail_a_patatina.library import Entry

    entries = [Entry("a1", "o", "IMAGE", "2026-10-12T10:00:00", "Erster Schultag", "", {}),
               Entry("a2", "o", "VIDEO", "2026-10-13T10:00:00", "", "", {})]
    target = NS(name="Oma Erika", address="oma@example.com", lang="de",
                token=links.make_token(settings, "falks", "u", "user-oma"))
    cfg = NS(id="falks", title="Die Falks", smtp_from="Die Falks <f@example.com>")
    msg = mailer.build_digest(settings, cfg, target, entries, {})
    assert msg["Subject"] == "Die Falks: Erster Schultag (+1 weitere)" and msg["Content-Language"] == "de"
    text = msg.get_body(("plain",)).get_content()
    assert "Guten Morgen, Oma Erika!" in text and "Montag, 12. Oktober 2026" in text
    html = msg.get_body(("html",)).get_content()
    assert '<html lang="de">' in html and "Kommentieren" in html
