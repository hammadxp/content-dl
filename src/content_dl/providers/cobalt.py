"""cobalt provider: asks a self-hosted cobalt instance (https://github.com/imputnet/cobalt) for media URLs.

cobalt returns a working file URL when the other providers fail, but almost no metadata, so
its Post carries only the URL it was asked about and the routing table never makes it the
metadata source. It runs only when an instance is configured (``--cobalt-url`` or
``CONTENT_DL_COBALT_URL``); the public instances use bot protection and aren't meant for
other projects.

The user's cookies are not sent: cobalt has no request field for them, and a remote server
shouldn't receive a user's session. A cobalt instance reads its own cookies file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from content_dl.providers.base import Cookies, ProviderError
from content_dl.providers.common import ext_from_url
from content_dl.providers.native import http
from content_dl.schema import Item, ItemKind, Post, Result

NAME = "cobalt"

URL_ENV = "CONTENT_DL_COBALT_URL"
API_KEY_ENV = "CONTENT_DL_COBALT_API_KEY"

AUDIO_EXTS = frozenset({"mp3", "ogg", "opus", "wav", "m4a", "aac", "flac"})
IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "webp", "gif", "heic"})
PICKER_KINDS: dict[str, ItemKind] = {"photo": "image", "video": "video", "gif": "video"}


@dataclass(frozen=True)
class CobaltInstance:
    """Where cobalt runs. ``api_key`` is sent as ``Authorization: Api-Key <key>``.

    >>> CobaltInstance("https://cobalt.example.com/").endpoint
    'https://cobalt.example.com/'
    >>> CobaltInstance("https://cobalt.example.com", api_key="abc").headers()
    {'Authorization': 'Api-Key abc'}
    """

    url: str
    api_key: str | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"cobalt instance must be an http(s) URL: {self.url!r}")

    @property
    def endpoint(self) -> str:
        return self.url.rstrip("/") + "/"

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}

        # A full header value ("Bearer <jwt>") is passed as given.
        key = self.api_key.strip()
        return {"Authorization": key if " " in key else f"Api-Key {key}"}

    @classmethod
    def from_env(cls) -> CobaltInstance | None:
        url = os.environ.get(URL_ENV, "").strip()
        return cls(url, os.environ.get(API_KEY_ENV) or None) if url else None


def request_body(url: str, want: list[str] | None = None) -> dict[str, Any]:
    """The POST body: best quality, no transcoding, and no client-side processing.

    >>> request_body("https://x.com/a/status/1", ["audio"])["downloadMode"]
    'audio'
    """
    audio_only = want is not None and set(want) <= {"audio"}
    return {
        "url": url,
        "downloadMode": "audio" if audio_only else "auto",
        "videoQuality": "max",
        "audioFormat": "best",
        "filenameStyle": "basic",
        # INFO: "local-processing" responses need the client to merge or convert the tunnels
        # with ffmpeg, which content-dl leaves to yt-dlp; ask for finished files instead.
        "localProcessing": "disabled",
        # X GIFs stay mp4 videos rather than being converted to .gif files.
        "convertGif": False,
    }


def _ext(filename: str | None, url: str) -> str | None:
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext.isalnum() and len(ext) <= 5:
            return ext

    return ext_from_url(url)


def _kind_for_ext(ext: str | None) -> ItemKind:
    if ext in AUDIO_EXTS:
        return "audio"

    return "image" if ext in IMAGE_EXTS else "video"


def items_from_response(data: Mapping[str, Any]) -> list[Item]:
    """Turn a tunnel, redirect or picker response into items.

    A picker entry's ``thumb`` becomes a thumbnail at the entry's index; a slideshow's
    background ``audio`` becomes an audio item without an index, since it belongs to the post.

    >>> [(i.index, i.kind, i.ext) for i in items_from_response(
    ...     {"status": "picker", "picker": [{"type": "photo", "url": "https://c/1.jpg"},
    ...      {"type": "video", "url": "https://c/2.mp4", "thumb": "https://c/2.jpg"}]})]
    [(1, 'image', 'jpg'), (2, 'video', 'mp4'), (2, 'thumbnail', 'jpg')]
    """
    status = data.get("status")
    if status in ("tunnel", "redirect"):
        url = data.get("url")
        if not url:
            raise ProviderError(f"cobalt: {status} response without a url")

        ext = _ext(data.get("filename"), url)
        extra = {"status": status, "filename": data.get("filename")}
        return [Item(index=1, kind=_kind_for_ext(ext), url=url, ext=ext, provider=NAME, extra=_compact(extra))]

    if status == "picker":
        items: list[Item] = []
        for index, entry in enumerate(data.get("picker") or [], start=1):
            url = entry.get("url")
            kind = PICKER_KINDS.get(entry.get("type", ""))
            if not url or kind is None:
                continue

            extra = {"status": status, "type": entry.get("type")}
            items.append(Item(index=index, kind=kind, url=url, ext=ext_from_url(url), provider=NAME, extra=extra))
            if entry.get("thumb") and kind == "video":
                thumb = entry["thumb"]
                items.append(Item(index=index, kind="thumbnail", url=thumb, ext=ext_from_url(thumb), provider=NAME))

        if data.get("audio"):
            audio = data["audio"]
            ext = _ext(data.get("audioFilename"), audio)
            extra = {"status": status, "filename": data.get("audioFilename")}
            items.append(Item(kind="audio", url=audio, ext=ext, provider=NAME, extra=_compact(extra)))

        return items

    if status == "local-processing":
        raise ProviderError(
            f"cobalt: the instance wants the client to {data.get('type', 'process')} the file itself"
            " (local processing), which content-dl doesn't do"
        )

    if status == "error":
        error = data.get("error") or {}
        code = error.get("code", "unknown error")
        hint = " (set an API key with --cobalt-api-key)" if ".auth." in code else ""
        raise ProviderError(f"cobalt: {code}{hint}")

    raise ProviderError(f"cobalt: unexpected response status {status!r}")


def _compact(extra: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in extra.items() if value is not None}


class CobaltProvider:
    name = NAME
    # cobalt's services among the platforms content-dl routes to it. Threads isn't one.
    platforms = frozenset({"youtube", "instagram", "x", "tiktok", "reddit", "snapchat"})

    def __init__(self, cookies: Cookies | None = None, instance: CobaltInstance | None = None) -> None:
        # cookies are accepted like every provider's, and deliberately not forwarded (see module docstring).
        self.instance = instance or CobaltInstance.from_env()

    def fetch(self, url: str, want: list[str] | None = None) -> dict[str, Any]:
        if self.instance is None:
            raise ProviderError(f"cobalt: no instance configured (set --cobalt-url or {URL_ENV})")

        response = http.post_json(self.instance.endpoint, request_body(url, want), headers=self.instance.headers())
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as error:
            raise ProviderError(f"cobalt: HTTP {response.status} with a body that isn't JSON") from error

        if not isinstance(data, dict):
            raise ProviderError(f"cobalt: HTTP {response.status} with an unexpected body")

        # Errors come back as JSON with a 4xx status; anything else non-2xx without one is reported by status.
        if response.status >= 400 and data.get("status") != "error":
            raise ProviderError(f"cobalt: HTTP {response.status}")

        return data

    def extract(
        self, url: str, platform: str, want: list[str] | None = None, known: Mapping[str, Result] | None = None
    ) -> Result:
        items = items_from_response(self.fetch(url, want))
        if not items:
            raise ProviderError("cobalt: nothing to download at this URL")

        # INFO: cobalt gives no id, caption, date or uploader; the merger fills them from the
        # metadata providers, and a field nobody has stays null.
        return Post(platform=platform, url=url, items=items)
