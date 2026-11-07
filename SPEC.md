# content-dl spec

Nov 7, 2026 · @Hammad Javed

## Goal and scope

content-dl takes one URL and returns everything that can be extracted from it, in one format, with consistent metadata. It works like yt-dlp: you can list what's available, dump it as JSON, or download what you pick. On top of video and audio streams it also returns images, thumbnails and profile pictures.

**In scope for v1**

- Platforms: YouTube, Instagram, Threads, X, TikTok, Reddit, Snapchat, plus a generic fallback for unknown sites.
- Item kinds: video, audio, image, thumbnail, avatar (profile picture).
- Metadata on every result: id, title, uploader, upload date, platform. A field the platform doesn't provide is `null` (see the serialisation rules under Data model).
- Public content by default. Cookies are optional: when the user supplies them (a cookies.txt file or a browser's cookie store), every provider gets them and the router prefers logged-in routes. content-dl never signs in or stores credentials itself.
- Runs on desktop (CLI, Python API, optional HTTP server) and on Android, embedded in the Seal fork.

**Out of scope for v1**

- Signing in for the user or storing credentials. Private accounts and stories work only as far as the user's own cookies already give access, and aren't tested.
- Downloading a whole profile or feed (one post or one profile at a time).
- Supporting every site on the internet. The generic fallback is a best effort.

## Architecture

The Seal fork, the CLI and the HTTP server are thin shells over one `extract()` call. Only the providers know about yt-dlp, gallery-dl or cobalt; everything after the merger sees content-dl's own format.

&#91;embedded content: content-dl architecture · 3 interfaces, 4 providers, 1 output format\]

Providers are tried in the order the routing table gives, and the merger combines whatever each one returned into a single result.

## Wrap the tools, don't fork them

content-dl uses yt-dlp, gallery-dl and cobalt as dependencies and translates their output. Only the gaps (Threads, Instagram avatars) are written from scratch. The single source of logic is content-dl's own format and routing, not merged copies of three codebases.

| Tool | Language | How content-dl uses it | Why not fork it |
| --- | --- | --- | --- |
| yt-dlp | Python | Imported as a library: `YoutubeDL.extract_info(url, download=False)` | Site fixes land almost daily; a fork would fall behind within weeks |
| gallery-dl | Python | Imported as a library: its `DataJob` returns metadata without downloading | Same reason; it already handles single posts as well as collections |
| cobalt | JavaScript (Node) | Optional remote provider: calls a self-hosted instance over HTTP | Can't be merged into a Python codebase; a few of its extractors may be ported to native providers later |
| Native providers | Python (content-dl) | Our own extractors for gaps no tool covers | Nothing to fork |

Upstream PRs (Threads to gallery-dl, for example) are still worth opening, but content-dl never waits on them.

## Data model

Every result is either a **Post** or a **Profile**, and both hold a list of **Items**. Metadata belongs to whichever object it describes, which is why a profile picture never needs an upload date.

| Object | Fields | Notes |
| --- | --- | --- |
| Post | `type`, `platform`, `id`, `title`, `description`, `upload_date`, `url`, `uploader` (a Profile), `items` | `title` is often `null` on social platforms; `description` holds the caption |
| Profile | `type`, `platform`, `id`, `username`, `name`, `url`, `avatar` (an Item), `items` | No `upload_date`. A profile URL returns this as the top-level result |
| Item | `index`, `kind`, `url`, `ext`, `width`, `height`, `duration`, `formats`, `provider`, `extra` | `kind` is one of `video`, `audio`, `image`, `thumbnail`, `avatar` |

**Rules**

1. **Never invent a value.** A field the platform doesn't expose is unset, never guessed. Output templates fall back to the next field instead (title, then description, then id). How an unset field is written depends on the object:
   - **Post and Profile** always write every field, and an unset one is `null`.
   - **Item** writes only the fields that are set, so a thumbnail has no `duration` key and a video served only through `formats` has no `url` key. An empty `formats: []` is still written, because it means the item is a stream source.
   - **A Post's `uploader`** is a Profile that leaves out `items` when the list is empty, since a post never extracts its uploader's other content. A top-level Profile always writes `items`, even as `[]`.
   - `platform` is always set, because the router knows it before any provider runs. A supported platform uses its name (`youtube`, `instagram`); an unknown site uses its domain without `www.` (`example.com`).
2. **Providers fill, the merger combines.** Each provider fills what it can. The merger picks each field from the highest-priority provider that has it, and removes duplicate items (same media id, or same URL with the size query stripped).
3. **Record where each item came from.** Every Item carries `provider`, and the provider's raw data goes in `extra` for debugging.
4. **Upload dates are UTC, ISO 8601** (`2026-10-30T18:22:00Z`). Providers convert timestamps on the way in, and a Post converts any upload date it is given with an offset. When a platform only gives the day, `upload_date` is the day alone (`2005-04-24`), never a made-up midnight.
5. **Items in a carousel share an `index`** with their thumbnail, so a UI can group a video with its cover image.

## Example output

An Instagram carousel post with one image and one video. Here yt-dlp returned the video and its thumbnail but not the image, so the next provider in the images column, gallery-dl, filled it in, and the merger combined them.

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
    "url": "https://www.instagram.com/someone/",
    "avatar": { "kind": "avatar", "url": "https://...", "ext": "jpg", "width": 1080, "height": 1080, "provider": "native" }
  },
  "items": [
    { "index": 1, "kind": "image", "url": "https://...", "ext": "jpg", "width": 1440, "height": 1800, "provider": "gallery-dl" },
    { "index": 2, "kind": "video", "ext": "mp4", "duration": 14.2, "formats": [], "provider": "yt-dlp" },
    { "index": 2, "kind": "thumbnail", "url": "https://...", "ext": "jpg", "provider": "yt-dlp" }
  ]
}
```

The same account's profile URL returns a Profile at the top level, with no `upload_date`:

```json
{
  "type": "profile",
  "platform": "instagram",
  "id": "123456",
  "username": "someone",
  "name": "Some One",
  "url": "https://www.instagram.com/someone/",
  "avatar": { "kind": "avatar", "url": "https://...", "ext": "jpg", "width": 1080, "height": 1080, "provider": "native" },
  "items": []
}
```

`formats` on a video or audio item holds the yt-dlp-style list of streams (resolution, codecs, bitrate, plus each stream's `url` and the `http_headers` needed to fetch it) so a UI can offer quality choices. Stream URLs usually expire within hours and can be tied to the IP that extracted them, so download soon after extracting. The examples above leave `formats` empty to stay short.

## Providers and routing

The router maps a URL to a platform, then looks up which providers to try for each item kind. Providers in a cell are tried left to right, and their results are merged, so a later provider can fill fields an earlier one left empty. The table lives in a JSON config so it can change without a code release.

| Platform | video / audio | images | thumbnail | avatar | metadata |
| --- | --- | --- | --- | --- | --- |
| YouTube | yt-dlp | none | yt-dlp | yt-dlp | yt-dlp |
| Instagram | yt-dlp > cobalt > gallery-dl | yt-dlp > gallery-dl > cobalt | yt-dlp | native | yt-dlp > gallery-dl |
| Threads | native | native | native | native | native |
| X | yt-dlp > cobalt | gallery-dl > cobalt | yt-dlp | gallery-dl | yt-dlp > gallery-dl |
| TikTok | yt-dlp > cobalt | gallery-dl | yt-dlp | gallery-dl | yt-dlp |
| Reddit | yt-dlp > cobalt | gallery-dl | yt-dlp | gallery-dl | gallery-dl > yt-dlp |
| Snapchat | cobalt | cobalt | cobalt | none | native (page OG tags) |
| Unknown site | yt-dlp > gallery-dl > generic | gallery-dl > generic | generic | none | yt-dlp > generic |

**With cookies.** When the user supplies cookies, a platform can switch some cells to logged-in providers; every other cell stays as above.

| Platform | images | metadata | Why |
| --- | --- | --- | --- |
| Instagram | gallery-dl > yt-dlp > cobalt | gallery-dl > yt-dlp | With a session, gallery-dl's app API works and returns more than yt-dlp's logged-out route (image sizes, full owner data) |

**What each provider is good at**

- **yt-dlp:** video and audio streams with every format, and uniform metadata (`id`, `title`, `uploader`, `timestamp`, `extractor_key`). On YouTube it also gives the channel avatar: a channel URL returns a Profile with its avatar, and a video costs one extra channel-page request to fill the uploader's avatar (skipped when avatars aren't wanted). Playlist URLs aren't supported. On Instagram it reads public posts logged out, from the JSON embedded in the post page or, when `curl_cffi` is installed, from the logged-out GraphQL query the website uses, which is why it leads Instagram's images and metadata. A carousel comes back with every image and video.
- **gallery-dl:** images and carousels, single posts or whole collections. Metadata field names differ per site, so its adapter has a small mapper per platform. On Instagram it only uses the app API, which redirects to the login page without a session, so there it is a fallback.
- **cobalt:** a working media URL when the others fail. It returns almost no metadata, so it is never the metadata source. It runs only as a remote server, on a datacenter IP that Instagram rate-limits sooner, so it is always a fallback.
- **native:** our own code. Threads posts and profiles (JSON embedded in the page), Instagram profile pictures (`web_profile_info`), and the generic fallback (`og:image` and `og:video` tags). On an Instagram post URL, which doesn't contain the username, it reads the uploader's username from what the metadata providers returned and adds only the avatar. Threads embeds the post JSON only for browser-like clients, so native requests go through `curl_cffi` when it is installed, falling back to `requests`.

A provider that raises an error or returns no items for the asked kind passes the request to the next one in its cell.

## CLI and HTTP API

The CLI copies yt-dlp's flags where they mean the same thing, so it feels familiar. The HTTP API returns exactly the JSON that `-J` prints.

| Command | What it does |
| --- | --- |
| `content-dl URL` | Download the default items (best video, or all images for an image post) |
| `content-dl -J URL` | Print the full result as JSON, download nothing |
| `content-dl -F URL` | List available items and video formats as a table |
| `content-dl --want images,avatar URL` | Download only these item kinds |
| `content-dl -f "bv*+ba/b" URL` | yt-dlp format selection, passed through for video items |
| `content-dl -o "%(platform)s/%(uploader.username)s/%(id)s_%(index)s.%(ext)s" URL` | Output file name template, nested fields with dots |
| `content-dl --provider gallery-dl URL` | Force one provider, for debugging |
| `content-dl --cookies cookies.txt URL` | Use cookies from a Netscape cookies.txt file, and prefer logged-in routes |
| `content-dl --cookies-from-browser firefox URL` | Same, reading the cookies from a browser's store (`BROWSER[:PROFILE]`) |
| `content-dl --embed-metadata URL` | Write metadata into the files (ffmpeg tags for video and audio, EXIF/XMP for images) |
| `content-dl serve --port 8080` | Start the optional HTTP server |

**HTTP endpoints** (for `content-dl serve`)

- `GET /extract?url=<url>&want=<kinds>` returns the result JSON.
- `GET /download?url=<url>&item=<index>&kind=<kind>` streams one item's file.

The Python API is the same thing without the process: `content_dl.extract(url, want=[...])` returns the result object, and `extract(url, cookies=Cookies(file="cookies.txt"))` passes cookies.

## Android and the Seal fork

content-dl runs on the phone, embedded in the Seal fork, the same way yt-dlp runs inside Seal today. The Seal fork becomes a UI over content-dl and stops talking to yt-dlp directly.

**Why on the phone:** requests go out from your mobile IP, which Instagram and Threads treat as a normal user. A server on a datacenter IP gets rate-limited much sooner.

**Runtime:** Seal uses youtubedl-android, which bundles a Python runtime. content-dl and gallery-dl are pure Python and would be added to that bundle. If that runtime turns out to be missing packages content-dl needs (gallery-dl needs `requests`), the fallback is Chaquopy.

**Share-sheet flow in the fork**

1. The user shares a URL to the app.
2. The app runs `extract(url)` and shows what was found, grouped: "3 images, 1 video, avatar".
3. The user picks items (and a video quality, from `formats`).
4. The app downloads them. Plain files go through the app's own downloader; yt-dlp video and audio stay with yt-dlp, because it handles stream merging and ffmpeg.
5. The result and its metadata are saved to Seal's existing Room download-history database.

**Updates:** yt-dlp, gallery-dl, content-dl's native providers and the routing table all update without a new APK, the same way Seal updates yt-dlp today.

**cobalt on Android:** the app calls it over HTTP only when the user has configured an instance URL. Without one, cobalt is skipped.

**Cookies on Android:** Seal already has a cookies setting for yt-dlp. The fork passes the same cookies to content-dl as a cookies.txt file, so the logged-in routes work on the phone too.

## Build order

Each milestone can be tested from the command line on desktop. Seal isn't touched until milestone 7.

1. [x] **Schema.** Post, Profile and Item as typed Python classes, with JSON serialisation.
   - Done when: the two examples above round-trip through the classes unchanged.
2. [x] **CLI and the yt-dlp provider.** Project setup, router, `-J` and `-F`.
   - Done when: `content-dl -J <youtube url>` prints a valid Post with video, audio, thumbnail and metadata.
3. [ ] **gallery-dl provider and the merger.**
   - Done when: an Instagram carousel returns every image and video once, with uploader and upload date filled.
4. [ ] **Native providers.** Threads posts and Instagram profile pictures.
   - Done when: a Threads carousel and an Instagram profile URL both return complete results.
5. [ ] **Downloader.** `--want`, `-o` templates, `--embed-metadata`.
   - Done when: one command saves a mixed post's files with correct names and embedded metadata.
6. [ ] **cobalt provider and the routing config.** Fallbacks driven by the JSON table.
   - Done when: turning off yt-dlp for a platform in the config makes cobalt serve that platform's video.
7. [ ] **Android.** content-dl running inside the Seal fork's Python runtime.
   - Done when: the fork prints `extract()` output for a shared URL on a real phone.
8. [ ] **Seal fork UI.** Share-sheet picker, downloads, history.
   - Done when: sharing an Instagram post lets you pick and save individual images and the avatar.

## Open questions and risks

| Risk or question | Impact | Plan |
| --- | --- | --- |
| Does youtubedl-android's Python runtime include `requests` and the other packages gallery-dl needs? | Blocks gallery-dl on Android | Check at the start of milestone 7; fall back to Chaquopy |
| Heavy use of the user's cookies can get their account rate-limited or flagged | The user's own account is at risk | Cookies stay opt-in; say so in the CLI help and the fork's settings. Note that yt-dlp writes refreshed cookies back to a `--cookies` file |
| `curl_cffi` (yt-dlp's browser impersonation) is a compiled package; its only Android wheel is arm64 for Python 3.13+ | yt-dlp loses the logged-out Instagram GraphQL route on Android and falls back to the post page | Check in milestone 7; yt-dlp works without it, so leave it out of the Android bundle if it won't install |
| Instagram and Threads change their page JSON and endpoints without notice | Native providers break | Keep native providers small, test them against saved pages, ship fixes as runtime updates |
| Rate limits on Instagram and Threads, even from a mobile IP | Requests fail after heavy use | Cache results, back off on errors, fall back to the next provider |
| cobalt's public instance requires bot verification | Can't rely on it | Self-host it; treat it as optional |
| gallery-dl's `extractor.module-sources` option for loading extra extractors | Decides whether Threads can be a gallery-dl extractor | Decided in milestone 4: Threads is a content-dl native provider, which needs no gallery-dl extension point |
| Instagram's `web_profile_info` answered HTTP 404 to a logged-out desktop request (Nov 7, 2026), and the logged-out profile page was an error page | Instagram avatars may need the user's cookies, or another route | Check from a mobile IP and with cookies; if it stays closed, find another logged-out source for the avatar |
| Licensing: yt-dlp is Unlicense, gallery-dl GPL-2.0, cobalt AGPL-3.0, Seal GPL-3.0 | Affects how content-dl can be licensed and shipped | Confirm each license before the first release |
