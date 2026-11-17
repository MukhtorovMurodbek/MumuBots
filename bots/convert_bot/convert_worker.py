import json
import os
import sys
import traceback
import warnings


PROGRESS_EVERY_S = 1.0


def _emit(ok: bool, **fields) -> None:
    fields["ok"] = ok
    sys.stdout.write("\n" + json.dumps(fields) + "\n")
    sys.stdout.flush()


def _wall_in(job: dict) -> "list[str]":
    try:
        import sandbox
    except ImportError:
        return []
    if not sandbox.enabled():
        return []
    folders = set()
    for place in (job.get("work_dir"), job.get("path"), *(job.get("in_paths") or ())):
        if place:
            folders.add(place if os.path.isdir(place) else os.path.dirname(os.path.abspath(place)))
    base = job.get("work_dir") or (os.path.dirname(os.path.abspath(job["path"])) if job.get("path") else None)
    if base and os.path.isdir(base):
        import atexit
        import shutil
        import tempfile
        scratch = tempfile.mkdtemp(prefix=f"{job.get('stem') or 'describe'}-", suffix="-contents", dir=base)
        tempfile.tempdir = scratch
        os.environ["TMPDIR"] = scratch
        atexit.register(shutil.rmtree, scratch, True)
    return sandbox.restrict(sorted(folders), read=sandbox.CODE, sockets=["/tmp"])


def _cap_memory(max_memory_mb: int) -> None:
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
    except convert_utils.ConversionError as exc:
        _emit(False, kind="refused", message=str(exc))
        return 2
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _emit(False, kind="crash", message=f"{type(exc).__name__}: {exc}")
        return 1
    _emit(True, info=info._asdict())
    return 0


def _describe_image(job: dict) -> int:
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
    except convert_utils.ConversionError as exc:
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
    _wall_in(job)
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
