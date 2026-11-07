"""Milestone 5: the downloader, ``-o`` templates, ``--want`` and ``--embed-metadata``.

Offline: files come from a fake HTTP stream, and yt-dlp's download step is replaced by one
that copies a small local video. The ffmpeg tests make that video with ffmpeg and are skipped
when it isn't installed. Set CONTENT_DL_LIVE=1 to also download a real YouTube video.
"""

import json
import os
import shutil
import struct
import subprocess
import zlib
from contextlib import contextmanager
from pathlib import Path

import pytest
import yt_dlp

from content_dl import Item, Post, Profile, download, select_items
from content_dl.cli import main
from content_dl.download import template_context
from content_dl.embed import Tags, build_exif, build_xmp, embed_image_tags, tags_for
from content_dl.providers import GalleryDlProvider, YtDlpProvider
from content_dl.providers.native import http
from content_dl.template import DEFAULT_TEMPLATE, render

FIXTURES = Path(__file__).parent / "fixtures"
POST_URL = "https://www.instagram.com/p/DAsTXK3JbrA/"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00\x03\x00\xff\xd9"


def png_bytes() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\0\0")) + chunk(b"IEND", b"")


def webp_bytes() -> bytes:
    # A lossless (VP8L) header for a 3x2 image with alpha; the pixel data doesn't matter here.
    bits = (3 - 1) | ((2 - 1) << 14) | (1 << 28)
    payload = b"\x2f" + struct.pack("<I", bits) + b"\0\0\0"
    body = b"WEBP" + b"VP8L" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def jpeg_segments(data: bytes) -> list[bytes]:
    segments, position = [], 2
    while data[position] == 0xFF and data[position + 1] != 0xDB:
        length = struct.unpack(">H", data[position + 2 : position + 4])[0]
        segments.append(data[position + 1 : position + 2 + length])
        position += 2 + length

    return segments


def carousel_post() -> Post:
    uploader = Profile(
        platform="instagram",
        id="123456",
        username="someone",
        name="Some One",
        url="https://www.instagram.com/someone/",
        avatar=Item(kind="avatar", url="https://cdn.example.com/avatar.jpg", ext="jpg", provider="native"),
    )
    video_formats = [{"format_id": "720", "url": "https://cdn.example.com/v.mp4", "ext": "mp4", "vcodec": "avc1"}]
    audio_formats = [{"format_id": "a", "url": "https://cdn.example.com/a.m4a", "ext": "m4a", "vcodec": "none"}]
    return Post(
        platform="instagram",
        id="DAsTXK3JbrA",
        description="sunset at the beach 🌅",
        upload_date="2026-10-30T18:22:00Z",
        url=POST_URL,
        uploader=uploader,
        items=[
            Item(index=1, kind="image", url="https://cdn.example.com/1.jpg?stp=x", ext="jpg", provider="gallery-dl"),
            Item(index=2, kind="video", ext="mp4", formats=video_formats, provider="yt-dlp"),
            Item(index=2, kind="audio", ext="m4a", formats=audio_formats, provider="yt-dlp"),
            Item(index=2, kind="thumbnail", url="https://cdn.example.com/2.jpg", ext="jpg", provider="yt-dlp"),
        ],
    )


@pytest.fixture
def fake_files(monkeypatch):
    """Serve every direct URL as a small JPEG (or PNG for .png) and record the requests."""
    requested: list[str] = []

    @contextmanager
    def fake_stream(url, **kwargs):
        requested.append(url)
        body = png_bytes() if ".png" in url else JPEG
        yield http.Stream(url=url, content_type="image/png" if ".png" in url else "image/jpeg", chunks=iter([body]))

    monkeypatch.setattr(http, "stream", fake_stream)
    return requested


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg isn't installed")

    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    color = "color=c=black:s=32x32:d=0.2"
    command = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", color, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True)
    return path


