"""native provider: content-dl's own extractors, for the gaps no wrapped tool covers.

- Threads posts and profiles, from the JSON embedded in the page, and share links resolved.
- Instagram profile pictures, from ``web_profile_info``.
- Facebook posts (author, text, date, photos), from the JSON a logged-in post page embeds.
- Pinterest profiles and avatars, from the web app's ``UserResource``, and pin.it links resolved.
- Reddit posts from the public embed page (logged out), profile and community icons from
  ``about.json``, and share links resolved.
- Snapchat Spotlight videos and profiles, from the page's schema.org JSON, and share links resolved.

The unknown-site row lists ``generic`` (og: tags), which isn't built yet; extract() skips it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from content_dl.providers.base import Cookies, NeedsKnownError, ProviderError
from content_dl.providers.native import facebook, http, instagram, pinterest, reddit, snapchat, threads
from content_dl.schema import Item, Post, Profile, Result

NAME = "native"


class NativeProvider:
    name = NAME
    platforms = frozenset({"threads", "instagram", "facebook", "pinterest", "reddit", "snapchat"})

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

    def resolve_reddit_url(self, url: str) -> str:
        """The page a Reddit share link opens, without its share parameters. On a network error the link is kept."""
        if not reddit.is_share_url(url):
            return url

        try:
            response = http.get(url, cookies=self.site_cookies("reddit.com"))
        except ProviderError:
            return url

        return reddit.clean_url(response.url) if response.url != url else url

    def reddit_video_items(self, player: dict[str, str]) -> list[Item]:
        """The video and audio streams in a Reddit player's HLS playlist, read by yt-dlp's generic extractor."""
        # INFO: Imported here so the provider modules don't import each other at load time.
        from content_dl.providers.ytdlp import YtDlpProvider, items_from_info

        source = player.get("src")
        if not source:
            return []

        try:
            info = YtDlpProvider(cookies=self.cookies).fetch_info(source)
        except ProviderError:
            return []  # The poster still stands in; yt-dlp and cobalt can still try the video.

        items = [item for item in items_from_info(info) if item.media_type in ("video", "audio")]
        for item in items:
            item.provider = NAME

        return items

    def reddit_profile(self, kind: str, name: str, platform: str) -> Profile:
        """A user's or community's icon. Reddit blocks this lookup logged out, so it needs cookies."""
        response = http.get(reddit.about_url(kind, name), cookies=self.site_cookies("reddit.com"))
        if response.status in (403, 429) or reddit.is_blocked_page(response.text):
            hint = "check the cookies are still signed in" if self.cookies else "pass --cookies or --cookies-from-browser"
            raise ProviderError(f"native: Reddit blocks logged-out profile lookups; {hint}")

        if response.status == 404:
            raise ProviderError(f"native: Reddit has no such {'user' if kind == 'profile' else 'community'}")

        if response.status != 200:
            raise ProviderError(f"native: Reddit answered the profile lookup with HTTP {response.status}")

        return reddit.profile_from_about(response.text, kind, name, platform)

    def extract_reddit(self, url: str, platform: str, media_types: list[str] | None) -> Result:
        kind, name, post_id = reddit.parse_url(url)
        if kind in ("profile", "community") and name:
            return self.reddit_profile(kind, name, platform)

        if not post_id:
            raise ProviderError("native: this Reddit URL names no post")

        # INFO: The embed page, unlike the post page and the JSON API, is served logged out.
        page = self.fetch_page(reddit.embed_url(name, post_id), "embed.reddit.com")
        player = reddit.player_attributes(page)
        video_items = self.reddit_video_items(player) if player else None
        post = reddit.post_from_embed(page, url, platform, video_items)
        uploader = post.uploader
        if self.cookies and (media_types is None or "avatar" in media_types) and uploader and uploader.username:
            # INFO: Only with cookies: logged out, about.json is blocked and the lookup would only cost a request.
            try:
                uploader.avatar = self.reddit_profile("profile", uploader.username, platform).avatar
            except ProviderError:
                pass

        return post

    def resolve_snapchat_url(self, url: str) -> str:
        """The page a Snapchat share link opens, in its canonical form. On a network error the link is kept."""
        if snapchat.is_short_url(url):
            try:
                response = http.get(url, cookies=self.site_cookies("snapchat.com"))
            except ProviderError:
                return url

            if response.status == 200:
                url = response.url

        return snapchat.clean_url(url)

    def extract_snapchat(self, url: str, platform: str) -> Result:
        kind, _ = snapchat.parse_url(url)
        page = self.fetch_page(snapchat.page_url(url), "snapchat.com")
        if kind == "profile":
            return snapchat.profile_from_page(page, platform)

        return snapchat.post_from_page(page, url, platform)

    def resolve_threads_url(self, url: str) -> str:
        """The post page a Threads share link opens, minus its share parameters. On a network error it's kept."""
        if not threads.is_share_url(url):
            return url

        try:
            response = http.get(url, cookies=self.site_cookies("threads.com"))
        except ProviderError:
            return url

        if response.status != 200 or threads.is_share_url(response.url):
            return url

        return threads.clean_url(response.url)

    def threads_hd_avatar(self, username: str, platform: str) -> Item | None:
        """The uploader's HD avatar from their profile page, or None when the page can't be read."""
        profile_url = f"{threads.ROOT}/@{username}"
        try:
            return threads.profile_from_page(self.fetch_page(profile_url, "threads.com"), profile_url, platform).avatar
        except ProviderError:
            return None

    def extract_threads(self, url: str, platform: str, media_types: list[str] | None) -> Result:
        kind, _, _ = threads.parse_url(url)
        html = self.fetch_page(url, "threads.com")
        if kind == "profile":
            return threads.profile_from_page(html, url, platform)

        post = threads.post_from_page(html, url, platform)
        uploader = post.uploader
        # INFO: A post page only carries the 150px avatar, and its CDN URL is signed, so a larger
        # size can't be asked for by editing it. The profile page lists the HD versions.
        if (media_types is None or "avatar" in media_types) and uploader and uploader.username and uploader.avatar:
            small = uploader.avatar
            hd = self.threads_hd_avatar(uploader.username, platform)
            if hd and (hd.width or 0) > (small.width or 0):
                uploader.avatar = hd

        return post

    def extract_instagram(self, url: str, platform: str, known: Mapping[str, Result] | None) -> Result:
        _, username = instagram.parse_url(url)
        username = username or instagram.username_from_known(known)
        if not username:
            raise ProviderError("native: the Instagram avatar needs the uploader's username, and no provider found it")

        profile = instagram.profile_from_response(self.fetch_instagram_profile(username), platform)
        return instagram.result_for(url, platform, profile)

    def extract(
        self, url: str, platform: str, media_types: list[str] | None = None, known: Mapping[str, Result] | None = None
    ) -> Result:
        if platform == "threads":
            return self.extract_threads(url, platform, media_types)

        if platform == "instagram":
            return self.extract_instagram(url, platform, known)

        if platform == "facebook":
            if not facebook.is_post_url(url):
                raise ProviderError("native: on Facebook only posts are read natively")

            return facebook.post_from_page(self.fetch_page(url, "facebook.com"), url, platform)

        if platform == "pinterest":
            return self.extract_pinterest(url, platform, known)

        if platform == "reddit":
            return self.extract_reddit(url, platform, media_types)

        if platform == "snapchat":
            return self.extract_snapchat(url, platform)

        raise ProviderError(f"native: no extractor for {platform}")


__all__ = ["NativeProvider"]
