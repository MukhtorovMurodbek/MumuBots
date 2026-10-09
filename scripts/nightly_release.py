#!/usr/bin/env python3
"""The 04:00 release, run unattended.

    python scripts/nightly_release.py            # do it
    python scripts/nightly_release.py --dry-run  # say what it would do
    python scripts/nightly_release.py --now      # skip the wait for 04:00
    python scripts/nightly_release.py --at 19:00
                                  # a window the owner picks, on this computer's
                                  # clock
    python scripts/nightly_release.py --edition C
                                  # the one-process edition: the same pause, window,
                                  # quiet and resume, but the push is BotFamily<ver>C

Runs the whole sequence from UPDATES.md, in order, and refuses rather than
guesses at every point where it could get it wrong:

    1. Pause the four public bots, PAUSE_LEAD_MINUTES before the window.
       Anyone who turns up from now on is told an update is coming and is
       written down, so step 6 can go back to them.
    2. Wait for the window, watching activity the whole time.
    3. At the window, publish -- but only once nobody has been active for
       QUIET_MINUTES. If somebody still is, keep waiting; if they are still
       there MAX_WAIT_MINUTES later, ABORT and resume the bots rather than
       redeploy underneath them.
    4. The push, which is what makes Railway rebuild: this folder to
       MumuBots, which the five per-bot services build from -- or, with
       --edition C, the BotFamily<version>C folder to MumuCombined
       (BotFamily until it is renamed), which the one-process service
       rebuilds from. Until 2.0.0 this step was publish.ps1, which pushed each
       bot folder to a repository of its own; the bots now share shared/, so
       one push carries all five.
    5. Wait for every bot's heartbeat to report the new version.
    6. Resume, which also messages everyone step 1 turned away.

Before step 1 it refuses, without pausing anything, a folder that is not the
release (uncommitted changes, or the nearest tag is not the version); step 4
asks again. And whatever goes wrong after step 1 -- any exception, a push that
times out, Ctrl-C -- finish() resumes the bots the same way step 6 does and
tells the owner, so the bots are never left closed by a crash. Every git command
runs without stdin, with GIT_TERMINAL_PROMPT=0 and GCM_INTERACTIVE=never and
with a timeout, so a credential prompt or a dead network fails rather than hangs.

Everything it does is written to scripts/release.log and, at the end, into
family.events -- so ManagerBot forwards the outcome to the owner as a Telegram
message whether or not anybody was watching a terminal.

WHY THE ACTIVITY CHECK IS NOT JUST A FORMALITY
    04:00 Asia/Tashkent is chosen because it is reliably empty, not because
    it is guaranteed to be. A redeploy costs a user their download or their
    half-written message, so "the window says it is fine" is not the same
    claim as "nobody is here". This checks, and it would rather ship nothing
    than ship over somebody.

WHICH EDITION. Since 2.0.0 every version has a base folder and a C folder,
and production runs one of them. Releasing the one production does not run
changes nothing for anybody, and waits out HEARTBEAT_WAIT_MINUTES for a
version that never comes. Both editions report the same version number, so
everything but the push itself is the same.

ADMIN IDS ARE EXCLUDED. The owner testing their own bot appears in
activity_events exactly like a real user, and a release has been held back
before on the strength of an "active user" who turned out to be the owner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = Path(__file__).resolve().parent / "release.log"

# --- the policy, in one place ------------------------------------------------
TZ_OFFSET_HOURS = 5          # Asia/Tashkent. No DST.
WINDOW_HOUR = 4              # 04:00 there.
PAUSE_LEAD_MINUTES = 20      # pause this long before the window opens
QUIET_MINUTES = 15           # nobody active in this long == nobody mid-work
MAX_WAIT_MINUTES = 60        # give up and abort rather than ship over someone
HEARTBEAT_WAIT_MINUTES = 12  # how long to wait for the new version to appear
GIT_TIMEOUT_SECONDS = 120    # any local git command; a healthy one takes milliseconds
PUSH_TIMEOUT_SECONDS = 600   # the push, which is the one that crosses the network

# Every git command here runs with these added, so that one which would ask a
# question fails at once. Under Task Scheduler nobody is there to answer: a
# credential prompt (git's own, or Git Credential Manager's window) would hang
# until the push timeout, with the bots paused the whole time.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}

BOTS = {
    "stickerbot": "sticker_bot",
    "convertbot": "convert_bot",
    "downloaderbot": "downloader_bot",
    "anonbot": "anon_bot",
}


def log(message: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"{stamp}  {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def tashkent_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=TZ_OFFSET_HOURS)


def run_git(folder: Path, *args: str, timeout: int = GIT_TIMEOUT_SECONDS):
    """git in `folder`, the way every git command in this script is run: it
    cannot ask anybody a question (GIT_ENV, and no stdin), and it cannot take
    for ever. Raises subprocess.TimeoutExpired, which each caller turns into
    its own kind of "no" -- a refusal before the pause, a failed step after.

    Its output goes to temporary files rather than pipes. A push is git plus a
    helper process (git-remote-https) that inherits the pipes; on Windows,
    killing git on a timeout then still waits for the helper to let go of them
    (measured: a 2 s timeout returned after 25 s, when the helper's own wait
    ended), and a push hung on a dead network is exactly the case the timeout
    is for. With files nothing waits for the helper."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        def output():
            texts = []
            for fh in (out, err):
                fh.seek(0)
                texts.append(fh.read().decode("utf-8", errors="replace"))
            return texts
        try:
            done = subprocess.run(["git", *args], cwd=folder, stdin=subprocess.DEVNULL,
                                  stdout=out, stderr=err, timeout=timeout,
                                  env={**os.environ, **GIT_ENV})
        except subprocess.TimeoutExpired as exc:
            exc.stdout, exc.stderr = output()
            raise
        stdout, stderr = output()
        return subprocess.CompletedProcess(done.args, done.returncode, stdout, stderr)


