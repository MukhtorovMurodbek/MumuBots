# `big_files.py`

[↑ shared](README.md) · [code](big_files.py)

Files over the Bot API's size limits, moved over MTProto instead.

The cloud Bot API will not let a bot download anything over 20 MB or send
anything over 50 MB. Those are limits of the Bot API, not of Telegram: the
Bot API is itself only a client of MTProto, Telegram's own protocol, and over
MTProto a bot may move files of up to 2000 MB in either direction. So files
past the Bot API's limits come and go over MTProto, logged in as this same bot
with its own token, and everything else -- every update, every message, every
file under the limits -- stays exactly where it was.

Why not a local Bot API server, which lifts the same limits and which
LOCAL_BOT_API_URL has understood since 1.0: CHANGES_v2.0.0.md has the whole
argument. In short, its limits only lift in --local mode, where it hands back
paths on its own disk, so on Railway it has to live inside this container as
a second process; switching to it means logging the bot out of Telegram's
cloud, with ten minutes before it can come back; and while it is down the bot
hears nothing at all. This route is only ever used for a big file, and when it
fails, the bot is the bot it was before, refusing files over 20 MB.

Off unless the bot's API id and hash are set (my.telegram.org -> API
development tools): &lt;PREFIX>\_API_ID and &lt;PREFIX>\_API_HASH, or TELEGRAM_API_ID
and TELEGRAM_API_HASH once for every bot, in shared/.env. They are the
Telegram \*app's\* credentials, not the bot's, so one pair serves all of them.
With a local Bot API server configured this module is never asked: that
server carries big files itself.

SHARED SINCE 2.0.0. ConvertBot and DownloaderBot both move big files, so this
module lives in shared/ and each bot says who it is, once, before using it:
big_files.configure("CBOT", "convertbot"). The login is per bot token, and so
is the session file.

EACH TRANSFER IS A PROCESS OF ITS OWN, like each conversion (convert_worker.py).
Measured on 2026-09-26: importing Telethon costs the process 26.6 MB, and
connecting 4 MB more -- and Python never hands an imported library back, so
the first big file would have left ConvertBot 31 MB heavier for as long as it
ran, on a host that bills resident memory. So the bot process never imports
Telethon at all. A transfer runs this same file as a child (`python
main.py --module big_files &lt;job>`), which reports progress and its outcome as JSON lines on
stdout and exits, giving everything back. Starting one costs about a second,
against transfers that take many.

What the route does not do:

```text
- It never receives updates. The session asks Telegram not to send any
  (receive_updates=False), so it cannot take an update away from polling.
- It does not log in for every file. The login (an MTProto auth key, as good
  as the token) is saved to one file in the container's temporary directory,
  readable only by the bot's own user, and reused by the next transfer. It
  never goes into the database, where it would travel in every /dbdump, and it
  goes with the container. A crash loop never logs in at all: nothing does
  until somebody sends a big file.
- It is used in private chats only. A message id in a private chat is the
  bot's own, so the message can be fetched by id alone; a group would need
  more than that to find it, and a group is not where files get converted.
```

_At `BOT_API_DOWNLOAD_BYTES = 20 * _MB`:_

What the cloud Bot API lets a bot do.

_At `MTPROTO_MAX_BYTES = 2000 * _MB`:_

What MTProto lets a bot do, both ways. (4000 MB is for Premium accounts,
which a bot is not.)

_At `CONNECT_TIMEOUT_S = 20`:_

How long connecting and logging in may take before the attempt is given up.

_At `REST_AFTER_FAILURE_S = 300`:_

After a failed connection, how long every big file is refused straight away
rather than each one waiting out CONNECT_TIMEOUT_S to fail the same way.

_At `PART_BYTES = 512 * 1024`:_

Telegram serves a file in parts of at most 512 KB a request, and a
transfer that asks for one part at a time waits a round trip for each.
Telethon's own choice for anything under 100 MB is 128 KB, one after
another -- which is what held a laptop 260 ms from Telegram's servers to half a megabyte a second. So parts are the largest there are,
and a download keeps PARALLEL_PARTS of them in flight at once.

_At `STALL_S = float(os.environ.get("BIG_FILES_STALL_SECONDS") or 90)`:_

A transfer that is still moving is left to finish; one that has moved
nothing for STALL_S has stalled, and is stopped.

_At `TRANSFER_TIME_LIMIT_S = 600`:_

And an upper bound all the same, for a transfer that creeps: ten minutes,
or the file at 256 KB a second if that is longer -- two hours for the
largest there is, which only a transfer still moving would reach.

_At `PROGRESS_EVERY_S = 0.5`:_

How often the child reports how far it has got.

### `transfer_limit_s()`

The longest a transfer of `size` bytes may take.

_At `PREFIX = ""`:_

Set by configure(): whose token this route logs in with, and through
which app.

_At `SCRIPT = Path(__spec__.origin if __spec__ else __file__).resolve()`:_

The file the child runs as. The real path, not \__file_\_: in the one-process
edition a module's \__file_\_ is its bot's home directory.

