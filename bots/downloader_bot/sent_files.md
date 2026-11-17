# `sent_files.py`

[↑ downloader_bot](README.md) · [code](sent_files.py)

Telegram's file IDs for what this bot has already sent, kept so the same post can be sent
again without being downloaded again.

Telegram gives every file the bot sends a `file_id`, and the same bot can send that id to any
chat without uploading anything. So a post this bot has delivered once, to anybody, is
delivered again from its ids: no route is asked for the media, nothing is downloaded, nothing
is uploaded. The ten-minute delivery cache (delivery_cache.py) does the same for one person
and a few minutes; this one is for everybody and days.

What it holds, and what it does not. One row per post and per form of delivery, and the row
holds nothing about people:

```text
* the key is a keyed hash of "version | platform | post | form" -- not the link, and not the
  person. The hash key comes from the bot's token, so nobody who can read the table can
  compute the key of a post they are curious about, and a rotated token makes every old
  row unreachable (the sweep deletes them). No new secret to deploy.
* the value is the list of (kind, file id) in delivery order, the last time the post was
  seen public, and the day it was last used. No user id, chat id, name, link, platform or
  caption anywhere. /deletemydata has nothing of a person's to delete here.
* `form` is "file" when the delivery is lossless (everything went as documents) and "media"
  otherwise: Telegram cannot send a video's id as a document, so they are two rows.
* `post` is the post's identity (post_identity): the same reel shared as /p/<code>/ and as
  /reel/<code>/, or a post on X under whichever account's name, is one row, not two.
```

How it is laid out (since 2.2.0; 2026-11-09: more people means more stored posts, so the lookup is made cheap now rather than when it is already slow). The lookup has always been
one probe of the primary key's B-tree, which stays a few page reads at any size; what grows is
the table and its index, and an index that no longer fits in the database's memory is what
makes a lookup slow. So a row is kept small (measured at a million rows: 216 bytes a row
instead of 376, and an index of 38 MB instead of 119):

```text
* the key is the first 16 bytes of the HMAC as a UUID column -- a fixed 16 bytes compared
  with memcmp, where the hex text it replaces was 65 bytes in the table and again in the
  index. 128 bits cannot collide among any number of posts this bot will ever see;
* the ids are stored as the bytes their base64url text encodes (a quarter smaller), with a
  byte for the kind and one for the length (encode_items);
* marking a row used writes nothing when it was already marked today, so a popular post
  costs no new row version per delivery (db.sent_files_used), and the table is made with
  room on each page (fillfactor 90) so the daily mark is a heap-only update. last_used is
  deliberately not indexed: an index on it would make every one of those marks touch the
  index too. The daily sweep reads the table once instead, which costs a fraction of a
  second at this size.
```

And it is read through a small memory of the rows this process used lately (recall/remember):
a post that many people send within minutes costs one database round trip, not one each. The
memory never decides trust -- is_fresh() reads the stored confirmation time on every use, so a
remembered row is exactly as trusted as the database row it copies.

Telegram does not say how long an id lasts, so the stored ids are always tried and an id that
Telegram refuses as invalid deletes the row and the post is fetched as usual. What the store
must never do is send a post that was removed: a row is trusted for CONFIRM_HOURS after the
post was last seen public (by a fetch, or by resolvers.lookup), and only then asked again.

This module has no database and no Telegram in it (is_invalid_file_id imports the error class
only when it is called): bot.py makes the database calls and the sends, and hands the results
here. The clocks can be replaced (`_clock`, `_monotonic`) so that tests need not wait.

_At `STORE_VERSION = 2`:_

Bumping this makes every existing row unreachable at once (a change in how files are made,
or in what names a post); the sweep deletes them later. 2: 2.2.0's post identities.

_At `MAX_ITEMS = 10`:_

One chunk of an album. A post with more items than this is not stored: it would be sent in
several chunks, and an id that is invalid in the second would re-send the first.

### `_whole_number()`

The number in the environment, `default` when it is not set, None when it is not a
whole number.

### `read_settings()`

