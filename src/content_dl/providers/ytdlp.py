"""yt-dlp provider: video and audio streams with every format, plus uniform metadata.

yt-dlp is imported as a library and only asked for metadata (``download=False``).
``post_from_info`` does the translation and takes a plain info dict, so it can be tested
against saved output without touching the network.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from typing import Any

from content_dl.providers.base import Cookies, LoginRequiredError, ProviderError
from content_dl.providers.common import ext_from_url as _ext_from_url
from content_dl.schema import Item, MediaType, Post, Profile, Result, SchemaError

NAME = "yt-dlp"

# The yt-dlp format fields kept in Item.formats, so a UI can offer quality choices and fetch a stream.
# INFO: Stream URLs usually expire within hours and can be tied to the IP that extracted them, so a
# client should download soon after extracting. Some sites also reject a request without the
# matching http_headers, which is why those are kept next to the URL.
FORMAT_FIELDS = (
    "format_id",
    "url",
    "http_headers",
    "format_note",
    "ext",
    "resolution",
    "width",
    "height",
    "fps",
    "dynamic_range",
    "vcodec",
    "acodec",
    "tbr",
    "vbr",
    "abr",
    "asr",
    "audio_channels",
    "filesize",
    "filesize_approx",
    "language",
    "protocol",
)

# Storyboards are preview sprite sheets, not streams.
STORYBOARD_EXTS = frozenset({"mhtml"})

# INFO: Some extractors make up a title when the site has none ("Post by someone" on Instagram).
# A made-up title would hide the caption in output templates, so it is dropped.
GENERATED_TITLES: dict[str, tuple[str, ...]] = {
    "Instagram": ("Post by {channel}", "Video by {channel}"),
}

# Extractors whose ``channel`` field is the account's username rather than a display name.
CHANNEL_IS_USERNAME = frozenset({"Instagram"})

# INFO: On X, a one-video post's ``id`` is the video's media id and ``display_id`` the post's own id.
POST_ID_IS_DISPLAY_ID = frozenset({"Twitter"})


def _trim_format(fmt: dict[str, Any]) -> dict[str, Any]:
    return {key: fmt[key] for key in FORMAT_FIELDS if fmt.get(key) is not None}


def _is_audio_only(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") == "none" and fmt.get("acodec") != "none"


def _has_video(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") != "none" and fmt.get("ext") not in STORYBOARD_EXTS


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _title_from_info(info: dict[str, Any]) -> str | None:
    title = info.get("title") or None
    channel = info.get("channel")
    generated = GENERATED_TITLES.get(info.get("extractor_key") or "", ())
    if title and channel and title in {pattern.format(channel=channel) for pattern in generated}:
        return None

    return title


def _uploader_from_info(info: dict[str, Any], platform: str) -> Profile | None:
    uploader_id = info.get("uploader_id")
    channel_id = info.get("channel_id")
    channel_is_username = info.get("extractor_key") in CHANNEL_IS_USERNAME
    name = info.get("uploader") or (None if channel_is_username else info.get("channel"))
    url = info.get("uploader_url") or info.get("channel_url")

    if not any((uploader_id, channel_id, name, url)):
        return None

    # INFO: On YouTube, uploader_id is the "@handle" and channel_id the stable "UC..." id. Other
    # extractors put a numeric id in uploader_id, which isn't a username, so it stays the id.
    username = info.get("channel") if channel_is_username else None
    is_handle = isinstance(uploader_id, str) and uploader_id.startswith("@")
    if is_handle:
        username = uploader_id[1:]

    if channel_is_username and username and not url and platform == "instagram":
        url = f"https://www.instagram.com/{username}/"

    profile_id = channel_id or (None if is_handle else uploader_id)

    return Profile(
        platform=platform,
        id=str(profile_id) if profile_id is not None else None,
        username=username,
        name=name,
        url=url,
    )


def _media_id(info: dict[str, Any]) -> str | None:
    """The entry's id, which the merger matches against other providers' ``media_id``."""
    return str(info["id"]) if info.get("id") is not None else None


def _thumbnail_from_info(info: dict[str, Any], index: int, media_type: MediaType = "thumbnail") -> Item | None:
    url = info.get("thumbnail")
    thumbnails = [thumb for thumb in info.get("thumbnails") or [] if thumb.get("url")]
    if not url and thumbnails:
        # yt-dlp sorts thumbnails worst to best.
        url = thumbnails[-1]["url"]

    if not url:
        return None

    chosen = next((thumb for thumb in thumbnails if thumb["url"] == url), {})
    extra = {"id": chosen.get("id"), "media_id": _media_id(info)}
    return Item(
        index=index,
        media_type=media_type,
        url=url,
        ext=_ext_from_url(url),
        width=chosen.get("width"),
        height=chosen.get("height"),
        provider=NAME,
        extra={key: value for key, value in extra.items() if value is not None} or None,
    )


# yt-dlp extractors whose pages can be a photo rather than a video.
STILL_EXTRACTORS = frozenset({"Pinterest"})


def items_from_info(info: dict[str, Any], index: int = 1, in_carousel: bool = False) -> list[Item]:
    """Turn one yt-dlp video entry into its video, audio and thumbnail items.

    All items share ``index``, so a UI can group the video with its audio and cover image.
    In a carousel, or from an extractor whose pages are often photos (a Pinterest image pin), an
    entry without streams is a still image, so its picture is an ``image`` item rather than a thumbnail.
    """
    formats = info.get("formats") or []
    video_formats = [fmt for fmt in formats if _has_video(fmt)]
    audio_formats = [fmt for fmt in formats if _is_audio_only(fmt)]
    duration = _number(info.get("duration"))
    extractor = info.get("extractor_key")

    # A single-file entry (no formats list) is still a stream yt-dlp can download.
    if not formats and info.get("url"):
        video_formats = [info]

    items: list[Item] = []
    if video_formats:
        items.append(
            Item(
                index=index,
                media_type="video",
                ext=info.get("ext"),
                width=info.get("width"),
                height=info.get("height"),
                duration=duration,
                formats=[_trim_format(fmt) for fmt in video_formats],
                provider=NAME,
                extra={"extractor_key": extractor, "format_id": info.get("format_id"), "media_id": _media_id(info)},
            )
        )

    if audio_formats:
        # yt-dlp sorts formats worst to best, so the last audio-only format is the default pick.
        best_audio = audio_formats[-1]
        items.append(
            Item(
                index=index,
                media_type="audio",
                ext=best_audio.get("ext"),
                duration=duration,
                formats=[_trim_format(fmt) for fmt in audio_formats],
                provider=NAME,
                extra={
                    "extractor_key": extractor,
                    "format_id": best_audio.get("format_id"),
                    "media_id": _media_id(info),
                },
            )
        )

    still = (in_carousel or extractor in STILL_EXTRACTORS) and not items
    thumbnail = _thumbnail_from_info(info, index, "image" if still else "thumbnail")
    if thumbnail:
        items.append(thumbnail)

    return items


def _post_id_from_info(info: dict[str, Any]) -> str | None:
    """
    >>> _post_id_from_info({"id": "211910935", "display_id": "211910944", "extractor_key": "Twitter"})
    '211910944'
    >>> _post_id_from_info({"id": "dQw4w9WgXcQ", "display_id": "dQw4w9WgXcQ", "extractor_key": "Youtube"})
    'dQw4w9WgXcQ'
    """
    if info.get("extractor_key") in POST_ID_IS_DISPLAY_ID and info.get("display_id") is not None:
        return str(info["display_id"])

    return str(info["id"]) if info.get("id") is not None else None


def _upload_date_from_info(info: dict[str, Any]) -> int | float | str | None:
    """Prefer a full timestamp; fall back to yt-dlp's day-only ``upload_date``.

    >>> _upload_date_from_info({"upload_date": "20050424"})
    '2005-04-24'
    >>> _upload_date_from_info({"timestamp": 1114313512, "upload_date": "20050424"})
    1114313512
    """
    timestamp = info.get("timestamp") or info.get("release_timestamp")
    if timestamp is not None:
        return timestamp

    day = info.get("upload_date") or info.get("release_date")
    if isinstance(day, str) and len(day) == 8 and day.isdigit():
        return f"{day[:4]}-{day[4:6]}-{day[6:]}"

    return None


def avatar_from_info(info: dict[str, Any]) -> Item | None:
    """Pick the channel avatar from a yt-dlp channel page's thumbnails.

    INFO: YouTube's extractor labels the full-size avatar ``avatar_uncropped``; the other entries
    are banners. Other extractors don't label avatars, so they return None here.
    """
    for thumb in info.get("thumbnails") or []:
        if thumb.get("id") == "avatar_uncropped" and thumb.get("url"):
            return Item(
                media_type="avatar",
                url=thumb["url"],
                ext=_ext_from_url(thumb["url"]),
                width=thumb.get("width"),
                height=thumb.get("height"),
                provider=NAME,
            )

    return None


def is_channel_info(info: dict[str, Any]) -> bool:
    """A channel page is a playlist whose id is the channel's own id, not a playlist id."""
    if info.get("_type") != "playlist":
        return False

    return info.get("id") is not None and info.get("id") in (info.get("channel_id"), info.get("uploader_id"))


