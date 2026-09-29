"""URL validation. Only links on known platform hosts are ever handed to yt-dlp."""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048

PLATFORM_HOSTS: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "instagram": ("instagram.com", "instagr.am"),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com", "fb.watch", "fb.com"),
}

PLATFORM_NAMES = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
}

_HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def validate_url(raw: object) -> tuple[str, str] | None:
    """Returns (normalized_url, platform_id), or None if the URL isn't an allowed platform link."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > MAX_URL_LENGTH or re.search(r"[\s\x00-\x1f\x7f]", value):
        return None
    if not re.match(r"^https?://", value, re.I):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.username or parts.password or port not in (None, 80, 443):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or not _HOST_RE.match(host):
        return None
    try:
        ipaddress.ip_address(host)
        return None  # IP literals are never platform links
    except ValueError:
        pass

    bare = host[4:] if host.startswith("www.") else host
    for platform, hosts in PLATFORM_HOSTS.items():
        if any(bare == h or bare.endswith("." + h) for h in hosts):
            normalized = urlunsplit(("https", host, parts.path or "/", parts.query, ""))
            return normalized, platform
    return None
