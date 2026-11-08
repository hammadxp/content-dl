"""Reddit: posts from saved embed pages, share links resolved before routing, profiles from about.json."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from content_dl import extract
from content_dl.download import ACCEPT_BY_MEDIA_TYPE, download
from content_dl.extract import ExtractionError
from content_dl.providers import GalleryDlProvider, YtDlpProvider
from content_dl.providers.base import Cookies, ProviderError
from content_dl.providers.native import NativeProvider, http, reddit
from content_dl.schema import Item, Post, Profile

FIXTURES = Path(__file__).parent / "fixtures"
SHARE_LINK = "https://www.reddit.com/r/ApplePhotos/s/LFh9KV4XZ9"
GALLERY_URL = "https://www.reddit.com/r/ApplePhotos/comments/1xl6f0e/how_does_this_guy_get_his_iphone_photos_to_look/"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf8")


def test_single_image_post_links_the_full_size_original():
    post = reddit.post_from_embed(page("reddit_embed_image.html"), "https://www.reddit.com/r/duolingo/comments/1xpk9zh/")

    assert post.id == "1xpk9zh"
    assert post.title == "We lost buff Duo in a bowl of peas! Where is he? 💚"
    assert post.uploader.username == "kiki_at_duolingo"
    assert post.upload_date == "2026-11-06T18:29:12.899000Z"
    assert [(item.media_type, item.url, item.ext) for item in post.items] == [
        ("image", "https://i.redd.it/62eelqblc10i1.jpeg", "jpg")
    ]


def test_gallery_keeps_every_image_in_order():
    post = reddit.post_from_embed(page("reddit_embed_gallery.html"), GALLERY_URL)

    assert post.url == GALLERY_URL
    assert [(item.index, item.url) for item in post.items] == [
        (1, "https://i.redd.it/k3eebnjov0zh1.jpeg"),
        (2, "https://i.redd.it/4eyeonjov0zh1.jpeg"),
    ]


def test_video_post_takes_the_streams_it_is_given_and_keeps_the_poster():
    html = page("reddit_embed_video.html")
    player = reddit.player_attributes(html)
    assert player["src"].startswith("https://v.redd.it/fsbjgajo770i1/HLSPlaylist.m3u8?")

    video = Item(index=1, media_type="video", formats=[{"url": "https://v.redd.it/x.m3u8"}], provider="native")
    post = reddit.post_from_embed(html, "https://www.reddit.com/r/pakistan/comments/1xq7awe/", video_items=[video])

    assert [item.media_type for item in post.items] == ["video", "thumbnail"]
    assert post.items[1].url.startswith("https://external-preview.redd.it/")


def test_blocked_page_is_reported_not_parsed():
    with pytest.raises(ProviderError, match="blocked"):
        reddit.post_from_embed(page("reddit_blocked.html"), GALLERY_URL)


def test_share_link_is_resolved_before_routing(monkeypatch):
    calls: list[str] = []

    def fake_get(url, headers=None, cookies=None):
        calls.append(url)
        if url == SHARE_LINK:
            return http.Response(status=200, url=GALLERY_URL + "?share_id=abc&utm_source=share", text="")

        if url == "https://embed.reddit.com/r/ApplePhotos/comments/1xl6f0e/":
            return http.Response(status=200, url=url, text=page("reddit_embed_gallery.html"))

        raise AssertionError(f"unexpected request {url}")

    def no_tool(self, url, *args, **kwargs):
        raise ProviderError("blocked by network security")

    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(YtDlpProvider, "extract", no_tool)
    monkeypatch.setattr(GalleryDlProvider, "extract", no_tool)

    post = extract(SHARE_LINK)

    assert isinstance(post, Post)
    assert len([item for item in post.items if item.media_type == "image"]) == 2
    assert calls[:2] == [SHARE_LINK, "https://embed.reddit.com/r/ApplePhotos/comments/1xl6f0e/"]


def test_profile_without_cookies_asks_for_them(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, headers=None, cookies=None: http.Response(403, url, "blocked"))
    monkeypatch.setattr(GalleryDlProvider, "extract", lambda self, *a, **k: (_ for _ in ()).throw(ProviderError("no")))
    monkeypatch.setattr(YtDlpProvider, "extract", lambda self, *a, **k: (_ for _ in ()).throw(ProviderError("no")))

    with pytest.raises(ExtractionError, match="--cookies"):
        extract("https://www.reddit.com/user/spez/")


def test_profile_and_community_icons_with_cookies(monkeypatch, tmp_path):
    bodies = {
        "https://www.reddit.com/user/spez/about.json": '{"data": {"name": "spez", "id": "1w72",'
        ' "snoovatar_img": "https://i.redd.it/snoovatar/avatars/a.png", "subreddit": {"title": "spez"}}}',
        "https://www.reddit.com/r/duolingo/about.json": '{"data": {"display_name": "duolingo", "id": "t5_1",'
        ' "title": "Duolingo", "community_icon": "https://styles.redditmedia.com/t5_1/icon.png?width=256&amp;s=x"}}',
    }
    monkeypatch.setattr(http, "get", lambda url, headers=None, cookies=None: http.Response(200, url, bodies[url]))
    (tmp_path / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    native = NativeProvider(Cookies(file=str(tmp_path / "cookies.txt")))
    monkeypatch.setattr(native, "site_cookies", lambda domain: {"reddit_session": "x"})

    user = native.extract("https://www.reddit.com/user/spez/", "reddit")
    community = native.extract("https://www.reddit.com/r/duolingo/", "reddit")

    assert isinstance(user, Profile) and user.avatar.url == "https://i.redd.it/snoovatar/avatars/a.png"
    assert community.name == "Duolingo"
    assert community.avatar.url == "https://styles.redditmedia.com/t5_1/icon.png"


def test_images_are_requested_like_an_img_tag_and_a_web_page_is_not_saved(monkeypatch, tmp_path):
    sent: list[dict] = []

    def fake_stream(url, headers=None, cookies=None):
        sent.append(headers or {})
        accepts_images = (headers or {}).get("Accept", "").startswith("image/")
        content_type = "image/jpeg" if accepts_images and "good" in url else "text/html"
        yield http.Stream(url=url, content_type=content_type, chunks=iter([b"data"]))

    monkeypatch.setattr(http, "stream", contextmanager(fake_stream))
    items = [
        Item(index=1, media_type="image", url="https://i.redd.it/good.jpeg", ext="jpg"),
        Item(index=2, media_type="image", url="https://i.redd.it/viewer.jpeg", ext="jpg"),
    ]
    saved, refused = download(Post(platform="reddit", id="p", items=items), output=str(tmp_path / "%(index)s.%(ext)s"))

    assert sent[0]["Accept"] == ACCEPT_BY_MEDIA_TYPE["image"]
    assert saved.status == "saved"
    assert refused.status == "failed" and "web page" in refused.error
    assert not (tmp_path / "2.jpg").exists()
