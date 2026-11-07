"""Build content-dl as a single zipapp for the Seal fork's Python runtime.

youtubedl-android (the library Seal uses) ships CPython 3.11 whose site-packages only holds
mutagen and pycryptodomex, and it runs yt-dlp as `libpython.so <yt-dlp zipapp> ARGS`. content-dl
runs the same way, so this zipapp carries everything content-dl needs that the runtime lacks:
gallery-dl, requests and their pure-Python dependencies.

Two packages are deliberately left out:

- yt-dlp: Seal already ships and updates its own yt-dlp zipapp. The fork puts that file on
  PYTHONPATH, so `import yt_dlp` finds it and content-dl never pins a second, older copy.
  Pass --with-yt-dlp for a fully self-contained zipapp (desktop testing, other hosts).
- curl_cffi: compiled, and its only Android wheel needs Python 3.13+. content-dl and yt-dlp both
  fall back to plain requests or urllib without it.

Usage:

    python scripts/build_pyz.py                  # dist/content-dl.pyz
    python scripts/build_pyz.py --with-yt-dlp    # also bundle yt-dlp
    python -S dist/content-dl.pyz -J URL         # run it the way the phone does
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "src" / "content_dl"

# INFO: youtubedl-android 0.17.x bundles CPython 3.11. Wheels are resolved for that version, as
# pure-Python ("py") wheels only, so nothing compiled for the desktop slips into the phone build.
TARGET_PYTHON = "3.11"

# Names as they appear in pyproject.toml's dependencies, matched without extras or versions.
LEFT_TO_HOST = {"yt-dlp"}

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


def bundled_requirements(with_yt_dlp: bool) -> list[str]:
    """Pick the dependencies to bundle, dropping extras so compiled ones (curl_cffi) stay out."""
    bundled = []
    for requirement in read_dependencies(ROOT / "pyproject.toml"):
        name = requirement_name(requirement)
        if name in LEFT_TO_HOST and not with_yt_dlp:
            continue

        bundled.append(re.sub(r"\[[^\]]*\]", "", requirement))

    return bundled


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


def build(output: Path, with_yt_dlp: bool) -> Path:
    with tempfile.TemporaryDirectory(prefix="content-dl-pyz-") as tmp:
        staging = Path(tmp)
        install(bundled_requirements(with_yt_dlp), staging)
        copy_package(staging)
        strip(staging)
        # INFO: zipapp's own `main=` entry point drops main()'s return value, so every run would exit
        # 0 and the fork couldn't tell a failed extraction from an empty one. Write the entry point.
        (staging / "__main__.py").write_text(MAIN_PY, encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        # No shebang: the phone runs it as `libpython.so content-dl.pyz ARGS`, never directly.
        zipapp.create_archive(staging, output, compressed=True)

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build content-dl as a zipapp for the Seal fork's Python runtime.")
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "dist" / "content-dl.pyz", help="output file")
    parser.add_argument("--with-yt-dlp", action="store_true", help="bundle yt-dlp instead of using the host's copy")
    args = parser.parse_args()

    path = build(args.output, args.with_yt_dlp)
    print(f"{path} ({path.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
