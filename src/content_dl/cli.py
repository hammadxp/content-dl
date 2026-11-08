"""content-dl command line.

INFO: Flag names are content-dl's own and spelled out, so they aren't mistaken for yt-dlp's flags.
A flag whose value is yt-dlp syntax says so in its name (``--ytdlp-format``).

Without ``--json-only`` or ``--list-only`` the URL's items are downloaded: the defaults (every
video and image), or the media types ``--media-types`` names, saved under ``--filename-template`` with
``--embed-metadata`` if asked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from content_dl import __version__
from content_dl.download import Download, ItemPick, download, media_types_to_extract, parse_item_picks, parse_max_quality
from content_dl.extract import ExtractionError, extract, normalise_media_types
from content_dl.providers.base import Cookies
from content_dl.providers.ytdlp import FFMPEG_LOCATION_ENV, JS_RUNTIMES_ENV
from content_dl.router import ROUTES_ENV, RouterError
from content_dl.schema import Item, Result, SchemaError, result_from_json, result_to_json
from content_dl.template import DEFAULT_TEMPLATE


def format_duration(seconds: float | None) -> str:
    """Format seconds the way yt-dlp does.

    >>> format_duration(19)
    '0:19'
    >>> format_duration(3725.4)
    '1:02:05'
    >>> format_duration(None)
    ''
    """
    if seconds is None:
        return ""

    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_size(size: float | None) -> str:
    """Format a byte count with binary units.

    >>> format_size(1536)
    '1.50KiB'
    >>> format_size(None)
    ''
    """
    if size is None:
        return ""

    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.2f}{unit}"

        value /= 1024

    return ""


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]
    lines = [headers, *rows]
    return "\n".join("  ".join(str(cell).ljust(width) for cell, width in zip(line, widths)).rstrip() for line in lines)


def _resolution(item: Item) -> str:
    return f"{item.width}x{item.height}" if item.width and item.height else ""


def _all_items(result: Result) -> list[Item]:
    avatar = result.avatar if hasattr(result, "avatar") else getattr(result.uploader, "avatar", None)
    return [*result.items, *([avatar] if avatar else [])]


def render_listing(result: Result) -> str:
    """The ``--list-only`` view: every item, then each stream item's formats."""
    items = _all_items(result)
    # A profile is known by its username or name; its id is a number nobody recognises.
    label = getattr(result, "username", None) or getattr(result, "name", None) or result.id or result.url or ""
    sections = [f"[{result.platform}] {label}: {len(items)} item(s)"]

    item_rows = [
        [
            "" if item.index is None else str(item.index),
            item.media_type,
            item.ext or "",
            _resolution(item),
            format_duration(item.duration),
            "" if item.formats is None else str(len(item.formats)),
            item.provider or "",
        ]
        for item in items
    ]
    headers = ["INDEX", "MEDIA TYPE", "EXT", "RESOLUTION", "DURATION", "FORMATS", "PROVIDER"]
    sections.append(render_table(headers, item_rows))

    for item in items:
        if not item.formats:
            continue

        format_rows = [
            [
                str(fmt.get("format_id", "")),
                str(fmt.get("ext", "")),
                str(fmt.get("resolution", "")),
                "" if fmt.get("fps") is None else f"{fmt['fps']:g}",
                str(fmt.get("vcodec", "")),
                str(fmt.get("acodec", "")),
                "" if fmt.get("tbr") is None else f"{fmt['tbr']:.0f}k",
                format_size(fmt.get("filesize") or fmt.get("filesize_approx")),
                str(fmt.get("format_note", "")),
            ]
            for fmt in item.formats
        ]
        heading = f"Formats for item {item.index} ({item.media_type}):"
        table = render_table(["ID", "EXT", "RESOLUTION", "FPS", "VCODEC", "ACODEC", "TBR", "SIZE", "NOTE"], format_rows)
        sections.append(f"{heading}\n{table}")

    return "\n\n".join(sections)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content-dl",
        description="Extract every video, audio track, image, thumbnail and avatar from one URL.",
    )
    parser.add_argument("url", nargs="?", help="a post or profile URL")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json-only", action="store_true", help="print the full result as JSON and download nothing"
    )
    output.add_argument(
        "--list-only", action="store_true", help="list available items and video formats and download nothing"
    )

    parser.add_argument(
        "--media-types", metavar="TYPES", help="only these media types, comma separated (images,avatar)"
    )
    parser.add_argument(
        "--from-json",
        metavar="FILE",
        help="read the result from a file --json-only wrote instead of extracting a URL"
        " (download soon: stream URLs expire)",
    )
    parser.add_argument("--provider", metavar="NAME", help="force one provider, for debugging")

    routing = parser.add_argument_group("routing")
    routing.add_argument(
        "--routes",
        metavar="FILE",
        help=f"JSON file that overrides the routing table (default: {ROUTES_ENV}, then content-dl/routes.json"
        " in the user config folder)",
    )

    files = parser.add_argument_group("download")
    files.add_argument(
        "--items",
        metavar="MEDIA_TYPE[:INDEX],...",
        help="download only these items, such as image:1,video:2,avatar"
        " (a media type without an index means all of it)",
    )
    files.add_argument(
        "--report-json",
        action="store_true",
        help="report each finished item as one JSON line (media_type, index, status, path, error, warning)",
    )
    files.add_argument(
        "--max-quality",
        metavar="RESOLUTION",
        help="highest video quality, such as 1080 or 720p, measured on the shorter side so vertical videos"
        " count too; takes the next best quality when a video has none at or under it",
    )
    files.add_argument(
        "--ytdlp-format",
        metavar="FORMAT",
        help='yt-dlp format selection syntax for video items, passed to yt-dlp as-is, such as "bv*+ba/b"',
    )
    files.add_argument(
        "--filename-template",
        metavar="TEMPLATE",
        default=DEFAULT_TEMPLATE,
        help="file name template, a folder path can go in front; nested fields with dots, fallbacks with commas"
        " (default: %(default)s)",
    )
    files.add_argument(
        "--embed-metadata",
        action="store_true",
        help="write metadata into the files (ffmpeg tags for video and audio, EXIF and XMP for images)",
    )

    host = parser.add_argument_group("host", "passed to every yt-dlp call, with the same meaning as yt-dlp's flags")
    host.add_argument(
        "--js-runtimes",
        metavar="RUNTIME[:PATH]",
        help=f"JavaScript runtimes for yt-dlp, comma separated, such as quickjs:/path/to/qjs"
        f" (default: {JS_RUNTIMES_ENV})",
    )
    host.add_argument(
        "--ffmpeg-location", metavar="PATH", help=f"ffmpeg binary or its folder (default: {FFMPEG_LOCATION_ENV})"
    )

    auth = parser.add_argument_group(
        "cookies",
        "optional; used by every provider and preferred routes when given. Heavy use with your cookies can get"
        " your account rate-limited or flagged",
    )
    auth.add_argument("--cookies", metavar="FILE", help="Netscape cookies.txt file to read cookies from")
    auth.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="read cookies from a browser's cookie store (chrome, edge, firefox, safari, ...)",
    )
    return parser


