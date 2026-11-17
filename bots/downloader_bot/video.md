# `video.py`

[↑ downloader_bot](README.md) · [code](video.py)

The yt-dlp provider: download a video with yt-dlp so the bot can re-send it.

Since v1.2.1 this is one link in resolvers.py's chain rather than the whole
download path, and it is deliberately \*last\* in every chain. yt-dlp makes the
request from this container's own address, and a datacenter address is the thing Instagram and the unadvertised site object to -- so it is the route most likely to
come back with a login page, and the only route that gets better the moment
DBOT\_&lt;SITE>\_COOKIES_FILE or DBOT\_&lt;SITE>\_PROXY is set. It is also the only
route that works for the unadvertised site at all, and the only one that muxes Reddit's
separate DASH video and audio tracks.

yt-dlp is imported lazily, inside download_video(). Importing it costs
roughly 30-40 MB of resident memory and a second of startup for its
several-hundred extractor modules, and a bot that goes a whole day without
anyone pasting a link should not be paying that the whole time. The first
download pays it once, and it stays loaded from then on.

_At `MAX_DOWNLOAD_MB = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48"))`:_

Telegram itself refuses uploads over 50 MB without a local Bot API server,
so anything larger was always going to be downloaded and then thrown away.
Telling yt-dlp up front turns "fill the container's disk, then fail" into
"don't start". The default leaves headroom under that 50 MB ceiling.

_At `YT_PLAYER_CLIENTS = [`:_

Empty leaves the choice to yt-dlp, which keeps up with that site better than a fixed list:
by late 2026 "tv" asked for a reload and "android_vr" links answered 403.

_At `COOKIEFILE = os.environ.get("DBOT_COOKIES_FILE") or None`:_

A cookies.txt exported from a logged-in browser is the reliable answer, and
it is deliberately not the default: it ties this bot to a real account, the
cookies expire in days, and the account can be banned for it. One should come from a throwaway account -- never a personal one.

DBOT_COOKIES_FILE applies to every site. The per-site ones win where they
are set, because the two problems want different accounts: one account for the unadvertised site and an Instagram one for Instagram, and handing either site the
other's cookie jar is worse than handing it none.

_At `_COOKIE_TEXT_VARS = {platforms.UNADVERTISED: "DBOT_YT_COOKIES", "instagram": "DBOT_IG_COOKIES",`:_

The same cookies.txt, pasted into a variable instead of left in a file. A
host like Railway has environment variables and no convenient place to put a
file, so a path-only setting meant cookies were, in practice, not an option
there -- and cookies are the one thing that reliably gets the unadvertised site to stop
asking a datacenter address to sign in. The text is written to a file only
this process can read, once, the first time a download needs it; the
\*\_COOKIES_FILE settings win where both are set. (From the unmerged cloud
session of 2026-09-25, rewritten here.)

_At `_cookie_text_refused: dict = {}`:_

Sites whose variable was read, found not to be a cookies.txt, and refused:
site -> the text that was refused. Remembered so that the answer does not
cost a re-check and a log line on every download, and keyed on the text so
that a changed value is looked at again.

### `_cookie_text_problem()`

Why `text` is not a cookies.txt, or None if it is one.

yt-dlp reads every line that is not blank or a comment as a cookie of
exactly seven tab-separated fields -- and when one is not, its error
quotes the line, and the line is a session secret, and the error goes
into the log (review, 2026-10-08). Pasting is how it happens: a browser
or a form turns the tabs into spaces. So the check is made here first,
and what it says contains no part of the text: numbers only.

"#HttpOnly\_" lines are cookies (the prefix is how the format marks
them), not comments.

Seven fields is not enough: confirmed with yt-dlp's own loader (the
2026.08.19 release), a domain whose flag disagrees with it, or an expiry
that is not plain digits (-1, 1.89e9), makes the loader print the whole
line, secret and all, in a warning or an exception. So each field is
checked against what that loader accepts silently. A few of these
(path, secure, name) would not print anything; they are refused anyway,
because a line like that is not a cookie any site would take.

- At `text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")`:
  Read the way yt-dlp reads the file: universal newlines, so a lone "\r"
  ends a line too; and a byte-order mark at the start is not part of it.
