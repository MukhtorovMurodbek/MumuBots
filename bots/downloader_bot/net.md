# `net.py`

[↑ downloader_bot](README.md) · [code](net.py)

One HTTP client for the whole process, and the two ways this bot fetches
media with it.

Every fetch used to build its own `httpx.AsyncClient` inside an `async with`
-- a fresh TCP connection and TLS handshake per request, thrown away straight
after, against hosts considerably further away than the database this family
already learned that lesson on. One client, kept open, reuses connections
across requests and across users. The pool is small on purpose: this bot is
never talking to more than a handful of hosts at once.

Split out of platforms.py at v1.2.1, because resolvers.py needs the same
client and importing it back out of a module about \*page scraping\* had the
dependency arrow pointing the wrong way.

_At `UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "`:_

A real browser's UA. Several of the media hosts below serve a challenge page
to anything that admits to being a script, and a challenge page is not
something this bot can answer.

_At `CRAWLER_UA = "TelegramBot (like TwitterBot)"`:_

The other half of the story: the "fix the embed" services (tnktok,
eeinstagram, fxtwitter, ...) exist to serve link-preview crawlers, and they
decide what to put in the page from the User-Agent. Asked as a browser, they give the human-facing page; asked as a crawler, the OpenGraph tags
with the media URL in them. So those providers ask as a crawler, on purpose.

_At `MAX_RESPONSE_BYTES = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48")) * 1024 * 1024`:_

Telegram itself refuses uploads over 50 MB without a local Bot API server,
so anything larger was always going to be fetched and then thrown away.

_At `FAILOVER_CONNECT_TIMEOUT = 4.0`:_

How long an address gets to answer when the host has another to try after it.

### `FetchError`

Raised with a message that is safe to show the user as-is.

### `BadEncoding`

The server sent a body in an encoding this bot does not read (or one
that did not decode). See body_chunks().

### `Trickle`

The server sent its body in far too many pieces for its size: see
body_chunks(). A BadEncoding so that whoever already treats an unreadable
body as a failed route (platforms.py's Reddit and Twitter reads) does the
same here, without a change of its own.

## Only the public internet

Every address this client fetches was handed to it by somebody else: a link
a stranger pasted, a page a stranger's link led to, a media URL a third-party
resolver service put in its answer, a post a stranger wrote on Reddit. Left
alone it will fetch any of them -- including `http://127.0.0.1:...`, an
address on the container's own private network, or a cloud host's metadata
service at 169.254.169.254 -- and follow a redirect from a public address to
any of those. What it fetched then comes back to the person as the "photo" or
the file they asked for (the lossless setting sends the bytes as they are).

So a request is checked where it is made: the name is resolved here, every
address it resolves to must be on the public internet, and the connection is
then made to the address that was checked -- not to whatever the name
resolves to a moment later, which a DNS server built for the purpose can
arrange to be different. Every hop of a redirect goes through the same
check, because each is a request of its own.

yt-dlp does its own networking and is not covered by this; what reaches it
is a link to a site the bot names (platforms.py), which is what it is for.

### `BlockedAddress`

The name resolves to an address that is not on the public internet.

### `is_public_address()`

Whether an IP address is somewhere on the public internet: not this
machine, a private network, a link-local address (which is where cloud
metadata services live), shared address space, multicast, or unspecified.
An IPv4 address written as IPv6 is judged as the IPv4 address it is.

### `_resolve()`

Every address `host` resolves to. Its own function so that a test can say
what a name resolves to.

### `public_addresses()`

The addresses to try for `host`, all of them public -- or BlockedAddress.

One address that is not public refuses the name: a name that answers with a
mixture is how the checked address and the connected address are made to
differ.

### `_guarded_transport()`

The transport `client()` uses: `inner` (an ordinary httpx transport)
behind public_addresses(). Built here rather than at import so that this
module can still be imported where httpx is a stand-in.

#### `_guarded_transport.PublicOnlyTransport`

##### `_guarded_transport.PublicOnlyTransport.handle_async_request()`

- `host = url.raw_host.decode("ascii")` — the ASCII form: it is also what TLS names
- At `addresses = sorted(addresses, key=lambda address: ":" in address)`:
  IPv4 first, the order kept otherwise. The client this replaces
  raced a host's addresses against each other, so an address that
  was not answering cost a quarter of a second; these are tried in
  turn, and a container with an IPv6 route that goes nowhere would
  spend its whole connect timeout on the first.
- At `extensions = dict(request.extensions)`:
  The request is sent to the address that was checked, with the
  name it was asked for kept where the server and the
  certificate look for it: the Host header, and the name TLS
  verifies. Which is why this is a request of its own rather
  than a change to the one it was handed.
- At `timeouts = dict(extensions.get("timeout") or {})`:
  Another address is waiting: this one is not given all day.
- `failure = exc` — the next address may answer

### `_without_redirect_body()`

A redirect, with its body thrown away unread; anything else, as it came.

When httpx follows a redirect it first reads the redirect's own body whole
-- `aread()`, decoded by its own decoders, with no limit -- and only then
goes where the Location header says. That body is never used, and it is
the one body body_chunks() never sees: a server answering 302 with a
brotli or gzip bomb would decode it in this process before the next hop.
The status and the headers are all a redirect is for, so that is all that
is passed on.

_At `ACCEPT_ENCODING = "gzip, deflate"`:_

The only encodings this bot asks for, and the only two body_chunks() reads.
httpx would otherwise advertise everything it can decode: `br` the moment
brotli is installed, and yt-dlp[default] installs it; `zstd` likewise. See
body_chunks() for why those two are not wanted.

### `close_client()`

Called from the bot's shutdown hook.

## Reading a body, with a ceiling on what it may become

A size limit that counts what the caller receives is not a limit on memory
when something in between decompresses. httpx decodes a whole raw chunk
before the caller sees any of it, so a 421-byte brotli body that decodes to
512 MB was 512 MB in this process before the first `len(chunk)` ran: the
page reader's 8 MB cap peaked at 1,069 MB on exactly that (review,
2026-10-08), in a container that five bots share 1 GB of. gzip does the same
at a thousand to one, so it is not only a brotli problem.

