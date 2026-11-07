"""Milestone 3: the gallery-dl provider and the merger.

Runs offline against saved gallery-dl messages and a yt-dlp info dict for the same Instagram
carousel (image, video, image). Set CONTENT_DL_LIVE=1 and CONTENT_DL_INSTAGRAM_URL to a public
carousel post to also run the live check from the milestone's "done when".
"""

import json
import os
from pathlib import Path

import pytest

from content_dl import ExtractionError, Item, Post, Profile, extract, result_from_json
from content_dl.cli import main
from content_dl.merge import merge, merge_items, strip_query
from content_dl.providers import GalleryDlProvider, ProviderError, YtDlpProvider
from content_dl.providers.gallerydl import item_from_file, result_from_messages
from content_dl.providers.ytdlp import post_from_info
from content_dl.router import routes_for

FIXTURES = Path(__file__).parent / "fixtures"
POST_URL = "https://www.instagram.com/p/DAsTXK3JbrA/"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def gallery_messages() -> list:
    return load_fixture("gallerydl_instagram_carousel.json")


@pytest.fixture
def ytdlp_carousel() -> dict:
    return load_fixture("ytdlp_instagram_carousel.json")


@pytest.fixture
def offline_providers(monkeypatch, gallery_messages, ytdlp_carousel):
    """Serve saved output from both providers and record which ones ran."""
    calls: list[str] = []

    def fake_messages(self, url):
        calls.append("gallery-dl")
        return gallery_messages

    def fake_info(self, url):
        calls.append("yt-dlp")
        return ytdlp_carousel

    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", fake_messages)
    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_info)
    return calls


# gallery-dl translation

def test_gallery_dl_carousel_becomes_a_post(gallery_messages) -> None:
    post = result_from_messages(gallery_messages, "instagram")

    assert (post.id, post.title, post.description) == ("DAsTXK3JbrA", None, "sunset at the beach")
    assert post.upload_date == "2026-10-30T18:22:00Z"
    assert post.url == POST_URL
    assert (post.uploader.id, post.uploader.username, post.uploader.name) == ("123456", "someone", "Some One")
    assert post.uploader.url == "https://www.instagram.com/someone/"
    assert [(item.index, item.kind) for item in post.items] == [(1, "image"), (2, "video"), (3, "image")]
    assert {item.provider for item in post.items} == {"gallery-dl"}


def test_gallery_dl_media_ids_match_yt_dlps(gallery_messages, ytdlp_carousel) -> None:
    gallery_ids = [item.extra["media_id"] for item in result_from_messages(gallery_messages, "instagram").items]

    assert gallery_ids == [entry["id"] for entry in ytdlp_carousel["entries"]]


def test_gallery_dl_items_keep_size_and_drop_zero_sizes() -> None:
    url = "https://cdn.example.com/a.jpg"

    image = item_from_file(url, {"num": 1, "width": 0, "height": 0, "extension": "jpg"}, "example.com", 1)

    assert image.to_dict() == {"index": 1, "kind": "image", "url": url, "ext": "jpg", "provider": "gallery-dl"}


def test_gallery_dl_skips_ytdl_hand_offs() -> None:
    assert item_from_file("ytdl:https://v.redd.it/abc", {"num": 1}, "reddit", 1) is None


def test_gallery_dl_rejects_several_posts(gallery_messages) -> None:
    with pytest.raises(ProviderError, match="several posts"):
        result_from_messages([*gallery_messages, gallery_messages[0]], "instagram")


def test_gallery_dl_rejects_profiles() -> None:
    with pytest.raises(ProviderError, match="profiles and collections"):
        result_from_messages([[6, "https://www.instagram.com/someone/posts/", {}]], "instagram")


def test_unmapped_platform_uses_the_generic_mapper() -> None:
    messages = [[3, "https://cdn.example.com/p/1.png", {"id": 7, "title": "A picture", "date": "2026-01-02 03:04:05"}]]

    post = result_from_messages(messages, "example.com")

    assert (post.id, post.title, post.upload_date) == ("7", "A picture", "2026-01-02T03:04:05Z")
    assert post.items[0].kind == "image"


# yt-dlp carousel translation

