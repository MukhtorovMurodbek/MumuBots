# `resolvers.py`

[↑ downloader_bot](README.md) · [code](resolvers.py)

Turning a pasted link into files, through whichever route is still working.

```text
WHY THIS EXISTS
    Until v1.2.1 this bot had exactly one way to get a video: yt-dlp, running
    inside the container, fetching from the site directly. That works from a
    phone and does not work from Railway, and the difference is not the code
    -- it is the IP address. Instagram, the unadvertised site and increasingly TikTok decide
    what to serve partly from *where* the request comes from, every cloud
    host's address sits in a published datacenter range, and those ranges get
    a login page where a home connection gets the post. v1.1.3 diagnosed this
    correctly and concluded there were two fixes, cookies or a residential
    proxy, both of which need something the bot cannot supply itself. So
    Instagram simply stopped working, and said so politely.
```

```text
There is a third option, and it is the one that holds: do not make the
request from here at all. The "fix the embed" services and the public
downloader APIs run their own fetching infrastructure -- their addresses,
their cookie pools, their problem -- and hand back either a direct CDN URL
or a copy proxied through themselves. Whether *this* container is on a
blocked range stops mattering.
```

```text
The catch is that any one of those services is mortal. They get bought,
rate-limited, sued (vxtiktok, as of this writing, answers every request
with "due to a legal request, this service is no longer available"), or
just stop resolving in DNS (ddinstagram and kkinstagram both did). A bot
built on one of them is a bot that breaks in a month with no warning.
```

```text
Hence a chain, per platform, with the bot keeping score. Every provider is
tried in turn until one produces media; whichever one worked is
remembered, whichever one failed is remembered too, and a provider that
keeps failing is moved to the back of the queue and retried more and more
rarely instead of being taken out of service. Nothing here needs a human
to notice a provider has died. Adding a replacement is a function and one
line in PROVIDERS.
```

```text
WHAT IS NOT SOLVED HERE
    Nothing makes a private account public, and nothing makes a deleted post
    come back. When every provider says no, the user gets one sentence saying
    which of those two kinds of "no" it looked like, not five stack traces.
```

```text
And the providers were verified from a residential connection, because
that is where this was written. A service that answers here could still be
challenged from Railway's range -- which is exactly the failure the health
scoring is built to absorb: the first user to hit it pays a few seconds,
the chain moves on, and `/providers` says what happened.
```

_At `PROVIDER_TIMEOUT_S = float(os.environ.get("DBOT_PROVIDER_TIMEOUT", "20"))`:_

How long any single provider gets before the chain gives up on it and tries
the next. Deliberately short: the whole point of a chain is that waiting out
a dead provider costs more than moving on, and four providers at 15 s each
is still inside what a user will sit through.

_At `YTDLP_TIMEOUT_S = float(os.environ.get("DBOT_YTDLP_TIMEOUT", "120"))`:_

...except yt-dlp, whose route is not a lookup but the download itself: it
fetches the video while it resolves it, and there is no second step after it
(the other routes hand back a URL, and the file is fetched after the chain
has ended). A route that timed out counts as failed, so the one that was
working would be rested; so it is given time for the download: this long for a
download under the free ceiling, and for one past it, this many more seconds
for every megabyte above the ceiling (2 MB/s is the pace it is asked to
keep). A route that stalls is stopped sooner than that, by yt-dlp's own
socket timeout.

_At `COOLDOWN_AFTER_FAILURES = int(os.environ.get("DBOT_PROVIDER_COOLDOWN_AFTER", "3"))`:_

After this many consecutive failures a provider starts being skipped -- not
dropped. See \_ordered(): it goes to the back of the queue and is still tried
if everything ahead of it fails, which is what lets a service that comes
back from the dead be noticed without anybody redeploying.

## What a resolver hands back

### `MediaItem`

One file. Either a URL to fetch (`url`) or something already on disk
(`path`) -- yt-dlp produces the second kind because it does its own
downloading and there is no sense making it hand back a URL it has
already spent the request on.

- `kind: str` — "video" | "photo" | "audio"
- At `alt_urls: list[str] = field(default_factory=list)`:
  Other URLs for the \*same\* file, tried in order if `url` will not serve.
  tikwm hands back both TikTok's own CDN and a copy proxied through
  tikwm: the first is faster when it works and the second is the entire
  reason the provider is useful when it does not.

### `ProviderFailed`

This provider could not do it. Try the next one.

`kind` is a hint for the sentence the user eventually sees if \*every\*
provider fails: "blocked" (the source turned the server away), "missing"
(the post looks gone or private), "too_big", or "error".

#### `ProviderFailed.__init__()`

