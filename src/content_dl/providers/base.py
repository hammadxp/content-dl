"""What every provider looks like to the rest of content-dl."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from content_dl.schema import Result

# The browsers yt-dlp and gallery-dl can both read cookies from (yt-dlp's SUPPORTED_BROWSERS).
COOKIE_BROWSERS = frozenset({"brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"})


class ProviderError(RuntimeError):
    """A provider couldn't extract this URL. The next provider in the route gets a turn."""


@dataclass(frozen=True)
class Cookies:
    """Cookies the user supplied, passed as-is to every provider that can use them.

    ``file`` is a Netscape cookies.txt file. ``browser`` is ``BROWSER[:PROFILE]``, read from
    the browser's own cookie store, as with yt-dlp's ``--cookies-from-browser``.

    >>> Cookies(browser="Firefox:work").browser_spec()
    ('firefox', 'work')
    >>> bool(Cookies())
    False
    """

    file: str | None = None
    browser: str | None = None

    def __post_init__(self) -> None:
        # INFO: Checked here because both tools only log a warning for a bad cookie source and then
        # carry on logged out, which would look like the cookies were used.
        if self.file is not None and not Path(self.file).expanduser().is_file():
            raise ValueError(f"cookies file not found: {self.file}")

        if self.browser is not None and self.browser_spec()[0] not in COOKIE_BROWSERS:
            raise ValueError(f"unsupported browser {self.browser!r}, expected one of: {', '.join(sorted(COOKIE_BROWSERS))}")

    def __bool__(self) -> bool:
        return bool(self.file or self.browser)

    def browser_spec(self) -> tuple[str, str | None]:
        """Split ``BROWSER[:PROFILE]`` on the first colon, so a Windows profile path survives."""
        name, _, profile = (self.browser or "").partition(":")
        return name.strip().lower(), profile or None


class Provider(Protocol):
    name: str

    def extract(self, url: str, platform: str, want: list[str] | None = None) -> Result:
        """Return everything the provider can find at ``url``, in content-dl's format.

        ``platform`` comes from the router and is copied into the result as-is. ``want`` lists
        the item kinds asked for (``None`` means all), so a provider can skip extra requests.
        Raise ``ProviderError`` when the URL can't be handled.

        Providers are built with the user's ``Cookies`` (or None) by the registry.
        """
        ...
