"""Optional user cookies: passed to every provider, and switching Instagram to logged-in routes."""

import json
from pathlib import Path

import pytest

from content_dl import Cookies, extract
from content_dl.cli import main
from content_dl.providers import GalleryDlProvider, YtDlpProvider
from content_dl.providers.gallerydl import config_entries
from content_dl.router import providers_for, routes_for

FIXTURES = Path(__file__).parent / "fixtures"
POST_URL = "https://www.instagram.com/p/DAsTXK3JbrA/"
AVATAR_URL = "https://cdn.example.com/avatar.jpg"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def cookie_file(tmp_path) -> str:
    path = tmp_path / "cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def recorded(monkeypatch):
    """Serve the saved carousel from both providers and record each run's cookie settings."""
    runs: list[tuple[str, object]] = []
    messages = load_fixture("gallerydl_instagram_carousel.json")
    info = load_fixture("ytdlp_instagram_carousel.json")

    def fake_messages(self, url):
        runs.append(("gallery-dl", self.cookies))
        if url.endswith("/avatar/"):
            return [(3, AVATAR_URL, {"username": "someone", "fullname": "Some One", "extension": "jpg"})]

        return messages

    def fake_info(self, url):
        cookie_keys = {key: self.options[key] for key in ("cookiefile", "cookiesfrombrowser") if key in self.options}
        runs.append(("yt-dlp", cookie_keys))
        return info

    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", fake_messages)
    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_info)
    return runs


# Cookies

def test_missing_cookie_file_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="cookies file not found"):
        Cookies(file=str(tmp_path / "nope.txt"))


def test_unsupported_browser_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported browser"):
        Cookies(browser="netscape")


def test_windows_profile_path_survives_the_split() -> None:
    assert Cookies(browser=r"firefox:C:\Users\me\profile").browser_spec() == ("firefox", r"C:\Users\me\profile")


# Routing

def test_cookies_put_gallery_dl_first_on_instagram() -> None:
    assert providers_for("instagram", with_cookies=True) == ["gallery-dl", "yt-dlp", "native"]
    assert routes_for("instagram", with_cookies=True)["video"] == routes_for("instagram")["video"]


def test_platforms_without_overrides_ignore_cookies() -> None:
    assert routes_for("youtube", with_cookies=True) == routes_for("youtube")


# Providers get the cookies

def test_logged_in_carousel_leads_with_gallery_dl(recorded, cookie_file) -> None:
    post = extract(POST_URL, cookies=Cookies(file=cookie_file))

    # gallery-dl runs twice: the post, then the uploader's avatar.
    assert [run[0] for run in recorded] == ["gallery-dl", "gallery-dl", "yt-dlp"]
    assert [(item.index, item.media_type, item.provider) for item in post.items] == [
        (1, "image", "gallery-dl"),
        (2, "video", "yt-dlp"),
        (2, "audio", "yt-dlp"),
        (2, "thumbnail", "yt-dlp"),
        (3, "image", "gallery-dl"),
    ]
    assert post.items[0].width == 1440
    assert (post.uploader.avatar.url, post.uploader.avatar.provider) == (AVATAR_URL, "gallery-dl")


def test_logged_in_avatar_is_skipped_when_not_requested(recorded, cookie_file) -> None:
    post = extract(POST_URL, media_types=["image"], cookies=Cookies(file=cookie_file))

    assert [run[0] for run in recorded] == ["gallery-dl"]
    assert post.uploader.avatar is None


def test_logged_in_profile_url_gets_its_avatar_from_gallery_dl(recorded, cookie_file) -> None:
    profile = extract("https://www.instagram.com/someone/", media_types=["avatar"], cookies=Cookies(file=cookie_file))

    assert (profile.username, profile.name, profile.avatar.provider) == ("someone", "Some One", "gallery-dl")


def test_every_provider_gets_the_cookies(recorded, cookie_file) -> None:
    cookies = Cookies(file=cookie_file)

    extract(POST_URL, cookies=cookies)

    assert dict(recorded) == {"gallery-dl": cookies, "yt-dlp": {"cookiefile": cookie_file}}


def test_browser_cookies_reach_yt_dlp(recorded) -> None:
    extract(POST_URL, media_types=["video"], cookies=Cookies(browser="firefox:work"))

    assert dict(recorded)["yt-dlp"] == {"cookiesfrombrowser": ("firefox", "work", None, None)}


def test_no_cookies_means_none_are_passed(recorded) -> None:
    extract(POST_URL)

    assert recorded == [("yt-dlp", {})]


def test_gallery_dl_cookies_last_one_run_only(cookie_file) -> None:
    from gallery_dl import config

    with config.apply(config_entries(Cookies(file=cookie_file))):
        assert config.get(("extractor",), "cookies") == cookie_file

    assert config.get(("extractor",), "cookies") is None


# CLI

def test_cli_passes_cookies(recorded, cookie_file, capsys) -> None:
    assert main(["--json-only", "--cookies", cookie_file, POST_URL]) == 0
    assert [run[0] for run in recorded] == ["gallery-dl", "gallery-dl", "yt-dlp"]


def test_cli_reports_a_missing_cookie_file(tmp_path, capsys) -> None:
    assert main(["--json-only", "--cookies", str(tmp_path / "nope.txt"), POST_URL]) == 1
    assert "cookies file not found" in capsys.readouterr().err


def test_profile_url_skips_video_and_image_providers(recorded, cookie_file) -> None:
    # Everything is requested, but once gallery-dl says it's a profile only the avatar is left.
    extract("https://www.instagram.com/someone/?srtk=x", cookies=Cookies(file=cookie_file))

    assert [run[0] for run in recorded] == ["gallery-dl"]


def test_profile_listing_is_headed_by_the_username(recorded, cookie_file, capsys) -> None:
    assert main(["--list-only", "--cookies", cookie_file, "https://www.instagram.com/someone/"]) == 0

    assert capsys.readouterr().out.startswith("[instagram] someone: 1 item(s)")
