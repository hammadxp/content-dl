"""Keep the offline suite offline and independent of the machine's own config.

Native providers and the downloader can't reach the network outside live tests, and the user's
routing override file and content-dl env vars are ignored.

Live tests are named ``test_live_*`` and are skipped unless CONTENT_DL_LIVE=1.
"""

from pathlib import Path

import pytest

from content_dl.providers import ProviderError
from content_dl import router
from content_dl.providers.native import http


@pytest.fixture(autouse=True)
def isolated_config(request, monkeypatch):
    monkeypatch.setattr(router, "config_folder_file", lambda: Path("/nonexistent/content-dl/routes.json"))
    if request.node.name.startswith("test_live_"):
        return

    for name in (
        "CONTENT_DL_ROUTES",
        "CONTENT_DL_JS_RUNTIMES",
        "CONTENT_DL_FFMPEG_LOCATION",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def offline_native_http(request, monkeypatch):
    if request.node.name.startswith("test_live_"):
        return

    def no_network(url, **kwargs):
        raise ProviderError(f"native: network is off in tests ({url})")

    monkeypatch.setattr(http, "get", no_network)
    monkeypatch.setattr(http, "stream", no_network)