- At `self.skip = tuple(skip)`:
  Providers that cannot help once this one has failed this way. A
  provider that learns the answer is "a video" knows the page scraper
  behind it would reply with a picture of that video, and a provider
  that succeeds ends the chain -- so it has to be able to say "not
  that one" rather than merely "not this provider".

### `NothingWorked`

Every provider for this platform failed. Carries the per-provider
reasons for the log and the best guess at which sentence to show.

#### `NothingWorked.kind()`

The most specific verdict any provider reached.

"missing" wins over "blocked" wins over the rest, because a provider
that got far enough to be told "this post does not exist" learned
something the ones that were bounced at the door did not.

## Keeping score

### `Health`

- `streak: int = 0` — consecutive failures
- `last_ok: float | None = None` — unix seconds

#### `Health.cooling_until()`

When this provider becomes a first-class citizen again.

Exponential from the third consecutive failure, capped at an hour, so
a service that went away this morning is asked about roughly hourly
rather than on every single request -- and a service having a bad
minute is back in the rotation two minutes later.

### `take_dirty()`

The rows that changed since the last flush, and clears the flag.

Health is kept in memory and written to Postgres on a timer rather than on
every attempt, for the same reason activity events are batched: this bot's
database is a shared one and a write per download is a write per download
forever. Losing the last minute of counters in a crash costs nothing --
they are advice about which provider to try first, not user data.

### `load()`

Seed from the database at startup, so a redeploy does not go back to
hammering a provider that has been dead for a week.

## Small shared helpers

_At `_META_TAG_MAX = 4096`:_

No real meta tag is anywhere near this long; one that is, is not read.

### `_meta_tags()`

Every `<meta ...>` tag of `html`, found in one pass.

This was the regex `<meta[^>]+>`, which is quadratic on a page made of
"&lt;meta" with no ">" after it: each opening scans on to the end of the page,
and an 8 MB page (what a route may read) held the event loop, and with it
every bot of the combined edition, for about an hour (review, 2026-10-08).
Here a tag runs to the first ">" after its opening and the next one is
looked for only after that, so every character is looked at once; and a
page with no more ">" ends the scan, so the work is linear in the page.

- `return` — no ">" left: no tag can end
- `pos = close + 1` — nothing starts inside a tag

### `_meta()`

Read one OpenGraph/Twitter-card meta tag's content.

Attribute order is not guaranteed -- Instagram's own markup puts
`content=` before `property=` -- so this isolates the whole tag first and
then pulls `content` out of it, rather than assuming an order.

_At `PAGE_LIMIT_BYTES = int(float(os.environ.get("DBOT_PAGE_LIMIT_MB", "8")) * 1024 * 1024)`:_

What a page or an API answer may weigh before it is refused. Every route
below reads a small document -- an embed page, a JSON answer -- and a
provider is somebody else's server: one that answers with a gigabyte would
otherwise be read whole into this process. Media goes through net.py's
streaming helpers, never through here.

### `_page()`

One small document, read under PAGE_LIMIT_BYTES (net.read_page), with
this module's way of failing: a ProviderFailed, kind "error", that the
chain moves past. The size is counted \*after\* decoding, and only gzip and
deflate are read at all -- see net.body_chunks for the 421 bytes that
decoded to 512 MB.

- `except net.Trickle:` — before BadEncoding, which it is one of

### `_post()`

The same for a POST (cobalt). An instance is somebody else's server
like any other provider, and `client().post()` would read a 200 MB answer
whole: it peaked at 400 MB in the review of 2026-10-08.

### `_ext_of()`

The extension to give a downloaded file, or `default`.

The name this reads is not the bot's and is not the user's either: it comes
out of whatever a resolver service or a remote URL said the file was
called. It ends up in `os.path.join(work_dir, f"{uuid}.{ext}")`, where a
"/" is a directory separator and ".." is a level up -- so anything that
is not plainly an extension is refused rather than trimmed. The length
cap was already doing most of this by accident; being explicit about it
is what makes it a rule instead of a coincidence.

- At `if not dot:`:
  rpartition puts the WHOLE string in the third slot when the separator is
  absent, so a dotless "video" used to come back as the extension "video".
  That reached \_kind_of, which then found "video" in neither the video nor
  the audio list and called it a photo -- and a clip sent as a photo is a
  Telegram error, not a slightly wrong label.

## Instagram

### `_jwt_payload()`

downloadgram wraps every media URL in a JWT, unverifiable from here, whose
payload carries the filename and the real source URL. Read, never
verified -- it is not this bot's token and the signature is between them
and their own CDN. Only the filename is used, to tell a video from a
cover image.

