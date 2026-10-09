"""Archives: ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ and 7z -- and RAR, which is read
and never written.

The case this serves is a phone that cannot open the `.7z` or `.rar` somebody
sent: it comes back as a ZIP. The others are the same machinery pointed the
other way. (From `PLANNED.md`, "Archives": the owner, 2026-09-28, "Elaborate on
archives ... Will do them next patch.")

## What makes an archive dangerous, and what stops it

The size of what comes out of an archive is unbounded by the size of what went
in -- a 45 KB ZIP can expand to petabytes -- and every name inside it was
chosen by a stranger. So nothing is ever written that has not been counted,
and everything is written under a work directory of its own:

  * an entry count (MAX_ENTRIES), a total of expanded bytes (MAX_EXPANDED_MB)
    and, for a ZIP, a ratio per entry (MAX_RATIO) -- each checked from the
    archive's own index where it has one, and again on the bytes actually
    written, because an index is only a claim;
  * every name goes through _safe_name: absolute paths, `..`, drive letters,
    control characters and, on a Windows host only, device names refuse the
    WHOLE archive. An entry like that is not a mistake somebody made, and
    dropping it quietly would hand back an archive that is not the one that
    was sent. (`aux.c` and `con.txt` are ordinary names on Linux, which is
    where this runs; _WINDOWS_NAMES is the switch.) A file and a folder with
    one name, names that are not text, and a tar header the size of a file
    are refused the same way, with a sentence, not a crash;
  * links, devices and fifos are skipped and never followed (a symlink to
    `/etc/passwd` is data nobody asked for, and in the archive that goes back
    to somebody it would be a way to write outside their folder);
  * an encrypted archive is refused with a sentence and nothing is charged: a
    password would be personal data this bot has no business holding;
  * an archive inside an archive is not opened. It is an ordinary file and
    goes through as bytes;
  * the expanded files live in `<work_dir>/<stem>-contents`, which is removed
    whether the conversion worked or not. A job that is KILLED (timeout, Stop,
    out of memory) never reaches the `finally` that does it, so jobs._finish
    removes that folder and `<stem>-source.*` again when the job ends, and
    the bot's sweeper removes any folder left in the upload directory past the
    age a file is allowed.

Reading ZIP and TAR is the standard library's (zipfile, tarfile: nothing to
install, and their handling of the format is the best-reviewed there is). 7z and
RAR are libarchive's, through `bsdtar` -- one small system package, no Python
dependency with native code -- which is also what writes a 7z. Without bsdtar
`formats.probe()` leaves 7z and RAR out of the menu.

Runs in ConvertBot's worker process, which has the memory cap and the deadline
(`convert_worker.py`); bsdtar inherits both.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path

import formats
from convert_utils import ConversionError, Result, remove_tree, report
from formats import bsdtar_path

# The formats this module writes, declared so that tests/test_conversions.py
# can check the matrix in formats.py against what is implemented.
WRITES = frozenset({"zip", "tar", "tgz", "tbz2", "txz", "7z"})

TAR_READ_MODES = {"tar": "r:", "tgz": "r:gz", "tbz2": "r:bz2", "txz": "r:xz"}
TAR_WRITE_MODES = {"tar": "w:", "tgz": "w:gz", "tbz2": "w:bz2", "txz": "w:xz"}

MAX_ENTRIES = int(os.environ.get("CONVERT_MAX_ARCHIVE_ENTRIES") or 10_000)
# 300, not 1000 (review, 2026-10-07): the container's whole disk is 1 GB, and next
# to the unpacked files sit the upload, the padded copy and the archive being
# written -- while the free-space check is blind, the container reporting the
# host's 2 TB.
MAX_EXPANDED_MB = int(os.environ.get("CONVERT_MAX_ARCHIVE_EXPANDED_MB") or 300)
# No single ZIP entry may expand to more than this many times what it takes in
# the archive. The classic bomb is far past it; ordinary text is about 10 and
# a file of zeros about 1000, which is why anything over 1 MB is held to it.
MAX_RATIO = int(os.environ.get("CONVERT_MAX_ARCHIVE_RATIO") or 200)
MAX_NAME_BYTES = 1024
MAX_PART_BYTES = 255
# The most a tar header may carry: a GNU long name or link, a pax record list.
# tarfile reads such a header's body whole into memory, and the size is the
# header's own claim -- a 611 KB .tgz that said "the next 700 MB is a long name"
# peaked at 718 MB (review, 2026-10-07). A real name is under 4 KB.
MAX_HEADER_BYTES = 1024 * 1024
BSDTAR_TIMEOUT_S = 900.0
# How much of bsdtar's stderr is ever looked at: the end of it, where the
# reason it gave up is.
_STDERR_TAIL = 64 * 1024
# Windows device names (CON, NUL, AUX ...) are devices only there. On Linux,
# where this runs, `aux.c` and `con.txt` are ordinary files that people have
# (a C source called aux.c is the usual one); refusing them refused real
# archives. A flag rather than a check of os.name in place so a test can
# try both.
_WINDOWS_NAMES = os.name == "nt"
# How often the size of what bsdtar has written is looked at while it runs, and
# how many times that look has had to stop it (read by the tests).
WATCH_INTERVAL_S = 0.25
_stats = {"watcher_killed": 0}

_MB = 1024 * 1024
_CHUNK = 1024 * 256
_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                           *(f"lpt{i}" for i in range(1, 10))})
_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_NOT_FILES = frozenset({stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK})
_PASSWORD_WORDS = ("passphrase", "encrypted", "password", "incorrect pass")


def _limit_bytes() -> int:
    return MAX_EXPANDED_MB * _MB


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def _safe_name(raw: str) -> "str | None":
    """The entry's path as a clean relative POSIX path, or None for an entry
    that is just the folder itself ("./", "."). Raises for anything that points
    outside, because that is not a thing to repair."""
    if raw is None or _CONTROL.search(raw):
        raise ConversionError("That archive has a file name with control characters in it, "
                              "so it was not opened.")
    name = raw.replace("\\", "/")
    if name.startswith("/") or _DRIVE.match(name):
        raise ConversionError("That archive has a file whose path starts at the root of a disk "
                              "instead of inside the archive, so it was not opened.")
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ConversionError("That archive has a file whose path climbs out of the archive "
                                  "(a `..`), so it was not opened.")
        if len(part.encode("utf-8", "replace")) > MAX_PART_BYTES:
            raise ConversionError("That archive has a file name longer than a file system takes, "
                                  "so it was not opened.")
        if _WINDOWS_NAMES and part.split(".")[0].lower() in _DEVICE_NAMES:
            raise ConversionError("That archive has a file named like a device (CON, NUL, ...), "
                                  "so it was not opened.")
        parts.append(part)
    if not parts:
        return None
    clean = "/".join(parts)
    if len(clean.encode("utf-8", "replace")) > MAX_NAME_BYTES:
        raise ConversionError("That archive has a path longer than a file system takes, "
                              "so it was not opened.")
    return clean


def _under(root: Path, relative: str) -> Path:
    """root/relative, and proof it is still inside root once symlinks are
    resolved -- a belt for the braces above."""
    target = (root / relative)
    resolved = target.resolve()
    if os.path.commonpath([str(root.resolve()), str(resolved)]) != str(root.resolve()):
        raise ConversionError("That archive would write outside its own folder, so it was not opened.")
    return target


class _Budget:
    """What may still be written. Counted on the bytes that are written, not on
    what an index says they will be."""

    def __init__(self):
        self.entries = 0
        self.files = 0
        self.bytes = 0
        self.skipped = 0

    def entry(self) -> None:
        self.entries += 1
        if self.entries > MAX_ENTRIES:
            raise ConversionError(f"That archive has more than {MAX_ENTRIES:,} entries, "
                                  "which is more than one conversion takes.")

    def add(self, count: int) -> None:
        self.bytes += count
        if self.bytes > _limit_bytes():
            raise ConversionError(f"That archive expands to more than {MAX_EXPANDED_MB:,} MB, "
                                  "which is more than one conversion takes.")


_CLASH = "That archive has a file and a folder with the same name, so it was not opened."


def _make_folder(path: Path) -> None:
    """mkdir -p, where a FILE already in the way is a refusal, not a crash.

    An archive is a list, not a tree, so nothing stops it listing `a` (a file)
    and then `a/` (a folder), or `a` and then `a/b`. Whichever came first is on
    the disk by the time the other asks for it."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError):
        raise ConversionError(_CLASH) from None


