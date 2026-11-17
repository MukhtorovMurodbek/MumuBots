# `archive_convert.py`

[↑ convert_bot](README.md) · [code](archive_convert.py)

Archives: ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ and 7z -- and RAR, which is read
and never written.

The case this serves is a phone that cannot open the `.7z` or `.rar` somebody
sent: it comes back as a ZIP. The others are the same machinery pointed the
other way. (From `PLANNED.md`, "Archives", 2026-09-28: archives were to be worked out and done in the next patch.)

\## What makes an archive dangerous, and what stops it

The size of what comes out of an archive is unbounded by the size of what went
in -- a 45 KB ZIP can expand to petabytes -- and every name inside it was
chosen by a stranger. So nothing is ever written that has not been counted,
and everything is written under a work directory of its own:

```text
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
```

Reading ZIP and TAR is the standard library's (zipfile, tarfile: nothing to
install, and their handling of the format is the best-reviewed there is). 7z and
RAR are libarchive's, through `bsdtar` -- one small system package, no Python
dependency with native code -- which is also what writes a 7z. Without bsdtar
`formats.probe()` leaves 7z and RAR out of the menu.

Runs in ConvertBot's worker process, which has the memory cap and the deadline
(`convert_worker.py`); bsdtar inherits both.

_At `WRITES = frozenset({"zip", "tar", "tgz", "tbz2", "txz", "7z"})`:_

The formats this module writes, declared so that tests/test_conversions.py
can check the matrix in formats.py against what is implemented.

_At `def expanded_limit_mb(free_mb: "int | None", slots: int) -> int:`:_

300, not 1000 (review, 2026-10-07): the container's whole disk is 1 GB, and next
to the unpacked files sit the upload, the padded copy and the archive being
written -- while the free-space check is blind, the container reporting the
host's 2 TB.

### `expanded_limit_mb()`

How much one archive may expand to, in MB, when nobody has said: half the free disk
shared among the conversions that may run at once (next to the unpacked files sit the
upload and the archive being written), at least the 300 it always was and at most 4000.
300 when the free space is not known.

### `_default_expanded_mb()`

Read once, when this module is imported (in the conversion worker, so each
conversion reads the disk as it is): the free disk of the folder conversions work
in -- ConvertBot's upload folder -- and the number of conversions at once.

_At `MAX_RATIO = int(os.environ.get("CONVERT_MAX_ARCHIVE_RATIO") or 200)`:_

No single ZIP entry may expand to more than this many times what it takes in
the archive. The classic bomb is far past it; ordinary text is about 10 and
a file of zeros about 1000, which is why anything over 1 MB is held to it.

_At `MAX_HEADER_BYTES = 1024 * 1024`:_

The most a tar header may carry: a GNU long name or link, a pax record list.
tarfile reads such a header's body whole into memory, and the size is the
header's own claim -- a 611 KB .tgz that said "the next 700 MB is a long name"
peaked at 718 MB (review, 2026-10-07). A real name is under 4 KB.

_At `_STDERR_TAIL = 64 * 1024`:_

How much of bsdtar's stderr is ever looked at: the end of it, where the
reason it gave up is.

_At `_WINDOWS_NAMES = os.name == "nt"`:_

Windows device names (CON, NUL, AUX ...) are devices only there. On Linux,
where this runs, `aux.c` and `con.txt` are ordinary files that people have
(a C source called aux.c is the usual one); refusing them refused real
archives. A flag rather than a check of os.name in place so a test can
try both.

_At `WATCH_INTERVAL_S = 0.25`:_

How often the size of what bsdtar has written is looked at while it runs, and
how many times that look has had to stop it (read by the tests).

## Names

### `_safe_name()`

The entry's path as a clean relative POSIX path, or None for an entry
that is just the folder itself ("./", "."). Raises for anything that points
outside, because that is not a thing to repair.

### `_under()`

root/relative, and proof it is still inside root once symlinks are
resolved -- a belt for the braces above.

### `_Budget`

What may still be written. Counted on the bytes that are written, not on
what an index says they will be.

