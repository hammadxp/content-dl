"""Write the notes for a GitHub release: what changed since the last release, and the bundled tools.

Lists the commit titles since the previous release tag, leaving out bot commits (Dependabot's
version bumps, which show up under "Bundled tools" instead), then the yt-dlp and gallery-dl
versions inside the executables.

Usage:

    python scripts/release_notes.py              # notes since the latest tag before HEAD
    python scripts/release_notes.py --since v1.21
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_FILE = "bundle/requirements.txt"

# The bundled tools users care about. requests, curl_cffi and pyinstaller are left out of the notes.
SHOWN_TOOLS = ("yt-dlp", "gallery-dl")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def previous_tag() -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0", "HEAD").strip() or None
    except subprocess.CalledProcessError:
        return None


def is_bot(author: str) -> bool:
    """
    >>> is_bot("dependabot[bot]"), is_bot("hammadxp")
    (True, False)
    """
    return author.endswith("[bot]")


def changes_since(tag: str | None) -> list[str]:
    revisions = f"{tag}..HEAD" if tag else "HEAD"
    log = git("log", "--no-merges", "--format=%an%x1f%s", revisions)
    titles = []
    for line in log.splitlines():
        author, _, title = line.partition("\x1f")
        if title and not is_bot(author):
            titles.append(title)

    return titles


def pinned_versions(text: str) -> dict[str, str]:
    """Exact versions from a requirements file.

    >>> pinned_versions("# note\\nyt-dlp[curl-cffi]==2026.10.26\\ngallery-dl==1.32.19\\n")
    {'yt-dlp': '2026.10.26', 'gallery-dl': '1.32.19'}
    """
    versions = {}
    for match in re.finditer(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(\S+)", text, re.M):
        versions[match.group(1)] = match.group(2)

    return versions


def bundled_at(tag: str | None) -> dict[str, str]:
    if tag is None:
        return {}

    try:
        return pinned_versions(git("show", f"{tag}:{BUNDLE_FILE}"))
    except subprocess.CalledProcessError:
        return {}


def bundled_lines(now: dict[str, str], before: dict[str, str]) -> list[str]:
    """
    >>> bundled_lines({"yt-dlp": "2026.11.02", "gallery-dl": "1.32.19"}, {"yt-dlp": "2026.10.26", "gallery-dl": "1.32.19"})
    ['- yt-dlp 2026.11.02 (updated from 2026.10.26)', '- gallery-dl 1.32.19']
    """
    lines = []
    for name in SHOWN_TOOLS:
        version = now.get(name)
        if version is None:
            continue

        old = before.get(name)
        updated = f" (updated from {old})" if old and old != version else ""
        lines.append(f"- {name} {version}{updated}")

    return lines


def notes(tag: str | None) -> str:
    sections = []
    changes = changes_since(tag)
    if changes:
        sections.append("## Changes\n\n" + "\n".join(f"- {title}" for title in changes))

    now = pinned_versions((ROOT / BUNDLE_FILE).read_text(encoding="utf-8"))
    sections.append("## Bundled tools\n\n" + "\n".join(bundled_lines(now, bundled_at(tag))))

    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Write release notes since the previous release.")
    parser.add_argument("--since", metavar="TAG", help="previous release tag (default: latest tag before HEAD)")
    args = parser.parse_args()

    print(notes(args.since or previous_tag()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
