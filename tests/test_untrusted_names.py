"""A filename from the other side of the wire cannot become a path.

Two bots build a filesystem path with an extension in it, and neither
extension is theirs:

  ConvertBot takes the upload's from `document.file_name`, which the sending
  client chooses, and the output's from callback data, which a modified
  client can put anything into.

  DownloaderBot takes it from whatever a resolver service or a remote URL
  said the file was called -- not even the user's, a third party's.

`Path(dir) / f"{uuid}.{ext}"` and `os.path.join(dir, f"{uuid}.{ext}")` both
treat a "/" in `ext` as a directory separator and a ".." as a level up, so
until v1.4.0 a file_name of `a.b/../../../x` decided where the download
landed. The fix is that neither function returns anything but ASCII
alphanumerics, and this asserts it against the shapes that mattered.

    python3 tests/test_untrusted_names.py <bot>

Run per bot, like test_imports.py: each bot's modules import each other by
bare name, so two of them cannot share a process.
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BOT = sys.argv[1]
sys.path.insert(0, str(HERE / ".mirror" / BOT))
sys.path.insert(0, str(HERE / "stubs"))
for var in ("SBOT", "CBOT", "DBOT", "ABOT", "MBOT"):
    os.environ[f"{var}_TOKEN"] = "test"
    os.environ[f"{var}_USERNAME"] = "test"
    os.environ[f"{var}_ADMIN_ID"] = "1"
os.environ["DATABASE_URL"] = "postgresql://stub"

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


# Names that have to produce no path component of their own. The first is the
# one that actually escaped: rsplit/rpartition on "." hands back everything
# after the LAST dot, which in `a.b/../../../etc/passwd` is most of the string.
HOSTILE = [
    "a.b/../../../etc/passwd",
    "a.b/../../../../../../root/.ssh/authorized_keys",
    "x./../..",
    "photo.jpg/../../../../tmp/x",
    "u.\\..\\..\\windows\\system32\\drivers\\etc\\hosts",
    "file.j pg",
    "file.j\x00pg",
    "clip.mp4;rm -rf /",
    "clip.mp4\nSELECT 1",
    "x." + "a" * 400,
    "тест.мп4",
    "e.%2e%2e%2fetc",
]

# And these have to still work, because a fix that refuses real files is not
# a fix. (name, the extension it should yield)
ORDINARY = [
    ("photo.jpg", "jpg"),
    ("VIDEO.MP4", "mp4"),
    ("archive.tar.gz", "gz"),
    ("a.webm", "webm"),
]

# ConvertBot's _ext_of does one thing DownloaderBot's does not: it normalises
# the spellings of a format onto one name, because v1.5.0's menu is built from
# that name and ".jpeg" and ".jpg" must not become two buttons. Sanitising and
# normalising are separate jobs and this is the second one, so it is asserted
# separately -- and only for the bot that does it.
NORMALISED = [
    ("clip.jpeg", "jpg"),
    ("scan.TIF", "tiff"),
    ("notes.markdown", "md"),
    ("page.htm", "html"),
    ("subs.ssa", "ass"),
    ("book.EPUB", "epub"),
]


def under(root, path) -> bool:
    """True when `path` really is inside `root`, `..` resolved."""
    root = pathlib.Path(root).resolve()
    try:
        return pathlib.Path(path).resolve().is_relative_to(root)
    except AttributeError:                      # Python 3.8
        return str(pathlib.Path(path).resolve()).startswith(str(root) + os.sep)


def check_convert_bot() -> None:
    import bot

    for name in HOSTILE:
        ext = bot._ext_of(name, "")
        check(f"convert: {name[:34]!r} yields no path component",
              ext.isalnum() and ext.isascii() or ext == "", repr(ext))
        path = bot._new_upload_path(1, bot._ext_of(name, ""))
        check(f"convert: {name[:34]!r} stays in the upload directory",
              under(bot.UPLOAD_DIR, path), str(path))

    for name, expected in ORDINARY + NORMALISED:
        check(f"convert: {name!r} gives {expected!r}",
              bot._ext_of(name, "") == expected, bot._ext_of(name, ""))

    # The output path is built from callback data, which is not the button's.
    for hostile in ("../../../../etc/cron.d/x", "mp4/../../x", "", "MP4"):
        path = bot._new_upload_path(1, bot._safe_ext(hostile))
        check(f"convert: callback data {hostile[:30]!r} stays in the upload directory",
              under(bot.UPLOAD_DIR, path), str(path))

    # And the format has to be one that was actually offered. Checked against
    # targets_for(), not against the declared table: a host without PyMuPDF
    # does not offer PDF, so it must not accept PDF either, and the two answers
    # come from the same call.
    import formats
    source = pathlib.Path(HERE.parent / "convert_bot" / "bot.py").read_text(encoding="utf-8")
    body = source.split("async def convert_format_chosen", 1)[1].split("\nasync def ", 1)[0]
    check("convert: the chosen format is checked against what was offered",
          "formats.targets_for(" in body and "not in formats.targets_for" in body)

    # Every format name in the matrix ends up in a filename, so none of them
    # may carry anything a path separator could be made of.
    plain = []
    for category in formats.CATEGORIES:
        plain += list(formats.SOURCES[category])
        plain += list(formats.TARGETS_BY_CATEGORY.get(category, ()))
    for targets in formats.TARGETS_BY_FORMAT.values():
        plain += list(targets)
    plain += list(formats.ANIMATED_TARGETS)
    bad = sorted({name for name in plain if not (name.isalnum() and name.isascii())})
    check("convert: every format in the matrix is a plain extension", not bad, str(bad))

    # An alias must not shadow a format that exists in its own right, or the
    # normalisation above would quietly redirect one real format to another.
    shadowed = sorted(set(formats.ALIASES) & set(formats.CATEGORY_OF))
    check("convert: no alias shadows a real format", not shadowed, str(shadowed))


def check_downloader_bot() -> None:
    import resolvers

    for name in HOSTILE:
        ext = resolvers._ext_of(name, "mp4")
        check(f"download: {name[:34]!r} yields no path component",
              ext.isalnum() and ext.isascii(), repr(ext))
        path = os.path.join("/tmp/work", f"deadbeef.{ext}")
        check(f"download: {name[:34]!r} stays in the working directory",
              under("/tmp/work", path), path)

    for name, expected in ORDINARY:
        check(f"download: {name!r} still gives {expected!r}",
              resolvers._ext_of(name, "mp4") == expected, resolvers._ext_of(name, "mp4"))

    # rpartition returns the WHOLE string when there is no separator, so a
    # dotless name used to come back as its own extension -- and _kind_of then
    # found it in neither the video nor the audio list and called it a photo.
    check("download: a dotless name has no extension",
          resolvers._ext_of("video", "mp4") == "mp4", resolvers._ext_of("video", "mp4"))
    check("download: a dotless video is not called a photo",
          resolvers._kind_of("bigbuckbunny") == "photo"
          and resolvers._kind_of("bigbuckbunny.mp4") == "video")


def main() -> int:
    print(f"=== {BOT}: names from the other side of the wire ===")
    if BOT == "convert_bot":
        check_convert_bot()
    elif BOT == "downloader_bot":
        check_downloader_bot()
    else:
        print(f"  --  nothing to check for {BOT}")
    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
