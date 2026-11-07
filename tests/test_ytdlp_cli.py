"""Milestone 2: router, yt-dlp provider, extract() and the -J / -F CLI.

Everything here runs offline against a saved, trimmed yt-dlp info dict. Set CONTENT_DL_LIVE=1
to also run the live YouTube check from the milestone's "done when".
"""

import json
import os
from pathlib import Path

import pytest

from content_dl import ExtractionError, Post, Profile, extract, result_from_json
from content_dl.cli import main
from content_dl.providers import ProviderError, YtDlpProvider
from content_dl.providers.ytdlp import post_from_info, result_from_info
from content_dl.router import RouterError, detect_platform, providers_for

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
CHANNEL_URL = "https://www.youtube.com/@jawed"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def youtube_info() -> dict:
    return load_fixture("ytdlp_youtube_video.json")


@pytest.fixture
def channel_info() -> dict:
    return load_fixture("ytdlp_youtube_channel.json")


@pytest.fixture
def offline_ytdlp(monkeypatch, youtube_info, channel_info):
    """Make YtDlpProvider return saved info dicts instead of calling YouTube."""
    calls: list[str] = []

    def fake_fetch(self, url):
        calls.append(url)
        return channel_info if "/@" in url or "/channel/" in url else youtube_info

    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_fetch)
    return calls


# Router

@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://m.youtube.com/shorts/abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.instagram.com/p/C9xYz123/", "instagram"),
        ("https://www.threads.net/@someone/post/abc", "threads"),
        ("https://twitter.com/someone/status/1", "x"),
        ("https://x.com/someone/status/1", "x"),
        ("https://vm.tiktok.com/abc/", "tiktok"),
        ("https://old.reddit.com/r/pics/comments/abc/", "reddit"),
        ("https://www.snapchat.com/spotlight/abc", "snapchat"),
        ("https://example.com/video.mp4", "example.com"),
        ("https://www.example.co.uk/a", "example.co.uk"),
        ("https://www.com/a", "www.com"),
        ("https://notyoutube.com/watch", "notyoutube.com"),
    ],
)
def test_detect_platform(url, platform) -> None:
    assert detect_platform(url) == platform


@pytest.mark.parametrize("url", ["not a url", "ftp://youtube.com/x", ""])
def test_detect_platform_rejects_non_http_urls(url) -> None:
    with pytest.raises(RouterError):
        detect_platform(url)


def test_providers_follow_the_routing_table_order() -> None:
    assert providers_for("instagram") == ["yt-dlp", "gallery-dl", "cobalt", "native"]
    assert providers_for("youtube", ["video"]) == ["yt-dlp"]
    assert providers_for("youtube", ["avatar"]) == ["yt-dlp"]


def test_unknown_sites_use_the_unknown_site_row() -> None:
    assert providers_for("example.com", ["image"]) == ["yt-dlp", "generic", "gallery-dl"]


# yt-dlp translation

def test_youtube_video_becomes_a_post(youtube_info) -> None:
    post = post_from_info(youtube_info, "youtube")

    assert post.platform == "youtube"
    assert post.id == "jNQXAC9IVRw"
    assert post.title == "Me at the zoo"
    assert post.upload_date == "2005-04-24T03:31:52Z"
    assert post.url == VIDEO_URL
    assert post.uploader.id == "UC4QobU6STFB0P71PMvOGN5A"
    assert post.uploader.username == "jawed"
    assert post.uploader.url == "https://www.youtube.com/@jawed"
    assert [item.kind for item in post.items] == ["video", "audio", "thumbnail"]
    assert {item.index for item in post.items} == {1}
    assert {item.provider for item in post.items} == {"yt-dlp"}


def test_streams_are_split_into_video_and_audio_formats(youtube_info) -> None:
    video, audio, _ = post_from_info(youtube_info, "youtube").items

    assert [fmt["format_id"] for fmt in video.formats] == ["160", "395"]
    assert [fmt["format_id"] for fmt in audio.formats] == ["249", "251"]
    assert audio.ext == "webm"
    assert video.duration == 19.0
    assert video.url is None


def test_formats_keep_stream_urls_and_drop_storyboards(youtube_info) -> None:
    video, audio, _ = post_from_info(youtube_info, "youtube").items

    for fmt in [*video.formats, *audio.formats]:
        assert fmt["url"].startswith("https://")
        assert fmt["ext"] != "mhtml"


def test_thumbnail_uses_yt_dlps_pick(youtube_info) -> None:
    thumbnail = post_from_info(youtube_info, "youtube").items[-1]

    assert thumbnail.url == youtube_info["thumbnail"]
    assert (thumbnail.ext, thumbnail.width, thumbnail.height) == ("jpg", 480, 360)


def test_day_only_upload_date_is_kept_without_a_time(youtube_info) -> None:
    del youtube_info["timestamp"]

    assert post_from_info(youtube_info, "youtube").upload_date == "2005-04-24"


def test_no_dates_give_no_upload_date(youtube_info) -> None:
    del youtube_info["timestamp"], youtube_info["upload_date"]

    assert post_from_info(youtube_info, "youtube").upload_date is None


def test_missing_uploader_fields_give_no_uploader(youtube_info) -> None:
    for key in ("uploader", "uploader_id", "uploader_url", "channel", "channel_id", "channel_url"):
        del youtube_info[key]

    assert post_from_info(youtube_info, "youtube").uploader is None


