"""Write a result's metadata into downloaded files (``--embed-metadata``).

Images get EXIF and XMP, written byte for byte without re-encoding the picture:

- JPEG: an EXIF APP1 segment and an XMP APP1 segment.
- PNG: an ``eXIf`` chunk and an ``iTXt`` XMP chunk.
- WebP: ``EXIF`` and ``XMP `` chunks (a simple WebP is converted to the extended VP8X layout).

XMP carries the full Unicode caption. EXIF's text fields are ASCII only, so EXIF gets the
description and creator only when they are plain ASCII, and the date only when it has a time
of day (a day-only date never gets a made-up midnight). A file that already has an EXIF or
XMP block keeps it, and only the missing block is added.

Video and audio get container tags through ffmpeg (``-c copy``, so nothing is re-encoded).
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from content_dl.providers.ytdlp import FFMPEG_LOCATION_ENV
from content_dl.schema import Post, Profile


class EmbedError(RuntimeError):
    """The metadata couldn't be written. The downloaded file itself is fine."""


@dataclass(frozen=True)
class Tags:
    """The metadata embedded in one file, taken from the object the file belongs to."""

    title: str | None = None
    description: str | None = None
    creator: str | None = None
    date: str | None = None
    source: str | None = None

    def __bool__(self) -> bool:
        return any((self.title, self.description, self.creator, self.date, self.source))


def tags_for(owner: Post | Profile) -> Tags:
    """A post's files carry the post's metadata; an avatar carries its profile's.

    >>> profile = Profile(platform="instagram", username="someone", url="https://www.instagram.com/someone/")
    >>> tags_for(profile)
    Tags(title=None, description=None, creator='someone', date=None, source='https://www.instagram.com/someone/')
    """
    if isinstance(owner, Profile):
        return Tags(creator=owner.name or owner.username, source=owner.url)

    uploader = owner.uploader
    creator = (uploader.name or uploader.username) if uploader else None
    return Tags(
        title=owner.title,
        description=owner.description,
        creator=creator,
        date=owner.upload_date,
        source=owner.url,
    )


def embed(path: Path, tags: Tags, kind: str) -> None:
    """Embed ``tags`` into the file at ``path``. Raises EmbedError for an unsupported file."""
    if not tags:
        return

    if kind in ("video", "audio"):
        embed_media_tags(path, tags)
    else:
        embed_image_tags(path, tags)


# Images


def _xml_text(value: str) -> str:
    # XML 1.0 forbids most control characters, which can turn up in captions.
    cleaned = "".join(char for char in value if char in "\t\n\r" or ord(char) >= 0x20)
    return escape(cleaned)


def build_xmp(tags: Tags) -> bytes:
    """An XMP packet with Dublin Core title, description, creator and source, and the date."""
    fields: list[str] = []
    for name, value in (("title", tags.title), ("description", tags.description)):
        if value:
            alternative = f'<rdf:Alt><rdf:li xml:lang="x-default">{_xml_text(value)}</rdf:li></rdf:Alt>'
            fields.append(f"<dc:{name}>{alternative}</dc:{name}>")

    if tags.creator:
        fields.append(f"<dc:creator><rdf:Seq><rdf:li>{_xml_text(tags.creator)}</rdf:li></rdf:Seq></dc:creator>")

    if tags.source:
        fields.append(f"<dc:source>{_xml_text(tags.source)}</dc:source>")

    if tags.date:
        fields.append(f"<xmp:CreateDate>{tags.date}</xmp:CreateDate>")
        fields.append(f"<photoshop:DateCreated>{tags.date}</photoshop:DateCreated>")

    body = "".join(fields)
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">'
        f"{body}</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


EXIF_ASCII = 2
EXIF_LONG = 4
TAG_IMAGE_DESCRIPTION = 0x010E
TAG_ARTIST = 0x013B
TAG_EXIF_IFD = 0x8769
TAG_DATE_TIME_ORIGINAL = 0x9003
TAG_OFFSET_TIME_ORIGINAL = 0x9011


def _ascii(value: str | None) -> bytes | None:
    if not value or not value.isascii():
        return None

    return value.encode("ascii") + b"\0"


def _exif_date(value: str | None) -> bytes | None:
    """EXIF's ``YYYY:MM:DD HH:MM:SS``, or None for a day-only date.

    >>> _exif_date("2026-10-30T18:22:00Z")
    b'2026:10:30 18:22:00\\x00'
    >>> _exif_date("2005-04-24") is None
    True
    """
    if not value or "T" not in value:
        return None

    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.strftime("%Y:%m:%d %H:%M:%S").encode("ascii") + b"\0"


