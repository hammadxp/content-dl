"""content-dl: one URL in, every extractable item out, in one format."""

from content_dl.download import Download, download, select_items
from content_dl.extract import ExtractionError, extract
from content_dl.providers.base import Cookies
from content_dl.schema import (
    MEDIA_TYPES,
    Item,
    MediaType,
    Post,
    Profile,
    Result,
    SchemaError,
    result_from_dict,
    result_from_json,
    result_to_json,
    to_utc_iso,
)

# INFO: Release builds (scripts/build_exe.py) generate _version.py as "1.<commit count>". Source
# installs don't have it, so they report the dev version.
try:
    from content_dl._version import __version__
except ImportError:
    __version__ = "1.0.dev"

__all__ = [
    "Cookies",
    "Download",
    "ExtractionError",
    "MEDIA_TYPES",
    "Item",
    "MediaType",
    "Post",
    "Profile",
    "Result",
    "SchemaError",
    "download",
    "extract",
    "result_from_dict",
    "result_from_json",
    "result_to_json",
    "select_items",
    "to_utc_iso",
]
