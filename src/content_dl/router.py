"""Maps a URL to a platform, and a platform to the providers to try for each item kind.

The provider order follows the routing table in SPEC.md. Providers that aren't built yet
(cobalt, native, generic) are listed anyway and skipped at extraction time, so
adding one later only means registering it. The table moves to a JSON config in milestone 6.
"""

from __future__ import annotations

from urllib.parse import urlparse

from content_dl.schema import ItemKind

# INFO: Routing key for sites without their own row. It never appears in results: an unknown
# site's platform is its domain (``example.com``), so templates still make sensible folders.
UNKNOWN_SITE = "unknown"

# INFO: Matched against the hostname and every parent domain, so "m.youtube.com" and
# "old.reddit.com" resolve without listing each subdomain.
PLATFORM_DOMAINS: dict[str, str] = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "youtube-nocookie.com": "youtube",
    "instagram.com": "instagram",
    "instagr.am": "instagram",
    "threads.net": "threads",
    "threads.com": "threads",
    "x.com": "x",
    "twitter.com": "x",
    "tiktok.com": "tiktok",
    "reddit.com": "reddit",
    "redd.it": "reddit",
    "snapchat.com": "snapchat",
}

# The routing table: platform -> column -> providers, tried left to right.
# Columns: "video" covers video and audio, "images", "thumbnail", "avatar", "metadata".
ROUTES: dict[str, dict[str, list[str]]] = {
    "youtube": {
        "video": ["yt-dlp"],
        "images": [],
        "thumbnail": ["yt-dlp"],
        "avatar": ["yt-dlp"],
        "metadata": ["yt-dlp"],
    },
    "instagram": {
        "video": ["yt-dlp", "cobalt", "gallery-dl"],
        "images": ["gallery-dl", "cobalt"],
        "thumbnail": ["yt-dlp"],
        "avatar": ["native"],
        "metadata": ["gallery-dl", "yt-dlp"],
    },
    "threads": {
        "video": ["native"],
        "images": ["native"],
        "thumbnail": ["native"],
        "avatar": ["native"],
        "metadata": ["native"],
    },
    "x": {
        "video": ["yt-dlp", "cobalt"],
        "images": ["gallery-dl", "cobalt"],
        "thumbnail": ["yt-dlp"],
        "avatar": ["gallery-dl"],
        "metadata": ["yt-dlp", "gallery-dl"],
    },
    "tiktok": {
        "video": ["yt-dlp", "cobalt"],
        "images": ["gallery-dl"],
        "thumbnail": ["yt-dlp"],
        "avatar": ["gallery-dl"],
        "metadata": ["yt-dlp"],
    },
    "reddit": {
        "video": ["yt-dlp", "cobalt"],
        "images": ["gallery-dl"],
        "thumbnail": ["yt-dlp"],
        "avatar": ["gallery-dl"],
        "metadata": ["gallery-dl", "yt-dlp"],
    },
    "snapchat": {
        "video": ["cobalt"],
        "images": ["cobalt"],
        "thumbnail": ["cobalt"],
        "avatar": [],
        "metadata": ["native"],
    },
    UNKNOWN_SITE: {
        "video": ["yt-dlp", "gallery-dl", "generic"],
        "images": ["gallery-dl", "generic"],
        "thumbnail": ["generic"],
        "avatar": [],
        "metadata": ["yt-dlp", "generic"],
    },
}

# Item kind -> routing column, in the table's column order (used when every kind is wanted).
KIND_COLUMNS: dict[str, str] = {
    "video": "video",
    "audio": "video",
    "image": "images",
    "thumbnail": "thumbnail",
    "avatar": "avatar",
}


class RouterError(ValueError):
    """Raised for a URL the router can't work with."""


def detect_platform(url: str) -> str:
    """Return the platform a URL belongs to, or the site's domain for an unknown site.

    >>> detect_platform("https://youtu.be/jNQXAC9IVRw")
    'youtube'
    >>> detect_platform("https://old.reddit.com/r/pics/comments/abc/")
    'reddit'
    >>> detect_platform("https://www.Example.com/video.mp4")
    'example.com'
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RouterError(f"not an http(s) URL: {url!r}")

    labels = parsed.hostname.lower().rstrip(".").split(".")
    for start in range(len(labels) - 1):
        platform = PLATFORM_DOMAINS.get(".".join(labels[start:]))
        if platform:
            return platform

    return ".".join(labels[1:] if labels[0] == "www" and len(labels) > 2 else labels)


def routes_for(platform: str) -> dict[str, list[str]]:
    """Return a platform's row of the routing table. Unknown sites share one row."""
    return ROUTES.get(platform, ROUTES[UNKNOWN_SITE])


def columns_for(want: list[ItemKind] | None = None) -> list[str]:
    """Return the routing columns to fill, metadata first, without duplicates.

    >>> columns_for(["audio", "video", "avatar"])
    ['metadata', 'video', 'avatar']
    """
    kinds = want if want is not None else KIND_COLUMNS
    return list(dict.fromkeys(["metadata", *(KIND_COLUMNS[kind] for kind in kinds)]))


def providers_for(platform: str, want: list[ItemKind] | None = None) -> list[str]:
    """Return the providers to try for a platform, in priority order, without duplicates.

    Metadata providers come first because they decide the Post's fields; the providers for
    each wanted kind follow. ``want=None`` means every kind. A platform without its own row
    (an unknown site's domain) uses the unknown-site row.

    >>> providers_for("x", ["video"])
    ['yt-dlp', 'gallery-dl', 'cobalt']
    """
    routes = ROUTES.get(platform, ROUTES[UNKNOWN_SITE])
    kinds = want if want is not None else KIND_COLUMNS

    columns = ["metadata", *(KIND_COLUMNS[kind] for kind in kinds)]
    ordered: list[str] = []
    for column in columns:
        for provider in routes.get(column, []):
            if provider not in ordered:
                ordered.append(provider)

    return ordered
