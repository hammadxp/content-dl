"""Milestone 7: what content-dl needs to run inside the Seal fork's Python runtime.

The phone runs content-dl as a zipapp with Seal's own yt-dlp on PYTHONPATH, and passes yt-dlp
the JS runtime and ffmpeg that youtubedl-android ships. These tests cover that wiring offline.
Building and running the zipapp itself is checked by hand (see scripts/build_pyz.py).
"""

import importlib.util
import json
from pathlib import Path

import pytest

from content_dl.cli import main
from content_dl.providers import YtDlpProvider
from content_dl.providers.ytdlp import FFMPEG_LOCATION_ENV, JS_RUNTIMES_ENV

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = Path(__file__).parent.parent / "scripts" / "build_pyz.py"
VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

QJS = "/data/app/com.junkfood.seal/lib/arm64/libqjs.so"
FFMPEG = "/data/app/com.junkfood.seal/lib/arm64/libffmpeg.so"


def load_build_script():
    spec = importlib.util.spec_from_file_location("build_pyz", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_options_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(JS_RUNTIMES_ENV, f"quickjs:{QJS}")
    monkeypatch.setenv(FFMPEG_LOCATION_ENV, FFMPEG)

    options = YtDlpProvider().options
    assert options["js_runtimes"] == {"quickjs": {"path": QJS}}
    assert options["ffmpeg_location"] == FFMPEG


def test_without_host_settings_yt_dlp_keeps_its_defaults() -> None:
    options = YtDlpProvider().options
    assert "js_runtimes" not in options
    assert "ffmpeg_location" not in options


def test_cli_flags_reach_the_yt_dlp_provider(monkeypatch, capsys) -> None:
    # setenv first so monkeypatch restores the variables the CLI sets.
    monkeypatch.setenv(JS_RUNTIMES_ENV, "")
    monkeypatch.setenv(FFMPEG_LOCATION_ENV, "")
    seen: list[dict] = []
    info = json.loads((FIXTURES / "ytdlp_youtube_video.json").read_text(encoding="utf-8"))

    def fake_fetch(self, url):
        seen.append(dict(self.options))
        return info

    monkeypatch.setattr(YtDlpProvider, "fetch_info", fake_fetch)

    code = main(["--json-only", "--media-types", "video", "--js-runtimes", f"quickjs:{QJS}", "--ffmpeg-location", FFMPEG, VIDEO_URL])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["platform"] == "youtube"
    assert seen and seen[0]["js_runtimes"] == {"quickjs": {"path": QJS}}
    assert seen[0]["ffmpeg_location"] == FFMPEG


@pytest.mark.parametrize(("with_yt_dlp", "expected"), [(False, False), (True, True)])
def test_zipapp_leaves_yt_dlp_to_the_host(with_yt_dlp, expected) -> None:
    build = load_build_script()
    requirements = build.bundled_requirements(with_yt_dlp)
    names = [build.requirement_name(requirement) for requirement in requirements]

    assert ("yt-dlp" in names) is expected
    assert {"gallery-dl", "requests"} <= set(names)
    # Extras are dropped, so yt-dlp's curl-cffi extra (compiled, no Python 3.11 Android wheel) stays out.
    assert not any("[" in requirement for requirement in requirements)