def profile_from_info(info: dict[str, Any], platform: str) -> Profile:
    """Translate a yt-dlp channel page into a Profile.

    ``items`` stays empty: extracting a whole channel's videos is out of scope for v1.
    """
    profile = _uploader_from_info(info, platform) or Profile(platform=platform)
    profile.avatar = avatar_from_info(info)
    return profile


def is_carousel_info(info: dict[str, Any]) -> bool:
    """A carousel is a playlist whose entries are the post's own media, not links to other pages.

    INFO: Instagram returns a multi-media post as a playlist with every entry extracted inline.
    A real playlist (a YouTube playlist, a channel tab) lists links (``_type: url``) instead,
    because the provider extracts playlists flat.
    """
    entries = info.get("entries")
    if info.get("_type") not in ("playlist", "multi_video") or not isinstance(entries, list) or not entries:
        return False

    return all(isinstance(entry, dict) and entry.get("_type", "video") == "video" for entry in entries)


def post_from_info(info: dict[str, Any], platform: str) -> Post:
    """Translate a yt-dlp info dict for one video, or one carousel post, into a Post."""
    carousel = is_carousel_info(info)
    if not carousel and (info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None):
        raise ProviderError("playlists aren't supported, pass a single video or channel URL")

    if carousel:
        extractor = {"extractor_key": info.get("extractor_key")}
        items = [
            item
            for position, entry in enumerate(info["entries"], 1)
            for item in items_from_info({**extractor, **entry}, position, in_carousel=True)
        ]
    else:
        items = items_from_info(info)

    try:
        return Post(
            platform=platform,
            id=_post_id_from_info(info),
            title=_title_from_info(info),
            description=info.get("description") or None,
            upload_date=_upload_date_from_info(info),
            url=info.get("webpage_url") or info.get("original_url"),
            uploader=_uploader_from_info(info, platform),
            items=items,
        )
    except SchemaError as error:
        raise ProviderError(f"yt-dlp returned data content-dl can't read: {error}") from error


