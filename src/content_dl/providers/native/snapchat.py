"""Snapchat: share links resolved before routing, Spotlight videos and profiles read from the page.

The app shares ``snapchat.com/t/CODE`` links that redirect to the page, and profile links in the
``/add/USER`` form, both with share parameters (``share_id``, ``locale``, ``sid``). extract()
resolves them first: gallery-dl only matches ``/@USER`` profile URLs.

Snapchat's web pages moved to Next.js's app router (Nov 8, 2026), so the ``__NEXT_DATA__`` blob
that yt-dlp's and gallery-dl's Spotlight extractors read is gone from Spotlight pages. Every
page still carries schema.org JSON for search engines: a ``VideoObject`` with the MP4, poster,
date, duration and creator on a Spotlight page, and a ``ProfilePage`` on a profile. Highlights
and stories stay with gallery-dl, whose extractors still read those pages.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from content_dl.providers.base import ProviderError
from content_dl.providers.common import ext_from_url
from content_dl.schema import Item, Post, Profile

PROVIDER = "native"
ROOT = "https://www.snapchat.com"

JsonDict = dict[str, Any]

LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
USERNAME = re.compile(r"[A-Za-z0-9._-]{1,40}")
DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")

# INFO: An avatar URL ends in a resize suffix such as "._RS0,90_FMjpeg" (90px). "._FMjpeg" alone
# serves the uploaded size as a JPEG; the unwrapped original is served as binary/octet-stream.
AVATAR_SUFFIX = re.compile(r"\._RS[^/?]*?_FM(\w+)$")


def is_short_url(url: str) -> bool:
    """
    >>> is_short_url("https://snapchat.com/t/xP8xMiKB")
    True
    >>> is_short_url("https://www.snapchat.com/@matrix.001")
    False
    """
    return parse_url(url)[0] == "short"


def parse_url(url: str) -> tuple[str, str | None]:
    """Split a Snapchat URL into ``(kind, value)``.

    Kinds: ``short`` (the share code), ``spotlight`` (the Spotlight id), ``profile`` (the
    username), and ``other`` for highlights, stories and the rest, which gallery-dl reads.

    >>> parse_url("https://snapchat.com/t/xP8xMiKB")
    ('short', 'xP8xMiKB')
    >>> parse_url("https://www.snapchat.com/spotlight/W7_EDl?share_id=e")
    ('spotlight', 'W7_EDl')
    >>> parse_url("https://www.snapchat.com/@sheraz/spotlight/W7_EDl")
    ('spotlight', 'W7_EDl')
    >>> parse_url("https://www.snapchat.com/add/matrix.001?share_id=x")
    ('profile', 'matrix.001')
    >>> parse_url("https://www.snapchat.com/@matrix.001")
    ('profile', 'matrix.001')
    >>> parse_url("https://www.snapchat.com/@matrix.001/highlight/505c99ec")
    ('other', None)
    """
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) == 2 and segments[0] == "t":
        return "short", segments[1]

    if segments and segments[0].startswith("@"):
        username = segments[0][1:]
        if len(segments) == 1 and USERNAME.fullmatch(username):
            return "profile", username

        segments = segments[1:]

    if len(segments) >= 2 and segments[0] == "spotlight":
        return "spotlight", segments[1]

    if len(segments) == 2 and segments[0] == "add" and USERNAME.fullmatch(segments[1]):
        return "profile", segments[1]

    return "other", None


def clean_url(url: str) -> str:
    """The canonical form of a Snapchat URL: ``/add/USER`` becomes ``/@USER``, share parameters go.

    >>> clean_url("https://www.snapchat.com/add/matrix.001?share_id=xev&locale=en-US")
    'https://www.snapchat.com/@matrix.001'
    >>> clean_url("https://www.snapchat.com/spotlight/W7_EDl?share_id=e&sid=9d")
    'https://www.snapchat.com/spotlight/W7_EDl'
    >>> clean_url("https://snapchat.com/@matrix.001/highlight/505c")
    'https://www.snapchat.com/@matrix.001/highlight/505c'
    """
    kind, value = parse_url(url)
    if kind == "profile":
        return f"{ROOT}/@{value}"

    parts = urlsplit(url)
    host = "www.snapchat.com" if (parts.hostname or "").lower() == "snapchat.com" else parts.netloc
    return urlunsplit(parts._replace(netloc=host, query="", fragment=""))


def page_url(url: str) -> str:
    """The page native reads for a URL: the profile page, or the Spotlight page itself."""
    kind, value = parse_url(url)
    if kind == "profile":
        return f"{ROOT}/@{value}"

    if kind == "spotlight":
        return f"{ROOT}/spotlight/{value}"

    raise ProviderError("native: on Snapchat only Spotlight videos and profiles are read natively")


def ld_objects(page: str) -> list[JsonDict]:
    """Every schema.org object on the page, with the items of an ``ItemList`` left inside it."""
    objects = []
    for text in LD_JSON.findall(page):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue

        objects.extend(entry for entry in (data if isinstance(data, list) else [data]) if isinstance(entry, dict))

    return objects


def find(objects: list[JsonDict], kind: str) -> JsonDict | None:
    return next((entry for entry in objects if entry.get("@type") == kind), None)


def full_size_avatar(url: str) -> str:
    """
    >>> full_size_avatar("https://cf-st.sc-cdn.net/aps/bolt/aHR0c._RS0,90_FMjpeg")
    'https://cf-st.sc-cdn.net/aps/bolt/aHR0c._FMjpeg'
    """
    return AVATAR_SUFFIX.sub(r"._FM\1", url)


def parse_duration(value: Any) -> float | None:
    """
    >>> parse_duration("PT17S"), parse_duration("PT1M5.5S"), parse_duration(None)
    (17.0, 65.5, None)
    """
    match = DURATION.fullmatch(value) if isinstance(value, str) else None
    if not match or not any(match.groups()):
        return None

    hours, minutes, seconds = (float(group or 0) for group in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def avatar_item(url: Any) -> Item | None:
    if not isinstance(url, str) or not url:
        return None

    url = full_size_avatar(url)
    return Item(media_type="avatar", url=url, ext="jpg" if url.endswith("_FMjpeg") else ext_from_url(url), provider=PROVIDER)


def profile_from_person(person: JsonDict, platform: str) -> Profile:
    """A schema.org ``Person``. An anonymous Spotlight poster has only a ``userID``, no username or picture.

    >>> profile_from_person({"name": "Snapchat User", "identifier": {"propertyID": "userID", "value": "296d"}}, "snapchat")
    Profile(type='profile', platform='snapchat', id='296d', username=None, name='Snapchat User', url=None, avatar=None, items=[])
    """
    identifier = person.get("identifier") if isinstance(person.get("identifier"), dict) else {}
    username = _text(person.get("alternateName"))
    if not username and identifier.get("propertyID") == "Username":
        username = _text(identifier.get("value"))

    user_id = _text(identifier.get("value")) if identifier.get("propertyID") == "userID" else None
    return Profile(
        platform=platform,
        id=user_id or username,
        username=username,
        name=_text(person.get("name")),
        url=f"{ROOT}/@{username}" if username else _text(person.get("url")),
        avatar=avatar_item(person.get("image")),
    )


def post_from_page(page: str, url: str, platform: str = "snapchat") -> Post:
    """The Spotlight video on its page, from the ``VideoObject`` schema.org entry."""
    video = find(ld_objects(page), "VideoObject")
    if not video:
        raise ProviderError("native: Snapchat's page has no video data; the Spotlight may be removed")

    _, spotlight_id = parse_url(url)
    items: list[Item] = []
    content = _text(video.get("contentUrl"))
    if content:
        items.append(
            Item(
                index=1,
                media_type="video",
                url=content,
                ext="mp4",
                width=_int(video.get("width")),
                height=_int(video.get("height")),
                duration=parse_duration(video.get("duration")),
                provider=PROVIDER,
            )
        )

    poster = _text(video.get("thumbnailUrl"))
    if poster:
        items.append(Item(index=1, media_type="thumbnail", url=poster, ext="jpg", provider=PROVIDER))

    creator = video.get("creator")
    return Post(
        platform=platform,
        id=spotlight_id,
        title=_text(video.get("name")),
        description=_text(video.get("description")),
        upload_date=_text(video.get("uploadDate")),
        url=f"{ROOT}/spotlight/{spotlight_id}" if spotlight_id else url,
        uploader=profile_from_person(creator, platform) if isinstance(creator, dict) else None,
        items=items,
    )


def profile_from_page(page: str, platform: str = "snapchat") -> Profile:
    """The account on a profile page, from the ``ProfilePage`` schema.org entry."""
    profile_page = find(ld_objects(page), "ProfilePage")
    person = profile_page.get("mainEntity") if profile_page else None
    if not isinstance(person, dict):
        raise ProviderError("native: Snapchat's page has no profile data; the account may not be public")

    return profile_from_person(person, platform)
