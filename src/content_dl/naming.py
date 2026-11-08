"""The readable parts of the default file name: ``title or caption (uploader) (id)-number.ext``.

These become the ``display_title``, ``display_uploader``, ``display_id`` and ``number`` template
fields, so a custom ``--filename-template`` can use them too.

INFO: The title vs caption choice. YouTube, Reddit and Pinterest have real titles. Instagram,
Threads, X, TikTok and Facebook don't, and yt-dlp makes one up from the caption: X's is
"Uploader - first 72 characters...", TikTok's is the caption cut at 72. A title that is only a
copy of the caption loses to the caption itself, which is complete and has no prefix.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from content_dl.schema import Item, Profile, Result
from content_dl.template import render

MAX_TITLE_CHARS = 80
# Marks a title that was cut. Three dots rather than "…", which some file systems and apps mangle.
ELLIPSIS = "..."

# INFO: File system limits. A file name is at most 255 bytes on Linux and macOS (255 UTF-16 units
# on Windows; UTF-8 bytes are never fewer, so counting bytes is safe everywhere). A full path is
# at most 259 characters on Windows unless long paths are turned on, which ffmpeg and many apps
# still don't support, so it is always assumed off. Linux and macOS allow far longer paths.
MAX_NAME_BYTES = 255
MAX_PATH_CHARS = 259 if os.name == "nt" else 1023

# Room for what is added to a name while it downloads or after: yt-dlp's per-format temporary
# files ("name.f251-drc.webm.part", the usual cause of yt-dlp's "file name too long"), its
# ".temp" merge file, and the " audio" content-dl adds when two items would share a name.
NAME_RESERVE = 32

URL = re.compile(r"https?://\S+")
# Hashtags and @mentions at either end of a line; ones inside a sentence are part of it.
EDGE_TAGS = re.compile(r"^(?:[#@][^\s#@]+\s*)+|(?:\s*[#@][^\s#@]+)+$")
SPACES = re.compile(r"\s+")
TRAILING = " .,:;!?-_|·…"


def _flat(text: str) -> str:
    return SPACES.sub(" ", URL.sub(" ", text)).strip()


def _is_copy_of_caption(title: str, caption: str) -> bool:
    """Is ``title`` made from the caption, as yt-dlp does on X and TikTok?

    >>> caption = "sunset at the beach with everyone https://t.co/x"
    >>> _is_copy_of_caption("Some One - sunset at the beach with...", caption)
    True
    >>> _is_copy_of_caption("A walk in the park", "Our first video together")
    False
    """
    flat_caption = _flat(caption).casefold()
    for candidate in (title, title.partition(" - ")[2]):
        core = _flat(candidate).rstrip(TRAILING).casefold()
        if core and core in flat_caption:
            return True

    return False


def _first_line(caption: str) -> str:
    """The caption's first line with words in it, without links or the hashtags around it.

    A line that is all hashtags is kept only when no line has words.

    >>> _first_line("#travel #beach\\n\\nsunset at the beach 🌅 #summer #sun\\nmore text")
    'sunset at the beach 🌅'
    >>> _first_line("a day with @someone at the beach")
    'a day with @someone at the beach'
    >>> _first_line("#travel #beach")
    '#travel #beach'
    """
    lines = [_flat(line) for line in caption.splitlines()]
    lines = [line for line in lines if line]
    for line in lines:
        words = _flat(EDGE_TAGS.sub(" ", line))
        if words:
            return words

    return lines[0] if lines else ""


def shorten(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    """Cut at a word boundary so a long caption doesn't end mid-word, and mark the cut with "...".

    The "..." counts toward ``limit``. Text that already fits is only stripped of trailing
    punctuation, so "..." always means something was cut here.

    >>> shorten("a very long caption about the trip we took to the northern mountains last summer", limit=60)
    'a very long caption about the trip we took to the...'
    >>> shorten("supercalifragilisticexpialidocious" * 2, limit=10)
    'superca...'
    >>> shorten("sunset at the beach!", limit=60)
    'sunset at the beach'
    """
    text = text.strip()
    if len(text) <= limit:
        return text.rstrip(TRAILING) or text

    room = max(limit - len(ELLIPSIS), 1)
    cut = text[: room + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > room // 2 else text[:room]
    return (cut.rstrip(TRAILING) or cut) + ELLIPSIS


def display_title(result: Result, item: Item) -> str | None:
    """The post's title, else its caption's first line. An avatar is called "avatar".

    ``None`` when the post has neither; the default name then starts at the uploader:
    ``(someone) (C9x).jpg``.

    >>> from content_dl.schema import Post
    >>> image = Item(index=1, media_type="image")
    >>> display_title(Post(platform="x", id="1", title="Some One - sunset at...", description="sunset at the beach"), image)
    'sunset at the beach'
    >>> display_title(Post(platform="youtube", id="a1", title="A walk in the park", description="Filmed in May"), image)
    'A walk in the park'
    >>> display_title(Post(platform="instagram", id="C9x"), image) is None
    True
    """
    if item.media_type == "avatar":
        return "avatar"

    title = getattr(result, "title", None)
    caption = getattr(result, "description", None)
    if isinstance(result, Profile):
        # A profile's items are named after the account, not its bio.
        title, caption = result.name or result.username, None

    if title and not (caption and _is_copy_of_caption(title, caption)):
        return shorten(_flat(title)) or None

    if caption and (line := shorten(_first_line(caption))):
        return line

    return None


def display_uploader(result: Result) -> str | None:
    """The uploader's username, else their display name.

    The username comes first because it is unique on the site and never changes looks; a display
    name made only of symbols (TikTok allows "⃟") is skipped.

    >>> from content_dl.schema import Post
    >>> display_uploader(Post(platform="x", uploader=Profile(platform="x", name="Some One", username="someone")))
    'someone'
    >>> display_uploader(Profile(platform="reddit", name="Some One"))
    'Some One'
    >>> display_uploader(Profile(platform="tiktok", name="⃟")) is None
    True
    """
    uploader = result if isinstance(result, Profile) else result.uploader
    if uploader is None:
        return None

    if uploader.username:
        return uploader.username

    name = _flat(uploader.name or "")
    return name if any(char.isalnum() for char in name) else None


def display_id(result: Result, item: Item) -> str | None:
    """The post's id, which keeps two posts with the same caption apart.

    Left out for an avatar, which belongs to the uploader rather than the post (``avatar (someone).jpg``).

    >>> from content_dl.schema import Post
    >>> display_id(Post(platform="instagram", id="C9x"), Item(index=1, media_type="image"))
    'C9x'
    >>> display_id(Post(platform="instagram", id="C9x"), Item(media_type="avatar")) is None
    True
    """
    return None if item.media_type == "avatar" else result.id


def item_number(items: Sequence[Item], item: Item) -> str | None:
    """The item's position in the post, zero-padded, only when the post holds more than one.

    A lone item has no number, so a single photo is ``sunset (someone) (C9x).jpg``. An index above
    1 is always numbered, even when the result was cut down to that one item (``--media-types``),
    so the file keeps the name a full download would give it.

    >>> carousel = [Item(index=i, media_type="image") for i in (1, 2, 3)]
    >>> item_number(carousel, carousel[1])
    '02'
    >>> item_number(carousel[:1], carousel[0]) is None
    True
    >>> item_number([Item(index=7, media_type="video")], Item(index=7, media_type="video"))
    '07'
    """
    if item.index is None or item.media_type == "avatar":
        return None

    indexes = {other.index for other in items if other.index is not None}
    highest = max(indexes | {item.index})
    if len(indexes) < 2 and highest < 2:
        return None

    return str(item.index).zfill(max(2, len(str(highest))))


def path_overshoot(path: Path) -> int:
    """How far a path is over the limits, with room for temporary suffixes; 0 or less when it fits.

    >>> path_overshoot(Path("short.jpg")) <= 0
    True
    >>> path_overshoot(Path("a" * 300 + ".jpg")) > 0
    True
    """
    name_over = len(path.name.encode("utf-8")) + NAME_RESERVE - MAX_NAME_BYTES
    full_over = len(str(Path.cwd() / path)) + NAME_RESERVE - MAX_PATH_CHARS
    return max(name_over, full_over)


def render_fitted(template: str, context: Mapping[str, Any]) -> Path:
    """Render a file name, cutting the title or caption back until the path fits the system's limits.

    The title is the only part cut: the uploader, id, number and extension keep the file
    findable and unique. The cut stays at a word boundary where it can (see ``shorten``), and any
    title that fits is kept, however short. Only when not one character of it fits (a very deep
    folder) is it left out, so the name starts at the uploader, as a post without a title does; if
    that doesn't fit either, the template's own limit applies.
    """
    path = render(template, context)
    title = context.get("display_title")
    if not title or path_overshoot(path) <= 0:
        return path

    def attempt(limit: int) -> Path:
        cut = shorten(title, limit) if limit > 0 else ""
        return render(template, {**context, "display_title": cut or None})

    # The longest title that fits, found by halving: a cut name is never longer than a longer cut.
    low, high, best = 0, len(title) - 1, attempt(0)
    while low <= high:
        middle = (low + high) // 2
        candidate = attempt(middle)
        if path_overshoot(candidate) <= 0:
            best, low = candidate, middle + 1
        else:
            high = middle - 1

    return best
