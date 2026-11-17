# `main.py`

[↑ MumuBots](README.md) · [code](main.py)

Every bot in the family: in one process (the default), or one process each.

```text
python main.py                  run every bot that has a token set, in this one process
python main.py sticker anon     run only these (same as BOTS=sticker,anon)
python main.py --separate       run each bot in a process of its own (same as
                                MUMU_PROCESSES=separate); words pick the bots here too
python main.py --check          load every bot, prove they are kept apart, run one conversion, exit
python main.py --list           which bots exist and which have a token
python main.py --module KEY ... run one module as a program of its own (the conversion
                                worker and the big-file transfer are started this way)
```

Each bot is loaded as a package of its own, so `import db` inside it means its
own db. The code lives in bots/: bots/convert_bot/jobs.py is ConvertBot's `jobs`
(key `convert_bot.jobs`), and shared/family_link.py (key `family_link`) is
a module every bot gets a copy of. index() finds them; a bot's modules are its
own folder's plus the shared ones, and only the ones a bot imports are ever
compiled.

With --separate this process is only a supervisor: it loads no bot code, starts
`main.py <bot>` once per bot, restarts one that fails (after 5 s, then longer,
up to 5 minutes) while the others carry on, and passes a stop on to all of them.

_At `DRAIN_SECONDS = float(os.environ.get("UNIFIED_DRAIN_SECONDS") or 90)`:_

How long a stop signal waits for the bots before the process ends anyway. Each bot waits up to
DEPLOY_SIGTERM_WAIT_SECONDS (60) for its work in progress first, and the host kills the process
after its draining window (railway.json: 120), so this sits between the two.

_At `FAMILY_PROCESS = sys.modules.setdefault("family_process", types.ModuleType("family_proce …`:_

The exit code a soft stop asks for (lifecycle.soft_stop): 0 stays down under an on-failure restart
policy, 1 is started again. A module of its own so every bot's lifecycle -- each a copy -- sees one.

_At `NOT_FOR = {"manager_bot": {"shared_features"}}`:_

ManagerBot is private: no donations, no /cancel, no translations.

### `index()`

Every module's key and file: shared/x.py (or shared/security/x.py) is `x`, bots/&lt;bot>\_bot/x.py is `<bot>_bot.x`.
Each bot gets its own copy of every module it uses (see \_BotFinder), so the keys are not
importable on their own.

### `Bot`

- At `executor: ThreadPoolExecutor | None = None`:
  Kept apart per bot in one process: the threads its blocking work runs on,
  the asyncio tasks it started (so a restart can end them all), and how often
  in a row it has failed to load or start (how long the next try waits).

#### `Bot.read_own_env_file()`

Run from the development tree, each bot folder has a .env of its own, and what is in it is that bot's alone -- including the names that would otherwise be ignored process-wide, like DB_SCHEMA.

### `discover()`

The five bots. data/&lt;folder>/ is each one's home: its logs, and its own .env if it has one.

_Notes on keeping secrets and keys out of reach are kept with the private notes; the public summary is [shared/security/README.md](shared/security/README.md)._

### `setting()`

The value of `key` as `bot` sees it, or None. Empty counts as unset.

### `BotEnviron`

os.environ as one bot sees it.

### `_BotOs`

`os` as a bot's modules see it: the real one, except its environment.

#### `_BotOs._exit()`

os.\_exit(), as a bot in the family means it: ManagerBot's /restart
ends the bot's process with 1 so the host starts it again. Here that
would end all five, so a running family restarts this bot alone (code
0: stops it) and the call returns to its caller, whose last line it is.
With no family running (--check, a worker started with --module) it is
the real os.\_exit.

### `_BotFinder`

Finds `<bot>.<module>` for the packages registered below, and nothing else, so no other import in the process can be affected by it.

### `_ModuleLoader`

One file under bots/, as that bot's own copy of the module.

### `_BotImportlib`

`importlib` as a bot's modules see it.

### `bot_environment()`

Whatever a bot writes into the real environment while it loads -- its own load_dotenv() does, in the development tree -- is undone afterwards, so the next bot does not inherit it.

