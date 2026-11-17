# `routewatch.py`

[↑ downloader_bot](README.md) · [code](routewatch.py)

Once a day, ask every download route whether it still works -- and whether
yt-dlp is still current -- from inside the container, and tell the operator when
something is about to run out rather than after a user has hit it.

```text
WHY THIS EXISTS
    resolvers.py is a chain of other people's services, and the design assumes
    they die without telling anyone. `/providers` reports what real traffic has
    taught the bot, and `probe` asks every route on demand -- but both need
    somebody to look. On 2026-09-29 the question was how long the routes last;
    the honest answer was "until the service behind one dies or the datacenter
    address is refused, and nothing will say so". This is the thing that says
    so: a platform whose last working route has gone is heard about the same
    day, not when the first person pastes a link and gets DL-ALL-ROUTES-FAILED.
```

```text
WHAT IT DOES
    For each sample link in resolvers.SAMPLES it walks the platform's chain in
    order and stops at the first route that answers *and serves its media*
    (64 KB are pulled back, as `probe` does). That is one or two requests per
    platform, not a full survey -- and yt-dlp, the last route of every chain and
    the one that costs 40 MB to load, is only reached when everything ahead of
    it failed. The platform is then
```

```text
up           the first route works
last_resort  only the final route works: one more failure and it is down
down         nothing works
sample_gone  every route says the post is missing -- the sample was
             probably deleted, which is not an outage (see the note on
             SAMPLES in resolvers.py)
```

```text
and the operator hears about a CHANGE, not a state: down and last_resort are
sent when they begin, `down` again after REALERT_DOWN_S if it lasts, and a
recovery is recorded without a message. The unadvertised site is checked and recorded but never pushed: it is deliberately unadvertised, and "the site is refusing a datacenter" is its normal condition, not news.
```

```text
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
```

```text
Where it keeps its memory: family.settings, so a redeploy does not make it
forget what it already said ("routewatch:*" keys); without a database, in
this process.
```

### `_flag()`

A switch from the environment. A value that is neither a yes nor a no
means the default, never "on": `DBOT_YTDLP_AUTOUPDATE=disabled` must not
switch on code arriving from PyPI (review, 2026-10-07).

- `MIN_GAP_S = 20 * 3600` — a redeploy loop must not mean a probe per deploy

_At `REPLY_WITHIN_S = 75.0`:_

How long a forced run (the bus command) waits for the whole check before it
answers with what it has. ManagerBot calls a family command failed after 90 s.

_At `ALERT_PLATFORMS = ("instagram", "tiktok", "twitter", "pinterest")`:_

Platforms whose trouble is worth a message. The unadvertised site is not one of them.

_At `_lock = asyncio.Lock()`:_

Held for the whole of a run, by whoever started it (see run()).

_At `_ytdlp_lock = asyncio.Lock()`:_

At most one yt-dlp probe at a time, across the platforms that are checked
side by side. A yt-dlp extraction is tens of megabytes and a download
besides, and by default five bots share one 1 GB container: platforms
in parallel is for the ordinary routes, which are a request or two each.

_At `_background: set = set()`:_

The background run of a forced check that answered early (see run()). Held
here because the event loop keeps only a weak reference to a task, and a
check that is garbage-collected half way is a check that never finishes.

## The routes

### `check_platform()`

The chain's routes in order, stopping at the first that works. A route
that answers but whose media will not serve is a failure here: a link that
resolves to a dead CDN address helps nobody.

- At `if name.startswith("ytdlp:"):`:
  yt-dlp gets what it gets in real use (load, extract, download): at the
  probe's 12 s a slow but working yt-dlp read as "down" and woke the operator.
- At `async with _ytdlp_lock:`:
  Waiting for the lock is not part of the route's time: the clock
  inside probe_route starts once it is held.

### `transition()`

What a platform's new state means, given what it was.

Returns (new record, alert), alert being None or (level, key). Pure, so the
rules for when the operator is bothered are testable without a network.

- `old_state, since, alerted = UP, now, 0.0` — first sight: only trouble is news

## yt-dlp

### `version_key()`

'2026.08.19' and '2026.8.19' are the same release; a '.1' tail is a
later one the same day.

### `_installed_ytdlp()`

- `import video` — the version a download would use, without importing yt-dlp

### `latest_ytdlp()`

