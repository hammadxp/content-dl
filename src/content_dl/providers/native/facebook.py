"""Facebook: share links resolved to the page they open, profile URLs recognised, and post pages read.

Facebook's app shares ``/share/...`` links (and ``fb.watch`` for videos) that only redirect.
yt-dlp follows them itself, but gallery-dl matches URL patterns and needs the real address, so
extract() resolves them first. A logged-out request for a post that needs a login lands on the
login page, which still carries the post's address in its ``next`` parameter.

Neither yt-dlp (videos only) nor gallery-dl (photos, albums, profiles) reads a regular post, so
``post_from_page`` does: the author, text, date and each photo, from the JSON the logged-in page
embeds. A post page only holds reduced copies of its photos, so each photo also carries its photo
page, where gallery-dl's photo extractor finds the full size.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from content_dl.providers.base import Cookies, ProviderError
from content_dl.providers.common import ext_from_url
from content_dl.schema import Item, Post, Profile

# Query parameters Facebook adds to a resolved share link that only track the share.
TRACKING_PARAMS = frozenset({"rdid", "share_url", "mibextid", "_fb_noscript", "sfnsn", "s", "app"})

# First path segments that are Facebook pages, not profile names.
RESERVED_PATHS = frozenset(
    {
        "share", "reel", "reels", "watch", "videos", "photo", "photo.php", "photos", "media", "story.php",
        "permalink.php", "login", "groups", "events", "marketplace", "gaming", "hashtag", "pages", "help",
        "settings", "notifications", "messages", "stories", "posts", "privacy", "policies", "plugins",
    }
)

PROFILE_NAME = re.compile(r"[A-Za-z0-9.]{1,80}")


def is_share_url(url: str) -> bool:
    """``/share/...`` links and ``fb.watch`` links only redirect to the real page.

    >>> is_share_url("https://www.facebook.com/share/p/19ak87B4rz/")
    True
    >>> is_share_url("https://fb.watch/abc123/")
    True
    >>> is_share_url("https://www.facebook.com/reel/857028250766757")
    False
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "fb.watch" or host.endswith(".fb.watch"):
        return True

    return parts.path.lower().startswith("/share/")


def clean_resolved_url(url: str) -> str:
    """Unwrap a login redirect to the page behind it, and drop the share's tracking parameters.

    >>> clean_resolved_url("https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fstory.php"
    ...                    "%3Fstory_fbid%3D12%26id%3D34%26rdid%3Dx&_fb_noscript=1")
    'https://www.facebook.com/story.php?story_fbid=12&id=34'
    >>> clean_resolved_url("https://www.facebook.com/people/Some-One/100/?rdid=x&share_url=y")
    'https://www.facebook.com/people/Some-One/100/'
    """
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if parts.path.rstrip("/").endswith("/login") and query.get("next"):
        return clean_resolved_url(query["next"][0])

    kept = [(key, value) for key, values in query.items() if key not in TRACKING_PARAMS for value in values]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def resolve_share_url(url: str, cookies: Cookies | None = None) -> str:
    """The page a share link opens. On a network error the link is returned unchanged.

    The user's cookies go along, so a post that needs a login resolves to the post itself.
    """
    if not is_share_url(url):
        return url

    try:
        return clean_resolved_url(follow_redirects(url, cookies))
    except ProviderError:
        return url


def follow_redirects(url: str, cookies: Cookies | None) -> str:
    # INFO: Imported here so the provider modules don't import each other at load time.
    from content_dl.providers.ytdlp import final_url

    return final_url(url, cookies)


def profile_id(url: str) -> str | None:
    """The profile a URL points at (a numeric id or a vanity name), or None for any other page.

    gallery-dl takes both forms in ``https://www.facebook.com/ID/avatar``.

    >>> profile_id("https://www.facebook.com/people/Gaffar-Musafir/100064025019966/")
    '100064025019966'
    >>> profile_id("https://www.facebook.com/profile.php?id=100064025019966")
    '100064025019966'
    >>> profile_id("https://www.facebook.com/zuck")
    'zuck'
    >>> profile_id("https://www.facebook.com/story.php?story_fbid=12&id=34") is None
    True
    >>> profile_id("https://www.facebook.com/zuck/posts/pfbid0abc") is None
    True
    """
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if segments == ["profile.php"]:
        ids = parse_qs(parts.query).get("id")
        return ids[0] if ids and ids[0].isdigit() else None

    if len(segments) == 3 and segments[0] == "people" and segments[2].isdigit():
        return segments[2]

    if len(segments) == 1 and segments[0].lower() not in RESERVED_PATHS and PROFILE_NAME.fullmatch(segments[0]):
        return segments[0]

    return None


def is_post_url(url: str) -> bool:
    """A regular post: ``story.php``, ``permalink.php`` or ``/NAME/posts/ID``.

    >>> is_post_url("https://www.facebook.com/story.php?story_fbid=12&id=34")
    True
    >>> is_post_url("https://www.facebook.com/someone/posts/pfbid0abc")
    True
    >>> is_post_url("https://www.facebook.com/photo/?fbid=12")
    False
    """
    segments = [segment.lower() for segment in urlsplit(url).path.split("/") if segment]
    if segments and segments[0] in ("story.php", "permalink.php"):
        return True

    return len(segments) >= 3 and segments[1] == "posts"


