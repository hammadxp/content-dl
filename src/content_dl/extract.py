"""The one call every interface (CLI, HTTP server, Seal fork) is a thin shell over."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from content_dl.merge import merge
from content_dl.providers import CobaltInstance, Cookies, ProviderError, get_provider, provider_covers
from content_dl.providers.base import NeedsKnownError
from content_dl.providers.native import NativeProvider, facebook
from content_dl.router import MEDIA_TYPE_COLUMNS, RoutingConfig, columns_for, detect_platform, load_config
from content_dl.schema import MEDIA_TYPES, MediaType, Post, Profile, Result


class ExtractionError(RuntimeError):
    """No provider could extract the URL."""


def normalise_want(want: Iterable[str] | None) -> list[MediaType] | None:
    """Accept media types in singular or plural form, as ``--media-types images,avatar`` does.

    >>> normalise_want(["images", "avatar", "audio"])
    ['image', 'avatar', 'audio']
    >>> normalise_want(None) is None
    True
    """
    if want is None:
        return None

    media_types: list[MediaType] = []
    for raw in want:
        name = raw.strip().lower()
        if not name:
            continue

        if name not in MEDIA_TYPES and name.endswith("s") and name[:-1] in MEDIA_TYPES:
            name = name[:-1]

        if name not in MEDIA_TYPES:
            raise ValueError(f"unknown media type {raw!r}, expected some of: {', '.join(sorted(MEDIA_TYPES))}")

        if name not in media_types:
            media_types.append(name)  # type: ignore[arg-type]

    return media_types


def _keep_media_types(result: Result, want: list[MediaType] | None) -> Result:
    if want is None:
        return result

    result.items = [item for item in result.items if item.media_type in want]
    if isinstance(result, Profile) and "avatar" not in want:
        result.avatar = None

    if isinstance(result, Post) and result.uploader and "avatar" not in want:
        result.uploader.avatar = None

    return result


class _Fetcher:
    """Runs each provider at most once per extraction and remembers what it returned."""

    def __init__(
        self,
        url: str,
        platform: str,
        media_types: list[MediaType] | None,
        cookies: Cookies | None,
        cobalt: CobaltInstance | None,
    ) -> None:
        self.url = url
        self.cookies = cookies
        self.cobalt = cobalt
        self.platform = platform
        self.media_types = media_types
        self.results: dict[str, Result] = {}
        self.errors: dict[str, str] = {}
        # Providers that asked to be tried again once others ran, with how many results they saw.
        self.waiting: dict[str, int] = {}

    def get(self, name: str) -> Result | None:
        retry = name in self.waiting and self.waiting[name] < len(self.results)
        if name not in self.results and (name not in self.errors or retry):
            try:
                # known is a copy, so a provider can't change what earlier providers returned.
                provider = get_provider(name, self.cookies, self.cobalt)
                self.results[name] = provider.extract(
                    self.url, self.platform, self.media_types, known=dict(self.results)
                )
                self.errors.pop(name, None)
                self.waiting.pop(name, None)
            except NeedsKnownError as error:
                self.errors[name] = str(error)
                self.waiting[name] = len(self.results)
            except ProviderError as error:
                self.errors[name] = str(error)
                self.waiting.pop(name, None)

        return self.results.get(name)


def _fills_column(result: Result, column: str, media_types: list[MediaType] | None) -> bool:
    """Does a result have what a routing column asks for? Metadata only needs a result."""
    if column == "metadata":
        return True

    if column == "avatar":
        return _avatar_of(result) is not None

    wanted = {media_type for media_type, media_type_column in MEDIA_TYPE_COLUMNS.items() if media_type_column == column}
    if media_types is not None:
        wanted &= set(media_types)

    return any(item.media_type in wanted for item in result.items)


def _avatar_of(result: Result) -> object:
    if isinstance(result, Profile):
        return result.avatar

    return result.uploader.avatar if result.uploader else None


def extract(
    url: str,
    want: Iterable[str] | None = None,
    provider: str | None = None,
    cookies: Cookies | None = None,
    routes: str | os.PathLike[str] | Mapping[str, Any] | RoutingConfig | None = None,
    cobalt: CobaltInstance | str | None = None,
) -> Result:
    """Extract everything at ``url`` and return it as a Post or Profile.

    ``want`` limits the result to some media types (``["video", "image"]``); metadata is always
    filled. ``provider`` forces one provider and skips the routing table and the merger, for
    debugging. ``cookies`` are passed to every provider, and switch the platform to its
    cookie routes (on Instagram, gallery-dl's logged-in API leads images and metadata).
    ``routes`` overrides the routing table (a JSON file path or a dict in the same format, see
    ``content_dl.router``); without it the user's override file applies, if there is one.
    ``cobalt`` is the cobalt instance to call (a URL or ``CobaltInstance``), defaulting to
    ``CONTENT_DL_COBALT_URL``; without one, cobalt is skipped.

    Each routing column (metadata, then one per wanted media_type) tries its providers left to right
    until one returns what the column asks for. A provider runs at most once, and everything
    the providers returned is merged, so a provider called for one column can still fill
    fields or items another column's provider left out.
    """
    media_types = normalise_want(want)
    config = load_config(routes)
    platform = detect_platform(url, config)
    if platform == "facebook":
        url = facebook.resolve_share_url(url, cookies)

    if platform == "reddit":
        url = NativeProvider(cookies).resolve_reddit_url(url)

    if platform == "snapchat":
        url = NativeProvider(cookies).resolve_snapchat_url(url)

    if platform == "pinterest":
        try:
            url = NativeProvider(cookies).resolve_pinterest_url(url)
        except ProviderError as error:
            raise ExtractionError(str(error)) from error

    instance = CobaltInstance(cobalt) if isinstance(cobalt, str) else cobalt or CobaltInstance.from_env()

    if provider:
        try:
            forced = get_provider(provider, cookies, instance).extract(url, platform, media_types)
            return _keep_media_types(forced, media_types)
        except ProviderError as error:
            raise ExtractionError(str(error)) from error

    row = config.routes_for(platform, with_cookies=bool(cookies))
    columns = columns_for(media_types)
    if not any(provider_covers(name, platform, instance) for column in columns for name in row.get(column, [])):
        raise ExtractionError(f"no provider can handle {platform} with the current routing config")

    fetcher = _Fetcher(url, platform, media_types, cookies, instance)
    for column in columns:
        # INFO: The metadata column runs first. Once it says the URL is a profile, only the avatar
        # is left to find: asking video or image providers about a profile costs requests and errors.
        if column not in ("metadata", "avatar") and any(isinstance(r, Profile) for r in fetcher.results.values()):
            continue

        for name in row.get(column, []):
            if not provider_covers(name, platform, instance):
                continue

            result = fetcher.get(name)
            if result is not None and _fills_column(result, column, media_types):
                break

    if not fetcher.results:
        raise ExtractionError("; ".join(fetcher.errors.values()))

    return _keep_media_types(merge(fetcher.results, row, media_types), media_types)
