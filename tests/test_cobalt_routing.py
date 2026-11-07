"""Milestone 6: the cobalt provider and the JSON routing config.

Runs offline: cobalt answers come from a fake instance, and yt-dlp from a saved info dict. Set
CONTENT_DL_LIVE=1 with CONTENT_DL_COBALT_URL (a self-hosted instance) and CONTENT_DL_COBALT_TEST_URL
(a public video post) to also run the live check from the milestone's "done when".
"""

import json
import os
from pathlib import Path

import pytest

from content_dl import ExtractionError, Item, Post, extract, result_from_json
from content_dl.cli import main
from content_dl.merge import merge
from content_dl.providers import CobaltInstance, CobaltProvider, GalleryDlProvider, ProviderError, YtDlpProvider
from content_dl.providers.cobalt import items_from_response
from content_dl.providers.native import http
from content_dl.router import (
    RouterError,
    default_config,
    detect_platform,
    load_config,
    providers_for,
    routes_for,
)

FIXTURES = Path(__file__).parent / "fixtures"
TIKTOK_URL = "https://www.tiktok.com/@someone/video/7430000000000000000"
COBALT = CobaltInstance("https://cobalt.example.com", api_key="test-key")
TUNNEL = {"status": "tunnel", "url": "https://cobalt.example.com/tunnel?id=abc", "filename": "tiktok_someone_7430.mp4"}


@pytest.fixture
def cobalt_server(monkeypatch):
    """A fake cobalt instance: records each request and answers with ``answer["body"]``."""
    requests: list[dict] = []
    answer = {"status": 200, "body": TUNNEL}

    def fake_post(url, payload, headers=None):
        requests.append({"url": url, "payload": payload, "headers": headers})
        return http.Response(status=answer["status"], url=url, text=json.dumps(answer["body"]))

    monkeypatch.setattr(http, "post_json", fake_post)
    return requests, answer


@pytest.fixture
def ytdlp_calls(monkeypatch):
    """yt-dlp serves a saved video; gallery-dl finds nothing (a TikTok video has no images)."""
    calls: list[str] = []
    info = json.loads((FIXTURES / "ytdlp_youtube_video.json").read_text(encoding="utf-8"))

    def fake_info(self, url):
        calls.append(url)
        return info

    def no_images(self, url):
        raise ProviderError("gallery-dl: no images here")

    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_info)
    monkeypatch.setattr(GalleryDlProvider, "fetch_messages", no_images)
    return calls


# Routing config

def test_shipped_config_matches_the_spec_table() -> None:
    assert routes_for("instagram")["video"] == ["yt-dlp", "cobalt", "gallery-dl"]
    assert routes_for("snapchat") == {
        "video": ["cobalt"],
        "images": ["cobalt"],
        "thumbnail": ["cobalt"],
        "avatar": [],
        "metadata": ["native"],
    }
    assert routes_for("example.com") == routes_for("unknown")
    assert detect_platform("https://vm.tiktok.com/abc") == "tiktok"


def test_override_disables_a_provider_on_one_platform() -> None:
    config = load_config({"disable": {"tiktok": ["yt-dlp"]}})
    assert config.routes_for("tiktok")["video"] == ["cobalt"]
    assert config.routes_for("tiktok")["metadata"] == []
    assert config.routes_for("x")["video"] == ["yt-dlp", "cobalt"]
    assert default_config().routes_for("tiktok")["video"] == ["yt-dlp", "cobalt"]


def test_override_disables_everywhere_and_applies_to_cookie_routes() -> None:
    config = load_config({"disable": {"*": ["gallery-dl"]}})
    assert config.routes_for("instagram", with_cookies=True)["images"] == ["yt-dlp", "cobalt"]
    assert "gallery-dl" not in providers_for("reddit", config=config)


def test_override_replaces_only_the_cells_it_names() -> None:
    config = load_config({"routes": {"x": {"images": ["cobalt"]}}})
    assert config.routes_for("x")["images"] == ["cobalt"]
    assert config.routes_for("x")["video"] == ["yt-dlp", "cobalt"]


