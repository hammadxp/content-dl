"""native provider: content-dl's own extractors, for the gaps no wrapped tool covers.

- Threads posts and profiles, from the JSON embedded in the page.
- Instagram profile pictures, from ``web_profile_info``.
- Facebook posts (author, text, date, photos), from the JSON a logged-in post page embeds.
- Pinterest profiles and avatars, from the web app's ``UserResource``, and pin.it links resolved.

The routing table also lists ``native`` for Snapchat metadata, and the unknown-site row lists
``generic`` (og: tags); neither is built yet, so ``platforms`` leaves Snapchat out and
extract() skips native there.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from content_dl.providers.base import Cookies, NeedsKnownError, ProviderError
from content_dl.providers.native import facebook, http, instagram, pinterest, threads
from content_dl.schema import Post, Result

NAME = "native"


class NativeProvider:
    name = NAME
    platforms = frozenset({"threads", "instagram", "facebook", "pinterest"})

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

    def fetch_pinterest(self, name: str, options: Mapping[str, Any]) -> tuple[int, str]:
        cookies = self.site_cookies("pinterest.com")
        response = http.get(
            pinterest.resource_url(name, options), headers=pinterest.request_headers(name, cookies), cookies=cookies
        )
        return response.status, response.text

    def resolve_pinterest_url(self, url: str) -> str:
        """The page a pin.it link opens, without its share parameters; a board invite becomes its board.

        A link that can't be resolved is passed on unchanged. A board invite that can't be found
        raises ProviderError, since no extractor reads the invite page.
        """
        if pinterest.is_short_url(url):
            try:
                response = http.get(pinterest.shortener_url(url))
            except ProviderError:
                return url

            if response.status != 200:
                return url

            url = response.url

        kind, board_id = pinterest.parse_url(url)
        if kind != "invite":
            return pinterest.clean_url(url)

        if not board_id:
            raise ProviderError("native: this Pinterest invitation names no board; open it and use the board's URL")

        status, body = self.fetch_pinterest("BoardResource", {"board_id": board_id, "field_set_key": "detailed"})
        board_url = pinterest.board_url_from_response(body) if status == 200 else None
        if not board_url:
            hint = "check the cookies are for the invited account" if self.cookies else "a private board needs --cookies"
            raise ProviderError(f"native: Pinterest didn't find the invited board; {hint}")

        return board_url

    def pinterest_avatar_url(self, user: Mapping[str, Any]) -> str | None:
        """The original upload when Pinterest serves it, else the largest sized copy."""
        candidates = pinterest.avatar_candidates(user)
        for url in candidates[:-1]:
            try:
                if http.get(url).status == 200:
                    return url
            except ProviderError:
                pass

        return candidates[-1] if candidates else None

    def extract_pinterest(self, url: str, platform: str, known: Mapping[str, Result] | None) -> Result:
        kind, username = pinterest.parse_url(url)
        options = {"username": username} if kind == "profile" else None
        if kind == "pin":
            options = pinterest.uploader_from_known(known)
            if not options:
                raise NeedsKnownError("native: the Pinterest avatar needs the pin's uploader, and no provider found it")

        if not options:
            raise ProviderError("native: on Pinterest only profiles and pin uploaders are read natively")

        status, body = self.fetch_pinterest("UserResource", {**options, "field_set_key": "profile"})
        user = pinterest.resource_data(body) if status in (200, 404) else None
        if status not in (200, 404):
            raise ProviderError(f"native: Pinterest answered the profile lookup with HTTP {status}")

        if not user:
            raise ProviderError("native: Pinterest has no such profile")

        profile = pinterest.profile_from_user(user, platform, self.pinterest_avatar_url(user))
        return profile if kind == "profile" else Post(platform=platform, uploader=profile)

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

        if platform == "pinterest":
            return self.extract_pinterest(url, platform, known)

        raise ProviderError(f"native: no extractor for {platform}")


__all__ = ["NativeProvider"]