def _write(text: str) -> None:
    # INFO: Windows consoles default to a legacy code page; write UTF-8 so captions with emoji don't crash.
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


def _report_json(outcome: Download) -> None:
    fields = {
        "media_type": outcome.item.media_type,
        "index": outcome.item.index,
        "status": outcome.status,
        "path": str(outcome.path) if outcome.path else None,
        "error": outcome.error,
        "warning": outcome.warning,
    }
    _write(json.dumps(fields, ensure_ascii=False))


def _report(outcome: Download) -> None:
    label = outcome.item.media_type if outcome.item.index is None else f"{outcome.item.media_type} {outcome.item.index}"
    if outcome.status == "failed":
        print(f"content-dl: error: {label}: {outcome.error}", file=sys.stderr)
        return

    note = " (already downloaded)" if outcome.status == "exists" else ""
    _write(f"[download] {label}: {outcome.path}{note}")
    if outcome.warning:
        print(f"content-dl: warning: {label}: {outcome.warning}", file=sys.stderr)


def _requested_but_missing(result: Result, media_types: list[str] | None, picks: list[ItemPick] | None) -> list[str]:
    """Labels of the media types or items asked for that the result doesn't have, such as "avatar"."""
    items = _all_items(result)
    if picks:
        return [
            media_type if index is None else f"{media_type} {index}"
            for media_type, index in picks
            if not any(item.media_type == media_type and (index is None or item.index == index) for item in items)
        ]

    if media_types:
        found = {item.media_type for item in items}
        return [media_type for media_type in media_types if media_type not in found]

    return []


