"""Milestone 1: the SPEC.md examples round-trip through the schema classes unchanged."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from content_dl import (
    Item,
    Post,
    Profile,
    SchemaError,
    result_from_dict,
    result_from_json,
    result_to_json,
    to_utc_iso,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# Round trips

@pytest.mark.parametrize("name", ["instagram_post.json", "instagram_profile.json"])
def test_spec_example_round_trips_unchanged(name: str) -> None:
    original = load_fixture(name)

    result = result_from_dict(original)

    assert result.to_dict() == original
    assert json.loads(result_to_json(result)) == original
    assert result_from_json(result_to_json(result)) == result


@pytest.mark.parametrize("name", ["instagram_post.json", "instagram_profile.json"])
def test_spec_example_keeps_key_order(name: str) -> None:
    original = load_fixture(name)

    assert json.dumps(result_from_dict(original).to_dict()) == json.dumps(original)


def test_post_example_parses_into_typed_objects() -> None:
    post = result_from_dict(load_fixture("instagram_post.json"))

    assert isinstance(post, Post)
    assert post.title is None
    assert post.upload_date == "2026-10-30T18:22:00Z"
    assert isinstance(post.uploader, Profile)
    assert post.uploader.username == "someone"
    assert isinstance(post.uploader.avatar, Item)
    assert post.uploader.avatar.media_type == "avatar"
    assert [item.media_type for item in post.items] == ["image", "video", "thumbnail"]
    assert [item.index for item in post.items] == [1, 2, 2]
    assert post.items[1].formats == []
    assert post.items[1].url is None


def test_profile_example_has_no_upload_date() -> None:
    profile = result_from_dict(load_fixture("instagram_profile.json"))

    assert isinstance(profile, Profile)
    assert "upload_date" not in profile.to_dict()
    assert profile.items == []


# Serialisation rules

def test_post_writes_missing_fields_as_null() -> None:
    data = Post(platform="threads").to_dict()

    assert data == {
        "type": "post",
        "platform": "threads",
        "id": None,
        "title": None,
        "description": None,
        "upload_date": None,
        "url": None,
        "uploader": None,
        "items": [],
    }


def test_profile_writes_missing_fields_as_null() -> None:
    assert Profile(platform="x").to_dict() == {
        "type": "profile",
        "platform": "x",
        "id": None,
        "username": None,
        "name": None,
        "url": None,
        "avatar": None,
        "items": [],
    }


def test_item_leaves_out_unset_fields() -> None:
    assert Item(media_type="thumbnail", url="https://example.com/t.jpg").to_dict() == {
        "media_type": "thumbnail",
        "url": "https://example.com/t.jpg",
    }


def test_uploader_keeps_items_when_it_has_some() -> None:
    uploader = Profile(platform="x", items=[Item(media_type="image", url="https://example.com/a.jpg")])

    data = Post(platform="x", uploader=uploader).to_dict()

    assert data["uploader"]["items"] == [{"media_type": "image", "url": "https://example.com/a.jpg"}]


def test_extra_round_trips() -> None:
    item = Item(media_type="video", provider="yt-dlp", extra={"extractor_key": "Youtube", "nested": {"a": [1, 2]}})

    assert Item.from_dict(item.to_dict()) == item


def test_json_keeps_non_ascii_text() -> None:
    post = Post(platform="instagram", description="plage au coucher du soleil 🌅")

    assert "🌅" in result_to_json(post)


# Dates

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1793384520, "2026-10-30T18:22:00Z"),
        (1793384520.5, "2026-10-30T18:22:00.500000Z"),
        ("2026-10-30T18:22:00Z", "2026-10-30T18:22:00Z"),
        ("2026-10-30T20:22:00+02:00", "2026-10-30T18:22:00Z"),
        ("2026-10-30T18:22:00", "2026-10-30T18:22:00Z"),
        (datetime(2026, 10, 30, 13, 22, tzinfo=timezone(timedelta(hours=-5))), "2026-10-30T18:22:00Z"),
        (None, None),
    ],
)
def test_to_utc_iso(value, expected) -> None:
    assert to_utc_iso(value) == expected


def test_post_normalises_date_to_utc() -> None:
    assert Post(platform="x", upload_date="2026-10-30T20:22:00+02:00").upload_date == "2026-10-30T18:22:00Z"


@pytest.mark.parametrize("value", ["yesterday", True, ["2026"]])
def test_to_utc_iso_rejects_non_dates(value) -> None:
    with pytest.raises(SchemaError):
        to_utc_iso(value)


# Validation

def test_unknown_item_media_type_is_rejected() -> None:
    with pytest.raises(SchemaError, match="unknown media type"):
        Item(media_type="gif")


def test_item_without_media_type_is_rejected() -> None:
    with pytest.raises(SchemaError, match="missing 'media_type'"):
        Item.from_dict({"url": "https://example.com/a.jpg"})


@pytest.mark.parametrize(
    ("cls", "data"),
    [
        (Item, {"media_type": "image", "size": 10}),
        (Post, {"type": "post", "platform": "x", "likes": 3}),
        (Profile, {"type": "profile", "platform": "x", "upload_date": "2026-10-30T18:22:00Z"}),
    ],
)
def test_unknown_fields_are_rejected(cls, data) -> None:
    with pytest.raises(SchemaError, match="unknown"):
        cls.from_dict(data)


def test_missing_platform_is_rejected() -> None:
    with pytest.raises(SchemaError, match="missing 'platform'"):
        Post.from_dict({"type": "post"})


def test_wrong_type_is_rejected() -> None:
    with pytest.raises(SchemaError, match="expected type 'post'"):
        Post.from_dict({"type": "profile", "platform": "x"})


@pytest.mark.parametrize("data", [{"type": "story", "platform": "x"}, {"platform": "x"}, []])
def test_result_from_dict_needs_a_known_type(data) -> None:
    with pytest.raises(SchemaError):
        result_from_dict(data)