def test_override_adds_a_platform_with_its_domains() -> None:
    config = load_config({"domains": {"Vimeo.com": "vimeo"}, "routes": {"vimeo": {"video": ["yt-dlp"]}}})
    assert detect_platform("https://player.vimeo.com/video/1", config) == "vimeo"
    assert config.routes_for("vimeo")["video"] == ["yt-dlp"]
    assert config.routes_for("vimeo")["images"] == []


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"routes": {"x": {"video": ["yt_dlp"]}}}, "unknown provider"),
        ({"routes": {"x": {"videos": ["yt-dlp"]}}}, "unknown column"),
        ({"disable": {"x": "yt-dlp"}}, "list of provider names"),
        ({"rotes": {}}, "unknown key"),
    ],
)
def test_invalid_override_is_rejected(override, message) -> None:
    with pytest.raises(RouterError, match=message):
        load_config(override)


def test_override_file_from_the_environment(tmp_path, monkeypatch) -> None:
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({"disable": {"tiktok": ["yt-dlp"]}}), encoding="utf-8")
    monkeypatch.setenv("CONTENT_DL_ROUTES", str(path))
    assert load_config().routes_for("tiktok")["video"] == ["cobalt"]

    monkeypatch.setenv("CONTENT_DL_ROUTES", str(tmp_path / "typo.json"))
    with pytest.raises(RouterError, match="missing file"):
        load_config()


def test_bad_override_file_reports_the_path(tmp_path) -> None:
    path = tmp_path / "routes.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RouterError, match="not valid JSON"):
        load_config(path)

    with pytest.raises(RouterError, match="couldn't read"):
        load_config(tmp_path / "missing.json")


# cobalt responses

def test_tunnel_becomes_one_video() -> None:
    [item] = items_from_response(TUNNEL)
    assert (item.index, item.media_type, item.ext, item.provider) == (1, "video", "mp4", "cobalt")
    assert item.formats is None


def test_redirect_with_an_audio_file_is_audio() -> None:
    [item] = items_from_response({"status": "redirect", "url": "https://cdn/x", "filename": "song.opus"})
    assert (item.media_type, item.ext) == ("audio", "opus")


def test_slideshow_picker_has_images_and_post_audio() -> None:
    items = items_from_response(
        {
            "status": "picker",
            "audio": "https://cobalt.example.com/tunnel?id=a",
            "audioFilename": "tiktok_audio.mp3",
            "picker": [
                {"type": "photo", "url": "https://cdn/1.jpeg"},
                {"type": "photo", "url": "https://cdn/2.jpeg"},
                {"type": "unknown", "url": "https://cdn/3"},
            ],
        }
    )
    assert [(i.index, i.media_type, i.ext) for i in items] == [(1, "image", "jpeg"), (2, "image", "jpeg"), (None, "audio", "mp3")]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"status": "error", "error": {"code": "error.api.content.video.unavailable"}}, "video.unavailable"),
        ({"status": "error", "error": {"code": "error.api.auth.key.missing"}}, "--cobalt-api-key"),
        ({"status": "local-processing", "type": "merge", "tunnel": ["a", "b"]}, "local processing"),
        ({"status": "tunnel"}, "without a url"),
        ({"status": "weird"}, "unexpected"),
    ],
)
def test_unusable_responses_raise(body, message) -> None:
    with pytest.raises(ProviderError, match=message):
        items_from_response(body)


def test_provider_sends_the_request_cobalt_expects(cobalt_server) -> None:
    requests, _ = cobalt_server
    result = CobaltProvider(instance=COBALT).extract(TIKTOK_URL, "tiktok", ["audio"])

    assert isinstance(result, Post)
    assert (result.platform, result.url, result.id, result.uploader) == ("tiktok", TIKTOK_URL, None, None)
    [request] = requests
    assert request["url"] == "https://cobalt.example.com/"
    assert request["headers"] == {"Authorization": "Api-Key test-key"}
    assert request["payload"]["url"] == TIKTOK_URL
    assert request["payload"]["downloadMode"] == "audio"
    assert request["payload"]["localProcessing"] == "disabled"


def test_provider_reports_http_errors_without_a_cobalt_body(cobalt_server) -> None:
    _, answer = cobalt_server
    answer.update(status=502, body={"message": "bad gateway"})
    with pytest.raises(ProviderError, match="HTTP 502"):
        CobaltProvider(instance=COBALT).extract(TIKTOK_URL, "tiktok")


