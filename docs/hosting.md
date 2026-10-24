# Hosting Mail-A-Patatina

The admin only sets up the installation; families do the rest on the [setup page](families.md).

## Installation

1. **Configure and start:**
   ```sh
   cp .env.example .env          # Immich URLs, SECRET_KEY, PUBLIC_URL, SMTP, OIDC client, INSTALL_ADMIN_EMAILS
   docker compose up -d          # pulls the image built by GitHub Actions (MAP_IMAGE), or add --build to build locally
   docker compose exec app python -m mail_a_patatina check
   ```
   Then register the OIDC client (see [Pocket ID](#pocket-id-or-any-oidc-provider) below) and send families the link `PUBLIC_URL/setup/`.
2. **Admin key for approvals** (`IMMICH_ADMIN_API_KEY`): `adminUser.read`, `adminUser.create`, `adminUser.update`, and `adminUser.delete` for `forget`.
   - **Pass it only to the commands that need it, never in `.env`:** `docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina approve`.
   - **OAuth-only Immich servers:** add `adminConfig.read` and `adminConfig.update`. Password login has to be on while an account gets its key, and `approve` switches it on for those few seconds and off again, even if something fails. Without those two permissions, or if your Immich settings come from a config file, it tells you to switch it yourself under *Administration → Settings → Authentication settings*.
   - **Delete rights, all narrow:** the relatives' `activity.delete` takes back their own like or comment; the album owner's `albumUser.delete` takes a relative out of the album; the admin key's `adminUser.delete` is only used by `forget`, on accounts Mail-A-Patatina created.
   - **What approved accounts look like:** they appear in Immich as `patatina-Oma Erika` (`USER_PREFIX`). Their storage quota is 0, so they can't upload anything, and their API key can't either. Immich's own album emails are switched off for them, so nobody gets two.
3. **Reverse proxy:** expose only port 8080, with HTTPS (Caddy, Traefik, nginx…), and set `PUBLIC_URL` to that address. Immich itself never has to be reachable from the internet, because images and videos are proxied.
4. **Email delivery:** make sure the sending domain has SPF, DKIM and DMARC set up, or the morning email lands in spam.

**Families defined by the admin** instead of on the setup page also still work: `config/feeds.yml`, or `ALBUM_ID` in `.env` for a single family, with `add-relative` and `add-bot` on the CLI. `add-relative --adopt` takes over an existing Immich account, which resets its password, so don't adopt anyone who signs in to Immich themselves. See [several families on one installation](how-it-works.md#several-families-on-one-installation).

## Pocket ID (or any OIDC provider)

In Pocket ID, add an OIDC client:
- **Name:** `Mail-A-Patatina`
- **Callback URL:** `https://<your PUBLIC_URL>/setup/callback`
- **Optional:** under *allowed user groups*, limit it to the group that may use Immich.

Put its client id and secret into `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`, and Pocket ID's URL into `OIDC_ISSUER`. The login uses the authorization-code flow with PKCE and asks for `openid email profile`. The email address it returns is matched against the Immich account of the feed's key.

**"Connect with Immich" instead of pasting a key** (on by default whenever Immich has OAuth login switched on; `IMMICH_OAUTH_CONNECT=false` turns it off):
- **One more callback, required:** in Pocket ID, open **Immich's** client (not Mail-A-Patatina's) and add `https://<your PUBLIC_URL>/setup/immich-callback` to its callback URLs.
- **What happens:** the setup page starts Immich's own OAuth login (with PKCE). The provider sends the person back to the setup page, which completes the login with Immich. Immich does the checking, so Mail-A-Patatina never handles a password or the provider's tokens.
- **What's kept:** for a few seconds, the server holds a normal Immich session of that person. It uses it only to check the email address, create the key with exactly the eight permissions (and, when reconnecting, delete the feed's old key), and then signs the session out. Only the narrow key is stored.
- **If it fails** (the callback isn't allowed, Immich's OAuth is off, or Immich changed its OAuth API), the page falls back to creating the key by hand.
- **Invite links** for co-parents and relatives with their own account use the same login. A link is signed for one album member and one role, works only if exactly that Immich account signs in, expires after 14 days (or with `LINK_VERSION`), and only starts the sign-in on a button press, so link scanners in mail apps can't trigger it.

## Messenger gateways

Families connect their own groups on the setup page. The admin runs the gateways.