### `_ig_downloadgram()`

downloadgram.org's public endpoint.

The most useful property is not that it works -- it is that the media
comes back proxied through `cdn.downloadgram.org`, so the \*second\* request
(actually fetching the file) does not touch Instagram's CDN from this
container either. Both halves of the problem are somebody else's.

The response is an obfuscated JavaScript blob that assigns innerHTML; the
URLs are in there with their forward slashes hex-escaped. Parsed by
pulling the token links out rather than by pretending to understand the
JavaScript.

- At `if any(i.kind == "video" for i in items):`:
  A reel comes back as [cover.jpg, video.mp4]. Sending both would mean
  handing someone a still of the video they just asked for, next to the
  video. If there is any video in the set, the photos are thumbnails.

### `_ig_instafix()`

An InstaFix instance (`eeinstagram.com`).

InstaFix is the open-source thing behind the ddinstagram/kkinstagram
links people paste to make Instagram embeds work in Discord and Telegram;
it answers a crawler User-Agent with OpenGraph tags pointing at the media.
Both of its better-known domains stopped resolving, which is the whole
argument for this file.

Its `/videos/<code>/N` endpoint sometimes redirects to the post's \*cover
frame\* rather than the video -- a JPEG where an MP4 was promised. So what
it says is checked against what it serves before the item is accepted,
rather than sending the user a still and calling it a download.

- At `async with net.client().stream("GET", target, headers={"Range": "bytes=0-0"}) as probe:`:
  Ask for one byte. Enough to read the Content-Type without pulling the
  file twice, and cheaper than trusting the meta tag. Streamed, so that
  only the headers are read: a server that ignores Range answers with the
  whole video, and `.get()` would hold all of it in memory to look at one
  header of it.

## TikTok

### `_tt_tikwm()`

tikwm.com's open API. Resolves `vm.`/`vt.` short links itself, strips
the watermark, and -- the part that matters here -- also exposes the same
media through its own host at `/video/media/<play|hdplay>/<id>.mp4`, which
is used as the fallback when TikTok's CDN will not serve this container
directly. Photo slideshows come back in `images`.

### `_tt_tnktok()`

tnktok.com, the TikTok embed-fixer. Same crawler-UA trick as InstaFix,
and it serves the video off its own `offload.` host rather than TikTok's,
which is the useful half.

- At `raise ProviderFailed("tnktok exposed no video for that link", "missing")`:
  It used to fall back to og:image here and return a photo. That is
  the Pinterest bug: og:image on a \*video\* post is the cover frame, so
  a tnktok that is rate-limited or broken for this post would have
  ended the chain with a still, and ytdlp:tiktok -- which handles both
  videos and photo slideshows -- would never have been tried.

  Refusing is right in both readings of a missing og:video. If the
  post is a video, the image is the wrong file. If it is a slideshow,
  og:image is only the first of several and the answer is incomplete.
  tikwm, ahead of this in the chain, is the provider that can tell
  them apart, because it reads an explicit `images` field.

## Twitter / X

### `_tw_api()`

fxtwitter and vxtwitter share a response shape close enough to read
with one function. Both are the community embed-fixers for X, both expose
a JSON API their own front ends use, and neither needs a key.

## Pinterest

_At `_PIN_RESOURCE = "https://www.pinterest.com/resource/PinResource/get/"`:_

Pinterest's own read endpoint, which is what the site's front end calls and
what yt-dlp uses. It answers without a login, and it is the only source that
distinguishes the three kinds of pin from each other:

```text
a plain video pin   `videos.video_list`
a story / idea pin  `story_pin_data.pages[].blocks[].video.video_list`
a photo pin         neither, just `images`
```

It is asked first because the page scrape below cannot tell a video pin from
a photo one -- see the comment on \_pin_og.

### `_pin_best_video()`

The biggest .mp4 in one of Pinterest's video_list objects.

Only mp4: the same list carries HLS playlists (V_HLSV4, V_HLSV3_MOBILE),
and an .m3u8 is a manifest rather than a file -- handing one to Telegram
would upload a few hundred bytes of text named .mp4.

### `_pin_api()`

Ask Pinterest what the pin actually is.

- At `resolved_url = str(url)`:
  A pin.it shortlink carries no id, and a slug URL buries it after a
  double dash. One redirect-following GET settles both, and httpx has
  already followed it by the time this reads resp.url.
- At `top_list = (data.get("videos") or {}).get("video_list")`:
  Whether the pin is a video at all, whatever Pinterest serves it as. A
  pin can carry a video_list with no mp4 in it -- only an HLS manifest,
  V_HLSV3_MOBILE -- and \_pin_best_video rightly refuses a manifest. What
  it must not then do is describe the pin with `images`, which is its
  cover frame: that is how a friend's shared video came back as a still.
