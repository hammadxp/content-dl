"""Save a result's items to disk: the step after ``extract()``.

Plain files (images, thumbnails, avatars, and videos a provider gives as one URL) are fetched
directly. Stream items (``formats``) go to yt-dlp, which picks the formats, downloads and
merges them with ffmpeg. yt-dlp gets the formats already in the result, so nothing is
extracted twice; stream URLs expire within hours, so download soon after extracting.
"""

from __future__ import annotations

import copy
import mimetypes
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from content_dl.embed import EmbedError, embed, tags_for
from content_dl.extract import normalise_want
from content_dl.providers.base import Cookies, ProviderError
from content_dl.providers.native import http
from content_dl.providers.ytdlp import SilentLogger, cookie_options, error_message, host_options
from content_dl.schema import Item, MediaType, Post, Profile, Result
from content_dl.template import DEFAULT_TEMPLATE, render

Owner = Post | Profile
Status = Literal["saved", "exists", "failed"]

# Audio items hold only audio-only formats, so the best of them is the right pick.
AUDIO_FORMAT = "ba/b"


@dataclass
class Download:
    """What happened to one item. ``warning`` is set when the file saved but its metadata didn't."""

    item: Item
    owner: Owner
    status: Status
    path: Path | None = None
    error: str | None = None
    warning: str | None = None


def parse_max_quality(value: str) -> int:
    """The pixel count of a ``--max-quality`` value, measured on the video's shorter side.

    >>> parse_max_quality("1080")
    1080
    >>> parse_max_quality("720p")
    720
    """
    digits = value.strip().lower().removesuffix("p")
    if not digits.isdigit() or int(digits) <= 0:
        raise ValueError(f"--max-quality expects a resolution such as 1080 or 720p, not {value!r}")

    return int(digits)


@dataclass
class _Run:
    output: str
    format: str | None
    max_quality: int | None
    embed_metadata: bool
    cookies: Cookies | None
    used: set[Path] = field(default_factory=set)
    jar: Any = None


def _avatar_with_owner(result: Result) -> tuple[Item, Owner] | None:
    if isinstance(result, Profile):
        return (result.avatar, result) if result.avatar else None

    uploader = result.uploader
    return (uploader.avatar, uploader) if uploader and uploader.avatar else None


ItemPick = tuple[MediaType, int | None]


def parse_item_picks(spec: str) -> list[ItemPick]:
    """Parse ``--items``: ``MEDIA_TYPE[:INDEX]``, comma separated.

    A media type without an index means every item of that media type.

    >>> parse_item_picks("image:1, video:2,avatar")
    [('image', 1), ('video', 2), ('avatar', None)]
    >>> parse_item_picks("images")
    [('image', None)]
    """
    picks: list[ItemPick] = []
    for part in spec.split(","):
        name, _, index = part.strip().partition(":")
        if not name:
            continue

        media_types = normalise_want([name]) or []
        if not index.strip():
            picks.append((media_types[0], None))
            continue

        try:
            picks.append((media_types[0], int(index)))
        except ValueError:
            raise ValueError(f"item index must be a number, got {part.strip()!r}") from None

    if not picks:
        raise ValueError("--items needs at least one MEDIA_TYPE[:INDEX]")

    return picks


def _picked(item: Item, picks: list[ItemPick]) -> bool:
    return any(item.media_type == media_type and (index is None or item.index == index) for media_type, index in picks)


def select_items(
    result: Result, want: Iterable[str] | None = None, picks: list[ItemPick] | None = None
) -> list[tuple[Item, Owner]]:
    """Pick the items to save, each with the object whose metadata it carries.

    With ``picks`` (from ``--items``), exactly those items, in the result's order. With
    ``want``, every item of those media_types (and the avatar, if asked). Without either, the
    defaults: every video and image, audio only where its post has no video (the video's
    download already includes the audio), and the avatar of a profile with nothing else.
    """
    media_types = normalise_want(want)
    avatar = _avatar_with_owner(result)

    if picks is not None:
        chosen = [(item, result) for item in result.items if _picked(item, picks)]
        if avatar and _picked(avatar[0], picks):
            chosen.append(avatar)

        return chosen

    if media_types is not None:
        chosen = [(item, result) for item in result.items if item.media_type in media_types]
        if "avatar" in media_types and avatar:
            chosen.append(avatar)

        return chosen

    video_indexes = {item.index for item in result.items if item.media_type == "video"}
    chosen = [
        (item, result)
        for item in result.items
        if item.media_type in ("video", "image") or (item.media_type == "audio" and item.index not in video_indexes)
    ]
    if not chosen and isinstance(result, Profile) and avatar:
        chosen.append(avatar)

    return chosen


