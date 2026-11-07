"""The merger: combines what each provider returned into one Post or Profile.

Rules (SPEC.md, "Data model" rule 2):

- Each field comes from the highest-priority provider that has it. Post and Profile fields
  follow the routing table's metadata column; items follow the column for their kind.
- Duplicate items are merged into one: same kind and either the same media id
  (``Item.extra["media_id"]``) or the same URL with its query stripped. The higher-priority
  copy wins and the lower one only fills fields it left unset.
- A provider outside a kind's column is a last resort: its items of that kind are used only
  when no provider in the column returned any.
- A fallback provider (cobalt) is used for a kind only when no other provider returned that
  kind. Its items carry no media id and often a tunnel URL, so they can't be matched to other
  providers' copies and would otherwise show up twice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from urllib.parse import urlsplit, urlunsplit

from content_dl.router import KIND_COLUMNS
from content_dl.schema import Item, ItemKind, Post, Profile, Result

# Item order inside one carousel index: the media first, then its cover image.
KIND_ORDER: dict[str, int] = {"video": 0, "audio": 1, "image": 2, "thumbnail": 3, "avatar": 4}

# Fields a lower-priority duplicate may fill. provider and extra describe the winning copy only.
FILLABLE_ITEM_FIELDS = tuple(f.name for f in fields(Item) if f.name not in ("kind", "provider", "extra"))

# Providers whose items can't be matched against other providers' items.
FALLBACK_PROVIDERS = frozenset({"cobalt"})

POST_FIELDS = ("id", "title", "description", "upload_date", "url")
PROFILE_FIELDS = ("id", "username", "name", "url")


def strip_query(url: str) -> str:
    """Drop the query and fragment, which CDNs use for size, crop and signature parameters.

    >>> strip_query("https://cdn.example.com/a/b.jpg?stp=dst-jpg_p1080x1080&_nc_ht=x#top")
    'https://cdn.example.com/a/b.jpg'
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, "", ""))


def _identity(item: Item) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    media_id = (item.extra or {}).get("media_id")
    if media_id is not None:
        keys.add(("id", str(media_id)))

    if item.url:
        keys.add(("url", strip_query(item.url)))

    return keys


def _fill(winner: Item, other: Item) -> None:
    for name in FILLABLE_ITEM_FIELDS:
        if getattr(winner, name) is None and getattr(other, name) is not None:
            setattr(winner, name, getattr(other, name))


def merge_items(groups: Sequence[Sequence[Item]]) -> list[Item]:
    """Merge item lists given in priority order, removing duplicates of the same kind.

    >>> high = [Item(index=2, kind="video", formats=[], provider="yt-dlp", extra={"media_id": "B"})]
    >>> low = [Item(index=2, kind="video", url="https://cdn/b.mp4", width=720, provider="gallery-dl",
    ...             extra={"media_id": "B"})]
    >>> [(i.provider, i.url, i.width) for i in merge_items([high, low])]
    [('yt-dlp', 'https://cdn/b.mp4', 720)]
    """
    merged: list[tuple[Item, set[tuple[str, str]]]] = []
    for group in groups:
        for item in group:
            keys = _identity(item)
            duplicate = next((entry for entry in merged if entry[0].kind == item.kind and entry[1] & keys), None)
            if duplicate is None:
                merged.append((replace(item), keys))
                continue

            _fill(duplicate[0], item)
            duplicate[1].update(keys)

    return [item for item, _ in merged]


def _items_of(result: Result, kind: str) -> list[Item]:
    return [item for item in result.items if item.kind == kind]


def _avatar_of(result: Result) -> Item | None:
    if isinstance(result, Profile):
        return result.avatar

    return result.uploader.avatar if result.uploader else None


def _ordered(column: Sequence[str], results: Mapping[str, Result]) -> tuple[list[str], list[str]]:
    """Split the providers that returned a result into the column's own and the rest."""
    own = [name for name in column if name in results]
    rest = [name for name in results if name not in own]
    return own, rest


def _merge_kind(kind: str, routes: Mapping[str, Sequence[str]], results: Mapping[str, Result]) -> list[Item]:
    own, rest = _ordered(routes.get(KIND_COLUMNS[kind], []), results)
    for names in (own, rest):
        groups = {name: _items_of(results[name], kind) for name in names}
        if any(items for name, items in groups.items() if name not in FALLBACK_PROVIDERS):
            groups = {name: items for name, items in groups.items() if name not in FALLBACK_PROVIDERS}

        items = merge_items(list(groups.values()))
        if items:
            return items

    return []


def _merge_avatar(routes: Mapping[str, Sequence[str]], results: Mapping[str, Result]) -> Item | None:
    own, rest = _ordered(routes.get("avatar", []), results)
    avatars = [avatar for name in [*own, *rest] if (avatar := _avatar_of(results[name]))]
    if not avatars:
        return None

    return merge_items([[avatar] for avatar in avatars])[0]


def _first(values: Sequence[object]) -> object:
    return next((value for value in values if value is not None), None)


def _merge_profile(
    platform: str, profiles: Sequence[Profile], avatar: Item | None, items: list[Item] | None = None
) -> Profile | None:
    if not profiles and avatar is None:
        return None

    return Profile(
        platform=platform,
        **{name: _first([getattr(profile, name) for profile in profiles]) for name in PROFILE_FIELDS},
        avatar=avatar,
        items=items or [],
    )


def sort_items(items: Sequence[Item]) -> list[Item]:
    """Group items by carousel index (unindexed last), media before its thumbnail."""
    return sorted(items, key=lambda item: (item.index is None, item.index or 0, KIND_ORDER[item.kind]))


def merge(
    results: Mapping[str, Result],
    routes: Mapping[str, Sequence[str]],
    want: Sequence[ItemKind] | None = None,
) -> Result:
    """Combine provider results into one.

    ``results`` maps provider name to its result, in the order the providers ran. ``routes`` is
    the platform's row of the routing table. ``want`` limits the merged items to some kinds.

    The highest-priority metadata provider decides whether the result is a Post or a Profile;
    results of the other type are left out, since their fields describe something else.
    """
    if not results:
        raise ValueError("nothing to merge")

    meta_own, meta_rest = _ordered(routes.get("metadata", []), results)
    meta_order = [*meta_own, *meta_rest]
    lead = results[meta_order[0]]
    platform = lead.platform

    same_type = {name: result for name, result in results.items() if type(result) is type(lead)}
    meta = [same_type[name] for name in meta_order if name in same_type]

    kinds = [kind for kind in KIND_ORDER if kind != "avatar" and (want is None or kind in want)]
    items = sort_items([item for kind in kinds for item in _merge_kind(kind, routes, same_type)])
    avatar = _merge_avatar(routes, same_type) if want is None or "avatar" in want else None

    if isinstance(lead, Profile):
        return _merge_profile(platform, meta, avatar, items)  # type: ignore[return-value]

    uploaders = [post.uploader for post in meta if post.uploader]
    return Post(
        platform=platform,
        **{name: _first([getattr(post, name) for post in meta]) for name in POST_FIELDS},
        uploader=_merge_profile(platform, uploaders, avatar),
        items=items,
    )
