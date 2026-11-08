"""Milestone 4: native providers for Threads posts and Instagram profile pictures.

Runs offline against saved pages: a Threads carousel post page (image, image, video), a Threads
profile page, and an Instagram ``web_profile_info`` response. Set CONTENT_DL_LIVE=1 with
CONTENT_DL_THREADS_URL (a public carousel post) or CONTENT_DL_INSTAGRAM_PROFILE_URL to also run
the live checks from the milestone's "done when".
"""

import json
import os
from pathlib import Path

import pytest

from content_dl import ExtractionError, Post, Profile, extract, result_from_json
from content_dl.cli import main
from content_dl.providers import GalleryDlProvider, NativeProvider, ProviderError, YtDlpProvider
from content_dl.providers.native import http, instagram, threads

FIXTURES = Path(__file__).parent / "fixtures"
THREADS_POST_URL = "https://www.threads.net/@someone/post/DThr3adsAbc"
THREADS_PROFILE_URL = "https://www.threads.com/@someone"
INSTAGRAM_POST_URL = "https://www.instagram.com/p/DAsTXK3JbrA/"
INSTAGRAM_PROFILE_URL = "https://www.instagram.com/someone/"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def threads_pages(monkeypatch):
    """Serve the saved Threads pages and record each URL fetched."""
    fetched: list[str] = []
    pages = {"post": read_fixture("threads_carousel.html"), "profile": read_fixture("threads_profile.html")}

    def fake_fetch(self, url, domain, headers=None):
        fetched.append(url)
        return pages[threads.parse_url(url)[0]]

    monkeypatch.setattr(NativeProvider, "fetch_page", fake_fetch)
    return fetched


@pytest.fixture
def instagram_profiles(monkeypatch):
    """Serve the saved web_profile_info response and record each username looked up."""
    looked_up: list[str] = []
    body = read_fixture("instagram_web_profile_info.json")

    def fake_lookup(self, username):
        looked_up.append(username)
        return body

    monkeypatch.setattr(NativeProvider, "fetch_instagram_profile", fake_lookup)
    return looked_up


@pytest.fixture
def instagram_post_providers(monkeypatch, instagram_profiles):
    """yt-dlp and gallery-dl serve the saved Instagram carousel; native serves the profile."""
    info = json.loads(read_fixture("ytdlp_instagram_carousel.json"))
    messages = json.loads(read_fixture("gallerydl_instagram_carousel.json"))
    monkeypatch.setattr(YtDlpProvider, "fetch_info", lambda self, url: info)
    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", lambda self, url: messages)
    return instagram_profiles


@pytest.fixture
def instagram_profile_providers(monkeypatch, instagram_profiles):
    """Neither wrapped tool reads an Instagram profile logged out, so both fail as they do live."""

    def ytdlp_fails(self, url):
        raise ProviderError("yt-dlp: Instagram profiles need a login")

    def gallery_dl_fails(self, url):
        raise ProviderError("gallery-dl: profiles and collections aren't supported, pass a single post URL")

    monkeypatch.setattr(YtDlpProvider, "fetch_info", ytdlp_fails)
    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", gallery_dl_fails)
    return instagram_profiles


# Threads page parsing

def test_threads_carousel_becomes_a_post() -> None:
    post = threads.post_from_page(read_fixture("threads_carousel.html"), THREADS_POST_URL)

    assert (post.platform, post.id, post.title, post.description) == ("threads", "DThr3adsAbc", None, "sunset at the beach")
    assert post.upload_date == "2026-10-30T18:22:00Z"
    assert post.url == "https://www.threads.com/@someone/post/DThr3adsAbc"
    assert [(item.index, item.media_type) for item in post.items] == [(1, "image"), (2, "image"), (3, "video"), (3, "thumbnail")]
    assert {item.provider for item in post.items} == {"native"}


def test_threads_items_use_the_largest_image() -> None:
    first, second, *_ = threads.post_from_page(read_fixture("threads_carousel.html"), THREADS_POST_URL).items

    assert (first.width, first.height, first.ext) == (1440, 1800, "webp")
    # The candidates aren't always sorted largest first.
    assert (second.width, second.height, second.ext) == (1080, 1350, "jpg")