### `_make_folder()`

mkdir -p, where a FILE already in the way is a refusal, not a crash.

An archive is a list, not a tree, so nothing stops it listing `a` (a file)
and then `a/` (a folder), or `a` and then `a/b`. Whichever came first is on
the disk by the time the other asks for it.

### `_copy_capped()`

- `if destination.is_dir():` — `a/` came first, and now there is a file `a`

## Reading

### `_extract_zip()`

- At `raise ConversionError("That archive's file names aren't readable text, so it was not ope …`:
  Names flagged as UTF-8 (bit 0x800) that are not: zipfile decodes every
  name while it reads the directory, so it fails here, before a single
  entry can be looked at.
- At `if stat.S_IFMT(info.external_attr >> 16) in _NOT_FILES:`:
  The high half of external_attr is a Unix st_mode when the ZIP was made
  on Unix, and its TYPE bits say what the entry is. Python's own
  writestr() leaves them zero -- permissions only -- which is a plain
  file, so "no type" is a file and only the kinds that are not one are
  skipped.

### `_HeaderReads`

A tar file's byte stream for the length of one header, handing over no
more than MAX_HEADER_BYTES in all.

The headers that carry a body (a GNU long name, a pax record list, the map
of a sparse file) are read by tarfile with `fileobj.read(&lt;size the header
claims>)`, or in a loop until the header's own count is met. This is what
bounds those reads, whatever the header says and whichever version of
tarfile does the reading: the limit is checked BEFORE the read, so nothing
is allocated for a claim that is refused.

#### `_HeaderReads.__getattr__()`

- `def __getattr__(self, name):` — tell, seek, ...

### `_CappedTarInfo`

tarfile's header reader, refusing a header larger than any name needs.

The three methods that read a body whole are the ones overridden; the
names are tarfile's own, the same in 3.11 and 3.12 (their arguments are
passed through untouched, so a patch release that changes them does not
matter). A header whose own size is over the limit is refused at once; any
header is then read through \_HeaderReads, which also bounds the loops that
size does not announce (sparse maps). A chain of headers shares one
allowance, so a stream of them is no way round it.

#### `_CappedTarInfo._within_allowance()`

- `if isinstance(tar.fileobj, _HeaderReads):` — already inside a header: the allowance is running

#### `_CappedTarInfo._proc_sparse()`

- At `return self._within_allowance(super()._proc_sparse, tar, False)`:
  The body of a sparse member is data and may be any size; what is
  read here is only the chain of extra header blocks before it.

### `_tar_refusal()`

- At `return ConversionError("That archive has a header far larger than any file name, "`:
  tarfile re-wraps an error from a header that follows another header
  (SubsequentHeaderError, ReadError), so the kind is told by the message.

### `_extract_tar()`

- `raise` — the machine is too small for it: not a damaged file
- `except Exception as exc:` — lzma.LZMAError, zlib.error, ...
- `budget.skipped += 1` — links, devices, fifos: never followed, never carried
- `except Exception as exc:` — lzma.LZMAError, zlib.error: a damaged stream

_At `_MAGIC = {"rar": b"Rar!\x1a\x07", "7z": b"7z\xbc\xaf\x27\x1c"}`:_

libarchive decides what a file is by asking every format it knows how to
read, and the ZIP reader is willing to take any file with a ZIP's
end-of-directory marker in its last 16 KB, however it begins. A RAR or 7z
that holds a ZIP as its last stored file -- which RAR does all the time,
because a ZIP does not compress -- is then read AS that ZIP: no error, a clean
listing of the inner archive's files, and everything else silently gone.
Measured 2026-10-07 on a RAR5 file built for the purpose. Two things stop it:
the file's own signature must be the one its extension claims, and bsdtar is
given a copy with more than 16 KB of padding after the archive's end, which
every reader of these formats ignores and the ZIP reader cannot see past.

### `_walk_failed()`

os.walk's onerror, for every walk over what an archive unpacked.

os.walk skips a folder it cannot list and says nothing, so a folder the
archive left unreadable made every file in it vanish from the walk: the
archive came back SMALLER, with no error. A hole is a refusal.

