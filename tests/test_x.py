"""X: profiles from gallery-dl's avatar page, and yt-dlp limited to a post's own media."""

from __future__ import annotations

import pytest
from yt_dlp.extractor.twitter import TwitterIE
from yt_dlp.utils import ExtractorError

from content_dl import extract
from content_dl.extract import ExtractionError
from content_dl.providers import ProviderError
from content_dl.providers.base import LoginRequiredError
from content_dl.providers.gallerydl import GalleryDlProvider, item_from_file
from content_dl.providers.ytdlp import X_LOGGED_OUT_MESSAGE, YtDlpProvider, own_media_twitter_ie, post_from_info
from content_dl.schema import Profile

PHOTO_MESSAGES = [
    (2, {"user": {"id": 1895750326051037184, "name": "sarahli", "nick": "sarah li"}}),
    (3, "https://pbs.twimg.com/profile_images/2016764496157786112/LjLKpCeo.jpg",
     {"user": {"id": 1895750326051037184, "name": "sarahli", "nick": "sarah li"}, "extension": "jpg"}),
]

QUOTING_STATUS = {
    "id_str": "2115137555075354846",
    "quoted_status_id_str": "2115130930125996103",
    "extended_entities": {"media": [{"type": "photo", "id_str": "2115136465256734720"}]},
    "quoted_status": {"extended_entities": {"media": [{"type": "video", "id_str": "2115130840275685376"}]}},
    "entities": {"urls": [
        {"expanded_url": "https://x.com/sarahli/status/2115130930125996103"},
        {"expanded_url": "https://docs.example.com/"},
    ]},
}


def test_profile_url_reads_the_photo_page(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def fake_fetch(self: GalleryDlProvider, url: str) -> list:
        asked.append(url)
        return PHOTO_MESSAGES

    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", fake_fetch)
    result = GalleryDlProvider().extract("https://x.com/sarahli", "x")

    assert asked == ["https://x.com/sarahli/photo"]
    assert isinstance(result, Profile)
    assert (result.id, result.username, result.name) == ("1895750326051037184", "sarahli", "sarah li")
    assert result.avatar is not None and result.avatar.ext == "jpg"


def test_quoted_post_media_and_link_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TwitterIE, "_extract_status", lambda self, twid: {**QUOTING_STATUS})
    status = own_media_twitter_ie()()._extract_status("2115137555075354846")

    assert "quoted_status" not in status
    assert status["entities"]["urls"] == [{"expanded_url": "https://docs.example.com/"}]
    assert status["extended_entities"]["media"][0]["type"] == "photo"


def test_logged_out_empty_status_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TwitterIE, "_extract_status", lambda self, twid: {"card": {}, "quoted_status": {}})
    with pytest.raises(ExtractorError, match="logged-in"):
        own_media_twitter_ie()()._extract_status("2111006294794481944")


def test_own_extractor_replaces_yt_dlps_under_the_same_key() -> None:
    assert own_media_twitter_ie().ie_key() == "Twitter"


def test_post_id_is_the_post_not_the_video() -> None:
    info = {"id": "2119109350828675072", "display_id": "2119109442671460405", "extractor_key": "Twitter",
            "formats": [{"format_id": "http-632", "url": "https://video.twimg.com/a.mp4", "vcodec": "avc1"}]}
    post = post_from_info(info, "x")

    assert post.id == "2119109442671460405"
    assert post.items[0].extra["media_id"] == "2119109350828675072"


def test_article_cover_gets_its_extension_from_the_query() -> None:
    url = "https://pbs.twimg.com/media/HT-vyDvXAAAKIQi?format=jpg&name=orig"
    item = item_from_file(url, {"extension": "", "num": 1, "type": "article:cover"}, "x", 1)

    assert item is not None and (item.media_type, item.ext) == ("image", "jpg")


def test_logged_out_post_reports_only_the_login_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    def logged_out(self, url, platform, media_types=None, known=None):
        raise LoginRequiredError(X_LOGGED_OUT_MESSAGE)

    def unavailable(self, url, platform, media_types=None, known=None):
        raise ProviderError("gallery-dl: AbortExtraction: 'Unavailable'")

    monkeypatch.setattr(YtDlpProvider, "extract", logged_out)
    monkeypatch.setattr(GalleryDlProvider, "extract", unavailable)
    with pytest.raises(ExtractionError) as caught:
        extract("https://x.com/someone/status/2111006294794481944")

    assert caught.value.login_required
    assert str(caught.value) == X_LOGGED_OUT_MESSAGE
