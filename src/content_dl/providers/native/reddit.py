"""Reddit: posts read from the public embed page, profiles from ``about.json``, and share links resolved.

INFO: Since late 2026 Reddit answers logged-out requests to its JSON API and post pages with a
"blocked by network security" page, which is why yt-dlp and gallery-dl fail without cookies. The
embed page (``embed.reddit.com``) that sites use to show a post is still served logged out, and
it holds the post's title, author, date, subreddit, full gallery and HLS video stream.

``post_from_embed`` takes the embed page's HTML, so it can be tested against saved pages.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from content_dl.providers.base import ProviderError
from content_dl.providers.common import ext_from_url
from content_dl.schema import Item, Post, Profile, SchemaError

PROVIDER = "native"
ROOT = "https://www.reddit.com"
EMBED_ROOT = "https://embed.reddit.com"

SHARE_PATH = re.compile(r"/(?:r|u|user)/[\w-]+/s/\w+/?")
POST_PATH = re.compile(r"(?:/(?:r|u|user)/(?P<owner>[\w-]+))?/comments/(?P<id>[a-z0-9]+)(?:/.*)?", re.I)
GALLERY_PATH = re.compile(r"/gallery/(?P<id>[a-z0-9]+)/?", re.I)
PROFILE_PATH = re.compile(r"/(?:u|user)/(?P<name>[\w-]+)/?(?:(?:submitted|overview)/?)?", re.I)
COMMUNITY_PATH = re.compile(r"/r/(?P<name>[\w-]+)/?(?:(?:hot|new|top|rising)/?)?", re.I)
SHORT_HOSTS = frozenset({"redd.it"})

SCREENVIEW = re.compile(r"<shreddit-screenview-data\s+data=\"([^\"]+)\"")
TITLE = re.compile(r"<a id=\"embed-title\".*?<h1[^>]*>(.*?)</h1>", re.S)
PERMALINK = re.compile(r"<embed-event-handler\s+link=\"([^\"]+)\"")
AUTHOR = re.compile(r"href=\"https://www\.reddit\.com/user/([\w-]+)/\?[^\"]*utm_content=header\"")
TIMESTAMP = re.compile(r"<faceplate-timeago\s+ts=\"([^\"]+)\"")
PLAYER = re.compile(r"<shreddit-player\b([^>]*)>", re.S)
ATTRIBUTE = re.compile(r"([\w-]+)=\"([^\"]*)\"")
# INFO: A preview copy is named "<slug>-v0-<media id>.<ext>" (or just "<media id>.<ext>"), and the
# full-size original lives at i.redd.it/<media id>.<ext>. external-preview.redd.it copies (link and
# video posters) have no original there, so they're not matched.
MEDIA = re.compile(r"https://(?:preview|i)\.redd\.it/(?:[^\"'\s?]*-v0-)?(?P<id>[a-z0-9]{8,})\.(?P<ext>jpe?g|png|gif|webp)", re.I)

BLOCKED_MARKERS = ("blocked by network security", 'name="solution"')

JsonDict = dict[str, Any]


def is_share_url(url: str) -> bool:
    """Links from Reddit's share button, which redirect to the post, profile or community.

    >>> is_share_url("https://www.reddit.com/r/duolingo/s/5Cvq3YFWxS")
    True
    >>> is_share_url("https://www.reddit.com/u/someone/s/8maL82rtT2")
    True
    >>> is_share_url("https://redd.it/1xpk9zh")
    True
    >>> is_share_url("https://www.reddit.com/r/duolingo/comments/1xpk9zh/")
    False
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return host in SHORT_HOSTS or bool(SHARE_PATH.fullmatch(parts.path))