(on, confirm hours) from the environment.

DBOT_SENT_FILES: 1 (the default) is on, 0 is off, and so is anything that is not a whole
number -- whoever typed "off" meant no, like DBOT_CACHE_SECONDS. DBOT_FILEID_CONFIRM_HOURS:
12, and 12 again for a value that is unreadable or under 1: a typo here must not stop the
bot starting, and must not make the check never happen. (How long unused rows are kept,
DBOT_FILEID_KEEP_DAYS, is read where they are deleted: db.py.)

- `_monotonic = time.monotonic` — replaced by tests

### `configure()`

The hash key, from the bot's token. Without a token there is no key, and the store is
off: a key anybody can guess would make the rows readable to anybody who sees them.

### `usable()`

Whether the store may be read or written for this sender: it is on, and the id is one
person's. A sender id that stands for many people (an anonymous group admin, a channel,
Telegram's own service account) never reads or writes it, as with the delivery cache.

## Which post a link names

The links arrive cleaned by platforms.canonical_link, which keeps what a fetch needs: an
Instagram link stays /p/ or /reel/ as it was shared, an X link keeps the account name. For
the store only the post matters. Each pattern reads one shape canonical_link writes; anything
else is named by its link, as before.

### `post_identity()`

The post this (canonical) link names, written one way: "instagram:&lt;code>",
"twitter:&lt;status id>", the same for the unadvertised site with its video id, or else the link itself. A quality mark the
picker added ("#q720", "#upto480") stays on the end: a capped copy is another delivery.
Canonical links carry no fragment, so whatever follows a "#" is such a mark.

### `post_key()`

The row of this post in this form: the first 128 bits of an HMAC-SHA256, as the UUID
column holds them. See the module docstring.

## What a row holds

### `StoredFile`

One stored file as bot.\_send_media takes it in place of a MediaItem: what it was (the
kind of the item sent, whatever the form) and the id to send it by.

_At `FORMAT = 1`:_

The row's bytes: FORMAT, then for each item a code byte (the kind, plus TEXT_ID when the id
is kept as its text), a length byte, and the id. A file id is base64url text without padding,
so it is kept as the bytes it encodes -- but only when encoding them again gives back exactly
the same text, which is checked for every id; one that does not (Telegram changing its ids'
alphabet, say) is kept as its text, flagged, rather than refused.

### `_id_bytes()`

(flag, bytes) to keep this id as, or None when it is too long for a length byte.

### `encode_items()`

[(kind, file id), ...] as the bytes of a row, or None if it is not something a row may
hold (1 to 10 items, kind video or photo, a non-empty id of at most 255 bytes).

### `decode_items()`

The pairs in a row, or None for anything that does not read as 1 to 10 items exactly:
such a row is treated as if it were absent.

### `is_fresh()`

Whether the post was seen public less than CONFIRM_HOURS ago. A time in the future
(the two clocks disagree) is fresh within five minutes of it and no more, so that a
wrong clock cannot keep a post trusted for ever; anything that is not a time is not fresh.

## The rows this process used lately

A copy of a database row, kept RECENT_SECONDS: (items bytes, confirmed_at, last_used). Only
this process writes the table in normal running (one DownloaderBot at a time); for the minute
two overlap during a redeploy, the worst a stale copy can do is send an id the other one has
just dropped, which Telegram refuses, and that path drops the row and downloads as usual.

### `recall()`

The remembered row for this key, if it is recent enough; None to ask the database.

### `used_today()`

Whether this process has seen the row marked used today, so marking it again would
write nothing anyway (db.sent_files_used) and need not cost a round trip.

### `note_used()`

The row was marked used today (and, with `confirmed_at`, seen public then).

_At `_INVALID_ID_MARKERS = (`:_

What Telegram's refusal of an id says (lowercased). Anything else -- Forbidden (the person
blocked the bot), RetryAfter (flood), TimedOut, NetworkError, any other BadRequest -- is an
ordinary send failure that says nothing about the id.

## Counts for /status (since the bot started; nothing about who or what)
