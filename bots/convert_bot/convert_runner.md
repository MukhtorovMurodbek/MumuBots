# `convert_runner.py`

[↑ convert_bot](README.md) · [code](convert_runner.py)

The Telegram side of a conversion job.

jobs.py decides what happens -- when credit is taken, when it goes back, who
is told. This file is how it is said: the status message a person watches,
the file they receive, the sentence that explains a failure, and the message the operator gets when the bot is at fault.

Kept out of bot.py for two reasons. bot.py is already the file every handler
lives in, and this is not a handler: it runs in a background task long after
the button that started it was answered. And it takes its dependencies in
its constructor -- the application, the admin ids, the caption builder --
so nothing here imports bot.py and nothing can go round in a circle.

_At `PROCESS_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"`:_

Which process a paid conversion in flight belongs to -- see jobs.HOLD_STALE_S.

### `_target()`

The job's target as the person is told it: PNG, or "Searchable PDF" for
the pseudo-target ocrpdf, never the pseudo-extension itself.

### `_pair()`

"png->pdf", for the credit ledger and the in-flight record, which the
person can read in /balance: the pseudo-target ocrpdf is written out.

## The status card: one message, redrawn while the person waits

### `progress_bar()`

Ten cells and the percentage. Never 100% before the work is done: a
full bar with the file still to come reads as a hang.

### `clock()`

0:07, 1:42, 12:05 -- a waiting time somebody can read at a glance.

### `time_left()`

Seconds still to go, from the rate so far -- or None until there is a
rate worth believing: a guess from the first second is noise, and a
number that swings wildly is worse than no number.

### `download_card()`

The same card for a file arriving, before there is a conversion.

### `result_filename()`

What the converted file is called when it arrives.

Every result used to be `converted.pdf`. Convert three documents and a
phone downloads `converted.pdf`, `converted (1).pdf`, `converted (2).pdf`
-- or, on a client that overwrites rather than numbering, one file three
times. Asked for: a unique name for every result, for example from the date and time of the conversion.

So it carries the moment it was made, to the second, in an order that
sorts: `converted_2026-09-13_235812.pdf`. UTC, because the container has
no idea what time it is where the person reading the name is, and a name
that claims a local time it guessed is worse than one that does not
claim one.

### `ConvertRunner`

- At `def stop_keyboard(self, job: jobs.Job) -> InlineKeyboardMarkup:`:
  ---- the status message --------------------------------------------

#### `ConvertRunner._status()`

Rewrite the conversion's one status message. When something newer is
below it -- a progress note, or the person writing -- it moves to the
bottom as a new message, and the copy left above is deleted: two
status messages for one conversion, one of them frozen, is exactly
what this card exists to avoid.

- At `async def refuse_start(self, job: jobs.Job) -> bool:`:
  ---- the policy's hooks --------------------------------------------

#### `ConvertRunner.card()`

The live status, redrawn only when what it says has changed.

#### `ConvertRunner.progress()`

A new message, not an edit: an edit arrives silently, and the point
is that somebody who has waited two minutes hears about it. Sent under
the status line, which is then moved below it for the ending.

- At `sent = await self.bot.send_message(chat_id=job.chat_id, text=text)`:
  Not a reply to the status card: the card moves below this note
  on its next redraw, and the copy this would quote is deleted.

#### `ConvertRunner.deliver()`

##### `ConvertRunner.deliver.sending()`

Over what the Bot API may send, so over MTProto instead. jobs.py
only lets a result this size through when that route exists
for this chat; if it has gone down since, this raises and the
job ends as a failed send, refunded.

- At `with open(outcome.path, "rb") as handle:`:
  Streamed from disk rather than read into memory: the result of a
  large conversion should never have to exist in the bot's process.

#### `ConvertRunner.succeeded()`

- At `try:`:
  After the person's line, and never in its way: a statistic must not cost them their result.

#### `ConvertRunner.keep_alive_and_recover()`

Stamp this process's paid conversions, and give back the credit of
any whose process was killed before it could. Returns how many were
given back. Called every jobs.HOLD_TOUCH_S.

#### `ConvertRunner.failed()`

- At `code = problems.JOB_ENDINGS.get(kind, "CV-CRASH")`:
  The code, and an incident the operator's alert and this log line carry
  too, so a report from this person meets both.

#### `ConvertRunner.alert()`

Straight to the operator, from this bot, in English.

Sent directly rather than only through family.events, because the
family log is read by ManagerBot from the shared database and a
TestBot on a laptop writes to a local one -- so an event alone would
reach nobody from exactly the place where testing happens. It is still
recorded as an info event, which ManagerBot does not forward, so in production the operator gets one message rather than two; if no admin
can be reached directly it is raised as a warning instead, which
ManagerBot does forward.

- At `credit = (f"{job.price} ⚡ back to the balance" if refunded_to is not None`:
  What failed and what it cost to run -- never who it happened to.
  No user details go into anything sent to the operator: no id, no username, and no person's balance.