def _timed_out(exc: subprocess.TimeoutExpired) -> str:
    return f"git did not answer within {exc.timeout:.0f} s ({' '.join(str(a) for a in exc.cmd[:3])})"


def _env_of(folder: str) -> dict:
    """A bot's settings as it reads them itself: its own .env over shared/.env.
    Since 2.0.0 DATABASE_URL can live in either, and reading only the bot's
    own file would find no database the day it moves to the shared one."""
    if str(ROOT / "shared") not in sys.path:
        sys.path.insert(1, str(ROOT / "shared"))
    import envfiles
    return envfiles.merged(ROOT / folder)


def _c_folder(explicit: str | None) -> Path:
    """The C edition of this version: BotFamily2.0.0 -> BotFamily2.0.0C, beside it."""
    return Path(explicit) if explicit else ROOT.parent / f"{ROOT.name}C"


# Where each edition is released to (docs/changes/CHANGES_v2.0.0.md, "GitHub:
# 12 repositories become 3"). The push is what Railway rebuilds from, so the
# right folder pushed to the wrong repository ships nothing -- and this
# folder's own origin was telegram-bot-family, whose early commits hold
# tokens and which the owner is retiring. BotFamily stays accepted for the C
# edition: GitHub redirects a renamed repository's old address.
REPOSITORIES = {"base": ("MumuBots",), "C": ("MumuCombined", "BotFamily")}


# telegram-bot-family's first commits -- `git rev-list --max-parents=0 --all`
# in BotFamily1.7.0, whose .git keeps that history. Its early commits hold bot
# tokens, and `git push origin HEAD:main` carries every commit HEAD descends
# from, so a base folder still on that history must never be pushed to
# MumuBots, which is public and starts again at 2.0.0 -- right origin or not.
OLD_HISTORY_ROOTS = {
    "0b3e05baa32c8e837b7da97e516abb049952b8d5",   # master
    "66061fe23233c89dd7684d230a8c38e37ba4e2ce",
    "cde616f669c9415e728b1e75a14404fda2334343",
    "abf3dc21aa9776ec9807039a8f1959e1891a9cad",
    "e8654b49c17938c68f379d54a0defd2e1eeb0eca",
    "1701346523ffbab589ddf7d7a28f000e4e6283af",   # the split/* branches
}


def carries_old_history(folder: Path) -> str | None:
    """Why HEAD in `folder` cannot go to MumuBots because of what it descends
    from, or None."""
    try:
        roots = run_git(folder, "rev-list", "--max-parents=0", "HEAD").stdout.split()
    except subprocess.TimeoutExpired as exc:
        return _timed_out(exc)   # unknown is not "clean": refuse
    if OLD_HISTORY_ROOTS & set(roots):
        return (f"{folder.name}'s history still begins with telegram-bot-family's, whose early "
                f"commits hold tokens, and a push would carry all of it -- MumuBots starts again "
                f"at 2.0.0; see docs/changes/CHANGES_v2.0.0.md, 'GitHub: 12 repositories become 3'")
    return None