def result_from_info(info: dict[str, Any], platform: str) -> Post | Profile:
    if is_channel_info(info):
        return profile_from_info(info, platform)

    return post_from_info(info, platform)


def cookie_options(cookies: Cookies | None) -> dict[str, Any]:
    """yt-dlp options for the user's cookies.

    >>> cookie_options(Cookies(browser="chrome"))
    {'cookiesfrombrowser': ('chrome', None, None, None)}
    >>> cookie_options(None)
    {}
    """
    options: dict[str, Any] = {}
    if cookies and cookies.file:
        options["cookiefile"] = cookies.file

    if cookies and cookies.browser:
        options["cookiesfrombrowser"] = (*cookies.browser_spec(), None, None)

    return options


JS_RUNTIMES_ENV = "CONTENT_DL_JS_RUNTIMES"
FFMPEG_LOCATION_ENV = "CONTENT_DL_FFMPEG_LOCATION"


def parse_js_runtimes(value: str) -> dict[str, dict[str, str]]:
    """Parse yt-dlp's ``--js-runtimes`` syntax (``RUNTIME[:PATH]``, comma separated) into its option.

    >>> parse_js_runtimes("quickjs:/data/app/lib/libqjs.so")
    {'quickjs': {'path': '/data/app/lib/libqjs.so'}}
    >>> parse_js_runtimes("deno, node")
    {'deno': {}, 'node': {}}
    """
    runtimes: dict[str, dict[str, str]] = {}
    for part in value.split(","):
        name, _, path = part.strip().partition(":")
        if name:
            runtimes[name.lower()] = {"path": path} if path else {}

    return runtimes


