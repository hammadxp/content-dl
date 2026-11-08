"""Threads: posts and profiles, read from the JSON embedded in the page.

A Threads page carries its data in ``<script type="application/json" data-sjs>`` blobs, nested
deep inside Relay payloads. Rather than follow one exact path, which Threads changes without
notice, the parser walks every blob for the objects it needs:

- a post is a dict whose ``code`` is the URL's shortcode and which has ``taken_at`` and ``user``
  (a carousel's children have their own ``code`` but no ``taken_at``);
- a user is a dict whose ``username`` matches and which has ``profile_pic_url``.

``post_from_page`` and ``profile_from_page`` take the page's HTML, so they can be tested
against saved pages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from content_dl.providers.base import ProviderError
from content_dl.providers.common import ext_from_url
from content_dl.schema import Item, Post, Profile, SchemaError

PROVIDER = "native"
ROOT = "https://www.threads.com"

JSON_SCRIPT = re.compile(r"<script type=\"application/json\"[^>]*>(.*?)</script>", re.S)
POST_PATH = re.compile(r"/@(?P<username>[\w.]+)/post/(?P<code>[\w-]+)/?")
SHORT_POST_PATH = re.compile(r"/t/(?P<code>[\w-]+)/?")
PROFILE_PATH = re.compile(r"/@(?P<username>[\w.]+)/?")
SIZE_HINT = re.compile(r"[?&]stp=[^&]*?_s(?P<width>\d+)x(?P<height>\d+)(?:_|&|$)")
SHARE_PATH = re.compile(r"/share/(?P<code>[\w-]+)/?")

# Threads' media_type values (shared with Instagram).
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_CAROUSEL = 8

JsonDict = dict[str, Any]


def is_share_url(url: str) -> bool:
    """A ``/share/<code>`` link, which redirects to the post page. Its code isn't the post's shortcode.

    >>> is_share_url("https://www.threads.com/share/BALHonL4O-")
    True
    >>> is_share_url("https://www.threads.com/@someone/post/DfaNgswkQPG")
    False
    """
    return bool(SHARE_PATH.fullmatch(urlsplit(url).path))


def clean_url(url: str) -> str:
    """A post or profile URL without its share parameters (``xmt``, ``slof``).

    >>> clean_url("https://www.threads.com/@someone/post/DfaNgswkQPG?xmt=abc&slof=1")
    'https://www.threads.com/@someone/post/DfaNgswkQPG'
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def parse_url(url: str) -> tuple[str, str | None, str | None]:
    """Split a Threads URL into ``(kind, username, code)``; kind is ``post`` or ``profile``.

    >>> parse_url("https://www.threads.net/@someone/post/DfaNgswkQPG?xmt=abc")
    ('post', 'someone', 'DfaNgswkQPG')
    >>> parse_url("https://www.threads.com/t/DfaNgswkQPG")
    ('post', None, 'DfaNgswkQPG')
    >>> parse_url("https://threads.com/@someone")
    ('profile', 'someone', None)
    """
    path = urlsplit(url).path
    if match := POST_PATH.fullmatch(path):
        return "post", match["username"], match["code"]

    if match := SHORT_POST_PATH.fullmatch(path):
        return "post", None, match["code"]

    if match := PROFILE_PATH.fullmatch(path):
        return "profile", match["username"], None

    raise ProviderError("native: not a Threads post or profile URL")


def json_blobs(html: str) -> list[Any]:
    """Every JSON script on the page. Blobs that don't parse are skipped."""
    blobs = []
    for text in JSON_SCRIPT.findall(html):
        try:
            blobs.append(json.loads(text))
        except ValueError:
            continue

    return blobs


def _walk(value: Any) -> Iterator[JsonDict]:
    # INFO: Iterative, so a deeply nested Relay payload can never hit Python's recursion limit.
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))


def find_post(blobs: list[Any], code: str) -> JsonDict | None:
    for blob in blobs:
        for node in _walk(blob):
            if node.get("code") == code and "taken_at" in node and isinstance(node.get("user"), dict):
                return node

    return None


def find_user(blobs: list[Any], username: str) -> JsonDict | None:
    """The richest user object for ``username``: one with HD avatars wins, then the one with most fields."""
    users = [
        node
        for blob in blobs
        for node in _walk(blob)
        if node.get("username") == username and "profile_pic_url" in node
    ]
    if not users:
        return None

    return max(users, key=lambda user: (bool(user.get("hd_profile_pic_versions")), len(user)))


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    return value.strip() or None


