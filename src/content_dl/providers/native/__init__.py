"""native provider: content-dl's own extractors, for the gaps no wrapped tool covers.

- Threads posts and profiles, from the JSON embedded in the page.
- Instagram profile pictures, from ``web_profile_info``.
- Facebook posts (author, text, date, photos), from the JSON a logged-in post page embeds.

The routing table also lists ``native`` for Snapchat metadata, and the unknown-site row lists
``generic`` (og: tags); neither is built yet, so ``platforms`` leaves Snapchat out and
extract() skips native there.
"""

from __future__ import annotations

from collections.abc import Mapping

from content_dl.providers.base import Cookies, ProviderError
from content_dl.providers.native import facebook, http, instagram, threads
from content_dl.schema import Result

NAME = "native"


class NativeProvider:
    name = NAME
    platforms = frozenset({"threads", "instagram", "facebook"})

    def __init__(self, cookies: Cookies | None = None) -> None:
        self.cookies = cookies
        self._jar_loaded = False
        self._jar = None

    def site_cookies(self, domain: str) -> dict[str, str]:
        # INFO: Loaded once, and only when a request needs it: reading a browser's store can take
        # a few seconds and may ask the OS keyring for access.
        if not self._jar_loaded:
            self._jar = http.load_cookie_jar(self.cookies)
            self._jar_loaded = True

        return http.cookies_for(self._jar, domain)

    def fetch_page(self, url: str, domain: str, headers: dict[str, str] | None = None) -> str:
        response = http.get(url, headers=headers, cookies=self.site_cookies(domain))
        if response.status == 404:
            raise ProviderError(f"native: {domain} has nothing at this URL (HTTP 404)")

        if response.status != 200:
            raise ProviderError(f"native: {domain} answered HTTP {response.status}")

        return response.text

    def fetch_instagram_profile(self, username: str) -> str:
        cookies = self.site_cookies("instagram.com")
        response = http.get(
            instagram.profile_url(username), headers=instagram.request_headers(username, cookies), cookies=cookies
        )
        if response.status == 404:
            raise ProviderError("native: Instagram refused the profile lookup (HTTP 404); it may need --cookies")

        if response.status == 429:
            raise ProviderError("native: Instagram is rate limiting profile lookups (HTTP 429); try again later")

        if response.status != 200:
            raise ProviderError(f"native: Instagram answered the profile lookup with HTTP {response.status}")

        return response.text

    def extract_threads(self, url: str, platform: str) -> Result:
        kind, _, _ = threads.parse_url(url)
        html = self.fetch_page(url, "threads.com")
        if kind == "profile":
            return threads.profile_from_page(html, url, platform)

        return threads.post_from_page(html, url, platform)

    def extract_instagram(self, url: str, platform: str, known: Mapping[str, Result] | None) -> Result:
        _, username = instagram.parse_url(url)
        username = username or instagram.username_from_known(known)
        if not username:
            raise ProviderError("native: the Instagram avatar needs the uploader's username, and no provider found it")

        profile = instagram.profile_from_response(self.fetch_instagram_profile(username), platform)
        return instagram.result_for(url, platform, profile)

    def extract(
        self, url: str, platform: str, want: list[str] | None = None, known: Mapping[str, Result] | None = None
    ) -> Result:
        if platform == "threads":
            return self.extract_threads(url, platform)

        if platform == "instagram":
            return self.extract_instagram(url, platform, known)

        if platform == "facebook":
            if not facebook.is_post_url(url):
                raise ProviderError("native: on Facebook only posts are read natively")

            return facebook.post_from_page(self.fetch_page(url, "facebook.com"), url, platform)

        raise ProviderError(f"native: no extractor for {platform}")


__all__ = ["NativeProvider"]
