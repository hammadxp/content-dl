"""Keep the offline suite offline: native providers and the downloader can't reach the network outside live tests.

Live tests are named ``test_live_*`` and are skipped unless CONTENT_DL_LIVE=1.
"""

import pytest

from content_dl.providers import ProviderError
from content_dl.providers.native import http


@pytest.fixture(autouse=True)
def offline_native_http(request, monkeypatch):
    if request.node.name.startswith("test_live_"):
        return

    def no_network(url, **kwargs):
        raise ProviderError(f"native: network is off in tests ({url})")

    monkeypatch.setattr(http, "get", no_network)
    monkeypatch.setattr(http, "stream", no_network)
