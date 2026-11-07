"""Small helpers shared by the providers."""

from __future__ import annotations

from urllib.parse import urlparse


def ext_from_url(url: str) -> str | None:
    """Return the file extension in a URL's path.

    >>> ext_from_url("https://i.ytimg.com/vi/abc/hqdefault.jpg?sqp=xyz")
    'jpg'
    >>> ext_from_url("https://example.com/image") is None
    True
    """
    last = urlparse(url).path.rsplit("/", 1)[-1]
    if "." not in last:
        return None

    ext = last.rsplit(".", 1)[-1].lower()
    return ext if ext.isalnum() and len(ext) <= 5 else None
