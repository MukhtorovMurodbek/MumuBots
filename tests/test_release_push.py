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
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

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

        # Windows holds git's files open a moment; let TemporaryDirectory cope.
        os.chdir(REPO)

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