def missing_message(
    result: Result,
    media_types: list[str] | None,
    picks: list[ItemPick] | None,
    has_cookies: bool,
    nothing_saved: bool,
) -> str | None:
    """Say what was asked for but not found, why when the reason is known, and what to try next.

    ``None`` when everything asked for was found.

    >>> from content_dl.schema import Post
    >>> post = Post(platform="instagram", items=[Item(index=1, media_type="image", url="https://e.com/1.jpg")])
    >>> print(missing_message(post, ["avatar"], None, has_cookies=False, nothing_saved=True))
    ... # doctest: +NORMALIZE_WHITESPACE
    no avatar found in this post. Instagram only shares profile pictures with a logged-in session: add
    --cookies FILE or --cookies-from-browser BROWSER. Run with --list-only to see everything that was found
    >>> missing_message(post, ["image"], None, has_cookies=False, nothing_saved=False) is None
    True
    """
    missing = _requested_but_missing(result, media_types, picks)
    if not missing and nothing_saved and not (media_types or picks):
        # The defaults pick videos and images (or a profile's avatar), so none of those was found.
        missing = ["avatar"] if hasattr(result, "avatar") else ["video or image"]

    if not missing:
        return None

    page = "profile" if hasattr(result, "avatar") else "post"
    sentences = [f"no {' or '.join(missing)} found in this {page}"]
    if "avatar" in missing and result.platform == "instagram" and not has_cookies:
        sentences.append(
            "Instagram only shares profile pictures with a logged-in session: add"
            " --cookies FILE or --cookies-from-browser BROWSER"
        )

    sentences.append("Run with --list-only to see everything that was found")
    return ". ".join(sentences)


def extraction_error_message(error: ExtractionError, has_cookies: bool) -> str:
    """The extraction error, with what to try next when the site wants a logged-in session.

    >>> error = ExtractionError("X only shows this post to logged-in accounts", login_required=True)
    >>> print(extraction_error_message(error, has_cookies=False))  # doctest: +NORMALIZE_WHITESPACE
    X only shows this post to logged-in accounts. Add --cookies FILE or --cookies-from-browser BROWSER
    from a logged-in session
    >>> print(extraction_error_message(error, has_cookies=True))  # doctest: +NORMALIZE_WHITESPACE
    X only shows this post to logged-in accounts. The cookies given aren't logged in, or have expired:
    export them again
    """
    if not error.login_required:
        return str(error)

    if has_cookies:
        return f"{error}. The cookies given aren't logged in, or have expired: export them again"

    return f"{error}. Add --cookies FILE or --cookies-from-browser BROWSER from a logged-in session"


def _load_result(path: str) -> Result:
    """A result saved with --json-only, so a host that already extracted and picked items doesn't extract twice."""
    with open(path, encoding="utf-8") as file:
        return result_from_json(file.read())


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    downloading = not (args.json_only or args.list_only)
    # INFO: The yt-dlp provider and the downloader read these from the environment (see
    # host_options), so the flags only need to set it for this process.
    if args.js_runtimes:
        os.environ[JS_RUNTIMES_ENV] = args.js_runtimes

    if args.ffmpeg_location:
        os.environ[FFMPEG_LOCATION_ENV] = args.ffmpeg_location

    if (args.url is None) == (args.from_json is None):
        print("content-dl: error: pass either a URL or --from-json FILE", file=sys.stderr)
        return 2

    try:
        media_types = normalise_media_types(args.media_types.split(",")) if args.media_types else None
        picks = parse_item_picks(args.items) if args.items else None
        max_quality = parse_max_quality(args.max_quality) if args.max_quality else None
        cookies = Cookies(file=args.cookies, browser=args.cookies_from_browser)
        extract_media_types = media_types_to_extract(media_types) if downloading else media_types
        if args.from_json:
            result = _load_result(args.from_json)
        else:
            result = extract(
                args.url,
                media_types=extract_media_types,
                provider=args.provider,
                cookies=cookies or None,
                routes=args.routes,
            )
    except ExtractionError as error:
        print(f"content-dl: error: {extraction_error_message(error, has_cookies=bool(cookies))}", file=sys.stderr)
        return 1
    except (RouterError, SchemaError, OSError, ValueError) as error:
        print(f"content-dl: error: {error}", file=sys.stderr)
        return 1

    if args.json_only:
        _write(result_to_json(result))
        return 0

    if args.list_only:
        _write(render_listing(result))
        return 0

    outcomes = download(
        result,
        media_types=media_types,
        output=args.filename_template,
        format=args.ytdlp_format,
        max_quality=max_quality,
        embed_metadata=args.embed_metadata,
        cookies=cookies or None,
        on_item=_report_json if args.report_json else _report,
        picks=picks,
    )

    missing = missing_message(result, media_types, picks, has_cookies=bool(cookies), nothing_saved=not outcomes)
    if not outcomes:
        print(f"content-dl: error: {missing or 'nothing to download'}", file=sys.stderr)
        return 1

    if missing:
        print(f"content-dl: warning: {missing}", file=sys.stderr)

    return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
