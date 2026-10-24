# Trips

Next to the daily feed, a family can keep a travel journal: one page per trip, entries with text and photos, to look back on years later. It follows a different rule from the feed on purpose:

- **The text lives in Markdown files**, not in Immich and not in a database. They're plain files you can read, edit, copy, put in Git or turn into a printed photo book without Mail-A-Patatina.
- **The photos and videos stay in Immich.** Entries point to them by their Immich id.
- **Each trip is one Immich album.** The people it's shared with may read the trip. Their likes and comments on its photos are that album's Immich activity, posted as their own accounts like on the feed, so you see them in the Immich app. They stay apart from the feed's: a photo that's in both the feed and a trip has two separate sets of likes and comments.
- **The text itself has no likes or comments**, only its photos do.

Relatives use the links from their feed emails; there's nothing new to sign in to. The **Trips** menu shows up once at least one trip's album is shared with them.

## Writing

Open the setup page (`PUBLIC_URL/setup/`), sign in as usual and tap **Write in the journal**. It works on a phone; add it to your home screen to have it at hand. The editor is in your phone's language.

1. **New trip:** give it a name and tick who may read it. An album for the trip is made in Immich and shared with them; the other parents get it as editors, so they can add photos too. If you already made an album for the trip, you can pick it instead.
2. **New entry:** a title, the date (today, unless you change it), the place, and then text and photos in any order: **+ Text** adds a paragraph, **+ Photos** opens the photo picker. ↑ and ↓ move a part, ✕ removes it.
3. **Picking photos:** the picker opens on the photos taken on the entry's day, wherever they are in your library. **Recent** shows your latest photos, **Album** what's already in the trip's album (e.g. photos the other parent added there). Tap to choose, then **Add photos**. Photos show up once the Immich app on your phone has backed them up. The place is filled in from where the photos were taken, if you left it empty.
4. **Save as draft** keeps it for you only. **Publish** shows it to the family and adds its photos to the trip's album, so they can like and comment on them.

Nothing you type gets lost: until it's saved, the editor also keeps it in your phone's browser. If the connection drops or you were signed out, it's still there when you come back. If someone else changed the same entry in the meantime, saving tells you instead of overwriting their version. Deleted entries go to a `.trash` folder in the trip's folder.

Writing needs the album owner's key to be allowed to make albums and add photos to them (`album.create`, `albumAsset.create`). Feeds set up before trips existed are asked to connect with Immich once more; it takes one tap. Only families set up on the setup page can use the editor; families in `feeds.yml` write the files by hand.

## Files

The editor writes these files; you can also write or change them with any text editor, they stay as you wrote them.

```
journal/                          # JOURNAL_DIR, mounted from ./journal
  falks/                          # one folder per family, named like its feed (the part after PUBLIC_URL/)
    2026-08-lapland/              # one folder per trip; the name appears in links
      trip.md                     # the trip
      2026-08-03-arrival.md       # one file per entry
      2026-08-04-reindeer-farm.md
```

`trip.md`:

```markdown
---
title: Lapland
album_id: 6b0c2b9e-…          # the trip's Immich album (from its address in Immich: /albums/<id>)
cover: 8f1c…e2a               # optional: the photo shown on the list of trips (else the first one)
---
Two weeks up north, with a lot of reindeer.
```

An entry:

```markdown
---
date: 2026-08-04T18:30        # optional if the file name starts with the date
title: Reindeer farm          # optional
place: Kuusamo, Finland       # optional
---
The kids fed the reindeer until they ran out of lichen.

![Feeding time](immich:8f1c…e2a "IMG_4412.HEIC")
![](immich:51d0…77c)

Dinner was salmon soup.
```

- **Photos and videos** are `![what it shows](immich:<asset id>)`, on lines of their own. Several on one line show side by side. The text in `[…]` is the photo's caption; the optional `"…"` is for you (e.g. the original file name, so the file still says which photo was meant if Immich is ever rebuilt). The asset id is the last part of the photo's address in Immich.
- **Only photos in the trip's album are shown.** Others are left out (and logged), because nobody could like or comment on them.
- **The text is Markdown**: paragraphs, **bold**, *italics*, lists, links, quotes. Line breaks are kept. HTML isn't: it shows as typed.
- **Entries** with `draft: true` are only shown in the editor. Entries are sorted by date. Folder and file names may contain letters, digits, `-` and `_`.
- What the editor saves shows up right away; changes made to the files elsewhere, or to the albums in Immich, with the next sync (every `SYNC_MINUTES`).

## Who sees a trip

Whoever the trip's album is shared with in Immich, among the people who get the family's feed. To share a trip with fewer relatives, share its album with fewer; to stop sharing, take them out of the album. Group chats can read a trip if the feed's chat account (`BOT_NAME`) is in its album.

## Coming next

- "On this day": what happened on today's date in earlier trips.
- Emailing relatives when there's a new entry.