@pytest.fixture
def fake_ytdlp_download(monkeypatch, tmp_path):
    """Replace yt-dlp's download step: write the chosen file where outtmpl says, record the call."""
    calls: list[dict] = []
    source = {"path": None}

    def fake_process(self, info, download=True):
        calls.append({"format": self.params.get("format"), "formats": [fmt["format_id"] for fmt in info["formats"]]})
        ext = info["formats"][0]["ext"]
        target = Path(self.params["outtmpl"]["default"].replace("%(ext)s", ext).replace("%%", "%"))
        if source["path"]:
            shutil.copy(source["path"], target)
        else:
            target.write_bytes(b"video")

        return {**info, "requested_downloads": [{"filepath": str(target)}]}

    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_ie_result", fake_process)
    return calls, source


# Output templates

def test_default_template_names_every_item_of_a_carousel() -> None:
    post = carousel_post()
    names = [render(DEFAULT_TEMPLATE, template_context(post, item)).as_posix() for item in post.items]

    assert names == [
        "sunset at the beach 🌅 [DAsTXK3JbrA] image 1.jpg",
        "sunset at the beach 🌅 [DAsTXK3JbrA] video 2.mp4",
        "sunset at the beach 🌅 [DAsTXK3JbrA] audio 2.m4a",
        "sunset at the beach 🌅 [DAsTXK3JbrA] thumbnail 2.jpg",
    ]


def test_spec_template_with_nested_fields() -> None:
    post = carousel_post()
    template = "%(platform)s/%(uploader.username)s/%(id)s_%(index)s.%(ext)s"

    assert render(template, template_context(post, post.items[0])) == Path("instagram/someone/DAsTXK3JbrA_1.jpg")


def test_profile_is_its_own_uploader_in_templates() -> None:
    profile = Profile(platform="instagram", id="1", username="someone", avatar=Item(kind="avatar", ext="jpg"))
    template = "%(uploader.username)s/%(title,description,username)s %(kind)s%(index& {})s.%(ext)s"

    assert render(template, template_context(profile, profile.avatar)) == Path("someone/someone avatar.jpg")


def test_template_values_cannot_make_folders_or_bad_names() -> None:
    context = {"title": 'a/b\\c: "d"?\nnext', "ext": "jpg"}

    assert render("%(title)s.%(ext)s", context) == Path("a_b_c_ _d__ next.jpg")


def test_template_formats_defaults_and_missing_fields() -> None:
    context = {"id": "x", "index": 3, "duration": 14.0}

    assert render("%(id)s_%(index)03d_%(duration)d_%(missing)s_%(missing|none)s.%(ext|bin)s", context) == Path(
        "x_003_14_NA_none.bin"
    )
    assert render("100%% %(id)s", context) == Path("100% x")


def test_long_names_are_cut_but_keep_the_extension() -> None:
    name = render("%(title)s.%(ext)s", {"title": "a" * 500, "ext": "jpg"}).name

    assert len(name) == 200 and name.endswith("a.jpg")


# Choosing items

def test_default_selection_is_videos_and_images() -> None:
    post = carousel_post()

    assert [(item.index, item.kind) for item, _ in select_items(post)] == [(1, "image"), (2, "video")]


def test_audio_only_post_downloads_its_audio() -> None:
    post = Post(platform="youtube", items=[Item(index=1, kind="audio", formats=[]), Item(index=1, kind="thumbnail")])

    assert [item.kind for item, _ in select_items(post)] == ["audio"]


def test_want_picks_kinds_and_the_uploaders_avatar() -> None:
    post = carousel_post()
    chosen = select_items(post, ["images", "avatar"])

    assert [item.kind for item, _ in chosen] == ["image", "avatar"]
    assert chosen[0][1] is post and chosen[1][1] is post.uploader


def test_profile_defaults_to_its_avatar() -> None:
    profile = Profile(platform="instagram", avatar=Item(kind="avatar", url="https://cdn.example.com/a.jpg"))

    assert [item.kind for item, _ in select_items(profile)] == ["avatar"]


# Downloading

