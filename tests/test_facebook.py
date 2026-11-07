"""Facebook: its routing row, share links resolved before routing, and profiles through gallery-dl."""

import json

import pytest

from content_dl import extract
from content_dl.extract import ExtractionError
from content_dl.cli import main
from content_dl.providers import GalleryDlProvider, YtDlpProvider
from content_dl.providers.base import ProviderError
from content_dl.providers.native import facebook
from content_dl.router import detect_platform

PROFILE_SHARE = "https://www.facebook.com/share/1DWmcVhBs5/"
PROFILE_URL = "https://www.facebook.com/people/Gaffar-Musafir/100064025019966/"
POST_SHARE = "https://www.facebook.com/share/p/19ak87B4rz/"
LOGIN_REDIRECT = (
    "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fstory.php%3Fstory_fbid%3D122%26id%3D615"
    "%26rdid%3DWW7%26share_url%3Dhttps%253A%252F%252Fwww.facebook.com%252Fshare%252Fp%252F19ak87B4rz%252F"
    "&rdid=WW7&_fb_noscript=1"
)
AVATAR_URL = "https://scontent.example.com/avatar.jpg"


@pytest.fixture
def providers(monkeypatch):
    """Record the URL each provider is asked about. yt-dlp has no Facebook profile extractor."""
    calls: list[tuple[str, str]] = []

    def fake_messages(self, url):
        calls.append(("gallery-dl", url))
        if url.endswith("/avatar"):
            return [(2, {"username": "", "user_id": "100064025019966"}), (3, AVATAR_URL, {"extension": "jpg"})]

        raise ProviderError("gallery-dl has no extractor for this URL")

    def fake_info(self, url):
        calls.append(("yt-dlp", url))
        raise ProviderError(f"yt-dlp: Unsupported URL: {url}")

    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", fake_messages)
    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_info)
    return calls


def redirect_to(target: str):
    def fake_follow(url, cookies):
        return target

    return fake_follow


@pytest.mark.parametrize(
    "url", ["https://www.facebook.com/reel/1", "https://m.facebook.com/watch/?v=1", "https://fb.watch/abc/"]
)
def test_facebook_urls_get_the_facebook_row(url) -> None:
    assert detect_platform(url) == "facebook"


def test_profile_share_link_reaches_gallery_dl_as_the_profile(monkeypatch, providers) -> None:
    monkeypatch.setattr(facebook, "follow_redirects", redirect_to(PROFILE_URL + "?rdid=VUB&share_url=x"))
    profile = extract(PROFILE_SHARE)

    assert ("gallery-dl", "https://www.facebook.com/100064025019966/avatar") in providers
    assert all(url != PROFILE_SHARE for _, url in providers)
    assert (profile.id, profile.name, profile.url) == ("100064025019966", "Gaffar Musafir", PROFILE_URL)
    assert (profile.avatar.url, profile.avatar.provider) == (AVATAR_URL, "gallery-dl")


def test_profile_listing(monkeypatch, providers, capsys) -> None:
    monkeypatch.setattr(facebook, "follow_redirects", redirect_to(PROFILE_URL))

    assert main(["--list-only", PROFILE_SHARE]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[facebook] Gaffar Musafir: 1 item(s)") and "avatar" in out


def test_post_behind_the_login_page_is_unwrapped(monkeypatch, providers) -> None:
    monkeypatch.setattr(facebook, "follow_redirects", redirect_to(LOGIN_REDIRECT))

    with pytest.raises(ExtractionError):
        extract(POST_SHARE)

    # The providers see the post itself, without the login page or the share's tracking.
    assert {url for _, url in providers} == {"https://www.facebook.com/story.php?story_fbid=122&id=615"}


def test_unreachable_share_link_is_passed_on_unchanged(monkeypatch, providers) -> None:
    def offline(url, cookies):
        raise ProviderError("request failed")

    monkeypatch.setattr(facebook, "follow_redirects", offline)
    with pytest.raises(ExtractionError):
        extract(POST_SHARE)

    assert {url for _, url in providers} == {POST_SHARE}


def test_direct_links_are_not_resolved(monkeypatch, providers) -> None:
    def never(url, cookies):
        raise AssertionError("only share links are resolved")

    monkeypatch.setattr(facebook, "follow_redirects", never)
    extract(PROFILE_URL)

    assert ("gallery-dl", "https://www.facebook.com/100064025019966/avatar") in providers


# Posts: native reads the post page, gallery-dl fetches each photo at full size

POST_URL = "https://www.facebook.com/story.php?story_fbid=222&id=111"
FULL_SIZE_URL = "https://scontent.example.com/v/901_n.jpg?stp=dst-jpg_tt6&ctp=s1166x2048"


def post_page() -> str:
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / "facebook_post.html").read_text(encoding="utf-8")


