"""Settings from environment variables, plus the feeds file. Configuration only, no data.

One installation can serve several families (feeds) who don't know each other. Each feed is one Immich
album with its owner's key; everything not set per feed falls back to the environment."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from email.utils import formataddr, parseaddr
from pathlib import Path

import yaml

from .i18n import LANGUAGES

CHAT_KINDS = ("whatsapp", "signal", "telegram")
FEED_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESERVED_IDS = {"static", "healthz", "setup"}


def _list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def clean_email(value: str) -> str:
    """An address as typed, pasted or stored: lowercased, without surrounding whitespace and without
    invisible characters (zero-width spaces, soft hyphens, byte-order marks) that copy-paste drags along
    and that make mail silently go nowhere."""
    return "".join(ch for ch in str(value) if unicodedata.category(ch) != "Cf").strip().lower()


def _bool(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Installation-wide settings."""

    immich_url: str
    immich_public_url: str  # where people open Immich in their browser (for links on the setup page)
    admin_api_key: str
    secret_key: str
    link_version: str
    link_days: int  # how long a magic link in an email signs someone in
    chat_link_days: int  # the same for links posted in chats
    session_days: int  # how long a browser stays signed in after opening a link, renewed on every visit
    # Language of emails to relatives who haven't visited yet, and of group-chat messages (en, de, fr, fi, ru, uk)
    default_language: str
    user_prefix: str
    public_url: str
    data_dir: Path
    feeds_file: Path
    keys_file: Path
    setup_dir: Path
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_name: str
    install_admin_emails: list[str]
    # Create keys by signing in to Immich through its OAuth login, instead of pasting them (only used while
    # Immich has OAuth login on). Needs PUBLIC_URL/setup/immich-callback allowed for Immich's client at the provider.
    immich_oauth_connect: bool
    sync_minutes: int
    settle_minutes: int
    resend_hours: int
    photo_link_days: int  # how long a photo in an email stays viewable, for families who keep photos on the server
    keep_photos_default: bool  # whether families keep their photos on the server unless they switch it off
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_user: str
    smtp_password: str
    smtp_from: str
    whatsapp_url: str
    whatsapp_auth: str
    whatsapp_device: str  # the gateway device of families defined in .env/feeds.yml; empty: its only device
    signal_url: str
    signal_number: str
    telegram_token: str

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @classmethod
    def from_env(cls) -> Settings:
        _load_dotenv(Path(".env"))
        env = os.environ.get
        security = env("SMTP_SECURITY", "starttls").lower()
        settings = cls(
            immich_url=_required("IMMICH_URL").rstrip("/"),
            immich_public_url=env("IMMICH_PUBLIC_URL", env("IMMICH_URL", "")).rstrip("/"),
            admin_api_key=env("IMMICH_ADMIN_API_KEY", ""),
            secret_key=_required("SECRET_KEY"),
            link_version=env("LINK_VERSION", "1"),
            link_days=int(env("LINK_DAYS", "7")),
            chat_link_days=int(env("CHAT_LINK_DAYS", "30")),
            session_days=int(env("SESSION_DAYS", "90")),
            default_language=env("DEFAULT_LANGUAGE", "en").strip().lower(),
            user_prefix=env("USER_PREFIX", "patatina-"),
            public_url=_required("PUBLIC_URL").rstrip("/"),
            data_dir=Path(env("DATA_DIR", "/data")),
            feeds_file=_feeds_file(),
            keys_file=Path(env("KEYS_FILE", "/secrets/relatives.yml")),
            setup_dir=Path(env("SETUP_DIR", "/secrets/feeds")),
            oidc_issuer=env("OIDC_ISSUER", "").rstrip("/"),
            oidc_client_id=env("OIDC_CLIENT_ID", ""),
            oidc_client_secret=env("OIDC_CLIENT_SECRET", ""),
            oidc_name=env("OIDC_NAME", "Pocket ID"),
            install_admin_emails=_list(env("INSTALL_ADMIN_EMAILS")),
            immich_oauth_connect=_bool(env("IMMICH_OAUTH_CONNECT"), True),
            sync_minutes=int(env("SYNC_MINUTES", "15")),
            settle_minutes=int(env("SETTLE_MINUTES", "30")),
            resend_hours=int(env("RESEND_HOURS", "48")),
            photo_link_days=int(env("PHOTO_LINK_DAYS", "30")),
            keep_photos_default=_bool(env("KEEP_PHOTOS_ON_SERVER"), True),
            smtp_host=env("SMTP_HOST", ""),
            smtp_port=int(env("SMTP_PORT", "465" if security == "ssl" else "587")),
            smtp_security=security,
            smtp_user=env("SMTP_USER", ""),
            smtp_password=env("SMTP_PASSWORD", ""),
            smtp_from=env("SMTP_FROM", env("SMTP_USER", "")),
            whatsapp_url=env("WHATSAPP_URL", "").rstrip("/"),
            whatsapp_auth=env("WHATSAPP_AUTH", ""),
            whatsapp_device=env("WHATSAPP_DEVICE_ID", ""),
            signal_url=env("SIGNAL_URL", "").rstrip("/"),
            signal_number=env("SIGNAL_NUMBER", ""),
            telegram_token=env("TELEGRAM_BOT_TOKEN", ""),
        )
        if len(settings.secret_key) < 32:
            raise SystemExit("SECRET_KEY must be at least 32 characters (e.g. `openssl rand -base64 48`)")
        if settings.default_language not in LANGUAGES:
            raise SystemExit(f"DEFAULT_LANGUAGE must be one of {', '.join(LANGUAGES)}")
        if security not in {"starttls", "ssl", "none"}:
            raise SystemExit("SMTP_SECURITY must be 'starttls', 'ssl' or 'none'")
        if bool(settings.oidc_issuer) != bool(settings.oidc_client_id):
            raise SystemExit("Set both OIDC_ISSUER and OIDC_CLIENT_ID (or neither)")
        return settings


