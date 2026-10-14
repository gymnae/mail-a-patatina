"""Admin commands, e.g. `docker compose exec app python -m mail_a_patatina check`."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from . import dispatch
from .config import Settings
from .core import Core, Feed
from .immich import KEY_NAME, RELATIVE_PERMISSIONS, Immich, LoginFailed
from .links import derived_password, feed_url, make_token
from .people import Member, save_key
from .whatsapp import Gateway


MANUAL_SWITCH = (
    "Switch it on for a moment (Administration → Settings → Authentication settings → Password login), run this "
    "command again, then switch it off again. Or give IMMICH_ADMIN_API_KEY the permissions adminConfig.read and "
    "adminConfig.update, and Mail-A-Patatina will do this itself."
)
log = logging.getLogger("mail_a_patatina.setup")


@asynccontextmanager
async def password_login(admin: Immich) -> AsyncIterator[None]:
    """Make sure password login is on while setting up an account. Signing in once is the only way Immich
    lets anyone create an API key for a user. On OAuth-only servers this switches it on for a few seconds
    and always restores it afterwards."""
    if (await admin.server_features()).get("passwordLogin"):
        yield
        return
    try:
        config = await admin.admin_config()
        config["passwordLogin"]["enabled"] = True
        await admin.update_admin_config(config)
    except httpx.HTTPStatusError as e:
        # 403: key lacks adminConfig permissions; 400: settings are managed by a config file
        raise SystemExit(f"Password login is switched off in Immich ({e.response.status_code}). {MANUAL_SWITCH}") from None
    log.info("Password login switched on for a moment")
    try:
        yield
    finally:
        try:
            # Re-read right before restoring, so settings changed by someone else meanwhile are kept.
            config = await admin.admin_config()
            config["passwordLogin"]["enabled"] = False
            await admin.update_admin_config(config)
            log.info("Password login switched off again")
        except Exception:
            log.critical("COULD NOT SWITCH PASSWORD LOGIN OFF AGAIN. Do it now in Immich: "
                         "Administration → Settings → Authentication settings → Password login")
            raise


async def add_account(
    core: Core, feed: Feed, name: str, email: str, role: str, adopt: bool, wants_email: bool
) -> Member:
    """Create (or adopt) an Immich account, share the feed's album with it, and give Mail-A-Patatina its own
    narrowly scoped API key for it (saved to KEYS_FILE). Needs IMMICH_ADMIN_API_KEY, and password login
    switched on in Immich while this runs (done automatically if the admin key may change settings)."""
    s = core.settings
    if not s.admin_api_key:
        raise SystemExit("Set IMMICH_ADMIN_API_KEY (an admin's key with adminUser.read/create/update) for this command")
    email = email.strip().lower()
    admin = Immich(s.immich_url, api_key=s.admin_api_key)
    try:
        async with password_login(admin):
            return await _setup_account(core, feed, admin, name, email, role, adopt, wants_email)
    finally:
        await admin.aclose()


async def _setup_account(
    core: Core, feed: Feed, admin: Immich, name: str, email: str, role: str, adopt: bool, wants_email: bool
) -> Member:
    s = core.settings
    password = derived_password(s, email)
    # Managed accounts are recognisable in Immich's user list by their prefix; pages and emails drop it.
    immich_name = name if name.startswith(s.user_prefix) else f"{s.user_prefix}{name}"
    name = core.people.display_name(immich_name)
    existing = next((u for u in await admin.admin_users(with_deleted=True) if u["email"].lower() == email), None)
    if existing and existing.get("deletedAt"):
        # Deleted in Immich but not purged yet: the email is still taken, so the account has to come back.
        try:
            existing = await admin.admin_restore_user(existing["id"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 403:
                raise
            raise SystemExit(
                f"{email} belongs to an Immich account that was deleted but not purged yet. Restore it in Immich "
                "(Administration → Users → deleted users → Restore) and run this again."
            ) from None
        log.info("Restored the deleted Immich account of %s", email)
    if existing and not adopt:
        try:  # created by an earlier run of this command?
            session = await Immich.login(s.immich_url, email, password)
        except LoginFailed:
            if wants_email and not core.people.is_managed(existing["name"]):
                # Someone's own Immich account: share the album and leave the account alone. Their key for
                # emails and reactions comes from them, pasted on the setup page.
                await share_album(feed, existing["id"], role)
                return Member(existing["id"], core.people.display_name(existing["name"]), email, role, managed=False)
            raise SystemExit(
                f"{email} already has an Immich account. Re-run with --adopt to let Mail-A-Patatina manage it "
                "(this resets its password, so its owner can't sign in to Immich with the old one)."
            ) from None
        user = await admin.admin_update_user(existing["id"], name=immich_name, quotaSizeInBytes=0)
    else:
        if existing:
            user = await admin.admin_update_user(
                existing["id"], name=immich_name, password=password, shouldChangePassword=False, quotaSizeInBytes=0
            )
        else:
            user = await admin.admin_create_user(email, immich_name, password)
        session = await Immich.login(s.immich_url, email, password)

    try:
        await share_album(feed, user["id"], role)
        for old in await session.api_keys():
            if old["name"] == KEY_NAME:
                await session.delete_api_key(old["id"])
        save_key(s.keys_file, email, await session.create_api_key(KEY_NAME, RELATIVE_PERMISSIONS))
        prefs = {"emailNotifications": {"enabled": wants_email, "albumInvite": False, "albumUpdate": False}}
        await session.update_preferences(prefs)
    finally:
        await session.logout()
        await session.aclose()
    await core.people.reload_keys()
    return Member(user["id"], name, email, role)


async def share_album(feed: Feed, user_id: str, role: str) -> None:
    try:
        await feed.curator.add_album_user(feed.cfg.album_id, user_id, role)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 400:  # 400: already shared with this user
            raise


OWN_ACCOUNT = ("has an Immich account of their own, so the album is now shared with it. For emails and reactions, "
               "the family adds a key from that account on the setup page (People → add key).")


async def check(feed: Feed) -> None:
    core, cfg = feed.core, feed.cfg
    album = await feed.curator.album(cfg.album_id)
    print(f"Feed “{feed.id}” ({cfg.title}): album “{album['albumName']}”, {len(feed.library.ordered)} shared moment(s), "
          f"comments {'on' if album.get('isActivityEnabled', True) else 'OFF'}, sends {cfg.send_time} {cfg.timezone}")
    print(f"  delivery markers writable for {len(feed.writers)} photo owner(s)")
    for m in feed.members.values():
        if m.role == "owner":
            print(f"  owner     {m.name}")
            continue
        is_bot = m.email == cfg.bot_email
        try:
            await core.people.session(m)
            if is_bot:
                state = "posts for group-chat visitors"
            else:
                state = "email on" if await core.people.wants_email(m) else "email off (unsubscribed)"
        except LoginFailed:
            state = "no API key, so no email (fine for co-parents; for a relative: add-relative)"
        print(f"  {'bot' if is_bot else m.role:9} {m.name} <{m.email}>: {state}")
    if cfg.chats and feed.bot is None:
        print(f"  ! group-chat visitors can only look, not comment: run add-bot --feed {feed.id}")
    for c in cfg.chats:
        print(f"  {c.kind:9} {c.name} → {c.address}")


async def unused_accounts(core: Core) -> list[str]:
    """Relatives' accounts (with a key here) that no family's album has anymore."""
    in_use = set()
    for feed in core.feeds.values():
        await feed.refresh_members()
        in_use |= {m.email for m in feed.members.values()}
    waiting = {p["email"] for raw in core.store.all().values() for p in raw.get("pending", [])}
    return sorted(set(core.people.keys) - in_use - waiting)


async def forget(core: Core, email: str) -> None:
    """Delete the Immich account of a relative no family needs anymore, and its key. Only accounts
    Mail-A-Patatina created (named with USER_PREFIX), and only if no album at all is shared with them,
    including albums outside Mail-A-Patatina."""
    s = core.settings
    email = email.strip().lower()
    if not s.admin_api_key:
        raise SystemExit("Set IMMICH_ADMIN_API_KEY (adminUser.read, adminUser.delete) for this command")
    waiting = [raw["id"] for raw in core.store.all().values() if any(p["email"] == email for p in raw.get("pending", []))]
    if waiting:
        raise SystemExit(f"{email} is waiting for approval in {', '.join(waiting)}; `reject` them there first")
    admin = Immich(s.immich_url, api_key=s.admin_api_key)
    try:
        user = next((u for u in await admin.admin_users() if u["email"].lower() == email), None)
        if user is None:
            save_key(s.keys_file, email, None)
            print(f"{email} has no Immich account (anymore). Its key, if any, is removed.")
            return
        if not core.people.is_managed(user["name"]) or not s.user_prefix:
            save_key(s.keys_file, email, None)
            print(f"{email} (“{user['name']}”) is someone's own Immich account, so it stays. Only its key here is "
                  "removed; they can revoke it in Immich (Account settings → API keys).")
            return
        # Only the person themselves can list the albums shared with them.
        async with password_login(admin):
            try:
                session = await Immich.login(s.immich_url, email, derived_password(s, email))
            except LoginFailed:
                raise SystemExit(f"Can't sign in as {email} to check which albums they're in. "
                                 "Check in Immich, and delete the account there if it's unused.") from None
            try:
                albums = await session.shared_albums()
            finally:
                await session.logout()
                await session.aclose()
        if albums:
            raise SystemExit(f"{email} is still in " + ", ".join(f"“{a['albumName']}”" for a in albums)
                             + ". Nothing was deleted.")
        await admin.admin_delete_user(user["id"])
    finally:
        await admin.aclose()
    save_key(s.keys_file, email, None)
    print(f"Deleted {email}. Immich purges the account after its deletion delay (Administration → Settings → "
          "User settings); their old likes and comments go with it.")


async def pending(core: Core) -> None:
    waiting = False
    for raw in core.store.all().values():
        feed = core.feeds.get(raw["id"])
        bot_missing = raw.get("bot_requested") and not (feed and feed.bot)
        if not raw.get("pending") and not bot_missing:
            continue
        waiting = True
        print(f"Feed “{raw['id']}” ({raw.get('title')}), owner {raw.get('owner_email')}:")
        for p in raw.get("pending", []):
            print(f"  relative  {p['name']} <{p['email']}>  (requested {p['requested_at'][:16]})")
        if bot_missing:
            print("  group-chat account (for comments from chat links)")
    if not waiting:
        print("Nothing is waiting for approval.")
    unused = await unused_accounts(core)
    if unused:
        print("\nIn no family's album anymore (delete with `forget EMAIL`):")
        for email in unused:
            print(f"  {email}")


async def approve(core: Core, feed_id: str | None, email: str | None) -> None:
    """Create the accounts families asked for on the setup page. The admin key is only ever used here."""
    for raw in list(core.store.all().values()):
        if feed_id and raw["id"] != feed_id:
            continue
        feed = core.feeds[raw["id"]]
        await feed.sync()
        for p in list(raw.get("pending", [])):
            if email and p["email"] != email.lower():
                continue
            try:
                member = await add_account(core, feed, p["name"], p["email"], "viewer", False, wants_email=True)
            except SystemExit as e:
                print(f"  ✗ {p['name']} <{p['email']}> for “{feed.id}”: {e}")
                continue
            raw["pending"] = [x for x in raw.get("pending", []) if x["email"] != p["email"]]
            core.store.save(raw)
            if member.managed:
                print(f"  ✓ {member.name} <{member.email}> can now get “{feed.id}”")
            else:
                print(f"  ✓ {member.name} <{member.email}> {OWN_ACCOUNT}")
        if raw.get("bot_requested") and not feed.bot and not email:
            try:
                await add_account(core, feed, feed.cfg.bot_name, feed.cfg.bot_email, "viewer", False, wants_email=False)
            except SystemExit as e:
                print(f"  ✗ group-chat account for “{feed.id}”: {e}")
                continue
            raw.pop("bot_requested", None)
            core.store.save(raw)
            print(f"  ✓ group-chat account for “{feed.id}”")


def links(feed: Feed) -> None:
    s = feed.core.settings
    print(f"Feed “{feed.id}”:")
    for m in feed.relatives():
        print(f"  {m.name:24} {feed_url(s, feed.id, make_token(s, feed.id, 'u', m.id))}")
    for c in feed.cfg.chats:
        person = feed.by_email(c.person) if c.person else None
        token = make_token(s, feed.id, "u", person.id) if person else make_token(s, feed.id, "c", c.ref)
        print(f"  {c.name:24} {feed_url(s, feed.id, token)}")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="mail-a-patatina")
    parser.add_argument("--feed", help="which feed (family) to act on; optional when there is only one")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify the Immich setup and show who gets what (all feeds unless --feed)")
    rel = sub.add_parser("add-relative", help="create an Immich account for a relative and share the album with them")
    rel.add_argument("name")
    rel.add_argument("email")
    rel.add_argument("--adopt", action="store_true", help="take over an existing account (resets its password)")
    rel.add_argument("--role", default="viewer", choices=["viewer", "editor"])
    bot = sub.add_parser("add-bot", help="create the account the feed's group-chat visitors post as")
    bot.add_argument("--adopt", action="store_true")
    bot.add_argument("--role", default="viewer", choices=["viewer", "editor"])
    sub.add_parser("links", help="print everyone's magic link (all feeds unless --feed)")
    sub.add_parser("sync", help="pull the latest from Immich now (all feeds unless --feed)")
    sub.add_parser("dispatch", help="run the daily send now (all feeds unless --feed)")
    test = sub.add_parser("send-test", help="email the latest moments to one relative, without marking them sent")
    test.add_argument("email")
    test.add_argument("-n", type=int, default=3, help="number of moments (default 3)")
    wa = sub.add_parser("whatsapp-link", help="link a WhatsApp account to a feed defined in .env/feeds.yml (prints a code)")
    wa.add_argument("phone", help="the account's number with country code, e.g. +491701234567")
    sub.add_parser("whatsapp-groups", help="list the WhatsApp groups of a feed's account (--feed) to find chat ids")
    sub.add_parser("pending", help="show accounts waiting for approval, and those no family needs anymore")
    app = sub.add_parser("approve", help="create the waiting accounts (all, or one feed with --feed, or one EMAIL)")
    app.add_argument("email", nargs="?")
    fgt = sub.add_parser("forget", help="delete a relative's Immich account that no album is shared with anymore")
    fgt.add_argument("email")
    rej = sub.add_parser("reject", help="drop a waiting relative of a feed (needs --feed)")
    rej.add_argument("email")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    core = Core(Settings.from_env())
    s = core.settings
    try:
        if args.cmd == "whatsapp-link":
            feed = core.feed(args.feed)
            device = feed.cfg.whatsapp_device or feed.id
            gateway = Gateway(s, core.http)
            await gateway.ensure_device(device)
            code = await gateway.pair_code(device, "".join(c for c in args.phone if c.isdigit()))
            print(f"On that phone: WhatsApp → Linked devices → Link a device → Link with phone number instead, enter {code}")
            if feed.cfg.whatsapp_device != device:
                print(f"Then set whatsapp_device: {device} for “{feed.id}” in feeds.yml (or WHATSAPP_DEVICE_ID={device} in .env)")
            return
        if args.cmd == "whatsapp-groups":
            device = core.feed(args.feed).cfg.whatsapp_device
            if device is None:
                raise SystemExit("That feed has no WhatsApp account linked yet")
            for name, jid in await Gateway(s, core.http).groups(device):
                print(f"{jid}  {name}")
            return

        if args.cmd == "pending":
            await pending(core)
            return
        if args.cmd == "approve":
            await approve(core, args.feed, args.email)
            return
        if args.cmd == "forget":
            await forget(core, args.email)
            return
        if args.cmd == "reject":
            raw = core.store.get(core.feed(args.feed).id) if args.feed else None
            if raw is None:
                raise SystemExit("Name the feed with --feed (only feeds from the setup page have waiting relatives)")
            before = len(raw.get("pending", []))
            raw["pending"] = [p for p in raw.get("pending", []) if p["email"] != args.email.lower()]
            core.store.save(raw)
            print("Dropped." if len(raw["pending"]) < before else "No such waiting relative.")
            return

        # Commands about everyone run on all feeds unless one is named; the others need exactly one feed.
        if args.cmd in ("check", "links", "sync", "dispatch") and args.feed is None:
            feeds = list(core.feeds.values())
        else:
            feeds = [core.feed(args.feed)]
        for feed in feeds:
            await feed.sync()

        if args.cmd == "add-relative":
            feed = feeds[0]
            member = await add_account(core, feed, args.name, args.email, args.role, args.adopt, wants_email=True)
            if not member.managed:
                print(f"{member.name} {OWN_ACCOUNT} Or re-run with --adopt to take the account over.")
                return
            print(f"{member.name} can now get emails from “{feed.id}”. "
                  f"Their link: {feed_url(s, feed.id, make_token(s, feed.id, 'u', member.id))}")
        elif args.cmd == "add-bot":
            feed = feeds[0]
            await add_account(core, feed, feed.cfg.bot_name, feed.cfg.bot_email, args.role, args.adopt, wants_email=False)
            print(f"Group-chat visitors of “{feed.id}” now post as “{feed.cfg.bot_name}”.")
        elif args.cmd == "check":
            print(f"Immich {await feeds[0].curator.server_version()}")
            for feed in feeds:
                await check(feed)
        elif args.cmd == "links":
            for feed in feeds:
                links(feed)
        elif args.cmd == "dispatch":
            for feed in feeds:
                await feed.dispatch()
        elif args.cmd == "send-test":
            feed = feeds[0]
            target = next(
                (t for t in await dispatch.targets(feed) if t.kind == "email" and t.address == args.email.lower()), None
            )
            if target is None:
                raise SystemExit(f"{args.email} isn't a relative in the album of “{feed.id}” who gets email")
            entries = feed.library.ordered[-args.n:]
            if not entries:
                raise SystemExit("Nothing shared in the album yet")
            await dispatch.send_email(feed, target, entries, record=False)
            print(f"Sent {len(entries)} moment(s) to {args.email}")
    finally:
        await core.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except httpx.HTTPStatusError as e:
        raise SystemExit(str(e)) from None