def clean_url(url: str) -> str:
    """A Reddit URL without its share and tracking parameters, with ``/u/`` spelled ``/user/``.

    >>> clean_url("https://www.reddit.com/u/someone?share_id=x&utm_source=share")
    'https://www.reddit.com/user/someone/'
    >>> clean_url("https://old.reddit.com/r/pics/comments/abc/title/?utm_term=1")
    'https://www.reddit.com/r/pics/comments/abc/title/'
    """
    path = urlsplit(url).path or "/"
    if path.startswith("/u/"):
        path = "/user/" + path[3:]

    return ROOT + (path if path.endswith("/") else path + "/")


def parse_url(url: str) -> tuple[str, str | None, str | None]:
    """Split a Reddit URL into ``(kind, name, post_id)``; kind is ``post``, ``profile`` or ``community``.

    For a post, ``name`` is the subreddit (or ``u_<user>`` for a profile post) when the URL has one.

    >>> parse_url("https://www.reddit.com/r/pics/comments/1xpk9zh/some_title/")
    ('post', 'pics', '1xpk9zh')
    >>> parse_url("https://www.reddit.com/user/spez/comments/abc123/")
    ('post', 'u_spez', 'abc123')
    >>> parse_url("https://www.reddit.com/gallery/1xl6f0e")
    ('post', None, '1xl6f0e')
    >>> parse_url("https://www.reddit.com/user/spez/")
    ('profile', 'spez', None)
    >>> parse_url("https://www.reddit.com/r/duolingo/")
    ('community', 'duolingo', None)
    """
    parts = urlsplit(url)
    path = parts.path or "/"
    if (parts.hostname or "").removeprefix("www.") in SHORT_HOSTS:
        return "post", None, path.strip("/") or None

    if match := POST_PATH.fullmatch(path):
        owner = match["owner"]
        if owner and re.match(r"/(?:u|user)/", path):
            owner = f"u_{owner}"

        return "post", owner, match["id"]

    if match := GALLERY_PATH.fullmatch(path):
        return "post", None, match["id"]

    if match := PROFILE_PATH.fullmatch(path):
        return "profile", match["name"], None

    if match := COMMUNITY_PATH.fullmatch(path):
        return "community", match["name"], None

    raise ProviderError("native: content-dl reads Reddit posts, profiles and communities; this URL is none of them")


def embed_url(subreddit: str | None, post_id: str) -> str:
    """
    >>> embed_url("pics", "abc123")
    'https://embed.reddit.com/r/pics/comments/abc123/'
    >>> embed_url(None, "abc123")
    'https://embed.reddit.com/comments/abc123/'
    """
    return f"{EMBED_ROOT}/r/{subreddit}/comments/{post_id}/" if subreddit else f"{EMBED_ROOT}/comments/{post_id}/"


def about_url(kind: str, name: str) -> str:
    return f"{ROOT}/user/{name}/about.json" if kind == "profile" else f"{ROOT}/r/{name}/about.json"


def is_blocked_page(text: str) -> bool:
    return any(marker in text[:20000] for marker in BLOCKED_MARKERS)


def _text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = html_lib.unescape(re.sub(r"<[^>]+>", "", value)).strip()
    return cleaned or None


def _screenview(page: str) -> JsonDict:
    match = SCREENVIEW.search(page)
    if not match:
        return {}

    try:
        data = json.loads(html_lib.unescape(match[1]))
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def _body(page: str) -> str:
    """The post part of the embed page: after the title, so the subreddit icon isn't taken for media."""
    start = page.find('id="embed-title"')
    return page[start:] if start >= 0 else page


def image_items(page: str) -> list[Item]:
    """Full-size copies of the post's images, numbered in gallery order.

    INFO: Each image appears several times (src, srcset widths, the full-size link), so they're
    told apart by media id and kept in their first-seen order, which is the gallery's order.
    """
    items: list[Item] = []
    seen: set[str] = set()
    for match in MEDIA.finditer(_body(page)):
        media_id, ext = match["id"], match["ext"].lower()
        if media_id in seen:
            continue

        seen.add(media_id)
        url = f"https://i.redd.it/{media_id}.{ext}"
        items.append(
            Item(
                index=len(items) + 1,
                media_type="image",
                url=url,
                ext="jpg" if ext == "jpeg" else ext,
                provider=PROVIDER,
                extra={"media_id": media_id},
            )
        )

    return items


