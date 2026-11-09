"""Writes sponsors.json from the Patreon API, for patrons of one tier only.

"current": active patrons entitled to TIER_TITLE, newest first.
"past": anyone who was in "current" before and no longer is (cancelled, payment failed, or
switched to another tier), most recently left first.

Patreon only reports the tier a patron is entitled to right now, so a former patron's old tier
is unknown. That is why "past" is built from the previous sponsors.json instead of the API alone.
Also rewrites the block between the sponsor markers in README.md with avatar rows.
Needs PATREON_ACCESS_TOKEN (the creator's access token).

Output example:
  {"current": [{"id": "123", "name": "Jane", "url": "https://www.patreon.com/jane", "avatar": "https://..."}],
   "past": []}
"""

import html
import json
import os
import sys
import urllib.parse
import urllib.request

API = "https://www.patreon.com/api/oauth2/v2"
OUTPUT_FILE = "sponsors.json"
README_FILE = "README.md"
README_START = "<!-- sponsors:start -->"
README_END = "<!-- sponsors:end -->"
PATREON_URL = "https://patreon.com/hammadxp"
AVATAR_SIZE = 48
# Shown for patrons without a Patreon picture.
DEFAULT_AVATAR = "https://github.com/identicons/content-dl.png"

# INFO: Matched by title, so renaming the tier on Patreon also needs a change here.
TIER_TITLE = "content-dl supporter"


def get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "User-Agent": "content-dl-sponsors-sync"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_members(token: str) -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    campaigns = get_json(f"{API}/campaigns", token)["data"]
    if not campaigns:
        sys.exit("No Patreon campaign found for this token.")
    campaign_id = campaigns[0]["id"]

    query = urllib.parse.urlencode(
        {
            "include": "user,currently_entitled_tiers",
            "fields[member]": "full_name,patron_status,pledge_relationship_start",
            "fields[user]": "full_name,thumb_url,url",
            "fields[tier]": "title",
            "page[count]": 500,
        }
    )
    url = f"{API}/campaigns/{campaign_id}/members?{query}"

    members: list[dict] = []
    users: dict[str, dict] = {}
    tiers: dict[str, dict] = {}
    while url:
        page = get_json(url, token)
        members.extend(page.get("data", []))
        for item in page.get("included", []):
            if item["type"] == "user":
                users[item["id"]] = item["attributes"]
            elif item["type"] == "tier":
                tiers[item["id"]] = item["attributes"]
        url = page.get("links", {}).get("next")
    return members, users, tiers


def user_id(member: dict) -> str | None:
    data = member.get("relationships", {}).get("user", {}).get("data") or {}
    return data.get("id")


def has_tier(member: dict, tiers: dict[str, dict]) -> bool:
    entitled = member.get("relationships", {}).get("currently_entitled_tiers", {}).get("data") or []
    return any(tiers.get(tier["id"], {}).get("title", "").strip().lower() == TIER_TITLE.lower() for tier in entitled)


def to_sponsor(member: dict, users: dict[str, dict]) -> dict:
    uid = user_id(member)
    user = users.get(uid, {})
    name = (member["attributes"].get("full_name") or user.get("full_name") or "Anonymous").strip()

    sponsor = {"id": uid, "name": name}
    if user.get("url"):
        sponsor["url"] = user["url"]
    if user.get("thumb_url"):
        sponsor["avatar"] = user["thumb_url"]
    return sponsor


def load_previous() -> dict:
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"current": [], "past": []}
    return {"current": data.get("current", []), "past": data.get("past", [])}


def main() -> None:
    token = os.environ.get("PATREON_ACCESS_TOKEN")
    if not token:
        sys.exit("PATREON_ACCESS_TOKEN is not set.")

    members, users, tiers = fetch_members(token)
    previous = load_previous()

    # Newest first by join date. Patreon dates are ISO 8601 strings, so they sort as text.
    active = [
        member
        for member in members
        if member["attributes"].get("patron_status") == "active_patron" and has_tier(member, tiers) and user_id(member)
    ]
    active.sort(key=lambda m: m["attributes"].get("pledge_relationship_start") or "", reverse=True)
    current = [to_sponsor(member, users) for member in active]
    current_ids = {sponsor["id"] for sponsor in current}

    # Fresh names and avatars for anyone still on Patreon, even if they left this tier.
    latest = {user_id(member): to_sponsor(member, users) for member in members if user_id(member)}

    # Those who just left go on top, then the earlier past list in its existing order.
    just_left = [s for s in previous["current"] if s.get("id") and s["id"] not in current_ids]
    past, seen = [], set()
    for sponsor in just_left + previous["past"]:
        sid = sponsor.get("id")
        if not sid or sid in current_ids or sid in seen:
            continue
        seen.add(sid)
        past.append(latest.get(sid, sponsor))

    # Dates stay out of sponsors.json, which is public.
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as file:
        json.dump({"current": current, "past": past}, file, ensure_ascii=False, indent=2)
        file.write("\n")

    update_readme(current, past)
    print(f'Wrote {len(current)} current and {len(past)} past "{TIER_TITLE}" sponsors to {OUTPUT_FILE}.')


def sponsor_avatar_html(sponsor: dict) -> str:
    name = html.escape(sponsor["name"], quote=True)
    avatar = html.escape(sponsor.get("avatar") or DEFAULT_AVATAR, quote=True)
    image = f'<img src="{avatar}" width="{AVATAR_SIZE}" height="{AVATAR_SIZE}" alt="{name}" title="{name}" />'
    if sponsor.get("url"):
        return f'<a href="{html.escape(sponsor["url"], quote=True)}">{image}</a>'
    return image


def update_readme(current: list[dict], past: list[dict]) -> None:
    """Rewrites the README block between the sponsor markers with avatar rows."""
    try:
        with open(README_FILE, encoding="utf-8", newline="") as file:
            readme = file.read()
    except FileNotFoundError:
        return

    start, end = readme.find(README_START), readme.find(README_END)
    if start == -1 or end == -1 or end < start:
        print(f"Sponsor markers not found in {README_FILE}, skipping it.")
        return

    if current or past:
        sections = []
        if current:
            avatars = " ".join(sponsor_avatar_html(s) for s in current)
            sections.append(f"**Current supporters ({len(current)})**\n\n{avatars}")
        if past:
            avatars = " ".join(sponsor_avatar_html(s) for s in past)
            sections.append(f"**Past supporters ({len(past)})**\n\n{avatars}")
        block = "\n\n".join(sections)
    else:
        block = f"No supporters yet. [Be the first!]({PATREON_URL})"

    readme = readme[: start + len(README_START)] + f"\n{block}\n" + readme[end:]
    with open(README_FILE, "w", encoding="utf-8", newline="") as file:
        file.write(readme)


if __name__ == "__main__":
    main()
