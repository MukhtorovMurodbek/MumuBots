"""The release pushes the right folder to the right repository.

scripts/nightly_release.py pauses the public bots before its window, so what
can be known in advance is checked before that. Since 2.0.0 the base folder
is released to MumuBots and the C folder to MumuCombined -- or BotFamily,
its old name, which GitHub redirects -- and a folder whose origin is anything
else is refused before anything is paused or read: telegram-bot-family, the
base folder's origin until then, holds tokens in its early commits and is
being retired -- and since a push carries every commit HEAD descends from, a
base folder whose history still begins with that repository's is refused
even with the right origin. --skip, which held one bot back while each had a repository of
its own, is refused outright: the bots share shared/, so one push carries
all five.

Throwaway repositories in a temporary folder, a bare one standing in for
GitHub, and a `db` that fails the test if anything touches it. Needs git and
nothing else; part of run.py.

The second half (2026-10-09) is what happens once the bots ARE paused, against
a fake `db` that records what is queued for the bots and written for the owner:
  * a crash anywhere after the pause -- before the push, during it, after it, in
    the pause itself, Ctrl-C, even a resume that cannot be queued -- still
    resumes the bots and tells the owner; a crash before the pause touches
    nothing;
  * a git command that times out is a failed step (a refusal before the pause,
    the failed-publish road after it), never an unhandled crash -- including a
    real push that hangs, with a helper process that holds its pipes;
  * every git command runs without stdin and with GIT_TERMINAL_PROMPT=0 and
    GCM_INTERACTIVE=never, under a timeout, and none bypasses run_git;
  * a folder that is not the release (uncommitted changes, wrong or no tag)
    is refused BEFORE anything is paused, as well as at the window;
  * scripts/run_release.cmd names its python and refuses to run without
    arguments.
"""
import ast
import contextlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent

failures = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        failures.append(label)