def host_options() -> dict[str, Any]:
    """yt-dlp options that describe the host rather than one request: its JS runtime and ffmpeg.

    INFO: The Seal fork needs these on Android, where yt-dlp's default runtime (deno) doesn't exist
    and YouTube needs a JS runtime. youtubedl-android ships QuickJS and ffmpeg as native libraries,
    and passes the same two settings to yt-dlp. Read from the environment so every yt-dlp call,
    in extraction and in downloads, sees them without threading them through ``extract()``.
    """
    options: dict[str, Any] = {}
    runtimes = os.environ.get(JS_RUNTIMES_ENV, "").strip()
    if runtimes:
        options["js_runtimes"] = parse_js_runtimes(runtimes)

    ffmpeg = os.environ.get(FFMPEG_LOCATION_ENV, "").strip()
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg

    return options


ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


# INFO: yt-dlp retries downloads but not most extractors' metadata requests, so a dropped
# connection (Pinterest sometimes ends TLS early) would fail the whole URL. HTTP errors aren't retried.
NETWORK_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


def is_network_error(error: BaseException) -> bool:
    """Did yt-dlp fail because the connection broke (TLS, reset, timeout) rather than on an answer?

    >>> from yt_dlp.networking.exceptions import HTTPError, TransportError
    >>> from yt_dlp.utils import DownloadError, ExtractorError
    >>> def failed(cause):
    ...     return DownloadError("x", (ExtractorError, ExtractorError("x", cause=cause), None))
    >>> is_network_error(failed(TransportError("EOF occurred in violation of protocol")))
    True
    >>> is_network_error(failed(ValueError("bad JSON")))
    False
    """
    from yt_dlp.networking.exceptions import TransportError

    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue

        seen.add(id(current))
        if isinstance(current, TransportError):
            return True

        exc_info = getattr(current, "exc_info", None)
        pending += [
            exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) > 1 else None,
            getattr(current, "cause", None),
            current.__cause__,
            current.__context__,
        ]

    return False


def error_message(error: BaseException) -> str:
    """yt-dlp's error text without its "ERROR:" prefix.

    INFO: In a terminal yt-dlp colours the prefix, so the colour codes are removed first or the
    prefix wouldn't match and would show up twice in content-dl's own error line.

    >>> error_message(ValueError("\\x1b[0;31mERROR:\\x1b[0m Unsupported URL: https://e.com/"))
    'Unsupported URL: https://e.com/'
    """
    return ANSI_ESCAPE.sub("", str(error)).removeprefix("ERROR:").strip()