def player_attributes(page: str) -> dict[str, str] | None:
    """The ``<shreddit-player>`` tag's attributes (HLS ``src``, ``poster``), or None for a post without video."""
    match = PLAYER.search(page)
    if not match:
        return None

    return {name: html_lib.unescape(value) for name, value in ATTRIBUTE.findall(match[1])}


def poster_item(player: dict[str, str]) -> Item | None:
    poster = player.get("poster")
    if not poster:
        return None

    return Item(index=1, media_type="thumbnail", url=poster, ext=ext_from_url(poster) or "jpg", provider=PROVIDER)


def post_from_embed(page: str, url: str, platform: str = "reddit", video_items: list[Item] | None = None) -> Post:
    """The post on a Reddit embed page.

    ``video_items`` are the video and audio streams already read from the player's HLS playlist
    (the caller fetches it); without them a video post keeps only its poster.
    """
    if is_blocked_page(page):
        raise ProviderError("native: Reddit blocked the embed page; try again later or pass --cookies")

    data = _screenview(page)
    post = data.get("post") if isinstance(data.get("post"), dict) else {}
    title = TITLE.search(page)
    if not post and not title:
        raise ProviderError("native: the Reddit embed page has no post (deleted, removed or changed layout)")

    permalink = PERMALINK.search(page)
    author = AUTHOR.search(page)
    timestamp = TIMESTAMP.search(page)
    post_id = str(post.get("id") or "").removeprefix("t3_") or parse_url(url)[2]

    player = player_attributes(page)
    if player:
        items = list(video_items or [])
        if not any(item.media_type == "thumbnail" for item in items) and (poster := poster_item(player)):
            items.append(poster)
    else:
        items = image_items(page)

    uploader = None
    if author:
        name = author[1]
        uploader = Profile(platform=platform, username=name, url=f"{ROOT}/user/{name}/")

    try:
        return Post(
            platform=platform,
            id=post_id,
            title=_text(title[1]) if title else None,
            description=None,
            upload_date=timestamp[1] if timestamp else None,
            url=html_lib.unescape(permalink[1]) if permalink else clean_url(url),
            uploader=uploader,
            items=items,
        )
    except SchemaError as error:
        raise ProviderError(f"native: Reddit returned data content-dl can't read: {error}") from error


def profile_from_about(text: str, kind: str, name: str, platform: str = "reddit") -> Profile:
    """A user's or community's icon from ``about.json``.

    >>> profile_from_about('{"data": {"name": "spez", "id": "1w72", "icon_img": "https://styles.redditmedia.com/a.png?s=1"}}', "profile", "spez").avatar.url
    'https://styles.redditmedia.com/a.png'
    """
    try:
        about = json.loads(text).get("data") or {}
    except (json.JSONDecodeError, AttributeError) as error:
        raise ProviderError("native: Reddit's profile lookup returned something content-dl can't read") from error

    if kind == "profile":
        icon = about.get("snoovatar_img") or about.get("icon_img")
        username = about.get("name") or name
        url = f"{ROOT}/user/{username}/"
        display = (about.get("subreddit") or {}).get("title") or None
    else:
        icon = about.get("community_icon") or about.get("icon_img")
        username = about.get("display_name") or name
        url = f"{ROOT}/r/{username}/"
        display = about.get("title") or None

    avatar = None
    if icon:
        # INFO: The query string holds a resize signature; without it styles.redditmedia.com serves the original.
        icon = html_lib.unescape(icon).split("?")[0]
        avatar = Item(index=1, media_type="avatar", url=icon, ext=ext_from_url(icon), provider=PROVIDER)

    return Profile(platform=platform, id=about.get("id"), username=username, name=display, url=url, avatar=avatar)