def git(folder, *args):
    return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def load(scratch: Path):
    spec = importlib.util.spec_from_file_location("nightly_release",
                                                  REPO / "scripts" / "nightly_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LOG = scratch / "release.log"   # never the real one
    return module


def working_copy(parent: Path, name: str, origin: str | None) -> Path:
    folder = parent / name
    folder.mkdir()
    git(folder, "init", "-q", "-b", "work")
    git(folder, "config", "user.email", "test@example.invalid")
    git(folder, "config", "user.name", "test")
    git(folder, "config", "commit.gpgsign", "false")
    git(folder, "config", "tag.gpgsign", "false")
    (folder / "file.txt").write_text("release\n", encoding="utf-8")
    git(folder, "add", "file.txt")
    git(folder, "commit", "-q", "-m", "release")
    if origin:
        git(folder, "remote", "add", "origin", origin)
    return folder


def run_main(release, *argv):
    """main()'s exit code, or the exception it died of, as text."""
    saved = sys.argv
    sys.argv = ["nightly_release.py", *argv]
    try:
        return release.main()
    except SystemExit as exc:
        return exc.code
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        sys.argv = saved


class Untouchable(types.ModuleType):
    """Stands in for ManagerBot's db: the refusals must come before it."""
    touched = []

    def __getattr__(self, name):
        Untouchable.touched.append(name)
        raise AssertionError(f"the database was touched: db.{name}")


class FakeDB(types.ModuleType):
    """ManagerBot's db as the release script uses it: what every bot reports,
    the commands queued for the bots, the events written for the owner."""

    def __init__(self, bots, version="1.0.0"):
        super().__init__("db")
        self.bots = list(bots)
        self.version = version
        self.queued = []         # (bot_id, command, args), in order
        self.events = []         # {"level", "message"}
        self.active = 0          # users the activity check finds
        self.fail_on = {}        # command -> how many of its queueings succeed first

    def all_heartbeats(self):
        return [{"bot_id": bot, "version": self.version} for bot in self.bots]

    def queue_command(self, bot_id, command, args, owner, requested_by):
        if command in self.fail_on:
            if sum(1 for _, name, _ in self.queued if name == command) >= self.fail_on[command]:
                raise ConnectionError("the database went away")
        self.queued.append((bot_id, command, args))

    @contextlib.contextmanager
    def pooled_read(self):
        yield _FakeConn(self)

    @contextlib.contextmanager
    def pooled(self):
        yield _FakeConn(self)

    def of(self, command):
        return [bot for bot, name, _ in self.queued if name == command]

    def order(self):
        return [name for _, name, _ in self.queued]


class _FakeConn:
    def __init__(self, db):
        self.db = db
        self.row = None

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("INSERT INTO family.events"):
            self.db.events.append({"level": params[1], "message": params[3]})
        else:
            self.row = (self.db.active,)
        return self

    def fetchone(self):
        return self.row

    def commit(self):
        pass


class NoSleep:
    """Stands in for `time` inside the release script: no waiting."""
    slept = []

    @staticmethod
    def sleep(seconds):
        NoSleep.slept.append(seconds)

    monotonic = staticmethod(time.monotonic)


def bare_github(scratch: Path, name: str, repository: str = "MumuBots") -> Path:
    """A fresh bare repository called `repository`, standing in for GitHub."""
    folder = scratch / name / f"{repository}.git"
    folder.parent.mkdir()
    git(scratch, "init", "-q", "--bare", str(folder))
    return folder


def release_folder(scratch: Path, name: str, github: Path, tag="v9.9.9", dirty=False) -> Path:
    folder = working_copy(scratch, name, str(github))
    if tag:
        git(folder, "tag", tag)
    if dirty:
        (folder / "file.txt").write_text("changed after the tag\n", encoding="utf-8")
    return folder


def release_run(release, fake, folder, *argv, patches=()):
    """main() as the scheduled task runs it, against `fake`, with no sleeping,
    in `folder`. `patches` are (attribute, value) pairs set on the release
    module for the run. Returns (exit code or the exception as text, the part of
    the log this run wrote)."""
    start = len(release.LOG.read_text(encoding="utf-8")) if release.LOG.exists() else 0
    release.ROOT = folder
    release._setup = lambda: (set(), "9.9.9")
    sys.modules["db"] = fake
    NoSleep.slept.clear()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(release, "time", NoSleep))
        for attribute, value in patches:
            stack.enter_context(mock.patch.object(release, attribute, value))
        try:
            code = run_main(release, *argv)
        finally:
            sys.modules.pop("db", None)
    text = release.LOG.read_text(encoding="utf-8")[start:] if release.LOG.exists() else ""
    return code, text


def calls_then(release, fail, real=subprocess.run):
    """A subprocess.run for the release script that raises TimeoutExpired for
    any git command `fail(args)` picks out, and runs the rest for real."""
    def run(cmd, *args, **kwargs):
        if fail(list(cmd)):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 1)
        return real(cmd, *args, **kwargs)
    return run


class Crash(RuntimeError):
    pass


def remove_tree(folder: Path, seconds: float = 20):
    """Removes a repository, however long a hung helper keeps it in use: git's
    own files are read-only, which on Windows rmtree will not delete, and a
    process that was started in the folder keeps it from being deleted until
    it ends."""
    give_up = time.monotonic() + seconds
    while folder.exists():
        for root, dirs, files in os.walk(folder):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), 0o700)
                except OSError:
                    pass
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            if time.monotonic() > give_up:
                print(f"  (could not remove {folder})")
                return
            time.sleep(0.5)


def crash_on_second_call(first):
    """active_users that answers once (the 24-hour summary, before the pause),
    then fails (the quiet check, after it)."""
    calls = []

    def active_users(db, admins, minutes):
        calls.append(minutes)
        if len(calls) == 1:
            return first
        raise Crash("boom")
    return active_users