def _size(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _id(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def avatar_from_user(user: JsonDict) -> Item | None:
    """The largest HD avatar, or the small ``profile_pic_url`` (sized from its URL)."""
    versions = [version for version in user.get("hd_profile_pic_versions") or [] if version.get("url")]
    if versions:
        best = max(versions, key=lambda version: (_size(version.get("width")) or 0))
        url = best["url"]
        return Item(
            media_type="avatar",
            url=url,
            ext=ext_from_url(url),
            width=_size(best.get("width")),
            height=_size(best.get("height")),
            provider=PROVIDER,
        )

    url = _text(user.get("profile_pic_url"))
    if not url:
        return None

    size = _size_hint(url)
    return Item(
        media_type="avatar",
        url=url,
        ext=ext_from_url(url),
        width=size[0] if size else None,
        height=size[1] if size else None,
        provider=PROVIDER,
    )


def _size_hint(url: str) -> tuple[int, int] | None:
    """The square size in a CDN URL's ``stp`` resize step.

    >>> _size_hint("https://cdn.example/a.jpg?stp=dst-jpg_s150x150_tt6&x=1")
    (150, 150)
    >>> _size_hint("https://cdn.example/a.jpg") is None
    True
    """
    match = SIZE_HINT.search(url)
    return (int(match["width"]), int(match["height"])) if match else None


def profile_from_user(user: JsonDict, platform: str) -> Profile:
    username = _text(user.get("username"))
    return Profile(
        platform=platform,
        id=_id(user.get("pk") or user.get("id")),
        username=username,
        name=_text(user.get("full_name")),
        url=f"{ROOT}/@{username}" if username else None,
        avatar=avatar_from_user(user),
    )


def _best_image(media: JsonDict) -> JsonDict | None:
    candidates = [c for c in (media.get("image_versions2") or {}).get("candidates") or [] if c.get("url")]
    if not candidates:
        return None

    return max(candidates, key=lambda c: (_size(c.get("width")) or 0) * (_size(c.get("height")) or 0))


def items_from_media(media: JsonDict, index: int) -> list[Item]:
    """One media entry's items: a video and its cover image, or an image.

    The video's size is the media's ``original_width`` and ``original_height``; Threads doesn't
    give a duration in the page, so it stays unset.
    """
    extra = {"media_id": _id(media.get("pk")), "media_type": media.get("media_type"), "code": media.get("code")}
    extra = {key: value for key, value in extra.items() if value is not None} or None

    items: list[Item] = []
    videos = [video for video in media.get("video_versions") or [] if video.get("url")]
    if videos:
        # INFO: The versions differ by "type" (101-103) but point at the same progressive MP4.
        url = videos[0]["url"]
        items.append(
            Item(
                index=index,
                media_type="video",
                url=url,
                ext=ext_from_url(url) or "mp4",
                width=_size(media.get("original_width")),
                height=_size(media.get("original_height")),
                provider=PROVIDER,
                extra=extra,
            )
        )

    image = _best_image(media)
    if image:
        items.append(
            Item(
                index=index,
                media_type="thumbnail" if videos else "image",
                url=image["url"],
                ext=ext_from_url(image["url"]),
                width=_size(image.get("width")),
                height=_size(image.get("height")),
                provider=PROVIDER,
                extra=extra,
            )
        )

    return items


def _own_items(media: JsonDict) -> list[Item]:
    carousel = media.get("carousel_media") or []
    if carousel:
        return [item for position, entry in enumerate(carousel, 1) for item in items_from_media(entry, position)]

    return items_from_media(media, 1)


def items_from_post(post: JsonDict) -> list[Item]:
    """A carousel's entries are numbered from 1; a single image or video is index 1. Text posts have none.

    INFO: A post that shares an Instagram post (``media_type`` 19, no media of its own) shows that
    post's photos and videos inline; they sit in ``text_post_app_info.linked_inline_media``.
    """
    items = _own_items(post)
    if items:
        return items

    linked = (post.get("text_post_app_info") or {}).get("linked_inline_media")
    return _own_items(linked) if isinstance(linked, dict) else []


def post_from_page(html: str, url: str, platform: str = "threads") -> Post:
    _, _, code = parse_url(url)
    blobs = json_blobs(html)
    post = find_post(blobs, code or "")
    if post is None:
        raise ProviderError("native: the Threads page has no data for this post (deleted, private or changed layout)")

    author = post["user"]
    username = _text(author.get("username"))
    # The post's own user object is a short one; the page may hold a fuller one with HD avatars.
    richer = find_user(blobs, username) if username else None
    uploader = profile_from_user({**author, **(richer or {})}, platform)

    try:
        return Post(
            platform=platform,
            id=_text(post.get("code")),
            title=None,
            description=_text((post.get("caption") or {}).get("text")),
            upload_date=post.get("taken_at"),
            url=_text(post.get("permalink")) or (f"{ROOT}/@{username}/post/{code}" if username else None),
            uploader=uploader,
            items=items_from_post(post),
        )
    except SchemaError as error:
        raise ProviderError(f"native: Threads returned data content-dl can't read: {error}") from error


def profile_from_page(html: str, url: str, platform: str = "threads") -> Profile:
    """A profile's avatar and names. ``items`` stays empty: whole feeds are out of scope for v1."""
    _, username, _ = parse_url(url)
    user = find_user(json_blobs(html), username or "")
    if user is None:
        raise ProviderError("native: the Threads page has no data for this profile (missing, private or changed layout)")

    return profile_from_user(user, platform)
