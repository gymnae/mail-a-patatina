"""The only module that talks to Immich. Immich is the single source of truth for photos, captions,
the audience (album members), likes and comments, and what was already sent (asset metadata)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

# Asset metadata key under which delivery bookkeeping is stored on each shared asset.
MARKER_KEY = "mail-a-patatina"


class LoginFailed(Exception):
    pass


# What a relative's own API key may do: like and comment (and take back their own like or comment), and
# read/write their email setting. activity.delete is the only delete right any key of this project holds.
RELATIVE_PERMISSIONS = [
    "activity.create", "activity.read", "activity.delete", "user.read", "userPreference.read", "userPreference.update",
]
KEY_NAME = "mail-a-patatina"
# What the album owner's key needs (entered on the setup page). Nothing that deletes.
OWNER_PERMISSIONS = [
    "album.read", "asset.read", "asset.view", "asset.update", "activity.read", "user.read", "albumUser.create",
]


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

    async def me(self) -> dict:
        return await self._call("GET", "/users/me")

    async def logout(self) -> None:
        await self.http.post("/auth/logout")

    # --- albums and assets ---

    async def albums(self) -> list[dict]:
        return await self._call("GET", "/albums")

    async def album(self, album_id: str) -> dict:
        return await self._call("GET", f"/albums/{album_id}")

    async def add_album_user(self, album_id: str, user_id: str, role: str = "viewer") -> None:
        await self._call("PUT", f"/albums/{album_id}/users", json={"albumUsers": [{"userId": user_id, "role": role}]})

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

    async def thumbnail(self, asset_id: str, size: str) -> bytes:
        r = await self.http.get(f"/assets/{asset_id}/thumbnail", params={"size": size})
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

    async def admin_update_user(self, user_id: str, **fields) -> dict:
        return await self._call("PUT", f"/admin/users/{user_id}", json=fields)
