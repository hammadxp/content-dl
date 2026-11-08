"""Build content-dl as a single zipapp for Markhor, the Android app built on content-dl.

Markhor runs Python through youtubedl-android, which ships CPython 3.11 whose site-packages only
holds mutagen and pycryptodomex. content-dl runs as `libpython.so content-dl.pyz ARGS`, so this
zipapp carries everything content-dl needs that the runtime lacks: yt-dlp, gallery-dl, requests and
their pure-Python dependencies. Markhor doesn't update yt-dlp on its own: a new yt-dlp reaches the
phone as a new content-dl release.

curl_cffi is deliberately left out: it is compiled, and its only Android wheel needs Python 3.13+.
content-dl and yt-dlp both fall back to plain requests or urllib without it.

The bundled packages use the exact versions in bundle/requirements.txt, the same ones the desktop
executables ship, and the zipapp reports the same "1.N" version (from the commit count). Releases
publish it as `content-dl.pyz`, which Markhor downloads to update content-dl without a new APK.

Usage:

    python scripts/build_pyz.py                  # dist/content-dl.pyz
    python -S dist/content-dl.pyz -J URL         # run it the way the phone does
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "src" / "content_dl"
PINNED_FILE = ROOT / "bundle" / "requirements.txt"

# INFO: youtubedl-android 0.17.x bundles CPython 3.11. Wheels are resolved for that version, as
# pure-Python ("py") wheels only, so nothing compiled for the desktop slips into the phone build.
TARGET_PYTHON = "3.11"

MAIN_PY = "from content_dl.cli import main\n\nraise SystemExit(main())\n"

# Directories pip installs that the runtime never needs.
STRIP_DIRS = ("bin", "__pycache__")


def read_dependencies(pyproject: Path) -> list[str]:
    """Read [project] dependencies from pyproject.toml without tomllib (Python 3.10 has none).

    >>> import tempfile, pathlib
    >>> path = pathlib.Path(tempfile.mkdtemp()) / "pyproject.toml"
    >>> _ = path.write_text('[project]\\ndependencies = ["a>=1", "b[x]>=2"]\\n')
    >>> read_dependencies(path)
    ['a>=1', 'b[x]>=2']
    """
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]\s*$",pyproject.read_text(encoding="utf-8"), re.S | re.M)
    if not match:
        raise SystemExit(f"no dependencies list in {pyproject}")

    return re.findall(r'"([^"]+)"', match.group(1))


def requirement_name(requirement: str) -> str:
    """Return a requirement's project name, without extras or version.

    >>> requirement_name("yt-dlp[curl-cffi]>=2026.10.21")
    'yt-dlp'
    >>> requirement_name("requests>=2.32")
    'requests'
    """
    return re.split(r"[\[<>=!~;\s]", requirement, maxsplit=1)[0]


def pinned_versions(text: str) -> dict[str, str]:
    """Exact versions from a requirements file, keyed by project name.

    >>> pinned_versions("# note\\nyt-dlp[curl-cffi]==2026.10.26\\nrequests==2.34.2\\n")
    {'yt-dlp': '2026.10.26', 'requests': '2.34.2'}
    """
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(\S+)", text, re.M))


def bundled_requirements() -> list[str]:
    """Pick the dependencies to bundle, at their pinned versions, without extras.

    Dropping extras keeps compiled packages (yt-dlp's curl_cffi) out of the zipapp.
    """
    pinned = pinned_versions(PINNED_FILE.read_text(encoding="utf-8")) if PINNED_FILE.exists() else {}
    bundled = []
    for requirement in read_dependencies(ROOT / "pyproject.toml"):
        name = requirement_name(requirement)
        if name in pinned:
            bundled.append(f"{name}=={pinned[name]}")
        else:
            bundled.append(re.sub(r"\[[^\]]*\]", "", requirement))

    return bundled


def release_version() -> str:
    """The "1.N" version build_exe.py gives the executables built from this commit."""
    spec = importlib.util.spec_from_file_location("build_exe", Path(__file__).with_name("build_exe.py"))
    build_exe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_exe)

    return build_exe.version_from_commits(build_exe.commit_count())


def install(requirements: list[str], target: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "--no-compile",
            "--target",
            str(target),
            "--only-binary=:all:",
            "--implementation",
            "py",
            "--python-version",
            TARGET_PYTHON,
            *requirements,
        ],
        check=True,
    )


def copy_package(target: Path) -> None:
    shutil.copytree(PACKAGE_DIR, target / "content_dl", ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"))


def strip(target: Path) -> None:
    for name in STRIP_DIRS:
        for path in list(target.rglob(name)):
            if path.is_dir():
                shutil.rmtree(path)


def build(output: Path, version: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="content-dl-pyz-") as tmp:
        staging = Path(tmp)
        install(bundled_requirements(), staging)
        copy_package(staging)
        strip(staging)
        # INFO: Written into the staged copy, not src/, so building never leaves files behind. The
        # Markhor reads this file straight from the zip to compare its bundled and downloaded copies.
        (staging / "content_dl" / "_version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        # INFO: zipapp's own `main=` entry point drops main()'s return value, so every run would exit
        # 0 and the fork couldn't tell a failed extraction from an empty one. Write the entry point.
        (staging / "__main__.py").write_text(MAIN_PY, encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        # No shebang: the phone runs it as `libpython.so content-dl.pyz ARGS`, never directly.
        zipapp.create_archive(staging, output, compressed=True)

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build content-dl as a zipapp for Markhor's Python runtime.")
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "dist" / "content-dl.pyz", help="output file")
    args = parser.parse_args()

    version = release_version()
    path = build(args.output, version)
    print(f"{path} {version} ({path.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