- At `if not domain:`:
  Fixed words only, never the field itself: it is part of a secret's line.

_At `_COOKIE_HEADER_RE = re.compile(r"#( Netscape)? HTTP Cookie File")`:_

The header http.cookiejar looks for on a cookies.txt's first line.

### `_cookiefile_from_text()`

- At `text = text.removeprefix("\ufeff")`:
  A byte-order mark (a Windows editor's, a form's) in front of the first
  line makes yt-dlp read that line as malformed: dropped, with a warning.
- At `if "\\n" in text and "\n" not in text:`:
  Some hosts flatten a pasted multi-line value into one line with literal
  "\n" and "\t" in it; yt-dlp would read that as one malformed cookie.
- At `text = text.lstrip("\r\n \t")`:
  yt-dlp refuses the WHOLE file, quietly as far as the operator can tell, unless
  its very first line is the format's header (checked with its own loader,
  2026.08.19: no header, or a blank line before it, and no cookie is read).
  A paste often starts at the first cookie, or with an empty line.
- `fd, path = tempfile.mkstemp(prefix=f"cookies-{site}-", suffix=".txt")` — mode 0600

### `cookiefile_for()`

The cookie jar for this site: its own file, its own pasted text, then
the every-site file, then the every-site text. None if there is none.

_At `PROXY = os.environ.get("DBOT_PROXY") or None`:_

Where the request goes out from. The reason cookies are not always enough:
these sites rate-limit by IP as well as by session, every cloud host's
address is a known datacenter range, and a datacenter IP is most of why the
server is challenged when a phone on the same link is not. A residential or
mobile proxy is the robust answer and the only one that does not expire.

_At `BOT_CHECK_MARKERS = ("sign in to confirm", "confirm you're not a bot",`:_

What the "sign in" bot-check wall looks like coming back
out of yt-dlp. Matched so the user gets a sentence instead of a stack of
yt-dlp's documentation links.

_At `LOGIN_WALL_MARKERS = (`:_

And what a login wall looks like. Instagram stopped serving reels to
anonymous callers from datacenter addresses, so this is now the \*ordinary\*
outcome there rather than an edge case -- and until it was matched here the
user got yt-dlp's raw text, complete with a link to its FAQ that Telegram
then expanded into a full-width GitHub preview card. That reads as the bot
having crashed and helpfully shown its documentation.

### `_site_of()`

Which per-site cookie jar and proxy apply. The split matters: each site's problem wants an account on that site, and handing either site the other's cookies is worse
than handing it none.

### `BlockedBySource`

The site refused the server, not the link.

`kind` says which way it refused, because the two want different
sentences: "bot_check" is a challenge that may pass on a retry, and
"login_required" will not pass on any retry -- it needs a credential this
bot does not have, and saying "try again later" about it is a lie.

### `TooLarge`

The clip is over the size/duration ceiling. Message is user-facing.

### `_reject_long()`

yt-dlp's match_filter hook: runs on the \*metadata\*, before a single
byte of media is fetched.

### `_best()`

(rank, format id, bytes) of the best video (with audio) that fits the budget, or None.
`height` caps the picture height (`exact`: that height and no other).

## The quality picker (private chats)

A link whose versions can be listed gets a card first (bot.py's `_offer_choices`): a button per
quality and its size. A choice is a quality as people name it -- the site's own label ("480p": it
calls its 608x1080 versions that), else the shorter side of the picture, so a vertical 1080x1920 clip
is "1080p" -- or AUDIO, the sound alone. The same name means the same thing everywhere: on the
button, in the link's `#q720` mark, in the version fetched, and in the keys of the kept files. A
group's cap (`#upto480`) is a different thing and still compares the picture's height
(`_best`). Measured on a real short (2026-11-15): streamed copies with no stated size sit beside
the plain files, so the plain file is preferred and its real size goes on the button; the sound has
dubbed tracks and a compressed ("DRC") copy, so the video's own track is taken.

_At `PICK_MIN_QUALITY = 240`:_

Qualities under this are not offered; at most PICK_MAX_QUALITIES free ones (the tallest) go on a
card, and the paid ones (over the free limit, where large files are set up) never push them off.