- At `for key in [key for key in os.environ if key not in saved]:`:
  Put back only what changed: a bot may be loading again while the
  others run and read the environment, so it is never emptied, even
  for a moment.

### `own_module()`

A shared module that needs nothing but the standard library (redact, sandbox), loaded
once for main.py's own use; the bots get their own copies as usual.

### `_install_log_tagging()`

Stamp every record with the bot it came from: by logger name when the logger belongs to a bot package, otherwise by the bot whose task or thread wrote it -- which is how python-telegram-bot's and psycopg's own lines get attributed.

### `adopting_handlers()`

While a bot loads, every handler it attaches to the root or `problems` logger is filtered down to that bot's own records -- so its /logs and its log files are its own -- and its console handler is dropped, because this process already prints one line per record for everybody.

### `setup_logging()`

Information to stdout, warnings and errors to stderr: hosts like Railway mark a line's level by the stream it came on, so this is what makes an error show as an error in their log viewer.

### `preflight()`

Everything wrong with the settings these bots depend on, or None.

### `cap_malloc_arenas()`

glibc gives a threaded process up to 8 x cpu_count malloc arenas, and cpu_count in a container is the host's.

### `give_back_memory()`

Return freed heap pages to the kernel.

### `_capture_run_polling()`

Each bot's main() ends in app.run_polling(), which would take the event loop for itself and never return.

### `load()`

Import the bot and run its main() as far as run_polling.

_At `def _will_run(bot: Bot) -> bool:`:_

Until 2.2.0 two things ended the whole process here: the data key guard
refusing any bot, and ManagerBot failing to load. Neither does now (2026-10-23: a part that is not working must not affect the rest of the bots). A wrong or missing key switches off what needs it
(family_link), and a bot that fails is tried again on its own (Family.retry).

### `_will_run()`

Running, or to be tried again: not a bot that ended for good.

### `memory_reporter()`

The one bot that records the service's memory (since 2.2.0 the container's,
its workers included: the `resources` module), for ManagerBot's /usage and
the memory alarm, so that /usage counts it once: ManagerBot when this service
runs it -- also while it waits to be tried again, so that two bots never both
report -- else the first bot that loaded. In a bot's own process
(MUMU_PROCESSES=separate) the same rule over the whole service, MUMU_SERVICE_BOTS:
None when the reporter is another process.

_At `_FAMILY: list = []`:_

The running family, while there is one: how a bot's os.\_exit() reaches it.

_At `THREADS_PER_BOT = int(os.environ.get("WORKER_THREADS_PER_BOT") or 16)`:_

Threads for each bot's blocking work (database, file I/O, yt-dlp, the album
PDF): a pool of its own per bot since 2.2.0, so one bot's stuck threads --
a dead network, a slow download -- cannot hold up another bot's database
calls. 2.1.0 shared 16 between all five; on 2026-10-09, with 8, every bot's
once-a-second database job was skipped for a minute and a half. An idle
thread costs next to nothing, and threads are only made as work needs them:
16, since DownloaderBot may run up to 8 downloads (each holds a thread for
yt-dlp) on a large service, and its database calls still need some.

_At `NOTHING_RUNNING_SECONDS = float(os.environ.get("UNIFIED_NOTHING_RUNNING_SECONDS") or 900)`:_

When not one bot has been running for this long -- each failing and waiting
for its next try -- the process ends non-zero, so the host starts it afresh.

_At `TEARDOWN_STEP_SECONDS = float(os.environ.get("UNIFIED_TEARDOWN_STEP_SECONDS") or 60)`:_

The longest one step of stopping a bot (its updater, app.stop(), post_stop,
shutdown, post_shutdown) may take before the next step goes ahead without it.

### `Family`

The bots of one process, on one event loop.

Each is started, stopped and -- when it fails to load or to start, or when
ManagerBot's /restart asks -- loaded again from its files on its own,
after RESTART_DELAYS, while the others carry on. Nothing one bot does ends
the others: the process ends when it is told to stop, when every bot has
stopped, or when none has run for NOTHING_RUNNING_SECONDS.

#### `Family.on_stop_running()`

Application.stop_running(), as this process understands it.