def _copy_capped(source, destination: Path, budget: _Budget) -> None:
    _make_folder(destination.parent)
    if destination.is_dir():                      # `a/` came first, and now there is a file `a`
        raise ConversionError(_CLASH)
    with open(destination, "wb") as out:
        while True:
            chunk = source.read(_CHUNK)
            if not chunk:
                break
            budget.add(len(chunk))
            out.write(chunk)
    budget.files += 1


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _extract_zip(in_path: str, root: Path, budget: _Budget) -> None:
    try:
        archive = zipfile.ZipFile(in_path)
    except UnicodeDecodeError:
        # Names flagged as UTF-8 (bit 0x800) that are not: zipfile decodes every
        # name while it reads the directory, so it fails here, before a single
        # entry can be looked at.
        raise ConversionError("That archive's file names aren't readable text, so it was not opened.") from None
    except (zipfile.BadZipFile, OSError, NotImplementedError, EOFError) as exc:
        raise ConversionError(f"Couldn't read that ZIP: {exc}") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ConversionError(f"That archive has {len(infos):,} entries, over the "
                                  f"{MAX_ENTRIES:,} one conversion takes.")
        declared = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise ConversionError("That ZIP is password-protected. Nothing was charged -- "
                                      "this bot never asks for a password.")
            declared += info.file_size
            if info.file_size > _MB and (info.compress_size == 0
                                         or info.file_size / max(1, info.compress_size) > MAX_RATIO):
                raise ConversionError(f"That ZIP has a file that expands more than {MAX_RATIO}-fold, "
                                      "which is how a zip bomb is built, so it was not opened.")
        if declared > _limit_bytes():
            raise ConversionError(f"That archive expands to {declared // _MB:,} MB, over the "
                                  f"{MAX_EXPANDED_MB:,} MB one conversion takes.")
        for info in infos:
            budget.entry()
            name = _safe_name(info.filename)
            if name is None:
                continue
            # The high half of external_attr is a Unix st_mode when the ZIP was made
            # on Unix, and its TYPE bits say what the entry is. Python's own
            # writestr() leaves them zero -- permissions only -- which is a plain
            # file, so "no type" is a file and only the kinds that are not one are
            # skipped.
            if stat.S_IFMT(info.external_attr >> 16) in _NOT_FILES:
                budget.skipped += 1
                continue
            if info.is_dir():
                _make_folder(_under(root, name))
                continue
            try:
                with archive.open(info) as source:
                    _copy_capped(source, _under(root, name), budget)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, ValueError) as exc:
                raise ConversionError(f"Couldn't read '{name}' inside that ZIP: {exc}") from exc


