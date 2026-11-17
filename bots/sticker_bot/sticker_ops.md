# `sticker_ops.py`

[↑ sticker_bot](README.md) · [code](sticker_ops.py)

What adding a sticker and retagging one take, for the private chat (bot.py) and groups
(group_mode.py) alike.

Since 2.2.0 every sticker added gets a short reply of its own ("✅ Added as #4 with 😭"),
and its emoji can be changed later by replying to it with emoji. Both were asked for on 2026-10-28: several pictures sent quickly were all added, but the one status message
the bot kept updating read as if only one had been, and "reply to the sent sticker, or to
the reply to that added sticker, with the right emoji" is how a person points at one.

```text
A reply has to say which sticker it means, and the two messages it can point at say it
differently:
  * a sticker message names its set and carries the sticker's file id itself, so that
    always works, for any sticker in a pack made here, however old;
  * the picture the person sent, or the bot's "Added" reply to it, says nothing about the
    sticker made from it. Those are remembered here, in memory: (chat, message) -> (pack,
    sticker), for the last REFS_MAX additions. After a restart only the sticker itself
    can be replied to.
```

_At `TITLES_MAX = 5000`:_

The title Telegram gave the last time a pack was read back (last_sticker), so the private chat
can follow a rename made in Telegram's own sticker settings (bot.py, since 2.1.6). Bounded.

### `remember()`

- `return` — the pack could not be read back: nothing to point a retag at

### `sticker_of()`

(pack name, sticker file id) that `message` stands for, or None.

### `in_pack()`

True if `sticker` is in pack `pack_name` right now.

`sticker.set_name` alone isn't enough to decide this: it reflects whatever set the
message's \*sender\* last saw the sticker belong to, and a sender's sticker panel can lag
behind a removal the bot itself just made (remove it, then re-send the same sticker from
the panel before Telegram's client has refreshed it). So once set_name points at the
pack, it is checked against the pack's actual current contents.

- `return True` — can't verify right now -- fall back to trusting set_name

_At `ASKED_MAX = 1000`:_

"Add it again?" questions waiting for a tap (group_mode.ask_add_again), in memory only: the
button carries a token, because a sticker's file id does not fit in a button's 64 bytes.
Gone on a restart, after ASKED_SECONDS, or past ASKED_MAX waiting at once; the button then
says the question has expired.

### `Unsupported`

The message holds nothing this bot can turn into a sticker.

### `media_of()`

("static" | "video", file id) of what in `message` can become a sticker, or None.
Animated (Lottie/.tgs) stickers are not supported anywhere in the family.

_At `TELEGRAM_TRIES = 3`:_

---------- Telegram's limits and hiccups (2.1.4) ----------
Telegram limits how quickly stickers go into a pack. A run of pictures (an album) meets that
limit as "Flood control exceeded. Retry in 10 seconds" (production, 2026-10-30), and that
picture was lost. A request answered that way was not carried out, so it is sent again after the
wait: at most TELEGRAM_TRIES times, and only for waits of up to FLOOD_WAIT_MAX seconds -- a
longer one fails that picture with the usual note. Reads (fetching the picture, reading the pack
back) are also tried again after a timeout or a dropped connection: on 2026-11-04 a picture's
download timed out and the person got the crash reply. Creating a pack or adding a sticker is
never repeated after a timeout, since it may have gone through. Only the person who sent the
pictures waits: other people's updates are handled meanwhile (PerUserUpdateProcessor).

_At `NAME_TRIES = 3`:_

"Shortname_occupy_failed" (production, 2026-10-29): Telegram could not take a new pack's name.
Another random name is tried, up to NAME_TRIES names in all.

### `telegram_call()`

`call(*args, **kwargs)`, sent again when Telegram asks to wait, and with read=True also
after a timeout or a dropped connection.

- At `if not read or isinstance(exc, BadRequest) or attempt == TELEGRAM_TRIES:`:
  BadRequest is a NetworkError in python-telegram-bot: a refusal, never sent again.

### `name_refused()`

Telegram would not take a new pack's name: another one usually works.

### `static_sticker()`

- At `png = (await asyncio.to_thread(to_sticker_png, raw)).getvalue()`:
  Pillow is CPU work: on the event loop it would hold up everybody's updates.

### `video_sticker()`

Raises video_sticker.ConversionError when the clip cannot fit Telegram's limits
(video_sticker.TooBig when only its size is in the way).

### `add_to_pack()`

Adds to an existing pack, on its creator's behalf (Telegram ties a set to the user it
was created for, whoever is sending). Returns (the new sticker's file id, its place in
the pack), or (None, None) with want_file_id=False: the file id costs a getStickerSet,
which a bulk import does not need for every one of a hundred stickers.

- At `await telegram_call(bot.add_sticker_to_set, user_id=owner_id, name=pack_name, sticker=in …`:
  Uploading a converted video sticker can take longer than python-telegram-bot's
  default 5 seconds, which showed as a "Timed out" on an upload that went through.

### `last_sticker()`

The pack's newest sticker's file id and how many stickers the pack has; (None, None)
when the pack cannot be read back. The sticker is in by then: only its number and the
handle for retagging it are missing.

### `added_text()`

What an added sticker's reply says: its place in the pack, or just that it went in when
the pack could not be read back -- and `how` a clip was made to fit, if it was.

_At `def error_key(exc: Exception) -> tuple:`:_

---------- what a failed add says ----------

### `error_key()`

Which explanation a raw Telegram error from set-creation/add calls
gets, as (i18n key, fields).

- At `return "err_timed_out", {}`:
  Not a rejection -- Telegram (or a local Bot API server) just
  didn't confirm in time, most often on a slow connection uploading
  a converted video sticker. The upload may well have gone through
  anyway, so don't call it "rejected" -- that's actively misleading.

### `explain_error()`

Turns raw Telegram API errors from set-creation/add calls into
something the user can actually act on, instead of a bare exception.

### `error_note()`

The status note a failed sticker leaves: what went wrong, what to do
next, and its problem code last, where the Report button looks for it.
