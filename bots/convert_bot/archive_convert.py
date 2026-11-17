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

WRITES = frozenset({"zip", "tar", "tgz", "tbz2", "txz", "7z"})

TAR_READ_MODES = {"tar": "r:", "tgz": "r:gz", "tbz2": "r:bz2", "txz": "r:xz"}
TAR_WRITE_MODES = {"tar": "w:", "tgz": "w:gz", "tbz2": "w:bz2", "txz": "w:xz"}

MAX_ENTRIES = int(os.environ.get("CONVERT_MAX_ARCHIVE_ENTRIES") or 10_000)


def expanded_limit_mb(free_mb: "int | None", slots: int) -> int:
    if free_mb is None:
        return 300
    return max(300, min(4000, free_mb // (2 * max(1, slots))))


def _default_expanded_mb() -> int:
    try:
        import jobs
        work = Path(os.environ.get("CONVERT_UPLOAD_DIR") or (Path(tempfile.gettempdir()) / "convertbot"))
        while not work.exists() and work != work.parent:
            work = work.parent
        return expanded_limit_mb(shutil.disk_usage(work).free // (1024 * 1024), jobs.MAX_CONCURRENT)
    except Exception:
        return 300


MAX_EXPANDED_MB = int(os.environ.get("CONVERT_MAX_ARCHIVE_EXPANDED_MB") or _default_expanded_mb())
MAX_RATIO = int(os.environ.get("CONVERT_MAX_ARCHIVE_RATIO") or 200)
MAX_NAME_BYTES = 1024
MAX_PART_BYTES = 255
MAX_HEADER_BYTES = 1024 * 1024
BSDTAR_TIMEOUT_S = 900.0
_STDERR_TAIL = 64 * 1024
_WINDOWS_NAMES = os.name == "nt"
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


def _safe_name(raw: str) -> "str | None":
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
    target = (root / relative)
    resolved = target.resolve()
    if os.path.commonpath([str(root.resolve()), str(resolved)]) != str(root.resolve()):
        raise ConversionError("That archive would write outside its own folder, so it was not opened.")
    return target


class _Budget:
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
    try:
        path.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError):
        raise ConversionError(_CLASH) from None


def _copy_capped(source, destination: Path, budget: _Budget) -> None:
    _make_folder(destination.parent)
    if destination.is_dir():
        raise ConversionError(_CLASH)
    with open(destination, "wb") as out:
        while True:
            chunk = source.read(_CHUNK)
            if not chunk:
                break
            budget.add(len(chunk))
            out.write(chunk)
    budget.files += 1


def _extract_zip(in_path: str, root: Path, budget: _Budget) -> None:
    try:
        archive = zipfile.ZipFile(in_path)
    except UnicodeDecodeError:
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
    def __init__(self, fileobj):
        self._fileobj = fileobj
        self._left = MAX_HEADER_BYTES

    def read(self, size=-1):
        if size is None or size < 0 or size > self._left:
            raise tarfile.HeaderError(_HEADER_TOO_BIG)
        self._left -= size
        return self._fileobj.read(size)

    def __getattr__(self, name):
        return getattr(self._fileobj, name)


class _CappedTarInfo(tarfile.TarInfo):
    def _within_allowance(self, parent, tar, check_size):
        if check_size and self.size > MAX_HEADER_BYTES:
            raise tarfile.HeaderError(_HEADER_TOO_BIG)
        if isinstance(tar.fileobj, _HeaderReads):
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
        return self._within_allowance(super()._proc_sparse, tar, False)


def _tar_refusal(exc: Exception) -> ConversionError:
    if _HEADER_TOO_BIG in str(exc):
        return ConversionError("That archive has a header far larger than any file name, "
                               "so it was not opened.")
    return ConversionError(f"Couldn't read that archive: {exc}")


def _extract_tar(in_path: str, src_ext: str, root: Path, budget: _Budget) -> None:
    try:
        archive = tarfile.open(in_path, mode=TAR_READ_MODES[src_ext], tarinfo=_CappedTarInfo)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise _tar_refusal(exc) from exc
    except MemoryError:
        raise
    except Exception as exc:
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
                    budget.skipped += 1
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
    except Exception as exc:
        raise ConversionError(f"That archive is damaged ({type(exc).__name__}).") from exc


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
    raise ConversionError("Some of that archive's files couldn't be read, so it was not opened.") from exc


def _tree_size(root: Path, strict: bool = False) -> int:
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
    return (mode | (0o700 if is_dir else 0o600)) & 0o777


def _open_up(root: Path) -> None:
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
            listing.wait()
            listing.stdout.close()
        errors = _tail(listing_errors)
    if listing.returncode not in (0, None) and not names:
        _refuse_bsdtar(errors)
    for name in names:
        _safe_name(name)

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

    _open_up(root)
    os.utime(root)
    if _tree_size(root, strict=True) > _limit_bytes():
        raise _too_big()
    _drop_non_files(root, budget)
    _count_files(root, budget)


def _count_files(root: Path, budget: _Budget) -> None:
    for current, _dirs, files in os.walk(root, onerror=_walk_failed):
        for name in files:
            budget.entry()
            budget.add(os.lstat(os.path.join(current, name)).st_size)
            budget.files += 1


def _drop_non_files(root: Path, budget: _Budget) -> None:
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


def _entries(root: Path):
    for current, dirs, files in os.walk(root, onerror=_walk_failed):
        dirs.sort()
        here = Path(current)
        for name in sorted(files):
            yield (here / name).relative_to(root).as_posix(), here / name, False
        if not dirs and not files and here != root:
            yield here.relative_to(root).as_posix(), here, True


def _require_text_names(entries, label: str) -> None:
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
        raise ConversionError("Couldn't make a 7z out of this one" + (f" ({detail})" if detail else "")
                              + ". A ZIP or TAR.XZ will work.")
    return count


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
