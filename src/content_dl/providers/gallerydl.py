"""gallery-dl provider: images and carousels, with per-site metadata mappers.

gallery-dl is imported as a library and run through its ``DataJob``, which collects the
extractor's messages without downloading anything. ``result_from_messages`` does the
translation and takes a plain message list, so it can be tested against saved output.

gallery-dl's metadata field names differ per site, so each platform has a small mapper.
A platform without one gets the generic mapper, which only reads common field names.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from content_dl.providers.base import Cookies, ProviderError
from content_dl.providers.common import ext_from_url as _ext_from_url
from content_dl.providers.native import facebook as facebook_url
from content_dl.providers.native import instagram as instagram_url
from content_dl.providers.native import tiktok as tiktok_url
from content_dl.providers.native import x as x_url
from content_dl.schema import Item, MediaType, Post, Profile, Result, SchemaError

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
# X skips posts without media by default, so a text-only post would return nothing, not even its text.
GALLERY_DL_OPTIONS: dict[str, dict[str, Any]] = {
    "instagram": {"videos": "merged"},
    "twitter": {"text-tweets": True},
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


def _avatar(url: Any) -> Item | None:
    if not isinstance(url, str) or not url:
        return None

    return Item(media_type="avatar", url=url, ext=_ext_from_url(url), provider=NAME)


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
    kind = "photo" if post.get("imagePost") else "video"
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
        "url": f"https://www.tiktok.com/@{username}/{kind}/{post_id}" if username and post_id else None,
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
    "facebook": lambda file: _id(file.get("id")),
}


def _media_type_for(ext: str | None) -> MediaType:
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
    media_type = _media_type_for(ext)

    return Item(
        index=index,
        media_type=media_type,
        url=url,
        ext=ext,
        width=_size(file.get("width")),
        height=_size(file.get("height")),
        duration=(
            float(duration) if media_type != "image" and isinstance(duration, (int, float)) and duration > 0 else None
        ),
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


def instagram_profile_from_messages(messages: Sequence[Message], platform: str, username: str) -> Profile:
    """Translate gallery-dl's Instagram ``/USER/avatar/`` run into a Profile holding the avatar.

    >>> messages = [(3, "https://cdn.example.com/pic.jpg?x=1",
    ...              {"username": "someone", "fullname": "Some One", "owner_id": "42", "extension": "jpg"})]
    >>> profile = instagram_profile_from_messages(messages, "instagram", "someone")
    >>> profile.name, profile.avatar.ext, profile.avatar.provider
    ('Some One', 'jpg', 'gallery-dl')
    """
    files = [(message[1], message[2]) for message in messages if message[0] == MESSAGE_URL]
    if not files:
        raise ProviderError("gallery-dl found no profile picture for this account")

    url, file = files[0]
    name = _text(file.get("username")) or username
    avatar = Item(
        media_type="avatar",
        url=url,
        ext=_text(file.get("extension")) or _ext_from_url(url),
        width=_size(file.get("width")),
        height=_size(file.get("height")),
        provider=NAME,
    )
    return Profile(
        platform=platform,
        id=_id(file.get("owner_id")),
        username=name,
        name=_text(file.get("fullname")),
        url=f"https://www.instagram.com/{name}/",
        avatar=avatar,
    )


def facebook_profile_from_messages(messages: Sequence[Message], platform: str, url: str) -> Profile:
    """Translate gallery-dl's Facebook ``/ID/avatar`` run into a Profile holding the avatar.

    gallery-dl's ``username`` is the profile's vanity name when it has one; ``user_id`` is numeric.

    >>> messages = [(2, {"username": "", "user_id": "100", "title": "Profile pictures"}),
    ...             (3, "https://cdn.example.com/pic.jpg?x=1", {"user_id": "100", "extension": "jpg"})]
    >>> profile = facebook_profile_from_messages(messages, "facebook", "https://www.facebook.com/people/Some-One/100/")
    >>> profile.id, profile.name, profile.url, profile.avatar.ext
    ('100', 'Some One', 'https://www.facebook.com/people/Some-One/100/', 'jpg')
    """
    directories = [message[1] for message in messages if message[0] == MESSAGE_DIRECTORY]
    files = [(message[1], message[2]) for message in messages if message[0] == MESSAGE_URL]
    if not files:
        raise ProviderError("gallery-dl found no profile picture for this Facebook profile")

    avatar_url, file = files[0]
    info = {**(directories[0] if directories else {}), **file}
    vanity = _text(info.get("username"))
    if vanity and not facebook_url.PROFILE_NAME.fullmatch(vanity):
        vanity = None  # A photo page's "username" is the owner's display name, not a vanity name.

    return Profile(
        platform=platform,
        id=_id(info.get("user_id")) or facebook_url.profile_id(url),
        username=vanity,
        name=facebook_url.profile_name_from_url(url) or (_text(info.get("username")) if not vanity else None),
        url=url,
        avatar=Item(
            media_type="avatar",
            url=avatar_url,
            ext=_text(file.get("extension")) or _ext_from_url(avatar_url),
            width=_size(file.get("width")),
            height=_size(file.get("height")),
            provider=NAME,
        ),
    )


def tiktok_profile_from_messages(messages: Sequence[Message], platform: str, username: str) -> Profile:
    """Translate gallery-dl's TikTok ``/@USER/avatar`` run into a Profile holding the avatar.

    The run's kwdict is the account itself: ``uniqueId`` is the username, ``nickname`` the display name.

    >>> messages = [(3, "https://cdn.example.com/pic.jpeg?x=1",
    ...              {"id": "42", "uniqueId": "someone", "nickname": "Some One", "extension": "jpeg"})]
    >>> profile = tiktok_profile_from_messages(messages, "tiktok", "someone")
    >>> profile.id, profile.name, profile.url, profile.avatar.ext
    ('42', 'Some One', 'https://www.tiktok.com/@someone', 'jpeg')
    """
    files = [(message[1], message[2]) for message in messages if message[0] == MESSAGE_URL]
    if not files:
        raise ProviderError("gallery-dl found no profile picture for this TikTok account")

    url, file = files[0]
    name = _text(file.get("uniqueId")) or username
    return Profile(
        platform=platform,
        id=_id(file.get("id")),
        username=name,
        name=_text(file.get("nickname")),
        url=f"{tiktok_url.ROOT}/@{name}",
        avatar=Item(
            media_type="avatar",
            url=url,
            ext=_text(file.get("extension")) or _ext_from_url(url),
            width=_size(file.get("width")),
            height=_size(file.get("height")),
            provider=NAME,
        ),
    )


def x_profile_from_messages(messages: Sequence[Message], platform: str, username: str) -> Profile:
    """Translate gallery-dl's X ``/USER/photo`` run into a Profile holding the avatar.

    The run's ``user`` is the account: ``name`` is the username, ``nick`` the display name.

    >>> user = {"id": 42, "name": "someone", "nick": "Some One"}
    >>> messages = [(3, "https://pbs.twimg.com/profile_images/1/pic.jpg", {"user": user, "extension": "jpg"})]
    >>> profile = x_profile_from_messages(messages, "x", "someone")
    >>> profile.id, profile.name, profile.url, profile.avatar.ext
    ('42', 'Some One', 'https://x.com/someone', 'jpg')
    """
    directories = [message[1] for message in messages if message[0] == MESSAGE_DIRECTORY]
    files = [(message[1], message[2]) for message in messages if message[0] == MESSAGE_URL]
    info = files[0][1] if files else directories[0] if directories else {}
    user = info.get("user") if isinstance(info.get("user"), dict) else {}
    if not user:
        raise ProviderError("gallery-dl found no account at this X profile")

    name = _text(user.get("name")) or username
    avatar = None
    if files:
        url, file = files[0]
        avatar = Item(
            media_type="avatar",
            url=url,
            ext=_text(file.get("extension")) or _ext_from_url(url),
            width=_size(file.get("width")),
            height=_size(file.get("height")),
            provider=NAME,
        )

    return Profile(
        platform=platform,
        id=_id(user.get("id")),
        username=name,
        name=_text(user.get("nick")),
        url=f"{x_url.ROOT}/{name}",
        avatar=avatar,
    )


def config_entries(cookies: Cookies | None) -> list[tuple[tuple[str, ...], str, Any]]:
    """The gallery-dl config entries for one run: per-site options, plus the user's cookies.

    A cookies.txt file is a path; a browser is gallery-dl's ``[browser, profile]`` list.

    >>> config_entries(Cookies(browser="firefox:work"))[-1]
    (('extractor',), 'cookies', ['firefox', 'work'])
    """
    entries = [
        (("extractor", category), key, value)
        for category, options in GALLERY_DL_OPTIONS.items()
        for key, value in options.items()
    ]
    if cookies and cookies.file:
        entries.append((("extractor",), "cookies", cookies.file))
    elif cookies and cookies.browser:
        entries.append((("extractor",), "cookies", list(cookies.browser_spec())))

    return entries


class _ErrorLog(logging.Handler):
    """Collects an extractor's error log lines, without the URL gallery-dl puts in front.

    >>> log = _ErrorLog()
    >>> log.emit(logging.makeLogRecord({"levelno": logging.ERROR, "msg": "%s: Requested %s not available",
    ...                                 "args": ("https://www.tiktok.com/@a/video/1", "post")}))
    >>> log.messages
    ['Requested post not available']
    """

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage().strip()
        head, _, rest = message.partition(": ")
        self.messages.append(rest if head.startswith(("http://", "https://")) and rest else message)


class GalleryDlProvider:
    name = NAME
    platforms = None

    def __init__(self, cookies: Cookies | None = None) -> None:
        self.cookies = cookies

    def fetch_messages(self, url: str) -> list[Message]:
        # INFO: Imported here so `import content_dl` stays fast and works without gallery-dl.
        from gallery_dl import config, extractor, job

        # gallery-dl logs warnings (lowered quality, retries) to stderr; failures surface as exceptions.
        logging.getLogger("gallery-dl").setLevel(logging.CRITICAL)

        # INFO: gallery-dl's config is process-wide, so the options (cookies included) are applied
        # for this run only and removed afterwards; one call's cookies never leak into the next.
        # content-dl never loads the user's own gallery-dl config file either.
        with config.apply(config_entries(self.cookies)):
            found = extractor.find(url)
            if found is None:
                raise ProviderError("gallery-dl has no extractor for this URL")

            # INFO: Some extractors (TikTok's, for an unavailable post) log the reason as an error and
            # yield nothing instead of raising. The log is caught here so the reason reaches the
            # ProviderError, and kept off stderr, where it would print outside content-dl's output.
            errors = _ErrorLog()
            logger = getattr(found.log, "logger", found.log)  # Extractors log through a LoggerAdapter.
            logger.addHandler(errors)
            propagate, logger.propagate = logger.propagate, False
            try:
                data_job = job.DataJob(found, file=None)
                data_job.run()
            finally:
                logger.removeHandler(errors)
                logger.propagate = propagate

        if data_job.exception is not None:
            error = data_job.exception
            # Only the first line: later ones are gallery-dl's own tips, such as how to resume a run.
            message = str(error).strip().splitlines()[0] if str(error).strip() else ""
            raise ProviderError(f"gallery-dl: {type(error).__name__}: {message}".rstrip(": "))

        if not data_job.data and errors.messages:
            raise ProviderError(f"gallery-dl: {errors.messages[-1]}")

        return data_job.data

    def instagram_profile(self, username: str, platform: str) -> Profile:
        """The profile and avatar from gallery-dl's ``/USER/avatar/`` extractor.

        INFO: Instagram turns content-dl's own ``web_profile_info`` lookup away even with a session
        (it answers with the logged-out page), while gallery-dl's logged-in API calls work. So with
        cookies, gallery-dl supplies Instagram avatars.
        """
        messages = self.fetch_messages(f"https://www.instagram.com/{username}/avatar/")
        return instagram_profile_from_messages(messages, platform, username)

    def facebook_post(self, platform: str, known: Mapping[str, Result] | None, avatar_requested: bool) -> Post:
        """Full-size copies of a Facebook post's photos, from the photo pages native found.

        INFO: gallery-dl has no Facebook post extractor (and its profile pattern would take
        "story.php" for a profile name). The post page only holds reduced copies, so native reads
        it and lists each photo's page, and gallery-dl's photo extractor fetches the full size.
        """
        native = (known or {}).get("native")
        native_items = native.items if isinstance(native, Post) else []
        photos = [item for item in native_items if (item.extra or {}).get("photo_page")]
        if not photos:
            raise ProviderError("gallery-dl can't read Facebook posts; it needs the photo pages native finds")

        items: list[Item] = []
        for photo in photos:
            try:
                messages = self.fetch_messages(photo.extra["photo_page"])
            except ProviderError:
                continue  # native's reduced copy still stands in for this photo.

            files = [(message[1], message[2]) for message in messages if message[0] == MESSAGE_URL]
            if files and (item := item_from_file(files[0][0], files[0][1], platform, photo.index or 1)):
                item.index = photo.index
                item.width = item.width or photo.extra.get("full_width")
                item.height = item.height or photo.extra.get("full_height")
                items.append(item)

        uploader = None
        native_uploader = native.uploader if isinstance(native, Post) else None
        if avatar_requested and native_uploader and native_uploader.id:
            try:
                messages = self.fetch_messages(f"https://www.facebook.com/{native_uploader.id}/avatar")
                uploader = facebook_profile_from_messages(messages, platform, native_uploader.url or "")
            except ProviderError:
                pass

        return Post(platform=platform, items=items, uploader=uploader)

    def extract(
        self, url: str, platform: str, media_types: list[str] | None = None, known: Mapping[str, Result] | None = None
    ) -> Result:
        avatar_requested = media_types is None or "avatar" in media_types
        if platform == "facebook" and (profile := facebook_url.profile_id(url)):
            # INFO: gallery-dl's profile extractor queues a whole photo feed; the avatar extractor
            # returns just the profile picture, which is what a profile URL means in content-dl.
            messages = self.fetch_messages(f"https://www.facebook.com/{profile}/avatar")
            return facebook_profile_from_messages(messages, platform, url)

        if platform == "tiktok":
            kind, username = tiktok_url.parse_url(url)
            if kind == "profile" and username:
                # INFO: As on Facebook, the profile extractor queues the whole feed; the avatar
                # extractor returns the account and its picture in one request.
                messages = self.fetch_messages(tiktok_url.avatar_page(username))
                return tiktok_profile_from_messages(messages, platform, username)

        if platform == "x" and (username := x_url.profile_username(url)):
            # INFO: As on TikTok, the profile extractor queues the whole timeline; /photo returns the
            # account and its picture in one request. An account without a picture has no file.
            messages = self.fetch_messages(x_url.avatar_page(username))
            return x_profile_from_messages(messages, platform, username)

        if platform == "facebook" and facebook_url.is_post_url(url):
            return self.facebook_post(platform, known, avatar_requested)

        if platform == "instagram" and self.cookies:
            try:
                page, username = instagram_url.parse_url(url)
            except ProviderError:
                page, username = None, None  # Not a post or profile URL; gallery-dl decides below.

            if page == "profile" and username:
                return self.instagram_profile(username, platform)

        post = result_from_messages(self.fetch_messages(url), platform)
        uploader = post.uploader
        if platform == "instagram" and self.cookies and avatar_requested and uploader and uploader.username:
            try:
                uploader.avatar = self.instagram_profile(uploader.username, platform).avatar
            except ProviderError:
                pass  # The post itself was found; the avatar column's other providers can still try.

        return post
