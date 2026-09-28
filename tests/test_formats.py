"""Every download has to arrive with its sound.

The old format chain ended `.../mp4/bestvideo+bestaudio/best`, and a bare
`mp4` in yt-dlp means "the best format whose extension is mp4" -- which on
YouTube is a video-only DASH stream. It sat ahead of the merge branch, so it
won whenever the two filesize branches missed, and on YouTube they miss
almost always: DASH formats report `filesize: None`, and a format with an
unknown size *fails* `[filesize<48M]` rather than passing it.

The result was a silent video. Not an error, not a failed download -- a file
that arrived, played, and had no audio, on one platform out of three, only
for people who did not notice until they had already sent it on.

So the rule this pins down is structural rather than about any one string:
every branch of the chain must either merge audio in explicitly (`+ba`) or be
a muxed selector (`b`, which by definition carries both). A branch that is
neither is a branch that can hand back a silent file.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".mirror", "downloader_bot"))
sys.path.insert(0, os.path.join(HERE, "stubs"))

import video

failures = []
def check(name, ok):
    print(("  ok  " if ok else "  FAIL  ") + name)
    if not ok:
        failures.append(name)


def carries_audio(branch: str) -> bool:
    """True if this branch cannot resolve to a video-only stream.

    `+ba` merges audio in. A selector starting `b` (and not `bv`) is a muxed
    format, which has both by definition. Anything else -- `mp4`, `bv*`,
    `bestvideo` alone -- can be video-only.
    """
    if "+ba" in branch or "+bestaudio" in branch:
        return True
    head = branch.split("[")[0]
    return head in ("b", "best")


def main() -> int:
    selector = video._format_selector(48)
    print(f"  {selector}\n")

    branches = [b for b in selector.split("/") if b]
    check("the chain has fallbacks rather than one selector", len(branches) >= 3)

    for branch in branches:
        check(f"branch {branch!r} cannot come back silent", carries_audio(branch))

    # The specific shape that caused it, named so nobody reintroduces it by
    # reaching for "prefer mp4".
    check("no bare extension branch (that is what `mp4` was)",
          not any(b in ("mp4", "webm", "mov") for b in branches))

    # The size ceiling still has to be expressed, or a mis-pasted link to a
    # feature film fills the disk before max_filesize notices.
    check("the size ceiling is still applied",
          any("filesize" in b for b in branches))
    check("and it tracks MAX_DOWNLOAD_MB rather than a hardcoded number",
          "97M" in video._format_selector(97))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("every branch comes back with audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
