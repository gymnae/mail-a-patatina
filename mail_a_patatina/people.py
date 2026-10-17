"""The relatives' Immich accounts, shared by all feeds (one grandma can be in two families' albums).
Likes and comments are posted as those people, with the API key created for them during setup (or, on
servers that allow password login, by signing in with their derived password)."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import httpx
import yaml

from .config import Settings, clean_email
from .immich import Immich, LoginFailed
from .links import derived_password

log = logging.getLogger(__name__)
T = TypeVar("T")


def load_keys(path: Path) -> dict[str, str]:
    """email -> that person's own Immich API key. Credentials, kept next to .env, not data."""
    if not path.exists():
        return {}
    return {clean_email(k): v for k, v in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items()}


def save_key(path: Path, email: str, key: str | None) -> None:
    """Store a person's key, or drop it (key None)."""
    keys = load_keys(path) | {clean_email(email): key}
    keys = {k: v for k, v in keys.items() if v is not None}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "# Immich API keys of the relatives' accounts, written by `add-relative`. Keep secret.\n"
        + yaml.safe_dump(keys, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    tmp.replace(path)


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    email: str
    role: str  # owner | editor | viewer
    managed: bool = True  # an account Mail-A-Patatina created (USER_PREFIX), not someone's own Immich account


class People:
    def __init__(self, settings: Settings):
        self.s = settings
        self.keys = load_keys(settings.keys_file)
        self._sessions: dict[str, Immich] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._unmanaged: set[str] = set()  # members we can't act as (no key, and no password login)
        self._no_email: dict[str, str] = {}  # member id -> why they get no email, logged once per change

    async def reload_keys(self) -> None:
        keys = load_keys(self.s.keys_file)
        if keys == self.keys:
            return
        self.keys = keys
        for member_id, client in list(self._sessions.items()):
            if client.via_key:
                del self._sessions[member_id]
                await client.aclose()

    def display_name(self, name: str) -> str:
        """Managed accounts are named '<USER_PREFIX>Oma Erika' in Immich; relatives just see 'Oma Erika'."""
        prefix = self.s.user_prefix
        return name[len(prefix):] if prefix and name.startswith(prefix) else name

    def is_managed(self, immich_name: str) -> bool:
        return not self.s.user_prefix or immich_name.startswith(self.s.user_prefix)

    def members(self, album_users: list[dict]) -> dict[str, Member]:
        """An album's members by user id."""
        return {
            u["user"]["id"]: Member(
                u["user"]["id"], self.display_name(u["user"]["name"]), clean_email(u["user"]["email"]), u["role"],
                self.is_managed(u["user"]["name"]),
            )
            for u in album_users
        }

    async def session(self, member: Member) -> Immich:
        async with self._locks[member.id]:
            if member.id not in self._sessions:
                key = self.keys.get(member.email)
                if key is None:  # added by `add-relative` while the app was running?
                    self.keys = load_keys(self.s.keys_file)
                    key = self.keys.get(member.email)
                if key:
                    client = Immich(self.s.immich_url, api_key=key)
                    client.user_id = member.id
                    self._sessions[member.id] = client
                    return client
                try:
                    self._sessions[member.id] = await Immich.login(
                        self.s.immich_url, member.email, derived_password(self.s, member.email)
                    )
                    self._unmanaged.discard(member.id)
                except LoginFailed:
                    if member.id not in self._unmanaged:
                        log.info("No API key for %s (see `add-relative`); no email or posting on their behalf", member.email)
                        self._unmanaged.add(member.id)
                    raise
            return self._sessions[member.id]

    async def run_as(self, member: Member, action: Callable[[Immich], Awaitable[T]]) -> T:
        """Run an Immich call as this member, signing in again once if the session expired."""
        client = await self.session(member)
        try:
            return await action(client)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 401:
                raise
            self._sessions.pop(member.id, None)
            await client.aclose()
            if client.via_key:
                raise LoginFailed(f"{member.email}: API key was revoked") from e
            return await action(await self.session(member))

    async def wants_email(self, member: Member) -> bool:
        """The member's own Immich email-notification switch doubles as the unsubscribe flag."""
        try:
            prefs = await self.run_as(member, lambda c: c.preferences())
        except LoginFailed as e:
            return self._skip(member, f"can't sign in as them ({str(e) or 'no API key'})")
        if not prefs.get("emailNotifications", {}).get("enabled", True):
            return self._skip(member, "email notifications are switched off in their Immich account (or they unsubscribed)")
        if self._no_email.pop(member.id, None):
            log.info("%s gets email again", member.email)
        return True

    def _skip(self, member: Member, reason: str) -> bool:
        if self._no_email.get(member.id) != reason:
            log.warning("No email to %s: %s", member.email, reason)
            self._no_email[member.id] = reason
        return False

    async def aclose(self) -> None:
        for client in self._sessions.values():
            try:
                if not client.via_key:
                    await client.logout()
            except httpx.HTTPError:
                pass
            await client.aclose()
        self._sessions.clear()
