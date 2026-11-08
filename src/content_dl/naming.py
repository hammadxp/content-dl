"""The readable parts of the default file name: ``title or caption (uploader)-number.ext``.

These become the ``display_title``, ``display_uploader`` and ``number`` template fields, so a
custom ``--filename-template`` can use them too.

INFO: The title vs caption choice. YouTube, Reddit and Pinterest have real titles. Instagram,
Threads, X, TikTok and Facebook don't, and yt-dlp makes one up from the caption: X's is
"Uploader - first 72 characters...", TikTok's is the caption cut at 72. A title that is only a
copy of the caption loses to the caption itself, which is complete and has no prefix.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from content_dl.schema import Item, Profile, Result

MAX_TITLE_CHARS = 60

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
    >>> _is_copy_of_caption("Haroon - sunset at the beach with...", caption)
    True
    >>> _is_copy_of_caption("Me at the zoo", "The first video on YouTube")
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
    >>> _first_line("a day with @haroon at the beach")
    'a day with @haroon at the beach'
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
    """Cut at a word boundary so a long caption doesn't end mid-word.

    >>> shorten("a very long caption about the trip we took to the northern mountains last summer")
    'a very long caption about the trip we took to the northern'
    >>> shorten("supercalifragilisticexpialidocious" * 2, limit=10)
    'supercalif'
    """
    text = text.strip()
    if len(text) <= limit:
        return text.rstrip(TRAILING) or text

    cut = text[: limit + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > limit // 2 else text[:limit]
    return cut.rstrip(TRAILING) or cut


def display_title(result: Result, item: Item) -> str | None:
    """The post's title, else its caption's first line, else its id. An avatar is called "avatar".

    >>> from content_dl.schema import Post
    >>> image = Item(index=1, media_type="image")
    >>> display_title(Post(platform="x", id="1", title="Haroon - sunset at...", description="sunset at the beach"), image)
    'sunset at the beach'
    >>> display_title(Post(platform="youtube", id="jNQ", title="Me at the zoo", description="The first video"), image)
    'Me at the zoo'
    >>> display_title(Post(platform="instagram", id="C9x"), image)
    'C9x'
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

    return result.id


def display_uploader(result: Result) -> str | None:
    """The uploader's display name, else their username.

    A display name made only of symbols (TikTok allows "⃟") says nothing, so the username wins.

    >>> from content_dl.schema import Post
    >>> display_uploader(Post(platform="x", uploader=Profile(platform="x", username="haroon")))
    'haroon'
    >>> display_uploader(Profile(platform="x", name="Haroon J", username="haroon"))
    'Haroon J'
    >>> display_uploader(Profile(platform="tiktok", name="⃟", username="drillyty"))
    'drillyty'
    """
    uploader = result if isinstance(result, Profile) else result.uploader
    if uploader is None:
        return None

    name = _flat(uploader.name or "")
    if not any(char.isalnum() for char in name):
        name = ""

    return name or uploader.username or None


def item_number(items: Sequence[Item], item: Item) -> str | None:
    """The item's zero-padded position, only when the post holds more than one media position.

    A lone item has no number, so a single photo is ``sunset (haroon).jpg``. An index above 1 is
    always numbered, even when the result was cut down to that one item (``--media-types``), so
    the file keeps the name a full download would give it.

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
