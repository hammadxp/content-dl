"""TikTok: share links resolved before routing, profiles read from gallery-dl's avatar extractor."""

from __future__ import annotations

import pytest

from content_dl.providers.gallerydl import GalleryDlProvider, result_from_messages
from content_dl.providers.native import NativeProvider, http
from content_dl.schema import Profile

SHARE_URL = "https://www.tiktok.com/t/ZPLVSx3Ey/"
PHOTO_URL = "https://www.tiktok.com/@usmanramzan/photo/7700208115660164359"

AVATAR_MESSAGES = [
    (2, {"id": "748", "uniqueId": "agonistech", "nickname": "Agonis Tech"}),
    (3, "https://p16.tiktokcdn.com/avt/abc~tplv:1080:1080.jpeg?x=1", {"id": "748", "uniqueId": "agonistech",
                                                                     "nickname": "Agonis Tech", "extension": "jpeg"}),
]


def test_share_link_resolves_to_the_clean_post_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, **_: object) -> http.Response:
        assert url == SHARE_URL
        return http.Response(status=200, url=f"{PHOTO_URL}?_r=1&_t=ZP-9BDNSBTWPz5", text="")

    monkeypatch.setattr(http, "get", fake_get)
    assert NativeProvider().resolve_tiktok_url(SHARE_URL) == PHOTO_URL


def test_share_link_is_kept_when_it_does_not_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "get", lambda url, **_: http.Response(status=403, url=url, text=""))
    assert NativeProvider().resolve_tiktok_url(SHARE_URL) == SHARE_URL


def test_profile_url_loses_its_share_parameters() -> None:
    url = NativeProvider().resolve_tiktok_url("https://www.tiktok.com/@agonistech?_r=1&_t=ZP-9BDNOtHgRgd")
    assert url == "https://www.tiktok.com/@agonistech"


def test_gallery_dl_reads_a_profile_from_the_avatar_page(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def fake_fetch(self: GalleryDlProvider, url: str) -> list:
        asked.append(url)
        return AVATAR_MESSAGES

    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", fake_fetch)
    result = GalleryDlProvider().extract("https://www.tiktok.com/@agonistech", "tiktok")

    assert asked == ["https://www.tiktok.com/@agonistech/avatar"]
    assert isinstance(result, Profile)
    assert (result.id, result.username, result.name) == ("748", "agonistech", "Agonis Tech")
    assert result.avatar is not None and result.avatar.ext == "jpeg"


def test_photo_post_keeps_its_photo_url() -> None:
    post = {"id": "7700208115660164359", "imagePost": {"images": []}, "desc": "hi",
            "author": {"id": "1", "uniqueId": "usmanramzan", "nickname": "Usman"}}
    messages = [
        (2, post),
        (3, "https://p16.tiktokcdn.com/photo/1.jpeg", {**post, "num": 1, "extension": "jpeg", "width": 1604}),
        (3, "https://p16.tiktokcdn.com/photo/2.jpeg", {**post, "num": 2, "extension": "jpeg", "width": 1604}),
    ]
    result = result_from_messages(messages, "tiktok")

    assert result.url == PHOTO_URL
    assert [item.index for item in result.items] == [1, 2]
    assert {item.media_type for item in result.items} == {"image"}