def _ifd(entries: list[tuple[int, int, bytes]], offset: int) -> bytes:
    """One TIFF image file directory at ``offset``, its long values placed right after it."""
    entries = sorted(entries)
    data_offset = offset + 2 + 12 * len(entries) + 4
    head = struct.pack(">H", len(entries))
    data = b""
    for tag, kind, payload in entries:
        count = len(payload) // 4 if kind == EXIF_LONG else len(payload)
        if len(payload) <= 4:
            value = payload.ljust(4, b"\0")
        else:
            value = struct.pack(">I", data_offset + len(data))
            data += payload + (b"\0" if len(payload) % 2 else b"")

        head += struct.pack(">HHI", tag, kind, count) + value

    return head + struct.pack(">I", 0) + data


def build_exif(tags: Tags) -> bytes | None:
    """A big-endian TIFF block with ImageDescription, Artist and DateTimeOriginal, or None."""
    main: list[tuple[int, int, bytes]] = []
    if description := _ascii(tags.description):
        main.append((TAG_IMAGE_DESCRIPTION, EXIF_ASCII, description))

    if artist := _ascii(tags.creator):
        main.append((TAG_ARTIST, EXIF_ASCII, artist))

    exif: list[tuple[int, int, bytes]] = []
    if taken := _exif_date(tags.date):
        # Upload dates are UTC, so the offset is always +00:00.
        exif = [(TAG_DATE_TIME_ORIGINAL, EXIF_ASCII, taken), (TAG_OFFSET_TIME_ORIGINAL, EXIF_ASCII, b"+00:00\0")]

    if not main and not exif:
        return None

    header = b"MM\x00\x2a" + struct.pack(">I", 8)
    if exif:
        placeholder = len(_ifd([*main, (TAG_EXIF_IFD, EXIF_LONG, b"\0\0\0\0")], 8))
        main.append((TAG_EXIF_IFD, EXIF_LONG, struct.pack(">I", 8 + placeholder)))

    first = _ifd(main, 8)
    return header + first + (_ifd(exif, 8 + len(first)) if exif else b"")


JPEG_EXIF = b"Exif\0\0"
JPEG_XMP = b"http://ns.adobe.com/xap/1.0/\0"
MAX_SEGMENT = 0xFFFF - 2


def _jpeg(data: bytes, exif: bytes | None, xmp: bytes) -> bytes:
    if data[:2] != b"\xff\xd8":
        raise EmbedError("not a JPEG file")

    position = 2
    insert_at = 2
    has_exif = has_xmp = False
    # Walk the APPn and COM segments at the start of the file.
    while position + 4 <= len(data) and data[position] == 0xFF:
        marker = data[position + 1]
        if not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            break

        length = struct.unpack(">H", data[position + 2 : position + 4])[0]
        payload = data[position + 4 : position + 2 + length]
        has_exif = has_exif or (marker == 0xE1 and payload.startswith(JPEG_EXIF))
        has_xmp = has_xmp or (marker == 0xE1 and payload.startswith(JPEG_XMP))
        if marker == 0xE0:
            # INFO: JFIF requires its APP0 segment to come first, so new segments go after it.
            insert_at = position + 2 + length

        position += 2 + length

    segments = b""
    for present, prefix, payload in ((has_exif, JPEG_EXIF, exif), (has_xmp, JPEG_XMP, xmp)):
        if present or not payload or len(prefix + payload) > MAX_SEGMENT:
            continue

        segments += b"\xff\xe1" + struct.pack(">H", len(prefix + payload) + 2) + prefix + payload

    return data[:insert_at] + segments + data[insert_at:]


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_XMP_KEYWORD = b"XML:com.adobe.xmp"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def _png(data: bytes, exif: bytes | None, xmp: bytes) -> bytes:
    if not data.startswith(PNG_SIGNATURE):
        raise EmbedError("not a PNG file")

    position = len(PNG_SIGNATURE)
    has_exif = has_xmp = False
    after_header = None
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        payload = data[position + 8 : position + 8 + length]
        has_exif = has_exif or kind == b"eXIf"
        has_xmp = has_xmp or (kind == b"iTXt" and payload.startswith(PNG_XMP_KEYWORD + b"\0"))
        position += 12 + length
        if kind == b"IHDR":
            after_header = position

        if kind == b"IEND":
            break

    if after_header is None:
        raise EmbedError("PNG file has no header chunk")

    chunks = b""
    if exif and not has_exif:
        chunks += _png_chunk(b"eXIf", exif)

    if not has_xmp:
        # keyword, then compression flag and method (none), then empty language and translated keyword.
        chunks += _png_chunk(b"iTXt", PNG_XMP_KEYWORD + b"\0\0\0\0\0" + xmp)

    return data[:after_header] + chunks + data[after_header:]


WEBP_FLAG_EXIF = 0x08
WEBP_FLAG_XMP = 0x04
WEBP_FLAG_ALPHA = 0x10


