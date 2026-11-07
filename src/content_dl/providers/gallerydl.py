"""gallery-dl provider: images and carousels, with per-site metadata mappers.

gallery-dl is imported as a library and run through its ``DataJob``, which collects the
extractor's messages without downloading anything. ``result_from_messages`` does the
translation and takes a plain message list, so it can be tested against saved output.

gallery-dl's metadata field names differ per site, so each platform has a small mapper.
A platform without one gets the generic mapper, which only reads common field names.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from content_dl.providers.base import ProviderError
from content_dl.schema import Item, ItemKind, Post, Profile, SchemaError

NAME = "gallery-dl"

# gallery-dl's message ids (gallery_dl.extractor.message.Message), copied so translating saved
# output doesn't need gallery-dl installed.
MESSAGE_DIRECTORY = 2
MESSAGE_URL = 3
MESSAGE_QUEUE = 6

VIDEO_EXTS = frozenset({"mp4", "webm", "mov", "m4v", "mkv"})
AUDIO_EXTS = frozenset({"m4a", "mp3", "aac", "ogg", "opus", "wav", "flac"})

# INFO: "ytdl:" URLs are gallery-dl handing a stream to yt-dlp, and "text:" URLs are post text
# saved as a file. Neither is a media file, and yt-dlp already covers streams in the routing table.
SKIPPED_URL_PREFIXES = ("ytdl:", "text:")

# Per-extractor options. Instagram's default yields "ytdl:" DASH URLs for videos; "merged" yields
# the direct, already-muxed MP4 instead, which is a plain file any downloader can fetch.
GALLERY_DL_OPTIONS: dict[str, dict[str, Any]] = {
    "instagram": {"videos": "merged"},
}

INSTAGRAM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

Kwdict = dict[str, Any]
Message = Sequence[Any]


def instagram_shortcode(media_id: Any) -> str | None:
    """Encode an Instagram numeric media id as the shortcode yt-dlp uses for the same media.

    Both providers then name a carousel entry the same way, so the merger can spot duplicates.

    >>> instagram_shortcode("3471234567890123456_123")
    'DAsTXK3JbrA'
    >>> instagram_shortcode(None) is None
    True
    """
    if media_id is None:
        return None

    digits = str(media_id).split("_", 1)[0]
    if not digits.isdigit():
        return None

    number = int(digits)
    code = ""
    while number:
        number, rest = divmod(number, 64)
        code = INSTAGRAM_ALPHABET[rest] + code

    return code or None


def _text(value: Any) -> str | None:
    """Return a non-empty string, or None. gallery-dl writes "" for a missing caption."""
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _id(value: Any) -> str | None:
    return None if value in (None, "", 0) else str(value)


def _size(value: Any) -> int | None:
    """gallery-dl writes 0 for an unknown width or height, which isn't a real size."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _date(kwdict: Kwdict, *keys: str) -> datetime | str | None:
    for key in keys:
        value = kwdict.get(key)
        if isinstance(value, (datetime, str)) and value:
            return value

    return None


def _ext_from_url(url: str) -> str | None:
    last = urlparse(url).path.rsplit("/", 1)[-1]
    if "." not in last:
        return None

    ext = last.rsplit(".", 1)[-1].lower()
    return ext if ext.isalnum() and len(ext) <= 5 else None


def _avatar(url: Any) -> Item | None:
    if not isinstance(url, str) or not url:
        return None

    return Item(kind="avatar", url=url, ext=_ext_from_url(url), provider=NAME)


# Post mappers: gallery-dl's post-level kwdict -> Post fields (uploader included).

def _instagram_post(post: Kwdict, platform: str) -> Kwdict:
    username = _text(post.get("username"))
    uploader = None
    if username or post.get("owner_id"):
        uploader = Profile(
            platform=platform,
            id=_id(post.get("owner_id")),
            username=username,
            name=_text(post.get("fullname")),
            url=f"https://www.instagram.com/{username}/" if username else None,
        )

    return {
        "id": _text(post.get("post_shortcode")),
        "title": None,
        "description": _text(post.get("description")),
        "upload_date": _date(post, "post_date", "date"),
        "url": _text(post.get("post_url")),
        "uploader": uploader,
    }


def _x_post(post: Kwdict, platform: str) -> Kwdict:
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    username = _text(author.get("name"))
    tweet_id = _id(post.get("tweet_id"))
    uploader = None
    if author:
        uploader = Profile(
            platform=platform,
            id=_id(author.get("id")),
            username=username,
            name=_text(author.get("nick")),
            url=f"https://x.com/{username}" if username else None,
            avatar=_avatar(author.get("profile_image")),
        )

    return {
        "id": tweet_id,
        "title": None,
        "description": _text(post.get("content")),
        "upload_date": _date(post, "date"),
        "url": f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else None,
        "uploader": uploader,
    }


def _reddit_post(post: Kwdict, platform: str) -> Kwdict:
    author = _text(post.get("author"))
    permalink = _text(post.get("permalink"))
    uploader = None
    if author and author != "[deleted]":
        uploader = Profile(platform=platform, username=author, url=f"https://www.reddit.com/user/{author}/")

    return {
        "id": _id(post.get("id")),
        "title": _text(post.get("title")),
        "description": _text(post.get("selftext")),
        "upload_date": _date(post, "date"),
        "url": f"https://www.reddit.com{permalink}" if permalink and permalink.startswith("/") else None,
        "uploader": uploader,
    }


