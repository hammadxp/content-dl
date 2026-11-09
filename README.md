# content-dl

Paste one link, get every photo, video, audio track, thumbnail and profile picture in it, with clean file names.

content-dl works like yt-dlp: you can download what a link holds, list it first, or print it as JSON. It uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [gallery-dl](https://github.com/mikf/gallery-dl) under the hood, plus its own extractors for the sites those two don't handle well, and gives you one consistent result whichever of them did the work.

## Contents

- [Supported sites](#supported-sites)
- [Install](#install)
- [Quick start](#quick-start)
- [What gets downloaded](#what-gets-downloaded)
- [File names](#file-names)
- [Cookies (logged-in content)](#cookies-logged-in-content)
- [All options](#all-options)
- [Examples](#examples)
- [JSON output and the Python API](#json-output-and-the-python-api)
- [Routing overrides](#routing-overrides)
- [Environment variables](#environment-variables)
- [Troubleshooting](#troubleshooting)
- [Updating](#updating)
- [Android](#android)
- [Development](#development)
- [Support](#support)

## Supported sites

| Site | Posts | Profiles (picture and name) | Needs cookies for |
| --- | --- | --- | --- |
| YouTube | videos, shorts, audio, thumbnail | channel avatar | age-restricted or members-only videos |
| Instagram | posts, carousels, reels | profile picture | profile pictures, and some single-photo posts |
| Threads | posts, carousels, share links, shared Instagram posts | profile picture | nothing in normal use |
| X (Twitter) | images, videos, multi-image posts | profile picture | posts X only shows to logged-in accounts (sensitive content, some replies) |
| TikTok | videos, audio, photo carousels, share links | profile picture | nothing in normal use |
| Reddit | images, galleries, videos, share links | user and community icons | user and community icons |
| Facebook | reels and videos, photo posts, share links | profile picture | photo posts and profile pictures |
| Pinterest | image and video pins, `pin.it` links, board invites | profile picture | private boards |
| Snapchat | Spotlight videos, `snapchat.com/t/...` links, highlights, stories | profile picture | nothing in normal use |

**Other sites.** Any other link goes to yt-dlp and gallery-dl, which between them support well over a thousand sites (see yt-dlp's [supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) and gallery-dl's [supported sites](https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md)). content-dl hasn't been tested on those, so treat them as best effort. If a site works in yt-dlp or gallery-dl directly, it should work here.

## Install

There are two ways to install content-dl. The executable is the easiest: one file, no Python needed.

### Download the executable (recommended)

Download the file for your system from the [latest release](https://github.com/hammadxp/content-dl/releases/latest):

| System | File |
| --- | --- |
| Windows | `content-dl.exe` |
| macOS | `content-dl_macos` |
| Linux | `content-dl_linux` |

Put it in a folder on your PATH (or in the folder you download into), then check it:

```bash
content-dl --version
```

On macOS and Linux, rename it to `content-dl` and make it executable first:

```bash
chmod +x content-dl
```

**Windows says "Windows protected your PC".** The executable isn't code-signed, so SmartScreen warns about it the first time. Click **More info**, then **Run anyway**. Some antivirus programs also flag it by mistake, as they do with many Python programs packaged this way. You can check that your file matches the release with the `SHA2-256SUMS` file next to it.

**macOS says the developer can't be verified.** The file isn't signed by Apple. Remove the download flag, then run it again:

```bash
xattr -d com.apple.quarantine content-dl
```

The macOS file runs on Apple Silicon Macs (M1 and newer), not on Intel Macs. On an Intel Mac, install from source instead.

The executable already includes Python, yt-dlp and gallery-dl. You still need ffmpeg and Deno for YouTube (see the table below; skip the Python and Git rows). To update, download the newest release and replace the file.

Releases are numbered `v1.N`, where N is the commit the build came from, so a higher number is always newer.

### Install from source

#### 1. Install the requirements

| Requirement | Why | How to get it |
| --- | --- | --- |
| Python 3.11 or newer | content-dl is a Python program | [python.org/downloads](https://www.python.org/downloads/). On Windows, tick "Add python.exe to PATH" in the installer |
| Git | to download content-dl | [git-scm.com](https://git-scm.com/downloads) |
| ffmpeg | joins video and audio (YouTube, Reddit), and writes metadata into files with `--embed-metadata` | Windows: `winget install Gyan.FFmpeg`. macOS: `brew install ffmpeg`. Linux: `sudo apt install ffmpeg` |
| Deno | YouTube needs a JavaScript runtime to unlock its video streams | Windows: `winget install DenoLand.Deno`. macOS/Linux: see [deno.com](https://deno.com/) |

Photos from most sites download without ffmpeg or Deno. Without ffmpeg, YouTube falls back to a lower quality that has audio and video in one file.

Open a new terminal after installing these, then check them:

```bash
python --version
```

```bash
ffmpeg -version
```

#### 2. Install content-dl

```bash
git clone https://github.com/hammadxp/content-dl.git
```

```bash
cd content-dl
```

Create a virtual environment so content-dl's packages stay separate from the rest of your system:

```bash
python -m venv .venv
```

Activate it. On Windows (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

Then install:

```bash
pip install -e .
```

Check that it works:

```bash
content-dl --version
```

You need to activate the virtual environment again in each new terminal before running `content-dl`. If you'd rather have the command available everywhere, install it with [pipx](https://pipx.pypa.io/) instead: `pipx install -e .` from the `content-dl` folder.

## Quick start

Download everything in a post into the current folder:

```bash
content-dl "https://www.threads.com/@USERNAME/post/POST_ID"
```

Always put the link in quotes. Links often contain `&` or `?`, which your terminal would otherwise read as its own syntax.

See what a link holds before downloading anything:

```bash
content-dl --list-only "https://www.youtube.com/watch?v=VIDEO_ID"
```

Download only the profile picture from a profile link:

```bash
content-dl "https://www.tiktok.com/@USERNAME"
```

Save into a folder per site and uploader:

```bash
content-dl --filename-template "downloads/%(platform)s/%(uploader.username)s/%(display_title|)s (%(id)s)%(number&-{})s.%(ext)s" "https://x.com/USERNAME/status/POST_ID"
```

## What gets downloaded

Every link gives either a **post** or a **profile**, made up of **items**. Each item has a media type:

| Media type | What it is |
| --- | --- |
| `video` | a video, in the best quality available (with its sound) |
| `audio` | an audio track: a video's sound on its own, or the music of a photo post |
| `image` | a photo |
| `thumbnail` | a video's cover image |
| `avatar` | the uploader's profile picture |

Without options, content-dl downloads:

- on a **post**: every video and photo, in the order the post shows them. A photo post's music is downloaded too, since there is no video that already carries it.
- on a **profile**: the profile picture.

Thumbnails, separate audio tracks and the uploader's profile picture are only downloaded when you ask for them with `--media-types` or `--items`.

Files are saved in the current folder unless the file name template puts them somewhere else. A file whose name already exists is skipped, not downloaded again or renamed, so running the same command twice is safe.

## File names

The default name is:

```text
title or caption (uploader) (id)-number.extension
```

For example `sunset at the beach (someone) (C9xYz123)-07.jpg`.

- **Title or caption.** Sites with real titles (YouTube, Reddit, Pinterest) use the title. Instagram, Threads, X, TikTok and Facebook posts have no title, so the caption is used instead: its first line with words in it, without links, and without the hashtags and @mentions at its start or end. It's cut to 80 characters at a word boundary, or shorter when needed to fit (see below), and a cut title ends with `...`: `a very long caption about the trip we took to the... (someone) (C9xYz123).jpg`.
- **Uploader.** The uploader's username, or their display name when the site gives no username. Left out (with its brackets) when unknown.
- **Id.** The post's id on the site. It keeps two posts with the same caption from getting the same name.
- **No title or caption?** The name starts at the uploader: `(someone) (C9xYz123)-02.jpg`.
- **Number.** The item's position in the post, padded to two digits (`-01`, `-02`, ...). It only appears when the post holds more than one item, so a single photo or video is just `sunset at the beach (someone) (C9xYz123).jpg`. A video and its cover image are at the same position, so they share a number and differ by extension.
- **Profile pictures** are named `avatar (uploader).jpg`.

**Long names never fail.** Windows allows 260 characters for a whole path (folders included), and every system allows 255 bytes for one file name. When a name would go over either limit, content-dl shortens the title or caption, at a word boundary, until it fits. Whatever part of the title fits is kept, however short. The uploader, id, number and extension are never cut. It also leaves room for the temporary names a download uses on the way (such as `name.f251.webm.part`), which are what usually cause "file name too long" errors. Only if the folder is so deep that not one character of the title fits is it left out, and the name starts at the uploader: `(someone) (C9xYz123)-02.jpg`.

Characters that aren't allowed in file names (`/ \ : * ? " < > |`) become `_`.

A file whose name already exists is skipped. In the rare case that two items in one download would get the same name (a YouTube video and its separate audio track can both be `.webm`), the second gets its media type added, as in `... (VIDEO_ID) audio.webm`.

### Your own file names

`--filename-template` takes a yt-dlp style template: fields from the first table below, written with the syntax from the second. Put a folder path in front to save into folders; they're created as needed.

**Template fields**

| Field | Example | Notes |
| --- | --- | --- |
| `display_title` | `sunset at the beach` | the title or caption, picked as described above; unset when the post has neither |
| `display_uploader` | `someone` | the uploader's username, or display name |
| `display_id` | `C9xYz123` | the post's id, unset on profile pictures |
| `number` | `07` | the item's padded position, only set on posts with several items |
| `platform` | `instagram` | the site; an unknown site gives its domain, such as `vimeo.com` |
| `id` | `C9xYz123` | the post's id on the site, always set |
| `title` | `A walk in the park` | the post's own title, often unknown on social sites |
| `description` | `sunset at the beach #summer` | the full caption |
| `upload_date` | `2026-10-30T18:22:00Z` | UTC; some sites give the day only (`2026-10-30`) |
| `url` | `https://www.instagram.com/p/C9xYz123/` | the post's link |
| `uploader.username` | `someone` | also `uploader.name`, `uploader.id`, `uploader.url` |
| `username`, `name` | `someone`, `Some One` | on a profile link, the profile's own username and display name |
| `index` | `7` | the item's position as a plain number |
| `media_type` | `image` | `video`, `audio`, `image`, `thumbnail` or `avatar` |
| `ext` | `jpg` | the file extension, without the dot |
| `width`, `height` | `1080` | in pixels, when known |
| `duration` | `14.2` | in seconds, for video and audio |
| `provider` | `yt-dlp` | which tool found the item: `yt-dlp`, `gallery-dl` or `native` |

**Template syntax**

| Syntax | Meaning | Example |
| --- | --- | --- |
| `%(field)s` | the field's value; `NA` when it's unknown | `%(id)s` |
| `%(a.b)s` | a nested field | `%(uploader.username)s` |
| `%(a,b,c)s` | the first of these fields that is known | `%(title,description,id)s` |
| `%(field\|text)s` | `text` when the field is unknown | `%(uploader.name\|unknown)s` |
| `%(field&text {})s` | `text` with the value in place of `{}` when the field is known, nothing otherwise | `%(number&-{})s` |
| `%(field).50s` | Python formatting: cut to 50 characters | `%(description).50s` |
| `%(field)03d` | Python formatting: pad a number with zeros | `%(index)03d` |
| `%%` | a literal `%` | |

The default template is:

```text
%(display_title|)s%(display_uploader& ({}))s%(display_id& ({}))s%(number&-{})s.%(ext)s
```

Read it piece by piece: the title or caption (nothing when there's none), then ` (uploader)` when known, then ` (id)`, then `-07` on posts with several items. A space left at the start of a name is dropped.

A template with a folder per site and uploader, and the id first:

```bash
content-dl --filename-template "%(platform)s/%(uploader.username|unknown)s/%(id)s%(number&-{})s.%(ext)s" URL
```

## Cookies (logged-in content)

Some content is only shown to logged-in accounts: Facebook photo posts, Instagram profile pictures, Reddit profile icons, some X posts. content-dl never asks for your password. Instead, you give it your browser's cookies, which tell the site you're already logged in.

**Warning.** A cookies file is your logged-in session. Anyone who has it can use your account without your password. Keep it private, never share or upload it, and don't put it inside a folder you sync or commit to Git. Downloading a lot with your cookies can also get your account rate-limited or flagged, so use them only for the links that need them.

### Make a cookies.txt file with Get cookies.txt LOCALLY

[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) is a browser extension that exports cookies to a file on your computer, without sending them anywhere. It works in Chrome, Edge, Brave and other Chromium browsers.

1. Open the [extension's page](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) and click **Add to Chrome**, then **Add extension**.
2. Click the puzzle-piece icon in the browser toolbar and pin **Get cookies.txt LOCALLY**, so its icon stays visible.
3. Log in to the site in that browser, for example instagram.com. Stay on the site.
4. Click the extension's icon. Check that the format is **Netscape**.
5. Click **Export**. The browser saves a file named like `www.instagram.com_cookies.txt` in your Downloads folder.
6. Move it somewhere private, such as your user folder, and rename it to something easy to type, such as `instagram-cookies.txt`.

To cover several sites with one file, log in to each of them, then click **Export All Cookies** instead. That file holds the cookies of every site you're logged in to, so guard it even more carefully. content-dl only sends each site its own cookies.

### Use the cookies file

Pass the file with `--cookies`:

```bash
content-dl --cookies "C:\Users\you\instagram-cookies.txt" "https://www.instagram.com/USERNAME/"
```

On macOS or Linux:

```bash
content-dl --cookies ~/instagram-cookies.txt "https://www.instagram.com/USERNAME/"
```

With cookies, content-dl also switches some sites to their logged-in routes, which give more (on Instagram, full-size images and uploader details).

### Keep the cookies working

- **Don't log out** of the site in the browser you exported from. Logging out ends the session, and the exported cookies stop working. If you want a session just for content-dl, log in from a private window, export there, and close the window without logging out.
- **Export again when they expire.** Sessions last weeks to months. When content-dl says the cookies "aren't logged in, or have expired", repeat the export.
- yt-dlp may update the cookies file with refreshed values while it runs. That's normal.

### Read cookies straight from a browser

`--cookies-from-browser` reads cookies from a browser's own store, with no export step:

```bash
content-dl --cookies-from-browser firefox "https://www.facebook.com/share/p/SHARE_CODE/"
```

Supported browsers: `brave`, `chrome`, `chromium`, `edge`, `firefox`, `opera`, `safari`, `vivaldi`, `whale`. Add a profile name after a colon to pick one: `firefox:work`.

This works best with **Firefox**. Recent versions of Chrome and Edge on Windows encrypt their cookies in a way other programs can't read, and lock the cookie store while the browser is open, so `--cookies-from-browser chrome` often fails there. Use a cookies.txt file from the extension instead.

## All options

```text
content-dl [OPTIONS] URL
content-dl [OPTIONS] --from-json FILE
```

### General

| Option | What it does |
| --- | --- |
| `URL` | the post or profile link to read |
| `-h`, `--help` | show the list of options and exit |
| `--version` | show content-dl's version and exit |
| `--json-only` | print everything found as JSON and download nothing |
| `--list-only` | print a table of the items found, and every video's available qualities, and download nothing |
| `--media-types TYPES` | only these media types, comma separated: `video`, `audio`, `image`, `thumbnail`, `avatar` (plurals such as `images` work too) |
| `--from-json FILE` | read a result saved from `--json-only` instead of reading a link, then download from it. Do it soon: video links in the result expire within hours |
| `--provider NAME` | use only one tool, for debugging: `yt-dlp`, `gallery-dl` or `native` |

### Download

| Option | What it does |
| --- | --- |
| `--items MEDIA_TYPE[:INDEX],...` | download only these items, such as `image:1,video:3,avatar`. A media type without a number means all of that type. Numbers are the `INDEX` column of `--list-only` |
| `--max-quality RESOLUTION` | the highest video quality, such as `1080` or `720p`. Measured on the shorter side, so `1080` covers both 1920x1080 and vertical 1080x1920 videos. When a video has nothing at or under it, the next best quality is taken |
| `--ytdlp-format FORMAT` | a yt-dlp format selection for video items, passed to yt-dlp unchanged, such as `"bv*+ba/b"` or `"137+140"` (format ids from `--list-only`) |
| `--filename-template TEMPLATE` | the file name template, see [File names](#file-names) |
| `--embed-metadata` | write the title, caption, uploader, date and link into each file (ffmpeg tags for video and audio, EXIF and XMP for images). Needs ffmpeg for video and audio |
| `--report-json` | print one JSON line per finished item (`media_type`, `index`, `status`, `path`, `error`, `warning`) instead of the readable lines, for scripts and apps |

### Cookies

| Option | What it does |
| --- | --- |
| `--cookies FILE` | read cookies from a Netscape cookies.txt file, see [Cookies](#cookies-logged-in-content) |
| `--cookies-from-browser BROWSER[:PROFILE]` | read cookies from a browser's own store |

### Routing

| Option | What it does |
| --- | --- |
| `--routes FILE` | a JSON file that changes which tools are tried for each site, see [Routing overrides](#routing-overrides) |

### yt-dlp host settings

| Option | What it does |
| --- | --- |
| `--js-runtimes RUNTIME[:PATH],...` | JavaScript runtimes yt-dlp may use for YouTube, such as `deno` or `node:/usr/bin/node`. Without it, yt-dlp looks for Deno |
| `--ffmpeg-location PATH` | the ffmpeg program or its folder, when it isn't on your PATH |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | everything asked for was found and saved |
| `1` | nothing could be read from the link, nothing was saved, or at least one item failed |
| `2` | the command was used wrongly, such as neither a URL nor `--from-json` |

## Examples

**See what's in a post, then pick items:**

```bash
content-dl --list-only "https://www.instagram.com/p/POST_ID/"
```

```text
[instagram] POST_ID: 8 item(s)

INDEX  MEDIA TYPE  EXT  RESOLUTION  DURATION  FORMATS  PROVIDER
1      image       jpg                                 yt-dlp
2      image       jpg                                 yt-dlp
...
8      image       jpg                                 yt-dlp
```

```bash
content-dl --items image:1,image:4 "https://www.instagram.com/p/POST_ID/"
```

**Only the photos of a mixed post:**

```bash
content-dl --media-types images "https://www.threads.com/@USERNAME/post/POST_ID"
```

**A post and its uploader's profile picture:**

```bash
content-dl --media-types video,image,avatar "https://x.com/USERNAME/status/POST_ID"
```

**Just the profile picture of a post's uploader:**

```bash
content-dl --media-types avatar "https://www.tiktok.com/@USERNAME/video/VIDEO_ID"
```

**A YouTube video at 720p at most:**

```bash
content-dl --max-quality 720 "https://www.youtube.com/watch?v=VIDEO_ID"
```

**A YouTube video's sound only:**

```bash
content-dl --media-types audio "https://www.youtube.com/watch?v=VIDEO_ID"
```

**A video's thumbnail:**

```bash
content-dl --media-types thumbnail "https://www.youtube.com/watch?v=VIDEO_ID"
```

**An exact yt-dlp format:**

```bash
content-dl --ytdlp-format "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b" "https://www.youtube.com/watch?v=VIDEO_ID"
```

**Files with metadata written into them:**

```bash
content-dl --embed-metadata "https://www.reddit.com/r/SUBREDDIT/comments/POST_ID/"
```

**A Facebook photo post (needs cookies):**

```bash
content-dl --cookies facebook-cookies.txt "https://www.facebook.com/share/p/SHARE_CODE/"
```

**Share links work as they are:**

```bash
content-dl "https://vm.tiktok.com/SHARE_CODE/"
```

```bash
content-dl "https://pin.it/SHARE_CODE"
```

```bash
content-dl "https://www.reddit.com/r/SUBREDDIT/s/SHARE_CODE"
```

**Folders per site and uploader, dated:**

```bash
content-dl --filename-template "%(platform)s/%(uploader.username|unknown)s/%(upload_date).10s%(display_title& {})s (%(id)s)%(number&-{})s.%(ext)s" URL
```

This gives `instagram/someone/2026-10-30 sunset at the beach (C9xYz123)-07.jpg`.

**Save the result now, download later (within a few hours):**

```bash
content-dl --json-only "https://www.instagram.com/p/POST_ID/" > post.json
```

```bash
content-dl --from-json post.json --items image:2
```

**Several links in a row** (PowerShell):

```powershell
Get-Content links.txt | ForEach-Object { content-dl $_ }
```

macOS or Linux:

```bash
while read -r url; do content-dl "$url"; done < links.txt
```

## JSON output and the Python API

`--json-only` prints the whole result. A post looks like this (shortened):

```json
{
  "type": "post",
  "platform": "instagram",
  "id": "C9xYz123",
  "title": null,
  "description": "sunset at the beach",
  "upload_date": "2026-10-30T18:22:00Z",
  "url": "https://www.instagram.com/p/C9xYz123/",
  "uploader": {
    "type": "profile",
    "platform": "instagram",
    "id": "123456",
    "username": "someone",
    "name": "Some One",
    "url": "https://www.instagram.com/USERNAME/",
    "avatar": { "media_type": "avatar", "url": "https://...", "ext": "jpg", "provider": "native" }
  },
  "items": [
    { "index": 1, "media_type": "image", "url": "https://...", "ext": "jpg", "width": 1440, "height": 1800, "provider": "gallery-dl" },
    { "index": 2, "media_type": "video", "ext": "mp4", "duration": 14.2, "formats": [], "provider": "yt-dlp" },
    { "index": 2, "media_type": "thumbnail", "url": "https://...", "ext": "jpg", "provider": "yt-dlp" }
  ]
}
```

- A field the site doesn't give is `null` on posts and profiles, and left out on items. content-dl never guesses a value.
- Items at the same `index` belong together, such as a video and its cover image.
- `formats` lists a video's streams (resolution, codecs, bitrate, link), as yt-dlp does.
- A profile link gives `"type": "profile"` at the top, with `username`, `name`, `avatar` and no `upload_date`.

The same from Python:

```python
from content_dl import Cookies, download, extract

result = extract("https://www.threads.com/@USERNAME/post/POST_ID")
print(result.description, [item.media_type for item in result.items])

for outcome in download(result, media_types=["image"]):
    print(outcome.status, outcome.path)

logged_in = extract("https://www.instagram.com/USERNAME/", cookies=Cookies(file="instagram-cookies.txt"))
```

`download()` takes the same choices as the command line: `media_types`, `picks` (from `content_dl.download.parse_item_picks("image:1,avatar")`), `output` (a file name template), `format`, `max_quality`, `embed_metadata` and `cookies`.

## Routing overrides

For each site, content-dl tries tools in a fixed order per media type (yt-dlp, gallery-dl, or its own `native` code) and merges what they return. The defaults are in [src/content_dl/routes.json](src/content_dl/routes.json). If a site changes and one tool breaks, you can change the order without waiting for an update, using a JSON file that only lists what it changes:

```json
{
  "disable": { "tiktok": ["yt-dlp"] },
  "routes": { "x": { "images": ["gallery-dl", "yt-dlp"] } },
  "domains": { "vimeo.com": "vimeo" }
}
```

- `disable` takes tools out of every column of a site, or of every site with `"*"`.
- `routes` replaces single cells. Columns are `video`, `images`, `thumbnail`, `avatar` and `metadata`.
- `routes_with_cookies` does the same for the order used when cookies are given.
- `domains` adds or changes which site a domain belongs to.

content-dl reads the override from, in order: `--routes FILE`, the `CONTENT_DL_ROUTES` environment variable, then `content-dl/routes.json` in your config folder (`%APPDATA%` on Windows, `~/.config` elsewhere).

## Environment variables

Each one does the same as its flag, for every command, so you can set it once instead of typing the flag each time. A flag given on the command line still wins.

| Variable | Same as |
| --- | --- |
| `CONTENT_DL_ROUTES` | `--routes FILE` |
| `CONTENT_DL_JS_RUNTIMES` | `--js-runtimes` |
| `CONTENT_DL_FFMPEG_LOCATION` | `--ffmpeg-location` |

## Troubleshooting

**`content-dl` is not recognized / command not found.** Activate the virtual environment first (see [Install](#install)), or run it as `python -m content_dl URL` from the `content-dl` folder.

**"... only shows this post to logged-in accounts".** The post needs cookies. Follow [Cookies](#cookies-logged-in-content). If you already gave cookies, they've expired: export them again.

**"no avatar found in this post".** Instagram only shares profile pictures with a logged-in session. Add `--cookies`.

**A YouTube video downloads in low quality, or only its thumbnail.** Install Deno and ffmpeg (see [Install](#install)), then update yt-dlp (see [Updating](#updating)). YouTube changes often, and an old yt-dlp stops working within weeks.

**A site that used to work stopped working.** Update first. If it still fails, run with `--list-only` to see what was found, and try `--provider yt-dlp` or `--provider gallery-dl` to see which tool still reads the site. You can then reorder the tools with a [routing override](#routing-overrides).

**Strange characters in file names on Windows.** Captions with emoji are kept as they are; Windows Explorer shows them fine. Use your own `--filename-template` with `%(id)s` if you'd rather have plain names.

**A file was skipped as "already downloaded".** A file with that name already exists in the folder. Delete or move it to download it again.

## Updating

**Executable.** Download the newest file from the [latest release](https://github.com/hammadxp/content-dl/releases/latest) and replace the old one. New releases follow yt-dlp and gallery-dl fixes closely, so update when a site stops working.

**From source.** Sites change often, and yt-dlp and gallery-dl ship fixes every few days. Update them regularly, with the virtual environment active:

```bash
pip install -U yt-dlp gallery-dl
```

To update content-dl itself, from the `content-dl` folder:

```bash
git pull
```

```bash
pip install -e .
```

## Android

content-dl runs on Android inside Markhor, an app built from a fork of [Seal](https://github.com/JunkFood02/Seal) that uses the Python runtime Seal ships. Each release publishes `content-dl.pyz` next to the executables: a single zipapp with content-dl, yt-dlp, gallery-dl and their pure-Python dependencies, at the same versions as the executables.

Markhor ships one `content-dl.pyz` inside the APK and checks the [latest release](https://github.com/hammadxp/content-dl/releases/latest) once a day. A newer zipapp is downloaded, checked against `SHA2-256SUMS`, started once with `--version`, and only then used. So a fix in content-dl, or a new yt-dlp or gallery-dl, reaches the phone with a release here, without a new APK. Markhor doesn't update yt-dlp any other way.

To build the zipapp yourself:

```bash
python scripts/build_pyz.py
```

Run it the way the phone does (no site-packages):

```bash
python -S dist/content-dl.pyz --version
```

## Development

Install the test tools and run the offline tests:

```bash
pip install -e ".[dev]"
```

```bash
python -m pytest
```

Check every real link in [tests/live_urls.json](tests/live_urls.json) against the live sites (nothing is downloaded):

```bash
python scripts/live_check.py
```

```bash
python scripts/live_check.py --only instagram,facebook --cookies cookies.txt
```

Build the executable locally (it lands in `dist/`). [bundle/requirements.txt](bundle/requirements.txt) holds the exact versions releases ship:

```bash
pip install -r bundle/requirements.txt .
```

```bash
python scripts/build_exe.py
```

Pushing to `main` builds the Windows, macOS and Linux executables on GitHub Actions and publishes them as release `v1.<commit count>` ([.github/workflows/release.yml](.github/workflows/release.yml)). Commits that only change docs or tests don't make a release. Run the workflow by hand from the Actions tab to release without a code change.

Dependabot ([.github/dependabot.yml](.github/dependabot.yml)) checks `bundle/requirements.txt` every day. When yt-dlp, gallery-dl or another bundled tool has a new release, it opens a PR that bumps the pin. [.github/workflows/checks.yml](.github/workflows/checks.yml) tests and builds every PR on all three systems. Dependabot PRs that pass are merged automatically and published as a new release. A failing one stays open for you to look at. Once a week Dependabot also opens a PR that updates the GitHub Actions the workflows use. Those change workflow files, so they wait for you to merge them. `pyproject.toml` only holds the minimum versions for source installs and doesn't change for these updates.

[SPEC.md](SPEC.md) describes the design: the data model, how each site is read, and why the tools are ordered as they are.

## Support

[![Support me on Patreon](https://img.shields.io/badge/Patreon-Support%20content--dl-F96854?style=for-the-badge&logo=patreon&logoColor=white)](https://patreon.com/hammadxp)

content-dl is free and open source. If you find it useful, you can support its development on [Patreon](https://patreon.com/hammadxp) with the **content-dl supporter** tier. Supporters are listed below and updated daily.

<!-- sponsors:start -->
No supporters yet. [Be the first!](https://patreon.com/hammadxp)
<!-- sponsors:end -->
