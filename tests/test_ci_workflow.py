"""The CI workflow installs what the tests would otherwise silently skip without.

    python3 tests/test_ci_workflow.py

tests/test_archives.py skips its 7z and RAR checks, saying so, where there is
no bsdtar -- and a skip on CI is a green nobody reads. bsdtar is what the
`libarchive-tools` package puts on the PATH of an Ubuntu runner, and it is what
convert_bot/Dockerfile installs for the same reason. So every workflow that
runs tests/run.py must apt-install it, in a step before the one that runs the
tests. Reads the workflow files as text; needs nothing.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
failures = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        failures.append(label)


def main() -> int:
    workflows = sorted((REPO / ".github" / "workflows").glob("*.y*ml"))
    check("there is a workflow", bool(workflows), workflows)
    running_tests = [w for w in workflows if "tests/run.py" in w.read_text(encoding="utf-8")]
    check("one of them runs the tests", bool(running_tests), [w.name for w in workflows])
    for workflow in running_tests:
        lines = workflow.read_text(encoding="utf-8").splitlines()
        code = [(n, line) for n, line in enumerate(lines) if not line.lstrip().startswith("#")]
        install = [n for n, line in code if "apt-get install" in line and "libarchive-tools" in line]
        run = [n for n, line in code if "tests/run.py" in line]
        check(f"{workflow.name}: apt-installs libarchive-tools (bsdtar)", bool(install), "no such line")
        check(f"{workflow.name}: ... before it runs the tests", bool(install) and bool(run) and min(install) < min(run),
              (install, run))
        check(f"{workflow.name}: ... after refreshing the package index",
              any("apt-get update" in line for n, line in code if install and n <= min(install)),
              "apt-get update must come first on a fresh runner")
    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
