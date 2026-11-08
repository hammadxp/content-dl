"""Run content-dl against every real URL in tests/live_urls.json and print a pass/fail table.

Nothing is downloaded: each case is one extract() call, checked against the item counts the case
expects. Cases marked ``"cookies": true`` are skipped unless cookies are given,. Cases run one at a time: extract() isn't
safe to call from several threads at once.

    python scripts/live_check.py
    python scripts/live_check.py --only instagram,facebook --cookies-from-browser firefox
    python scripts/live_check.py --only "youtube:video" --verbose

Exit status is 1 when any case fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from content_dl.extract import extract  # noqa: E402
from content_dl.providers.base import Cookies  # noqa: E402

CASES_FILE = ROOT / "tests" / "live_urls.json"
MARKS = {"pass": "PASS", "fail": "FAIL", "skip": "skip", "todo": "todo"}


@dataclass
class Outcome:
    platform: str
    name: str
    status: str
    detail: str
    seconds: float = 0.0


def count_items(result) -> Counter:
    """Count items per media type, the avatar included, plus ``any`` for every image and video."""
    avatar = getattr(result, "avatar", None) or getattr(getattr(result, "uploader", None), "avatar", None)
    counts = Counter(item.media_type for item in [*result.items, *([avatar] if avatar else [])])
    counts["any"] = counts["image"] + counts["video"]
    return counts


def describe(counts: Counter) -> str:
    """``image:3 video:1``, without the ``any`` total."""
    return " ".join(f"{key}:{value}" for key, value in sorted(counts.items()) if key != "any" and value)


def matches(case: dict, selectors: list[str]) -> bool:
    """A selector is a platform (``instagram``) or a platform and a case name prefix (``instagram:post``)."""
    if not selectors:
        return True

    for selector in selectors:
        platform, _, name = selector.partition(":")
        if case["platform"] == platform and case["name"].startswith(name):
            return True

    return False


def run_case(case: dict, cookies: Cookies | None, verbose: bool) -> Outcome:
    platform, name = case["platform"], case["name"]
    if not case.get("url"):
        return Outcome(platform, name, "todo", "no URL yet")

    if case.get("cookies") and not cookies:
        return Outcome(platform, name, "skip", "needs --cookies or --cookies-from-browser")

    started = time.monotonic()
    try:
        result = extract(case["url"], media_types=case.get("media_types"), cookies=cookies)
    except Exception as error:  # every failure is a result here, not a crash
        if verbose:
            traceback.print_exc()

        message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
        return Outcome(platform, name, "fail", message[:160], time.monotonic() - started)

    seconds = time.monotonic() - started
    counts = count_items(result)
    short = [f"{key} {counts[key]}/{least}" for key, least in case["expect"].items() if counts[key] < least]
    if short:
        return Outcome(platform, name, "fail", f"too few: {', '.join(short)} (got {describe(counts) or 'nothing'})", seconds)

    return Outcome(platform, name, "pass", describe(counts), seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check content-dl against the real URLs in tests/live_urls.json.")
    parser.add_argument("--only", metavar="SELECTORS", help="comma separated platforms or platform:name prefixes")
    parser.add_argument("--cookies", metavar="FILE", help="Netscape cookies.txt file")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER", help="read cookies from a browser's store")
    parser.add_argument("--verbose", action="store_true", help="print the traceback of each failure")
    args = parser.parse_args()

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    selectors = [part.strip() for part in (args.only or "").split(",") if part.strip()]
    cookies = Cookies(file=args.cookies, browser=args.cookies_from_browser)
    cookies = cookies if cookies else None

    outcomes: list[Outcome] = []
    for case in cases:
        if not matches(case, selectors):
            continue

        outcome = run_case(case, cookies, args.verbose)
        outcomes.append(outcome)
        took = f"{outcome.seconds:5.1f}s" if outcome.seconds else "      "
        print(f"{MARKS[outcome.status]}  {took}  {outcome.platform:<10} {outcome.name:<28} {outcome.detail}", flush=True)

    totals = Counter(outcome.status for outcome in outcomes)
    print("\n" + ", ".join(f"{totals[status]} {status}" for status in MARKS if totals[status]))
    return 1 if totals["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