def test_mixed_post_downloads_with_names(tmp_path, fake_files, fake_ytdlp_download) -> None:
    calls, _ = fake_ytdlp_download
    template = str(tmp_path / "%(uploader.username)s" / "%(id)s_%(index)s_%(kind)s.%(ext)s")
    outcomes = download(carousel_post(), want=["image", "video", "avatar"], output=template)

    assert [outcome.status for outcome in outcomes] == ["saved"] * 3
    assert sorted(path.name for path in (tmp_path / "someone").iterdir()) == [
        "DAsTXK3JbrA_1_image.jpg",
        "DAsTXK3JbrA_2_video.mp4",
        "DAsTXK3JbrA_NA_avatar.jpg",
    ]
    # The query string is part of the request, never of the file name.
    assert fake_files == ["https://cdn.example.com/1.jpg?stp=x", "https://cdn.example.com/avatar.jpg"]
    # The video's audio formats go along, so yt-dlp can merge the best pair.
    assert calls == [{"format": None, "formats": ["720", "a"]}]


def test_format_selection_applies_to_videos_only(tmp_path, fake_ytdlp_download) -> None:
    calls, _ = fake_ytdlp_download
    download(carousel_post(), want=["video", "audio"], output=str(tmp_path / "%(kind)s.%(ext)s"), format="bv*+ba/b")

    assert [call["format"] for call in calls] == ["bv*+ba/b", "ba/b"]


def test_existing_files_are_not_downloaded_again(tmp_path, fake_files) -> None:
    template = str(tmp_path / "%(id)s_%(index)s.%(ext)s")
    first = download(carousel_post(), want=["image"], output=template)
    second = download(carousel_post(), want=["image"], output=template)

    assert (first[0].status, second[0].status) == ("saved", "exists")
    assert len(fake_files) == 1


def test_same_name_in_one_run_gets_a_counter(tmp_path, fake_files) -> None:
    outcomes = download(carousel_post(), want=["image", "thumbnail"], output=str(tmp_path / "%(id)s.%(ext)s"))

    assert [outcome.path.name for outcome in outcomes] == ["DAsTXK3JbrA.jpg", "DAsTXK3JbrA (2).jpg"]


def test_missing_ext_comes_from_the_content_type(tmp_path, fake_files) -> None:
    post = Post(platform="example.com", id="x", items=[Item(index=1, kind="image", url="https://e.com/pic.png")])
    post.items[0].ext = None

    outcomes = download(post, output=str(tmp_path / "%(id)s.%(ext)s"))

    assert outcomes[0].path.name == "x.png"


def test_one_failed_item_does_not_stop_the_others(tmp_path, monkeypatch) -> None:
    @contextmanager
    def flaky(url, **kwargs):
        if "1.jpg" in url:
            raise http.ProviderError("HTTP 403")

        yield http.Stream(url=url, content_type="image/jpeg", chunks=iter([JPEG]))

    monkeypatch.setattr(http, "stream", flaky)
    outcomes = download(carousel_post(), want=["image", "thumbnail"], output=str(tmp_path / "%(kind)s.%(ext)s"))

    assert [outcome.status for outcome in outcomes] == ["failed", "saved"]
    assert "403" in outcomes[0].error
    assert not list(tmp_path.glob("*.part"))


def test_item_without_any_url_fails_clearly(tmp_path) -> None:
    post = Post(platform="instagram", items=[Item(index=1, kind="video", formats=[])])

    assert download(post, output=str(tmp_path / "%(kind)s.%(ext)s"))[0].error == "no file or stream URL to download"


# Embedded metadata: images

def test_post_metadata_and_avatar_metadata_differ() -> None:
    post = carousel_post()

    assert tags_for(post) == Tags(
        description="sunset at the beach 🌅", creator="Some One", date="2026-10-30T18:22:00Z", source=POST_URL
    )
    assert tags_for(post.uploader) == Tags(creator="Some One", source="https://www.instagram.com/someone/")


def test_jpeg_gets_exif_and_xmp_after_jfif(tmp_path) -> None:
    path = tmp_path / "a.jpg"
    path.write_bytes(JPEG)
    embed_image_tags(path, tags_for(carousel_post()))
    segments = jpeg_segments(path.read_bytes())

    assert [segment[:1] for segment in segments] == [b"\xe0", b"\xe1", b"\xe1"]
    exif, xmp = segments[1][3:], segments[2][3:]
    assert exif.startswith(b"Exif\0\0MM") and b"2026:10:30 18:22:00\0" in exif and b"Some One\0" in exif
    # The caption has an emoji, which EXIF's ASCII fields can't hold; XMP carries it.
    assert b"sunset" not in exif
    assert "sunset at the beach 🌅" in xmp.decode("utf-8")
    # The picture itself (here a quantisation table and the end marker) is untouched.
    assert path.read_bytes().startswith(JPEG[:20]) and path.read_bytes().endswith(JPEG[20:])