- At `for page in ((data.get("story_pin_data") or {}).get("pages") or []):`:
  A story pin is several pages, each with blocks. Every video block is a
  separate file, in order, the same way a carousel is.
- At `raise ProviderFailed(`:
  yt-dlp fetches HLS and muxes it, which is what it is for. The page
  scraper would only find the same cover frame in og:image and end
  the chain with it, so it is ruled out rather than tried.
- At `original = ((data.get("images") or {}).get("orig") or {}).get("url")`:
  A photo pin, or a kind this does not know. `orig` is the full-size
  image and is what \_pin_og reconstructs by hand.

### `_pin_og()`

The pin page's own OpenGraph tags -- the fallback for when the endpoint
above changes shape.

It cannot be the first route any more. Pinterest stopped emitting
`og:video`, so on a video pin this finds no video, falls through to
`og:image` -- which is present, because it is the video's cover frame --
and \*succeeds\* with a photo. A provider that succeeds ends the chain, so
yt-dlp was never reached and a video pin quietly downloaded a still.
Nothing errored; the user just got the wrong file.

- At `original = re.sub(r"/(?:\d{2,4}x\d{0,4}|originals)/", "/originals/", image, count=1)`:
  og:image points at a resized copy -- `.../736x/ab/cd/....jpg`. The same
  file is on the same host under `originals`, usually several times the
  resolution, and downloading someone a thumbnail when the full picture is
  one path segment away is the sort of thing nobody notices until they
  try to print it. Not every pin has an `originals` copy, hence the
  resized one as the fallback rather than the only option.

## cobalt, for the platforms with nothing else

cobalt (github.com/imputnet/cobalt) is an open-source downloader whose whole
product is an API: POST a link, get back a URL that serves the file. The
official instance wants a Turnstile token, but the project is built to be
self-hosted, and a number of people run public instances that answer
without one. Like the embed-fixers above, the fetching happens from \*their\*
address -- and the file comes back through their `/tunnel`, so the second
request never touches that site's CDN from this container either.

Written in the cloud session of 2026-09-25 for the unadvertised site, which had yt-dlp and nothing else: a redeploy can land on an address the site challenges (a "sign in" bot check) and a link that downloaded the day before is
then DL-BLOCKED. Ported here at 2.1.0 (that branch was never merged). With
cobalt ahead of it, yt-dlp becomes what it is everywhere else -- the last
resort, and the route that gets better if cookies or a proxy are set.

Checked 2026-10-07 from a residential address: of the three default
instances one answered, one answered with its login error and one
content.no_valid_content. Every one is somebody's hobby server, which is why
there are several, tried in order inside this one provider, with whichever
answered last going first next time -- so a dead instance costs one request
per restart rather than one per download. To use an instance of the operator's own
(the robust answer; a cobalt container is small), put it first in
DBOT_COBALT_INSTANCES and its key in DBOT_COBALT_API_KEY, which is sent to
that first instance only: a key is a secret, and the rest are strangers.

_At `COBALT_QUALITY = os.environ.get("DBOT_COBALT_QUALITY", "720")`:_

720p rather than "max": the bot sends through the cloud Bot API's 50 MB, and
a 1080p minute of video is most of that on its own.

_At `COBALT_INSTANCE_TIMEOUT_S = float(os.environ.get("DBOT_COBALT_INSTANCE_TIMEOUT", "15"))`:_

Per instance, for its answer (the address of the file, not the file). Production,
2026-11-07: the one instance that still fetched was given up on at 10 s.

_At `COBALT_TIMEOUT_S = float(os.environ.get("DBOT_COBALT_TIMEOUT", "240"))`:_

For the whole route, file included. Since 2.1.5 the file is fetched here, inside the route
(see \_cobalt_take), and a public instance's tunnel is slow: about 100 KB/s when measured on
2026-11-10. A file over the free ceiling gets YTDLP_SECONDS_PER_MB more for each megabyte.

_At `COBALT_STEP_DOWN = (480, 360)`:_

The heights asked for next when the file at COBALT_QUALITY is over the size ceiling: cobalt
has no "the best that fits", so the bot walks down itself.

_At `_COBALT_MISSING = ("content.video.unavailable", "content.post.unavailable",`:_

cobalt's error codes, sorted into the verdicts the chain understands. The
codes are namespaced ("error.api.content.video.unavailable",
"error.api.<site>.login"), so these match on parts rather than whole codes
-- new codes under a known namespace land in the right bucket unaided.

### `_cobalt_items()`

