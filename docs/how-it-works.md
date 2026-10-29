# How it works

Mail-A-Patatina only **transports, presents and collects**. It has no database: everything it knows comes from, or is written back to, Immich.

```
             Immich = the single source of truth                 Mail-A-Patatina (stateless)
 ┌────────────────────────────────────────────────────────┐     ┌──────────────────────────────┐
 │ album "Family Feed"                                    │     │ daily email (your SMTP)      │
 │   photos + description (= caption)       ──────────────┼────►│ WhatsApp / Signal / Telegram │
 │   shared with Oma, Uncle Tom… (= recipients)           │     │ feed · calendar · photo page │
 │   ❤️ likes and comments, per person      ◄─────────────┼─────│ (magic links, no passwords)  │
 │   asset metadata "mail-a-patatina" (= what was sent)   │     └──────────────────────────────┘
 │   each person's email setting (= unsubscribe)          │
 └────────────────────────────────────────────────────────┘
```

| What | Where it lives |
|---|---|
| Photos, captions, dates | Immich assets and their description |
| What's shared | One album (photos with a description, if `REQUIRE_CAPTION=true`) |
| Who gets email | The people the album is shared with (not the owner, the co-parents or the bot) |
| Likes and comments | Immich album activity, posted as each relative's own Immich account |
| What was already sent, and to whom | Immich asset metadata, key `mail-a-patatina` |
| Unsubscribed | The relative's own Immich *email notifications* setting |
| Families, chats, SMTP, schedule | Configuration: `.env` and `config/feeds.yml` or `config/chats.yml` |
| The relatives' API keys | Credentials: `./secrets/relatives.yml`, next to `.env` |

