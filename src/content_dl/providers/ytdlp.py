"""yt-dlp provider: video and audio streams with every format, plus uniform metadata.

yt-dlp is imported as a library and only asked for metadata (``download=False``).
``post_from_info`` does the translation and takes a plain info dict, so it can be tested
against saved output without touching the network.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from content_dl.providers.base import Cookies, ProviderError
from content_dl.schema import Item, ItemKind, Post, Profile, SchemaError

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


def _trim_format(fmt: dict[str, Any]) -> dict[str, Any]:
    return {key: fmt[key] for key in FORMAT_FIELDS if fmt.get(key) is not None}


def _is_audio_only(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") == "none" and fmt.get("acodec") != "none"


def _has_video(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") != "none" and fmt.get("ext") not in STORYBOARD_EXTS


def _ext_from_url(url: str) -> str | None:
    """Return the file extension in a URL's path.

    >>> _ext_from_url("https://i.ytimg.com/vi/abc/hqdefault.jpg?sqp=xyz")
    'jpg'
    >>> _ext_from_url("https://example.com/image") is None
    True
    """
    last = urlparse(url).path.rsplit("/", 1)[-1]
    if "." not in last:
        return None

    ext = last.rsplit(".", 1)[-1].lower()
    return ext if ext.isalnum() and len(ext) <= 5 else None


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


def _thumbnail_from_info(info: dict[str, Any], index: int, kind: ItemKind = "thumbnail") -> Item | None:
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
        kind=kind,
        url=url,
        ext=_ext_from_url(url),
        width=chosen.get("width"),
        height=chosen.get("height"),
        provider=NAME,
        extra={key: value for key, value in extra.items() if value is not None} or None,
    )


def items_from_info(info: dict[str, Any], index: int = 1, in_carousel: bool = False) -> list[Item]:
    """Turn one yt-dlp video entry into its video, audio and thumbnail items.

    All items share ``index``, so a UI can group the video with its audio and cover image.
    In a carousel an entry without streams is a still image, so its picture is an ``image``
    item rather than a thumbnail.
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
                kind="video",
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
                kind="audio",
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

    still = in_carousel and not items
    thumbnail = _thumbnail_from_info(info, index, "image" if still else "thumbnail")
    if thumbnail:
        items.append(thumbnail)

    return items


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
                kind="avatar",
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
            id=str(info["id"]) if info.get("id") is not None else None,
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


class YtDlpProvider:
    name = NAME

    def __init__(self, options: dict[str, Any] | None = None, cookies: Cookies | None = None) -> None:
        self.options = {
            **cookie_options(cookies),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
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

        try:
            with yt_dlp.YoutubeDL(self.options) as ydl:
                info = ydl.extract_info(url, download=False)
                return ydl.sanitize_info(info)
        except yt_dlp.utils.DownloadError as error:
            message = str(error).removeprefix("ERROR: ").strip()
            raise ProviderError(f"yt-dlp: {message}") from error

    def fetch_avatar(self, uploader_url: str) -> Item | None:
        """Look up an uploader's avatar on their channel page. A failure leaves it unset."""
        try:
            return avatar_from_info(self.fetch_info(uploader_url))
        except ProviderError:
            return None

    def extract(self, url: str, platform: str, want: list[str] | None = None) -> Post | Profile:
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
        wants_avatar = want is None or "avatar" in want
        uploader = result.uploader if isinstance(result, Post) else None
        if platform == "youtube" and wants_avatar and uploader and uploader.url and uploader.avatar is None:
            uploader.avatar = self.fetch_avatar(uploader.url)

        return result