- At `kind = "photo" if entry.get("type") == "photo" else "video"`:
  "gif" is a short muted mp4 in cobalt's vocabulary, which is a
  video to Telegram.
- At `tunnels = data.get("tunnel") or []`:
  Newer instances can hand back separate streams for the client to
  merge. One stream is already a whole file; several would need
  ffmpeg here, which is what yt-dlp is further down the chain for.

_At `COBALT_HEIGHTS = (144, 240, 360, 480, 720, 1080, 1440, 2160)`:_

The heights cobalt takes for "videoQuality".

### `_cobalt_quality()`

COBALT_QUALITY, unless a height was asked for (\_HEIGHT): then that height, or the
nearest one under it that cobalt knows. An exact height (the quality picker) asked of
cobalt gets that height when the video has it; cobalt has no way to refuse the others.

## Files fetched inside a route

Up to 2.1.4 a relay's answer was an address, and the file behind it was fetched after the
chain had ended (download(), below). Production, 2026-11-07, and a check from a datacenter
address on 2026-11-10: an instance the site has blocked still answers with a tunnel address,
and the tunnel then sends nothing. The chain had already stopped at that instance, so the
person got an empty file's error while the next instance, or the next route, would have
worked. The relays that front that site therefore fetch the file themselves and look at it,
and an instance whose file is no good is one more instance that failed.

_At `MIN_MEDIA_BYTES = 1024`:_

Under this a "video" is an error page or nothing at all.

### `_looks_like_video()`

Does the file start the way an MP4 (the "ftyp" box) or a WebM/Matroska file does.

### `_limit_bytes()`

The size ceiling of the download being resolved (see \_LIMIT_MB).

### `_take()`

`url` into a new file in the work folder; its path. On any failure, a cancellation
included (the chain's time limit, the bot stopping), nothing is left on disk.

### `_instances_verdict()`

What a route says when every one of its instances failed.

### `_cobalt_take()`

Fetch every item of a cobalt answer to disk and check it; the items then carry paths.
net.TooLarge goes up as it is (the caller asks for a smaller version).

### `_cobalt_qualities()`

The qualities to ask one instance for, in turn: the first one, then -- unless one height
and no other was asked for -- the lower ones of COBALT_STEP_DOWN.

### `_cobalt_fetch()`

One instance, as far as files on disk (as far as its answer, for lookup()).

- At `resolved = await asyncio.wait_for(_cobalt_one(host, url, platform, key, quality),`:
  A hard deadline, not httpx's: its timeout is per read, and
  an instance trickling bytes would never trip it.
- `raise ProviderFailed(f"{netloc}: nothing asked for", "error")` — qualities is never empty

### `_cobalt_provider()`

#### `_cobalt_provider.provider()`

- At `budget = _timeout_for("cobalt:") * INSTANCE_SHARE`:
  One instance may not use up the whole route: the next one is still asked.
- At `raise`:
  The clip itself is the problem; another instance will
  say the same thing about it. "missing" is not trusted
  the same way: an instance the site has blocked can
  report a perfectly good video as unavailable.

## Invidious: a second family of relays for the unadvertised site

Written 2026-11-10 for 2.1.5. A check from a datacenter address that day: the site refused every one of yt-dlp's 14 player clients (a bot check, or HTTP 403 on the file), with and
without a token provider, and of 24 public cobalt instances one still fetched -- the rest
want a browser's Turnstile token, or are blocked themselves. One hobby server is not a route
to rely on, so this is another kind of server doing the same job: Invidious, a front end
people run for themselves, whose API lists a video's versions and, asked with local=true,
hands out addresses on the instance itself, which passes the bytes on from the site.

It lists picture and sound as separate files (the joined ones the site used to offer are gone), so this route fetches two and joins them with ffmpeg, without re-encoding. It reads
the sizes first, so, unlike cobalt, it can choose the best version that fits.

Instances come and go, so there are two sources: DBOT_INVIDIOUS_INSTANCES (the operator's own first), and
the project's own list of public instances (DBOT_INVIDIOUS_DIRECTORY), read a few times a day
and used only for the instances that say their API is open.

_At `INVIDIOUS_DIRECTORY = os.environ.get("DBOT_INVIDIOUS_DIRECTORY", "https://api.invidious. …`:_

"" or "0" turns the directory off: then only the instances above are asked.

_At `INSTANCE_SHARE = 0.75`:_

The share of a route's time one instance may use (cobalt's instances too).

_At `INVIDIOUS_SIZE_MARGIN = 0.96`:_

The same margin video.py keeps under the ceiling (SIZE_MARGIN): the joined file is a little
longer than its two parts.

### `_invidious_from_directory()`

The instances of the directory's answer that are reached over https and say their API is
open, in the directory's order (it sorts by health), at most INVIDIOUS_DIRECTORY_MAX. Pure.

### `_invidious_hosts()`

The instances to ask, in order: the one that answered last, the configured ones, then
the directory's. The directory is read again every few hours; when it cannot be read, the
list from last time stays.

- `except Exception as exc:` — the directory is a convenience: the configured instances remain

### `_invidious_height()`

A version's picture height: from its size ("720x1280" is 1280 high, the way yt-dlp
counts it and MAX_HEIGHT caps it), else from its label ("720p").

