"""Instagram profile pictures, from the ``web_profile_info`` endpoint.

yt-dlp and gallery-dl don't return an Instagram avatar on a logged-out route, so this fills the
avatar column. A profile URL returns a Profile. A post URL doesn't contain the uploader's
username, so it is taken from what the metadata providers already returned (``known``), and
the result is a Post holding only that uploader, for the merger to combine.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

from content_dl.providers.base import ProviderError
from content_dl.providers.common import ext_from_url
from content_dl.schema import Item, Post, Profile, Result

PROVIDER = "native"
ROOT = "https://www.instagram.com"
PROFILE_API = ROOT + "/api/v1/users/web_profile_info/?username={username}"

# INFO: The web app's public app id. The endpoint rejects requests without it.
APP_ID = "936619743392459"

USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")

# First path segments that are Instagram pages, not usernames.
RESERVED_PATHS = frozenset(
    {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about", "developer", "legal", "api", "web"}
)


def parse_url(url: str) -> tuple[str, str | None]:
    """Split an Instagram URL into ``(kind, username)``; kind is ``post`` or ``profile``.

    A post URL only has a username in the ``/USER/p/CODE/`` form.

    >>> parse_url("https://www.instagram.com/someone/")
    ('profile', 'someone')
    >>> parse_url("https://www.instagram.com/p/C9xYz123/")
    ('post', None)
    >>> parse_url("https://www.instagram.com/someone/reel/C9xYz123/")
    ('post', 'someone')
    """
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if not segments:
        raise ProviderError("native: not an Instagram post or profile URL")

    first = segments[0]
    if first.lower() in RESERVED_PATHS:
        return "post", None

    if not USERNAME.fullmatch(first):
        raise ProviderError("native: not an Instagram post or profile URL")

    if len(segments) > 1 and segments[1] in ("p", "reel", "tv"):
        return "post", first

    return "profile", first


def username_from_known(known: Mapping[str, Result] | None) -> str | None:
    """The uploader's username, from the first provider result that has one."""
    for result in (known or {}).values():
        profile = result if isinstance(result, Profile) else result.uploader
        if profile and profile.username:
            return profile.username

    return None


def _size(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def avatar_from_user(user: Mapping[str, Any]) -> Item | None:
    """Prefer ``hd_profile_pic_url_info``, which carries a size; else the HD URL, else the small one.

    The plain URLs come without a size, so width and height stay unset for them.
    """
    info = user.get("hd_profile_pic_url_info")
    if isinstance(info, dict) and info.get("url"):
        return Item(
            kind="avatar",
            url=info["url"],
            ext=ext_from_url(info["url"]),
            width=_size(info.get("width")),
            height=_size(info.get("height")),
            provider=PROVIDER,
        )

    url = user.get("profile_pic_url_hd") or user.get("profile_pic_url")
    if not isinstance(url, str) or not url:
        return None

    return Item(kind="avatar", url=url, ext=ext_from_url(url), provider=PROVIDER)


def profile_from_response(text: str, platform: str = "instagram") -> Profile:
    """Translate a ``web_profile_info`` response body into a Profile."""
    try:
        data = json.loads(text)
    except ValueError as error:
        # INFO: Without a session Instagram often answers with the login page instead of JSON.
        raise ProviderError("native: Instagram sent a page instead of profile data; try --cookies") from error

    user = (data.get("data") or {}).get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        raise ProviderError("native: Instagram has no such profile, or it is hidden logged out")

    username = user.get("username") or None
    return Profile(
        platform=platform,
        id=str(user["id"]) if user.get("id") is not None else None,
        username=username,
        name=user.get("full_name") or None,
        url=f"{ROOT}/{username}/" if username else None,
        avatar=avatar_from_user(user),
    )


def request_headers(username: str, cookies: Mapping[str, str]) -> dict[str, str]:
    headers = {
        "Accept": "*/*",
        "X-IG-App-ID": APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{ROOT}/{username}/",
    }
    # A logged-in session's requests are checked against its CSRF cookie.
    if cookies.get("csrftoken"):
        headers["X-CSRFToken"] = cookies["csrftoken"]

    return headers


def profile_url(username: str) -> str:
    return PROFILE_API.format(username=quote(username))


def result_for(url: str, platform: str, profile: Profile) -> Result:
    """A profile URL gets the Profile; a post URL gets a Post whose only content is its uploader."""
    kind, _ = parse_url(url)
    if kind == "profile":
        return profile

    return Post(platform=platform, uploader=profile)