So nothing here lets httpx decode. The bytes are read raw (aiter_raw) and
decompressed by zlib, which can be told how much output one call may
produce (max_length); the rest of the input waits in `unconsumed_tail`
until the caller has counted what came out. Only gzip and deflate are read,
because those are the two zlib can do that for. brotli and zstd cannot be
capped from here, so they are refused outright -- and are not asked for, see
ACCEPT_ENCODING; a server that sends one regardless is refused, not trusted.

_At `_DECODE_BLOCK = 64 * 1024`:_

The most one decompress() call may return, whatever room is left under the
limit. Without it a body allowed 48 MB would come back in one 48 MB piece,
and stream_to_file, which exists to hold a chunk at a time, would hold all of it.

_At `_KNOWN_ENCODINGS = ("gzip", "x-gzip", "deflate", "br", "zstd", "compress", "x-compress")`:_

Every Content-Encoding token that names a real encoding. A token that is not
one of these ("none", "utf-8", "binary" -- servers do send them) means no
encoding, as it does to httpx, and the body is read as it comes, still under
the ceiling. Of the real ones only gzip and deflate are read.

### `_content_encodings()`

The real encodings a body was sent in, lower-cased: "identity" (which
means none), empty entries and tokens that are no encoding at all are
dropped. The header, when it repeats, is already joined with commas by
httpx.

### `_Inflater`

One gzip or deflate body, decompressed a bounded piece at a time.

#### `_Inflater.__init__()`

- `self.total = 0` — decoded bytes handed out so far
- `self.finished = False` — the compressed stream has ended
- `self._first = deflate` — the first call still gets a second chance
- `self._head = b""` — deflate's first bytes, held until there are two to judge by

#### `_Inflater._room()`

max_length for the next call: one byte more than what may still be
accepted, so that a body over the limit shows as over rather than as
exactly at it.

#### `_Inflater._decompress()`

- At `self._first = False`:
  "deflate" is zlib-wrapped by the standard and raw by habit; servers
  send both. The first bytes tell, and a failure on them means raw, as
  in httpx's own decoder. The same input is given to the new decoder.

#### `_Inflater._drain()`

Decoded pieces of `data`, none over \_DECODE_BLOCK, until zlib has
nothing more to give for it. Raises too_big() the moment the total is
over the limit.

- At `self.finished = True`:
  The stream is complete. Whatever follows it is not part of
  it and is ignored, as httpx does -- and not fed in, because
  zlib keeps all of it in `unused_data`, without a bound.
- At `if self.d.unconsumed_tail or len(out) == room:`:
  More to take out when input is left over because the output was
  capped, or when the cap was hit exactly and zlib may be holding
  output it has not been asked for yet.

#### `_Inflater.feed()`

