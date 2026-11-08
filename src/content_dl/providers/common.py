"""Small helpers shared by the providers."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def ext_from_url(url: str) -> str | None:
    """Return the file extension in a URL's path, or its ``format`` parameter when the path has none.

    INFO: X's image URLs carry the type in the query (``/media/ID?format=jpg&name=orig``).

    >>> ext_from_url("https://i.ytimg.com/vi/abc/hqdefault.jpg?sqp=xyz")
    'jpg'
    >>> ext_from_url("https://pbs.twimg.com/media/HT-vyDvXAAAKIQi?format=jpg&name=orig")
    'jpg'
    >>> ext_from_url("https://example.com/image") is None
    True
    """
    parts = urlparse(url)
    last = parts.path.rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)[-1].lower() if "." in last else (parse_qs(parts.query).get("format") or [""])[0].lower()
    return ext if ext and ext.isalnum() and len(ext) <= 5 else None
