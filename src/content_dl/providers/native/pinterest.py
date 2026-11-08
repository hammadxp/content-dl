"""Pinterest: pin.it links resolved before routing, board invites turned into boards, and profiles read.

The app shares ``pin.it`` links that redirect to the page with share parameters
(``invite_code``, ``sender``) on it. gallery-dl matches profile URLs only without a query, and
neither tool knows a profile or an ``/invited/`` page, so extract() resolves the link first.

A board invitation (``/invited/?board_id=...``) is looked up by its board id; a private board
is only found with the user's cookies. Profiles and avatars come from the web app's
``UserResource``: yt-dlp has no profile extractor, and gallery-dl's queues every board.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from content_dl.providers.base import ProviderError
from content_dl.providers.common import ext_from_url
from content_dl.schema import Item, Post, Profile, Result

PROVIDER = "native"
ROOT = "https://www.pinterest.com"
SHORTENER = "https://api.pinterest.com/url_shortener/{code}/redirect/"
RESOURCE = ROOT + "/resource/{name}/get/?{query}"

# Query parameters a shared link carries that only describe the share.
TRACKING_PARAMS = frozenset(
    {"invite_code", "sender", "sfo", "mt", "board_collab_inviter", "inviter_user_id", "inviter_first_name"}
)

# First path segments that are Pinterest pages, not usernames.
RESERVED_PATHS = frozenset(
    {
        "pin", "search", "ideas", "today", "invited", "settings", "business", "categories", "explore",
        "login", "signup", "password", "notifications", "messages", "resource", "_",
    }
)  # fmt: skip

USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,40}")

# A sized copy of an image, such as /280x280_RS/ or /736x/; /originals/ is the uploaded file.
SIZED_PATH = re.compile(r"/\d+x\d*(?:_RS)?/")

# The web app answers a resource request only when it names the page handler it came from.
HANDLERS = {"UserResource": "www/[username].js", "BoardResource": "www/[username]/[slug].js"}


def is_short_url(url: str) -> bool:
    """
    >>> is_short_url("https://pin.it/3cMcqPMgg")
    True
    >>> is_short_url("https://www.pinterest.com/pin/1/")
    False
    """
    return (urlsplit(url).hostname or "").lower() == "pin.it"


def clean_url(url: str) -> str:
    """Drop the share parameters from a Pinterest URL.

    >>> clean_url("https://www.pinterest.com/beulsetter/?invite_code=5b3&sender=843")
    'https://www.pinterest.com/beulsetter/'
    >>> clean_url("https://www.pinterest.com/search/pins/?q=cats&invite_code=1")
    'https://www.pinterest.com/search/pins/?q=cats'
    """
    parts = urlsplit(url)
    query = [(key, value) for key, values in parse_qs(parts.query).items() for value in values if key not in TRACKING_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def parse_url(url: str) -> tuple[str, str | None]:
    """Split a Pinterest URL into ``(kind, value)``.

    Kinds: ``invite`` (value: the board id), ``profile`` (the username), ``pin`` (the pin id),
    and ``other`` for boards, searches and the rest, which yt-dlp and gallery-dl read.

    >>> parse_url("https://www.pinterest.com/beulsetter/")
    ('profile', 'beulsetter')
    >>> parse_url("https://www.pinterest.com/beulsetter/_saved/")
    ('profile', 'beulsetter')
    >>> parse_url("https://www.pinterest.com/pin/894879388484489207/")
    ('pin', '894879388484489207')
    >>> parse_url("https://www.pinterest.com/invited/?invite_code=f3&board_id=8434")
    ('invite', '8434')
    >>> parse_url("https://www.pinterest.com/hammadxp/memes/")
    ('other', None)
    """
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return "other", None

    first = segments[0]
    if first.lower() == "invited":
        board_id = (parse_qs(parts.query).get("board_id") or [None])[0]
        return "invite", board_id

    if first.lower() == "pin" and len(segments) > 1:
        return "pin", segments[1].rsplit("--", 1)[-1]

    if first.lower() in RESERVED_PATHS or not USERNAME.fullmatch(first):
        return "other", None

    if len(segments) == 1 or segments[1:] == ["_saved"]:
        return "profile", first

    return "other", None


def resource_url(name: str, options: Mapping[str, Any], source_url: str = "/") -> str:
    data = json.dumps({"options": dict(options), "context": {}}, separators=(",", ":"))
    return RESOURCE.format(name=name, query=f"source_url={quote(source_url)}&data={quote(data)}")


def request_headers(name: str, cookies: Mapping[str, str]) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/javascript, */*, q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "X-Pinterest-PWS-Handler": HANDLERS[name],
    }
    # A logged-in session's requests are checked against its CSRF cookie.
    if cookies.get("csrftoken"):
        headers["X-CSRFToken"] = cookies["csrftoken"]

    return headers


def resource_data(text: str) -> dict[str, Any] | None:
    """The ``data`` of a resource response, or None when Pinterest found nothing."""
    try:
        body = json.loads(text)
    except ValueError as error:
        raise ProviderError("native: Pinterest sent a page instead of data") from error

    data = (body.get("resource_response") or {}).get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else None


def board_url_from_response(text: str) -> str | None:
    """The address of the board a ``BoardResource`` response describes.

    >>> board_url_from_response('{"resource_response": {"data": {"url": "/hammadxp/memes/"}}}')
    'https://www.pinterest.com/hammadxp/memes/'
    >>> board_url_from_response('{"resource_response": {"error": {"message": "Board not found."}, "data": null}}')
    """
    data = resource_data(text)
    path = data.get("url") if data else None
    return ROOT + path if isinstance(path, str) and path.startswith("/") else None


def avatar_candidates(user: Mapping[str, Any]) -> list[str]:
    """Avatar URLs, best first: the original upload, then the largest sized copy.

    >>> avatar_candidates({"image_xlarge_url": "https://i.pinimg.com/280x280_RS/45/cc/a.jpg"})
    ['https://i.pinimg.com/originals/45/cc/a.jpg', 'https://i.pinimg.com/280x280_RS/45/cc/a.jpg']
    """
    for key in ("image_xlarge_url", "image_large_url", "image_medium_url", "image_small_url"):
        url = user.get(key)
        if isinstance(url, str) and url:
            original = SIZED_PATH.sub("/originals/", url, count=1)
            return [original, url] if original != url else [url]

    return []


def profile_from_user(user: Mapping[str, Any], platform: str, avatar_url: str | None) -> Profile:
    username = user.get("username") or None
    return Profile(
        platform=platform,
        id=str(user["id"]) if user.get("id") is not None else None,
        username=username,
        name=user.get("full_name") or None,
        url=f"{ROOT}/{username}/" if username else None,
        avatar=Item(media_type="avatar", url=avatar_url, ext=ext_from_url(avatar_url), provider=PROVIDER)
        if avatar_url
        else None,
    )


def uploader_from_known(known: Mapping[str, Result] | None) -> dict[str, str] | None:
    """The ``UserResource`` options for a pin's uploader, from what the other providers found."""
    for result in (known or {}).values():
        uploader = result.uploader if isinstance(result, Post) else None
        if uploader and uploader.username:
            return {"username": uploader.username}

        if uploader and uploader.id:
            return {"user_id": uploader.id}

    return None


def short_code(url: str) -> str:
    """
    >>> short_code("https://pin.it/3cMcqPMgg")
    '3cMcqPMgg'
    """
    return urlsplit(url).path.strip("/")


def shortener_url(url: str) -> str:
    return SHORTENER.format(code=quote(short_code(url)))
