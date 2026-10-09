"""Once a day, ask every download route whether it still works -- and whether
yt-dlp is still current -- from inside the container, and tell the owner when
something is about to run out rather than after a user has hit it.

WHY THIS EXISTS
    resolvers.py is a chain of other people's services, and the design assumes
    they die without telling anyone. `/providers` reports what real traffic has
    taught the bot, and `probe` asks every route on demand -- but both need
    somebody to look. The owner, 2026-09-29, asked how long the routes last;
    the honest answer was "until the service behind one dies or the datacenter
    address is refused, and nothing will say so". This is the thing that says
    so: a platform whose last working route has gone is heard about the same
    day, not when the first person pastes a link and gets DL-ALL-ROUTES-FAILED.

WHAT IT DOES
    For each sample link in resolvers.SAMPLES it walks the platform's chain in
    order and stops at the first route that answers *and serves its media*
    (64 KB are pulled back, as `probe` does). That is one or two requests per
    platform, not a full survey -- and yt-dlp, the last route of every chain and
    the one that costs 40 MB to load, is only reached when everything ahead of
    it failed. The platform is then

        up           the first route works
        last_resort  only the final route works: one more failure and it is down
        down         nothing works
        sample_gone  every route says the post is missing -- the sample was
                     probably deleted, which is not an outage (see the note on
                     SAMPLES in resolvers.py)

    and the owner hears about a CHANGE, not a state: down and last_resort are
    sent when they begin, `down` again after REALERT_DOWN_S if it lasts, and a
    recovery is recorded without a message. YouTube is checked and recorded but
    never pushed: it is deliberately unadvertised, and "YouTube is refusing a
    datacenter" is its normal condition, not news.

    Separately it asks PyPI for yt-dlp's newest release (not GitHub: that API
    allows an address without a login 60 requests an hour, and a cloud host's
    address is shared with everybody else on it). yt-dlp is the safety
    net under every platform and the only route for some, and it rots as the
    sites change -- the image holds whatever version its last build installed,
    and Docker's cached `pip install` layer may keep it there across deploys.
    A release older than YTDLP_STALE_DAYS that the container has not caught up
    with is reported once per release. With DBOT_YTDLP_AUTOUPDATE=1 the bot
    fetches it itself (see update_ytdlp), after the release has had
    YTDLP_COOLDOWN_DAYS to show a regression -- which is also the window in
    which a hijacked release would be noticed by somebody else first.

    Where it keeps its memory: family.settings, so a redeploy does not make it
    forget what it already said ("routewatch:*" keys); without a database, in
    this process.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import family_link
import net
import resolvers

logger = logging.getLogger(__name__)


_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off", "")


def _flag(name: str, default: str) -> bool:
    """A switch from the environment. A value that is neither a yes nor a no
    means the default, never "on": `DBOT_YTDLP_AUTOUPDATE=disabled` must not
    switch on code arriving from PyPI (review, 2026-10-07)."""
    value = os.environ.get(name, default).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default.strip().lower() in _TRUE


ENABLED = _flag("DBOT_ROUTEWATCH", "1")
FIRST_RUN_S = float(os.environ.get("DBOT_ROUTEWATCH_FIRST_S", "600"))
TICK_S = 6 * 3600
MIN_GAP_S = 20 * 3600            # a redeploy loop must not mean a probe per deploy
PROBE_TIMEOUT_S = float(os.environ.get("DBOT_PROBE_TIMEOUT", "12"))
REALERT_DOWN_S = 3 * 86400
YTDLP_STALE_DAYS = int(os.environ.get("DBOT_YTDLP_STALE_DAYS", "14"))
YTDLP_COOLDOWN_DAYS = int(os.environ.get("DBOT_YTDLP_COOLDOWN_DAYS", "3"))
AUTOUPDATE = _flag("DBOT_YTDLP_AUTOUPDATE", "0")
PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
# How long a forced run (the bus command) waits for the whole check before it
# answers with what it has. ManagerBot calls a family command failed after 90 s.
REPLY_WITHIN_S = 75.0

# Platforms whose trouble is worth a message. YouTube is not one of them.
ALERT_PLATFORMS = ("instagram", "tiktok", "twitter", "pinterest")

UP, LAST_RESORT, DOWN, SAMPLE_GONE = "up", "last_resort", "down", "sample_gone"
_KEY = "routewatch:state:"
_LAST_RUN_KEY = "routewatch:last_run"
_YTDLP_ALERTED_KEY = "routewatch:ytdlp_alerted"

# Held for the whole of a run, by whoever started it (see run()).
_lock = asyncio.Lock()
# At most one yt-dlp probe at a time, across the platforms that are checked
# side by side. A yt-dlp extraction is tens of megabytes and a download
# besides, and the C edition runs five bots in one 1 GB container: platforms
# in parallel is for the ordinary routes, which are a request or two each.
_ytdlp_lock = asyncio.Lock()

# The background run of a forced check that answered early (see run()). Held
# here because the event loop keeps only a weak reference to a task, and a
# check that is garbage-collected half way is a check that never finishes.
_background: set = set()

STILL_RUNNING = "A route check is already running; /nightly shows the states once it is done."


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------

async def check_platform(platform: str, url: str, timeout: float = PROBE_TIMEOUT_S) -> list[dict]:
    """The chain's routes in order, stopping at the first that works. A route
    that answers but whose media will not serve is a failure here: a link that
    resolves to a dead CDN address helps nobody."""
    rows: list[dict] = []
    for name, fn in resolvers.PROVIDERS.get(platform, []):
        # yt-dlp gets what it gets in real use (load, extract, download): at the
        # probe's 12 s a slow but working yt-dlp read as "down" and woke the owner.
        if name.startswith("ytdlp:"):
            allowed = max(timeout, resolvers._timeout_for(name))
            # Waiting for the lock is not part of the route's time: the clock
            # inside probe_route starts once it is held.
            async with _ytdlp_lock:
                row = await resolvers.probe_route(name, fn, url, fetch=True, timeout=allowed)
        else:
            row = await resolvers.probe_route(name, fn, url, fetch=True, timeout=timeout)
        fetched = str(row.get("fetched") or "")
        if row["ok"] and fetched.startswith(("fetch failed", "file gone")):
            row["ok"] = False
            row["detail"] = f"resolved, but its media did not serve: {fetched}"
        rows.append(row)
        if row["ok"]:
            break
    return rows


def classify(rows: list[dict], chain_length: int) -> str:
    working = [i for i, row in enumerate(rows) if row["ok"]]
    if working:
        return LAST_RESORT if chain_length > 1 and working[0] == chain_length - 1 else UP
    if rows and all(row["detail"].startswith("missing") for row in rows):
        return SAMPLE_GONE
    return DOWN


def _parse(record: "str | None") -> "tuple[str, float, float] | None":
    try:
        state, since, alerted = (record or "").split("|")
        return state, float(since), float(alerted)
    except ValueError:
        return None


def transition(record: "str | None", new_state: str, now: float):
    """What a platform's new state means, given what it was.

    Returns (new record, alert), alert being None or (level, key). Pure, so the
    rules for when the owner is bothered are testable without a network."""
    old = _parse(record)
    if old is None:
        old_state, since, alerted = UP, now, 0.0       # first sight: only trouble is news
    else:
        old_state, since, alerted = old
    alert = None
    if new_state == old_state:
        if new_state == DOWN and now - alerted >= REALERT_DOWN_S:
            alert, alerted = ("error", "still_down"), now
    else:
        since = now
        if new_state == DOWN:
            alert, alerted = ("error", "down"), now
        elif new_state == LAST_RESORT:
            alert, alerted = ("warning", "last_resort"), now
        elif new_state == SAMPLE_GONE:
            alert, alerted = ("warning", "sample_gone"), now
        elif old_state in (DOWN, LAST_RESORT):
            alert = ("info", "recovered")
    return f"{new_state}|{since:.0f}|{alerted:.0f}", alert


def _lines(rows: list[dict]) -> list[str]:
    return [f"{'ok  ' if row['ok'] else 'FAIL'} {row['provider']}: {resolvers.tidy_error(row['detail'])}"
            for row in rows]


def _message(platform: str, key: str, rows: list[dict], since_days: float) -> str:
    name = platform.capitalize()
    if key == "down":
        return (f"No download route works for {name}, asked from inside the container. "
                f"People who paste a {name} link get DL-ALL-ROUTES-FAILED until one comes back.")
    if key == "still_down":
        return f"{name} still has no working download route -- {since_days:.0f} days now."
    if key == "last_resort":
        return (f"{name} is down to its last download route ({rows[-1]['provider']}); "
                "the ones ahead of it failed. One more failure and it is down.")
    if key == "sample_gone":
        return (f"Every {name} route says the sample link is gone. Probably the post was "
                "deleted: replace it in resolvers.SAMPLES.")
    return f"{name} has a working download route again."


# ---------------------------------------------------------------------------
# yt-dlp
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.(\d+))?")


def version_key(text: "str | None") -> "tuple | None":
    """'2026.08.19' and '2026.8.19' are the same release; a '.1' tail is a
    later one the same day."""
    m = _VERSION_RE.search(text or "")
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def _installed_ytdlp() -> "str | None":
    try:
        import video       # the version a download would use, without importing yt-dlp
        found = video.ytdlp_version()
    except Exception:
        found = None
    if found:
        return found
    try:
        from importlib import metadata
        return metadata.version("yt-dlp")
    except Exception:
        return None


async def latest_ytdlp() -> "tuple[str, datetime] | None":
    """(version, when it was published) of yt-dlp's newest release, or None if
    PyPI did not say. Asked through net.py, like every request this bot
    makes, so it too can only reach the public internet.

    PyPI, not GitHub's releases API: that one allows 60 requests an hour per
    address without a login, a datacenter address is shared with every other
    tenant of the host, and a refusal was logged at INFO, where nobody looks.
    The version is what pip would install
    -- which is what update_ytdlp installs, so the two cannot disagree -- and
    it was published when the first of its files was uploaded."""
    try:
        raw = await net.fetch_bytes(PYPI_JSON, limit=8 * 1024 * 1024)
        data = json.loads(raw)
        tag = str(data["info"]["version"])
        # "urls" are the files of that version. None, and there is nothing to
        # date it by (a release with its files still going up, or all yanked).
        uploads = [datetime.fromisoformat(str(f["upload_time_iso_8601"]).replace("Z", "+00:00"))
                   for f in data.get("urls") or []]
        # PyPI writes UTC with a "Z"; a time without a zone would make the age
        # arithmetic in ytdlp_verdict fail, so one is given the zone it means.
        uploads = [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in uploads]
        if not uploads:
            return None
        return tag, min(uploads)
    except Exception as exc:
        logger.warning("Could not ask PyPI for yt-dlp's newest release: %s: %s", type(exc).__name__, exc)
        return None


def ytdlp_verdict(installed: "str | None", latest_tag: str, published: datetime,
                  now: "datetime | None" = None) -> dict:
    """Pure: is the container behind, by how much, and is the release old
    enough to take (it has been out YTDLP_COOLDOWN_DAYS)."""
    now = now or datetime.now(timezone.utc)
    have, want = version_key(installed), version_key(latest_tag)
    behind = bool(have and want and have < want)
    gap_days = 0
    if behind:
        gap_days = (datetime(*want[:3], tzinfo=timezone.utc) - datetime(*have[:3], tzinfo=timezone.utc)).days
    age_days = (now - published).total_seconds() / 86400
    return {"behind": behind, "gap_days": gap_days, "age_days": age_days,
            "stale": behind and gap_days >= YTDLP_STALE_DAYS,
            "cooled": age_days >= YTDLP_COOLDOWN_DAYS}


async def update_ytdlp(tag: str) -> "tuple[bool, str]":
    """Fetch yt-dlp `tag` from PyPI into a folder of its own and make every
    later download use it. Returns (done, what happened).

    Deliberately not `pip install -U` into the image's site-packages: that
    would need the container's filesystem to be writable and trusted, and would
    leave a half-upgraded package behind if it failed midway. A separate folder
    is either complete and verified or deleted, and the old copy stays
    importable until the swap. --no-deps because yt-dlp's own extras (the JS
    challenge solver, curl_cffi) were installed with the image and do not move
    with a release; --only-binary because yt-dlp is a universal wheel and
    nothing should run a setup script on the way in."""
    version = tag.lstrip("v")
    base = Path(os.environ.get("DBOT_YTDLP_DIR") or tempfile.gettempdir())
    target = base / f"yt-dlp-{version}"
    if not (target / "yt_dlp" / "version.py").is_file():
        cmd = [sys.executable, "-m", "pip", "install", "--no-deps", "--only-binary=:all:",
               "--disable-pip-version-check", "--no-input", "--quiet",
               "--target", str(target), f"yt-dlp=={version}"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return False, "pip did not finish in 5 minutes"
        except OSError as exc:
            return False, f"pip could not be started: {exc}"
        if proc.returncode != 0:
            return False, "pip failed: " + resolvers.tidy_error(out.decode("utf-8", "replace")[-400:])
    # Does what was fetched import, and is it the release that was asked for?
    # Checked in a child process, so a broken package cannot take this one with it.
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
             "import yt_dlp, yt_dlp.version as v; print(v.__version__)")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", probe, str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    except (asyncio.TimeoutError, OSError) as exc:
        return False, f"the new yt-dlp could not be tried: {type(exc).__name__}"
    got = out.decode().strip()
    if proc.returncode != 0 or version_key(got) != version_key(version):
        return False, "the new yt-dlp did not import as " + version + ": " + \
            resolvers.tidy_error((err or out).decode("utf-8", "replace")[-300:])
    import video
    swapped = await asyncio.to_thread(video.use_ytdlp_from, str(target))
    if not swapped:
        return False, "downloads were still running after 5 minutes; will try again"
    return True, got


async def check_ytdlp() -> "str | None":
    """Compare, say so once, and update if allowed. Returns a line for the report."""
    latest = await latest_ytdlp()
    if latest is None:
        return None
    tag, published = latest
    installed = _installed_ytdlp()
    verdict = ytdlp_verdict(installed, tag, published)
    if not verdict["behind"]:
        return f"yt-dlp {installed} is current ({tag})."
    line = (f"yt-dlp {installed} is {verdict['gap_days']} days behind {tag} "
            f"(released {verdict['age_days']:.0f} days ago).")
    if AUTOUPDATE and verdict["cooled"]:
        done, what = await update_ytdlp(tag)
        if done:
            family_link.report_event_soon("info", "ytdlp", f"DownloaderBot updated yt-dlp {installed} -> {what}.")
            return line + f" Updated to {what}."
        line += f" Update failed: {what}."
        problem = f"The automatic yt-dlp update to {tag} failed: {what}"
    elif AUTOUPDATE:
        # Waiting out the cooldown; it will be taken on a later run.
        return line + f" Waiting {YTDLP_COOLDOWN_DAYS} days before taking it."
    elif verdict["stale"]:
        problem = (f"yt-dlp in the container is {installed}; {tag} has been out {verdict['age_days']:.0f} "
                   "days. TikTok, Instagram and YouTube extractors break between releases. Redeploy "
                   "without the build cache, or set DBOT_YTDLP_AUTOUPDATE=1.")
    else:
        return line
    # Once per release, not once a day.
    already = await asyncio.to_thread(family_link.get_setting, _YTDLP_ALERTED_KEY)
    if already != tag:
        await asyncio.to_thread(family_link.set_setting, _YTDLP_ALERTED_KEY, tag)
        family_link.report_event_soon("warning", "ytdlp", problem)
    return line


# ---------------------------------------------------------------------------
# One run, and when to run
# ---------------------------------------------------------------------------

def _header() -> str:
    return ("Download routes, asked from inside this container "
            f"({os.environ.get('RAILWAY_ENVIRONMENT') or 'local'}):")


class _Progress:
    """What a run has finished so far, for a forced run that has to answer
    before the run does (see run())."""

    def __init__(self):
        self.order = list(resolvers.SAMPLES)             # the order the report lists them in
        self.waiting = list(self.order)                  # platforms not finished
        self.blocks: dict = {}                           # platform -> its lines of the report

    def partial(self) -> str:
        lines = [_header()]
        for platform in self.order:
            lines += self.blocks.get(platform, [])
        # With every platform done, what is left is the yt-dlp comparison.
        names = ", ".join(self.waiting) or "yt-dlp's newest release"
        lines.append(f"Still checking: {names}. The check carries on; any change is alerted "
                     "as usual, and /nightly shows the states once it is done.")
        return "\n".join(lines)


async def _check(force: bool, progress: _Progress) -> str:
    """The run itself. The caller holds _lock."""
    now = time.time()
    if not force:
        last = await asyncio.to_thread(family_link.get_setting, _LAST_RUN_KEY)
        try:
            if last and now - float(last) < MIN_GAP_S:
                return "skipped: checked less than 20 hours ago"
        except ValueError:
            pass
    await asyncio.to_thread(family_link.set_setting, _LAST_RUN_KEY, f"{now:.0f}")
    samples = dict(resolvers.SAMPLES)
    progress.order, progress.waiting = list(samples), list(samples)

    async def one(platform: str, url: str) -> None:
        try:
            chain = resolvers.PROVIDERS.get(platform, [])
            try:
                rows = await check_platform(platform, url)
            except Exception:
                logger.warning("route check for %s failed", platform, exc_info=True)
                return
            state = classify(rows, len(chain))
            key = _KEY + platform
            old = await asyncio.to_thread(family_link.get_setting, key)
            record, alert = transition(old, state, now)
            await asyncio.to_thread(family_link.set_setting, key, record)
            progress.blocks[platform] = [f"{platform}: {state}"] + ["  " + line for line in _lines(rows)]
            if alert and platform in ALERT_PLATFORMS:
                level, what = alert
                since = (now - (_parse(record) or ("", now, 0))[1]) / 86400
                family_link.report_event_soon(level, "routes", _message(platform, what, rows, since),
                                              "\n".join(_lines(rows)))
        finally:
            progress.waiting.remove(platform)

    # Side by side, as resolvers.probe_all does it: one after another, five
    # platforms took as long as the sum of their chains, and a chain that
    # ends in yt-dlp is 90 s on its own. (check_platform lets one yt-dlp
    # route at a time through, which is what keeps this inside the memory
    # five bots share.) Every platform is finished before any failure is
    # raised, so one that broke does not leave the others half recorded.
    results = await asyncio.gather(*(one(p, u) for p, u in samples.items()), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    report = [_header()]
    for platform in progress.order:
        report += progress.blocks.get(platform, [])
    line = await check_ytdlp()
    if line:
        report.append(line)
    text = "\n".join(report)
    logger.info("%s", text)
    return text


async def _check_logged(force: bool, progress: _Progress) -> str:
    """_check as a task of its own. Nobody may be waiting for it any more
    (run() answered early), so a failure is logged here, not left to a caller."""
    try:
        return await _check(force, progress)
    except Exception:
        logger.warning("the route check failed", exc_info=True)
        raise


def _background_done(task) -> None:
    """However the run ended -- and even if it was cancelled before it began,
    when its own code would never have run -- the lock it was started holding
    is let go."""
    _background.discard(task)
    _lock.release()
    if not task.cancelled():
        task.exception()            # read, so that asyncio does not complain it never was


async def run(force: bool = False, reply_within: "float | None" = None) -> str:
    """The whole check. Returns the report as text (the bus command shows it).

    Without `force` it does nothing if a run happened in the last MIN_GAP_S.

    A forced run is asked for by a person, over a bus that gives up after 90
    seconds, and a check can take longer than that (yt-dlp alone is allowed
    90). With `reply_within` it answers at that many seconds if it is not
    finished: the platforms done so far, and which are still being asked. The
    run goes on in a task of its own, holding the lock, recording every state
    and sending every alert as it would have -- only the person has stopped
    waiting. A forced run asked for meanwhile is told so at once, rather than
    queued behind it to be answered after the 90 seconds are gone."""
    if force and _lock.locked():
        return STILL_RUNNING
    progress = _Progress()
    if reply_within is None:
        async with _lock:
            return await _check(force, progress)
    await _lock.acquire()
    task = asyncio.ensure_future(_check_logged(force, progress))
    _background.add(task)
    task.add_done_callback(_background_done)
    done, _ = await asyncio.wait({task}, timeout=reply_within)
    if task in done:
        return task.result()
    return progress.partial()


async def _tick(_context=None) -> None:
    try:
        await run()
    except Exception:
        logger.warning("the daily route check failed", exc_info=True)


def schedule(application) -> None:
    """Called from _post_init. The timer ticks every few hours and run() decides
    whether a day has passed, so a redeploy loop neither skips a day nor probes
    on every deploy."""
    if not ENABLED or application.job_queue is None:
        return
    application.job_queue.run_repeating(_tick, interval=TICK_S, first=FIRST_RUN_S)