def test_threads_video_has_a_direct_file_and_its_cover() -> None:
    items = threads.post_from_page(read_fixture("threads_carousel.html"), THREADS_POST_URL).items
    video, cover = items[2], items[3]

    assert video.to_dict() == {
        "index": 3,
        "media_type": "video",
        "url": "https://scontent.cdninstagram.com/o1/v/t16/f2/m84/AQPvid.mp4?_nc_cat=106&efg=abc&_nc_ht=x",
        "ext": "mp4",
        "width": 720,
        "height": 1280,
        "provider": "native",
        "extra": {"media_id": "3333333333333333303", "media_type": 2, "code": "DThrC3"},
    }
    assert (cover.media_type, cover.width, cover.height) == ("thumbnail", 640, 800)


def test_threads_post_uploader_is_filled() -> None:
    uploader = threads.post_from_page(read_fixture("threads_carousel.html"), THREADS_POST_URL).uploader

    assert (uploader.id, uploader.username, uploader.name) == ("123456", "someone", "Some One")
    assert uploader.url == "https://www.threads.com/@someone"
    # A post page only has the small avatar; its size comes from the URL's s150x150 resize step.
    assert uploader.avatar.url.startswith("https://scontent.cdninstagram.com/v/t51.82787-19/av_n.jpg?stp=dst-jpg_s150x150")
    assert (uploader.avatar.width, uploader.avatar.height) == (150, 150)


def test_threads_picks_the_post_by_its_code() -> None:
    reply_url = "https://www.threads.com/@friend/post/DThrReply01"

    reply = threads.post_from_page(read_fixture("threads_carousel.html"), reply_url)

    assert (reply.id, reply.description, reply.uploader.username, reply.items) == ("DThrReply01", "nice", "friend", [])


def test_threads_short_url_finds_the_post() -> None:
    post = threads.post_from_page(read_fixture("threads_carousel.html"), "https://www.threads.com/t/DThr3adsAbc")

    assert post.uploader.username == "someone"


def test_threads_missing_post_is_a_provider_error() -> None:
    with pytest.raises(ProviderError, match="no data for this post"):
        threads.post_from_page("<html>login</html>", THREADS_POST_URL)


def test_threads_profile_has_the_largest_hd_avatar() -> None:
    profile = threads.profile_from_page(read_fixture("threads_profile.html"), THREADS_PROFILE_URL)

    assert (profile.id, profile.username, profile.name) == ("123456", "someone", "Some One")
    assert (profile.avatar.width, profile.avatar.height, profile.avatar.ext) == (640, 640, "jpg")
    assert profile.items == []


def test_threads_post_sharing_an_instagram_post_uses_its_media() -> None:
    image = {"image_versions2": {"candidates": [{"url": "https://cdn.example/a.jpg", "width": 2000, "height": 2666}]}}
    post = {
        "media_type": 19,
        "image_versions2": {"candidates": []},
        "text_post_app_info": {"linked_inline_media": {"media_type": 8, "carousel_media": [image, image]}},
    }

    items = threads.items_from_post(post)

    assert [(item.index, item.media_type, item.width) for item in items] == [(1, "image", 2000), (2, "image", 2000)]


def test_threads_share_link_resolves_to_the_clean_post_url(monkeypatch) -> None:
    target = "https://www.threads.com/@someone/post/DfaNgswkQPG"
    monkeypatch.setattr(http, "get", lambda url, **kwargs: http.Response(200, f"{target}?xmt=abc&slof=1", ""))

    assert NativeProvider().resolve_threads_url("https://www.threads.com/share/BALHonL4O-") == target
    assert NativeProvider().resolve_threads_url(target) == target


def test_threads_rejects_other_urls() -> None:
    with pytest.raises(ProviderError, match="not a Threads post or profile URL"):
        threads.parse_url("https://www.threads.com/search?q=beach")


# Instagram profile pictures

def test_instagram_response_becomes_a_profile() -> None:
    profile = instagram.profile_from_response(read_fixture("instagram_web_profile_info.json"))

    assert profile.to_dict() == {
        "type": "profile",
        "platform": "instagram",
        "id": "123456",
        "username": "someone",
        "name": "Some One",
        "url": "https://www.instagram.com/someone/",
        "avatar": {
            "media_type": "avatar",
            "url": "https://scontent.cdninstagram.com/v/t51.2885-19/av_n.jpg?stp=dst-jpg_s320x320&_nc_ht=x",
            "ext": "jpg",
            "provider": "native",
        },
        "items": [],
    }


