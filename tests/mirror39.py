"""Copy a bot's sources into a 3.9-parsable mirror.

The deployed bots run 3.11+ and use `str | None` in signatures; the Python on
this machine is 3.9, which cannot parse that. `from __future__ import
annotations` makes 3.9 accept it and changes nothing else, so the mirror runs
the same code the deployment does.

Since 2.0.0 the modules every bot shares live once, in shared/. A mirror is
still one self-contained folder per bot -- what every test was written
against -- so shared/'s modules are copied in beside the bot's own; a bot's
own file of the same name wins, and ManagerBot, which has monitoring.py
instead, is not given shared_features.py.
"""
import pathlib, shutil, sys

def _sources(src):
    own = sorted(src.glob("*.py"))
    shared = src.parent / "shared"
    taken = {path.name for path in own}
    extra = [] if not shared.is_dir() else [
        path for path in sorted(shared.glob("*.py"))
        if path.name not in taken and not (src.name == "manager_bot" and path.name == "shared_features.py")]
    return own + extra

def mirror(src_dir, dst_dir):
    src, dst = pathlib.Path(src_dir), pathlib.Path(dst_dir)
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for path in _sources(src):
        text = path.read_text(encoding="utf-8")
        if "from __future__ import annotations" not in text:
            lines = text.split("\n")
            i = 0
            if lines and lines[0].lstrip().startswith(('"""', "'''")):
                q = lines[0].lstrip()[:3]
                if lines[0].count(q) < 2:
                    i = next(j for j in range(1, len(lines)) if q in lines[j])
                i += 1
            lines.insert(i, "from __future__ import annotations")
            text = "\n".join(lines)
        (dst / path.name).write_text(text, encoding="utf-8")
    return dst

if __name__ == "__main__":
    print(mirror(sys.argv[1], sys.argv[2]))