### `_invidious_track()`

How much a sound track is the video's own: 2 the original, 1 the only one (nothing
said), 0 a dubbed or described one. The site says which in the address ("xtags").

### `_invidious_pick()`

(picture, sound) for the best version that fits `budget_bytes`: H.264 with AAC, which
every Telegram app plays and which go into one MP4 as they are; the tallest picture up to
`max_height` (or `height`; with `exact`, that height and no other). Pure. ProviderFailed
when there is none: "too_big" when the size is why, "error" otherwise.

### `_invidious_local()`

The address of a file on the instance itself. With local=true the instance writes these
itself; whatever it wrote, the request goes to the instance asked and to nowhere it names:
its /videoplayback, with the site's parameters, and the site's server as the `host` parameter when the instance left the address pointing at the site.

### `_mux()`

One MP4 from a picture file and a sound file, nothing re-encoded, the index at the
front (Telegram plays it while it loads). The inputs are files a stranger's server sent:
ffmpeg is told they are MP4 and may open files and nothing else, so one that is really a
playlist cannot make it fetch an address or read another file.

### `_invidious_fetch()`

Both files, checked, joined; the path of the MP4. Nothing else is left on disk.

- At `for task in tasks:`:
  gather leaves the other fetch running when one fails. It is stopped here, and
  \_take removes what it had written; a fetch that had already finished left a
  whole file, which goes too.

### `_invidious_one()`

- `import video` — for its ceilings; deferred as in \_ytdlp_provider

### `_invidious_provider()`

#### `_invidious_provider.provider()`

- `if exc.kind == "too_big":` — the clip's own size: as for cobalt

## yt-dlp, the universal last resort

### `_ytdlp_provider()`

#### `_ytdlp_provider.provider()`

- `import video` — deferred with yt-dlp itself; see video.py's docstring
- At `stop = threading.Event()`:
  A thread cannot be cancelled, only asked. This provider is cancelled
  by the chain's time limit and by the bot stopping, and the download
  in its thread would carry on to the end -- a file nobody collects,
  left on disk and paid for in bandwidth -- unless it is told.

## The chains

Order is the order they are tried on a healthy day: verified-working first,
then the ones that are known to be more fragile, then yt-dlp -- which is
last everywhere rather than first because it is the one route that makes the
request from this container's own address, and that address is the problem.
It is still in every chain, because it is also the only one that gets better
the moment DBOT\_\*\_COOKIES_FILE or DBOT\_\*\_PROXY is set.

_At `"pinterest": [`:_

api first: it is the only one of the three that can tell a video pin
from a photo pin, and getting that wrong is silent rather than loud.

_At `"reddit": [`:_

Reddit has one entry on purpose. bot.py reads the post itself -- it
needs the title, author and score for the card anyway -- and sends a
plain image post straight from `url_overridden_by_dest`. What is left
for the chain is hosted video, which is DASH with a separate audio
track, and yt-dlp is the only thing here that muxes it.

_At `platforms.UNADVERTISED: [`:_