def wrong_remote(folder: Path, edition: str) -> str | None:
    """Why `origin` in `folder` is not where `edition` is released to, or None."""
    try:
        url = run_git(folder, "remote", "get-url", "origin").stdout.strip()
    except subprocess.TimeoutExpired as exc:
        return _timed_out(exc)
    # https://github.com/o/MumuBots.git, git@github.com:o/MumuBots, C:\mirror\MumuBots.git
    name = re.split(r"[/\\:]", url.rstrip("/\\").removesuffix(".git"))[-1]
    if name in REPOSITORIES[edition]:
        return None
    return (f"{folder.name}'s origin is {name or 'not set'}, not "
            f"{' or '.join(REPOSITORIES[edition])} -- see docs/changes/CHANGES_v2.0.0.md, "
            f"'GitHub: 12 repositories become 3'")


def _setup():
    """Point this process at the shared database and the target version.

    Imports ManagerBot's db module, because that is the one that already knows
    how to reach across every bot's schema -- and because reusing it means
    this script cannot drift away from what the bots themselves do.
    """
    env = _env_of("manager_bot")
    os.environ.setdefault("DATABASE_URL", env.get("DATABASE_URL", ""))
    os.environ.setdefault("DB_SCHEMA", env.get("DB_SCHEMA", "manager_bot"))
    sys.path.insert(0, str(ROOT / "manager_bot"))
    admins = {int(x) for x in env.get("MBOT_ADMIN_ID", "").split(",") if x.strip()}
    version = re.search(r'FAMILY_VERSION", "([^"]+)"',
                        (ROOT / "shared" / "family_link.py").read_text(encoding="utf-8"))
    return admins, (version.group(1) if version else "?")


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def active_users(db, admins: set[int], minutes: int) -> dict[str, int]:
    """Non-admin users seen in the last `minutes`, per bot."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    counts = {}
    with db.pooled_read() as conn:
        for bot_id, schema in BOTS.items():
            cur = conn.execute(
                f'SELECT count(DISTINCT user_id) FROM "{schema}".activity_events '
                f"WHERE occurred_at >= %s AND NOT (user_id = ANY(%s))",
                (since, list(admins) or [0]),
            )
            counts[bot_id] = cur.fetchone()[0]
    return counts


def versions(db) -> dict[str, str]:
    return {row["bot_id"]: row["version"] for row in db.all_heartbeats()}


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------

def queue(db, admins: set[int], command: str, args: str = "") -> None:
    owner = next(iter(admins), 0)
    for bot_id in BOTS:
        db.queue_command(bot_id, command, args, owner, owner)
        log(f"    queued {command} {args} -> {bot_id}")


def announce(db, level: str, message: str, details: str = "") -> None:
    """Into family.events, which is what ManagerBot forwards to the owner.

    The whole point of doing it this way rather than sending a Telegram
    message directly: this script holds no bot token, and it should not.
    """
    try:
        with db.pooled() as conn:
            conn.execute(
                "INSERT INTO family.events (bot_id, level, kind, message, details) "
                "VALUES (%s, %s, %s, %s, %s)",
                ("managerbot", level, "release", message, details or None),
            )
            conn.commit()
    except Exception as exc:
        log(f"    (could not write the event: {exc})")


def release_not_ready(target: str) -> str | None:
    """Why the working copy is not the release, or None.

    The push carries committed files, so an uncommitted change would be left
    behind without a word, and a missing tag means the version was never
    finalised. Release tooling committed after the tag is fine -- that is how
    scripts/run_release.cmd arrived -- so it is the nearest tag that is checked,
    not the commit it sits on.

    Asked twice: before anything is paused, so that a release that is not going
    to happen never closes the bots, and again at the window, because the folder
    can change during the hours in between."""
    def git(*args):
        return run_git(ROOT, *args).stdout.strip()
    try:
        nearest = git("describe", "--tags", "--abbrev=0")
        if nearest != f"v{target}":
            return f"the nearest tag is {nearest or 'none'}, not v{target}"
        if git("status", "--porcelain", "--untracked-files=no"):
            return "there are uncommitted changes"
    except subprocess.TimeoutExpired as exc:
        return _timed_out(exc)
    return None


def c_not_ready(folder: Path, target: str) -> str | None:
    """Why the C folder is not the release, or None -- the same two questions as
    release_not_ready, asked of the C folder's own repository and tag."""
    if not (folder / ".git").exists():
        return f"{folder} is not a checkout of MumuCombined (formerly BotFamily)"
    def git(*args):
        return run_git(folder, *args).stdout.strip()
    try:
        nearest = git("describe", "--tags", "--abbrev=0")
        if nearest != f"v{target}C":
            return f"the C folder's nearest tag is {nearest or 'none'}, not v{target}C"
        if git("status", "--porcelain", "--untracked-files=no"):
            return "the C folder has uncommitted changes"
    except subprocess.TimeoutExpired as exc:
        return _timed_out(exc)
    return None


