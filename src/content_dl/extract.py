"""The one call every interface (CLI, HTTP server, Seal fork) is a thin shell over."""

from __future__ import annotations

from collections.abc import Iterable

from content_dl.merge import merge
from content_dl.providers import Cookies, ProviderError, get_provider, provider_covers
from content_dl.router import KIND_COLUMNS, columns_for, detect_platform, routes_for
from content_dl.schema import ITEM_KINDS, ItemKind, Post, Profile, Result


class ExtractionError(RuntimeError):
    """No provider could extract the URL."""


def normalise_want(want: Iterable[str] | None) -> list[ItemKind] | None:
    """Accept item kinds in singular or plural form, as ``--want images,avatar`` does.

    >>> normalise_want(["images", "avatar", "audio"])
    ['image', 'avatar', 'audio']
    >>> normalise_want(None) is None
    True
    """
    if want is None:
        return None

    kinds: list[ItemKind] = []
    for raw in want:
        name = raw.strip().lower()
        if not name:
            continue

        if name not in ITEM_KINDS and name.endswith("s") and name[:-1] in ITEM_KINDS:
            name = name[:-1]

        if name not in ITEM_KINDS:
            raise ValueError(f"unknown item kind {raw!r}, expected some of: {', '.join(sorted(ITEM_KINDS))}")

        if name not in kinds:
            kinds.append(name)  # type: ignore[arg-type]

    return kinds


def _keep_kinds(result: Result, want: list[ItemKind] | None) -> Result:
    if want is None:
        return result

    result.items = [item for item in result.items if item.kind in want]
    if isinstance(result, Profile) and "avatar" not in want:
        result.avatar = None

    if isinstance(result, Post) and result.uploader and "avatar" not in want:
        result.uploader.avatar = None

    return result


class _Fetcher:
    """Runs each provider at most once per extraction and remembers what it returned."""

    def __init__(self, url: str, platform: str, kinds: list[ItemKind] | None, cookies: Cookies | None) -> None:
        self.url = url
        self.cookies = cookies
        self.platform = platform
        self.kinds = kinds
        self.results: dict[str, Result] = {}
        self.errors: dict[str, str] = {}

    def get(self, name: str) -> Result | None:
        if name not in self.results and name not in self.errors:
            try:
                # known is a copy, so a provider can't change what earlier providers returned.
                provider = get_provider(name, self.cookies)
                self.results[name] = provider.extract(self.url, self.platform, self.kinds, known=dict(self.results))
            except ProviderError as error:
                self.errors[name] = str(error)

        return self.results.get(name)


def _fills_column(result: Result, column: str, kinds: list[ItemKind] | None) -> bool:
    """Does a result have what a routing column asks for? Metadata only needs a result."""
    if column == "metadata":
        return True

    if column == "avatar":
        return _avatar_of(result) is not None

    wanted = {kind for kind, kind_column in KIND_COLUMNS.items() if kind_column == column}
    if kinds is not None:
        wanted &= set(kinds)

    return any(item.kind in wanted for item in result.items)


def _avatar_of(result: Result) -> object:
    if isinstance(result, Profile):
        return result.avatar

    return result.uploader.avatar if result.uploader else None


def extract(
    url: str,
    want: Iterable[str] | None = None,
    provider: str | None = None,
    cookies: Cookies | None = None,
) -> Result:
    """Extract everything at ``url`` and return it as a Post or Profile.

    ``want`` limits the result to some item kinds (``["video", "image"]``); metadata is always
    filled. ``provider`` forces one provider and skips the routing table and the merger, for
    debugging. ``cookies`` are passed to every provider, and switch the platform to its
    cookie routes (on Instagram, gallery-dl's logged-in API leads images and metadata).

    Each routing column (metadata, then one per wanted kind) tries its providers left to right
    until one returns what the column asks for. A provider runs at most once, and everything
    the providers returned is merged, so a provider called for one column can still fill
    fields or items another column's provider left out.
    """
    kinds = normalise_want(want)
    platform = detect_platform(url)

    if provider:
        try:
            return _keep_kinds(get_provider(provider, cookies).extract(url, platform, kinds), kinds)
        except ProviderError as error:
            raise ExtractionError(str(error)) from error

    routes = routes_for(platform, with_cookies=bool(cookies))
    columns = columns_for(kinds)
    if not any(provider_covers(name, platform) for column in columns for name in routes.get(column, [])):
        raise ExtractionError(f"no provider can handle {platform} yet")

    fetcher = _Fetcher(url, platform, kinds, cookies)
    for column in columns:
        for name in routes.get(column, []):
            if not provider_covers(name, platform):
                continue

            result = fetcher.get(name)
            if result is not None and _fills_column(result, column, kinds):
                break

    if not fetcher.results:
        raise ExtractionError("; ".join(fetcher.errors.values()))

    return _keep_kinds(merge(fetcher.results, routes, kinds), kinds)
