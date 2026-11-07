"""Provider registry. Only providers listed here can be routed to."""

from __future__ import annotations

from collections.abc import Callable

from content_dl.providers.base import Cookies, Provider, ProviderError
from content_dl.providers.gallerydl import GalleryDlProvider
from content_dl.providers.native import NativeProvider
from content_dl.providers.ytdlp import YtDlpProvider

# Each factory takes the user's cookies (or None).
PROVIDERS: dict[str, Callable[..., Provider]] = {
    YtDlpProvider.name: YtDlpProvider,
    GalleryDlProvider.name: GalleryDlProvider,
    NativeProvider.name: NativeProvider,
}


def provider_covers(name: str, platform: str) -> bool:
    """Is ``name`` built, and does it have an extractor for ``platform``?

    >>> provider_covers("native", "threads"), provider_covers("native", "snapchat"), provider_covers("cobalt", "x")
    (True, False, False)
    """
    factory = PROVIDERS.get(name)
    if factory is None:
        return False

    platforms = getattr(factory, "platforms", None)
    return platforms is None or platform in platforms


def get_provider(name: str, cookies: Cookies | None = None) -> Provider:
    factory = PROVIDERS.get(name)
    if factory is None:
        available = ", ".join(sorted(PROVIDERS))
        raise ProviderError(f"unknown provider {name!r}, available: {available}")

    return factory(cookies=cookies)


__all__ = [
    "PROVIDERS",
    "Cookies",
    "GalleryDlProvider",
    "NativeProvider",
    "Provider",
    "ProviderError",
    "YtDlpProvider",
    "get_provider",
    "provider_covers",
]
