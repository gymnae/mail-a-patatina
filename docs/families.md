# Setting up a family feed

Everyone who can sign in to Immich can set up their own family feed at `PUBLIC_URL/setup/`, with no help from the admin:

1. **Sign in** with your identity provider (e.g. Pocket ID), the same account as for Immich.
2. **Connect Immich:** if Immich signs in through OAuth, one button: people sign in to Immich as usual, and the page creates the key with exactly the right permissions (see [Pocket ID](hosting.md#pocket-id-or-any-oidc-provider)). Otherwise, or as a fallback, the page links to Immich's API-key screen and lists the eight permissions to tick. Either way the key is checked:
   - it must belong to the same email address,
   - it must have exactly what the feed needs,
   - it must not be able to administer Immich or delete anything, except take people out of the album (`albumUser.delete`).
3. **Album and rules:** pick one of your own albums, a title, the sending time and timezone, digest or one-a-day, whether only captioned photos go out, and whether you want an email when someone comments.
4. **People and chats:**
   - Add relatives by name and email.
   - Requests from people who opened a relative's link (e.g. a forwarded email) and clicked **Not …?** show up here as "asks to join": **add** them like any relative, or **decline**.
   - Connect co-parents, and relatives who have an Immich account of their own: if Immich signs in through OAuth, the page shows a link for each of them to send (valid 14 days). They open it, sign in to Immich, and are connected; they never see the setup page. Otherwise, paste a key they created in their own account.
   - Remove a relative: takes them out of the album, since its members are who gets the emails. Their Immich account stays; the admin can delete it.
   - Remove a co-parent: Mail-A-Patatina forgets their key and stops sending their photos. Their access to the album in Immich isn't touched.
   - **WhatsApp:** link a WhatsApp account of the family's own, by scanning a QR code or typing a pairing code on the phone, like WhatsApp Web. Moments are posted from that account, and its groups are offered to pick from. Use a spare number rather than a personal one, because the server holds the session.
   - **Signal:** add the installation's number to the group and type the group's exact name. The page never lists the number's groups, because those include other families' groups.
   - **Telegram:** add the installation's bot and enter the group's chat id.
5. **Test email**, changing the rules, replacing the key, or deleting the feed, whenever you like.

## Relatives wait for the admin

Creating an Immich account needs admin rights, and the running service doesn't hold any:
- **The admin is told:** a family's request shows up as "waiting for approval", and `INSTALL_ADMIN_EMAILS` gets an email.
- **One command approves:** the admin runs `approve`, which creates the accounts as before, with the `patatina-` prefix, no upload quota and their own narrow key.
- **Then they're live:** the relative appears as "ready" on the setup page and is in the next morning's email. The same goes for the group-chat account a feed needs once a chat is connected.
- **Relatives with an Immich account of their own** keep it untouched: no new account, no password reset. The album is shared with their account (right away from the setup page if Immich shows other users to non-admins, otherwise by `approve`). For emails and reactions they need a key in their own account: the family sends them the invite link from *People* (if Immich signs in through OAuth), or they create a key with the six relative permissions and the family pastes it. Removing them later only drops that key. (`add-relative --adopt` still takes an account over instead, if you really want that.)
- **Removed relatives keep their account** until the admin deletes it. When a family removes someone who is then in no family's album, `INSTALL_ADMIN_EMAILS` gets an email with the command. `forget` deletes the account only if no album at all is shared with it (it signs in as the person to check, so albums shared outside Mail-A-Patatina count too), and only accounts with the `patatina-` prefix. Immich purges it after its deletion delay, together with the person's old likes and comments. `pending` also lists such accounts.

```sh
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina pending
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina approve            # everything waiting
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina --feed die-falks reject oma@example.com
docker compose run --rm -e IMMICH_ADMIN_API_KEY app python -m mail_a_patatina forget oma@example.com     # account no family needs
```

## Co-parents who add their own photos

Immich only lets a photo's owner write its metadata, where Mail-A-Patatina records what was sent. So the co-parent needs a key in *their own* account. Once the album is shared with them in Immich as editor, they show up under *Co-parents*. If Immich signs in through OAuth, the parent sends them the link shown there; they sign in to Immich once and the key (`user.read`, `asset.read`, `asset.update`) is created for them. Otherwise they create that key themselves and the parent pastes it. Either way the co-parent never signs in to the setup page. The key must belong to someone the album is shared with. Until a key is added, their photos are skipped, and the setup page says whose. For families in `feeds.yml`, the admin lists the keys under `contributor_keys`.

## Where the settings live

Each family's settings are saved as one file in `SETUP_DIR` (`./secrets/feeds/`, mode 600; it contains that family's key). Everything else stays in Immich. People can only see and change the feeds they created themselves.
