"""URL helpers for X / Twitter."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

STATUS_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:twitter|x)\.com/[^/]+/status/(\d+)",
    re.IGNORECASE,
)
PROFILE_OR_MEDIA_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})(?:/(media))?/?$",
    re.IGNORECASE,
)


def normalize_url(url: str) -> str:
    u = url.strip()
    if not u.startswith("http"):
        u = "https://" + u
    return u


def extract_tweet_id(url: str) -> str | None:
    m = STATUS_RE.search(url)
    return m.group(1) if m else None


def extract_username_and_kind(url: str) -> tuple[str | None, str | None]:
    u = normalize_url(url)
    m = PROFILE_OR_MEDIA_RE.match(u)
    if not m:
        return None, None
    user = m.group(1)
    media_flag = m.group(2)
    if user.lower() in {"home", "explore", "settings", "messages", "notifications", "i", "intent", "search"}:
        return None, None
    if media_flag:
        return user, "media"
    return user, "profile"


def is_status_url(url: str) -> bool:
    return extract_tweet_id(url) is not None


def is_search_url(url: str) -> bool:
    parsed = urlparse(normalize_url(url))
    host = parsed.netloc.lower()
    return host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} and parsed.path.rstrip("/") == "/search"


def extract_search_query(url: str) -> str:
    parsed = urlparse(normalize_url(url))
    values = parse_qs(parsed.query).get("q") or []
    return values[0] if values else ""
