"""WhatsApp accounts on the GOWA gateway, one per family.

Each family links a WhatsApp account of its own on the setup page, ideally a spare number. GOWA keeps one
session ("device") per family, so a family's moments go out from its own account, and the setup page only
ever sees that account's groups. Families defined by the admin can name a device in feeds.yml instead,
or use the gateway's only device (WHATSAPP_DEVICE_ID left empty)."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from .config import Settings


class GatewayError(RuntimeError):
    pass


class Gateway:
    def __init__(self, s: Settings, http: httpx.AsyncClient):
        if not s.whatsapp_url:
            raise GatewayError("WHATSAPP_URL is not configured")
        self.url = s.whatsapp_url
        self.http = http
        user, _, password = s.whatsapp_auth.partition(":")
        self.auth = (user, password) if s.whatsapp_auth else None

    async def _call(self, method: str, path: str, device: str = "", **kw) -> httpx.Response:
        headers = {"X-Device-Id": device} if device else {}
        return await self.http.request(method, f"{self.url}{path}", headers=headers, auth=self.auth, **kw)

    async def _results(self, method: str, path: str, device: str = "", **kw):
        r = await self._call(method, path, device, **kw)
        try:
            body = r.json()
        except ValueError:
            r.raise_for_status()
            raise GatewayError(f"WhatsApp gateway: unexpected answer from {path}") from None
        if r.is_error or body.get("code") not in (None, "SUCCESS"):
            raise GatewayError(f"WhatsApp gateway: {body.get('code') or r.status_code}: {body.get('message')}")
        return body.get("results")

    async def _device(self, device: str) -> dict | None:
        try:
            return await self._results("GET", f"/devices/{device}") or {}
        except GatewayError as e:
            if "not found" in str(e):  # GOWA answers 500 "device … not found"
                return None
            raise

    async def ensure_device(self, device: str) -> None:
        if await self._device(device) is None:
            await self._results("POST", "/devices", json={"device_id": device})

    async def account(self, device: str) -> dict | None:
        """-> {"phone", "name"} of the linked account, or None while nothing is linked."""
        info = await self._device(device)
        if info is None or not (await self._results("GET", f"/devices/{device}/status")).get("is_logged_in"):
            return None
        info = await self._device(device) or {}  # name and number are filled in once logged in
        jid = str(info.get("jid") or "")
        return {"phone": str(info.get("phone_number") or jid.split("@")[0].split(":")[0].split(".")[0]),
                "name": str(info.get("display_name") or "")}

    async def qr(self, device: str) -> tuple[bytes, int]:
        """-> (PNG of a QR code to scan under WhatsApp > Linked devices, seconds it stays valid)."""
        results = await self._results("GET", f"/devices/{device}/login")
        # The link names the gateway's own host, which the browser can't reach; fetch it from here.
        image = await self._call("GET", urlparse(results["qr_link"]).path)
        if image.is_error:
            raise GatewayError(f"WhatsApp gateway: QR code not available ({image.status_code})")
        return image.content, int(results.get("qr_duration") or 30)

    async def pair_code(self, device: str, phone: str) -> str:
        """A code to type under WhatsApp > Linked devices > Link with phone number instead."""
        return (await self._results("POST", f"/devices/{device}/login/code", params={"phone": phone}))["pair_code"]

    async def remove(self, device: str) -> None:
        """Log the account out and delete its session from the gateway."""
        if await self._device(device) is not None:
            await self._results("DELETE", f"/devices/{device}")

    async def groups(self, device: str) -> list[tuple[str, str]]:
        """-> [(name, chat id)] of the account's groups."""
        results = await self._results("GET", "/user/my/groups", device)
        return [(g.get("Name") or "", g["JID"]) for g in (results or {}).get("data") or [] if g.get("JID")]

    async def send_image(self, device: str, chat_id: str, jpeg: bytes, caption: str) -> None:
        await self._results(
            "POST", "/send/image", device,
            data={"phone": chat_id, "caption": caption, "compress": "false"},
            files={"image": ("photo.jpg", jpeg, "image/jpeg")},
        )
