"""Chat channels. Each one posts a single photo with a caption to a chat or group.

WhatsApp goes through a GOWA sidecar, Signal through signal-cli-rest-api, Telegram through the Bot API.
"""

from __future__ import annotations

import base64

import httpx

from .config import Settings


async def send_whatsapp(s: Settings, http: httpx.AsyncClient, chat_id: str, jpeg: bytes, caption: str) -> None:
    if not s.whatsapp_url:
        raise RuntimeError("WHATSAPP_URL is not configured")
    user, _, password = s.whatsapp_auth.partition(":")
    r = await http.post(
        f"{s.whatsapp_url}/send/image",
        data={"phone": chat_id, "caption": caption, "compress": "false"},
        files={"image": ("photo.jpg", jpeg, "image/jpeg")},
        headers={"X-Device-Id": s.whatsapp_device} if s.whatsapp_device else {},
        auth=(user, password) if s.whatsapp_auth else None,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in (None, "SUCCESS"):
        raise RuntimeError(f"WhatsApp gateway: {body.get('code')}: {body.get('message')}")


async def send_signal(s: Settings, http: httpx.AsyncClient, chat_id: str, jpeg: bytes, caption: str) -> None:
    if not (s.signal_url and s.signal_number):
        raise RuntimeError("SIGNAL_URL and SIGNAL_NUMBER must be configured")
    attachment = "data:image/jpeg;filename=photo.jpg;base64," + base64.b64encode(jpeg).decode()
    r = await http.post(
        f"{s.signal_url}/v2/send",
        json={"number": s.signal_number, "recipients": [chat_id], "message": caption, "base64_attachments": [attachment]},
    )
    r.raise_for_status()


async def send_telegram(s: Settings, http: httpx.AsyncClient, chat_id: str, jpeg: bytes, caption: str) -> None:
    if not s.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    r = await http.post(
        f"https://api.telegram.org/bot{s.telegram_token}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1024]},
        files={"photo": ("photo.jpg", jpeg, "image/jpeg")},
    )
    r.raise_for_status()


SENDERS = {"whatsapp": send_whatsapp, "signal": send_signal, "telegram": send_telegram}