### `quality_of()`

A version's quality as people name it: the site's label, else the shorter side.

### `_sound_rank()`

The video's own sound first (not a dubbed track, nor the dynamic-range-compressed copy), then as
`_audio_rank`.

### `_picture_of()`

The version to fetch for a quality: H.264 first (every Telegram app plays it), then one whose size
is stated (a plain file, not a stream: the size on the button is then the real one).

### `_choice_format()`

(yt-dlp format id, size in bytes or None) for one choice, or None when the video has no such version.

### `choices_of()`

What the card can offer for a video yt-dlp has read: `{"qualities": [(quality, bytes, free)],
"audio": bytes or None}`. Only versions whose size is stated: a button has to say what it costs, and
a version of unknown size could not be promised. Free ones fit the free limit; paid ones fit
`paid_bytes` (0 where large files are not set up).

### `pick_choice()`

The yt-dlp format id for what was picked; TooLarge when that version is not there or does not fit.
`_download` uses it for every exact pick (`#q`), so what is fetched is what the button said.

### `_format_selector()`

- At `f"bv*[filesize<{mb}M]+ba/"`:
  A video stream that fits, plus the best audio, merged.
- At `f"b[filesize<{mb}M]/"`:
  Or a single already-muxed stream that fits.
- At `"bv*+ba/b"`:
  Nothing advertised a size it could keep to: take the best merge, or
  the best muxed stream, and let max_filesize stop it if it runs long.

### `_remove_leftovers()`

Everything yt-dlp wrote for this download except `keep`.

A download that does not finish leaves what it had: `<id>.mp4.part`, the
video and audio halves of a merge (`<id>.f137.mp4`, `<id>.f140.m4a`), a
`.ytdl` state file. Nothing else knows those names, so nothing else
removed them -- and a container that runs for months on an ephemeral disk
is filled by the ones a failed or timed-out download left behind.

## Replacing yt-dlp while the bot runs

yt-dlp is the route that rots: the sites change under it every few weeks and
the image has whatever version its last build installed (routewatch.py says
when that is behind, and can fetch the newer one). Putting a newer copy in
place means swapping modules in a process that may be using the old ones --
harmless between downloads, a mixed-version crash in the middle of one. So a
download holds a \*slot\* for as long as it runs, and a swap waits until none
is held and keeps new ones out while it happens.

### `use_ytdlp_from()`

Make `path` (a folder holding a newer yt_dlp package) the one every
later download imports. Blocking. Returns False, and changes nothing, if
downloads did not finish within SWAP_WAIT_S -- the caller tries again the
next day.

- At `for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:`:
  What the old copy had imported must go, or the next `import
  yt_dlp` is satisfied from sys.modules and nothing has changed.

### `ytdlp_version()`

The version a download would use right now, without importing it.

_At `READ_KEEP_SECONDS = 600`:_

What read_info read, by link, so that the download after the person picks a quality
starts from it rather than reading the page a second time (a few seconds on a long video).
Format links on such a page are good for hours; this keeps them for minutes.

### `_recall_read()`

A copy of what was read: yt-dlp writes into the info it downloads from, and two
people downloading the same video at once would otherwise share one, across threads.

### `_site_options()`

The yt-dlp options that depend on the site: player clients, cookies, proxy.

### `read_info()`

What yt-dlp reads about one video without fetching it (title, length, versions), for the quality
picker; kept for READ_KEEP_SECONDS for the download that follows a tap. BlockedBySource when the
site turned the request away; TooLarge when the link is not one video. Reads the page only, no
media. Blocking -- call it through asyncio.to_thread.

- `import yt_dlp` — deferred: see the module docstring

### `download_video()`

Downloads the video at `url` into `out_dir`, returns the local file path.
`max_mb` is the ceiling for this one download -- MAX_DOWNLOAD_MB unless
somebody has switched on large files (large_files.py).

`cancel` is a threading.Event: set it and the download stops at its next
chunk and removes what it had written. A thread cannot be cancelled, only
asked, and a caller that stops waiting (resolvers.py gives a route a time
limit) would otherwise leave the download running to its end, holding
bandwidth and disk for a file nobody will collect.

