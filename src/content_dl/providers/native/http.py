"""HTTP for the native providers: one GET with the user's cookies, errors as ProviderError."""

from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any

from content_dl.providers.base import Cookies, ProviderError

TIMEOUT_SECONDS = 20

# INFO: Used only when curl_cffi isn't installed. With curl_cffi, its browser profile sets a
# matching User-Agent and TLS fingerprint, the same way yt-dlp's impersonation does.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
IMPERSONATE = "chrome"


@dataclass(frozen=True)
class Response:
    status: int
    url: str
    text: str


def load_cookie_jar(cookies: Cookies | None) -> CookieJar | None:
    """Read the user's cookies with yt-dlp's loaders, which content-dl already depends on.

    INFO: yt-dlp's jar reads the ``#HttpOnly_`` lines that browser exporters write, which the
    standard library's MozillaCookieJar silently skips.
    """
    if not cookies:
        return None

    from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser

    try:
        if cookies.file:
            jar = YoutubeDLCookieJar(cookies.file)
            jar.load()
            return jar

        name, profile = cookies.browser_spec()
        return extract_cookies_from_browser(name, profile)
    except Exception as error:  # noqa: BLE001 - both loaders raise a wide range of errors
        raise ProviderError(f"native: couldn't read the cookies: {error}") from error


def cookies_for(jar: CookieJar | None, domain: str) -> dict[str, str]:
    """The jar's cookies for one site, so a request never carries another site's cookies.

    >>> from http.cookiejar import Cookie
    >>> def cookie(name, domain):
    ...     return Cookie(0, name, "v", None, False, domain, True, True, "/", True, True, None, False, None, None, {})
    >>> jar = CookieJar()
    >>> for name, domain in (("sessionid", ".instagram.com"), ("sid", ".example.com")):
    ...     jar.set_cookie(cookie(name, domain))
    >>> cookies_for(jar, "instagram.com")
    {'sessionid': 'v'}
    """
    if jar is None:
        return {}

    return {
        cookie.name: cookie.value or ""
        for cookie in jar
        if cookie.domain.lstrip(".") == domain or cookie.domain.endswith("." + domain)
    }


def get(url: str, *, headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None) -> Response:
    """GET a page, preferring curl_cffi (installed through yt-dlp's curl-cffi extra) over requests."""
    options: dict[str, Any] = {"headers": dict(headers or {}), "cookies": cookies or {}, "timeout": TIMEOUT_SECONDS}

    try:
        from curl_cffi import requests as client

        options["impersonate"] = IMPERSONATE
        errors: tuple[type[BaseException], ...] = (client.RequestsError,)
    except ImportError:
        import requests as client

        options["headers"].setdefault("User-Agent", USER_AGENT)
        errors = (client.RequestException,)

    try:
        response = client.get(url, **options)
    except errors as error:
        raise ProviderError(f"native: request to {url} failed: {error}") from error

    return Response(status=response.status_code, url=str(response.url), text=response.text)