def template_context(result: Result, item: Item) -> dict[str, Any]:
    """The fields an output template can use: the result's, then the item's.

    The item's own ``url`` is ``item.url``, since ``url`` is the post's. A top-level profile is
    also its own ``uploader``, so one template works for post and profile URLs alike.
    """
    context = result.to_dict()
    context.pop("items", None)
    if isinstance(result, Profile):
        context["uploader"] = result.to_dict(as_uploader=True)

    item_fields = item.to_dict()
    context.update({key: value for key, value in item_fields.items() if key not in ("url", "formats", "extra")})
    context["item"] = item_fields
    return context


def _unique(path: Path, used: set[Path]) -> Path:
    """Add `` (2)`` and up when two items in one run render the same name."""
    candidate = path
    counter = 2
    while candidate in used:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        counter += 1

    used.add(candidate)
    return candidate


def _ext_from_type(content_type: str | None) -> str | None:
    """
    >>> _ext_from_type("image/jpeg")
    'jpg'
    >>> _ext_from_type(None) is None
    True
    """
    if not content_type:
        return None

    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
    return guessed.lstrip(".") if guessed else None


def _site_cookies(run: _Run, url: str) -> dict[str, str]:
    if not run.cookies:
        return {}

    if run.jar is None:
        run.jar = http.load_cookie_jar(run.cookies)

    host = urlparse(url).hostname or ""
    return http.cookies_for(run.jar, ".".join(host.split(".")[-2:]))


# INFO: What a browser sends when it loads the file into a page rather than navigating to it. Some
# hosts answer a navigation-style request with an HTML viewer instead of the file: i.redd.it
# redirects it to reddit.com/media, which is a bot-check page logged out.
ACCEPT_BY_MEDIA_TYPE = {
    "image": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "thumbnail": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "avatar": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "video": "video/*,*/*;q=0.8",
    "audio": "audio/*,*/*;q=0.8",
}


def _save_file(run: _Run, result: Result, item: Item, owner: Owner) -> Download:
    assert item.url is not None
    context = template_context(result, item)
    path = None
    if item.ext:
        path = _unique(render(run.output, context), run.used)
        if path.exists():
            return Download(item=item, owner=owner, status="exists", path=path)

    headers = {"Accept": ACCEPT_BY_MEDIA_TYPE[item.media_type]} if item.media_type in ACCEPT_BY_MEDIA_TYPE else None
    with http.stream(item.url, headers=headers, cookies=_site_cookies(run, item.url)) as response:
        if (response.content_type or "").split(";")[0].strip().lower() == "text/html":
            raise ProviderError(f"{urlparse(item.url).hostname} answered with a web page instead of the file")

        if path is None:
            context["ext"] = _ext_from_type(response.content_type) or "bin"
            path = _unique(render(run.output, context), run.used)
            if path.exists():
                return Download(item=item, owner=owner, status="exists", path=path)

        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        try:
            with part.open("wb") as file:
                for chunk in response.chunks:
                    file.write(chunk)

            part.replace(path)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    return Download(item=item, owner=owner, status="saved", path=path)


def _stream_formats(result: Result, item: Item) -> list[dict[str, Any]]:
    """A video's formats plus the audio formats at its index, so yt-dlp can merge the best pair.

    INFO: Segmented DASH formats need their fragment list, which a result doesn't keep (it
    would multiply the JSON's size), so they're left out; yt-dlp picks from the rest.
    """
    formats = list(item.formats or [])
    if item.media_type == "video":
        for other in result.items:
            if other.media_type == "audio" and other.index == item.index and other.provider == item.provider:
                formats += other.formats or []

    usable = [fmt for fmt in formats if fmt.get("url") and not str(fmt.get("protocol", "")).startswith("http_dash")]
    return copy.deepcopy(usable)


def _ytdlp_template(path: Path, ext: str | None) -> str:
    """yt-dlp's outtmpl for an already rendered path: its own ``%(ext)s``, every other % escaped."""
    name = path.name
    if ext and name.endswith("." + ext):
        name = name[: -len(ext) - 1]

    return str(path.with_name(name.replace("%", "%%") + ".%(ext)s"))