def final_url(url: str, cookies: Cookies | None = None) -> str:
    """Follow a link's redirects with yt-dlp's HTTP client and return where it ends up.

    INFO: Facebook redirects a share link for yt-dlp's client but not for a bare request, so share
    links are resolved here, with the same headers and cookies yt-dlp's extractors use.
    """
    import yt_dlp
    from yt_dlp.networking.exceptions import RequestError

    options = {**host_options(), **cookie_options(cookies), "quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL({**options, "logger": SilentLogger()}) as ydl:
            response = ydl.urlopen(url)
            try:
                return str(response.url)
            finally:
                response.close()
    except (RequestError, yt_dlp.utils.DownloadError) as error:
        raise ProviderError(f"request to {url} failed: {error_message(error)}") from error


X_LOGGED_OUT_MESSAGE = "X only shows this post to logged-in accounts (it may be age-restricted, protected or deleted)"

_own_media_twitter_ie: type | None = None


def own_media_twitter_ie() -> type:
    """yt-dlp's X extractor, limited to the post's own media.

    INFO: yt-dlp adds a quoted post's videos to the quoting post, so a photo post that quotes a video
    came back as that video, under the video's id. The quoted post is dropped from the status before
    yt-dlp reads it, along with the link to it, which yt-dlp would otherwise follow when the post
    has no video. Logged out, X answers an age-restricted post with an empty result that yt-dlp
    reads as a post without media; that is raised as an error instead.
    """
    global _own_media_twitter_ie
    if _own_media_twitter_ie is not None:
        return _own_media_twitter_ie

    from yt_dlp.extractor.twitter import TwitterIE
    from yt_dlp.utils import ExtractorError

    class OwnMediaTwitterIE(TwitterIE):
        @classmethod
        def ie_key(cls) -> str:
            return TwitterIE.ie_key()

        def _extract_status(self, twid: str) -> dict[str, Any]:
            status = super()._extract_status(twid)
            if not status.get("id_str"):
                raise ExtractorError(X_LOGGED_OUT_MESSAGE, expected=True)

            quoted_id = status.get("quoted_status_id_str")
            status.pop("quoted_status", None)
            urls = (status.get("entities") or {}).get("urls")
            if quoted_id and isinstance(urls, list):
                status["entities"]["urls"] = [
                    link for link in urls if f"/status/{quoted_id}" not in str(link.get("expanded_url") or "")
                ]

            return status

    _own_media_twitter_ie = OwnMediaTwitterIE
    return OwnMediaTwitterIE


class SilentLogger:
    """yt-dlp prints errors and warnings even when quiet; content-dl reports failures itself, once."""

    def debug(self, message: str) -> None:
        pass

    info = warning = error = debug


class YtDlpProvider:
    name = NAME
    platforms = None

    def __init__(self, options: dict[str, Any] | None = None, cookies: Cookies | None = None) -> None:
        self.options = {
            **host_options(),
            **cookie_options(cookies),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": SilentLogger(),
            # A watch URL with &list= means the video, not the playlist.
            "noplaylist": True,
            # INFO: A channel URL is a playlist to yt-dlp. Listing entries flat, and only the first,
            # keeps a profile lookup to one page request instead of one per video.
            "extract_flat": "in_playlist",
            # INFO: A carousel entry that is a still image has no formats. Without this, yt-dlp
            # treats it as an error and drops the whole carousel.
            "ignore_no_formats_error": True,
            **(options or {}),
        }

    def fetch_info(self, url: str) -> dict[str, Any]:
        # INFO: Imported here so `import content_dl` stays fast and works without yt-dlp installed
        # for the parts that don't need it.
        import yt_dlp

        twitter_ie = own_media_twitter_ie()
        for attempt in range(1, NETWORK_ATTEMPTS + 1):
            try:
                with yt_dlp.YoutubeDL(self.options) as ydl:
                    if twitter_ie.suitable(url):
                        # Replaces yt-dlp's own X extractor under the same key, in the same place.
                        ydl.add_info_extractor(twitter_ie())
                    info = ydl.extract_info(url, download=False)
                    return ydl.sanitize_info(info)
            except yt_dlp.utils.DownloadError as error:
                if attempt < NETWORK_ATTEMPTS and is_network_error(error):
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue

                if X_LOGGED_OUT_MESSAGE in str(error):
                    raise LoginRequiredError(X_LOGGED_OUT_MESSAGE) from error

                raise ProviderError(f"yt-dlp: {error_message(error)}") from error

        raise AssertionError("unreachable")

    def fetch_avatar(self, uploader_url: str) -> Item | None:
        """Look up an uploader's avatar on their channel page. A failure leaves it unset."""
        try:
            return avatar_from_info(self.fetch_info(uploader_url))
        except ProviderError:
            return None

    def extract(
        self, url: str, platform: str, media_types: list[str] | None = None, known: Mapping[str, Result] | None = None
    ) -> Post | Profile:
        # INFO: Only the first entry of a YouTube channel page is listed, keeping a profile lookup
        # to one page request. Elsewhere a playlist can be a carousel, whose entries all count.
        if platform == "youtube":
            self.options.setdefault("playlistend", 1)

        info = self.fetch_info(url)
        if not info:
            raise ProviderError("yt-dlp returned nothing for this URL")

        result = result_from_info(info, platform)

        # INFO: A video page doesn't include the uploader's avatar, so it costs one more request
        # (about a second). Skipped when the caller didn't ask for avatars, and on other platforms,
        # whose yt-dlp extractors don't label an avatar on the profile page.
        avatar_requested = media_types is None or "avatar" in media_types
        uploader = result.uploader if isinstance(result, Post) else None
        if platform == "youtube" and avatar_requested and uploader and uploader.url and uploader.avatar is None:
            uploader.avatar = self.fetch_avatar(uploader.url)

        return result
