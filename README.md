<p align="center"><img src="assets/logo.svg" alt="A little potato peeking out of an envelope" width="180"></p>

<h1 align="center">mail-a-patatina</h1>

A self-hosted, ad-free take on Tinybeans for [Immich](https://immich.app). Each morning, relatives get an email with the newest captioned photos from a shared Immich album. They tap ❤️ or comment without passwords, and every like and comment lands in Immich under their own name. The same moments can also go to WhatsApp, Signal or Telegram chats.

mail-a-patatina only **transports, presents and collects**. It has no database: everything it knows comes from, or is written back to, Immich.

```
             Immich = the single source of truth                 mail-a-patatina (stateless)
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
| Who gets email | The people the album is shared with (not the owner, not the bot) |
| Likes and comments | Immich album activity, posted as each relative's own Immich account |
| What was already sent, and to whom | Immich asset metadata, key `mail-a-patatina` |
| Unsubscribed | The relative's own Immich *email notifications* setting |
| Families, chats, SMTP, schedule | Configuration: `.env` and `config/feeds.yml` or `config/chats.yml` |
| The relatives' API keys | Credentials: `./secrets/relatives.yml`, next to `.env` |

`./data` only holds a cache of resized JPEGs and is safe to delete. Restarting or rebuilding the container loses nothing.

## How it works

**Sharing a moment.** Add a photo to the album and write its description in Immich. Writing the caption is what publishes it; uncaptioned photos are never sent. At `SEND_TIME` (default 07:00), everything new goes out. Until then, or for at least `SETTLE_MINUTES`, you can still fix the caption. To pull something back, remove it from the album or clear its caption.

**Relatives are Immich users.** Each relative gets a viewer-only Immich account that the album is shared with. When it's set up, `add-relative` creates an API key *for that account* that can only like, comment, take back its own like or comment, and read or change its email setting. It saves the key in `./secrets/relatives.yml`. mail-a-patatina uses that key to like and comment as the person, so their ❤️ and comments show up natively in your Immich app, and your replies in Immich appear on their page too. Relatives never need to know their account exists. This works on OAuth-only servers too: `add-relative` switches password login on for the few seconds it needs, because signing in once is the only way Immich lets anyone create a key for a user.

**Magic links.** Every link in the email signs the relative in, using an HMAC-signed token checked against the album's member list:
- Tapping ❤️ in the email likes the photo. Tapping it again on the page takes the like back, and relatives can delete their own comments.
- Tapping **Comment** opens the photo page, ready to type.
- The token is turned into a cookie and removed from the address bar right away.
- Removing someone from the album in Immich kills their link.
- Bumping `LINK_VERSION` kills all links at once.
- The ❤️ is only saved by an auto-submitted form on the page, so email security scanners that pre-open links can't like on someone's behalf.

**Group chats.** A link posted in a WhatsApp, Signal or Telegram group is shared by everyone in it:
- Visitors are asked for their name once. They comment through one bot account (`BOT_NAME`), shown in Immich as `[Tante Ute] Hi!` and on the page as *Tante Ute*.
- Immich allows one ❤️ per account, so a group has one shared ❤️.
- A one-to-one chat can instead be tied to a relative (`person:` in the chat's config), so its links sign in as them.

**Only Immich's reactions.** Immich has exactly two kinds of activity: like (❤️) and comment. The site offers just those.

**What gets sent.** Each photo is marked in Immich when it's sent to each recipient and chat:
- Failed sends are retried at the next run.
- Someone newly added to the album catches up on the last `RESEND_HOURS` (default 48).
- When mail-a-patatina first starts on an album, photos already there count as backlog: they show on the website but aren't sent (`SEND_BACKLOG=true` sends them).
- `SEND_MODE=drip` sends one photo per day from the queue instead of a digest.

## Several families on one installation

One mail-a-patatina can serve several Immich users who don't know each other. Each family is a **feed**: one album, its owner's API key, its own title, send time, timezone, chats and bot account, listed in `config/feeds.yml` (see `feeds.example.yml`). With a single family you can skip that file and put `ALBUM_ID` and `IMMICH_API_KEY` in `.env`.

How families are kept apart:
- **Separate pages:** each family's pages live under `PUBLIC_URL/<feed id>/`, and the sign-in cookie only applies to that path.
- **Links can't cross over:** magic-link tokens are signed together with their feed id, so a link from the Müllers' email is rejected on the Schmidts' pages, even by someone who rewrites the URL.
- **Every request is checked against one album:** photos, likes and comments are only served if they belong to that feed's album, through that album owner's key.
- **Separate emails:** a grandma who is in two families' albums has one Immich account, gets two separate emails and has two separate links. Unsubscribing is her own Immich email setting, so it stops both.

Because the families' API keys sit in `config/feeds.yml`, whoever runs mail-a-patatina can read those families' libraries. In practice that's the Immich admin, who can see everything on the server anyway.

## Setup

1. **In Immich:** create the album, turn on *comments & likes* for it, and create API keys:
   - **Album owner's key** (`IMMICH_API_KEY`): `album.read`, `asset.read`, `asset.view`, `asset.update`, `activity.read`, `user.read`, `albumUser.create`.
   - **Admin's key** (`IMMICH_ADMIN_API_KEY`): `adminUser.read`, `adminUser.create`, `adminUser.update`. No delete permission is needed. The only delete right any key of this project holds is the relatives' `activity.delete`, for taking back their own like or comment. Only the setup commands use it, so you can remove it afterwards.
   - **Contributors:** if a co-parent also adds their own photos to the album, they create an API key in *their own* Immich account (*Account settings → API keys*) with exactly `user.read`, `asset.read` and `asset.update`, and you add it to that feed's `contributor_keys` (or `IMMICH_CONTRIBUTOR_KEYS` for a single family). mail-a-patatina only uses it to read and write the delivery marker on that person's photos. Immich only lets an asset's owner write its metadata; without their key, their photos are skipped.
   - **OAuth-only servers:** password login has to be on while `add-relative` and `add-bot` run, because signing in once is the only way to create a key for an account. Give the admin key `adminConfig.read` and `adminConfig.update` too, and the command switches password login on for those few seconds and off again, even if something fails. Without those permissions, or if your Immich settings come from a config file, it tells you to switch it yourself under *Administration → Settings → Authentication settings*.
2. **Configure and start:**
   ```sh
   cp .env.example .env                      # Immich, SECRET_KEY, SMTP, PUBLIC_URL (+ ALBUM_ID for one family)
   mkdir -p config && chmod 700 config
   cp feeds.example.yml config/feeds.yml     # several families (or: cp chats.example.yml config/chats.yml)
   docker compose up -d          # pulls the image built by GitHub Actions (MAP_IMAGE), or add --build to build locally
   ```
3. **Add the people:**
   ```sh
   export IMMICH_ADMIN_API_KEY=…   # only for these commands; keep it out of .env
   docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed mueller add-relative "Oma Erika" oma@example.com
   docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed mueller add-bot   # for group-chat visitors
   docker compose exec app python -m mail_a_patatina check
   ```
   The accounts appear in Immich as `patatina-Oma Erika` (`USER_PREFIX`), so they're easy to tell apart from real users. Pages and emails show just *Oma Erika*. They get a storage quota of 0, so they can't upload anything, and their API key can't either.
   `add-relative` creates the Immich account, shares the album with it as *viewer*, creates that account's API key in `./secrets/relatives.yml`, and turns off Immich's own album emails for it so nobody gets two. Running it again for the same person replaces the key. If the relative already has an Immich account, `--adopt` takes it over. That resets its password, so don't adopt co-parents or anyone who signs in to Immich themselves. They see everything in Immich anyway and simply get no email. `check` shows who is who.
4. **Put a reverse proxy in front:** expose only port 8080, with HTTPS (Caddy, Traefik, nginx…), and set `PUBLIC_URL` to that address. Immich itself never has to be reachable from the internet, because images and videos are proxied.
5. **Email delivery:** make sure the sending domain has SPF, DKIM and DMARC set up, or the morning email lands in spam. Test with `send-test oma@example.com`.

### WhatsApp (GOWA)

1. Set `WHATSAPP_AUTH` in `.env`, then start the gateway with `docker compose --profile whatsapp up -d`.
2. Open `http://127.0.0.1:3000` on the Docker host. On the phone, go to *WhatsApp → Linked devices* and scan the QR code.
3. Find the group's id (it ends in `@g.us`) with `python -m mail_a_patatina whatsapp-groups`, and add it to that family's `chats:`.

⚠️ WhatsApp has no official API for posting to groups. GOWA acts like WhatsApp Web on a linked phone, which is against WhatsApp's terms and can get the number banned. Use a spare number, and keep the volume to these few messages a day.

### Signal and Telegram

- **Signal:** run `docker compose --profile signal up -d`, then link via `http://127.0.0.1:8081/v1/qrcodelink?device_name=family-feed`. Set `SIGNAL_NUMBER`. Group ids are at `/v1/groups/<number>`.
- **Telegram:** create a bot with @BotFather, add it to the group, and set `TELEGRAM_BOT_TOKEN`. Use the group's (negative) chat id.

## Container image

`.github/workflows/container.yml` builds the image for amd64 and arm64 and pushes it to the GitHub Container Registry:
- **Pushes to `main`** become `ghcr.io/<owner>/<repo>:latest`.
- **Tags like `v1.2.0`** become `:1.2.0` and `:1.2`.
- **Every build** also gets a `:sha-…` tag.
- **Pull requests** are built but not pushed.

Point `MAP_IMAGE` in `.env` at your image. If the package is private, run `docker login ghcr.io` on the server first.

Running from a checkout without Docker works too: `pip install -r requirements.txt`, then `uvicorn mail_a_patatina.web:app` from the repo root. It reads `.env` and `feeds.yml` from the current folder.

## Logo

The patatina lives in `assets/`:
- **`logo.svg`, `logo.png`:** the logo for the repo and anywhere else.
- **`social-preview.png`:** the card shown when the repo is shared. Upload it under *Settings → General → Social preview* on GitHub; the preview can't be set from a file in the repo.

The app uses its own copies in `mail_a_patatina/static/`:
- `favicon.svg` and `apple-touch-icon.png` for the browser tab and home screen,
- `logo-email.png`, embedded in every email, because most mail apps don't show SVG.

## Admin commands

`--feed <id>` picks the family; it can be left out when there is only one. `check`, `links`, `sync` and `dispatch` cover all families when no feed is named.

```sh
docker compose exec app python -m mail_a_patatina check                              # setup status, who gets what
docker compose exec app python -m mail_a_patatina links                              # everyone's magic links
docker compose exec app python -m mail_a_patatina --feed mueller send-test EMAIL -n 3   # doesn't mark anything as sent
docker compose exec app python -m mail_a_patatina sync | dispatch
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed mueller add-relative NAME EMAIL [--adopt]
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed mueller add-bot
```

## Keep in mind

- **Back up `./secrets` like `.env`.** If the keys are lost, run `add-relative` again for each relative, with password login switched on for a moment. You can see and revoke each key in Immich under the relative's account (*API keys → mail-a-patatina*).
- **`SECRET_KEY`:** it signs the magic links. It also derives the relatives' passwords, which are only used while `add-relative` runs. If you change it, `add-relative` needs `--adopt` for existing relatives.
- **Immich's own emails:** if Immich's own SMTP is set up, it may still email album *invites*. `add-relative` turns off album update emails for managed accounts, and invites aren't sent because accounts are created with `notify: false`.
- **Removing a relative:** remove them from the album (their link stops working). Delete the Immich account if you like; their likes and comments go with it.