def test_yt_dlp_reads_an_instagram_carousel(ytdlp_carousel) -> None:
    post = post_from_info(ytdlp_carousel, "instagram")

    assert post.title is None
    assert (post.uploader.id, post.uploader.username, post.uploader.name) == ("123456", "someone", "Some One")
    assert [(item.index, item.kind) for item in post.items] == [
        (1, "image"),
        (2, "video"),
        (2, "audio"),
        (2, "thumbnail"),
        (3, "image"),
    ]


def test_yt_dlp_still_rejects_real_playlists(ytdlp_carousel) -> None:
    ytdlp_carousel["entries"] = [{"_type": "url", "url": "https://www.youtube.com/watch?v=abc"}]

    with pytest.raises(ProviderError, match="playlists"):
        post_from_info(ytdlp_carousel, "youtube")


# Merger

def test_strip_query_ignores_cdn_parameters() -> None:
    assert strip_query("https://x.com/a.jpg?name=orig") == strip_query("https://x.com/a.jpg?name=small")


def test_duplicates_by_url_merge_and_keep_the_winner() -> None:
    high = [Item(index=1, kind="image", url="https://cdn/a.jpg?s=1080", provider="gallery-dl")]
    low = [Item(index=1, kind="image", url="https://cdn/a.jpg?s=640", width=640, provider="cobalt")]

    (merged,) = merge_items([high, low])

    assert (merged.url, merged.width, merged.provider) == ("https://cdn/a.jpg?s=1080", 640, "gallery-dl")


def test_different_kinds_are_never_duplicates() -> None:
    video = Item(index=2, kind="video", provider="yt-dlp", extra={"media_id": "B"})
    thumbnail = Item(index=2, kind="thumbnail", url="https://cdn/b.jpg", provider="yt-dlp", extra={"media_id": "B"})

    assert len(merge_items([[video, thumbnail]])) == 2


def test_merge_does_not_change_provider_results(gallery_messages, ytdlp_carousel) -> None:
    results = {
        "gallery-dl": result_from_messages(gallery_messages, "instagram"),
        "yt-dlp": post_from_info(ytdlp_carousel, "instagram"),
    }
    before = {name: result.to_dict() for name, result in results.items()}

    merge(results, routes_for("instagram"))

    assert {name: result.to_dict() for name, result in results.items()} == before


def test_fields_come_from_the_highest_priority_provider() -> None:
    # Ran in this order, but Instagram's metadata column puts yt-dlp first.
    results = {
        "gallery-dl": Post(platform="x", id="1", title="from gallery-dl", description="caption"),
        "yt-dlp": Post(platform="x", id="1", title="from yt-dlp", upload_date="2026-01-01"),
    }

    post = merge(results, routes_for("instagram"))

    assert (post.title, post.description, post.upload_date) == ("from yt-dlp", "caption", "2026-01-01")


def test_lead_metadata_provider_decides_post_or_profile() -> None:
    results = {
        "gallery-dl": Post(platform="x", id="1"),
        "yt-dlp": Profile(platform="x", username="someone"),
    }

    assert isinstance(merge(results, routes_for("instagram")), Profile)


def test_providers_outside_a_column_are_a_last_resort() -> None:
    image = Item(index=1, kind="image", url="https://cdn/a.jpg", provider="yt-dlp")

    # YouTube's images column is empty, so yt-dlp's image is only there as a last resort.
    post = merge({"yt-dlp": Post(platform="youtube", items=[image])}, routes_for("youtube"))

    assert post.items == [image]


# extract() on an Instagram carousel (milestone 3 "done when")

def test_carousel_returns_every_image_and_video_once(offline_providers) -> None:
    post = extract(POST_URL)

    assert [(item.index, item.kind, item.provider) for item in post.items] == [
        (1, "image", "yt-dlp"),
        (2, "video", "yt-dlp"),
        (2, "audio", "yt-dlp"),
        (2, "thumbnail", "yt-dlp"),
        (3, "image", "yt-dlp"),
    ]
    # yt-dlp's logged-out route filled every column, so gallery-dl never ran.
    assert offline_providers == ["yt-dlp"]