def _load_dotenv(path: Path) -> None:
    """When run from a checkout, read ./.env like docker compose's env_file (last occurrence wins);
    variables that are already set, e.g. by the container, take precedence."""
    if not path.is_file():
        return
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _feeds_file() -> Path:
    """FEEDS_FILE, else /config/feeds.yml (the container), else ./feeds.yml (running from the repo root)."""
    if os.environ.get("FEEDS_FILE"):
        return Path(os.environ["FEEDS_FILE"])
    container = Path("/config/feeds.yml")
    return container if container.exists() else Path("feeds.yml")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting {name}")
    return value


@dataclass(frozen=True)
class ChatSpec:
    """A chat that gets every moment of its feed. Immich has no notion of chats, so they live in config."""

    kind: str  # whatsapp | signal | telegram
    name: str
    address: str
    # Email of the album member this chat belongs to (a one-to-one chat). Its links then sign in as
    # that person; group-chat links sign in as the feed's bot account instead.
    person: str | None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.address}"

    @property
    def ref(self) -> str:
        """Short stable id used in magic links."""
        return hashlib.sha256(self.key.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class FeedConfig:
    """One family: an Immich album, its owner's key, and how and when it is delivered."""

    id: str  # appears in URLs: https://family.example.com/<id>/
    title: str
    album_id: str
    api_key: str  # the album owner's key
    contributor_keys: list[str]
    require_caption: bool
    # Photos are never attached to emails or chat messages: emails load them from this server through
    # expiring links, chats get the caption and a link only.
    keep_photos_on_server: bool
    send_backlog: bool
    timezone: str
    send_time: str
    send_mode: str
    bot_email: str
    bot_name: str
    smtp_from: str
    admin_emails: list[str]
    # GOWA device that sends this family's WhatsApp messages. None: no account linked yet;
    # "": the gateway's only device (no X-Device-Id header).
    whatsapp_device: str | None
    chats: list[ChatSpec] = field(default_factory=list)


def _chats(items) -> list[ChatSpec]:
    chats = []
    for c in items or []:
        kind = str(c["channel"]).strip().lower()
        if kind not in CHAT_KINDS:
            raise SystemExit(f"Unknown chat channel {kind!r}, expected one of {CHAT_KINDS}")
        address = str(c["chat_id"]).strip()
        person = clean_email(c["person"]) if c.get("person") else None
        chats.append(ChatSpec(kind, str(c.get("name") or address), address, person))
    return chats


def feed_config(raw: dict, settings: Settings) -> FeedConfig:
    env = os.environ.get
    feed_id = str(raw.get("id") or "").strip().lower()
    if not FEED_ID.match(feed_id) or feed_id in RESERVED_IDS:
        raise SystemExit(f"Feed id {feed_id!r} must be 1-32 lowercase letters, digits or dashes (and not {RESERVED_IDS})")
    for key in ("album_id", "api_key"):
        if not raw.get(key):
            raise SystemExit(f"Feed {feed_id!r} needs {key}")
    send_mode = str(raw.get("send_mode") or env("SEND_MODE", "digest")).lower()
    title = str(raw.get("title") or env("SITE_TITLE", "Family Feed"))
    if send_mode not in {"digest", "drip"}:
        raise SystemExit(f"Feed {feed_id!r}: send_mode must be 'digest' or 'drip'")
    return FeedConfig(
        id=feed_id,
        title=title,
        album_id=str(raw["album_id"]).strip(),
        api_key=str(raw["api_key"]).strip(),
        contributor_keys=_list(raw.get("contributor_keys")),
        require_caption=_bool(raw.get("require_caption", env("REQUIRE_CAPTION")), True),
        keep_photos_on_server=_bool(raw.get("keep_photos_on_server"), settings.keep_photos_default),
        send_backlog=_bool(raw.get("send_backlog", env("SEND_BACKLOG")), False),
        timezone=str(raw.get("timezone") or env("TZ", "UTC")),
        send_time=str(raw.get("send_time") or env("SEND_TIME", "07:00")),
        send_mode=send_mode,
        bot_email=str(raw.get("bot_email") or f"{feed_id}-chat@mail-a-patatina.invalid").strip().lower(),
        bot_name=str(raw.get("bot_name") or env("BOT_NAME", "Family chat")),
        # The family's title as the sender's name, at the installation's address (SMTP_FROM).
        smtp_from=str(raw.get("smtp_from") or formataddr((title, parseaddr(settings.smtp_from)[1]))),
        admin_emails=_list(raw.get("admin_emails", env("ADMIN_EMAILS"))),
        whatsapp_device=None if raw.get("whatsapp_device") is None else str(raw["whatsapp_device"]).strip(),
        chats=_chats(raw.get("chats")),
    )


def load_feeds(settings: Settings) -> list[FeedConfig]:
    """Families from feeds.yml (or the single family in .env) plus those created on the setup page."""
    from .store import FeedStore  # avoid an import cycle

    if settings.feeds_file.exists():
        data = yaml.safe_load(settings.feeds_file.read_text(encoding="utf-8")) or {}
        feeds = [feed_config({"whatsapp_device": settings.whatsapp_device} | raw, settings)
                 for raw in data.get("feeds") or []]
    elif os.environ.get("ALBUM_ID"):
        # Single-family setup straight from .env (plus the optional chats file).
        chats_file = Path(os.environ.get("CHATS_FILE", "/config/chats.yml"))
        chats = (yaml.safe_load(chats_file.read_text(encoding="utf-8")) or {}).get("chats") if chats_file.exists() else []
        feeds = [feed_config({
            "id": os.environ.get("FEED_ID", "family"),
            "album_id": os.environ["ALBUM_ID"],
            "api_key": os.environ.get("IMMICH_API_KEY"),
            "contributor_keys": os.environ.get("IMMICH_CONTRIBUTOR_KEYS"),
            "bot_email": os.environ.get("BOT_EMAIL"),
            "whatsapp_device": settings.whatsapp_device,
            "chats": chats,
        }, settings)]
    else:
        feeds = []
    static_ids = {f.id for f in feeds}
    if len(static_ids) != len(feeds):
        raise SystemExit("Feed ids must be unique")
    for feed_id, raw in FeedStore(settings.setup_dir).all().items():
        if feed_id in static_ids:
            raise SystemExit(f"Feed id {feed_id!r} is in feeds.yml and also set up on the setup page")
        feeds.append(feed_config(raw, settings))
    bots = [f.bot_email for f in feeds]
    if len(set(bots)) != len(bots):
        raise SystemExit("Each feed needs its own bot_email")
    return feeds
