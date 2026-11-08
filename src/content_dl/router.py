"""Maps a URL to a platform, and a platform to the providers to try for each media type.

The routing table lives in JSON so it can change without a code release (on Android it
updates the same way yt-dlp does). The defaults ship as ``routes.json`` next to this module,
following the table in SPEC.md. A user file can override them, found in this order:

1. the ``routes`` argument of ``extract()``, or the CLI's ``--routes FILE``
2. the ``CONTENT_DL_ROUTES`` environment variable (a file path)
3. ``content-dl/routes.json`` in the user config folder (``%APPDATA%`` on Windows,
   ``$XDG_CONFIG_HOME`` or ``~/.config`` elsewhere)

An override only lists what it changes. ``domains`` entries are added or replaced, a cell in
``routes`` or ``routes_with_cookies`` replaces that one cell, and ``disable`` takes providers
out of every cell of a platform (``"*"`` for all platforms):

    {"disable": {"tiktok": ["yt-dlp"]}, "routes": {"x": {"images": ["cobalt"]}}}

Why the defaults are ordered as they are:

- Instagram: yt-dlp leads because it has a logged-out route (the post page's embedded JSON, or
  a logged-out GraphQL query when curl_cffi is installed). gallery-dl only uses the app API,
  which redirects to the login page without a session, so it is the fallback.
- Instagram with cookies: gallery-dl's app API works and returns more than yt-dlp's logged-out
  route (image sizes, full owner data), so it leads images and metadata again. yt-dlp stays
  first for video, where its format list is what matters.
- Reddit: native leads because the public embed page is the only logged-out source left; Reddit
  blocks yt-dlp's and gallery-dl's requests without a session (Nov 8, 2026). They're listed only
  in the cookie row, where they can stand in if the embed page changes.
- cobalt returns almost no metadata and runs on a datacenter IP, so it is always a fallback.

- Snapchat: native leads because its pages dropped the ``__NEXT_DATA__`` blob that yt-dlp's and
  gallery-dl's Spotlight extractors read (Nov 8, 2026). gallery-dl still reads highlights,
  stories and profiles, so it follows. yt-dlp has only a Spotlight extractor, so it isn't listed.
  gallery-dl is left out of images and avatar: as the metadata fallback it has already run on
  every URL native can't read, and its photos still merge in from outside the column. Listed
  there, it would only be asked about a Spotlight (always a video) or an anonymous poster's
  avatar, and fail.

Providers that aren't built (generic) and cobalt without a configured instance are listed anyway and skipped at
extraction time.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from content_dl.schema import MediaType

# INFO: Routing key for sites without their own row. It never appears in results: an unknown
# site's platform is its domain (``example.com``), so templates still make sensible folders.
UNKNOWN_SITE = "unknown"

# "video" covers video and audio.
COLUMNS = ("video", "images", "thumbnail", "avatar", "metadata")

# Every provider name a config may use, built or not. A typo would otherwise be skipped silently.
KNOWN_PROVIDERS = frozenset({"yt-dlp", "gallery-dl", "cobalt", "native", "generic"})

ROUTES_ENV = "CONTENT_DL_ROUTES"
ALL_PLATFORMS = "*"

# Media type -> routing column, in the table's column order (used when every media type is requested).
MEDIA_TYPE_COLUMNS: dict[str, str] = {
    "video": "video",
    "audio": "video",
    "image": "images",
    "thumbnail": "thumbnail",
    "avatar": "avatar",
}

Row = dict[str, list[str]]


class RouterError(ValueError):
    """Raised for a URL the router can't work with, or a routing config that isn't valid."""


@dataclass(frozen=True)
class RoutingConfig:
    """The routing table: domains, provider order per platform and column, and overrides.

    ``routes`` maps platform -> column -> providers, tried left to right. ``routes_with_cookies``
    holds the cells that change when the user supplies cookies. ``disable`` maps a platform
    (or ``"*"``) to providers removed from all of its cells.
    """

    domains: dict[str, str]
    routes: dict[str, Row]
    routes_with_cookies: dict[str, Row] = field(default_factory=dict)
    disable: dict[str, list[str]] = field(default_factory=dict)

    def routes_for(self, platform: str, with_cookies: bool = False) -> Row:
        row = self.routes.get(platform, self.routes.get(UNKNOWN_SITE, {}))
        if with_cookies:
            row = {**row, **self.routes_with_cookies.get(platform, {})}

        off = {*self.disable.get(ALL_PLATFORMS, []), *self.disable.get(platform, [])}
        return {column: [name for name in row.get(column, []) if name not in off] for column in COLUMNS}

    def overridden(self, data: Mapping[str, Any], source: str = "routing config") -> RoutingConfig:
        """Return this config with ``data`` (an override in the JSON format) applied on top."""
        _check(data, source)
        routes = {platform: dict(row) for platform, row in self.routes.items()}
        for platform, row in data.get("routes", {}).items():
            routes.setdefault(platform, {}).update(row)

        with_cookies = {platform: dict(row) for platform, row in self.routes_with_cookies.items()}
        for platform, row in data.get("routes_with_cookies", {}).items():
            with_cookies.setdefault(platform, {}).update(row)

        disable = {platform: list(names) for platform, names in self.disable.items()}
        for platform, names in data.get("disable", {}).items():
            disable[platform] = list(dict.fromkeys([*disable.get(platform, []), *names]))

        domains = {**self.domains, **{domain.lower(): name for domain, name in data.get("domains", {}).items()}}
        return RoutingConfig(domains=domains, routes=routes, routes_with_cookies=with_cookies, disable=disable)


def _check(data: Any, source: str) -> None:
    """Reject a config that would route wrongly instead of failing quietly at extraction time."""
    if not isinstance(data, Mapping):
        raise RouterError(f"{source}: expected a JSON object")

    unknown_keys = set(data) - {"version", "domains", "routes", "routes_with_cookies", "disable"}
    if unknown_keys:
        raise RouterError(f"{source}: unknown key(s) {', '.join(sorted(unknown_keys))}")

    domains = data.get("domains", {})
    if not isinstance(domains, Mapping) or not all(isinstance(v, str) for v in domains.values()):
        raise RouterError(f"{source}: 'domains' must map a domain to a platform name")

    for table in ("routes", "routes_with_cookies"):
        rows = data.get(table, {})
        if not isinstance(rows, Mapping):
            raise RouterError(f"{source}: {table!r} must be an object of platforms")

        for platform, row in rows.items():
            if not isinstance(row, Mapping):
                raise RouterError(f"{source}: {table}.{platform} must be an object of columns")

            for column, names in row.items():
                where = f"{source}: {table}.{platform}.{column}"
                if column not in COLUMNS:
                    raise RouterError(f"{where}: unknown column, expected one of {', '.join(COLUMNS)}")

                _check_names(names, where)

    disable = data.get("disable", {})
    if not isinstance(disable, Mapping):
        raise RouterError(f"{source}: 'disable' must map a platform to a list of providers")

    for platform, names in disable.items():
        _check_names(names, f"{source}: disable.{platform}")


def _check_names(names: Any, where: str) -> None:
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise RouterError(f"{where}: expected a list of provider names")

    unknown = [name for name in names if name not in KNOWN_PROVIDERS]
    if unknown:
        raise RouterError(f"{where}: unknown provider(s) {', '.join(unknown)}, known: {', '.join(sorted(KNOWN_PROVIDERS))}")


@lru_cache(maxsize=1)
def default_config() -> RoutingConfig:
    """The routing table shipped with content-dl (``routes.json``)."""
    text = resources.files("content_dl").joinpath("routes.json").read_text(encoding="utf-8")
    empty = RoutingConfig(domains={}, routes={})
    return empty.overridden(json.loads(text), "routes.json")


def user_config_path() -> Path | None:
    """The user's override file, if there is one: ``CONTENT_DL_ROUTES``, then the config folder.

    A path set in ``CONTENT_DL_ROUTES`` must exist, so a typo isn't mistaken for "no override".
    """
    configured = os.environ.get(ROUTES_ENV)
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise RouterError(f"{ROUTES_ENV} points to a missing file: {configured}")

        return path

    path = config_folder_file()
    return path if path.is_file() else None


def config_folder_file() -> Path:
    """``content-dl/routes.json`` in the user config folder, whether or not it exists."""
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    return Path(base or Path.home() / ".config") / "content-dl" / "routes.json"


def load_config(routes: str | os.PathLike[str] | Mapping[str, Any] | RoutingConfig | None = None) -> RoutingConfig:
    """The routing table to use: the defaults with an override applied on top.

    ``routes`` is an override file path, an override as a dict, or a full ``RoutingConfig``.
    Without one, the user's override file is used if there is one. Read on every call (it's a
    small file), so an updated config applies to the next extraction without a restart.

    >>> load_config({"disable": {"tiktok": ["yt-dlp"]}}).routes_for("tiktok")["video"]
    ['cobalt']
    """
    if isinstance(routes, RoutingConfig):
        return routes

    if isinstance(routes, Mapping):
        return default_config().overridden(routes)

    path = Path(routes).expanduser() if routes is not None else user_config_path()
    if path is None:
        return default_config()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RouterError(f"couldn't read the routing config {path}: {error.strerror or error}") from error
    except json.JSONDecodeError as error:
        raise RouterError(f"{path}: not valid JSON ({error})") from error

    return default_config().overridden(data, str(path))


def detect_platform(url: str, config: RoutingConfig | None = None) -> str:
    """Return the platform a URL belongs to, or the site's domain for an unknown site.

    Matched against the hostname and every parent domain, so "m.youtube.com" and
    "old.reddit.com" resolve without listing each subdomain.

    >>> detect_platform("https://youtu.be/jNQXAC9IVRw")
    'youtube'
    >>> detect_platform("https://old.reddit.com/r/pics/comments/abc/")
    'reddit'
    >>> detect_platform("https://www.Example.com/video.mp4")
    'example.com'
    """
    domains = (config or default_config()).domains
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RouterError(f"not an http(s) URL: {url!r}")

    labels = parsed.hostname.lower().rstrip(".").split(".")
    for start in range(len(labels) - 1):
        platform = domains.get(".".join(labels[start:]))
        if platform:
            return platform

    return ".".join(labels[1:] if labels[0] == "www" and len(labels) > 2 else labels)


def routes_for(platform: str, with_cookies: bool = False, config: RoutingConfig | None = None) -> Row:
    """Return a platform's row of the routing table. Unknown sites share one row.

    ``with_cookies`` applies the platform's cookie overrides, for when the user gave cookies.

    >>> routes_for("instagram")["images"], routes_for("instagram", with_cookies=True)["images"]
    (['yt-dlp', 'gallery-dl', 'cobalt'], ['gallery-dl', 'yt-dlp', 'cobalt'])
    """
    return (config or default_config()).routes_for(platform, with_cookies)


def columns_for(media_types: list[MediaType] | None = None) -> list[str]:
    """Return the routing columns to fill, metadata first, without duplicates.

    >>> columns_for(["audio", "video", "avatar"])
    ['metadata', 'video', 'avatar']
    """
    chosen = media_types if media_types is not None else MEDIA_TYPE_COLUMNS
    return list(dict.fromkeys(["metadata", *(MEDIA_TYPE_COLUMNS[media_type] for media_type in chosen)]))


def providers_for(
    platform: str,
    media_types: list[MediaType] | None = None,
    with_cookies: bool = False,
    config: RoutingConfig | None = None,
) -> list[str]:
    """Return the providers to try for a platform, in priority order, without duplicates.

    Metadata providers come first because they decide the Post's fields; the providers for
    each requested media type follow. ``media_types=None`` means every media type. A platform without its own row
    (an unknown site's domain) uses the unknown-site row.

    >>> providers_for("x", ["video"])
    ['yt-dlp', 'gallery-dl', 'cobalt']
    """
    routes = routes_for(platform, with_cookies, config)
    ordered: list[str] = []
    for column in columns_for(media_types):
        for provider in routes.get(column, []):
            if provider not in ordered:
                ordered.append(provider)

    return ordered
