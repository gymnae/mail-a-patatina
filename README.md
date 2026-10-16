<p align="center"><img src="assets/logo.svg" alt="A little potato peeking out of an envelope" width="180"></p>

<h1 align="center">Mail-A-Patatina</h1>

<p align="center">A self-hosted, ad-free take on Tinybeans for <a href="https://immich.app">Immich</a>.</p>

Add a photo to a shared Immich album and write a caption. The next morning, grandparents, aunts and uncles get it by email (or in the family's WhatsApp, Signal or Telegram group). They tap ❤️ or comment without a password, and every like and comment shows up in Immich under their name.

## Why Mail-A-Patatina

- **Immich stays in charge.** No database of its own: photos, captions, recipients, likes, comments and what was already sent all live in Immich. Back up Immich, and you've backed up the feed.
- **Nothing new for the parents.** Share by adding a photo to the album and writing a caption in the Immich app you already use.
- **Nothing to install for relatives.** A daily email with magic links: no app, no account to remember, no password.
- **Reactions come home.** Likes and comments are real Immich activity, so you see them in your Immich app and can reply there.
- **Photos stay on your server.** By default nothing is attached: emails and chats link to images that expire and can be revoked.
- **Several families, one installation.** Anyone who can sign in to Immich sets up their own feed on a self-service page, kept fully apart from the others.
- **Private by design.** Self-hosted, no ads, no tracking, and narrow API keys that can't delete photos.

## Quick start: share with your family

Your Immich admin runs Mail-A-Patatina and gives you its address.

1. Open `https://<that address>/setup/` and sign in with your usual account.
2. Click **Connect with Immich** (or paste an API key, the page tells you which permissions).
3. Pick an album, a send time, and add your relatives' names and email addresses.
4. Optionally link a WhatsApp group, Signal group or Telegram chat.
5. Add photos to the album in Immich and write a caption. They go out at the next send time.

To take a photo back before it's sent, clear its caption or remove it from the album. More in [Setting up a family feed](docs/families.md).

## Quick start: host it

You need a running Immich, an SMTP account, a domain with HTTPS, and an OIDC provider such as Pocket ID.

```sh
git clone https://github.com/gymnae/mail-a-patatina.git && cd mail-a-patatina
cp .env.example .env              # Immich URL, PUBLIC_URL, SECRET_KEY, SMTP, OIDC client, INSTALL_ADMIN_EMAILS
docker compose up -d
docker compose exec app python -m mail_a_patatina check
```

Then:
1. Put port 8080 behind your reverse proxy with HTTPS. Immich itself doesn't need to be public.
2. Register the OIDC client (callback `PUBLIC_URL/setup/callback`, and add `PUBLIC_URL/setup/immich-callback` to Immich's client).
3. Send families the link `PUBLIC_URL/setup/`.
4. When families add relatives, approve their accounts:
   ```sh
   docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina approve
   ```

Optional: `docker compose --profile whatsapp up -d` for WhatsApp, `--profile signal` for Signal. Details, permissions and every setting are in [Hosting](docs/hosting.md).

## Documentation

- [How it works](docs/how-it-works.md): data model, magic links, group chats, what gets sent, privacy
- [Setting up a family feed](docs/families.md): setup page, relatives, co-parents, chats
- [Hosting](docs/hosting.md): installation, OIDC, messenger gateways, admin commands, container image