def test_embedding_twice_does_not_duplicate_blocks(tmp_path) -> None:
    path = tmp_path / "a.jpg"
    path.write_bytes(JPEG)
    embed_image_tags(path, tags_for(carousel_post()))
    once = path.read_bytes()
    embed_image_tags(path, tags_for(carousel_post()))

    assert path.read_bytes() == once


def test_exif_ifds_are_well_formed() -> None:
    exif = build_exif(Tags(description="plain caption", creator="someone", date="2026-10-30T18:22:00Z"))
    first_ifd = struct.unpack(">I", exif[4:8])[0]
    count = struct.unpack(">H", exif[first_ifd : first_ifd + 2])[0]
    entries = [struct.unpack(">HHI4s", exif[first_ifd + 2 + 12 * n : first_ifd + 14 + 12 * n]) for n in range(count)]
    tags = {tag: (count, value) for tag, _, count, value in entries}

    assert sorted(tags) == [0x010E, 0x013B, 0x8769]
    offset = struct.unpack(">I", tags[0x010E][1])[0]
    assert exif[offset : offset + tags[0x010E][0]] == b"plain caption\0"
    exif_ifd = struct.unpack(">I", tags[0x8769][1])[0]
    assert struct.unpack(">H", exif[exif_ifd : exif_ifd + 2])[0] == 2


def test_day_only_date_gets_no_exif_time() -> None:
    assert build_exif(Tags(date="2005-04-24")) is None
    assert b"<xmp:CreateDate>2005-04-24</xmp:CreateDate>" in build_xmp(Tags(date="2005-04-24"))


def test_png_gets_exif_and_xmp_chunks(tmp_path) -> None:
    path = tmp_path / "a.png"
    path.write_bytes(png_bytes())
    embed_image_tags(path, tags_for(carousel_post()))
    data = path.read_bytes()

    kinds, position = [], 8
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        payload = data[position + 8 : position + 8 + length]
        crc = struct.unpack(">I", data[position + 8 + length : position + 12 + length])[0]
        assert crc == zlib.crc32(kind + payload)
        kinds.append(kind)
        position += 12 + length

    assert kinds == [b"IHDR", b"eXIf", b"iTXt", b"IDAT", b"IEND"]


def test_webp_becomes_extended_with_exif_and_xmp(tmp_path) -> None:
    path = tmp_path / "a.webp"
    path.write_bytes(webp_bytes())
    embed_image_tags(path, tags_for(carousel_post()))
    data = path.read_bytes()

    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8
    assert data[12:16] == b"VP8X"
    flags, canvas = data[20], data[24:30]
    assert flags == 0x10 | 0x08 | 0x04
    assert (int.from_bytes(canvas[:3], "little") + 1, int.from_bytes(canvas[3:], "little") + 1) == (3, 2)
    assert data.index(b"VP8L") < data.index(b"EXIF") < data.index(b"XMP ")


def test_unsupported_image_is_a_warning_not_a_failure(tmp_path, monkeypatch) -> None:
    @contextmanager
    def gif(url, **kwargs):
        yield http.Stream(url=url, content_type="image/gif", chunks=iter([b"GIF89a"]))

    monkeypatch.setattr(http, "stream", gif)
    gif_item = Item(index=1, kind="image", url="https://e.com/a.gif", ext="gif")
    post = Post(platform="x", id="1", description="caption", items=[gif_item])
    outcome = download(post, output=str(tmp_path / "%(id)s.%(ext)s"), embed_metadata=True)[0]

    assert outcome.status == "saved"
    assert "JPEG, PNG and WebP only" in outcome.warning


# Embedded metadata: video

def probe_tags(path: Path) -> dict:
    output = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout
    return {key.lower(): value for key, value in json.loads(output)["format"].get("tags", {}).items()}


