"""TikTok: share links resolved before routing, and URL helpers the other providers use.

The app shares ``tiktok.com/t/CODE`` (and ``vm.``/``vt.tiktok.com/CODE``) links that redirect to
the post page with a long tail of share parameters (``_r``, ``_t``, ``share_*``, ``u_code`` and
more). extract() resolves them first: yt-dlp's short-link extractor can't follow photo posts, and
gallery-dl only hands the resolved URL on to another extractor, which content-dl reads as a
collection.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

ROOT = "https://www.tiktok.com"
SHORT_HOSTS = frozenset({"vm.tiktok.com", "vt.tiktok.com"})
USERNAME = re.compile(r"[A-Za-z0-9._]{1,40}")


def is_short_url(url: str) -> bool:
    """
    >>> is_short_url("https://www.tiktok.com/t/ZPLVSx3Ey/"), is_short_url("https://vm.tiktok.com/ZMabc/")
    (True, True)
    >>> is_short_url("https://www.tiktok.com/@someone/video/1")
    False
    """
    return parse_url(url)[0] == "short"


def parse_url(url: str) -> tuple[str, str | None]:
    """Split a TikTok URL into ``(kind, value)``.

    Kinds: ``short`` (the share code), ``profile`` (the username), ``post`` (the post id, video
    or photo), and ``other`` for the rest.

    >>> parse_url("https://www.tiktok.com/t/ZPLVSx3Ey/")
    ('short', 'ZPLVSx3Ey')
    >>> parse_url("https://www.tiktok.com/@agonistech?_r=1&_t=ZP-9")
    ('profile', 'agonistech')
    >>> parse_url("https://www.tiktok.com/@usmanramzan/photo/7700208115660164359?_r=1")
    ('post', '7700208115660164359')
    >>> parse_url("https://www.tiktok.com/tag/cats")
    ('other', None)
    """
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if (parts.hostname or "").lower() in SHORT_HOSTS and len(segments) == 1:
        return "short", segments[0]

    if len(segments) == 2 and segments[0] == "t":
        return "short", segments[1]

    if segments and segments[0].startswith("@") and USERNAME.fullmatch(segments[0][1:]):
        if len(segments) == 1:
            return "profile", segments[0][1:]

        if len(segments) == 3 and segments[1] in ("video", "photo") and segments[2].isdigit():
            return "post", segments[2]

    return "other", None


def clean_url(url: str) -> str:
    """The URL without share parameters or a fragment. A short link is left alone.

    >>> clean_url("https://www.tiktok.com/@usmanramzan/video/7705203371606560018?_d=x&story_type=1")
    'https://www.tiktok.com/@usmanramzan/video/7705203371606560018'
    >>> clean_url("https://m.tiktok.com/@agonistech/?_r=1")
    'https://www.tiktok.com/@agonistech'
    """
    kind, _ = parse_url(url)
    if kind == "short":
        return url

    parts = urlsplit(url)
    if kind in ("profile", "post"):
        return urlunsplit(("https", "www.tiktok.com", parts.path.rstrip("/"), "", ""))

    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def avatar_page(username: str) -> str:
    """The URL gallery-dl's avatar extractor reads for a profile.

    >>> avatar_page("agonistech")
    'https://www.tiktok.com/@agonistech/avatar'
    """
    return f"{ROOT}/@{username}/avatar"
