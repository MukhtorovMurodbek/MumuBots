# `platforms.py`

[↑ downloader_bot](README.md) · [code](platforms.py)

Which platform a pasted link belongs to, and the two page-scrapes that are
about \*text\* rather than media.

Media resolution moved to resolvers.py at v1.2.1. What is left here is
recognition -- the regexes bot.py's MessageHandler filters on -- plus the
Reddit and Twitter/X reads that exist to render a card for a post with no
media in it, which is a different job from downloading one.

Instagram and TikTok used to share a single regex, because they shared a
single code path: yt-dlp. They no longer share a code path -- they have
separate provider chains with nothing in common -- so they are separate
platforms here too.

- `from net import FetchError, client, close_client, fetch_bytes` — re-exported

### `_spelled()`

One video site is handled but never advertised, and its name is written nowhere in this
repository, in code or in notes. It is kept here once, base64-encoded, and every other module reads
it from the constants below. Base64 is no secret -- anybody can decode it; it only keeps the name out
of searches and out of text that is skimmed. At runtime the value is the plain name, the same string
the platform was stored under before 2.2.0, so the rows already in the database (sent files, usage,
the groups' platform lists) still match and nothing had to be migrated. Names that belong to the
download library, or to settings already set on the services, keep their own spelling.

_At `UNADVERTISED = _spelled("eW91dHViZQ==")`:_

`UNADVERTISED` is the platform key: what detect_platform returns and what is stored.
`UNADVERTISED_SHORT_HOST` is the share button's short host, `UNADVERTISED_THUMBNAIL_HOST` where a
video's still picture is fetched for the quality picker's card, and `UNADVERTISED_MEDIA_HOST` the host
the video files themselves are served from (resolvers.py passes it on to an Invidious relay, so the
relay fetches from the right server).

_At `PINTEREST_RE = re.compile(`:_

Pinterest runs a country subdomain per market -- co.pinterest.com,
br.pinterest.com, in.pinterest.com, ru.pinterest.com -- and the share sheet
hands out whichever one the sharer was on. Allowing only `www.` meant a link
from anywhere but the default host was answered with "that link is not recognised", which reads as the bot not supporting Pinterest at all.

What follows `pinterest.` is a top-level domain and nothing else: com, a
country code (pinterest.ca, pinterest.de), or the two-part forms
(pinterest.co.uk, pinterest.com.au). It used to be `[a-z.]+`, which is any
domain at all that begins with "pinterest." -- `pinterest.attacker.example`
was a Pinterest link, and the pin resolvers fetch whatever address they are
given and follow it wherever it leads, so a stranger's link could point this
container at any address it could reach, and at the `og:image` that page
named after that. See net.py, which refuses the addresses no link to a
public site should ever lead to.

_At `UNADVERTISED_RE = re.compile(`:_

Deliberately undocumented everywhere else (see bot.py's HELP_TEXT and
BOT_COMMANDS) -- handled if someone pastes one, never mentioned in any
command list or help text. It is also the one platform with no provider but
yt-dlp, so it is the one most likely to be refusing a server on any given
day; advertising it would be promising something the bot cannot keep.

The shapes a link is copied in: the app's share button (the short host's /ID?si=...), a browser's
address bar with other parameters ahead of the video (watch?app=desktop&v=ID,
watch?list=...&v=ID), the music site, and an embedded player's address (embed/ID). Up to
2.1.4 only "watch?v=" was read, and the others were answered as links the bot does not know.

_At `_PLATFORM_PATTERNS = [`:_

Ordered so the most specific patterns are tried first.

_At `_TRAILING_PUNCT = ".,;:!?)]}>»\"'"`:_

---------- The same post, written one way ----------