def only_run_git_runs_subprocesses(path: Path):
    """Names of the functions in `path` that call subprocess.run/Popen/call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    callers = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                    and node.func.attr in ("run", "Popen", "call", "check_call", "check_output")):
                callers.add(function.name)
    return callers


def release_flow_checks(release, scratch: Path):
    """Everything about a run that gets past the checks and pauses the bots.
    Returns folders to remove at the very end (a hung helper may still be in them)."""
    bots = list(release.BOTS)
    n = 0

    def fresh(repository="MumuBots", **kw):
        nonlocal n
        n += 1
        github = bare_github(scratch, f"flow{n}", repository)
        return github, release_folder(scratch, f"flow{n}-work", github, **kw)

    # -- the road that works, with a real push ---------------------------------
    print("\n== a release that goes right ==")
    fake = FakeDB(bots)
    github, folder = fresh()
    real_publish = release.publish
    def publish_and_report(where, tag, dry_run):
        done = real_publish(where, tag, dry_run)   # a real push, to the bare repository
        if done:
            fake.version = "9.9.9"                 # ... after which every bot reports the new version
        return done
    code, text = release_run(release, fake, folder, "--now", patches=[("publish", publish_and_report)])
    check("a clean folder at its tag is released", code == 0, (code, text[-400:]))
    check("every bot is paused, then every bot is resumed",
          fake.order() == ["pause"] * 4 + ["resume"] * 4 and fake.of("pause") == bots, fake.queued)
    check("the pause outlasts the window", {a for _, c, a in fake.queued if c == "pause"} == {"80"}, fake.queued)
    check("the owner is told it shipped, once", [e["level"] for e in fake.events] == ["info"], fake.events)
    check("the push reached the remote",
          git(github, "rev-parse", "v9.9.9^{commit}").stdout.strip() == git(folder, "rev-parse", "HEAD").stdout.strip())

    # -- a crash after the pause still opens the bots --------------------------
    print("\n== a crash after the pause ==")
    github, folder = fresh()
    fake = FakeDB(bots)
    code, text = release_run(release, fake, folder, "--now",
                             patches=[("active_users", crash_on_second_call({b: 0 for b in bots}))])
    check("the crash is not swallowed", code == "Crash: boom", code)
    check("the bots were paused and then resumed all the same",
          fake.order() == ["pause"] * 4 + ["resume"] * 4, fake.queued)
    check("the owner is told, as an error", [e["level"] for e in fake.events] == ["error"], fake.events)
    message = fake.events[0]["message"] if fake.events else ""
    check("... that the release was aborted, why, and that nothing was published",
          "aborted" in message and "boom" in message and "Nothing was published" in message
          and "resumed" in message, message)
    check("... and the log says so", "ABORTING" in text and "queued resume" in text, text[-400:])

    fake = FakeDB(bots)
    def crash_first(db, admins, minutes):
        raise Crash("before the pause")
    code, text = release_run(release, fake, folder, "--now", patches=[("active_users", crash_first)])
    check("a crash before the pause queues and announces nothing",
          code == "Crash: before the pause" and not fake.queued and not fake.events, (code, fake.queued, fake.events))

    fake = FakeDB(bots)
    code, text = release_run(release, fake, folder, "--now",
                             patches=[("active_users", crash_on_second_call({b: 0 for b in bots})),
                                      ("publish", lambda *a: (_ for _ in ()).throw(AssertionError("pushed")))])
    check("... and a crash does not go on to publish", code == "Crash: boom", code)

    fake = FakeDB(bots)
    def crash_while_publishing(where, tag, dry_run):
        raise Crash("mid-push")
    code, text = release_run(release, fake, folder, "--now", patches=[("publish", crash_while_publishing)])
    check("a crash while publishing resumes the bots",
          code == "Crash: mid-push" and fake.order() == ["pause"] * 4 + ["resume"] * 4, (code, fake.queued))
    message = fake.events[0]["message"] if fake.events else ""
    check("... and says it is not known whether the push went through",
          "not known" in message and "Nothing was published" not in message, message)

    fake = FakeDB(bots)
    calls = []
    def versions_then_crash(db):
        calls.append(1)
        if len(calls) > 1:
            raise Crash("lost the database")
        return {b: "1.0.0" for b in bots}
    code, text = release_run(release, fake, folder, "--now", patches=[
        ("versions", versions_then_crash), ("publish", lambda where, tag, dry: True)])
    check("a crash after the push still resumes the bots",
          code == "Crash: lost the database" and fake.order() == ["pause"] * 4 + ["resume"] * 4, (code, fake.queued))
    message = fake.events[0]["message"] if fake.events else ""
    check("... and says the push had already gone", "already been pushed" in message, message)

    fake = FakeDB(bots)
    fake.fail_on = {"pause": 2}     # the third pause cannot be queued
    code, text = release_run(release, fake, folder, "--now")
    check("a pause that is only half queued is still undone",
          code == "ConnectionError: the database went away" and fake.of("pause") == bots[:2]
          and fake.of("resume") == bots, (code, fake.queued))

    fake = FakeDB(bots)
    fake.fail_on = {"resume": 0}    # the database cannot take the resume either
    code, text = release_run(release, fake, folder, "--now",
                             patches=[("active_users", crash_on_second_call({b: 0 for b in bots}))])
    check("the crash that is reported is the first one, not the failed resume", code == "Crash: boom", code)
    check("a resume that cannot be queued is logged, loudly, with the way out",
          "COULD NOT QUEUE THE RESUME" in text and "/resume" in text, text[-500:])
    message = fake.events[0]["message"] if fake.events else ""
    check("... and the owner is told the bots were NOT resumed", "could NOT be resumed" in message, message)

    fake = FakeDB(bots)
    interrupted = False
    seen = []
    def quiet_then_interrupt(db, admins, minutes):
        seen.append(minutes)
        if len(seen) == 1:
            return {b: 0 for b in bots}
        raise KeyboardInterrupt()
    try:
        release_run(release, fake, folder, "--now", patches=[("active_users", quiet_then_interrupt)])
    except KeyboardInterrupt:
        interrupted = True
    check("Ctrl-C after the pause resumes the bots too",
          interrupted and fake.order() == ["pause"] * 4 + ["resume"] * 4 and len(fake.events) == 1,
          (interrupted, fake.queued, fake.events))

    fake = FakeDB(bots)
    code, text = release_run(release, fake, folder, "--dry-run", "--now",
                             patches=[("active_users", crash_on_second_call({b: 0 for b in bots}))])
    check("a dry run pauses nothing, so there is nothing to undo", not fake.queued and not fake.events,
          (code, fake.queued, fake.events))

    # -- a git command that times out is a failed step ---------------------------
    print("\n== git that does not answer ==")
    github, folder = fresh()
    fake = FakeDB(bots)
    hang_push = calls_then(release, lambda cmd: "push" in cmd)
    code, text = release_run(release, fake, folder, "--now",
                             patches=[("subprocess", types.SimpleNamespace(
                                 run=hang_push, TimeoutExpired=subprocess.TimeoutExpired,
                                 DEVNULL=subprocess.DEVNULL, CompletedProcess=subprocess.CompletedProcess))])
    check("a push that times out is a failed publish, not a crash", code == 1, (code, text[-400:]))
    check("... the bots are resumed", fake.order() == ["pause"] * 4 + ["resume"] * 4, fake.queued)
    check("... the owner is told it failed while publishing",
          [e["level"] for e in fake.events] == ["error"] and "failed while publishing" in fake.events[0]["message"],
          fake.events)
    check("... and the log says the push did not finish, and that nobody knows if it arrived",
          "did not finish within" in text and "not known" in text, text[-400:])
    with mock.patch.object(release, "subprocess", types.SimpleNamespace(
            run=hang_push, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL,
            CompletedProcess=subprocess.CompletedProcess)):
        check("publish() itself answers False for a push that times out",
              release.publish(folder, "v9.9.9", dry_run=False) is False)

    for what, picks in (("git describe", lambda cmd: "describe" in cmd),
                        ("git status", lambda cmd: "status" in cmd),
                        ("git remote", lambda cmd: "remote" in cmd),
                        ("git rev-list", lambda cmd: "rev-list" in cmd)):
        fake = FakeDB(bots)
        stub = types.SimpleNamespace(run=calls_then(release, picks), TimeoutExpired=subprocess.TimeoutExpired,
                                     DEVNULL=subprocess.DEVNULL, CompletedProcess=subprocess.CompletedProcess)
        code, text = release_run(release, fake, folder, "--now", patches=[("subprocess", stub)])
        check(f"{what} that times out refuses the release before the pause",
              code == 1 and not fake.queued, (code, fake.queued, text[-300:]))
        check(f"  ... and the log says git did not answer", "did not answer" in text, text[-300:])

    print("\n== a push that really hangs ==")
    hang_dir = Path(tempfile.mkdtemp(prefix="release-hang-"))
    orphans = [hang_dir]
    helper = hang_dir / "slow_ssh.py"
    helper.write_text(
        "import subprocess, sys, time\n"
        # a helper of the helper, holding the same output handles, like git-remote-https
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'])\n"
        "time.sleep(6)\n", encoding="utf-8")
    hung = working_copy(hang_dir, "hung", "ssh://git@example.invalid/MumuBots.git")
    git(hung, "tag", "v9.9.9")
    saved_env = os.environ.get("GIT_SSH_COMMAND")
    saved_timeout = release.PUSH_TIMEOUT_SECONDS
    os.environ["GIT_SSH_COMMAND"] = f'"{Path(sys.executable).as_posix()}" "{helper.as_posix()}"'
    release.PUSH_TIMEOUT_SECONDS = 1
    try:
        started = time.monotonic()
        answered = release.publish(hung, "v9.9.9", dry_run=False)
        took = time.monotonic() - started
    finally:
        release.PUSH_TIMEOUT_SECONDS = saved_timeout
        if saved_env is None:
            os.environ.pop("GIT_SSH_COMMAND", None)
        else:
            os.environ["GIT_SSH_COMMAND"] = saved_env
    log = release.LOG.read_text(encoding="utf-8")
    check("a push that hangs is stopped and reported as failed", answered is False and "did not finish within" in log,
          (answered, log[-300:]))
    check("... after the timeout, not after its helper has finished (pipes would have waited 6 s)",
          took < 4, f"{took:.1f} s")

    # -- every git command is run the same careful way -----------------------------
    print("\n== git never asks, and never waits for ever ==")
    seen = []
    def spy(cmd, *args, **kwargs):
        seen.append((list(cmd), kwargs))
        return real_run(cmd, *args, **kwargs)
    real_run = subprocess.run
    github, folder = fresh()
    c_folder = release_folder(scratch, "spy-c", github, tag="v9.9.9C")
    with mock.patch.object(release, "subprocess", types.SimpleNamespace(
            run=spy, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL,
            CompletedProcess=subprocess.CompletedProcess)):
        release.wrong_remote(folder, "base")
        release.carries_old_history(folder)
        saved_root = release.ROOT
        release.ROOT = folder
        release.release_not_ready("9.9.9")
        release.ROOT = saved_root
        release.c_not_ready(c_folder, "9.9.9")
        release.publish(folder, "v9.9.9", dry_run=True)
    check("every kind of git command the script runs was seen", len(seen) == 7, [c for c, _ in seen])
    check("every one has GIT_TERMINAL_PROMPT=0",
          all((k.get("env") or {}).get("GIT_TERMINAL_PROMPT") == "0" for _, k in seen), [c for c, _ in seen])
    check("every one has GCM_INTERACTIVE=never",
          all((k.get("env") or {}).get("GCM_INTERACTIVE") == "never" for _, k in seen), [c for c, _ in seen])
    check("every one has nobody to read from", all(k.get("stdin") == subprocess.DEVNULL for _, k in seen))
    check("every one has a timeout", all(isinstance(k.get("timeout"), (int, float)) and k["timeout"] > 0
                                         for _, k in seen), [(c, k.get("timeout")) for c, k in seen])
    check("the push has the longer one",
          all(k["timeout"] == release.PUSH_TIMEOUT_SECONDS for c, k in seen if "push" in c)
          and any("push" in c for c, _ in seen))
    check("none of them captures through pipes (they would outlive a timeout)",
          all(k.get("capture_output") is None and k.get("stdout") not in (subprocess.PIPE, None) for _, k in seen),
          [(c, k.get("stdout")) for c, k in seen][:2])
    check("the environment is added to, not replaced",
          all("PATH" in (k.get("env") or {}) or "Path" in (k.get("env") or {}) for _, k in seen))
    check("nothing in the script calls subprocess except run_git",
          only_run_git_runs_subprocesses(REPO / "scripts" / "nightly_release.py") == {"run_git"},
          only_run_git_runs_subprocesses(REPO / "scripts" / "nightly_release.py"))

    # -- the folder is checked before the pause, and again at the window ----------
    print("\n== a folder that is not the release stops it before anything is paused ==")
    cases = [
        ("uncommitted changes", dict(dirty=True), "uncommitted changes"),
        ("a nearest tag that is another version", dict(tag="v9.9.8"), "nearest tag is v9.9.8, not v9.9.9"),
        ("no tag at all", dict(tag=None), "nearest tag is none"),
    ]
    for label, kw, expected in cases:
        github, folder = fresh(**kw)
        fake = FakeDB(bots)
        code, text = release_run(release, fake, folder, "--now")
        check(f"{label}: refused", code == 1, (code, text[-300:]))
        check("  ... before any bot is paused", not fake.queued, fake.queued)
        check("  ... with the reason in the log", f"not the release: " in text and expected in text, text[-300:])
        check("  ... and the owner is told, nothing having been touched",
              [e["level"] for e in fake.events] == ["warning"]
              and "before anything was paused" in fake.events[0]["message"]
              and expected in fake.events[0]["message"], fake.events)

    github, folder = fresh()
    combined = bare_github(scratch, "flowc", "MumuCombined")
    folder_c = release_folder(scratch, "flowc-work", combined, tag="v9.9.9C", dirty=True)
    fake = FakeDB(bots)
    code, text = release_run(release, fake, folder, "--now", "--edition", "C", "--c-folder", str(folder_c))
    check("the C edition is held to the same rule", code == 1 and not fake.queued
          and "the C folder has uncommitted changes" in text and "Not releasing" not in text,
          (code, fake.queued, text[-300:]))

    github, folder = fresh(dirty=True)
    fake = FakeDB(bots)
    code, text = release_run(release, fake, folder, "--now", "--dry-run")
    check("a dry run reports it and carries on without queueing anything", not fake.queued and not fake.events
          and "not the release:" in text, (code, fake.queued, text[-300:]))

    fake = FakeDB("stickerbot convertbot downloaderbot anonbot".split(), version="9.9.9")
    github, folder = fresh(dirty=True)
    code, text = release_run(release, fake, folder, "--now")
    check("when every bot is on the version already, a dirty folder is not an alarm",
          code == 0 and not fake.queued and not fake.events, (code, fake.queued, fake.events))

    print("\n== ... and again at the window ==")
    github, folder = fresh()
    fake = FakeDB(bots)
    answers = iter([None, "there are uncommitted changes"])
    code, text = release_run(release, fake, folder, "--now", patches=[
        ("release_not_ready", lambda target: next(answers)),
        ("publish", lambda *a: (_ for _ in ()).throw(AssertionError("pushed")))])
    check("a folder that changes during the wait is refused at the window, bots resumed",
          code == 1 and fake.order() == ["pause"] * 4 + ["resume"] * 4, (code, fake.queued, text[-300:]))
    check("... and the owner is told it was aborted before publishing",
          [e["level"] for e in fake.events] == ["warning"] and "aborted before publishing" in fake.events[0]["message"],
          fake.events)

    # -- the launcher ---------------------------------------------------------------
    print("\n== scripts/run_release.cmd ==")
    cmd = (REPO / "scripts" / "run_release.cmd").read_text(encoding="utf-8")
    venv_python = r"C:\Bitura\.Projects\MumuBots\TempTools\venv\Scripts\python.exe"
    check("it runs the TempTools python", venv_python in cmd)
    launch = [line for line in cmd.splitlines() if "nightly_release.py" in line and not line.lstrip().upper().startswith("REM")]
    check("... and that is what runs the release", len(launch) == 1 and '"%PY%"' in launch[0], launch)
    check("it refuses to run with no arguments", 'if "%~1"==""' in cmd and "exit /b 2" in cmd)
    check("... before it writes to the log or changes folder",
          cmd.index('if "%~1"==""') < cmd.index('>> "%~dp0release.log"') and cmd.index('if "%~1"==""') < cmd.index("cd /d"))
    check("it hands the arguments on", "%*" in launch[0] if launch else False)
    if os.name == "nt":
        before = (REPO / "scripts" / "release.log").stat().st_size if (REPO / "scripts" / "release.log").exists() else None
        done = subprocess.run(["cmd", "/c", str(REPO / "scripts" / "run_release.cmd")], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=60)
        after = (REPO / "scripts" / "release.log").stat().st_size if (REPO / "scripts" / "release.log").exists() else None
        check("run with no arguments it exits 2 with a usage line",
              done.returncode == 2 and "usage:" in done.stdout and "--edition C" in done.stdout, (done.returncode, done.stdout))
        check("... and writes nothing to the release log", before == after, (before, after))

    return orphans


def main() -> int:
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        print("  FAIL git is not installed")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        release = load(scratch)

        # -- which origin each edition goes to --------------------------------
        cases = [
            ("base", "https://github.com/MukhtorovMurodbek/MumuBots.git", True),
            ("base", "https://github.com/MukhtorovMurodbek/MumuBots", True),
            ("base", "https://github.com/MukhtorovMurodbek/MumuBots/", True),
            ("base", "git@github.com:MukhtorovMurodbek/MumuBots.git", True),
            ("base", "https://github.com/MukhtorovMurodbek/telegram-bot-family.git", False),
            ("base", "https://github.com/MukhtorovMurodbek/MumuCombined.git", False),
            ("base", "https://github.com/MukhtorovMurodbek/ConvertBot.git", False),
            ("base", "https://github.com/MukhtorovMurodbek/MumuBotsHistory.git", False),
            ("base", None, False),
            ("C", "https://github.com/MukhtorovMurodbek/MumuCombined.git", True),
            ("C", "https://github.com/MukhtorovMurodbek/BotFamily.git", True),
            ("C", "git@github.com:MukhtorovMurodbek/BotFamily.git", True),
            ("C", "https://github.com/MukhtorovMurodbek/MumuBots.git", False),
            ("C", None, False),
        ]
        for n, (edition, origin, fine) in enumerate(cases):
            folder = working_copy(scratch, f"remote{n}", origin)
            why = release.wrong_remote(folder, edition)
            shown = origin or "no origin"
            if fine:
                check(f"{edition} edition goes to {shown}", why is None, why)
            else:
                check(f"{edition} edition refuses {shown}", why is not None)
                if why:
                    check(f"  ... and says what it expected, and where to read why",
                          ("MumuBots" if edition == "base" else "MumuCombined") in why
                          and "12 repositories become 3" in why, why)

        # -- the push: branch and tag, and a dry run pushes nothing -----------
        github = scratch / "MumuBots.git"
        git(scratch, "init", "-q", "--bare", str(github))
        work = working_copy(scratch, "BotFamily9.9.9", str(github))
        git(work, "tag", "v9.9.9")
        check("a local bare MumuBots counts as MumuBots", release.wrong_remote(work, "base") is None)
        check("a dry run succeeds", release.publish(work, "v9.9.9", dry_run=True))
        check("... and pushes nothing", git(github, "rev-parse", "--verify", "-q", "main").returncode != 0)
        check("the push succeeds", release.publish(work, "v9.9.9", dry_run=False))
        head = git(work, "rev-parse", "HEAD").stdout.strip()
        check("main is the folder's HEAD, whatever its branch is called",
              git(github, "rev-parse", "main").stdout.strip() == head)
        check("the version's tag went with it",
              git(github, "rev-parse", "v9.9.9^{commit}").stdout.strip() == head)
        log = release.LOG.read_text(encoding="utf-8") if release.LOG.exists() else ""
        check("the push is in the log", "git push origin HEAD:main refs/tags/v9.9.9" in log, log[-300:])

        # -- the old history never goes to MumuBots ------------------------------
        root = git(work, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
        check("a fresh history is not the old one", release.carries_old_history(work) is None)
        saved_roots = release.OLD_HISTORY_ROOTS
        release.OLD_HISTORY_ROOTS = saved_roots | {root}
        try:
            why = release.carries_old_history(work)
            check("a history that begins with telegram-bot-family's is refused", why is not None)
            check("  ... and says so", why is not None and "telegram-bot-family" in why, why)
            descendant = working_copy(scratch, "descendant", str(github))
            git(descendant, "fetch", "-q", str(work), "work")
            git(descendant, "reset", "-q", "--hard", "FETCH_HEAD")
            (descendant / "later.txt").write_text("later\n", encoding="utf-8")
            git(descendant, "add", "later.txt")
            git(descendant, "commit", "-q", "-m", "later")
            check("... however many commits later", release.carries_old_history(descendant) is not None)
        finally:
            release.OLD_HISTORY_ROOTS = saved_roots
        real = REPO.parent / "BotFamily1.7.0"
        if (real / ".git").exists():
            found = set(git(real, "rev-list", "--max-parents=0", "--all").stdout.split())
            check("the roots listed are telegram-bot-family's, all of them",
                  found == release.OLD_HISTORY_ROOTS, sorted(found ^ release.OLD_HISTORY_ROOTS))

        # -- the refusals come before anything is paused or read --------------
        calls = []
        release._setup = lambda: calls.append("setup") or (set(), "9.9.9")
        sys.modules["db"] = Untouchable("db")
        try:
            code = run_main(release, "--skip", "anon_bot", "--dry-run")
            check("--skip is refused", code == 2, code)
            check("... before anything else happens", not calls, calls)

            release.ROOT = working_copy(scratch, "BotFamily9.9.8",
                                        "https://github.com/MukhtorovMurodbek/telegram-bot-family.git")
            code = run_main(release, "--now")
            check("a base folder still on telegram-bot-family is not released", code == 1, code)
            check("... and the database was never touched", not Untouchable.touched, Untouchable.touched)
            log = release.LOG.read_text(encoding="utf-8")
            check("... and the log says why", "telegram-bot-family, not MumuBots" in log, log[-300:])

            release.OLD_HISTORY_ROOTS = release.OLD_HISTORY_ROOTS | {
                git(work, "rev-list", "--max-parents=0", "HEAD").stdout.strip()}
            release.ROOT = work   # origin MumuBots, history "old"
            code = run_main(release, "--now")
            check("the right origin with the old history is not released", code == 1, code)
            check("... and the database was never touched", not Untouchable.touched, Untouchable.touched)
            log = release.LOG.read_text(encoding="utf-8")
            check("... and the log says why", "history still begins with telegram-bot-family's" in log,
                  log[-300:])

            c_folder = working_copy(scratch, "elsewhere",
                                    "https://github.com/MukhtorovMurodbek/MumuBots.git")
            code = run_main(release, "--now", "--edition", "C", "--c-folder", str(c_folder))
            check("a C folder pointed at MumuBots is not released", code == 1, code)
            check("... and the database was never touched", not Untouchable.touched, Untouchable.touched)
        finally:
            sys.modules.pop("db", None)

        release.OLD_HISTORY_ROOTS = saved_roots
        orphans = release_flow_checks(release, scratch)

        # Windows holds git's files open a moment; let TemporaryDirectory cope.
        os.chdir(REPO)
        for folder in orphans:
            remove_tree(folder)

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
