"""content-dl's output format: Post, Profile and Item.

Every extraction returns either a Post or a Profile, and both hold a list of Items.
Providers translate their own output into these classes; nothing downstream of the
merger sees yt-dlp or gallery-dl data except inside ``Item.extra``.

Serialisation rules (chosen so the SPEC.md examples round-trip unchanged):

- Post and Profile always write every field. A value the platform doesn't expose is ``null``.
- Item writes only the fields that are set, so a thumbnail doesn't carry ``duration: null``
  and a video without a direct URL doesn't carry ``url: null``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
import re
from datetime import date, datetime, timezone
from typing import Any, Literal, Union, get_args

MediaType = Literal["video", "audio", "image", "thumbnail", "avatar"]
MEDIA_TYPES: frozenset[str] = frozenset(get_args(MediaType))

DAY_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")

JsonDict = dict[str, Any]


class SchemaError(ValueError):
    """Raised when data doesn't match content-dl's format."""


def to_utc_iso(value: int | float | str | datetime | None) -> str | None:
    """Convert a timestamp to the UTC ISO 8601 form used in every result.

    >>> to_utc_iso(1793384520)
    '2026-10-30T18:22:00Z'
    >>> to_utc_iso("2026-10-30T20:22:00+02:00")
    '2026-10-30T18:22:00Z'
    >>> to_utc_iso("2005-04-24")
    '2005-04-24'
    >>> to_utc_iso(None) is None
    True

    Naive datetimes and strings without an offset are treated as UTC. A day without a time
    (``2005-04-24``) stays day-only, because adding midnight would invent a time of day.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise SchemaError(f"not a timestamp: {value!r}")

    if isinstance(value, str) and DAY_ONLY.fullmatch(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as error:
            raise SchemaError(f"not an ISO 8601 date: {value!r}") from error

    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        try:
            # INFO: Python 3.10's fromisoformat doesn't accept a trailing "Z".
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise SchemaError(f"not an ISO 8601 date: {value!r}") from error
    elif isinstance(value, datetime):
        moment = value
    else:
        raise SchemaError(f"not a timestamp: {value!r}")

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    moment = moment.astimezone(timezone.utc)
    pattern = "%Y-%m-%dT%H:%M:%S.%fZ" if moment.microsecond else "%Y-%m-%dT%H:%M:%SZ"
    return moment.strftime(pattern)


def _check_keys(data: Any, allowed: tuple[str, ...], name: str) -> JsonDict:
    if not isinstance(data, dict):
        raise SchemaError(f"{name} must be an object, got {type(data).__name__}")

    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise SchemaError(f"unknown {name} field(s): {', '.join(unknown)}")

    return data


def _check_type(data: JsonDict, expected: str) -> None:
    actual = data.get("type", expected)
    if actual != expected:
        raise SchemaError(f"expected type {expected!r}, got {actual!r}")


def _items_from_list(data: Any, owner: str) -> list[Item]:
    if data is None:
        return []

    if not isinstance(data, list):
        raise SchemaError(f"{owner}.items must be a list")

    return [Item.from_dict(entry) for entry in data]


@dataclass(kw_only=True)
class Item:
    """One downloadable thing: a video, an audio track, an image, a thumbnail or an avatar.

    Items in a carousel share an ``index`` with their thumbnail, so a UI can group them.
    """

    index: int | None = None
    media_type: MediaType
    url: str | None = None
    ext: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    # INFO: yt-dlp-style stream list (resolution, codecs, bitrate). None means "not a stream source";
    # an empty list means the provider has streams but listed none yet, and is kept in the output.
    formats: list[JsonDict] | None = None
    provider: str | None = None
    extra: JsonDict | None = None

    FIELDS = ("index", "media_type", "url", "ext", "width", "height", "duration", "formats", "provider", "extra")

    def __post_init__(self) -> None:
        if self.media_type not in MEDIA_TYPES:
            raise SchemaError(f"unknown media type {self.media_type!r}, expected one of {sorted(MEDIA_TYPES)}")

    def to_dict(self) -> JsonDict:
        return {name: getattr(self, name) for name in self.FIELDS if getattr(self, name) is not None}

    @classmethod
    def from_dict(cls, data: Any) -> Item:
        data = _check_keys(data, cls.FIELDS, "item")
        if "media_type" not in data:
            raise SchemaError("item is missing 'media_type'")

        return cls(**data)


@dataclass(kw_only=True)
class Profile:
    """An account. Returned at the top level for a profile URL, and as ``Post.uploader``.

    A profile has no ``upload_date``: metadata belongs to the object it describes.
    """

    type: Literal["profile"] = field(default="profile", init=False)
    platform: str
    id: str | None = None
    username: str | None = None
    name: str | None = None
    url: str | None = None
    avatar: Item | None = None
    items: list[Item] = field(default_factory=list)

    FIELDS = ("type", "platform", "id", "username", "name", "url", "avatar", "items")

    def to_dict(self, *, as_uploader: bool = False) -> JsonDict:
        """Serialise the profile.

        ``as_uploader`` is for ``Post.uploader``: a post never extracts its uploader's other
        content, so an empty ``items`` list is left out there instead of written as ``[]``.
        """
        data: JsonDict = {
            "type": self.type,
            "platform": self.platform,
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "url": self.url,
            "avatar": self.avatar.to_dict() if self.avatar else None,
            "items": [item.to_dict() for item in self.items],
        }
        if as_uploader and not self.items:
            del data["items"]

        return data

    @classmethod
    def from_dict(cls, data: Any) -> Profile:
        data = _check_keys(data, cls.FIELDS, "profile")
        _check_type(data, "profile")
        if "platform" not in data:
            raise SchemaError("profile is missing 'platform'")

        avatar = data.get("avatar")
        return cls(
            platform=data["platform"],
            id=data.get("id"),
            username=data.get("username"),
            name=data.get("name"),
            url=data.get("url"),
            avatar=Item.from_dict(avatar) if avatar is not None else None,
            items=_items_from_list(data.get("items"), "profile"),
        )


@dataclass(kw_only=True)
class Post:
    """A single post: one video, one image, or a carousel of several.

    ``title`` is often ``None`` on social platforms; ``description`` holds the caption.
    ``upload_date`` is UTC ISO 8601 (``2026-10-30T18:22:00Z``) and is normalised on the way in.
    """

    type: Literal["post"] = field(default="post", init=False)
    platform: str
    id: str | None = None
    title: str | None = None
    description: str | None = None
    upload_date: str | None = None
    url: str | None = None
    uploader: Profile | None = None
    items: list[Item] = field(default_factory=list)

    FIELDS = ("type", "platform", "id", "title", "description", "upload_date", "url", "uploader", "items")

    def __post_init__(self) -> None:
        self.upload_date = to_utc_iso(self.upload_date)

    def to_dict(self) -> JsonDict:
        return {
            "type": self.type,
            "platform": self.platform,
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "upload_date": self.upload_date,
            "url": self.url,
            "uploader": self.uploader.to_dict(as_uploader=True) if self.uploader else None,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Post:
        data = _check_keys(data, cls.FIELDS, "post")
        _check_type(data, "post")
        if "platform" not in data:
            raise SchemaError("post is missing 'platform'")

        uploader = data.get("uploader")
        return cls(
            platform=data["platform"],
            id=data.get("id"),
            title=data.get("title"),
            description=data.get("description"),
            upload_date=data.get("upload_date"),
            url=data.get("url"),
            uploader=Profile.from_dict(uploader) if uploader is not None else None,
            items=_items_from_list(data.get("items"), "post"),
        )


Result = Union[Post, Profile]


def result_from_dict(data: Any) -> Result:
    """Build a Post or Profile from a dict, picking the class from its ``type`` field."""
    if not isinstance(data, dict):
        raise SchemaError(f"result must be an object, got {type(data).__name__}")

    result_type = data.get("type")
    if result_type == "post":
        return Post.from_dict(data)

    if result_type == "profile":
        return Profile.from_dict(data)

    raise SchemaError(f"result type must be 'post' or 'profile', got {result_type!r}")


def result_from_json(text: str) -> Result:
    return result_from_dict(json.loads(text))


def result_to_json(result: Result, *, indent: int | None = 2) -> str:
    return json.dumps(result.to_dict(), indent=indent, ensure_ascii=False)
