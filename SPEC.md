# content-dl spec

Nov 7, 2026 · @Hammad Javed

## Goal and scope

content-dl takes one URL and returns everything that can be extracted from it, in one format, with consistent metadata. It works like yt-dlp: you can list what's available, dump it as JSON, or download what you pick. On top of video and audio streams it also returns images, thumbnails and profile pictures.

**In scope for v1**

- Platforms: YouTube, Instagram, Threads, X, TikTok, Reddit, Facebook, Pinterest, Snapchat, plus a generic fallback for unknown sites.
- Media types: video, audio, image, thumbnail, avatar (profile picture).
- Metadata on every result: id, title, uploader, upload date, platform. A field the platform doesn't provide is `null` (see the serialisation rules under Data model).
- Public content by default. Cookies are optional: when the user supplies them (a cookies.txt file or a browser's cookie store), every provider gets them and the router prefers logged-in routes. content-dl never signs in or stores credentials itself.
- Runs on desktop (CLI, Python API, optional HTTP server) and on Android, embedded in the Seal fork.

**Out of scope for v1**

- Signing in for the user or storing credentials. Private accounts and stories work only as far as the user's own cookies already give access, and aren't tested.
- Downloading a whole profile or feed (one post or one profile at a time).
- Supporting every site on the internet. The generic fallback is a best effort.

## Architecture

The Seal fork, the CLI and the HTTP server are thin shells over one `extract()` call. Only the providers know about yt-dlp or gallery-dl; everything after the merger sees content-dl's own format.

&#91;embedded content: content-dl architecture · 3 interfaces, 4 providers, 1 output format\]

Providers are tried in the order the routing table gives, and the merger combines whatever each one returned into a single result.

## Wrap the tools, don't fork them

content-dl uses yt-dlp and gallery-dl as dependencies and translates their output. The gaps neither covers (Threads, Reddit, Snapchat, Facebook posts, avatars) are written from scratch as native providers. The single source of logic is content-dl's own format and routing, not merged copies of other codebases.

| Tool | Language | How content-dl uses it | Why not fork it |
| --- | --- | --- | --- |
| yt-dlp | Python | Imported as a library: `YoutubeDL.extract_info(url, download=False)` | Site fixes land almost daily; a fork would fall behind within weeks |
| gallery-dl | Python | Imported as a library: its `DataJob` returns metadata without downloading | Same reason; it already handles single posts as well as collections |
| Native providers | Python (content-dl) | Our own extractors for gaps no tool covers | Nothing to fork |

Upstream PRs (Threads to gallery-dl, for example) are still worth opening, but content-dl never waits on them.

## Data model

Every result is either a **Post** or a **Profile**, and both hold a list of **Items**. Metadata belongs to whichever object it describes, which is why a profile picture never needs an upload date.

| Object | Fields | Notes |
| --- | --- | --- |
| Post | `type`, `platform`, `id`, `title`, `description`, `upload_date`, `url`, `uploader` (a Profile), `items` | `title` is often `null` on social platforms; `description` holds the caption |
| Profile | `type`, `platform`, `id`, `username`, `name`, `url`, `avatar` (an Item), `items` | No `upload_date`. A profile URL returns this as the top-level result |
| Item | `index`, `media_type`, `url`, `ext`, `width`, `height`, `duration`, `formats`, `provider`, `extra` | `media_type` is one of `video`, `audio`, `image`, `thumbnail`, `avatar` |

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
    "avatar": { "media_type": "avatar", "url": "https://...", "ext": "jpg", "width": 1080, "height": 1080, "provider": "native" }
  },
  "items": [
    { "index": 1, "media_type": "image", "url": "https://...", "ext": "jpg", "width": 1440, "height": 1800, "provider": "gallery-dl" },
    { "index": 2, "media_type": "video", "ext": "mp4", "duration": 14.2, "formats": [], "provider": "yt-dlp" },
    { "index": 2, "media_type": "thumbnail", "url": "https://...", "ext": "jpg", "provider": "yt-dlp" }
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
  "avatar": { "media_type": "avatar", "url": "https://...", "ext": "jpg", "width": 1080, "height": 1080, "provider": "native" },
  "items": []
}
```

`formats` on a video or audio item holds the yt-dlp-style list of streams (resolution, codecs, bitrate, plus each stream's `url` and the `http_headers` needed to fetch it) so a UI can offer quality choices. Stream URLs usually expire within hours and can be tied to the IP that extracted them, so download soon after extracting. The examples above leave `formats` empty to stay short.

## Providers and routing

The router maps a URL to a platform, then looks up which providers to try for each media type. Providers in a cell are tried left to right, and their results are merged, so a later provider can fill fields an earlier one left empty. The table lives in a JSON config so it can change without a code release. The defaults ship as `routes.json` in the package; a user file (`--routes FILE`, `CONTENT_DL_ROUTES`, or `content-dl/routes.json` in the user config folder) overrides single cells, adds domains and platforms, and can `disable` a provider on one platform or on all (`"*"`).

| Platform | video / audio | images | thumbnail | avatar | metadata |
| --- | --- | --- | --- | --- | --- |
| YouTube | yt-dlp | none | yt-dlp | yt-dlp | yt-dlp |
| Instagram | yt-dlp > gallery-dl | yt-dlp > gallery-dl | yt-dlp | native | yt-dlp > gallery-dl |
| Threads | native | native | native | native | native |
| X | yt-dlp | gallery-dl | yt-dlp | gallery-dl | yt-dlp > gallery-dl |
| TikTok | yt-dlp | gallery-dl | yt-dlp | gallery-dl | yt-dlp |
| Reddit | native | native | native | native | native |
| Facebook | yt-dlp | gallery-dl > native | yt-dlp | gallery-dl | native > yt-dlp > gallery-dl |
| Pinterest | yt-dlp > gallery-dl | yt-dlp > gallery-dl | yt-dlp | native | native > yt-dlp > gallery-dl |
| Snapchat | native > gallery-dl | none (gallery-dl's photos merge in) | native > gallery-dl | native | native > gallery-dl |
| Unknown site | yt-dlp > gallery-dl > generic | gallery-dl > generic | generic | none | yt-dlp > generic |

**With cookies.** When the user supplies cookies, a platform can switch some cells to logged-in providers; every other cell stays as above.

| Platform | images | avatar | metadata | Why |
| --- | --- | --- | --- | --- |
| Instagram | gallery-dl > yt-dlp | gallery-dl > native | gallery-dl > yt-dlp | With a session, gallery-dl's app API works and returns more than yt-dlp's logged-out route (image sizes, full owner data). Its `/USER/avatar/` extractor also gives the HD profile picture, which content-dl's own `web_profile_info` lookup doesn't get even with a session (Nov 7, 2026: Instagram answered it with the logged-out page) |
| Reddit | native > gallery-dl (video: native > yt-dlp, thumbnail: native > yt-dlp) | native > gallery-dl | native > gallery-dl > yt-dlp | Logged out, Reddit blocks yt-dlp's and gallery-dl's requests (Nov 8, 2026), so they'd only add seconds of failed requests; with a session they can stand in if the embed page changes |

**Facebook links.** The app shares `/share/...` links (and `fb.watch` for videos) that only redirect. extract() follows them first, through yt-dlp's HTTP client (Facebook doesn't redirect a bare request) and with the user's cookies if given, because gallery-dl matches URL patterns and wouldn't recognise the share link. A post that needs a login resolves to the login page, so its `next` parameter is unwrapped to the post's own address, and the share's tracking parameters (`rdid`, `share_url`, `mibextid`) are dropped. A profile URL (`/people/NAME/ID/`, `/profile.php?id=ID` or `/NAME`) goes to gallery-dl's `/ID/avatar` extractor, which returns the profile picture; its profile extractor would queue the whole photo feed instead. Logged out, Facebook refuses the avatar's photo page, so profiles need cookies (Nov 7, 2026). Reels and videos work logged out through yt-dlp. Neither yt-dlp (videos only) nor gallery-dl (photos, albums, profiles) reads a regular post, so native reads the logged-in post page: the author, text, date and each photo, from the JSON the page embeds (the post's own objects carry its `post_id`, which keeps the page's comments, ads and suggested posts out). The page only holds reduced copies of the photos, so native lists each photo's page, and gallery-dl's photo extractor, running after it, fetches the full size there; the merger keeps that copy. Posts need cookies.

**Pinterest links.** The app shares `pin.it` links, which extract() resolves first through Pinterest's URL shortener and strips of their share parameters (`invite_code`, `sender`); gallery-dl matches a profile URL only without a query, and neither tool knows a profile or an invite page. A profile URL (`/USER/`) goes to native, which reads the web app's `UserResource` for the name and avatar (the `/originals/` upload when Pinterest serves it, else the 280px copy); gallery-dl's user extractor would queue every board. On a pin, native looks up the uploader's avatar by the numeric id yt-dlp returns, so it waits until yt-dlp has run. A collaborative board invite (`/invited/?board_id=...`) is turned into the board's URL through `BoardResource`; a private board is found only with cookies, and logged out the invite fails with that hint. yt-dlp returns an image pin as a stream-less entry, which counts as an `image`, not a thumbnail (Nov 8, 2026). Pinterest sometimes drops a TLS connection mid-request, and yt-dlp doesn't retry an extractor's metadata requests, so the yt-dlp provider tries a URL up to three times when the failure is a network error (never on an HTTP error). gallery-dl can't stand in for a board, since it extracts one post at a time.

**Snapchat links.** The app shares `snapchat.com/t/CODE` links, which extract() follows to the Spotlight page, and profile links as `/add/USER`, which become `/@USER`; the share parameters (`share_id`, `locale`, `sid`) are dropped, since gallery-dl matches only `/@USER` profile URLs. Snapchat's pages moved to Next.js's app router and no longer embed the `__NEXT_DATA__` blob that yt-dlp's and gallery-dl's Spotlight extractors read (Nov 8, 2026), so native reads the schema.org JSON every page still carries for search engines: a Spotlight's `VideoObject` gives the MP4, poster, size, duration, date and creator, and a profile's `ProfilePage` gives the name and picture. The picture URL's resize suffix (`._RS0,90_FMjpeg`) is swapped for `._FMjpeg`, which serves the uploaded size as a JPEG. An anonymous Spotlight poster has no username or picture. Highlights and stories go to gallery-dl, whose extractors still read them. gallery-dl isn't listed for images or avatars: as the metadata fallback it has already run on every highlight, story or profile native can't read, and a provider's items outside the column still merge in when the column has none. Listed there, it would only be asked about a Spotlight, which is always a video, or an anonymous poster's avatar, and fail.

**Reddit links.** Since Nov 2026 Reddit answers logged-out requests to its JSON API and post pages with a "blocked by network security" check, so yt-dlp and gallery-dl fail on every post without cookies. The embed page sites use to show a post (`embed.reddit.com/r/SUB/comments/ID/`) is still served logged out, and native reads the title, author, date, the full-size images (`i.redd.it/<media id>`, in gallery order) and the video's HLS playlist from it; yt-dlp's generic extractor turns the playlist into video and audio formats (Nov 8, 2026: 1080p with audio). App share links (`/r/SUB/s/CODE`, `/u/USER/s/CODE`) and `redd.it` links are resolved first and stripped of `share_id` and `utm_*`. A profile or community URL gives its icon from `about.json`, which Reddit blocks logged out, so it needs cookies; with cookies, a post's uploader avatar comes from the same lookup. `i.redd.it` sends a request that looks like a page visit (browser `Accept: text/html`) to the `reddit.com/media` viewer instead of the file, so the downloader sends each media type's own `Accept` header, and never saves an HTML answer as a media file.

**What each provider is good at**

- **yt-dlp:** video and audio streams with every format, and uniform metadata (`id`, `title`, `uploader`, `timestamp`, `extractor_key`). On YouTube it also gives the channel avatar: a channel URL returns a Profile with its avatar, and a video costs one extra channel-page request to fill the uploader's avatar (skipped when avatars aren't requested). Playlist URLs aren't supported. On Instagram it reads public posts logged out, from the JSON embedded in the post page or, when `curl_cffi` is installed, from the logged-out GraphQL query the website uses, which is why it leads Instagram's images and metadata. A carousel comes back with every image and video.
- **gallery-dl:** images and carousels, single posts or whole collections. Metadata field names differ per site, so its adapter has a small mapper per platform. On Instagram it only uses the app API, which redirects to the login page without a session, so there it is a fallback logged out and the lead logged in. With cookies, a post costs one extra request for the uploader's avatar (skipped when avatars aren't requested).
- **native:** our own code. Threads posts and profiles (JSON embedded in the page), Facebook posts (JSON embedded in the logged-in post page), Instagram profile pictures logged out (`web_profile_info`, which Instagram often refuses), Pinterest profiles and pin uploaders (`UserResource`), Reddit posts from the logged-out embed page and profile icons from `about.json`, and the generic fallback (`og:image` and `og:video` tags). On an Instagram post URL, which doesn't contain the username, it reads the uploader's username from what the metadata providers returned and adds only the avatar. Threads embeds the post JSON only for browser-like clients, so native requests go through `curl_cffi` when it is installed, falling back to `requests`.

A provider that raises an error or returns no items for the asked media type passes the request to the next one in its cell.

## CLI and HTTP API

The CLI's flags are content-dl's own and spelled out, so they aren't mistaken for yt-dlp's. A flag whose value is passed to yt-dlp unchanged says so in its name (`--ytdlp-format`). The HTTP API returns exactly the JSON that `--json-only` prints.

| Command | What it does |
| --- | --- |
| `content-dl URL` | Download the default items (best video, or all images for an image post), in the post's order, named `title or caption (uploader)-07.jpg` (the number only when the post has several items; see `content_dl/naming.py`) |
| `content-dl --json-only URL` | Print the full result as JSON, download nothing |
| `content-dl --list-only URL` | List available items and video formats as a table, download nothing |
| `content-dl --media-types images,avatar URL` | Download only these media types (video, audio, image, thumbnail, avatar; plurals work too) |
| `content-dl --max-quality 1080 URL` | Highest video quality, measured on the shorter side so 1080 covers 1920x1080 and vertical 1080x1920; falls back to the next best quality when there is none at or under it (yt-dlp's `res` format sort) |
| `content-dl --ytdlp-format "bv*+ba/b" URL` | yt-dlp format selection syntax, passed to yt-dlp as-is for video items |
| `content-dl --filename-template "%(platform)s/%(uploader.username)s/%(id)s_%(index)s.%(ext)s" URL` | File name template, nested fields with dots; a folder can go in front, otherwise files save in the current folder |
| `content-dl --provider gallery-dl URL` | Force one provider, for debugging |
| `content-dl --cookies cookies.txt URL` | Use cookies from a Netscape cookies.txt file, and prefer logged-in routes |
| `content-dl --cookies-from-browser firefox URL` | Same, reading the cookies from a browser's store (`BROWSER[:PROFILE]`) |
| `content-dl --embed-metadata URL` | Write metadata into the files (ffmpeg tags for video and audio, EXIF/XMP for images) |
| `content-dl --from-json result.json --items image:1,video:2,avatar --report-json` | Save single items of a result `--json-only` already printed, without extracting again, and report each one as a JSON line (the Seal fork's picker uses this) |
| `content-dl --js-runtimes quickjs:PATH --ffmpeg-location PATH URL` | Tell yt-dlp which JS runtime and ffmpeg to use, as its own flags do (the Seal fork passes youtubedl-android's) |
| `content-dl serve --port 8080` | Start the optional HTTP server |

**HTTP endpoints** (for `content-dl serve`)

- `GET /extract?url=<url>&media_types=<media types>` returns the result JSON.
- `GET /download?url=<url>&item=<index>&media_type=<media type>` streams one item's file.

The Python API is the same thing without the process: `content_dl.extract(url, media_types=[...])` returns the result object, and `extract(url, cookies=Cookies(file="cookies.txt"))` passes cookies.

## Android and the Seal fork

content-dl runs on the phone, embedded in the Seal fork, the same way yt-dlp runs inside Seal today. The Seal fork becomes a UI over content-dl and stops talking to yt-dlp directly.

**Why on the phone:** requests go out from your mobile IP, which Instagram and Threads treat as a normal user. A server on a datacenter IP gets rate-limited much sooner.

**Runtime:** Seal uses youtubedl-android, which bundles CPython 3.11 and starts yt-dlp as `libpython.so <yt-dlp zipapp> ARGS`. Its site-packages holds only `mutagen` and `Cryptodome`, so content-dl ships as its own zipapp, `content-dl.pyz` (built by `scripts/build_pyz.py`), that carries gallery-dl, `requests` and their pure-Python dependencies. The fork starts it the same way, with the same environment, and puts Seal's own yt-dlp zipapp on `PYTHONPATH`, so content-dl imports the copy Seal already keeps up to date. It also passes youtubedl-android's QuickJS and ffmpeg to yt-dlp with `--js-runtimes` and `--ffmpeg-location` (also `CONTENT_DL_JS_RUNTIMES` and `CONTENT_DL_FFMPEG_LOCATION`), as youtubedl-android does for yt-dlp. Chaquopy is no longer needed as a fallback.

**Share-sheet flow in the fork**

1. The user shares a URL to the app.
2. The app runs `extract(url)` and shows what was found, grouped: "3 images, 1 video, avatar".
3. The user picks items (and a video quality, from `formats`).
4. The app saves them with content-dl's downloader, from the result it already has (`--from-json`, `--items`), so nothing is extracted twice. Plain files are fetched by content-dl; video and audio stay with yt-dlp inside content-dl, because it handles stream merging and ffmpeg. Files get content-dl's names and embedded metadata, the same as on desktop, in Seal's download folder.
5. Each saved file is added to the media library and to Seal's existing Room download-history database, one row per file. Still pictures use the saved file as their history thumbnail, because CDN URLs expire.

**Updates:** yt-dlp, gallery-dl, content-dl's native providers and the routing table all update without a new APK, the same way Seal updates yt-dlp today.

**Cookies on Android:** Seal already has a cookies setting for yt-dlp. The fork passes the same cookies to content-dl as a cookies.txt file, so the logged-in routes work on the phone too.

## Build order

Each milestone can be tested from the command line on desktop. Seal isn't touched until milestone 7.

1. [x] **Schema.** Post, Profile and Item as typed Python classes, with JSON serialisation.
   - Done when: the two examples above round-trip through the classes unchanged.
2. [x] **CLI and the yt-dlp provider.** Project setup, router, `--json-only` and `--list-only` (first named `-J` and `-F`).
   - Done when: `content-dl --json-only <youtube url>` prints a valid Post with video, audio, thumbnail and metadata.
3. [ ] **gallery-dl provider and the merger.**
   - Done when: an Instagram carousel returns every image and video once, with uploader and upload date filled.
4. [ ] **Native providers.** Threads posts and Instagram profile pictures.
   - Done when: a Threads carousel and an Instagram profile URL both return complete results.
5. [x] **Downloader.** `--media-types`, `--filename-template` templates, `--embed-metadata`.
   - Done when: one command saves a mixed post's files with correct names and embedded metadata.
6. [x] **Routing config.** Fallbacks driven by the JSON table.
   - Done when: an override file can turn a provider off for one platform or for all.
   - Nov 8, 2026: the cobalt provider was removed. Native providers now cover every platform where it was a fallback, and it needed a self-hosted server.
7. [x] **Android.** content-dl running inside the Seal fork's Python runtime.
   - Done when: the fork prints `extract()` output for a shared URL on a real phone.
   - Checked Nov 7, 2026 on a Pixel 7 (Android 17): sharing a YouTube URL to "Extract with content-dl" (the fork's `content-dl` branch) printed the full Post, with video, audio, thumbnail and avatar, on screen and to Logcat (tag `content-dl`). This needed a current yt-dlp. A fresh install still has youtubedl-android's bundled 2024.09.27 yt-dlp, which YouTube rejects (only the thumbnail comes back) until Seal's yt-dlp update runs.
8. [ ] **Seal fork UI.** Share-sheet picker, downloads, history.
   - Done when: sharing an Instagram post lets you pick and save individual images and the avatar.
   - Status (Nov 7, 2026): the fork's "Extract with content-dl" share target now opens a picker: the uploader, the caption, a summary ("3 images, 1 video, avatar"), then every item grouped by media type, with a quality menu on each video. Picked items save through `content-dl --from-json --items --report-json`, show their progress one by one, and land in Seal's download folder and history. A share runs Seal's yt-dlp update first when yt-dlp has never been updated on that install, or when Seal's auto-update is due. On desktop, the same commands saved a YouTube video's thumbnail and avatar, and its video at a picked quality merged with audio. Instagram refused the desktop IP logged out, so the Instagram check is still to do on a real phone.

## Open questions and risks

| Risk or question | Impact | Plan |
| --- | --- | --- |
| Facebook post pages are a large logged-in app page whose embedded JSON changes without notice | Posts with images break | Done Nov 8, 2026: native reads the post's objects by `post_id` and gallery-dl fetches each photo. Only a single-photo post was checked live; a post with several photos is read from `all_subattachments`, which is untested against a real page. Keep a cut-down fixture (`tests/fixtures/facebook_post.html`), never a real saved page: it holds the user's session |
| Does youtubedl-android's Python runtime include `requests` and the other packages gallery-dl needs? | Blocks gallery-dl on Android | Checked in milestone 7 (youtubedl-android 0.17.3): CPython 3.11 with only `mutagen` and `Cryptodome`. Solved by bundling the pure-Python dependencies in `content-dl.pyz` |
| Heavy use of the user's cookies can get their account rate-limited or flagged | The user's own account is at risk | Cookies stay opt-in; say so in the CLI help and the fork's settings. Note that yt-dlp writes refreshed cookies back to a `--cookies` file |
| `curl_cffi` (yt-dlp's browser impersonation) is a compiled package; its only Android wheel is arm64 for Python 3.13+ | yt-dlp loses the logged-out Instagram GraphQL route on Android and falls back to the post page. The native Threads provider also loses it: with plain `requests`, Threads sent no embedded profile data on desktop or on the phone (Nov 7, 2026), so Threads doesn't work on Android yet | Checked in milestone 7: the runtime is 3.11, so `curl_cffi` is left out of the zipapp. Decide how the native provider reaches Threads without `curl_cffi` (for example a newer Python runtime that can install it) |
| Instagram and Threads change their page JSON and endpoints without notice | Native providers break | Keep native providers small, test them against saved pages, ship fixes as runtime updates |
| Rate limits on Instagram and Threads, even from a mobile IP | Requests fail after heavy use | Cache results, back off on errors, fall back to the next provider |
| gallery-dl's `extractor.module-sources` option for loading extra extractors | Decides whether Threads can be a gallery-dl extractor | Decided in milestone 4: Threads is a content-dl native provider, which needs no gallery-dl extension point |
| Instagram's `web_profile_info` answered HTTP 404 to a logged-out desktop request (Nov 7, 2026), and the logged-out profile page was an error page | Instagram avatars may need the user's cookies, or another route | Check from a mobile IP and with cookies; if it stays closed, find another logged-out source for the avatar |
| A fresh Seal install runs youtubedl-android's bundled yt-dlp (2024.09.27), which YouTube rejects | content-dl gets no YouTube streams until yt-dlp is updated | Done in milestone 8: the share target runs Seal's yt-dlp update before extracting when yt-dlp was never updated on that install, or when Seal's auto-update is due. If the update fails, it extracts with the current yt-dlp |
| Latest yt-dlp needs Python 3.11 or newer | content-dl's `requires-python = ">=3.10"` is looser than its main dependency | The phone's runtime is 3.11, so Android is fine; raise `requires-python` to 3.11 when convenient |
| Licensing: yt-dlp is Unlicense, gallery-dl GPL-2.0, Seal GPL-3.0 | Affects how content-dl can be licensed and shipped | Confirm each license before the first release |
