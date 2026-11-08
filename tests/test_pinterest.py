"""Pinterest: pin.it links resolved before routing, board invites, profiles and pin uploaders from native."""

import json
from urllib.parse import unquote

import pytest

from content_dl import extract
from content_dl.extract import ExtractionError
from content_dl.providers import GalleryDlProvider, YtDlpProvider
from content_dl.providers.base import ProviderError
from content_dl.providers.native import http
from content_dl.router import detect_platform
from content_dl.schema import Post, Profile

PROFILE_LINK = "https://www.pinterest.com/beulsetter/?invite_code=5b3&sender=843"
INVITE_LINK = (
    "https://www.pinterest.com/invited/?invite_code=f3f&board_collab_inviter=True&inviter_user_id=843"
    "&board_id=8434&inviter_first_name=Hammad"
)
AVATAR = "https://i.pinimg.com/280x280_RS/45/cc/c3/a.jpg"
ORIGINAL = "https://i.pinimg.com/originals/45/cc/c3/a.jpg"
USER = {"id": "601", "username": "beulsetter", "full_name": "Jennie", "image_xlarge_url": AVATAR}


def resource(data):
    return json.dumps({"resource_response": {"data": data}})


@pytest.fixture
def site(monkeypatch):
    """Fake Pinterest: the shortener, UserResource, BoardResource and the avatar files."""
    state = {"redirects": {}, "boards": {}, "users": {"beulsetter": USER, "601": USER}, "originals": True}
    calls: list[str] = []

    def fake_get(url, headers=None, cookies=None):
        calls.append(url)
        if url.startswith("https://api.pinterest.com/url_shortener/"):
            code = url.split("/")[4]
            target = state["redirects"].get(code)
            return http.Response(200 if target else 404, target or url, "")

        if "/resource/UserResource/" in url:
            options = json.loads(unquote(url.split("data=", 1)[1]))["options"]
            user = state["users"].get(options.get("username") or options.get("user_id"))
            return http.Response(200 if user else 404, url, resource(user))

        if "/resource/BoardResource/" in url:
            board = next((path for board_id, path in state["boards"].items() if board_id in url), None)
            return http.Response(200 if board else 404, url, resource({"url": board} if board else None))

        if url == ORIGINAL:
            return http.Response(200 if state["originals"] else 403, url, "")

        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(http, "get", fake_get)
    state["calls"] = calls
    return state


@pytest.fixture
def tools(monkeypatch):
    """yt-dlp reads pins (with the uploader's numeric id); gallery-dl must not be reached."""
    asked: list[tuple[str, str]] = []

    def fake_info(self, url):
        asked.append(("yt-dlp", url))
        if "/pin/" not in url:
            raise ProviderError(f"yt-dlp: Unsupported URL: {url}")

        return {
            "id": "894",
            "extractor_key": "Pinterest",
            "title": "A pin",
            "uploader": "Jennie",
            "uploader_id": "601",
            "thumbnail": "https://i.pinimg.com/originals/d2/pin.jpg",
            "formats": [],
        }

    def fake_messages(self, url):
        asked.append(("gallery-dl", url))
        raise ProviderError("gallery-dl: not expected here")

    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_info)
    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", fake_messages)
    return asked


@pytest.mark.parametrize(
    "url",
    ["https://pin.it/abc", "https://www.pinterest.com/pin/1/", "https://pinterest.co.uk/pin/1/", "https://in.pinterest.com/x/"],
)
def test_pinterest_urls_get_the_pinterest_row(url) -> None:
    assert detect_platform(url) == "pinterest"


def test_profile_share_link_becomes_a_profile_with_the_original_avatar(site, tools) -> None:
    site["redirects"]["3cM"] = PROFILE_LINK
    result = extract("https://pin.it/3cM")

    assert isinstance(result, Profile)
    assert (result.username, result.name, result.url) == ("beulsetter", "Jennie", "https://www.pinterest.com/beulsetter/")
    assert result.avatar.url == ORIGINAL
    # The profile is answered natively: no tool is asked to walk the whole account.
    assert tools == []


def test_avatar_falls_back_to_the_sized_copy(site, tools) -> None:
    site["originals"] = False
    assert extract("https://www.pinterest.com/beulsetter/").avatar.url == AVATAR


def test_unknown_profile_is_an_extraction_error(site, tools) -> None:
    with pytest.raises(ExtractionError, match="no such profile"):
        extract("https://www.pinterest.com/nobody-here/")


def test_image_pin_is_an_image_with_its_uploader(site, tools) -> None:
    site["redirects"]["6v4"] = "https://www.pinterest.com/pin/894/?invite_code=1&sender=2"
    result = extract("https://pin.it/6v4")

    assert isinstance(result, Post)
    assert [item.media_type for item in result.items] == ["image"]
    assert tools[0] == ("yt-dlp", "https://www.pinterest.com/pin/894/")
    assert (result.uploader.username, result.uploader.avatar.url) == ("beulsetter", ORIGINAL)


def test_pin_without_the_avatar_column_skips_the_profile_lookup(site, tools) -> None:
    extract("https://www.pinterest.com/pin/894/", want=["image"])
    assert not any("UserResource" in url for url in site["calls"])


def test_board_invite_becomes_the_board(site, tools) -> None:
    site["redirects"]["1l2"] = INVITE_LINK
    site["boards"]["8434"] = "/hammadxp/memes/"
    with pytest.raises(ExtractionError):
        extract("https://pin.it/1l2")

    assert ("yt-dlp", "https://www.pinterest.com/hammadxp/memes/") in tools


def test_private_board_invite_asks_for_cookies(site, tools) -> None:
    site["redirects"]["1l2"] = INVITE_LINK
    with pytest.raises(ExtractionError, match="private board needs --cookies"):
        extract("https://pin.it/1l2")

    assert tools == []


def test_unresolvable_short_link_is_passed_on(site, tools) -> None:
    with pytest.raises(ExtractionError):
        extract("https://pin.it/gone")

    assert ("yt-dlp", "https://pin.it/gone") in tools