_HEADER_TOO_BIG = "a header in the archive is larger than the limit"


class _HeaderReads:
    """A tar file's byte stream for the length of one header, handing over no
    more than MAX_HEADER_BYTES in all.

    The headers that carry a body (a GNU long name, a pax record list, the map
    of a sparse file) are read by tarfile with `fileobj.read(<size the header
    claims>)`, or in a loop until the header's own count is met. This is what
    bounds those reads, whatever the header says and whichever version of
    tarfile does the reading: the limit is checked BEFORE the read, so nothing
    is allocated for a claim that is refused."""

    def __init__(self, fileobj):
        self._fileobj = fileobj
        self._left = MAX_HEADER_BYTES

    def read(self, size=-1):
        if size is None or size < 0 or size > self._left:
            raise tarfile.HeaderError(_HEADER_TOO_BIG)
        self._left -= size
        return self._fileobj.read(size)

    def __getattr__(self, name):                  # tell, seek, ...
        return getattr(self._fileobj, name)


class _CappedTarInfo(tarfile.TarInfo):
    """tarfile's header reader, refusing a header larger than any name needs.

    The three methods that read a body whole are the ones overridden; the
    names are tarfile's own, the same in 3.11 and 3.12 (their arguments are
    passed through untouched, so a patch release that changes them does not
    matter). A header whose own size is over the limit is refused at once; any
    header is then read through _HeaderReads, which also bounds the loops that
    size does not announce (sparse maps). A chain of headers shares one
    allowance, so a stream of them is no way round it."""

    def _within_allowance(self, parent, tar, check_size):
        if check_size and self.size > MAX_HEADER_BYTES:
            raise tarfile.HeaderError(_HEADER_TOO_BIG)
        if isinstance(tar.fileobj, _HeaderReads):  # already inside a header: the allowance is running
            return parent(tar)
        real = tar.fileobj
        tar.fileobj = _HeaderReads(real)
        try:
            return parent(tar)
        finally:
            tar.fileobj = real

    def _proc_gnulong(self, tar):
        return self._within_allowance(super()._proc_gnulong, tar, True)

    def _proc_pax(self, tar):
        return self._within_allowance(super()._proc_pax, tar, True)

    def _proc_sparse(self, tar):
        # The body of a sparse member is data and may be any size; what is
        # read here is only the chain of extra header blocks before it.
        return self._within_allowance(super()._proc_sparse, tar, False)