(version, when it was published) of yt-dlp's newest release, or None if
PyPI did not say. Asked through net.py, like every request this bot
makes, so it too can only reach the public internet.

PyPI, not GitHub's releases API: that one allows 60 requests an hour per
address without a login, a datacenter address is shared with every other
tenant of the host, and a refusal was logged at INFO, where nobody looks.
The version is what pip would install
-- which is what update_ytdlp installs, so the two cannot disagree -- and
it was published when the first of its files was uploaded.

- At `uploads = [datetime.fromisoformat(str(f["upload_time_iso_8601"]).replace("Z", "+00:00"))`:
  "urls" are the files of that version. None, and there is nothing to
  date it by (a release with its files still going up, or all yanked).
- At `uploads = [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in uploads]`:
  PyPI writes UTC with a "Z"; a time without a zone would make the age
  arithmetic in ytdlp_verdict fail, so one is given the zone it means.

### `ytdlp_verdict()`

Pure: is the container behind, by how much, and is the release old
enough to take (it has been out YTDLP_COOLDOWN_DAYS).

### `update_ytdlp()`

Fetch yt-dlp `tag` from PyPI into a folder of its own and make every
later download use it. Returns (done, what happened).

Deliberately not `pip install -U` into the image's site-packages: that
would need the container's filesystem to be writable and trusted, and would
leave a half-upgraded package behind if it failed midway. A separate folder
is either complete and verified or deleted, and the old copy stays
importable until the swap. --no-deps because yt-dlp's own extras (the JS
challenge solver, curl_cffi) were installed with the image and do not move
with a release; --only-binary because yt-dlp is a universal wheel and
nothing should run a setup script on the way in.

- At `probe = ("import sys; sys.path.insert(0, sys.argv[1]); "`:
  Does what was fetched import, and is it the release that was asked for?
  Checked in a child process, so a broken package cannot take this one with it.

### `check_ytdlp()`

Compare, say so once, and update if allowed. Returns a line for the report.

- At `return line + f" Waiting {YTDLP_COOLDOWN_DAYS} days before taking it."`:
  Waiting out the cooldown; it will be taken on a later run.
- At `already = await asyncio.to_thread(family_link.get_setting, _YTDLP_ALERTED_KEY)`:
  Once per release, not once a day.

## One run, and when to run

### `_Progress`

What a run has finished so far, for a forced run that has to answer
before the run does (see run()).

#### `_Progress.__init__()`

- `self.order = list(resolvers.SAMPLES)` — the order the report lists them in
- `self.waiting = list(self.order)` — platforms not finished
- `self.blocks: dict = {}` — platform -> its lines of the report

#### `_Progress.partial()`

- At `names = ", ".join(self.waiting) or "yt-dlp's newest release"`:
  With every platform done, what is left is the yt-dlp comparison.

### `_check()`

The run itself. The caller holds \_lock.

- At `results = await asyncio.gather(*(one(p, u) for p, u in samples.items()), return_exceptio …`:
  Side by side, as resolvers.probe_all does it: one after another, five
  platforms took as long as the sum of their chains, and a chain that
  ends in yt-dlp is 90 s on its own. (check_platform lets one yt-dlp
  route at a time through, which is what keeps this inside the memory
  five bots share.) Every platform is finished before any failure is
  raised, so one that broke does not leave the others half recorded.

### `_check_logged()`

\_check as a task of its own. Nobody may be waiting for it any more
(run() answered early), so a failure is logged here, not left to a caller.

### `_background_done()`

However the run ended -- and even if it was cancelled before it began,
when its own code would never have run -- the lock it was started holding
is let go.

- `task.exception()` — read, so that asyncio does not complain it never was

### `run()`

The whole check. Returns the report as text (the bus command shows it).

Without `force` it does nothing if a run happened in the last MIN_GAP_S.

A forced run is asked for by a person, over a bus that gives up after 90
seconds, and a check can take longer than that (yt-dlp alone is allowed
90). With `reply_within` it answers at that many seconds if it is not
finished: the platforms done so far, and which are still being asked. The
run goes on in a task of its own, holding the lock, recording every state
and sending every alert as it would have -- only the person has stopped
waiting. A forced run asked for meanwhile is told so at once, rather than
queued behind it to be answered after the 90 seconds are gone.

### `schedule()`

Called from \_post_init. The timer ticks every few hours and run() decides
whether a day has passed, so a redeploy loop neither skips a day nor probes
on every deploy.