def profile_name_from_url(url: str) -> str | None:
    """The display name a ``/people/Some-Name/ID/`` URL carries.

    >>> profile_name_from_url("https://www.facebook.com/people/Gaffar-Musafir/100064025019966/")
    'Gaffar Musafir'
    """
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) == 3 and segments[0] == "people":
        return segments[1].replace("-", " ")

    return None


PROVIDER = "native"
ROOT = "https://www.facebook.com"

# INFO: A logged-in page embeds its data in these script blocks; the post's own objects carry its
# post_id, which keeps the comments, ads and suggested posts on the same page out.
JSON_BLOCK = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)


def story_fbid(url: str) -> str | None:
    """The post id a ``story.php`` or ``permalink.php`` URL carries; ``/NAME/posts/ID`` ids are opaque.

    >>> story_fbid("https://www.facebook.com/story.php?story_fbid=122&id=615")
    '122'
    """
    ids = parse_qs(urlsplit(url).query).get("story_fbid")
    return ids[0] if ids else None


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def page_objects(html: str) -> Iterator[dict[str, Any]]:
    """Every JSON object in the page's data blocks."""
    for block in JSON_BLOCK.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue

        yield from _walk(data)


def _photos(attachments: Any) -> list[dict[str, Any]]:
    """The Photo media of a post's attachments, in order: a single photo, or every photo of a set."""
    # INFO: The same photo appears more than once: as a bare {"id"} reference and as the styled
    # renderer's full media. Copies are merged by id, so the photo keeps every field either has.
    photos: dict[str, dict[str, Any]] = {}
    for attachment in attachments if isinstance(attachments, list) else []:
        styled = (attachment.get("styles") or {}).get("attachment") or {}
        nested = ((styled.get("all_subattachments") or attachment.get("all_subattachments") or {}).get("nodes")) or []
        for media in [attachment.get("media"), styled.get("media"), *(node.get("media") for node in nested)]:
            if isinstance(media, dict) and media.get("__typename") == "Photo" and media.get("id"):
                merged = photos.setdefault(str(media["id"]), {})
                merged.update({key: value for key, value in media.items() if key not in merged})

    return list(photos.values())


def _photo_item(index: int, media: dict[str, Any]) -> Item | None:
    image = media.get("photo_image") or media.get("image") or {}
    url = image.get("uri") if isinstance(image, dict) else None
    if not isinstance(url, str) or not url:
        return None

    width, height = image.get("width"), image.get("height")
    extra: dict[str, Any] = {
        "media_id": str(media["id"]),
        "photo_page": media.get("url") or f"{ROOT}/photo/?fbid={media['id']}",
    }
    # The photo page's full size, for the full-size copy gallery-dl fetches there (gallery-dl reports none).
    full = media.get("viewer_image")
    if isinstance(full, dict) and isinstance(full.get("width"), int) and isinstance(full.get("height"), int):
        extra["full_width"], extra["full_height"] = full["width"], full["height"]

    return Item(
        index=index,
        media_type="image",
        url=url,
        ext=ext_from_url(url),
        width=width if isinstance(width, int) and width > 0 else None,
        height=height if isinstance(height, int) and height > 0 else None,
        provider=PROVIDER,
        extra=extra,
    )


def _uploader(actor_lists: list[Any], platform: str) -> Profile | None:
    """The post's first actor, merged from every copy of the post: one copy has the name, another the link."""
    actor: dict[str, Any] = {}
    for actors in actor_lists:
        first = actors[0] if isinstance(actors, list) and actors and isinstance(actors[0], dict) else None
        if first and (not actor or first.get("id") == actor.get("id")):
            actor.update({key: value for key, value in first.items() if value and key not in actor})

    if not actor.get("id"):
        return None

    actor_id = str(actor["id"])
    url = actor.get("url") if isinstance(actor.get("url"), str) else None
    return Profile(platform=platform, id=actor_id, name=actor.get("name") or None, url=url or f"{ROOT}/{actor_id}")


def post_from_page(html: str, url: str, platform: str = "facebook") -> Post:
    """Translate a logged-in Facebook post page into a Post with its photos.

    A post's data is spread over several objects with the same ``post_id``: the one holding its
    attachments and creation time is the post itself, and another holds the text.
    """
    wanted = story_fbid(url)
    objects = [obj for obj in page_objects(html) if isinstance(obj.get("post_id"), str)]
    posts = [obj for obj in objects if "attachments" in obj and "creation_time" in obj]
    if wanted:
        posts = [obj for obj in posts if obj["post_id"] == wanted]

    if not posts:
        raise ProviderError("native: Facebook sent no post data; the post may need --cookies, or was removed")

    post = posts[0]
    post_id = post["post_id"]
    same_post = [obj for obj in objects if obj["post_id"] == post_id]

    message = next(
        (
            obj["message"]["text"]
            for obj in same_post
            if isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("text"), str)
        ),
        None,
    )
    actors = [obj["actors"] for obj in same_post if obj.get("actors")]
    created = post.get("creation_time")
    photos = _photos(post.get("attachments"))
    items = [item for index, media in enumerate(photos, 1) if (item := _photo_item(index, media))]

    return Post(
        platform=platform,
        id=post_id,
        description=message.strip() or None if message else None,
        upload_date=datetime.fromtimestamp(created, tz=timezone.utc) if isinstance(created, int) else None,
        url=url,
        uploader=_uploader(actors, platform),
        items=items,
    )