def test_post_page_gives_the_post_and_its_photos() -> None:
    post = facebook.post_from_page(post_page(), POST_URL)

    assert (post.id, post.upload_date) == ("222", "2026-11-01T11:07:34Z")
    assert post.description == "Haripur 🇵🇰 to Nuremberg 🇩🇪"
    assert (post.uploader.id, post.uploader.name, post.uploader.url) == (
        "111",
        "Some Page",
        "https://www.facebook.com/profile.php?id=111",
    )
    # The ad's photo is left out; the bare {"id"} reference and the full media are one photo.
    [photo] = post.items
    assert (photo.index, photo.media_type, photo.width, photo.height) == (1, "image", 526, 924)
    assert photo.extra == {
        "media_id": "901",
        "photo_page": "https://www.facebook.com/photo/?fbid=901&set=a.555",
        "full_width": 1166,
        "full_height": 2048,
    }


def test_post_with_several_photos_keeps_their_order() -> None:
    def photo(photo_id: str) -> dict:
        return {
            "media": {
                "__typename": "Photo",
                "id": photo_id,
                "photo_image": {"uri": f"https://scontent.example.com/{photo_id}.jpg", "width": 1, "height": 1},
                "url": f"https://www.facebook.com/photo/?fbid={photo_id}",
            }
        }

    post = {
        "post_id": "5",
        "creation_time": 1,
        "attachments": [{"styles": {"attachment": {"all_subattachments": {"nodes": [photo("a"), photo("b")]}}}}],
    }
    page = f'<script type="application/json">{json.dumps({"data": {"node_v2": post}})}</script>'

    items = facebook.post_from_page(page, "https://www.facebook.com/x/posts/5").items

    assert [(item.index, item.extra["media_id"]) for item in items] == [(1, "a"), (2, "b")]


def test_page_without_post_data_asks_for_cookies() -> None:
    with pytest.raises(ProviderError, match="may need --cookies"):
        facebook.post_from_page("<html>log in to continue</html>", POST_URL)


@pytest.fixture
def post_providers(monkeypatch):
    """A logged-in post page for native; gallery-dl answers photo pages with full-size copies."""
    from content_dl.providers.native import NativeProvider

    calls: list[str] = []

    def fake_page(self, url, domain, headers=None):
        calls.append(f"native {url}")
        return post_page()

    def fake_messages(self, url):
        calls.append(f"gallery-dl {url}")
        if "/photo/" in url:
            return [(2, {"set_id": "a.555"}), (3, FULL_SIZE_URL, {"id": "901", "num": 1, "extension": "jpg"})]

        if url.endswith("/avatar"):
            return [(2, {"user_id": "111"}), (3, AVATAR_URL, {"extension": "jpg"})]

        raise ProviderError("gallery-dl has no extractor for this URL")

    def fake_info(self, url):
        calls.append(f"yt-dlp {url}")
        raise ProviderError("yt-dlp: Cannot parse data")

    monkeypatch.setattr(NativeProvider, "fetch_page", fake_page)
    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", fake_messages)
    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_info)
    return calls


def test_image_post_gets_full_size_photos_and_the_posts_metadata(post_providers) -> None:
    post = extract(POST_URL)

    [image] = post.items
    assert (image.url, image.provider, image.width, image.height) == (FULL_SIZE_URL, "gallery-dl", 1166, 2048)
    assert (post.id, post.description) == ("222", "Haripur 🇵🇰 to Nuremberg 🇩🇪")
    assert (post.uploader.name, post.uploader.avatar.url) == ("Some Page", AVATAR_URL)
    assert "gallery-dl https://www.facebook.com/photo/?fbid=901&set=a.555" in post_providers


def test_reduced_copy_stands_in_when_the_photo_page_fails(post_providers, monkeypatch) -> None:
    def no_photo_pages(self, url):
        raise ProviderError("gallery-dl: AuthRequired: You must be logged in")

    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", no_photo_pages)
    [image] = extract(POST_URL, want=["image"]).items

    assert (image.provider, image.width) == ("native", 526)
