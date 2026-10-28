"""One conversion, in a process of its own.

ConvertBot used to run convert() on a thread inside the bot's own process.
Three things go wrong with that, and a 200-megapixel photo found all of them
at once:

  it cannot be stopped. Python has no way to kill a thread, so a time limit on
  a threaded conversion only stops *waiting* for it -- the work goes on,
  holding a core and a gigabyte, after everybody has given up on it;
  it does not give memory back. Decoding a photo that size takes the process
  past a gigabyte, and CPython's allocator returns little of that to the
  operating system afterwards, so the bot idles fat until it is restarted --
  on a host that bills resident memory by the second;
  it is not isolated. A crash in a C decoder, or the kernel's OOM killer
  choosing a victim, takes the whole bot down with everybody's pending work.

A child process fixes all three for the price of starting one. jobs.py gives
it a deadline and kills it when the deadline passes; the operating system
takes the memory back the moment it exits; and if it dies, it dies alone.

The protocol is deliberately plain. The job arrives as one JSON argument and
the outcome leaves as one JSON line -- the last non-empty line on stdout.
Anything a library prints along the way is ignored.

    {"ok": true,  "path": "...", "zipped": false, "items": 1, "bytes": 123}
    {"ok": false, "kind": "refused", "message": "..."}   a ConversionError
    {"ok": false, "kind": "crash",   "message": "..."}   anything else

A second kind of job shares the process and the protocol: {"kind":
"describe_raw", "path": ..., "src_ext": ...} asks for the size of a camera raw
photo, which is LibRaw parsing a stranger's file and so is not done in the
bot's own process. Its answer is {"ok": true, "info": {<RawInfo's fields>}} or
one of the failures above. So does {"kind": "describe_pdf", "path": ...}, which
asks how many pages a PDF has and how many of them are pictures without text
(ocr_convert.count_scanned), for the same reason. Its answer comes in two
steps, because the second can be slow and the asker is on a deadline
(jobs.describe_pdf): the line {"pages": n} the moment the PDF is open, and
last {"ok": true, "pages": n, "scanned": m} -- m None when the host cannot read
text and nothing was looked at.

"refused" means the file could not be converted and saying so is the bot
working; "crash" means the bot failed. jobs.py refunds both and tells the
owner only about the second.
"""
import json
import os
import sys
import traceback
import warnings

# main.py runs this (`--module convert_bot.convert_worker`) with the bot's modules on hand,
# so it needs no path of its own.


PROGRESS_EVERY_S = 1.0


def _emit(ok: bool, **fields) -> None:
    fields["ok"] = ok
    sys.stdout.write("\n" + json.dumps(fields) + "\n")
    sys.stdout.flush()


def _cap_memory(max_memory_mb: int) -> None:
    """Give the worker a MemoryError instead of letting the kernel pick a
    victim. POSIX only, and only when asked: the OOM killer on a container
    chooses the largest process, which is normally this one, but "normally"
    is not the guarantee a shared container deserves."""
    if not max_memory_mb or os.name != "posix":
        return
    try:
        import resource
        limit = int(max_memory_mb) * 1024 * 1024
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except Exception:
        pass


def _describe_raw(job: dict) -> int:
    """Read a camera raw photo's size without decoding it (raw_convert.inspect)
    and say it as JSON. Anything that goes wrong is reported as one of the
    protocol's failures and the parent treats every one of them as "unreadable"."""
    try:
        import convert_utils
        import raw_convert
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit(False, kind="crash", message=f"could not load the converters: {exc}")
        return 1
    try:
        info = raw_convert.inspect(job["path"], job.get("src_ext") or "dng")
    except MemoryError:
        _emit(False, kind="no_memory", message="reading the raw photo ran out of memory")
        return 2
    except convert_utils.ConversionError as exc:      # the ordinary "that is not a raw photo"
        _emit(False, kind="refused", message=str(exc))
        return 2
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit(False, kind="crash", message=f"{type(exc).__name__}: {exc}")
        return 1
    _emit(True, info=info._asdict())
    return 0


def _describe_image(job: dict) -> int:
    """A picture's width and height from Pillow's header read, as JSON
    (jobs.describe_image). Pillow's bomb ceiling is off: deciding it is what
    the size is asked for. Here, in a process of its own, because some of
    Pillow's "header reads" decode (see jobs.describe_image)."""
    try:
        import convert_utils
        from PIL import Image
        convert_utils.register_openers()
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(job["path"]) as img:
            width, height = img.size
    except MemoryError:
        _emit(False, kind="no_memory", message="reading the picture's size ran out of memory")
        return 2
    except Exception as exc:
        _emit(False, kind="refused", message=type(exc).__name__)
        return 2
    _emit(True, width=int(width), height=int(height))
    return 0


