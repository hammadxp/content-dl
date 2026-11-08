"""Build content-dl as a single executable with PyInstaller, like yt-dlp's yt-dlp.exe.

The executable bundles Python, yt-dlp, gallery-dl, requests and curl_cffi, so users download one
file and run it. ffmpeg and Deno stay separate installs, the same as for yt-dlp.

Versions are "1.N", where N is the number of commits on the built branch. Every commit bumps N, so
a release tag (v1.21, v1.22, ...) always maps back to exactly one commit.

Usage (from a virtual environment):

    pip install -r bundle/requirements.txt .   # the exact versions releases ship
    python scripts/build_exe.py           # dist/content-dl.exe on Windows, dist/content-dl elsewhere
    python scripts/build_exe.py --version  # print the version this commit builds as, and exit
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "src" / "content_dl"
VERSION_FILE = PACKAGE_DIR / "_version.py"
ENTRY_POINT = PACKAGE_DIR / "__main__.py"

MAJOR_VERSION = 1


def commit_count() -> int:
    output = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout

    return int(output.strip())


def version_from_commits(count: int) -> str:
    """The release version for a commit count.

    >>> version_from_commits(21)
    '1.21'
    """
    return f"{MAJOR_VERSION}.{count}"


def write_version(version: str) -> None:
    # INFO: _version.py is generated and gitignored. Source installs don't have it and report the
    # fallback version in __init__.py instead.
    VERSION_FILE.write_text(f'__version__ = "{version}"\n', encoding="utf-8")


def build(name: str) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--clean",
            "--noconfirm",
            "--name",
            name,
            "--paths",
            str(ROOT / "src"),
            "--distpath",
            str(ROOT / "dist"),
            "--workpath",
            str(ROOT / "build"),
            "--specpath",
            str(ROOT / "build"),
            # routes.json is read through importlib.resources.
            "--collect-data",
            "content_dl",
            # INFO: gallery-dl imports its extractor modules by name at runtime, so PyInstaller's
            # import scan can't see them. yt-dlp ships its own PyInstaller hook and needs nothing here.
            "--collect-submodules",
            "gallery_dl",
            str(ENTRY_POINT),
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build content-dl as a single executable.")
    parser.add_argument("--version", action="store_true", help="print the version this commit builds as, and exit")
    parser.add_argument("--name", default="content-dl", help="executable name, without extension")
    args = parser.parse_args()

    version = version_from_commits(commit_count())
    if args.version:
        print(version)
        return 0

    write_version(version)
    try:
        build(args.name)
    finally:
        VERSION_FILE.unlink(missing_ok=True)

    print(f"built {args.name} {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
