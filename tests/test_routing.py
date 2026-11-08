"""The JSON routing config: shipped defaults, overrides and how the CLI reports a bad one."""

import json

import pytest

from content_dl.cli import main
from content_dl.router import (
    RouterError,
    default_config,
    detect_platform,
    load_config,
    providers_for,
    routes_for,
)

TIKTOK_URL = "https://www.tiktok.com/@someone/video/7430000000000000000"


# Routing config

def test_shipped_config_matches_the_spec_table() -> None:
    assert routes_for("instagram")["video"] == ["yt-dlp", "gallery-dl"]
    assert routes_for("snapchat") == {
        "video": ["native", "gallery-dl"],
        "images": [],
        "thumbnail": ["native", "gallery-dl"],
        "avatar": ["native"],
        "metadata": ["native", "gallery-dl"],
    }
    assert routes_for("example.com") == routes_for("unknown")
    assert detect_platform("https://vm.tiktok.com/abc") == "tiktok"


def test_override_disables_a_provider_on_one_platform() -> None:
    config = load_config({"disable": {"tiktok": ["yt-dlp"]}})
    assert config.routes_for("tiktok")["video"] == []
    assert config.routes_for("tiktok")["metadata"] == ["gallery-dl"]
    assert config.routes_for("x")["video"] == ["yt-dlp"]
    assert default_config().routes_for("tiktok")["video"] == ["yt-dlp"]


def test_override_disables_everywhere_and_applies_to_cookie_routes() -> None:
    config = load_config({"disable": {"*": ["gallery-dl"]}})
    assert config.routes_for("instagram", with_cookies=True)["images"] == ["yt-dlp"]
    assert "gallery-dl" not in providers_for("reddit", config=config)


def test_override_replaces_only_the_cells_it_names() -> None:
    config = load_config({"routes": {"x": {"images": ["yt-dlp", "gallery-dl"]}}})
    assert config.routes_for("x")["images"] == ["yt-dlp", "gallery-dl"]
    assert config.routes_for("x")["video"] == ["yt-dlp"]


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
    assert load_config().routes_for("tiktok")["video"] == []

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


# CLI

def test_cli_rejects_a_bad_routes_file(tmp_path, capsys) -> None:
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({"routes": {"tiktok": {"video": ["cobalt"]}}}), encoding="utf-8")
    assert main(["--json-only", "--routes", str(routes), TIKTOK_URL]) == 1
    assert "unknown provider" in capsys.readouterr().err