`./data` only holds a cache of pictures (Immich's own previews for the website, JPEGs for emails) and each relative's last browser language, and is safe to delete. Restarting or rebuilding the container loses nothing.

## Sharing a moment

Add a photo to the album and write its description in Immich; both work in the mobile app. Writing the caption is what publishes it; uncaptioned photos are never sent. At `SEND_TIME` (default 07:00), everything new goes out. Until then, or for at least `SETTLE_MINUTES`, you can still fix the caption. To pull something back, remove it from the album or clear its caption. Each photo is sent only once, whatever is edited later, and photos already in the album when a family starts are never sent (unless `SEND_BACKLOG=true`).

## Relatives are Immich users

Each relative gets a viewer-only Immich account that the album is shared with. When it's set up, `add-relative` creates an API key *for that account* that can only like, comment, take back its own like or comment, and read or change its email setting. It saves the key in `./secrets/relatives.yml`. Mail-A-Patatina uses that key to like and comment as the person, so their ❤️ and comments show up natively in your Immich app, and your replies in Immich appear on their page too. Relatives never need to know their account exists. This works on OAuth-only servers too: `add-relative` switches password login on for the few seconds it needs, because signing in once is the only way Immich lets anyone create a key for a user.

## Magic links

Every link in the email signs the relative in, using an HMAC-signed token checked against the album's member list:
- Tapping ❤️ in the email likes the photo. Tapping it again on the page takes the like back, and relatives can delete their own comments.
- Tapping **Comment** opens the photo page, ready to type.
- The token is removed from the address bar right away. The browser gets a sign-in cookie of its own instead, valid for `SESSION_DAYS` (default 90) and renewed with every visit.
- Links expire after `LINK_DAYS` (default 7; links in chats after `CHAT_LINK_DAYS`, default 30), so a forwarded email soon stops working. Relatives get fresh links with every email, and an expired link offers **Email me a new link**, which only ever goes to the relative's own address. Unsubscribing works with expired links too.
- Removing someone from the album in Immich kills their link and sign-in.
- Bumping `LINK_VERSION` kills all links and sign-ins at once.
- The ❤️ is only saved by an auto-submitted form on the page, so email security scanners that pre-open links can't like on someone's behalf.

## Forwarded emails

Within its few days, a forwarded link signs the reader in as the relative it was meant for. The comment box therefore says whose name a comment goes out under, and every page ends with **Not Oma Erika?**, which signs the reader out and lets them ask for emails of their own: the request shows up on the family's setup page under *People* (the family is emailed), and **add** treats it like adding a relative. For families defined in `feeds.yml`, the request is emailed to `admin_emails` with the `add-relative` command.

## Group chats

A link posted in a WhatsApp, Signal or Telegram group is shared by everyone in it:
- Visitors are asked for their name once. They comment through one bot account (`BOT_NAME`), shown in Immich as `[Tante Ute] Hi!` and on the page as *Tante Ute*.
- Immich allows one ❤️ per account, so a group has one shared ❤️.
- A one-to-one chat can instead be tied to a relative (`person:` in the chat's config), so its links sign in as them.

## Keeping photos on the server

On by default; each family can switch it off on the setup page, and `KEEP_PHOTOS_ON_SERVER=false` changes the default. Nothing is attached to emails or chat messages, so there's no photo file in anyone's mailbox or chat to forward or save:
- **Emails** load each photo from `PUBLIC_URL` through a link made for that one recipient. The link signs nobody in. It stops working after `PHOTO_LINK_DAYS` (default 30), and right away if the photo is removed from the album or loses its caption, or the recipient is removed.
- **The pages** offer no one-tap saving: long-pressing a photo on a phone or right-clicking it doesn't bring up "Save image", videos have no download button or "Save video as…", and iOS can't lift the photo out as a sticker.
- **Chats** get the caption and the link only. Telegram's link preview is switched off; the pages carry no preview image for WhatsApp or Signal to pick up.
- **What it doesn't prevent:** screenshots, saving the photo while it's displayed, or copies made by mail services that fetch images through their own proxy (Gmail does, and Apple Mail's privacy protection) while the link is valid. Some mail apps block remote images until the reader taps "load images"; the caption and buttons show either way. It makes photos much harder to pass around, not impossible.

## Languages

Everything relatives see is in English, German, French, Finnish, Russian or Ukrainian: the pages, messages, the daily email, chat captions and the invite page for connecting their own Immich account. The parents' setup page is in English.
- **Pages** follow the visitor's browser or phone language. Other languages get `DEFAULT_LANGUAGE` (default `en`).
- **Emails** have no browser to ask, so each relative gets them in the language their browser showed on their last visit (kept in `./data/languages.json`). Before their first visit, or if that file is lost, `DEFAULT_LANGUAGE` applies.
- **Group-chat captions** are in `DEFAULT_LANGUAGE`; one-to-one chats tied to a relative follow that relative.

Captions and comments are what the family writes and aren't translated. New texts go into `mail_a_patatina/i18n.py`; a test fails if a page uses a text that has no translation.

## Only Immich's reactions

Immich has exactly two kinds of activity: like (❤️) and comment. The site offers just those.

## What gets sent

Each photo is marked in Immich when it's sent to each recipient and chat:
- Failed sends are retried at the next run.
- Someone newly added to the album catches up on the last `RESEND_HOURS` (default 48).
- When Mail-A-Patatina first starts on an album, photos already there count as backlog: they show on the website but aren't sent (`SEND_BACKLOG=true` sends them).
- `SEND_MODE=drip` sends one photo per day from the queue instead of a digest.

## Several families on one installation

One Mail-A-Patatina can serve several Immich users who don't know each other. Families usually set themselves up on the [setup page](families.md); the admin can also define them in a file. Each family is a **feed**: one album, its owner's API key, its own title, send time, timezone, chats and bot account, listed in `config/feeds.yml` (see `feeds.example.yml`). With a single family you can skip that file and put `ALBUM_ID` and `IMMICH_API_KEY` in `.env`.

How families are kept apart:
- **Separate pages:** each family's pages live under `PUBLIC_URL/<feed id>/`, and the sign-in cookie only applies to that path.
- **Links can't cross over:** magic-link tokens are signed together with their feed id, so a link from the Müllers' email is rejected on the Schmidts' pages, even by someone who rewrites the URL.
- **Every request is checked against one album:** photos, likes and comments are only served if they belong to that feed's album, through that album owner's key.
- **Separate emails:** a grandma who is in two families' albums has one Immich account, gets two separate emails and has two separate links. Unsubscribing is her own Immich email setting, so it stops both.

Because the families' API keys sit in `config/feeds.yml`, whoever runs Mail-A-Patatina can read those families' libraries. In practice that's the Immich admin, who can see everything on the server anyway.