def _riff_chunk(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def _webp_canvas(kind: bytes, payload: bytes) -> tuple[int, int, bool]:
    """Width, height and alpha of a simple (VP8 or VP8L) WebP image."""
    if kind == b"VP8 " and payload[3:6] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", payload[6:10])
        return width & 0x3FFF, height & 0x3FFF, False

    if kind == b"VP8L" and payload[:1] == b"\x2f":
        bits = struct.unpack("<I", payload[1:5])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool(bits >> 28 & 1)

    raise EmbedError("unreadable WebP image data")


def _webp(data: bytes, exif: bytes | None, xmp: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise EmbedError("not a WebP file")

    chunks: list[tuple[bytes, bytes]] = []
    position = 12
    while position + 8 <= len(data):
        kind, length = struct.unpack("<4sI", data[position : position + 8])
        chunks.append((kind, data[position + 8 : position + 8 + length]))
        position += 8 + length + (length % 2)

    if not chunks:
        raise EmbedError("empty WebP file")

    kinds = {kind for kind, _ in chunks}
    if chunks[0][0] != b"VP8X":
        width, height, alpha = _webp_canvas(*chunks[0])
        flags = WEBP_FLAG_ALPHA if alpha else 0
        canvas = struct.pack("<I", width - 1)[:3] + struct.pack("<I", height - 1)[:3]
        chunks.insert(0, (b"VP8X", bytes([flags, 0, 0, 0]) + canvas))

    header = bytearray(chunks[0][1])
    if exif and b"EXIF" not in kinds:
        chunks.append((b"EXIF", exif))
        header[0] |= WEBP_FLAG_EXIF

    if b"XMP " not in kinds:
        chunks.append((b"XMP ", xmp))
        header[0] |= WEBP_FLAG_XMP

    chunks[0] = (b"VP8X", bytes(header))
    body = b"WEBP" + b"".join(_riff_chunk(kind, payload) for kind, payload in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


IMAGE_WRITERS = {b"\xff\xd8": _jpeg, PNG_SIGNATURE[:2]: _png, b"RI": _webp}


def embed_image_tags(path: Path, tags: Tags) -> None:
    data = path.read_bytes()
    writer = IMAGE_WRITERS.get(data[:2])
    if writer is None:
        raise EmbedError(f"can't write metadata into {path.suffix or 'this'} files (JPEG, PNG and WebP only)")

    updated = writer(data, build_exif(tags), build_xmp(tags))
    if updated != data:
        _replace(path, updated)


def _replace(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".temp")
    temp.write_bytes(data)
    os.replace(temp, path)


# Video and audio


def ffmpeg_tags(tags: Tags) -> dict[str, str]:
    """Container tags as ffmpeg names them; ffmpeg maps them per format (``©day`` in MP4).

    >>> ffmpeg_tags(Tags(title="Me at the zoo", date="2005-04-24", source="https://youtu.be/x"))
    {'title': 'Me at the zoo', 'date': '2005-04-24', 'comment': 'https://youtu.be/x', 'purl': 'https://youtu.be/x'}
    """
    pairs = {
        "title": tags.title,
        "description": tags.description,
        "synopsis": tags.description,
        "artist": tags.creator,
        "date": tags.date,
        "comment": tags.source,
        "purl": tags.source,
    }
    return {key: value for key, value in pairs.items() if value}


def find_ffmpeg() -> str | None:
    """The ffmpeg that yt-dlp is told to use (``--ffmpeg-location``), else the one on PATH.

    INFO: On Android, youtubedl-android ships ffmpeg as ``libffmpeg.so``, which isn't on PATH
    under the name ``ffmpeg``, so the location has to be honoured here too.
    """
    location = os.environ.get(FFMPEG_LOCATION_ENV, "").strip()
    if location:
        candidate = Path(location)
        if candidate.is_dir():
            found = shutil.which("ffmpeg", path=str(candidate))
            if found:
                return found
        elif candidate.is_file():
            return str(candidate)

    return shutil.which("ffmpeg")


def embed_media_tags(path: Path, tags: Tags) -> None:
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise EmbedError("ffmpeg isn't installed, so video and audio metadata wasn't written")

    temp = path.with_name(f"{path.stem}.temp{path.suffix}")
    command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(path), "-map", "0", "-dn", "-ignore_unknown", "-c", "copy"]
    for key, value in ffmpeg_tags(tags).items():
        command += ["-metadata", f"{key}={value}"]

    # INFO: Puts the MP4 index at the start, the same as yt-dlp's own metadata step does.
    if path.suffix.lower() in (".mp4", ".m4a", ".mov"):
        command += ["-movflags", "+faststart"]

    command.append(str(temp))
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        temp.unlink(missing_ok=True)
        message = completed.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise EmbedError(f"ffmpeg couldn't write metadata: {message[0]}")

    os.replace(temp, path)