def _save_stream(run: _Run, result: Result, item: Item, owner: Owner) -> Download:
    import yt_dlp

    formats = _stream_formats(result, item)
    if not formats:
        raise ProviderError("no downloadable stream in this item's formats")

    path = _unique(render(run.output, template_context(result, item)), run.used)
    path.parent.mkdir(parents=True, exist_ok=True)

    info = {
        "id": result.id or "content",
        # yt-dlp only uses the title in its messages; the file name comes from the content-dl template.
        "title": getattr(result, "title", None) or result.id or "content",
        "extractor": "content-dl",
        "extractor_key": "ContentDl",
        "webpage_url": result.url,
        "duration": item.duration,
        "formats": formats,
    }
    options = {
        **host_options(),
        **cookie_options(run.cookies),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": SilentLogger(),
        "outtmpl": {"default": _ytdlp_template(path, item.ext)},
    }
    chosen_format = run.format if item.media_type == "video" else AUDIO_FORMAT
    if chosen_format:
        options["format"] = chosen_format

    if run.max_quality and item.media_type == "video":
        # INFO: yt-dlp's "res" sort field is the shorter side, so 1080 means 1920x1080 landscape and
        # 1080x1920 vertical alike. Sorting prefers the largest format at or under the limit and
        # falls back to the smallest one above it, so a video is never skipped for lacking a match.
        options["format_sort"] = [f"res:{run.max_quality}"]

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            done = ydl.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as error:
        raise ProviderError(error_message(error)) from error

    downloads = (done or {}).get("requested_downloads") or []
    saved = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
    if saved is None or not saved.exists():
        raise ProviderError("yt-dlp didn't save a file")

    run.used.add(saved)
    return Download(item=item, owner=owner, status="saved", path=saved)


def _save(run: _Run, result: Result, item: Item, owner: Owner) -> Download:
    if item.formats:
        saved = _save_stream(run, result, item, owner)
    elif item.url:
        saved = _save_file(run, result, item, owner)
    else:
        return Download(item=item, owner=owner, status="failed", error="no file or stream URL to download")

    if run.embed_metadata and saved.status == "saved" and saved.path:
        try:
            embed(saved.path, tags_for(owner), item.media_type)
        except (EmbedError, OSError, ValueError) as error:
            saved.warning = f"metadata not embedded: {error}"

    return saved


def download(
    result: Result,
    want: Iterable[str] | None = None,
    output: str = DEFAULT_TEMPLATE,
    format: str | None = None,
    embed_metadata: bool = False,
    cookies: Cookies | None = None,
    on_item: Callable[[Download], None] | None = None,
    picks: list[ItemPick] | None = None,
    max_quality: int | None = None,
) -> list[Download]:
    """Save the items of an extracted result and report what happened to each.

    ``want`` picks media types as ``extract()`` does, ``picks`` names single items (see
    ``parse_item_picks``), and without either the defaults of ``select_items`` apply.
    ``output`` is a yt-dlp style file name template (see ``content_dl.template``). ``format`` is a yt-dlp format selection for video items, and
    ``max_quality`` caps their shorter side in pixels (1080 for 1080p, landscape or vertical).
    ``embed_metadata`` writes the post's (or, for an avatar, the profile's) metadata into each
    file. One failed item doesn't stop the others; ``on_item`` is called as each one finishes.

    For the best video quality, extract with audio included: a video item's download merges
    in the audio formats at the same index.
    """
    run = _Run(
        output=output, format=format, max_quality=max_quality, embed_metadata=embed_metadata, cookies=cookies
    )
    downloads: list[Download] = []
    for item, owner in select_items(result, want, picks):
        try:
            outcome = _save(run, result, item, owner)
        except (ProviderError, OSError, ValueError) as error:
            outcome = Download(item=item, owner=owner, status="failed", error=str(error))

        downloads.append(outcome)
        if on_item:
            on_item(outcome)

    return downloads


def media_types_to_extract(want: Iterable[str] | None) -> list[MediaType] | None:
    """The media types to ask ``extract()`` for so the downloader can save ``want``.

    A video needs its audio formats to merge the best pair, even when only videos are saved.

    >>> media_types_to_extract(["videos"])
    ['video', 'audio']
    >>> media_types_to_extract(None) is None
    True
    """
    media_types = normalise_want(want)
    if media_types is not None and "video" in media_types and "audio" not in media_types:
        media_types.append("audio")

    return media_types
