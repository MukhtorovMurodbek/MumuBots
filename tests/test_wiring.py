"""Run each bot's main() as far as run_polling, with the database and
python-telegram-bot faked. Catches the wiring that only exists inside main():
the builder chain, the order of lifecycle.install() against
ConversationHandler(persistent=...), and what gets passed to run_polling."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = sys.argv[1]
sys.path.insert(0, os.path.join(HERE, ".mirror", BOT))
sys.path.insert(0, os.path.join(HERE, "stubs"))
for var in ("SBOT", "CBOT", "DBOT", "ABOT", "MBOT"):
    os.environ[f"{var}_TOKEN"] = "test"; os.environ[f"{var}_USERNAME"] = "test"
    os.environ[f"{var}_ADMIN_ID"] = "1"
os.environ["DATABASE_URL"] = "postgresql://stub"

import telegram.ext as tgext

STARTED = {}


class FakeJobQueue:
    def __init__(self): self.jobs = []
    def run_repeating(self, cb, interval=None, first=None, **k):
        self.jobs.append(("repeating", cb.__name__, interval)); return object()
    def run_once(self, cb, when=None, **k):
        self.jobs.append(("once", cb.__name__, when)); return object()
    def run_daily(self, cb, t, **k):
        self.jobs.append(("daily", cb.__name__, str(t))); return object()


class Ran(Exception):
    """run_polling reached -- everything before it is wired."""


class FakeApp:
    def __init__(self, persistence):
        self.persistence = persistence
        self.job_queue = FakeJobQueue()
        self.handlers = []
        self.user_data = {}
    def add_handler(self, handler, group=0):
        # The real Application refuses a persistent handler when it has no
        # persistence, which is the mistake this check exists to catch.
        if getattr(handler, "persistent", False) and self.persistence is None:
            raise ValueError("persistent handler added to an Application without persistence")
        if getattr(handler, "persistent", False) and not getattr(handler, "name", None):
            raise ValueError("persistent ConversationHandler needs a name")
        self.handlers.append(handler)
    def add_error_handler(self, cb): pass
    def drop_user_data(self, uid): self.user_data.pop(uid, None)
    async def update_persistence(self): pass
    def run_polling(self, **kwargs):
        STARTED["kwargs"] = kwargs
        STARTED["app"] = self
        raise Ran


class FakeBuilder:
    def __init__(self): self._persistence = None
    def token(self, *a): return self
    def post_init(self, *a): return self
    def post_stop(self, *a): return self
    def base_url(self, *a): return self
    def base_file_url(self, *a): return self
    def local_mode(self, *a): return self
    def persistence(self, p):
        assert p is not None, "persistence(None) would be a TypeError in PTB"
        self._persistence = p
        return self
    def build(self): return FakeApp(self._persistence)


tgext.ApplicationBuilder = FakeBuilder


class FakeConversationHandler:
    END = -1
    def __init__(self, entry_points=None, states=None, fallbacks=None,
                 name=None, persistent=False, **k):
        self.name, self.persistent = name, persistent
tgext.ConversationHandler = FakeConversationHandler

import db
db.init_db = lambda *a, **k: None
db.pooled = lambda: (_ for _ in ()).throw(RuntimeError("no database in tests"))

import bot as target
target.ConversationHandler = FakeConversationHandler
target.ApplicationBuilder = FakeBuilder
if hasattr(target, "init_db"):
    target.init_db = lambda *a, **k: None
if hasattr(target, "_sweep_uploads"):
    target._sweep_uploads = lambda **k: None

import family_link
family_link.attach = lambda *a, **k: None

# Second mode: pretend the state table is there, so the path where
# persistence *does* come up is exercised too.
WITH_STATE = "--with-state" in sys.argv
if WITH_STATE:
    import lifecycle
    lifecycle.init_state_table = lambda: None

failures = []
def equals(name, got, want):
    """Named for what it does, because the rest of this suite spells an
    assertion check(name, ok, detail) -- and a comparison written in that
    shape by mistake passes whenever `got` is truthy, which is a test that
    reports success without testing anything."""
    if got != want: failures.append(f"{name}: got {got!r}, want {want!r}")
    else: print(f"  ok  {BOT}: {name}")

try:
    target.main()
except Ran:
    pass

kwargs = STARTED["kwargs"]
app = STARTED["app"]
equals("run_polling was reached", "allowed_updates" in kwargs, True)
equals("pending updates are NOT dropped over a redeploy",
      kwargs.get("drop_pending_updates", False), False)
equals("this process takes the stop signals itself",
      kwargs.get("stop_signals"), () if sys.platform != "win32" else None)

# persistence() could not build (no database here), so the handlers must have
# agreed with that rather than asking for something that is not there.
if WITH_STATE:
    equals("persistence is attached when the table is there",
          app.persistence is not None, True)
    equals("and a periodic save is scheduled",
          any(j[1] == "_persist_job" for j in app.job_queue.jobs), True)
else:
    equals("no persistence without a database", app.persistence, None)

convs = [h for h in app.handlers if isinstance(h, FakeConversationHandler)]
for conv in convs:
    equals(f"conversation {conv.name!r} is named", bool(conv.name), True)
    equals(f"conversation {conv.name!r} follows the persistence switch",
          conv.persistent, WITH_STATE)

print()
if failures:
    print("FAILURES:")
    for f in failures: print("  -", f)
    sys.exit(1)
sys.exit(0)