def test_video_gets_ffmpeg_tags(tmp_path, fake_ytdlp_download, sample_video) -> None:
    _, source = fake_ytdlp_download
    source["path"] = sample_video
    post = carousel_post()
    post.title = "Golden hour"

    outcome = download(post, want=["video"], output=str(tmp_path / "%(id)s.%(ext)s"), embed_metadata=True)[0]
    tags = probe_tags(outcome.path)

    assert outcome.warning is None
    assert tags["title"] == "Golden hour"
    assert tags["artist"] == "Some One"
    assert tags["date"] == "2026-10-30T18:22:00Z"
    assert tags["description"] == "sunset at the beach 🌅"
    assert tags["comment"] == POST_URL
    assert not list(tmp_path.glob("*.temp*"))


# CLI

@pytest.fixture
def offline_instagram(monkeypatch):
    gallery = json.loads((FIXTURES / "gallerydl_instagram_carousel.json").read_text(encoding="utf-8"))
    info = json.loads((FIXTURES / "ytdlp_instagram_carousel.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", lambda self, url: gallery)
    monkeypatch.setattr(YtDlpProvider, "fetch_info", lambda self, url: info)


def test_cli_saves_a_mixed_post_with_names_and_metadata(
    tmp_path, capsys, offline_instagram, fake_files, fake_ytdlp_download, sample_video
) -> None:
    """The milestone's "done when": one command saves a mixed post with names and metadata."""
    _, source = fake_ytdlp_download
    source["path"] = sample_video
    template = str(tmp_path / "%(platform)s" / "%(uploader.username)s" / "%(id)s_%(index)s.%(ext)s")

    assert main([POST_URL, "-o", template, "--embed-metadata"]) == 0

    folder = tmp_path / "instagram" / "someone"
    names = sorted(path.name for path in folder.iterdir())
    assert names == ["DAsTXK3JbrA_1.jpg", "DAsTXK3JbrA_2.mp4", "DAsTXK3JbrA_3.jpg"]
    assert "sunset at the beach" in jpeg_segments((folder / "DAsTXK3JbrA_1.jpg").read_bytes())[2][3:].decode("utf-8")
    assert probe_tags(folder / "DAsTXK3JbrA_2.mp4")["description"] == "sunset at the beach"
    printed = capsys.readouterr().out
    assert printed.count("[download]") == 3 and "video 2:" in printed


def test_cli_want_only_downloads_those_kinds(tmp_path, capsys, offline_instagram, fake_files) -> None:
    assert main([POST_URL, "--want", "images", "-o", str(tmp_path / "%(index)s.%(ext)s")]) == 0

    assert sorted(path.name for path in tmp_path.iterdir()) == ["1.jpg", "3.jpg"]


def test_cli_reports_failures_with_exit_code(tmp_path, capsys, offline_instagram) -> None:
    # conftest turns the network off, so every image download fails.
    assert main([POST_URL, "--want", "images", "-o", str(tmp_path / "%(index)s.%(ext)s")]) == 1

    assert capsys.readouterr().err.count("error: image") == 2


def test_cli_with_nothing_to_download(capsys, offline_instagram) -> None:
    # The native avatar lookup can't reach the network in tests, so there is no avatar.
    assert main([POST_URL, "--want", "avatar"]) == 1

    assert "nothing to download" in capsys.readouterr().err


# Live check (milestone 5 "done when", with a real video)

@pytest.mark.skipif(os.environ.get("CONTENT_DL_LIVE") != "1", reason="set CONTENT_DL_LIVE=1 to hit YouTube")
def test_live_youtube_download(tmp_path) -> None:
    template = str(tmp_path / "%(id)s_%(kind)s.%(ext)s")

    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    assert main([url, "--want", "video,thumbnail", "-o", template, "--embed-metadata"]) == 0

    names = sorted(path.name for path in tmp_path.iterdir())
    assert len(names) == 2 and names[0].startswith("jNQXAC9IVRw_thumbnail") and names[1].startswith("jNQXAC9IVRw_video")
    if HAS_FFMPEG:
        assert probe_tags(tmp_path / names[1])["title"] == "Me at the zoo"