yt-dlp first (with deno it can answer that site's challenges), the relays when it
fails: yt-dlp asks from this container's own address, which is the address the site challenges, and a relay asks from its own. See the cobalt and Invidious
sections. On a host the site refuses, yt-dlp fails in a second or two and is then
rested by its health score, so the order costs little there.

### `_ordered()`

Registry order, with the currently-unwell moved to the back.

Not filtered -- moved. A provider in cooldown is still tried if
everything ahead of it failed, so a chain whose every member is having a
bad day still does the most useful thing available instead of refusing on
the strength of yesterday's scores.

_At `_LIMIT_MB: contextvars.ContextVar = contextvars.ContextVar("download_limit_mb", default= …`:_

The size ceiling for the download being resolved right now: None is the
free one (video.MAX_DOWNLOAD_MB); a large download (large_files.py) asks for
more. A context variable rather than an argument to every provider, because
only yt-dlp -- which downloads while it resolves -- has a use for it.

_At `_HEIGHT: contextvars.ContextVar = contextvars.ContextVar("download_height", default=None)`:_

The picture height asked for, as (height, exact), or None for the best that fits: the
quality picker asks for one height exactly, a group's setting caps it. Only yt-dlp and
cobalt can choose, so, like \_LIMIT_MB, a context variable rather than an argument.

_At `_LOOKUP: contextvars.ContextVar = contextvars.ContextVar("lookup_only", default=False)`:_

True while lookup() asks: the routes that fetch the file inside the route (the relays for the unadvertised site) then stop at the instance's answer and fetch nothing.

### `_timeout_for()`

How long the route called `name` gets before the chain moves on: PROVIDER_TIMEOUT_S,
except for the routes that fetch the file while they resolve it (yt-dlp, and the relays for the unadvertised site unless lookup() is asking), which get their own time and more for a large file.

### `resolve()`

Walk the chain until something produces media, with `max_mb` as the
ceiling for anything downloaded on the way, and `height` the picture height
(see \_HEIGHT).

_At `MAX_ITEMS_PER_POST = max(1, int(os.environ.get("DBOT_MAX_ITEMS_PER_POST", "40")))`:_

Most files one post may deliver. The bot downloads every item of a delivery to
disk before it sends the first, each up to the download ceiling, and a
provider is somebody else's server: a cobalt picker or a resolver that lists
a thousand items would fill the container's disk (review, 2026-10-08). A
TikTok slideshow carries up to 35 photos, so the default leaves room.

### `_cut_items()`

Keep the first MAX_ITEMS_PER_POST items of `resolved`. A file yt-dlp
already wrote for an item that is cut is deleted: nothing else knows of it.

### `_resolve()`

Walk the chain until something produces media.

- `except Exception as exc:` — a provider's own bug must not end the chain

### `lookup()`

Is the post still public? "present", "missing" or "unknown" -- asked of the
routes without taking any media (sent_files.py: a stored file id is trusted
only while the post was seen public in the last few hours).

The platform's routes in their usual order, each under its usual limit, but
yt-dlp is never asked: it downloads inside resolve (video.py), and the whole
point here is to not download. And nothing is recorded -- no health score, no
cooldown -- because a deleted post is the post's failure, not the route's, and
a question is not traffic (the same rule as probe_all).

"present" as soon as one route returns an item. "missing" only if every route
asked said the post is missing: one route's word is not enough (tikwm reports
a rate limit as missing). Anything else -- a timeout, a network error, a mix of
answers, or no route left once yt-dlp is left out (Reddit has only yt-dlp) --
is "unknown", and the caller then does not trust the stored file ids.

- `only_asking = _LOOKUP.set(True)` — the relays that fetch inside the route: the answer, no file
- `except Exception as exc:` — a timeout, a network error, a route's own bug: no answer
- `said_missing += 1` — a route that returns nothing says what \_resolve counts it as

## Asking every route, on purpose

resolve() stops at the first route that works, which is what a user wants
and exactly the wrong thing for finding out what is going on. This asks all
of them and reports each answer.

It deliberately does not touch the health scores. A probe is a question, not
traffic, and letting an operator's curiosity reorder the chain -- or worse,
rest a route that a real user could have used -- would make the scores mean
something other than "what happens to users".

_At `SAMPLES = {`:_

Public posts, each one confirmed to resolve when this was written. If a row
comes back "missing" for EVERY route at once, suspect the sample rather than
the internet: a real outage looks like one route failing, not all of them
agreeing.

_At `PROBE_FETCH_TIMEOUT_S = 20.0`:_

How long the whole media fetch of a probe row may take, however many
addresses it tries. The route itself has its own timeout; this one is for
what the route \*returned\*: a CDN address that accepts the connection and
then sends a byte a minute never trips httpx's per-read timeout, and a
probe is reachable over the bus, where ManagerBot gives up at 90 s.

### `probe_route()`

One route, one link. Never raises.

The relays that fetch the file inside the route are asked the way lookup() asks them:
for their answer. A check is given seconds, not the minutes a whole file may take, and
`fetch` then proves the address serves with its first bytes, as for every other route.

- At `row["fetched"] = f"fetch failed: no media in {PROBE_FETCH_TIMEOUT_S:.0f}s"`:
  "fetch failed" exactly: routewatch.check_platform reads the
  start of this text to tell a route whose media does not serve.

### `_probe_fetch()`

Prove the media URL actually serves, without pulling the whole file.

A GET abandoned after the first chunk, never a HEAD -- see net.py for the
TikTok CDN's 503-on-HEAD. yt-dlp's items are already on disk, so they are
measured and deleted instead.

#### `_probe_fetch.CapReached`

Not an error here: the cap is what this fetch is for.