def _tar_refusal(exc: Exception) -> ConversionError:
    if _HEADER_TOO_BIG in str(exc):
        # tarfile re-wraps an error from a header that follows another header
        # (SubsequentHeaderError, ReadError), so the kind is told by the message.
        return ConversionError("That archive has a header far larger than any file name, "
                               "so it was not opened.")
    return ConversionError(f"Couldn't read that archive: {exc}")


def _extract_tar(in_path: str, src_ext: str, root: Path, budget: _Budget) -> None:
    try:
        archive = tarfile.open(in_path, mode=TAR_READ_MODES[src_ext], tarinfo=_CappedTarInfo)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise _tar_refusal(exc) from exc
    except MemoryError:
        raise                                     # the machine is too small for it: not a damaged file
    except Exception as exc:                      # lzma.LZMAError, zlib.error, ...
        raise ConversionError(f"Couldn't read that archive: {type(exc).__name__}") from exc
    try:
        with archive:
            for member in archive:
                budget.entry()
                name = _safe_name(member.name)
                if name is None:
                    continue
                if member.isdir():
                    _make_folder(_under(root, name))
                    continue
                if not member.isreg():
                    budget.skipped += 1           # links, devices, fifos: never followed, never carried
                    continue
                source = archive.extractfile(member)
                if source is None:
                    budget.skipped += 1
                    continue
                with source:
                    _copy_capped(source, _under(root, name), budget)
    except ConversionError:
        raise
    except (tarfile.TarError, EOFError, OSError, ValueError) as exc:
        raise _tar_refusal(exc) from exc
    except MemoryError:
        raise
    except Exception as exc:                      # lzma.LZMAError, zlib.error: a damaged stream
        raise ConversionError(f"That archive is damaged ({type(exc).__name__}).") from exc


# libarchive decides what a file is by asking every format it knows how to
# read, and the ZIP reader is willing to take any file with a ZIP's
# end-of-directory marker in its last 16 KB, however it begins. A RAR or 7z
# that holds a ZIP as its last stored file -- which RAR does all the time,
# because a ZIP does not compress -- is then read AS that ZIP: no error, a clean
# listing of the inner archive's files, and everything else silently gone.
# Measured 2026-10-07 on a RAR5 file built for the purpose. Two things stop it:
# the file's own signature must be the one its extension claims, and bsdtar is
# given a copy with more than 16 KB of padding after the archive's end, which
# every reader of these formats ignores and the ZIP reader cannot see past.
_MAGIC = {"rar": b"Rar!\x1a\x07", "7z": b"7z\xbc\xaf\x27\x1c"}
_PADDING = 20 * 1024


def _padded_copy(in_path: str, src_ext: str, work_dir: Path, stem: str) -> Path:
    with open(in_path, "rb") as source:
        if not source.read(8).startswith(_MAGIC[src_ext]):
            raise ConversionError(f"That file is not a {src_ext.upper()} archive, whatever it is called.")
    copy = work_dir / f"{stem}-source.{src_ext}"
    shutil.copyfile(in_path, copy)
    with open(copy, "ab") as out:
        out.write(b"\0" * _PADDING)
    return copy


