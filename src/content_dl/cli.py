"""content-dl command line. Flags copy yt-dlp's where they mean the same thing.

Without ``-J`` or ``-F`` the URL's items are downloaded: the defaults (every video and image),
or the kinds ``--want`` names, saved under ``-o`` with ``--embed-metadata`` if asked.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from content_dl import __version__
from content_dl.download import Download, download, kinds_to_extract
from content_dl.extract import ExtractionError, extract, normalise_want
from content_dl.providers.base import Cookies
from content_dl.router import RouterError
from content_dl.schema import Item, Result, result_to_json
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
    """The ``-F`` view: every item, then each stream item's formats."""
    items = _all_items(result)
    label = result.id or result.url or ""
    sections = [f"[{result.platform}] {label}: {len(items)} item(s)"]

    item_rows = [
        [
            "" if item.index is None else str(item.index),
            item.kind,
            item.ext or "",
            _resolution(item),
            format_duration(item.duration),
            "" if item.formats is None else str(len(item.formats)),
            item.provider or "",
        ]
        for item in items
    ]
    sections.append(render_table(["INDEX", "KIND", "EXT", "RESOLUTION", "DURATION", "FORMATS", "PROVIDER"], item_rows))

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
        heading = f"Formats for item {item.index} ({item.kind}):"
        table = render_table(["ID", "EXT", "RESOLUTION", "FPS", "VCODEC", "ACODEC", "TBR", "SIZE", "NOTE"], format_rows)
        sections.append(f"{heading}\n{table}")

    return "\n\n".join(sections)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content-dl",
        description="Extract every video, audio track, image, thumbnail and avatar from one URL.",
    )
    parser.add_argument("url", help="a post or profile URL")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    output = parser.add_mutually_exclusive_group()
    output.add_argument("-J", "--dump-single-json", action="store_true", help="print the full result as JSON")
    output.add_argument("-F", "--list-formats", action="store_true", help="list available items and video formats")

    parser.add_argument("--want", metavar="KINDS", help="only these item kinds, comma separated (images,avatar)")
    parser.add_argument("--provider", metavar="NAME", help="force one provider, for debugging")

    files = parser.add_argument_group("download")
    files.add_argument(
        "-f", "--format", metavar="FORMAT", help='yt-dlp format selection for video items, such as "bv*+ba/b"'
    )
    files.add_argument(
        "-o",
        "--output",
        metavar="TEMPLATE",
        default=DEFAULT_TEMPLATE,
        help="output file name template; nested fields with dots, fallbacks with commas (default: %(default)s)",
    )
    files.add_argument(
        "--embed-metadata",
        action="store_true",
        help="write metadata into the files (ffmpeg tags for video and audio, EXIF and XMP for images)",
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


def _report(outcome: Download) -> None:
    label = outcome.item.kind if outcome.item.index is None else f"{outcome.item.kind} {outcome.item.index}"
    if outcome.status == "failed":
        print(f"content-dl: error: {label}: {outcome.error}", file=sys.stderr)
        return

    note = " (already downloaded)" if outcome.status == "exists" else ""
    _write(f"[download] {label}: {outcome.path}{note}")
    if outcome.warning:
        print(f"content-dl: warning: {label}: {outcome.warning}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    downloading = not (args.dump_single_json or args.list_formats)

    try:
        want = normalise_want(args.want.split(",")) if args.want else None
        cookies = Cookies(file=args.cookies, browser=args.cookies_from_browser)
        extract_want = kinds_to_extract(want) if downloading else want
        result = extract(args.url, want=extract_want, provider=args.provider, cookies=cookies or None)
    except (ExtractionError, RouterError, ValueError) as error:
        print(f"content-dl: error: {error}", file=sys.stderr)
        return 1

    if args.dump_single_json:
        _write(result_to_json(result))
        return 0

    if args.list_formats:
        _write(render_listing(result))
        return 0

    outcomes = download(
        result,
        want=want,
        output=args.output,
        format=args.format,
        embed_metadata=args.embed_metadata,
        cookies=cookies or None,
        on_item=_report,
    )

    if not outcomes:
        print("content-dl: error: nothing to download; try -F to see what was found, or --want", file=sys.stderr)
        return 1

    return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
