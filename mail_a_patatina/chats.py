"""Chat channels. Each one posts a single photo with a caption to a chat or group, or just the caption
(with its link) for families who keep their photos on the server (jpeg None).

WhatsApp goes through a GOWA sidecar (each family on its own linked account), Signal through signal-cli-rest-api, Telegram through the Bot API.
"""

from __future__ import annotations

import base64

import httpx

from .config import FeedConfig, Settings
from .whatsapp import Gateway


async def send_whatsapp(s: Settings, http: httpx.AsyncClient, feed: FeedConfig, chat_id: str, jpeg: bytes | None,
                        caption: str) -> None:
    if feed.whatsapp_device is None:
        raise RuntimeError("No WhatsApp account is linked to this feed (setup page → Family chats)")
    gateway = Gateway(s, http)
    if jpeg is None:
        await gateway.send_text(feed.whatsapp_device, chat_id, caption)
    else:
        await gateway.send_image(feed.whatsapp_device, chat_id, jpeg, caption)


async def send_signal(s: Settings, http: httpx.AsyncClient, feed: FeedConfig, chat_id: str, jpeg: bytes | None,
                      caption: str) -> None:
    if not (s.signal_url and s.signal_number):
        raise RuntimeError("SIGNAL_URL and SIGNAL_NUMBER must be configured")
    message = {"number": s.signal_number, "recipients": [chat_id], "message": caption}
    if jpeg is not None:
        message["base64_attachments"] = ["data:image/jpeg;filename=photo.jpg;base64," + base64.b64encode(jpeg).decode()]
    r = await http.post(f"{s.signal_url}/v2/send", json=message)
    r.raise_for_status()


async def send_telegram(s: Settings, http: httpx.AsyncClient, feed: FeedConfig, chat_id: str, jpeg: bytes | None,
                        caption: str) -> None:
    if not s.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    if jpeg is None:
        r = await http.post(f"https://api.telegram.org/bot{s.telegram_token}/sendMessage", json={
            "chat_id": chat_id, "text": caption, "link_preview_options": {"is_disabled": True}})
        r.raise_for_status()
        return
    r = await http.post(
        f"https://api.telegram.org/bot{s.telegram_token}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1024]},
        files={"photo": ("photo.jpg", jpeg, "image/jpeg")},
    )
    r.raise_for_status()


SENDERS = {"whatsapp": send_whatsapp, "signal": send_signal, "telegram": send_telegram}