- At `data = self._head + data`:
  Wrapped or raw is told by the first two bytes (the zlib header),
  and a first piece of one byte cannot tell: judged on it alone
  the second chance was spent, and a raw stream failed on its next
  byte as a body that "did not decode".

#### `_Inflater.finish()`

- At `if not self.finished:`:
  (A single byte still held back here is the whole body: no deflate
  stream is that short, so it is a truncated one, which is accepted
  as it always was -- it gives nothing.)

### `body_chunks()`

The body of a streamed `response`, decoded, in pieces; `too_big()` is
called (it returns the exception to raise) the moment more than `limit`
decoded bytes have arrived.

A body in an encoding this reads only as identity, gzip and deflate is
refused with BadEncoding before a byte of it is read, whether this bot
asked for it or not (br, zstd and compress) -- and so is two encodings
stacked, which is a way of asking for more than one decoder's worth. A
Content-Encoding token that names no encoding at all ("none", "utf-8")
is no encoding, as it is to httpx, and the body is read as it is.
Nothing about the response is trusted past what a status line says: not
Content-Length, which a server can leave out or get wrong, and not the
encoding's own idea of how big it will be. And a body that arrives in
more pieces than its ceiling can explain is refused with Trickle.

Use as `async for piece in body_chunks(...)`. A caller that stops early
should `await pieces.aclose()` on it, so that the connection is let go now
rather than whenever the interpreter gets to it.

- At `content = response.content`:
  Already read whole, and decoded by httpx when that happened: what a
  test's MockTransport hands back for `Response(content=...)`. A real
  connection never gives one of these, and for it there is nothing
  left to bound but the size.
- At `raw_cap = limit + limit // 50 + 64 * 1024`:
  What a compressed body may weigh \*before\* decoding. A stream of empty
  blocks decodes to nothing for ever and would never reach the limit; real
  compressors stay within a fraction of a percent above the size of their
  input, so this is a ceiling with a wide margin, not a measurement.
- At `pieces_left = limit // 1024 + 1024`:
  And how many pieces it may arrive in. Each piece costs the event loop a
  few microseconds whatever its size, so a server that sends one byte at a
  time makes a 2 MB body 12 seconds of this process's one thread (48 MB:
  five minutes) -- for every bot of the combined edition at once. A real
  server sends kilobytes at a time; this allows a piece per KB of the
  ceiling and a thousand more.

### `read_page()`

A whole small document, read under a ceiling, as an ordinary
`httpx.Response`: status, final URL, history, the body decoded.

`too_big()` builds the exception for a body that grew past `limit`.
A Content-Length that already says it will is refused before a byte is
read, with `declared_too_big(declared)` if given (so that it can say how
much) and `too_big()` if not. BadEncoding, see body_chunks, is the caller's
to translate. The status is the caller's too: nothing is raised for a 404.

- At `kept = [(k, v) for k, v in resp.headers.multi_items()`:
  The body is decoded already (gzip and friends), so the headers that
  described the encoded one come off before it is wrapped again.

### `fetch_bytes()`

GET a URL into memory, refusing anything that would not fit in a
Telegram upload anyway.

Streamed and counted as it arrives, so a server that lies about (or omits)
Content-Length still cannot make this process hold an unbounded amount of
data -- and counted after decoding, by body_chunks, so that a small
compressed body cannot become a large one on the way.

_At `ESTIMATE_SLACK = 1.25`:_

How far past the ceiling a server's guess at a file's length (Estimated-Content-Length) has
to be before the file is refused unread.

### `TooLarge`

Over the size ceiling this download was given. A FetchError still, so
whoever only cares that it failed catches it as before; DownloaderBot
catches this one first, because a file over the free ceiling may be one
somebody has switched on large files for (large_files.py).

### `stream_to_file()`

Same, but to disk, for the things that are too big to want in memory.

Returns the byte count. Videos go this way and images do not, because
python-telegram-bot streams a file object off disk to Telegram rather than
reading it in first -- so a 45 MB video is never resident, while a 2 MB
JPEG is not worth the round trip through the filesystem.

HEAD is deliberately never used to size-check first. TikTok's CDN answers
HEAD with a 503 while serving the identical GET perfectly, which turned a
working download into a "that file is unavailable" for no reason at all.
Content-Length on the GET says the same thing a beat later and is always
there when it matters.

- At `guessed = response.headers.get("estimated-content-length")`:
  A relay that builds the file while it sends it (cobalt's tunnel) cannot say how
  long it will be, and says what it expects instead. That is a guess from the
  bitrate, so only one well past the ceiling is believed: the file is then refused
  now, not after minutes of fetching it.