_At `SESSION_FILE = Path(tempfile.gettempdir()) / "unconfigured-mtproto.session"`:_

One per token, so a new token is never handed an old login.

### `Unavailable`

The MTProto route could not be used. Nothing about the file is known to be wrong -- it is the route that is down.

### `Stalled`

The transfer stopped moving: nothing arrived or left for STALL_S. A
TimeoutError too, so whoever only cares that it ran out of time still
catches it.

### `NotThere`

The route works, but the message or its file is not what was asked
for: deleted since, or a different file than the one announced.

_At `CONFIGURED = False`:_

Decided in configure(): whether the route exists at all. Whether it is
\*working\* is \_resting_until's -- except that verify() switches it off for
good when Telegram refuses the API id and hash themselves.

_At `VERIFIED = None`:_

What verify() found, as the bot started: None until it has asked, then
whether Telegram accepted the API id and hash.

_At `_REFUSALS = ("ApiIdInvalid", "api_id/api_hash", "AccessTokenInvalid", "AccessTokenExpire …`:_

What Telegram says when the credentials themselves are wrong -- as opposed
to a network that is not answering, which says nothing about them.

### `configure()`

Which bot this is: its settings' prefix ("CBOT") and its name
("convertbot"), for its token, its API id and its session file. Returns
whether the route is on.

- At `SESSION_FILE = Path(tempfile.gettempdir()) / (`:
  One per token, so a new token is never handed an old login.

### `usable()`

Whether a big file in this chat can go over MTProto. Private chats
only: a user's id is positive, a group's is negative.

### `describe()`

One line for the startup log.

### `on_disabled()`

Call `hook()` if verify() switches the route off -- for a module that
worked out a limit from CONFIGURED when it was imported.

### `verify()`

Log in once, as the bot starts, so that an API id and hash that are set
but wrong switch the route off -- the bot then promises and takes exactly
what the Bot API allows, as if they had never been set -- instead of
failing the first person who sends a big file. A network that does not
answer is not a verdict: the route stays on, and the next transfer finds
out. Returns whether the route is on. Never raises.

- `if not _closing:` — stopped by close(): the bot is shutting down, not the route failing

## The bot's side: start a child, follow it, say how it ended

### `_child_environment()`

What the child needs, handed over explicitly and under names of its
own: in the one-process edition this module sees its bot's settings
through a view of its own, the child inherits only the real environment,
and the child has no way to know which bot started it.

### `_run_one()`

Run one transfer in a child and return its last line. Raises
Unavailable, NotThere, asyncio.TimeoutError past the limit, or
RuntimeError for anything else the child reported.

#### `_run_one.keep_stderr_tail()`

- At `while chunk := await proc.stderr.read(65536):`:
  Read as it comes, and in chunks rather than lines: a chatty library
  must not fill the pipe and stall the child, and one line longer than
  the stream's 64 KB limit would kill a line-by-line reader and leave
  wait() waiting for a pipe nobody empties. Only the end is reported.

#### `_run_one.follow()`

- `continue` — a line over the limit: not one the child wrote; readline dropped it

### `close()`

Stop any transfer still running -- the bot is shutting down. Never raises.

## The child's side: one transfer, then exit

### `_save_session()`

Written whole and then renamed, so a reader never sees half a file. Under
a name of its own, so two transfers that log in at once cannot rename each
other's file away, and created readable by this user alone rather than
made so afterwards.

### `_connect()`

A connected, logged-in client. Raises Unavailable.

- At `logger.warning("Could not keep the MTProto login in %s; the next transfer logs in again.",`:
  Keeping the login is an economy, not a need: the next transfer
  logs in again. It must not turn a login that worked into "the
  route is down", which is what failing here did -- every big
  file refused for five minutes over a full or read-only /tmp.

### `_Moving`

When the transfer last moved a byte.

### `_watched()`

Run a transfer to its end, unless nothing moves for STALL_S or it
runs past `limit_s`.

### `_download_in_parallel()`

PARALLEL_PARTS streams, each asking for every PARALLEL_PARTS-th part of
PART_BYTES, written where it belongs as it arrives.

### `_child_download()`

- At `message = await client.get_messages(None, ids=job["message_id"])`:
  No chat given: for anything but a channel that is
  messages.getMessages, which finds a message by id alone.
- At `if getattr(message.peer_id, "user_id", None) != chat_id:`:
  Message ids in private chats belong to the bot's whole account, so the
  id alone finds the message -- and so it is checked to be in the chat it
  was sent in, and to carry the file that was announced.

### `_child_send()`

- At `uploaded = await client.upload_file(job["path"], part_size_kb=PART_BYTES // 1024,`:
  Uploaded first, in the largest parts Telegram takes, then sent: left to
  send_file, anything under 100 MB would go up 128 KB at a time.

### `_child()`

- At `await asyncio.wait_for(client.get_me(), CONNECT_TIMEOUT_S)`:
  verify(): the login alone, and Telegram's own view of who it is.

_At `API_ID = os.environ.get("BIG_FILES_API_ID", "")`:_

The child: told everything by the bot that started it.