def test_instagram_avatar_size_comes_from_hd_info_only() -> None:
    user = {"hd_profile_pic_url_info": {"url": "https://cdn/a.jpg", "width": 1080, "height": 1080}}

    avatar = instagram.avatar_from_user(user)

    assert (avatar.width, avatar.height) == (1080, 1080)


def test_instagram_login_page_is_a_provider_error() -> None:
    with pytest.raises(ProviderError, match="try --cookies"):
        instagram.profile_from_response("<!DOCTYPE html><html>Login</html>")


def test_instagram_unknown_user_is_a_provider_error() -> None:
    with pytest.raises(ProviderError, match="no such profile"):
        instagram.profile_from_response('{"data": {"user": null}, "status": "ok"}')


def test_instagram_headers_carry_the_csrf_cookie() -> None:
    headers = instagram.request_headers("someone", {"csrftoken": "abc", "sessionid": "s"})

    assert (headers["X-IG-App-ID"], headers["X-CSRFToken"]) == (instagram.APP_ID, "abc")
    assert "X-CSRFToken" not in instagram.request_headers("someone", {})


@pytest.mark.parametrize(
    ("status", "message"),
    [(404, "refused the profile lookup"), (429, "rate limiting"), (500, "HTTP 500")],
)
def test_instagram_http_errors_are_provider_errors(monkeypatch, status, message) -> None:
    monkeypatch.setattr(http, "get", lambda url, **kwargs: http.Response(status=status, url=url, text=""))

    with pytest.raises(ProviderError, match=message):
        NativeProvider().fetch_instagram_profile("someone")


def test_native_sends_only_the_sites_cookies(monkeypatch, tmp_path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t2000000000\tsessionid\tsecret\n"
        ".instagram.com\tTRUE\t/\tTRUE\t2000000000\tcsrftoken\ttoken\n"
        ".example.com\tTRUE\t/\tFALSE\t2000000000\tother\tx\n",
        encoding="utf-8",
    )
    sent: list[tuple[dict, dict]] = []

    def fake_get(url, headers=None, cookies=None):
        sent.append((headers, cookies))
        return http.Response(status=200, url=url, text=read_fixture("instagram_web_profile_info.json"))

    monkeypatch.setattr(http, "get", fake_get)
    from content_dl import Cookies

    NativeProvider(cookies=Cookies(file=str(cookie_file))).fetch_instagram_profile("someone")

    headers, cookies = sent[0]
    assert cookies == {"sessionid": "secret", "csrftoken": "token"}
    assert headers["X-CSRFToken"] == "token"


# extract() on Threads (milestone 4 "done when")

def test_threads_carousel_returns_a_complete_post(threads_pages) -> None:
    post = extract(THREADS_POST_URL)

    assert isinstance(post, Post)
    assert (post.id, post.description, post.upload_date) == ("DThr3adsAbc", "sunset at the beach", "2026-10-30T18:22:00Z")
    assert (post.uploader.username, post.uploader.name) == ("someone", "Some One")
    # The post page only has the 150px avatar, so the uploader's profile page supplies the HD one.
    assert (post.uploader.avatar.width, post.uploader.avatar.height) == (640, 640)
    assert [(item.index, item.media_type) for item in post.items] == [(1, "image"), (2, "image"), (3, "video"), (3, "thumbnail")]
    # Every Threads column is native, so each page is fetched once.
    assert threads_pages == [THREADS_POST_URL, "https://www.threads.com/@someone"]


def test_threads_post_keeps_the_small_avatar_when_the_profile_fails(monkeypatch) -> None:
    def fetch(self, url, domain, headers=None):
        if threads.parse_url(url)[0] == "profile":
            raise ProviderError("native: threads.com answered HTTP 429")

        return read_fixture("threads_carousel.html")

    monkeypatch.setattr(NativeProvider, "fetch_page", fetch)
    post = extract(THREADS_POST_URL)

    assert (post.uploader.avatar.width, post.uploader.avatar.height) == (150, 150)


def test_threads_profile_url_returns_a_profile(threads_pages) -> None:
    profile = extract(THREADS_PROFILE_URL)

    assert isinstance(profile, Profile)
    assert (profile.username, profile.avatar.width) == ("someone", 640)


def test_threads_media_types_filter_items(threads_pages) -> None:
    post = extract(THREADS_POST_URL, media_types=["video"])

    assert [item.media_type for item in post.items] == ["video"]
    assert post.uploader.avatar is None
    # Without the avatar requested, the profile page isn't fetched.
    assert threads_pages == [THREADS_POST_URL]


