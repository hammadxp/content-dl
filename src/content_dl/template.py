"""Output file name templates, in yt-dlp's ``%(field)s`` syntax.

Supported per field (a subset of yt-dlp's, in its order):
``%(name[.key...][,alternate...][&replacement][|default])<python format>``

- ``uploader.username`` reads nested fields with dots.
- ``title,description,id`` uses the first field that is set, so a post without a title still
  gets a readable name (the fallback order the spec asks for).
- ``index& {}`` writes the replacement when the field is set (``{}`` is the value), else nothing.
- ``index|0`` writes the default when no field is set. Without one, an unset field is ``NA``.

Values are made safe for file names; the template's own ``/`` separators make folders.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MISSING = "NA"

# INFO: The title falls back to the caption, which can be a paragraph, so it is cut at 80 characters.
DEFAULT_TEMPLATE = "%(title,description,username,id).80s [%(id)s] %(kind)s%(index& {})s.%(ext)s"

FIELD = re.compile(r"%(?:%|\((?P<spec>[^)]*)\)(?P<format>[-#0 +]*\d*(?:\.\d+)?[diouxXeEfFgGcrs]))")
UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
SPACES = re.compile(r"\s+")

# Leaves room for a ".part" suffix and a " (2)" counter inside the common 255 byte limit.
MAX_COMPONENT_CHARS = 200


class TemplateError(ValueError):
    """The output template can't be rendered."""


def _lookup(context: Mapping[str, Any], path: str) -> Any:
    value: Any = context
    for key in path.strip().split("."):
        if not isinstance(value, Mapping) or key not in value:
            return None

        value = value[key]

    return None if value == "" else value


def sanitize(value: str, strip: bool = True) -> str:
    """Make one value safe inside a file name.

    >>> sanitize('sunset: day 1/2\\n"best"')
    'sunset_ day 1_2 _best_'
    """
    cleaned = UNSAFE.sub("_", SPACES.sub(" ", value))
    return cleaned.strip() if strip else cleaned


def _render_field(spec: str, fmt: str, context: Mapping[str, Any]) -> str:
    spec, has_default, default = spec.partition("|")
    spec, has_replacement, replacement = spec.partition("&")

    value = next((found for name in spec.split(",") if (found := _lookup(context, name)) is not None), None)
    if value is not None and has_replacement:
        # The template author's own spacing around the value is kept.
        return sanitize(replacement.replace("{}", sanitize(str(value))), strip=False)

    if value is None:
        return sanitize(default, strip=False) if has_default else ("" if has_replacement else MISSING)

    if isinstance(value, float) and value.is_integer() and fmt[-1] in "diouxX":
        value = int(value)

    try:
        text = ("%" + fmt) % value
    except TypeError:
        # %(index)d on a string id, say: write the value instead of failing the download.
        text = str(value)

    return sanitize(text)


def render(template: str, context: Mapping[str, Any]) -> Path:
    """Render a template into a relative or absolute file path.

    >>> context = {"id": "C9x", "title": None, "description": "sunset", "kind": "image", "index": 1, "ext": "jpg"}
    >>> render(DEFAULT_TEMPLATE, context).as_posix()
    'sunset [C9x] image 1.jpg'
    >>> render("%(uploader.username|unknown)s/%(id)s_%(index)03d.%(ext)s", context).as_posix()
    'unknown/C9x_001.jpg'
    >>> render(DEFAULT_TEMPLATE, {**context, "kind": "avatar", "index": None}).as_posix()
    'sunset [C9x] avatar.jpg'
    """

    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "%%":
            return "%"

        return _render_field(match.group("spec"), match.group("format"), context)

    rendered = FIELD.sub(replace, template)
    if not rendered.strip():
        raise TemplateError(f"output template {template!r} renders an empty file name")

    path = Path(rendered)
    parts = [_fit(part, last=index == len(path.parts) - 1) for index, part in enumerate(path.parts)]
    return Path(*parts)


def _fit(part: str, last: bool) -> str:
    """Cut an over-long folder or file name, keeping the extension; drop trailing dots and spaces."""
    if part == Path(part).anchor:
        return part

    if part in (".", ".."):
        return part

    if len(part) > MAX_COMPONENT_CHARS:
        stem, dot, ext = part.rpartition(".") if last else (part, "", "")
        if not dot or len(ext) > 10:
            stem, dot, ext = part, "", ""

        part = stem[: MAX_COMPONENT_CHARS - len(dot + ext)].rstrip() + dot + ext

    return part.rstrip(" .") or "_"