### WhatsApp (GOWA)

Every family sends from a WhatsApp account under its own control. The installation has no WhatsApp number.

1. Set `WHATSAPP_AUTH` in `.env`, then start the gateway with `docker compose --profile whatsapp up -d`.
2. That's all for the admin. Families link their accounts on the setup page, and GOWA keeps one session ("device") per family, named after the feed. Unlinking or deleting the feed removes the session.
3. **Families defined in `.env` or `feeds.yml`** link with `docker compose run --rm app python -m mail_a_patatina whatsapp-link --feed <id> +491701234567`, which prints a code to type in on that phone. `whatsapp-groups --feed <id>` then lists the chat ids of its groups.

⚠️ WhatsApp has no official API for posting to groups. GOWA acts like WhatsApp Web on a linked phone, which is against WhatsApp's terms and can get the number banned. Tell families to use a spare number, not their personal one. The server can also read that account's chats. Keep the volume to these few messages a day.

### Signal and Telegram

- **Signal:** run `docker compose --profile signal up -d`, then link via `http://127.0.0.1:8081/v1/qrcodelink?device_name=family-feed`. Set `SIGNAL_NUMBER`. Group ids are at `/v1/groups/<number>`.
- **Telegram:** create a bot with @BotFather, add it to the group, and set `TELEGRAM_BOT_TOKEN`. Use the group's (negative) chat id.

## Admin commands

`--feed <id>` picks the family; it can be left out when there is only one. `check`, `links`, `sync` and `dispatch` cover all families when no feed is named.

```sh
docker compose exec app python -m mail_a_patatina check                              # setup status, who gets what
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina pending   # what families are waiting for
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina approve [EMAIL]
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed die-falks reject EMAIL
docker compose exec app python -m mail_a_patatina links                              # everyone's magic links
docker compose exec app python -m mail_a_patatina --feed mueller send-test EMAIL -n 3   # doesn't mark anything as sent
docker compose exec app python -m mail_a_patatina sync | dispatch
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed mueller add-relative NAME EMAIL [--adopt]
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed mueller add-bot
```

## Container image

`.github/workflows/container.yml` builds the image for amd64 and arm64 and pushes it to the GitHub Container Registry:
- **Pushes to `main`** become `ghcr.io/<owner>/<repo>:latest`.
- **Tags like `v1.2.0`** become `:1.2.0` and `:1.2`.
- **Every build** also gets a `:sha-…` tag.
- **Pull requests** are built but not pushed.

Point `MAP_IMAGE` in `.env` at your image. If the package is private, run `docker login ghcr.io` on the server first.

Running from a checkout without Docker works too: `pip install -r requirements.txt`, then `uvicorn mail_a_patatina.web:app` from the repo root. It reads `.env` and `feeds.yml` from the current folder.

Tests need neither Immich nor SMTP: `pip install -r requirements-dev.txt`, then `python -m pytest` from the repo root. GitHub Actions runs them before building the image.

## Keep in mind

- **Back up `./journal` like Immich.** It holds the trips' text (see [Trips](journal.md)); nothing else has a copy.
- **Back up `./secrets` like `.env`.** If the keys are lost, run `add-relative` again for each relative, with password login switched on for a moment. You can see and revoke each key in Immich under the relative's account (*API keys → Mail-A-Patatina*).
- **`SECRET_KEY`:** it signs the magic links. It also derives the relatives' passwords, which are only used while `add-relative` runs. If you change it, `add-relative` needs `--adopt` for existing relatives.
- **Immich's own emails:** if Immich's own SMTP is set up, it may still email album *invites*. `add-relative` turns off album update emails for managed accounts, and invites aren't sent because accounts are created with `notify: false`.
- **Removing a relative:** remove them from the album (their link stops working). Delete the Immich account if you like; their likes and comments go with it.

## Logo

The patatina lives in `assets/`:
- **`logo.svg`, `logo.png`:** the logo for the repo and anywhere else.
- **`social-preview.png`:** the card shown when the repo is shared. Upload it under *Settings → General → Social preview* on GitHub; the preview can't be set from a file in the repo.

The app uses its own copies in `mail_a_patatina/static/`:
- `favicon.svg` and `apple-touch-icon.png` for the browser tab and home screen,
- `logo-email.png`, embedded in every email, because most mail apps don't show SVG.