def test_threads_failure_is_an_extraction_error(monkeypatch) -> None:
    def blocked(self, url, domain, headers=None):
        raise ProviderError("native: threads.com answered HTTP 429")

    monkeypatch.setattr(NativeProvider, "fetch_page", blocked)

    with pytest.raises(ExtractionError, match="HTTP 429"):
        extract(THREADS_POST_URL)


def test_cli_dumps_a_threads_post(threads_pages, capsys) -> None:
    assert main(["--json-only", THREADS_POST_URL]) == 0

    post = result_from_json(capsys.readouterr().out)
    assert [item.media_type for item in post.items].count("image") == 2


# extract() on Instagram (milestone 4 "done when")

def test_instagram_profile_url_returns_a_complete_profile(instagram_profile_providers) -> None:
    profile = extract(INSTAGRAM_PROFILE_URL)

    assert isinstance(profile, Profile)
    assert (profile.id, profile.username, profile.name) == ("123456", "someone", "Some One")
    assert profile.url == "https://www.instagram.com/someone/"
    assert (profile.avatar.media_type, profile.avatar.provider) == ("avatar", "native")
    assert profile.items == []
    assert instagram_profile_providers == ["someone"]


def test_instagram_post_gets_the_uploader_avatar_from_native(instagram_post_providers) -> None:
    post = extract(INSTAGRAM_POST_URL)

    assert (post.uploader.id, post.uploader.username, post.uploader.name) == ("123456", "someone", "Some One")
    assert (post.uploader.avatar.provider, post.uploader.avatar.ext) == ("native", "jpg")
    # The username came from yt-dlp's metadata, because a /p/ URL doesn't contain it.
    assert instagram_post_providers == ["someone"]
    # Native only adds the avatar; the items are still yt-dlp's.
    assert {item.provider for item in post.items} == {"yt-dlp"}


def test_instagram_post_skips_the_avatar_lookup_when_not_requested(instagram_post_providers) -> None:
    post = extract(INSTAGRAM_POST_URL, media_types=["images"])

    assert post.uploader.avatar is None
    assert instagram_post_providers == []


def test_instagram_avatar_failure_keeps_the_post(monkeypatch, instagram_post_providers) -> None:
    def refused(self, username):
        raise ProviderError("native: Instagram refused the profile lookup (HTTP 404); it may need --cookies")

    monkeypatch.setattr(NativeProvider, "fetch_instagram_profile", refused)

    post = extract(INSTAGRAM_POST_URL)

    assert post.uploader.username == "someone"
    assert post.uploader.avatar is None


def test_forcing_native_on_a_post_needs_a_username(instagram_profiles) -> None:
    with pytest.raises(ExtractionError, match="needs the uploader's username"):
        extract(INSTAGRAM_POST_URL, provider="native")


def test_native_is_skipped_on_platforms_it_does_not_cover() -> None:
    # native has no YouTube extractor, so a row that lists only native leaves nothing to run.
    only_native = {column: ["native"] for column in ("video", "images", "thumbnail", "avatar", "metadata")}
    with pytest.raises(ExtractionError, match="no provider can handle youtube"):
        extract("https://www.youtube.com/watch?v=abc", routes={"routes": {"youtube": only_native}})


# Live checks

@pytest.mark.skipif(
    os.environ.get("CONTENT_DL_LIVE") != "1" or not os.environ.get("CONTENT_DL_THREADS_URL"),
    reason="set CONTENT_DL_LIVE=1 and CONTENT_DL_THREADS_URL to a public carousel post",
)
def test_live_threads_carousel() -> None:
    post = extract(os.environ["CONTENT_DL_THREADS_URL"])

    assert post.uploader and post.uploader.username and post.uploader.avatar and post.upload_date
    media = [item for item in post.items if item.media_type in ("image", "video")]
    assert len({item.index for item in media}) == len(media) > 1


@pytest.mark.skipif(
    os.environ.get("CONTENT_DL_LIVE") != "1" or not os.environ.get("CONTENT_DL_INSTAGRAM_PROFILE_URL"),
    reason="set CONTENT_DL_LIVE=1 and CONTENT_DL_INSTAGRAM_PROFILE_URL to a public profile",
)
def test_live_instagram_profile() -> None:
    profile = extract(os.environ["CONTENT_DL_INSTAGRAM_PROFILE_URL"])

    assert isinstance(profile, Profile)
    assert profile.id and profile.username and profile.avatar and profile.avatar.url
