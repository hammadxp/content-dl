"""Snapchat: share links resolved before routing, Spotlight videos and profiles from saved pages."""

from pathlib import Path

import pytest

from content_dl import extract
from content_dl.providers import GalleryDlProvider
from content_dl.providers.base import ProviderError
from content_dl.providers.native import NativeProvider, http, snapchat
from content_dl.schema import Post, Profile

FIXTURES = Path(__file__).parent / "fixtures"
SPOTLIGHT_ID = "W7_EDlXWTBiXAEEniNoMPwAAYcGVxbHFna2dsAaEF6cRRAaEF6cQ4AAAAAw"
SPOTLIGHT_LINK = f"https://www.snapchat.com/spotlight/{SPOTLIGHT_ID}?share_id=eYLVaGzEc4w&locale=en-US"
SHORT_LINK = "https://snapchat.com/t/xP8xMiKB"
PROFILE_LINK = "https://www.snapchat.com/add/matrix.001?share_id=xev-3PfdJng&locale=en-US"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf8")


@pytest.fixture
def site(monkeypatch):
    """Fake snapchat.com: the short link redirects to the Spotlight, which serves the saved pages."""
    calls: list[str] = []

    def fake_get(url, headers=None, cookies=None):
        calls.append(url)
        if url == SHORT_LINK:
            return http.Response(200, f"https://www.snapchat.com/spotlight/{SPOTLIGHT_ID}?sid=9d&share_id=2I", "")

        if url == f"https://www.snapchat.com/spotlight/{SPOTLIGHT_ID}":
            return http.Response(200, url, page("snapchat_spotlight.html"))

        if url == "https://www.snapchat.com/@matrix.001":
            return http.Response(200, url, page("snapchat_profile.html"))

        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(http, "get", fake_get)
    # INFO: native reads Spotlights and profiles fully, so gallery-dl, the fallback, is never asked.
    monkeypatch.setattr(GalleryDlProvider, "extract", lambda *args, **kwargs: pytest.fail("gallery-dl shouldn't run"))
    return calls


def test_spotlight_page_gives_the_video_poster_and_creator():
    post = snapchat.post_from_page(page("snapchat_spotlight.html"), f"https://www.snapchat.com/spotlight/{SPOTLIGHT_ID}")

    assert post.id == SPOTLIGHT_ID
    assert post.title == "Sheraz Bhatti's Emotional Reaction to Pakistan Cricket Win"
    assert post.upload_date == "2026-10-04T07:56:08.632000Z"
    video, thumbnail = post.items
    assert (video.media_type, video.ext, video.width, video.height, video.duration) == ("video", "mp4", 540, 960, 17.0)
    assert video.url.startswith("https://cf-st.sc-cdn.net/o/bHB5G5LLDzyW7s5VuZS37.27.")
    assert thumbnail.media_type == "thumbnail"
    assert post.uploader.username == "sheraz_bhatti98"
    assert post.uploader.avatar.url.endswith("._FMjpeg")


def test_profile_page_gives_the_full_size_avatar():
    profile = snapchat.profile_from_page(page("snapchat_profile.html"))

    assert (profile.username, profile.url) == ("matrix.001", "https://www.snapchat.com/@matrix.001")
    assert profile.name == "ہارون علی واہلہ"
    assert profile.avatar.url.endswith("WllBRSUzRCZ1Yz0yNQ._FMjpeg")


def test_page_without_schema_data_is_an_error():
    with pytest.raises(ProviderError, match="no video data"):
        snapchat.post_from_page("<html></html>", f"https://www.snapchat.com/spotlight/{SPOTLIGHT_ID}")


def test_short_link_resolves_to_the_clean_spotlight_url(site):
    assert NativeProvider().resolve_snapchat_url(SHORT_LINK) == f"https://www.snapchat.com/spotlight/{SPOTLIGHT_ID}"


def test_add_link_becomes_the_profile_url_without_a_request(site):
    assert NativeProvider().resolve_snapchat_url(PROFILE_LINK) == "https://www.snapchat.com/@matrix.001"
    assert site == []


def test_extract_reads_a_shared_spotlight_natively(site):
    result = extract(SHORT_LINK)

    assert isinstance(result, Post)
    assert [item.media_type for item in result.items] == ["video", "thumbnail"]
    assert result.uploader.avatar.provider == "native"


def test_extract_reads_a_profile_natively(site):
    result = extract(PROFILE_LINK)

    assert isinstance(result, Profile)
    assert result.avatar.provider == "native"


def test_highlights_are_left_to_gallery_dl():
    with pytest.raises(ProviderError, match="only Spotlight videos and profiles"):
        snapchat.page_url("https://www.snapchat.com/@matrix.001/highlight/505c99ec")