def test_numeric_uploader_id_is_not_a_username(youtube_info) -> None:
    youtube_info.update(uploader_id="123456", channel_id=None)

    uploader = post_from_info(youtube_info, "tiktok").uploader

    assert (uploader.id, uploader.username) == ("123456", None)


def test_playlists_are_rejected(channel_info) -> None:
    channel_info["id"] = "PLbpi6ZahtOH6Blw3RGYpWkSByi_T7Rygb"

    with pytest.raises(ProviderError, match="playlists"):
        result_from_info(channel_info, "youtube")


def test_channel_page_becomes_a_profile(channel_info) -> None:
    profile = result_from_info(channel_info, "youtube")

    assert isinstance(profile, Profile)
    assert (profile.id, profile.username, profile.name) == ("UC4QobU6STFB0P71PMvOGN5A", "jawed", "jawed")
    assert profile.url == CHANNEL_URL
    assert profile.items == []
    assert profile.avatar.kind == "avatar"
    assert profile.avatar.url.endswith("=s0")
    assert profile.avatar.provider == "yt-dlp"


def test_channel_without_a_labelled_avatar_has_none(channel_info) -> None:
    channel_info["thumbnails"] = [thumb for thumb in channel_info["thumbnails"] if thumb["id"] != "avatar_uncropped"]

    assert result_from_info(channel_info, "youtube").avatar is None


# extract()

def test_extract_returns_a_post_with_the_uploader_avatar(offline_ytdlp) -> None:
    result = extract(VIDEO_URL)

    assert isinstance(result, Post)
    assert result.uploader.avatar.kind == "avatar"
    assert offline_ytdlp == [VIDEO_URL, CHANNEL_URL]


def test_extract_skips_the_avatar_request_when_not_wanted(offline_ytdlp) -> None:
    result = extract(VIDEO_URL, want=["video"])

    assert result.uploader.avatar is None
    assert offline_ytdlp == [VIDEO_URL]


def test_avatar_lookup_failure_keeps_the_post(monkeypatch, youtube_info) -> None:
    def fetch(self, url):
        if url == VIDEO_URL:
            return youtube_info

        raise ProviderError("yt-dlp: channel unavailable")

    monkeypatch.setattr(YtDlpProvider, "fetch_info", fetch)

    assert extract(VIDEO_URL).uploader.avatar is None


def test_extract_returns_a_profile_for_a_channel_url(offline_ytdlp) -> None:
    result = extract(CHANNEL_URL)

    assert isinstance(result, Profile)
    assert offline_ytdlp == [CHANNEL_URL]


def test_extract_keeps_only_wanted_kinds(offline_ytdlp) -> None:
    result = extract(VIDEO_URL, want=["thumbnails"])

    assert [item.kind for item in result.items] == ["thumbnail"]
    assert result.title == "Me at the zoo"


def test_extract_rejects_unknown_kinds(offline_ytdlp) -> None:
    with pytest.raises(ValueError, match="unknown item kind"):
        extract(VIDEO_URL, want=["gifs"])


def test_extract_reports_provider_errors(monkeypatch) -> None:
    def failing_fetch(self, url):
        raise ProviderError("yt-dlp: Video unavailable")

    monkeypatch.setattr(YtDlpProvider, "fetch_info", failing_fetch)

    with pytest.raises(ExtractionError, match="Video unavailable"):
        extract(VIDEO_URL)


def test_extract_fails_clearly_when_no_provider_is_usable() -> None:
    with pytest.raises(ExtractionError, match="no provider can handle snapchat"):
        extract("https://www.snapchat.com/spotlight/abc")


def test_forcing_an_unknown_provider_fails(offline_ytdlp) -> None:
    with pytest.raises(ExtractionError, match="unknown provider"):
        extract(VIDEO_URL, provider="generic")


# CLI

def test_dump_json_prints_a_valid_post(offline_ytdlp, capsys) -> None:
    assert main(["-J", VIDEO_URL]) == 0

    post = result_from_json(capsys.readouterr().out)
    assert isinstance(post, Post)
    assert {"video", "audio", "thumbnail"} <= {item.kind for item in post.items}


def test_list_formats_shows_items_and_formats(offline_ytdlp, capsys) -> None:
    assert main(["-F", VIDEO_URL]) == 0

    out = capsys.readouterr().out
    assert "[youtube] jNQXAC9IVRw: 4 item(s)" in out
    assert "Formats for item 1 (video):" in out
    assert "Formats for item 1 (audio):" in out
    assert "395" in out


def test_cli_reports_bad_urls(capsys) -> None:
    assert main(["-J", "not a url"]) == 1
    assert "error" in capsys.readouterr().err


# Live check (milestone 2 "done when")

@pytest.mark.skipif(os.environ.get("CONTENT_DL_LIVE") != "1", reason="set CONTENT_DL_LIVE=1 to hit YouTube")
def test_live_youtube_dump_json(capsys) -> None:
    assert main(["-J", VIDEO_URL]) == 0

    post = result_from_json(capsys.readouterr().out)
    assert post.title and post.upload_date and post.uploader and post.uploader.avatar
    assert {"video", "audio", "thumbnail"} <= {item.kind for item in post.items}


@pytest.mark.skipif(os.environ.get("CONTENT_DL_LIVE") != "1", reason="set CONTENT_DL_LIVE=1 to hit YouTube")
def test_live_youtube_channel_is_a_profile(capsys) -> None:
    assert main(["-J", CHANNEL_URL]) == 0

    profile = result_from_json(capsys.readouterr().out)
    assert isinstance(profile, Profile)
    assert profile.username == "jawed" and profile.avatar