def test_carousel_has_uploader_and_upload_date(offline_providers) -> None:
    post = extract(POST_URL)

    assert post.upload_date == "2026-10-30T18:22:00Z"
    assert (post.uploader.id, post.uploader.username, post.uploader.name) == ("123456", "someone", "Some One")
    assert (post.id, post.title, post.description) == ("DAsTXK3JbrA", None, "sunset at the beach")


@pytest.fixture
def ytdlp_without_images(offline_providers, ytdlp_carousel):
    """yt-dlp returns the carousel but no picture for its still images."""
    for position in (0, 2):
        del ytdlp_carousel["entries"][position]["thumbnail"], ytdlp_carousel["entries"][position]["thumbnails"]

    return offline_providers


def test_missing_images_are_filled_by_gallery_dl(ytdlp_without_images) -> None:
    post = extract(POST_URL)

    assert [(item.index, item.kind, item.provider) for item in post.items] == [
        (1, "image", "gallery-dl"),
        (2, "video", "yt-dlp"),
        (2, "audio", "yt-dlp"),
        (2, "thumbnail", "yt-dlp"),
        (3, "image", "gallery-dl"),
    ]
    assert ytdlp_without_images == ["yt-dlp", "gallery-dl"]


def test_merged_video_keeps_formats_and_gains_the_direct_file(ytdlp_without_images) -> None:
    video = next(item for item in extract(POST_URL).items if item.kind == "video")

    assert [fmt["format_id"] for fmt in video.formats] == ["101", "dash-video"]
    assert video.url.startswith("https://scontent.cdninstagram.com/o1/v/t16/f2/m86/222.mp4")
    assert (video.width, video.height, video.duration) == (720, 1280, 14.2)


def test_images_only_need_one_provider(offline_providers) -> None:
    post = extract(POST_URL, want=["images"])

    assert [item.kind for item in post.items] == ["image", "image"]
    assert offline_providers == ["yt-dlp"]


def test_yt_dlp_failure_falls_back_to_gallery_dl(monkeypatch, gallery_messages) -> None:
    def failing(self, url):
        raise ProviderError("yt-dlp: Instagram sent an empty media response")

    monkeypatch.setattr(YtDlpProvider, "fetch_info", failing)
    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", lambda self, url: gallery_messages)

    post = extract(POST_URL)

    assert post.uploader.username == "someone"
    assert [(item.index, item.kind) for item in post.items] == [(1, "image"), (2, "video"), (3, "image")]
    assert {item.provider for item in post.items} == {"gallery-dl"}


def test_all_providers_failing_reports_every_error(monkeypatch) -> None:
    def gallery_fails(self, url):
        raise ProviderError("gallery-dl: login required")

    def ytdlp_fails(self, url):
        raise ProviderError("yt-dlp: rate limited")

    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", gallery_fails)
    monkeypatch.setattr(YtDlpProvider, "fetch_info", ytdlp_fails)

    with pytest.raises(ExtractionError, match="rate limited; gallery-dl: login required"):
        extract(POST_URL)


def test_forcing_gallery_dl_skips_the_merger(offline_providers) -> None:
    post = extract(POST_URL, provider="gallery-dl")

    assert {item.provider for item in post.items} == {"gallery-dl"}
    assert offline_providers == ["gallery-dl"]


def test_cli_dumps_the_merged_carousel(offline_providers, capsys) -> None:
    assert main(["-J", POST_URL]) == 0

    post = result_from_json(capsys.readouterr().out)
    assert isinstance(post, Post)
    assert [item.kind for item in post.items].count("image") == 2


# Live check

@pytest.mark.skipif(
    os.environ.get("CONTENT_DL_LIVE") != "1" or not os.environ.get("CONTENT_DL_INSTAGRAM_URL"),
    reason="set CONTENT_DL_LIVE=1 and CONTENT_DL_INSTAGRAM_URL to a public carousel post",
)
def test_live_instagram_carousel() -> None:
    post = extract(os.environ["CONTENT_DL_INSTAGRAM_URL"])

    assert post.uploader and post.uploader.username and post.upload_date
    media = [item for item in post.items if item.kind in ("image", "video")]
    assert len({(item.index, item.kind) for item in media}) == len(media) > 1
