"""HTTP for the native and cobalt providers and the downloader: requests with the user's cookies, errors as ProviderError."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any

from content_dl.providers.base import Cookies, ProviderError

TIMEOUT_SECONDS = 20
CHUNK_BYTES = 1 << 16

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


def post_json(url: str, payload: Any, *, headers: dict[str, str] | None = None) -> Response:
    """POST a JSON body, for API providers (cobalt). Uses requests: an API needs no browser impersonation."""
    import requests

    options_headers = {"Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
    try:
        response = requests.post(url, json=payload, headers=options_headers, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise ProviderError(f"request to {url} failed: {error}") from error

    return Response(status=response.status_code, url=str(response.url), text=response.text)


@dataclass(frozen=True)
class Stream:
    url: str
    content_type: str | None
    chunks: Iterator[bytes]


@contextmanager
def stream(
    url: str, *, headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None
) -> Iterator[Stream]:
    """GET a file in chunks, for the downloader. A status of 400 or more raises ProviderError."""
    options: dict[str, Any] = {
        "headers": dict(headers or {}),
        "cookies": cookies or {},
        # INFO: (connect, read) rather than one total, so a large video isn't cut off after 20 seconds.
        "timeout": (TIMEOUT_SECONDS, TIMEOUT_SECONDS),
        "stream": True,
    }

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
        raise ProviderError(f"request to {url} failed: {error}") from error

    try:
        if response.status_code >= 400:
            raise ProviderError(f"{url} answered HTTP {response.status_code}")

        content_type = response.headers.get("Content-Type")
        try:
            yield Stream(url=str(response.url), content_type=content_type, chunks=response.iter_content(CHUNK_BYTES))
        except errors as error:
            raise ProviderError(f"download from {url} failed: {error}") from error
    finally:
        response.close()