def publish(folder: Path, tag: str, dry_run: bool) -> bool:
    """Push the branch and the version's tag: this folder to MumuBots, or the
    C folder to MumuCombined.

    False for any push that did not finish -- including one that ran out of
    time, which is a failed step like any other and so goes down the same road
    (the bots are resumed and the owner is told), not an unhandled crash. A
    push that timed out may or may not have reached GitHub; the log says so."""
    args = ["push", "origin", "HEAD:main", f"refs/tags/{tag}"]
    if dry_run:
        args.append("--dry-run")
    log("    running: git " + " ".join(args) + f"   (in {folder.name})")
    try:
        result = run_git(folder, *args, timeout=PUSH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        log(f"    the push did not finish within {PUSH_TIMEOUT_SECONDS} s and was stopped; "
            f"whether it reached the remote is not known")
        for chunk in (exc.stdout, exc.stderr):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
            for line in (chunk or "").splitlines():
                if line.strip():
                    log("    | " + line.rstrip())
        return False
    for line in ((result.stdout or "") + (result.stderr or "")).splitlines():
        if line.strip():
            log("    | " + line.rstrip())
    return result.returncode == 0


def not_the_release(c_folder: Path | None, target: str) -> str | None:
    """release_not_ready, or c_not_ready for the C edition -- one question,
    asked before the pause and again at the window."""
    return c_not_ready(c_folder, target) if c_folder else release_not_ready(target)


class ReleaseState:
    """What `finish` needs to know to leave the bots open and the owner told,
    however the run ends. Filled in by `_run` as it goes."""

    def __init__(self):
        self.db = None
        self.admins: set[int] = set()
        self.target = "?"
        self.paused = False      # the pause was (at least partly) queued
        self.resumed = False     # the resume was queued, for every bot
        self.announced = False   # the owner has been told how this ended
        self.stage = "before"    # before -> publishing -> published
        self.crash: BaseException | None = None


def resume_bots(state: ReleaseState) -> None:
    """The one place the resume is queued, so `finish` knows whether it has been."""
    queue(state.db, state.admins, "resume")
    state.resumed = True


def tell(state: ReleaseState, level: str, message: str, details: str = "") -> None:
    announce(state.db, level, message, details)
    state.announced = True


def finish(state: ReleaseState) -> None:
    """The last line of defence, run however main() ends.

    Pausing is the only thing this script does that hurts people if it is then
    forgotten: the bots turn away everyone who comes, until something resumes
    them. The normal paths all resume; this is for the ones nobody wrote, an
    exception or a timeout somewhere after the pause. It resumes exactly as the
    normal path does -- the same queue, so the people who were turned away are
    told it is over just the same -- and says in family.events that the release
    did not go as planned. The pause also runs out by itself (PAUSE_LEAD_MINUTES
    + MAX_WAIT_MINUTES), which is the net under this net."""
    if not state.paused or state.announced:
        return
    what = (f"{type(state.crash).__name__}: {state.crash}" if state.crash is not None
            else "the run ended without saying how")[:300]
    log(f"ABORTING: the release stopped on an error after the bots were paused ({what}).")
    resume_failed = ""
    if not state.resumed:
        try:
            resume_bots(state)
        except Exception as exc:
            resume_failed = f"{type(exc).__name__}: {exc}"
            log(f"    COULD NOT QUEUE THE RESUME ({resume_failed}). The pause runs out by itself "
                f"within {PAUSE_LEAD_MINUTES + MAX_WAIT_MINUTES} minutes; /resume from ManagerBot "
                f"opens the bots sooner.")
    if resume_failed:
        fate = (f"The bots could NOT be resumed from here ({resume_failed}); they reopen by "
                f"themselves within {PAUSE_LEAD_MINUTES + MAX_WAIT_MINUTES} minutes, or send /resume.")
    else:
        fate = "The bots have been resumed."
    if state.stage == "published":
        outcome = "The new version had already been pushed."
    elif state.stage == "publishing":
        outcome = "It stopped during the push, so whether the push reached GitHub is not known."
    else:
        outcome = "Nothing was published."
    if state.db is not None:
        tell(state, "error",
             f"Release {state.target} aborted: the release script stopped on an error ({what}) "
             f"after the bots were paused. {outcome} {fate} See scripts/release.log.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", action="store_true",
                        help="skip the wait for the window (still checks activity)")
    parser.add_argument("--at", metavar="HH:MM",
                        help="the window at this time on this computer's clock, the next time it "
                             "comes round, instead of 04:00 Tashkent")
    parser.add_argument("--skip", metavar="BOT_DIR",
                        help="retired in 2.0.0: one push carries all five bots")
    parser.add_argument("--edition", choices=("base", "C"), default="base",
                        help="which edition to release; C pushes the BotFamily<version>C folder")
    parser.add_argument("--c-folder", metavar="PATH",
                        help="the C folder, if it is not this folder's name plus C beside it")
    opts = parser.parse_args()
    if opts.skip:
        # Until 2.0.0 publish.ps1 pushed each bot to a repository of its own,
        # so a busy one could be held back. The bots now share shared/, and
        # every release changes it, so every release rebuilds all five.
        parser.error("--skip no longer applies: since 2.0.0 one push carries all five bots")

    state = ReleaseState()
    try:
        return _run(opts, state)
    except BaseException as exc:
        state.crash = exc
        raise
    finally:
        # Whatever happened above -- a bug, a database that went away, a push that
        # timed out, Ctrl-C -- the bots are not left closed and nobody is left
        # guessing. A no-op unless the bots were paused and nothing has yet said
        # how this ends.
        finish(state)


def _run(opts, state: "ReleaseState") -> int:
    admins, target = _setup()
    c_folder = _c_folder(opts.c_folder) if opts.edition == "C" else None
    import db  # ManagerBot's, now that the path and env are set
    state.db, state.admins, state.target = db, admins, target

    log("=" * 70)
    log(f"Release run, {'C edition' if c_folder else 'base edition'}. Target version {target}. Tashkent time "
        f"{tashkent_now():%Y-%m-%d %H:%M}."
        + ("  [DRY RUN]" if opts.dry_run else ""))

    # -- 1. how it looks before we touch anything ---------------------------
    remote = wrong_remote(c_folder or ROOT, opts.edition) or \
        (None if c_folder else carries_old_history(ROOT))
    if remote:
        # Before anything is paused: no wait makes the wrong repository right.
        log(f"Not releasing: {remote}.")
        return 1
    before = versions(db)
    log(f"    versions now: {before}")
    stale = [b for b in BOTS if before.get(b) != target]
    if not stale:
        log("Every bot already reports the target version. Nothing to do.")
        return 0
    log(f"    to publish: {', '.join(stale)}")

    # Is the folder the release at all? Asked now, before anything is paused:
    # an uncommitted change or a missing tag will not mend itself during the wait,
    # and finding out after the pause costs the users a closed bot for nothing.
    # (Asked again at the window, below -- the folder can change in between.)
    not_ready = not_the_release(c_folder, target)
    if not_ready:
        log(f"    not the release: {not_ready}")
        if not opts.dry_run:
            log("ABORTING before anything was paused. Nothing was published.")
            tell(state, "warning", f"Release {target} aborted before anything was paused: "
                                   f"{not_ready}. The bots were not touched.")
            return 1

    day = active_users(db, admins, 24 * 60)
    log(f"    users in the last 24h (admins excluded): {day}")

    # -- 2. pause, ahead of the window -------------------------------------
    if opts.at:
        # The owner's call for this release, on the clock they read. Kept in
        # the same Tashkent-shifted form as tashkent_now(), so everything below
        # compares like with like.
        hour, minute = (int(part) for part in opts.at.split(":"))
        local = dt.datetime.now().astimezone()
        at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= local:
            at += dt.timedelta(days=1)
        window = at.astimezone(dt.timezone.utc) + dt.timedelta(hours=TZ_OFFSET_HOURS)
        log(f"    window: {at:%H:%M} on this computer's clock ({window:%H:%M} Tashkent)")
    else:
        window = tashkent_now().replace(hour=WINDOW_HOUR, minute=0, second=0, microsecond=0)
        if window <= tashkent_now():
            window += dt.timedelta(days=1)
    pause_at = window - dt.timedelta(minutes=PAUSE_LEAD_MINUTES)

    if not opts.now:
        wait = (pause_at - tashkent_now()).total_seconds()
        if wait > 0:
            log(f"    waiting {wait / 60:.0f} min to pause at {pause_at:%H:%M} Tashkent")
            time.sleep(wait)

    log("1. Pausing the four public bots.")
    if not opts.dry_run:
        state.paused = True   # before the queueing: half a pause is still a pause to undo
        queue(db, admins, "pause", str(PAUSE_LEAD_MINUTES + MAX_WAIT_MINUTES))
        time.sleep(20)   # let each bot's bus poll pick it up

    # -- 3. wait for the window, and for quiet ------------------------------
    if not opts.now:
        wait = (window - tashkent_now()).total_seconds()
        if wait > 0:
            log(f"    waiting {wait / 60:.0f} min for the window at {window:%H:%M} Tashkent")
            time.sleep(wait)

    log(f"2. Window open. Waiting for {QUIET_MINUTES} quiet minutes.")
    deadline = time.monotonic() + MAX_WAIT_MINUTES * 60
    while True:
        busy = active_users(db, admins, QUIET_MINUTES)
        if not any(busy.values()):
            log(f"    nobody active in the last {QUIET_MINUTES} min. Clear to publish.")
            break
        log(f"    still active: { {b: n for b, n in busy.items() if n} } -- waiting")
        if time.monotonic() > deadline:
            log("ABORTING: somebody has been active throughout the whole window.")
            log("    Resuming the bots. Nothing was published.")
            if not opts.dry_run:
                resume_bots(state)
                tell(state, "warning",
                     f"Release {target} aborted: users were active throughout the "
                     f"{MAX_WAIT_MINUTES}-minute window. The bots have been resumed "
                     f"and nothing was published. Run it by hand when convenient.")
            return 2
        time.sleep(60)

    # -- 4. publish ---------------------------------------------------------
    log("3. Publishing.")
    not_ready = not_the_release(c_folder, target)
    if not_ready:
        log(f"    not the release: {not_ready}")
        if not opts.dry_run:
            log("ABORTING: resuming the bots. Nothing was published.")
            resume_bots(state)
            tell(state, "warning", f"Release {target} aborted before publishing: {not_ready}. "
                                   f"The bots have been resumed.")
            return 1
    state.stage = "publishing"
    published = (publish(c_folder, f"v{target}C", opts.dry_run) if c_folder
                 else publish(ROOT, f"v{target}", opts.dry_run))
    if not published:
        log("ABORTING: the publish step failed. Resuming the bots.")
        if not opts.dry_run:
            resume_bots(state)
            tell(state, "error",
                 f"Release {target} failed while publishing. The bots have been "
                 f"resumed and are still on their old version. See "
                 f"scripts/release.log.")
        return 1
    state.stage = "published"

    if opts.dry_run:
        log("DRY RUN: stopping here. Nothing was paused, published or resumed.")
        return 0

    # -- 5. wait for the new version to appear ------------------------------
    log(f"4. Waiting for every bot to report {target}.")
    deadline = time.monotonic() + HEARTBEAT_WAIT_MINUTES * 60
    while True:
        now = versions(db)
        behind = [b for b in BOTS if now.get(b) != target]
        if not behind:
            log("    all four are on the new version.")
            break
        if time.monotonic() > deadline:
            log(f"    gave up waiting; still behind: {behind}")
            log(f"    versions: {now}")
            break
        time.sleep(20)

    # -- 6. resume ----------------------------------------------------------
    log("5. Resuming, and telling everyone who was turned away.")
    resume_bots(state)
    time.sleep(20)

    final = versions(db)
    behind = [b for b in BOTS if final.get(b) != target]
    if behind:
        log(f"DONE, WITH A CAVEAT: {behind} did not report {target} in time.")
        tell(state, "warning",
             f"Release {target} published and the bots were resumed, but "
             f"{', '.join(behind)} had not reported the new version after "
             f"{HEARTBEAT_WAIT_MINUTES} minutes. Check /status.",
             details=str(final))
        return 3

    log(f"DONE. All four on {target}, paused and resumed cleanly.")
    tell(state, "info",
         f"Release {target} shipped at {window:%H:%M} Tashkent. All four public bots "
         f"were paused, published and resumed; nobody was active during the "
         f"window. /status to confirm.",
         details=str(final))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # a crash here must still be visible in the morning
        log(f"CRASHED: {type(exc).__name__}: {exc}")
        import traceback
        log(traceback.format_exc())
        raise SystemExit(1)