def _walk_failed(exc: OSError):
    """os.walk's onerror, for every walk over what an archive unpacked.

    os.walk skips a folder it cannot list and says nothing, so a folder the
    archive left unreadable made every file in it vanish from the walk: the
    archive came back SMALLER, with no error. A hole is a refusal."""
    raise ConversionError("Some of that archive's files couldn't be read, so it was not opened.") from exc


def _tree_size(root: Path, strict: bool = False) -> int:
    """Bytes under root. The watcher asks while bsdtar is still writing, when
    a folder or a file coming and going is normal and a miss only means a later
    look; the check after it is strict."""
    total = 0
    for current, _dirs, files in os.walk(root, onerror=_walk_failed if strict else None):
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError as exc:
                if strict:
                    _walk_failed(exc)
    return total


def _opened_mode(mode: int, is_dir: bool) -> int:
    """A mode with the owner able to read and write (and, for a folder, to
    enter and list), the setuid, setgid and sticky bits gone, and everything
    else as it was."""
    return (mode | (0o700 if is_dir else 0o600)) & 0o777


def _open_up(root: Path) -> None:
    """Make everything bsdtar unpacked readable and writable by us, top down.

    libarchive applies the modes a 7z or RAR carries (minus the umask), and
    the archive is a stranger's: a file with mode 000 cannot be read, a folder
    with 0500 cannot have its files removed afterwards (so the work folder
    could not be), and a folder with 000 cannot be listed, so os.walk would
    skip it. Each folder is opened BEFORE it is listed -- os.walk lists a folder
    after it has handed over its parent, which is when this runs. Links are
    never followed or chmod-ed (they are removed next); devices and fifos are
    left for the same step."""
    try:
        os.chmod(root, _opened_mode(os.lstat(root).st_mode, True))
        for current, dirs, files in os.walk(root, onerror=_walk_failed):
            for name in dirs + files:
                path = os.path.join(current, name)
                mode = os.lstat(path).st_mode
                if stat.S_ISDIR(mode):
                    os.chmod(path, _opened_mode(mode, True))
                elif stat.S_ISREG(mode):
                    os.chmod(path, _opened_mode(mode, False))
    except OSError as exc:
        _walk_failed(exc)


def _tail(handle, limit: int = _STDERR_TAIL) -> str:
    """The last `limit` bytes of a file bsdtar wrote its complaints to."""
    try:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - limit))
        return handle.read(limit).decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def _too_big() -> ConversionError:
    return ConversionError(f"That archive expands to more than {MAX_EXPANDED_MB:,} MB, "
                           "which is more than one conversion takes.")


