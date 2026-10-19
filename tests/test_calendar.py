"""Calendar: a day with one moment opens it, a day with several opens that day's feed."""

from mail_a_patatina import links
from mail_a_patatina.library import Entry


def moment(asset_id, taken_at, caption=""):
    return Entry(asset_id=asset_id, owner_id="u", type="IMAGE", taken_at=taken_at, caption=caption, updated_at="")


def test_busy_day_opens_the_days_feed(client, family, settings, oma):
    family.feed.library.replace([
        moment("a", "2026-10-03T09:00:00"),
        moment("b", "2026-10-12T08:00:00", "breakfast"),
        moment("c", "2026-10-12T17:30:00", "playground"),
        moment("d", "2026-10-20T12:00:00"),
    ])
    client.get(f"/falks/?t={links.make_token(settings, 'falks', 'u', oma.id)}")

    r = client.get("/falks/calendar?m=2026-10")
    assert 'href="/falks/day/2026-10-12"' in r.text and 'href="/falks/e/a"' in r.text

    r = client.get("/falks/day/2026-10-12")
    assert r.status_code == 200
    assert r.text.index("breakfast") < r.text.index("playground")  # the day as it happened
    assert 'href="/falks/e/a"' not in r.text and 'href="/falks/e/d"' not in r.text
    assert 'href="/falks/day/2026-10-03"' in r.text and 'href="/falks/day/2026-10-20"' in r.text
    assert "Monday, 12 October 2026" in r.text

    assert client.get("/falks/day/2026-10-13").status_code == 404
    assert client.get("/falks/day/nonsense").status_code == 404