#### `Family._initialize()`

app.initialize() is where the bot first talks to Telegram and reads its saved state.

#### `Family.start()`

- At `again = type(exc).__name__ != "InvalidToken" and not self.stopping`:
  A token Telegram rejects stays rejected until the settings change,
  which takes a redeploy: that one is not tried again.

#### `Family.teardown()`

Everything run_polling does on its way out, in the same order.

- At `await asyncio.wait_for(step(), TEARDOWN_STEP_SECONDS)`:
  Bounded: app.stop() waits for every handler still running, and one
  stuck on a dead connection would hold a restart, or the process's
  end, for ever.

#### `Family.end_if_nothing_left()`

No bot running, starting, loading or waiting for its next try: end
non-zero, so the host starts the process again.

- At `def retry(self, bot: Bot) -> None:`:
  --- one bot again, alone ----------------------------------------------------

#### `Family.retry()`

Load and start `bot` again after a while: 5 s after its first failure in
a row, twice as long each time after that, at most 5 minutes.

#### `Family._reload_now()`

- At `self.loop.create_task(self.reload(bot), context=contextvars.Context())`:
  In a context of its own, so the reload is not counted as one of the bot's own tasks.

#### `Family.request_restart()`

What a bot's os.\_exit(code) means in this process: 0 stops that bot,
anything else restarts it (ManagerBot's /restart). Never the others.

- At `log.info("%s is already restarting.", bot.name)`:
  /restart twice, or the bus delivering it twice: one restart is the
  answer to both. Two at once would tear down and load one bot twice
  over, on two threads.

#### `Family.reload()`

Load the bot again from its files, from scratch, and start it.

#### `Family._load_again()`

On a worker thread: the bot's old modules go, and new ones are made from
its files, while the other bots keep running on the event loop.

#### `Family.launch()`

Start a loaded bot in a context, with threads, of its own.

- At `bot.state = "starting"`:
  "starting" from now, not from when the task first runs: a bot that fails
  at once must not find the others still "loaded" and conclude that
  nothing is left.

#### `Family.end_tasks()`

Cancel every task the bot started (its jobs, pumps, pending sends), so
nothing of the old copy is left running beside the new one.

- At `def _take_executor(self) -> None:`:
  --- what the bots share, kept apart ---------------------------------------------

#### `Family._take_executor()`

Blocking work goes to the threads of the bot that asked: asyncio.to_thread()
and run_in_executor(None, ...) look at which bot's context they run in. Work
that is no bot's (a reload, the family's own) has a small pool of its own.

#### `Family._track_tasks()`

Every task is noted against the bot that started it (by the context it
starts in), so a restart can end that bot's tasks and no other's.

#### `Family.run()`

- At `unfinished = [bot.task for bot in self.bots if bot.task is not None and not bot.task.don …`:
  A bot still starting -- Telegram not answering, say -- is stopped on
  every way out, not only on a signal (start() tears it down itself):
  otherwise the wait below never ends, and a process that meant to end
  with 1 so the host starts it afresh stays up with nothing polling.
- At `self.shared_executor.shutdown(wait=False, cancel_futures=True)`:
  asyncio.run() joins the default pool when this returns, with no limit
  (Python 3.11): a reload still loading must not hold the end.

### `patch_python_telegram_bot()`

The two places where python-telegram-bot assumes it owns the process.

### `derive_siblings()`

SIBLING_BOTS -- how the public bots point at each other -- is nothing but their usernames, which are already set once each.

### `_worker_problem()`

One real conversion through the worker process, the way jobs.py starts it.

### `_probe_problem()`

DownloaderBot's video measuring on a real clip: a 64x36, two-frame video made
with the ffmpeg on this machine must come back from `video.probe` as 64 wide and
36 high. It proves the ffprobe arguments work on the ffmpeg the image installs.

### `check()`

Load every bot without starting any, and prove each got its own modules.

- At `if os.environ.get("TESSDATA_PREFIX"):`:
  Text recognition reads a line of English and a line of Russian from a page
  it draws, so an image without the language data, or with a PyMuPDF that has
  no engine in it, fails the build here and not on somebody's scan. The
  Dockerfile sets TESSDATA_PREFIX; a checkout without it says it skipped.