def _extract_with_bsdtar(in_path: str, root: Path, budget: _Budget) -> None:
    tar = bsdtar_path()
    if tar is None:
        raise ConversionError("7z and RAR archives aren't supported on this host.")
    # 1. The names, which are the index: counted and checked before anything is
    #    written, and read no further than the limit allows. bsdtar's stderr
    #    goes to a file, not a pipe: this loop reads stdout to the end before
    #    it looks at stderr, and a pipe nobody is draining fills at 64 KB and
    #    stops bsdtar until the timeout.
    with tempfile.TemporaryFile() as listing_errors:
        listing = subprocess.Popen([tar, "-tf", in_path], stdout=subprocess.PIPE, stderr=listing_errors)
        names = []
        try:
            for line in listing.stdout:
                names.append(line.decode("utf-8", "replace").rstrip("\r\n"))
                if len(names) > MAX_ENTRIES:
                    listing.kill()
                    raise ConversionError(f"That archive has more than {MAX_ENTRIES:,} entries, "
                                          "which is more than one conversion takes.")
            listing.wait(timeout=60)
        except subprocess.TimeoutExpired:
            listing.kill()
            raise ConversionError("Reading that archive's index took too long, so it was not opened.") from None
        finally:
            if listing.poll() is None:
                listing.kill()
            listing.wait()              # so it has let go of the file before anyone deletes it
            listing.stdout.close()
        errors = _tail(listing_errors)
    if listing.returncode not in (0, None) and not names:
        _refuse_bsdtar(errors)
    for name in names:
        _safe_name(name)

    # 2. The extraction, with a watcher that stops it at the limit: bsdtar will
    #    write whatever the archive says, and the archive is a stranger's.
    #    -p, not --no-same-permissions: without it libarchive gives a folder its
    #    mode the moment it makes it, so a folder the archive marks 000 or 0500
    #    is closed before its own files are written into it, and bsdtar fails on
    #    every one of them ("Error exit delayed from previous errors"; first seen
    #    on Linux CI, 2026-10-10 -- Windows has no modes). With -p it gives
    #    folders their modes only at the end, after everything is in them, and
    #    _open_up then opens the whole tree again (setuid cleared). ACLs, extended
    #    attributes and file flags are a stranger's too, and not wanted at all.
    with tempfile.TemporaryFile() as extract_errors:
        proc = subprocess.Popen(
            [tar, "-xpf", in_path, "-C", str(root), "--no-same-owner",
             "--no-acls", "--no-xattrs", "--no-fflags"],
            stdout=subprocess.DEVNULL, stderr=extract_errors)
        over = threading.Event()
        done = threading.Event()

        def watch():
            while not done.wait(WATCH_INTERVAL_S):
                if _tree_size(root) > _limit_bytes():
                    over.set()
                    _stats["watcher_killed"] += 1
                    proc.kill()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            proc.wait(timeout=BSDTAR_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise ConversionError("Opening that archive took too long, so it was stopped.") from None
        finally:
            done.set()
            watcher.join(2)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        err = _tail(extract_errors)
    if over.is_set():
        raise _too_big()
    if proc.returncode != 0:
        if _tree_size(root) > _limit_bytes():
            raise _too_big()
        if proc.returncode < 0:
            raise MemoryError("the archive reader was stopped by the system")
        _refuse_bsdtar(err)

    # The modes the archive carried are applied now, and a 000 folder hides
    # what is in it from every walk that follows -- the size check included.
    _open_up(root)
    # And so is the date the archive gave its `./`, which can be 1970: the
    # folder is this job's, and it is new (the sweeper also knows the job's
    # name, see bot._sweep_uploads; this is so the folder never LOOKS old).
    os.utime(root)
    if _tree_size(root, strict=True) > _limit_bytes():
        raise _too_big()
    _drop_non_files(root, budget)
    _count_files(root, budget)


def _count_files(root: Path, budget: _Budget) -> None:
    """What bsdtar wrote, counted against the same budget as the other readers."""
    for current, _dirs, files in os.walk(root, onerror=_walk_failed):
        for name in files:
            budget.entry()
            budget.add(os.lstat(os.path.join(current, name)).st_size)
            budget.files += 1


def _drop_non_files(root: Path, budget: _Budget) -> None:
    """Whatever bsdtar made that is not a plain file or a folder goes: links
    (which it creates as links), devices, fifos. Never followed, never carried."""
    for current, dirs, files in os.walk(root, followlinks=False, onerror=_walk_failed):
        for name in list(dirs) + list(files):
            path = os.path.join(current, name)
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                os.unlink(path)
                budget.skipped += 1
                if name in dirs:
                    dirs.remove(name)


def _refuse_bsdtar(message: str) -> None:
    lowered = message.lower()
    if any(word in lowered for word in _PASSWORD_WORDS):
        raise ConversionError("That archive is password-protected. Nothing was charged -- "
                              "this bot never asks for a password.")
    raise ConversionError("Couldn't read that archive: " + (message.strip().splitlines() or ["unknown error"])[-1][:200])


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _entries(root: Path):
    """(relative path, absolute path, is a directory), directories first within
    each level, in a stable order -- so the same input is the same output. A
    folder that cannot be listed is a refusal, not a gap (_walk_failed)."""
    for current, dirs, files in os.walk(root, onerror=_walk_failed):
        dirs.sort()
        here = Path(current)
        for name in sorted(files):
            yield (here / name).relative_to(root).as_posix(), here / name, False
        if not dirs and not files and here != root:
            yield here.relative_to(root).as_posix(), here, True


def _require_text_names(entries, label: str) -> None:
    """A ZIP or a 7z stores names as text, so a name that is not text cannot go
    into one. A TAR cannot hold only text: it holds the bytes of a name, and
    the surrogate-escaped name tarfile gives a non-UTF-8 name (a cp1251 or
    Shift-JIS archive from an old machine) goes back out as the bytes it came in
    as. Found by sending one: UnicodeEncodeError out of zipfile, a crash that
    refunded and paged the owner for what is a file the bot does not take."""
    for name, _path, _is_dir in entries:
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            raise ConversionError("That archive's file names aren't readable text, so it can't be "
                                  f"made into a {label}.") from None


def _write_zip(root: Path, out_path: Path) -> int:
    count = 0
    entries = list(_entries(root))
    _require_text_names(entries, "ZIP")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False) as archive:
        for index, (name, path, is_dir) in enumerate(entries):
            report(index, len(entries))
            if is_dir:
                archive.writestr(name + "/", b"")
            else:
                archive.write(path, arcname=name)
                count += 1
    report(len(entries), len(entries))
    return count


