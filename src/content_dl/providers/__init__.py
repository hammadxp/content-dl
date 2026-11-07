"""Provider registry. Only providers listed here can be routed to."""

from __future__ import annotations

from collections.abc import Callable

from content_dl.providers.base import Cookies, Provider, ProviderError
from content_dl.providers.gallerydl import GalleryDlProvider
from content_dl.providers.ytdlp import YtDlpProvider

# Each factory takes the user's cookies (or None).
PROVIDERS: dict[str, Callable[..., Provider]] = {
    YtDlpProvider.name: YtDlpProvider,
    GalleryDlProvider.name: GalleryDlProvider,
}


def get_provider(name: str, cookies: Cookies | None = None) -> Provider:
    factory = PROVIDERS.get(name)
    if factory is None:
        available = ", ".join(sorted(PROVIDERS))
        raise ProviderError(f"unknown provider {name!r}, available: {available}")

    return factory(cookies=cookies)


__all__ = ["PROVIDERS", "Cookies", "GalleryDlProvider", "Provider", "ProviderError", "YtDlpProvider", "get_provider"]