- At `office = importlib.import_module("convert_bot.office_convert")`:
  PowerPoint, Word and Excel files go through LibreOffice: a PDF is opened as slides,
  saved as PPTX, then PPT, then PDF again, and its text read back; a document and a
  spreadsheet go round too. The Dockerfile sets MUMU_CHECK_OFFICE, so an image without a
  working LibreOffice fails here; a checkout without it is checked only where it is installed.
- At `import shutil`:
  Videos are sent with their real width and height (video.probe); a build
  whose ffprobe does not answer to those arguments fails here, not on a reel.
  Skipped, with a note, where there is no ffmpeg (a bare checkout).

### `offer_to_the_oom_killer()`

A job's own process (`--module`: ConvertBot's worker and probes, a big-file
transfer) is the one the kernel kills first when the service runs out of
memory: one job lost, never the bots. Linux; ffmpeg and anything else the
job starts inherit it. Raising one's own score needs no privilege.

### `run_module()`

`main.py --module KEY ...`: one module as a program of its own, for the
processes the bots start -- ConvertBot's worker, and the big-file transfer.
A bot's module runs with that bot's modules and settings, as it would
inside the bot; a shared one with nothing but itself.

- At `bot.dotenv = {k: v for k, v in bot.dotenv.items() if not is_secret_setting(k)}`:
  A job the bot started opens a stranger's file: none of the bot's secrets.

_At `PROCESSES_SETTING = "MUMU_PROCESSES"`:_

--- one process per bot -------------------------------------------------------

_At `CHILD_SETTING = "MUMU_CHILD"`:_

Set in every child, so a child never becomes a supervisor itself.

_At `SERVICE_BOTS_SETTING = "MUMU_SERVICE_BOTS"`:_

The bots this service (the container) runs, whichever process each is in: set
here, inherited by the children, read by the `resources` module (how much of
the machine a bot's work may count on) and by memory_reporter.

_At `RESTART_DELAYS = (5, 10, 20, 40, 80, 160, 300)`:_

How long a child that ended with an error waits before it is started again:
5 s, then twice as long each time, up to 5 minutes. A child that ran
HEALTHY_SECONDS before it failed starts over from 5 s.

_At `STOP_GRACE_SECONDS = DRAIN_SECONDS + 15`:_

After a stop is passed on, how long the children get to finish their work
(each drains for up to DRAIN_SECONDS itself) before they are killed.

### `process_groups()`

None runs every bot in this process; otherwise, the bots of each process.

MUMU_PROCESSES is "one" (the default), "separate" (a process per bot), or groups split
by ";": "manager;convert" gives those two a process each and the rest one together, and
"manager;convert;sticker,downloader" names every process.

### `child_command()`

- `env.update(_SECRETS)` — a child running bots needs them; it takes them out again at its start

### `Supervisor`

Starts one process per group of bots (process_groups), keeps them running, and
stops them together.

A child that ends with an error is started again after a while; the others
are never touched by it. ManagerBot's /restart &lt;bot> is such an end: the bot
exits with code 1, so here it is back 5 seconds later, alone. A child that
ends with exit code 0 stopped on purpose and is left stopped. The supervisor
ends when it is told to stop, or when every child has stopped on purpose. It
never starts a second copy of a bot: a child is started again only after its
last process has ended.

#### `Supervisor.pass_on_stop()`

SIGTERM to every running child, once (Linux). On Windows they had the
console's Ctrl+C themselves.

#### `Supervisor.step()`

One look at the children. False once there is nothing left to do.

### `with_tokens()`

The bots that can run: each needs a token, and no two may share one.

### `main()`

- `_hold_secrets()` — anything a bot's import put back into the environment
- At `retrying = [bot for bot in wanted if bot.state == "failed" or (bot.state == "skipped" an …`:
  A bot that failed is tried again later, alone. One that ended itself with a
  plain SystemExit (a setting it needs is missing) is not: a restart cannot
  cure that.

_At `sys.exit(main(sys.argv[1:]))`:_

A job's own process: an ordinary exit, which flushes whatever it wrote.