def test_instance_comes_from_the_environment(monkeypatch) -> None:
    assert CobaltInstance.from_env() is None
    monkeypatch.setenv("CONTENT_DL_COBALT_URL", "http://localhost:9000")
    assert CobaltInstance.from_env() == CobaltInstance("http://localhost:9000")

    with pytest.raises(ValueError, match="http"):
        CobaltInstance("localhost:9000")


# Done when: turning off yt-dlp for a platform in the config makes cobalt serve that platform's video.

def test_yt_dlp_serves_video_by_default_and_cobalt_stays_idle(cobalt_server, ytdlp_calls) -> None:
    requests, _ = cobalt_server
    result = extract(TIKTOK_URL, cobalt=COBALT)

    assert ytdlp_calls == [TIKTOK_URL]
    assert requests == []
    assert {item.provider for item in result.items if item.media_type == "video"} == {"yt-dlp"}


def test_disabling_yt_dlp_in_the_config_makes_cobalt_serve_the_video(cobalt_server, ytdlp_calls) -> None:
    requests, _ = cobalt_server
    result = extract(TIKTOK_URL, routes={"disable": {"tiktok": ["yt-dlp"]}}, cobalt=COBALT)

    assert ytdlp_calls == []
    assert len(requests) == 1
    [video] = [item for item in result.items if item.media_type == "video"]
    assert (video.provider, video.url, video.ext) == ("cobalt", TUNNEL["url"], "mp4")
    assert result.platform == "tiktok"


def test_cli_with_a_routes_file_and_cobalt_url(tmp_path, capsysbinary, cobalt_server, ytdlp_calls) -> None:
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({"routes": {"tiktok": {"video": ["cobalt"], "thumbnail": [], "metadata": []}}}), encoding="utf-8")

    code = main(["--json-only", "--routes", str(routes), "--cobalt-url", "https://cobalt.example.com", TIKTOK_URL])

    assert code == 0
    result = result_from_json(capsysbinary.readouterr().out.decode("utf-8"))
    assert [(item.media_type, item.provider) for item in result.items] == [("video", "cobalt")]
    assert ytdlp_calls == []
    assert cobalt_server[0][0]["headers"] == {}


def test_without_an_instance_cobalt_is_skipped(ytdlp_calls) -> None:
    with pytest.raises(ExtractionError, match="no provider can handle tiktok"):
        extract(TIKTOK_URL, want=["video"], routes={"disable": {"tiktok": ["yt-dlp"]}})


def test_cli_rejects_a_bad_routes_file(tmp_path, capsys) -> None:
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({"routes": {"tiktok": {"video": ["cobalt2"]}}}), encoding="utf-8")
    assert main(["--json-only", "--routes", str(routes), TIKTOK_URL]) == 1
    assert "unknown provider" in capsys.readouterr().err


# Merger

def test_cobalt_copy_is_dropped_when_another_provider_has_the_media_type() -> None:
    video = Item(index=1, media_type="video", formats=[], provider="yt-dlp", extra={"media_id": "1"})
    tunnel = Item(index=1, media_type="video", url=TUNNEL["url"], ext="mp4", provider="cobalt")
    image = Item(index=1, media_type="image", url="https://cdn/1.jpg", provider="cobalt")
    results = {
        "yt-dlp": Post(platform="instagram", id="abc", items=[video]),
        "cobalt": Post(platform="instagram", items=[tunnel, image]),
    }

    post = merge(results, routes_for("instagram"))

    assert [(item.media_type, item.provider) for item in post.items] == [("video", "yt-dlp"), ("image", "cobalt")]
    assert post.id == "abc"


# Live

@pytest.mark.skipif(
    not (os.environ.get("CONTENT_DL_LIVE") and os.environ.get("CONTENT_DL_COBALT_URL") and os.environ.get(
        "CONTENT_DL_COBALT_TEST_URL")),
    reason="set CONTENT_DL_LIVE=1, CONTENT_DL_COBALT_URL and CONTENT_DL_COBALT_TEST_URL",
)
def test_live_cobalt_serves_video_with_yt_dlp_turned_off() -> None:
    url = os.environ["CONTENT_DL_COBALT_TEST_URL"]
    platform = detect_platform(url)
    result = extract(url, want=["video"], routes={"disable": {platform: ["yt-dlp"]}})

    assert any(item.media_type == "video" and item.provider == "cobalt" and item.url for item in result.items)
