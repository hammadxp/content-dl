"""What every provider looks like to the rest of content-dl."""

from __future__ import annotations

from typing import Protocol

from content_dl.schema import Result


class ProviderError(RuntimeError):
    """A provider couldn't extract this URL. The next provider in the route gets a turn."""


class Provider(Protocol):
    name: str

    def extract(self, url: str, platform: str, want: list[str] | None = None) -> Result:
        """Return everything the provider can find at ``url``, in content-dl's format.

        ``platform`` comes from the router and is copied into the result as-is. ``want`` lists
        the item kinds asked for (``None`` means all), so a provider can skip extra requests.
        Raise ``ProviderError`` when the URL can't be handled.
        """
        ...
