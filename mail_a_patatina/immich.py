"""The only module that talks to Immich. Immich is the single source of truth for photos, captions,
the audience (album members), likes and comments, and what was already sent (asset metadata)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx

# Asset metadata key under which delivery bookkeeping is stored on each shared asset.
MARKER_KEY = "mail-a-patatina"


class LoginFailed(Exception):
    pass


# Liking and commenting, and taking back one's own like or comment: what everyone's key needs to post
# on the website in their own name. activity.delete never removes someone else's (the website checks).
ACTIVITY_PERMISSIONS = ["activity.create", "activity.read", "activity.delete"]
# What a relative's own API key may do: like and comment, and read/write their email setting.
RELATIVE_PERMISSIONS = [*ACTIVITY_PERMISSIONS, "user.read", "userPreference.read", "userPreference.update"]
KEY_NAME = "mail-a-patatina"
# What the album owner's key needs (entered on the setup page). Besides activity.delete, the only delete
# right is albumUser.delete, which takes someone out of the album (never photos or accounts). album.create
# and albumAsset.create are for trips: the editor makes each trip's album and adds an entry's photos to it.
OWNER_PERMISSIONS = [
    "album.read", "asset.read", "asset.view", "asset.update", *ACTIVITY_PERMISSIONS, "user.read",
    "albumUser.create", "albumUser.delete", "album.create", "albumAsset.create",
]
TRIP_PERMISSIONS = ["album.create", "albumAsset.create"]  # what keys from before trips existed may lack
# What a co-parent's key needs: record deliveries on their own photos (only an asset's owner may), and
# like and comment on the website in their own name.
CONTRIBUTOR_PERMISSIONS = ["user.read", "asset.read", "asset.update", *ACTIVITY_PERMISSIONS]


class Immich:
    """One authenticated connection: an API key, or a user session from `login`."""

    def __init__(self, url: str, *, api_key: str | None = None, token: str | None = None):
        headers = {"Accept": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.url = url
        self.via_key = bool(api_key)
        self.user_id: str | None = None
        self.http = httpx.AsyncClient(base_url=f"{url}/api", headers=headers, timeout=httpx.Timeout(30, read=120))

    @classmethod
    async def login(cls, url: str, email: str, password: str) -> Immich:
        async with httpx.AsyncClient(base_url=f"{url}/api", timeout=30) as http:
            r = await http.post("/auth/login", json={"email": email, "password": password})
        if r.status_code in (400, 401):
            raise LoginFailed(f"{email}: {r.json().get('message', r.status_code)}")
        r.raise_for_status()
        data = r.json()
        client = cls(url, token=data["accessToken"])
        client.user_id = data["userId"]
        return client

    @staticmethod
    async def oauth_start(url: str, redirect_uri: str, state: str, code_challenge: str) -> str:
        """Start Immich's own OAuth login; -> the provider URL to send the browser to. The provider sends
        it back to `redirect_uri`, which must be allowed for Immich's client there."""
        async with httpx.AsyncClient(base_url=f"{url}/api", timeout=30) as http:
            r = await http.post("/oauth/authorize", json={
                "redirectUri": redirect_uri, "state": state, "codeChallenge": code_challenge})
        r.raise_for_status()
        return r.json()["url"]

    @classmethod
    async def oauth_finish(cls, url: str, callback_url: str, state: str, code_verifier: str) -> Immich:
        """Complete it with the URL the provider redirected to; -> a session of the user who signed in."""
        async with httpx.AsyncClient(base_url=f"{url}/api", timeout=30) as http:
            r = await http.post("/oauth/callback", json={"url": callback_url, "state": state, "codeVerifier": code_verifier})
        if r.status_code in (400, 401):
            raise LoginFailed(f"Immich: {r.json().get('message', r.status_code)}")
        r.raise_for_status()
        data = r.json()
        client = cls(url, token=data["accessToken"])
        client.user_id = data["userId"]
        return client

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _call(self, method: str, path: str, **kwargs):
        r = await self.http.request(method, path, **kwargs)
        if r.is_error:
            # Keep Immich's own explanation, e.g. "Missing required permission: asset.read".
            try:
                detail = r.json().get("message")
            except ValueError:
                detail = None
            raise httpx.HTTPStatusError(
                f"Immich refused {method} {path} ({r.status_code}){': ' + str(detail) if detail else ''}",
                request=r.request, response=r,
            )
        return r.json() if r.content else None

    # --- server, me ---

    async def server_version(self) -> str:
        v = await self._call("GET", "/server/version")
        return f"{v['major']}.{v['minor']}.{v['patch']}"

    async def server_features(self) -> dict:
        return await self._call("GET", "/server/features")

    async def users(self) -> list[dict]:
        """The server's users, as far as this user may see them (only themselves if Immich hides other users)."""
        return await self._call("GET", "/users")

    async def me(self) -> dict:
        return await self._call("GET", "/users/me")

    async def logout(self) -> None:
        await self.http.post("/auth/logout")

    # --- albums and assets ---

    async def albums(self) -> list[dict]:
        return await self._call("GET", "/albums")

    async def album(self, album_id: str) -> dict:
        return await self._call("GET", f"/albums/{album_id}")

    async def create_album(self, name: str, users: list[tuple[str, str]]) -> dict:
        """A new album of this user's, shared with (user id, role) pairs."""
        return await self._call("POST", "/albums", json={
            "albumName": name, "albumUsers": [{"userId": u, "role": r} for u, r in users]})

    async def add_album_assets(self, album_id: str, asset_ids: list[str]) -> None:
        """Photos already in the album are fine (Immich answers "duplicate" for them)."""
        await self._call("PUT", f"/albums/{album_id}/assets", json={"ids": asset_ids})

    async def add_album_user(self, album_id: str, user_id: str, role: str = "viewer") -> None:
        await self._call("PUT", f"/albums/{album_id}/users", json={"albumUsers": [{"userId": user_id, "role": role}]})

    async def remove_album_user(self, album_id: str, user_id: str) -> None:
        await self._call("DELETE", f"/albums/{album_id}/user/{user_id}")

    async def search(self, **filters) -> AsyncIterator[dict]:
        """Yield every asset matching the filters, following Immich's pagination."""
        cursor: str | None = None
        page = 1
        while True:
            body = {"withExif": True, "size": 1000, **filters}
            # Immich >= 3.2 pages with a cursor; older versions with page numbers.
            if cursor:
                body["cursor"] = cursor
            else:
                body["page"] = page
            assets = (await self._call("POST", "/search/metadata", json=body))["assets"]
            for item in assets["items"]:
                yield item
            if assets.get("nextCursor"):
                cursor = assets["nextCursor"]
            elif assets.get("nextPage"):
                page = int(assets["nextPage"])
            else:
                return

    async def search_page(self, after: str = "", **filters) -> tuple[list[dict], str]:
        """One page of matching assets; -> (assets, what to pass as `after` for the next page, "" at the end)."""
        body = {"withExif": True, **filters}
        if after.startswith("c:"):
            body["cursor"] = after[2:]
        else:
            body["page"] = int(after[2:]) if after.startswith("p:") else 1
        assets = (await self._call("POST", "/search/metadata", json=body))["assets"]
        nxt = f"c:{assets['nextCursor']}" if assets.get("nextCursor") else (
            f"p:{assets['nextPage']}" if assets.get("nextPage") else "")
        return assets["items"], nxt

    async def thumbnail(self, asset_id: str, size: str) -> bytes:
        # "fullsize" of a photo browsers can show is the original itself, which Immich answers with a redirect.
        r = await self.http.get(f"/assets/{asset_id}/thumbnail", params={"size": size}, follow_redirects=True)
        r.raise_for_status()
        return r.content

    async def original(self, asset_id: str) -> bytes:
        r = await self.http.get(f"/assets/{asset_id}/original")
        r.raise_for_status()
        return r.content

    async def open_video(self, asset_id: str, range_header: str | None) -> httpx.Response:
        """Start streaming a video; the caller must close the response."""
        headers = {"Range": range_header} if range_header else {}
        request = self.http.build_request("GET", f"/assets/{asset_id}/video/playback", headers=headers)
        return await self.http.send(request, stream=True)

    # --- delivery bookkeeping (asset metadata) ---

    async def marker(self, asset_id: str) -> dict | None:
        r = await self.http.get(f"/assets/{asset_id}/metadata/{MARKER_KEY}")
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.json()["value"]

    async def set_markers(self, markers: dict[str, dict]) -> None:
        items = [{"assetId": asset_id, "key": MARKER_KEY, "value": value} for asset_id, value in markers.items()]
        await self._call("PUT", "/assets/metadata", json={"items": items})

    # --- likes and comments ---

    async def activities(self, album_id: str) -> list[dict]:
        return await self._call("GET", "/activities", params={"albumId": album_id})

    async def add_activity(self, album_id: str, asset_id: str, kind: str, comment: str | None = None) -> dict:
        body = {"albumId": album_id, "assetId": asset_id, "type": kind}
        if comment is not None:
            body["comment"] = comment
        return await self._call("POST", "/activities", json=body)

    async def delete_activity(self, activity_id: str) -> None:
        await self._call("DELETE", f"/activities/{activity_id}")

    # --- preferences (used as the email opt-out) ---

    async def preferences(self) -> dict:
        return await self._call("GET", "/users/me/preferences")

    async def update_preferences(self, prefs: dict) -> None:
        await self._call("PUT", "/users/me/preferences", json=prefs)

    # --- API keys (a relative's own key, created once during setup) ---

    async def api_keys(self) -> list[dict]:
        return await self._call("GET", "/api-keys")

    async def create_api_key(self, name: str, permissions: list[str]) -> str:
        return (await self._call("POST", "/api-keys", json={"name": name, "permissions": permissions}))["secret"]

    async def api_key_info(self) -> dict:
        """The key this connection uses: its name and permissions."""
        return await self._call("GET", "/api-keys/me")

    async def delete_api_key(self, key_id: str) -> None:
        await self._call("DELETE", f"/api-keys/{key_id}")

    # --- admin (only used by the setup commands) ---

    async def admin_config(self) -> dict:
        return await self._call("GET", "/admin/config")

    async def update_admin_config(self, config: dict) -> None:
        await self._call("PUT", "/admin/config", json=config)

    async def admin_users(self, with_deleted: bool = False) -> list[dict]:
        return await self._call("GET", "/admin/users", params={"withDeleted": str(with_deleted).lower()})

    async def admin_restore_user(self, user_id: str) -> dict:
        return await self._call("POST", f"/admin/users/{user_id}/restore")

    async def admin_create_user(self, email: str, name: str, password: str) -> dict:
        return await self._call(
            "POST", "/admin/users",
            # quota 0: the account can't upload anything (Immich rejects any file once quota < used + size)
            json={"email": email, "name": name, "password": password, "notify": False, "shouldChangePassword": False,
                  "quotaSizeInBytes": 0},
        )

    async def admin_delete_user(self, user_id: str) -> None:
        """Soft delete: Immich purges the account (and with it its likes and comments) after its deletion delay."""
        await self._call("DELETE", f"/admin/users/{user_id}", json={"force": False})

    async def shared_albums(self) -> list[dict]:
        """Albums shared with this user (needs a session or key that may read albums)."""
        return await self._call("GET", "/albums", params={"shared": "true"})

    async def admin_update_user(self, user_id: str, **fields) -> dict:
        return await self._call("PUT", f"/admin/users/{user_id}", json=fields)


ACTIVITY_TTL = 15  # seconds; likes and comments are re-read from Immich this often at most


class AlbumActivity:
    """An album's likes and comments, read through `immich` and cached for a few seconds."""

    def __init__(self, immich: Immich, album_id: str):
        self.immich = immich
        self.album_id = album_id
        self._cached: tuple[float, list[dict]] | None = None

    async def get(self) -> list[dict]:
        if self._cached is None or time.monotonic() - self._cached[0] > ACTIVITY_TTL:
            self._cached = (time.monotonic(), await self.immich.activities(self.album_id))
        return self._cached[1]

    def changed(self) -> None:
        self._cached = None