`height` caps the picture height (a group's setting); with `exact`, it is the one height
the person picked from the quality picker, and a version at another height is never sent
in its place.

Whatever ends the download -- it finishing, failing or being stopped --
leaves only the file it returns.

Blocking -- call it through asyncio.to_thread.

- `import yt_dlp` — deferred: see the module docstring

#### `download_video.match_filter()`

- At `stop_if_asked()`:
  Asked once the page has been read and before any media is fetched: a
  request that is already abandoned stops there.

- `stop_if_asked()` — finished, but too late for anyone to use it

### `_download()`

- At `"cachedir": False,`:
  Nothing here reads a cache, and the default location is a writeable
  directory this process would otherwise keep growing.
- At `info = _recall_read(url)`:
  Read already when the person was offered the qualities: start from that.
- At `info = ydl.extract_info(url, download=True)`:
  Read afresh (2.1.7): fetching from the reading above -- or from the picker's,
  kept by \_remember_read -- was refused with HTTP 403 for most single-file
  versions and some pairs, measured 2026-11-15 with yt-dlp 2026.10.26: the file
  addresses don't survive being processed a second time. A fresh read costs ~3 s
  and fetched 6 of 6. The chosen format id is the same on both readings.
- At `if not os.path.exists(path):`:
  merge_output_format can change the extension after download

## The shape of a video, for the send

Telegram draws a video from the width, height and duration the sender gives
it. Left out, the clients fall back to a 1:1 square and crop into it, so a
9:16 reel arrived as a square (reported 2026-10-12).
bot.py's \_send_media measures each video it sends as a video, with the ffprobe
that comes with ffmpeg, and passes the three numbers. It is a nicety and never
a gate: whatever goes wrong here, the video is sent exactly as it was before.

_At `PROBE_BUDGET_SECONDS = 10`:_

And one delivery (an album, a large download's items) gets this much probing in
all, however many videos it holds; past it the rest are sent without measuring.

- `MAX_SECONDS = 2 ** 31 - 1` — a file can claim any length; Telegram's field is a 32-bit integer

### `VideoShape`

What the player will show: the DISPLAYED size (the stored size, widened
by a non-square pixel and turned a quarter when the file says so) and the
length in whole seconds, or None when ffprobe did not say.

### `ProbeRun`

One delivery's probing. `timed_out` is set by probe() when ffprobe was
killed for taking too long, so that the rest of an album is sent without
asking again: a hostile album must not cost PROBE_SECONDS for each file. And
`spent` adds up the seconds every probe of the delivery has taken, which may
not pass PROBE_BUDGET_SECONDS in all: slow-but-not-stalled files cannot add up
to a long wait either.

#### `ProbeRun.exhausted()`

No more probing for this delivery.

#### `ProbeRun.allowance()`

How long the next probe may take: its own limit, or what is left of
the delivery's budget if that is less.

### `_quarter_turn()`

Whether the player turns the picture 90 or 270 degrees, which swaps its
sides. The display matrix (side data) is asked first and the older `rotate`
tag only when there is none, so a file that carries both is not turned
twice. ffprobe rounds (89.99999 happens), hence round() and not int().

### `_display_width()`

`width` widened by a non-square pixel ("N:D"), as the player shows it.

### `shape_from_ffprobe()`

The shape from ffprobe's JSON, or None for anything that is not a
plausible picture. Pure.

### `_ffprobe_argv()`

The command that starts ffprobe, or None. There is no configured ffmpeg
location in this bot (yt-dlp finds ffmpeg on PATH, and the image installs
both programs from Debian's one `ffmpeg` package), so ffprobe is found the
same way.

### `_read_capped()`

Everything on `stream`, or None once it is more than `limit` bytes.

### `probe()`

The width, height and duration of the video at `path`, or None.

One ffprobe, started without a shell, with nothing to read from and nothing
to say; killed and reaped after PROBE_SECONDS. `file:` and a protocol
whitelist because ffprobe goes by what a file CONTAINS, not what it is
called: a downloaded "video" that is really a playlist must not make this
server fetch anything. Never raises, and logs numbers only: no path, no link.

- At `try:`:
  Out of time, over the cap, or cancelled: it is killed and its exit
  collected, so that no child is left running or unreaped.