def _normalised(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Nobody's user id, nobody's name, and permissions that mean the same on
    any machine: what was on the sender's disk is not the recipient's business."""
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def _write_tar(root: Path, target_ext: str, out_path: Path) -> int:
    count = 0
    entries = list(_entries(root))
    with tarfile.open(out_path, mode=TAR_WRITE_MODES[target_ext]) as archive:
        for index, (name, path, is_dir) in enumerate(entries):
            report(index, len(entries))
            archive.add(path, arcname=name, recursive=False, filter=_normalised)
            count += 0 if is_dir else 1
    report(len(entries), len(entries))
    return count


def _write_7z(root: Path, out_path: Path) -> int:
    tar = bsdtar_path()
    if tar is None:
        raise ConversionError("7z archives can't be made on this host.")
    entries = list(_entries(root))
    _require_text_names(entries, "7Z")
    count = sum(1 for _, _, is_dir in entries if not is_dir)
    try:
        proc = subprocess.run(
            [tar, "--format", "7zip", "--options", "7zip:compression=lzma2", "-cf", str(out_path), "-C", str(root), "."],
            capture_output=True, timeout=BSDTAR_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ConversionError("Making that 7z took too long, so it was stopped.") from None
    if proc.returncode != 0 or not out_path.exists():
        out_path.unlink(missing_ok=True)
        detail = (proc.stderr.decode("utf-8", "replace").strip().splitlines() or [""])[-1][:160]
        # A writer that dies without a word (seen on Windows' own bsdtar with
        # names outside ASCII) is not something to dress up as a damaged file.
        raise ConversionError("Couldn't make a 7z out of this one" + (f" ({detail})" if detail else "")
                              + ". A ZIP or TAR.XZ will work.")
    return count


# ---------------------------------------------------------------------------
# The conversion
# ---------------------------------------------------------------------------

def convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str = "converted") -> Result:
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    if target_ext not in WRITES:
        raise ConversionError(f"Nothing here makes a .{target_ext} archive.")
    work_dir = Path(work_dir)
    out_path = work_dir / f"{stem}.{target_ext}"
    root = work_dir / f"{stem}-contents"
    if root.exists():
        remove_tree(root)
    root.mkdir(parents=True)
    budget = _Budget()
    try:
        if src_ext == "zip":
            _extract_zip(str(in_path), root, budget)
        elif src_ext in TAR_READ_MODES:
            _extract_tar(str(in_path), src_ext, root, budget)
        elif src_ext in ("7z", "rar"):
            padded = _padded_copy(str(in_path), src_ext, work_dir, stem)
            try:
                _extract_with_bsdtar(str(padded), root, budget)
            finally:
                padded.unlink(missing_ok=True)
        else:
            raise ConversionError(f"Nothing here opens a .{src_ext} archive.")
        if budget.files == 0:
            raise ConversionError("That archive has no files in it." + (
                " Links, devices and other things that are not plain files are never carried over."
                if budget.skipped else ""))
        if target_ext == "zip":
            files = _write_zip(root, out_path)
        elif target_ext in TAR_WRITE_MODES:
            files = _write_tar(root, target_ext, out_path)
        else:
            files = _write_7z(root, out_path)
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        remove_tree(root)
    return Result(out_path, items=max(1, files))
