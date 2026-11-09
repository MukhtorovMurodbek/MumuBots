# Loaded by every Python process that membench starts (PYTHONPATH=membench/site): points every
# python-telegram-bot Application at the fake Bot API, and on SIGUSR1 writes a histogram of the
# live objects to $TYPES_ON_SIGNAL. Measurement only; nothing in the bots depends on it.
import os

URL = os.environ.get("FAKE_TG_URL")
if URL:
    try:
        from telegram.ext import _applicationbuilder as ab
        _orig = ab.ApplicationBuilder.__init__

        def __init__(self, *a, **k):
            _orig(self, *a, **k)
            self._base_url = URL + "/bot"
            self._base_file_url = URL + "/file/bot"

        ab.ApplicationBuilder.__init__ = __init__
    except Exception:  # a process without python-telegram-bot
        pass

if os.environ.get("TYPES_ON_SIGNAL"):
    import collections
    import gc
    import signal
    import sys

    def _types(*_):
        gc.collect()
        objs = gc.get_objects()
        count = collections.Counter(type(o).__module__ + "." + type(o).__qualname__ for o in objs)
        with open(os.environ["TYPES_ON_SIGNAL"], "w") as out:
            out.write(f"gc-tracked objects: {len(objs)}; pymalloc blocks: {sys.getallocatedblocks()}\n")
            for name, n in count.most_common(40):
                out.write(f"{n:9d}  {name}\n")

    signal.signal(signal.SIGUSR1, _types)
