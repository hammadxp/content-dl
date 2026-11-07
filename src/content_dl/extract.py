"""The one call every interface (CLI, HTTP server, Seal fork) is a thin shell over."""

from __future__ import annotations

from collections.abc import Iterable

from content_dl.providers import PROVIDERS, ProviderError, get_provider
from content_dl.router import detect_platform, providers_for
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


def extract(url: str, want: Iterable[str] | None = None, provider: str | None = None) -> Result:
    """Extract everything at ``url`` and return it as a Post or Profile.

    ``want`` limits the result to some item kinds (``["video", "image"]``); metadata is always
    filled. ``provider`` forces one provider and skips the routing table, for debugging.

    INFO: The merger arrives in milestone 3. Until then the first provider in the route that
    returns a result wins, and a failing provider passes the request to the next one.
    """
    kinds = normalise_want(want)
    platform = detect_platform(url)

    names = [provider] if provider else [name for name in providers_for(platform, kinds) if name in PROVIDERS]
    if not names:
        raise ExtractionError(f"no provider can handle {platform} yet")

    errors: list[str] = []
    for name in names:
        try:
            result = get_provider(name).extract(url, platform, kinds)
        except ProviderError as error:
            errors.append(str(error))
            continue

        return _keep_kinds(result, kinds)

    raise ExtractionError("; ".join(errors))