- At `pieces = net.body_chunks(resp, PROBE_FETCH_BYTES, CapReached)`:
  Through body_chunks like every other body, so that a media
  address which answers with a compressed bomb is stopped at the
  cap instead of decoded whole -- this is the one place that used
  to read a CDN's body without counting what it became.
- `got = max(got, PROBE_FETCH_BYTES)` — more than the cap arrived: success
- At `last = "it sent nothing"`:
  A relay the site has blocked answers with an address that sends nothing.

### `probe_all()`

Every route for every platform in `urls` (default: SAMPLES).

Platforms run concurrently and the routes within one run in order, so the
whole thing costs about as long as the slowest single chain rather than
the sum of all of them. That matters because this is reachable over the
family bus, where ManagerBot gives a command 90 seconds before calling it
a timeout -- and a probe that cannot finish inside that window is a probe
nobody can run from their phone.

_At `_URL_RE = re.compile(r"https?://\S+")`:_

yt-dlp's errors are a paragraph each and every one of them quotes its own
issue tracker. Telegram expands the first URL in a message into a full-width
preview card, which is how a failed download once arrived as a GitHub
repository with a logo (v1.1.3). A probe report can carry a dozen of those,
so the URLs come out and the rest is trimmed to a line.

_At `_NOISE_MARKERS = ((re.compile("please report this issue on", re.IGNORECASE), True), …`:_

The two places yt-dlp's text turns to its own advice. The text is cut at the
first of them by a plain search for the words: the regex this replaced
(`\s*;?\s*please report this issue on.*|...`) backtracked cubically on a run
of whitespace -- 1000 spaces took a second, 10,000 about a quarter of an
hour -- and the text can be a hostile cobalt instance's error code, on its
way through a probe into the daily check's report (review, 2026-10-08).

- `_NOISE_MARKERS = ((re.compile("please report this issue on", re.IGNORECASE), True),` — True: and the ";" before it

_At `_TIDY_READS = 4000`:_

More than this is never shown, and none of it needs searching.

### `tidy_error()`

- `if semicolon:` — "...; please report this issue on ..."

### `format_probe()`

The report, as plain text -- it goes to a Telegram message and to a
terminal, and both want the same thing.

## Getting the bytes

### `download()`

Put one MediaItem on disk and return the path, no bigger than `max_mb`
(the free ceiling if None).

Already-local items (yt-dlp's) are handed straight back. Everything else
is streamed, with a `Referer` some CDNs want and a size ceiling that stops
a mis-detected link from filling the container's disk. `alt_urls` are
other routes to the same file and are tried in order.

- At `if os.path.exists(path):`:
  Over the size ceiling. Another route to the same file will be
  the same size, so there is nothing to gain by trying it. Only
  this: any other FetchError (an encoding this bot does not read,
  a body sent in specks) is one route failing, and the next may
  serve the same file in a way that reads.

## The quality picker's list

For the sites in PICK_PLATFORMS, a link pasted in a private chat first gets a card (bot.py) when one of
the routes can say which versions there are and how big each is: yt-dlp's reading, else an Invidious
instance's (PICK_INVIDIOUS_HOSTS of them). When none can in PICK_LIST_TIMEOUT_S (10 s,
`DBOT_PICK_LIST_TIMEOUT`, or the older `DBOT_QUALITY_LOOKUP_TIMEOUT`), the link is downloaded the
default way: the card is never the reason a download fails. A lister whose download route is resting
after failures (`Health.cooling_until`) is not asked: from a server address yt-dlp is refused, and
waiting on it for every link would only delay the download.

### `Choices`

What a card offers: the title, the length, a picture, `[(quality, bytes, free)]` and the sound's size.
Worth a card when there are at least two things to choose between.

### `_invidious_quality()`

A version's quality as people name it (video.quality_of): its label ("480p"), else the shorter side
("720x1280" is 720p). An exact pick (`#q`) is matched by this, a group's cap by the height.

### `_invidious_sound_format()`

The sound to give somebody who picked just the sound: the video's own AAC track (see `_invidious_pick`).

### `choices()`

The versions a card can offer for `url`, or None: the site has no picker, nothing listed them in time,
or there is nothing to choose between. Never raises.

### `_invidious_sound()`

Just the sound, for a pick of the sound alone: the video's own AAC track, checked like a picture is
(its stated size, and that what arrived is a media file).

_At `COBALT_AUDIO = "audio"`:_

What a cobalt instance is asked for when only the sound was picked: its audio mode, as MP3, with no
stepping down (`_cobalt_qualities`). `_SOUND` says so to every route of the chain, as `_HEIGHT` says
which quality.
