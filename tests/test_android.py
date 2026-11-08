"""Milestone 7: what content-dl needs to run inside Markhor's Python runtime.

The phone runs content-dl as a zipapp that bundles yt-dlp and gallery-dl, and passes yt-dlp the
JS runtime and ffmpeg that youtubedl-android ships. These tests cover that wiring offline.
Building and running the zipapp itself is checked by hand (see scripts/build_pyz.py).
"""

import importlib.util
import json
from pathlib import Path


from content_dl.cli import main
from content_dl.providers import YtDlpProvider
from content_dl.providers.ytdlp import FFMPEG_LOCATION_ENV, JS_RUNTIMES_ENV

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = Path(__file__).parent.parent / "scripts" / "build_pyz.py"
VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

QJS = "/data/app/com.hammadxp.markhor/lib/arm64/libqjs.so"
FFMPEG = "/data/app/com.hammadxp.markhor/lib/arm64/libffmpeg.so"


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


def test_zipapp_bundles_every_tool_at_its_pinned_version() -> None:
    build = load_build_script()
    pinned = build.pinned_versions(build.PINNED_FILE.read_text(encoding="utf-8"))
    requirements = build.bundled_requirements()

    # The phone gets the same yt-dlp and gallery-dl as the desktop executables of the same release,
    # and yt-dlp updates on the phone only through content-dl releases.
    for name in ("yt-dlp", "gallery-dl", "requests"):
        assert f"{name}=={pinned[name]}" in requirements
    # Extras are dropped, so yt-dlp's curl-cffi extra (compiled, no Python 3.11 Android wheel) stays out.
    assert not any("[" in requirement for requirement in requirements)