def _tiktok_post(post: Kwdict, platform: str) -> Kwdict:
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    username = _text(author.get("uniqueId"))
    post_id = _id(post.get("id"))
    uploader = None
    if author:
        uploader = Profile(
            platform=platform,
            id=_id(author.get("id")),
            username=username,
            name=_text(author.get("nickname")),
            url=f"https://www.tiktok.com/@{username}" if username else None,
            avatar=_avatar(author.get("avatarLarger")),
        )

    return {
        "id": post_id,
        "title": None,
        "description": _text(post.get("desc")),
        "upload_date": _date(post, "date"),
        "url": f"https://www.tiktok.com/@{username}/video/{post_id}" if username and post_id else None,
        "uploader": uploader,
    }


def _generic_post(post: Kwdict, platform: str) -> Kwdict:
    return {
        "id": _id(post.get("post_id") or post.get("id")),
        "title": _text(post.get("title")),
        "description": _text(post.get("description") or post.get("content")),
        "upload_date": _date(post, "date"),
        "url": None,
        "uploader": None,
    }


POST_MAPPERS: dict[str, Callable[[Kwdict, str], Kwdict]] = {
    "instagram": _instagram_post,
    "x": _x_post,
    "reddit": _reddit_post,
    "tiktok": _tiktok_post,
}

# Per-file media id, in the form other providers use for the same media.
MEDIA_IDS: dict[str, Callable[[Kwdict], str | None]] = {
    "instagram": lambda file: instagram_shortcode(file.get("media_id")),
    "x": lambda file: _id(file.get("media_id")),
    "reddit": lambda file: _id(file.get("media_id")),
}


def _kind_for(ext: str | None) -> ItemKind:
    if ext in VIDEO_EXTS:
        return "video"

    if ext in AUDIO_EXTS:
        return "audio"

    return "image"


def item_from_file(url: str, file: Kwdict, platform: str, position: int) -> Item | None:
    """Turn one gallery-dl file message into an Item, or None for a non-media URL.

    ``index`` is gallery-dl's ``num`` (the carousel position, from 1). Sites that number a single
    file 0 get the file's position instead.
    """
    if url.startswith(SKIPPED_URL_PREFIXES):
        return None

    ext = _text(file.get("extension")) or _ext_from_url(url)
    num = file.get("num")
    index = num if isinstance(num, int) and not isinstance(num, bool) and num > 0 else position
    duration = file.get("duration")
    media_id = MEDIA_IDS.get(platform, lambda _: None)(file)

    extra = {
        "category": file.get("category"),
        "subcategory": file.get("subcategory"),
        "media_id": media_id,
        "filename": file.get("filename"),
    }
    kind = _kind_for(ext)

    return Item(
        index=index,
        kind=kind,
        url=url,
        ext=ext,
        width=_size(file.get("width")),
        height=_size(file.get("height")),
        duration=float(duration) if kind != "image" and isinstance(duration, (int, float)) and duration > 0 else None,
        provider=NAME,
        extra={key: value for key, value in extra.items() if value not in (None, "")} or None,
    )


def result_from_messages(messages: Sequence[Message], platform: str) -> Post:
    """Translate the messages of one gallery-dl ``DataJob`` run into a Post.

    A run with several posts (a profile feed, a collection) is rejected: v1 extracts one post
    at a time. So is a run that only hands URLs on to other extractors.
    """
    directories = [message[1] for message in messages if message[0] == MESSAGE_DIRECTORY]
    files = [(message[1], message[2]) for message in messages if message[0] == MESSAGE_URL]
    queued = [message for message in messages if message[0] == MESSAGE_QUEUE]

    if len(directories) > 1:
        raise ProviderError("gallery-dl: this URL holds several posts, pass a single post URL")

    if not directories and not files:
        if queued:
            raise ProviderError("gallery-dl: profiles and collections aren't supported, pass a single post URL")

        raise ProviderError("gallery-dl found nothing at this URL")

    post_kwdict = directories[0] if directories else files[0][1]
    mapper = POST_MAPPERS.get(platform, _generic_post)

    items = [item_from_file(url, file, platform, position) for position, (url, file) in enumerate(files, 1)]

    try:
        return Post(platform=platform, items=[item for item in items if item], **mapper(post_kwdict, platform))
    except SchemaError as error:
        raise ProviderError(f"gallery-dl returned data content-dl can't read: {error}") from error


class GalleryDlProvider:
    name = NAME

    def fetch_messages(self, url: str) -> list[Message]:
        # INFO: Imported here so `import content_dl` stays fast and works without gallery-dl.
        from gallery_dl import config, extractor, job

        found = extractor.find(url)
        if found is None:
            raise ProviderError("gallery-dl has no extractor for this URL")

        # INFO: gallery-dl's config is process-wide. content-dl only sets the options it needs and
        # never loads the user's gallery-dl config file, so results don't depend on the machine.
        for category, options in GALLERY_DL_OPTIONS.items():
            for key, value in options.items():
                config.set(("extractor", category), key, value)

        # gallery-dl logs warnings (lowered quality, retries) to stderr; failures surface as exceptions.
        logging.getLogger("gallery-dl").setLevel(logging.CRITICAL)

        data_job = job.DataJob(found, file=None)
        data_job.run()
        if data_job.exception is not None:
            error = data_job.exception
            raise ProviderError(f"gallery-dl: {type(error).__name__}: {error}".rstrip(": "))

        return data_job.data

    def extract(self, url: str, platform: str, want: list[str] | None = None) -> Post:
        return result_from_messages(self.fetch_messages(url), platform)