Every share sheet appends its own trackers to a link -- Instagram's `?igsh=`
and `?igshid=`, the unadvertised site's `?si=`, TikTok's `?is_from_webapp=1&sender_device=pc`,
`utm_*` everywhere -- and a different one for every person who shares it. So
the same post arrived as a different string each time, and everything keyed on
the string (the five-minute delivery cache, the redo memory, the "same link
again" check, the file-ID store) treated one post as many. detect_platform is
the single place a link enters the bot, so it is the place to write it one way.

An allow-list, not a block-list: each rule below keeps what identifies the post
and drops everything else. A parameter nobody has seen yet is dropped, which is
the safe direction (a post is never identified by a query string not on the list). Where a rule cannot find what it needs the link is returned unchanged:
a link this function does not understand must still go through as it was sent.

_At `_TRAILING_PUNCT = ".,;:!?)]}>»\"'"`:_

What the `\S+` of the patterns above swallows from prose: "look at this
https://...ABC/." or "(https://...)". A post code never ends with one.

_At `_IG_USER_POST = re.compile(r"/([^/]+)" + _IG_POST.pattern)`:_

`/<username>/reel/<CODE>/`. `share` is Instagram's own prefix for a redirecting
share link, whose id is not the post's code, so it is not a username.

_At `_PIN_HOST = re.compile(`:_

The same shape PINTEREST_RE accepts as a host: an optional country prefix, then
pinterest + a top-level domain, or pin.it.

### `watch_link()`

A video of the unadvertised site, written the one way: its watch page on the www host, with the
video's 11-character id. canonical_link turns every other shape (the short host, the music site,
shorts/, live/, embed/) into this one, and sent_files.py reads the id back out of it, so both build
the link here.

### `canonical_link()`

The link written the one way that identifies the post: share trackers,
query strings and fragments gone, the host and path normalised, the
punctuation a sentence leaves on the end of a link taken off. Pure, no
network, and it never raises -- anything it cannot read comes back as it
was given. Writing the result through it again changes nothing.

### `detect_platform()`

Returns (platform, canonical_link) for the first recognized link in
text, or None if nothing matches. The link comes back with its share
trackers removed (canonical_link), so one post is one string wherever it
was shared from.

_At `TEXT_PAGE_LIMIT_BYTES = 8 * 1024 * 1024`:_

What a Reddit or X answer may weigh: the 8 MB that resolvers.PAGE_LIMIT_BYTES
allows a provider's page, as a constant of its own because this module
depends on net.py alone and should not start importing the provider chain
(and a post's JSON or a tweet is far under it anyway).

_At `async def fetch_reddit_post(url: str) -> dict:`:_

---------- Reddit: public .json endpoint, no auth ----------

### `fetch_reddit_post()`

Reddit's own public .json endpoint on any post URL -- what old.reddit
and countless tools already rely on, no login needed for public
subreddits. Reddit's anti-scraping has gotten more aggressive about this
over time though (IP-reputation based, not just User-Agent), so this can
403 from some hosts even for a perfectly public post -- that's surfaced
as a FetchError with a clear message, not a stack trace.

- At `unreadable = "Couldn't read that Reddit post -- it may have been removed."`:
  Read through net.read_page, not `.get()`: `.get()` holds whatever the
  server sends, decoded however it was encoded, and a post's JSON is a
  page like any other (see net.body_chunks for what that can cost).

### `reddit_direct_image_url()`

A plain-image post's direct URL, if this isn't a gallery/video.

_At `def _tweet_id_from_url(url: str) -> str | None:`:_

---------- Twitter/X: public syndication endpoint, no auth ----------
The same unauthenticated endpoint X's own embed widget
(platform.twitter.com/widgets.js) calls to render an embedded tweet -- not a
private or reverse-engineered API. Kept for the \*card\*: resolvers.py handles
a tweet's media through fxtwitter/vxtwitter, but a text-only tweet has no
media to resolve and this is where its text, author and like count come
from. Best-effort by nature: X can rate-limit or change it without notice,
which is why the caller falls back to handing back the link.

### `_base36()`

Python doesn't have JS's Number.prototype.toString(36) built in --
this reimplements it (integer part + fractional digits) just precisely
enough to reproduce the syndication token below.

### `_syndication_token()`

X's embed widget computes this the same way to authorize an otherwise
open syndication request -- not a secret, just an obfuscation step:
((id / 1e15) \* pi) in base 36, with zeros and the decimal point
stripped.

### `fetch_tweet_syndication()`

- `except (httpx.HTTPError, FetchError):` — too big and unreadable encodings are FetchErrors
- At `if not data or data.get("__typename") != "Tweet":`:
  Deleted/protected/age-restricted tweets come back as a "tombstone" with
  a moderation notice instead of real content -- treat that the same as
  "couldn't fetch it" rather than rendering the notice as if it were the
  tweet.
