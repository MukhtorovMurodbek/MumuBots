# `convert_worker.py`

[↑ convert_bot](README.md) · [code](convert_worker.py)

One conversion, in a process of its own.

ConvertBot used to run convert() on a thread inside the bot's own process.
Three things go wrong with that, and a 200-megapixel photo found all of them
at once:

```text
it cannot be stopped. Python has no way to kill a thread, so a time limit on
a threaded conversion only stops *waiting* for it -- the work goes on,
holding a core and a gigabyte, after everybody has given up on it;
it does not give memory back. Decoding a photo that size takes the process
past a gigabyte, and CPython's allocator returns little of that to the
operating system afterwards, so the bot idles fat until it is restarted --
on a host that bills resident memory by the second;
it is not isolated. A crash in a C decoder, or the kernel's OOM killer
choosing a victim, takes the whole bot down with everybody's pending work.
```

A child process fixes all three for the price of starting one. jobs.py gives
it a deadline and kills it when the deadline passes; the operating system
takes the memory back the moment it exits; and if it dies, it dies alone.

The protocol is deliberately plain. The job arrives as one JSON argument and
the outcome leaves as one JSON line -- the last non-empty line on stdout.
Anything a library prints along the way is ignored.

```text
{"ok": true,  "path": "...", "zipped": false, "items": 1, "bytes": 123}
{"ok": false, "kind": "refused", "message": "..."}   a ConversionError
{"ok": false, "kind": "crash",   "message": "..."}   anything else
```

A second kind of job shares the process and the protocol: {"kind":
"describe_raw", "path": ..., "src_ext": ...} asks for the size of a camera raw
photo, which is LibRaw parsing a stranger's file and so is not done in the
bot's own process. Its answer is {"ok": true, "info": {&lt;RawInfo's fields>}} or
one of the failures above. So does {"kind": "describe_pdf", "path": ...}, which
asks how many pages a PDF has and how many of them are pictures without text
(ocr_convert.count_scanned), for the same reason. Its answer comes in two
steps, because the second can be slow and the asker is on a deadline
(jobs.describe_pdf): the line {"pages": n} the moment the PDF is open, and
last {"ok": true, "pages": n, "scanned": m} -- m None when the host cannot read
text and nothing was looked at.

"refused" means the file could not be converted and saying so is the bot
working; "crash" means the bot failed. jobs.py refunds both and tells the operator only about the second.

_At `PROGRESS_EVERY_S = 1.0`:_

main.py runs this (`--module convert_bot.convert_worker`) with the bot's modules on hand,
so it needs no path of its own.

### `_wall_in()`

From here on the worker -- and everything it starts -- may change only its job's
folders, has no network and can't reach the bots (sandbox.py). Its scratch files go in a
folder of its own inside the job's folder: named like the job's other folders, so the
upload sweeper takes it if the worker is killed before it removes it.

### `_cap_memory()`

Give the worker a MemoryError instead of letting the kernel pick a
victim. POSIX only, and only when asked: the OOM killer on a container
chooses the largest process, which is normally this one, but "normally"
is not the guarantee a shared container deserves.

### `_describe_raw()`

Read a camera raw photo's size without decoding it (raw_convert.inspect)
and say it as JSON. Anything that goes wrong is reported as one of the
protocol's failures and the parent treats every one of them as "unreadable".

- `except convert_utils.ConversionError as exc:` — the ordinary "that is not a raw photo"

### `_describe_image()`

A picture's width and height from Pillow's header read, as JSON
(jobs.describe_image). Pillow's bomb ceiling is off: deciding it is what
the size is asked for. Here, in a process of its own, because some of
Pillow's "header reads" decode (see jobs.describe_image).

### `_describe_pdf()`

A PDF's page count and how many pages are pictures without text
(ocr_convert.count_scanned), as JSON (jobs.describe_pdf). MuPDF reading a
stranger's file happens here, in a process of its own. Anything that goes
wrong is one of the protocol's failures and the parent reads every one of
them as "the menu without the numbers".

The page count is written and flushed the moment it is known, on a line of
its own, and the pages are looked at after: the parent waits only a few
seconds, and a page of a million glyphs takes longer than that to look at.
Pages are looked at only where text can be read at all, and not past the
most one conversion reads.

- `except convert_utils.ConversionError as exc:` — the ordinary "that is not a readable PDF"

### `main()`

- At `warnings.simplefilter("ignore")`:
  The size policy is decided by the parent, before anything is charged.
  A DecompressionBombWarning here would only be noise on stderr.
- At `import time`:
  How far it has got, for the status message the person is watching: one
  JSON line at most every PROGRESS_EVERY_S, never the last line -- the
  outcome always comes after it.
