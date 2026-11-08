"""X (Twitter): URL helpers the other providers use.

A profile URL is ``x.com/USER``. yt-dlp has no X profile extractor, and gallery-dl's queues the
whole timeline, so gallery-dl reads ``x.com/USER/photo`` instead, which returns the account and
its profile picture in one request.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

ROOT = "https://x.com"
USERNAME = re.compile(r"[A-Za-z0-9_]{1,15}")

# First path segments that are X's own pages, not usernames.
RESERVED_PATHS = frozenset(
    {
        "i",
        "home",
        "explore",
        "search",
        "notifications",
        "messages",
        "settings",
        "hashtag",
        "compose",
        "login",
        "logout",
        "signup",
        "tos",
        "privacy",
        "intent",
        "share",
        "jobs",
    }
)


def profile_username(url: str) -> str | None:
    """The username of a profile URL, or None for any other X URL.

    >>> profile_username("https://x.com/sarahli"), profile_username("https://twitter.com/sarahli/?s=19")
    ('sarahli', 'sarahli')
    >>> profile_username("https://x.com/sarahli/status/2115137555075354846") is None
    True
    >>> profile_username("https://x.com/explore") is None
    True
    """
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) != 1 or segments[0].lower() in RESERVED_PATHS or not USERNAME.fullmatch(segments[0]):
        return None

    return segments[0]


def avatar_page(username: str) -> str:
    """The URL gallery-dl's avatar extractor reads for a profile.

    >>> avatar_page("sarahli")
    'https://x.com/sarahli/photo'
    """
    return f"{ROOT}/{username}/photo"