def _describe_pdf(job: dict) -> int:
    """A PDF's page count and how many pages are pictures without text
    (ocr_convert.count_scanned), as JSON (jobs.describe_pdf). MuPDF reading a
    stranger's file happens here, in a process of its own. Anything that goes
    wrong is one of the protocol's failures and the parent reads every one of
    them as "the menu without the numbers".

    The page count is written and flushed the moment it is known, on a line of
    its own, and the pages are looked at after: the parent waits only a few
    seconds, and a page of a million glyphs takes longer than that to look at.
    Pages are looked at only where text can be read at all, and not past the
    most one conversion reads."""
    try:
        import convert_utils
        import ocr_convert
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit(False, kind="crash", message=f"could not load the converters: {exc}")
        return 1

    def say_pages(pages: int) -> None:
        sys.stdout.write(json.dumps({"pages": int(pages)}) + "\n")
        sys.stdout.flush()

    try:
        counted = ocr_convert.count_scanned(job["path"], on_pages=say_pages, scan=ocr_convert.available(),
                                            stop_after=ocr_convert.OCR_MAX_PAGES)
    except MemoryError:
        _emit(False, kind="no_memory", message="reading the PDF ran out of memory")
        return 2
    except convert_utils.OutOfMemory as exc:
        _emit(False, kind="no_memory", message=str(exc))
        return 2
    except convert_utils.ConversionError as exc:      # the ordinary "that is not a readable PDF"
        _emit(False, kind="refused", message=str(exc))
        return 2
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit(False, kind="crash", message=f"{type(exc).__name__}: {exc}")
        return 1
    scanned = counted["scanned"]
    _emit(True, pages=int(counted["pages"]), scanned=None if scanned is None else int(scanned))
    return 0


def main(argv) -> int:
    if len(argv) < 2:
        _emit(False, kind="crash", message="no job was given to the worker")
        return 1
    try:
        job = json.loads(argv[1])
    except ValueError as exc:
        _emit(False, kind="crash", message=f"the job was not valid JSON: {exc}")
        return 1

    _cap_memory(job.get("max_memory_mb") or 0)
    # The size policy is decided by the parent, before anything is charged.
    # A DecompressionBombWarning here would only be noise on stderr.
    warnings.simplefilter("ignore")

    if job.get("kind") == "describe_raw":
        return _describe_raw(job)
    if job.get("kind") == "describe_image":
        return _describe_image(job)
    if job.get("kind") == "describe_pdf":
        return _describe_pdf(job)

    try:
        import convert_utils
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit(False, kind="crash", message=f"could not load the converters: {exc}")
        return 1

    # How far it has got, for the status message the person is watching: one
    # JSON line at most every PROGRESS_EVERY_S, never the last line -- the
    # outcome always comes after it.
    import time
    last = [0.0]

    def progress(done, total):
        now = time.monotonic()
        if now - last[0] >= PROGRESS_EVERY_S or done >= total:
            last[0] = now
            sys.stdout.write(json.dumps({"progress": [round(float(done), 3), round(float(total), 3)]}) + "\n")
            sys.stdout.flush()

    convert_utils.PROGRESS = progress

    try:
        from PIL import Image
        if job.get("max_pixels"):
            Image.MAX_IMAGE_PIXELS = int(job["max_pixels"])
    except Exception:
        pass

    try:
        result = convert_utils.convert(
            job["in_paths"], job["src_ext"], job["target_ext"],
            job["work_dir"], job.get("stem") or "converted",
            ocr_lang=job.get("ocr_lang") or "", ocr_pages=int(job.get("ocr_pages") or 0),
        )
    except convert_utils.OutOfMemory as exc:
        _emit(False, kind="no_memory", message=str(exc))
        return 2
    except convert_utils.ConversionError as exc:
        _emit(False, kind="refused", message=str(exc))
        return 2
    except MemoryError:
        _emit(False, kind="no_memory", message="the conversion ran out of memory (MemoryError)")
        return 2
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit(False, kind="crash", message=f"{type(exc).__name__}: {exc}")
        return 1

    path = str(result.path)
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        _emit(False, kind="crash", message=f"the converter reported a file that is not there: {exc}")
        return 1
    _emit(True, path=path, zipped=bool(getattr(result, "zipped", False)),
          items=int(getattr(result, "items", 1) or 1), bytes=size)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