### `_tree_size()`

Bytes under root. The watcher asks while bsdtar is still writing, when
a folder or a file coming and going is normal and a miss only means a later
look; the check after it is strict.

### `_opened_mode()`

A mode with user read and write (and, for a folder, user execute, to enter and list), the setuid, setgid and sticky bits gone, and everything
else as it was.

### `_open_up()`

Make everything bsdtar unpacked readable and writable by this process, top down.

libarchive applies the modes a 7z or RAR carries (minus the umask), and
the archive is a stranger's: a file with mode 000 cannot be read, a folder
with 0500 cannot have its files removed afterwards (so the work folder
could not be), and a folder with 000 cannot be listed, so os.walk would
skip it. Each folder is opened BEFORE it is listed -- os.walk lists a folder
after it has handed over its parent, which is when this runs. Links are
never followed or chmod-ed (they are removed next); devices and fifos are
left for the same step.

### `_tail()`

The last `limit` bytes of a file bsdtar wrote its complaints to.

### `_extract_with_bsdtar()`

- At `with tempfile.TemporaryFile() as listing_errors:`:
  ```text
  1. The names, which are the index: counted and checked before anything is
     written, and read no further than the limit allows. bsdtar's stderr
     goes to a file, not a pipe: this loop reads stdout to the end before
     it looks at stderr, and a pipe nobody is draining fills at 64 KB and
     stops bsdtar until the timeout.
  ```
- `listing.wait()` — so it has let go of the file before anyone deletes it
- At `with tempfile.TemporaryFile() as extract_errors:`:
  ```text
  2. The extraction, with a watcher that stops it at the limit: bsdtar will
     write whatever the archive says, and the archive is a stranger's.
     -p, not --no-same-permissions: without it libarchive gives a folder its
     mode the moment it makes it, so a folder the archive marks 000 or 0500
     is closed before its own files are written into it, and bsdtar fails on
     every one of them ("Error exit delayed from previous errors"; first seen
     on Linux CI, 2026-10-10 -- Windows has no modes). With -p it gives
     folders their modes only at the end, after everything is in them, and
     _open_up then opens the whole tree again (setuid cleared). ACLs, extended
     attributes and file flags are a stranger's too, and not wanted at all.
  ```
- At `_open_up(root)`:
  The modes the archive carried are applied now, and a 000 folder hides
  what is in it from every walk that follows -- the size check included.
- At `os.utime(root)`:
  And so is the date the archive gave its `./`, which can be 1970: the
  folder is this job's, and it is new (the sweeper also knows the job's
  name, see bot.\_sweep_uploads; this is so the folder never LOOKS old).

### `_count_files()`

What bsdtar wrote, counted against the same budget as the other readers.

### `_drop_non_files()`

Whatever bsdtar made that is not a plain file or a folder goes: links
(which it creates as links), devices, fifos. Never followed, never carried.

## Writing

### `_entries()`

(relative path, absolute path, is a directory), directories first within
each level, in a stable order -- so the same input is the same output. A
folder that cannot be listed is a refusal, not a gap (\_walk_failed).

### `_require_text_names()`

A ZIP or a 7z stores names as text, so a name that is not text cannot go
into one. A TAR cannot hold only text: it holds the bytes of a name, and
the surrogate-escaped name tarfile gives a non-UTF-8 name (a cp1251 or
Shift-JIS archive from an old machine) goes back out as the bytes it came in
as. Found by sending one: UnicodeEncodeError out of zipfile, a crash that
refunded and paged the operator for what is a file the bot does not take.

### `_normalised()`

Nobody's user id, nobody's name, and permissions that mean the same on
any machine: what was on the sender's disk is not the recipient's business.

### `_write_7z()`

- At `raise ConversionError("Couldn't make a 7z out of this one" + (f" ({detail})" if detail e …`:
  A writer that dies without a word (seen on Windows' own bsdtar with
  names outside ASCII) is not something to dress up as a damaged file.

## The conversion
