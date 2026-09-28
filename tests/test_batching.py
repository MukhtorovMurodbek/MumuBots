"""An album is one conversion, not one conversion per photo.

    python3 tests/test_batching.py

Telegram sends an album as N separate messages sharing a `media_group_id`, a
fraction of a second apart. ConvertBot handled each on its own and each new
arrival called `_discard_pending()` on the last, so five photos produced five
format menus of which four already pointed at a deleted file. Nothing failed;
the user just tapped a button and was told the conversion had expired.

It is also the flow that makes "these pictures, one PDF" possible at all, and
that is the conversion this whole version exists for -- so it is worth a test
that drives the handler rather than reading it.

Telegram is stubbed out entirely (`tests/stubs`) and so is the language
lookup; the files are real, small, and written into a temporary directory.
What is being checked is the state machine:

  1. One file on its own still draws a menu immediately.
  2. An album draws exactly one menu, after the last file lands, holding all
     of them.
  3. A second album replaces the first, and the first album's files are gone.
  4. An album of two different formats is refused with a sentence, not
     guessed at.
  5. Abandoning a batch deletes every file in it, not just the last.
"""
import asyncio
import os
import pathlib
import sys
import tempfile
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
sys.path.insert(0, str(HERE / "stubs"))
# Files over 20 MB are test_big_files.py's; here the bot runs without them,
# whatever the shell this runs from has set.
for _variable in ("CBOT_API_ID", "CBOT_API_HASH", "CONVERT_MAX_FILE_MB"):
    os.environ.pop(_variable, None)
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
STAGE = tempfile.mkdtemp(prefix="convertbot-batch-")
os.environ["CONVERT_UPLOAD_DIR"] = STAGE

import bot                                              # noqa: E402
import i18n                                             # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
# The smallest Telegram that this handler can tell from the real one
# ---------------------------------------------------------------------------

class FakeFile:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def download_to_drive(self, custom_path):
        pathlib.Path(custom_path).write_bytes(self.payload)
        return custom_path


class FakeBot:
    def __init__(self):
        self.payload = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    async def get_file(self, file_id):
        return FakeFile(self.payload)

    async def delete_message(self, *a, **k):
        return True

    async def edit_message_text(self, *a, **k):
        return True


class FakeMessage:
    """Enough of a Message for convert_receive_file and the menu it draws."""

    def __init__(self, chat_id=7, message_id=1):
        self.chat_id = chat_id
        self.message_id = message_id
        self.document = self.photo = self.video = None
        self.audio = self.voice = self.animation = None
        self.media_group_id = None
        self.replies = []
        self.keyboards = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.keyboards.append(kwargs.get("reply_markup"))
        return FakeMessage(self.chat_id, self.message_id + 100 + len(self.replies))


class FakeJob:
    def __init__(self, callback, name, data, when):
        self.callback, self.name, self.data, self.when = callback, name, data, when
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    """Runs nothing on its own. The test fires the surviving job by hand,
    which is what "the album stopped arriving" means."""

    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return [job for job in self.jobs if job.name == name and not job.removed]

    def run_once(self, callback, when, name=None, data=None, **kwargs):
        job = FakeJob(callback, name, data, when)
        self.jobs.append(job)
        return job

    def live(self):
        return [job for job in self.jobs if not job.removed]


def new_context(job_queue=True):
    return SimpleNamespace(
        bot=FakeBot(),
        user_data={"lang": "en"},
        job_queue=FakeJobQueue() if job_queue else None,
        job=None,
    )


def photo_update(context, chat_id=7, message_id=1, group=None, ext=None):
    """A photo, or a document with the extension given."""
    message = FakeMessage(chat_id, message_id)
    if ext is None:
        message.photo = [SimpleNamespace(file_id=f"f{message_id}", file_size=512)]
    else:
        message.document = SimpleNamespace(
            file_id=f"f{message_id}", file_size=512, file_name=f"pic{message_id}.{ext}")
    message.media_group_id = group
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=4242, username="tester"),
    )


async def deliver(update, context):
    await bot.convert_receive_file(update, context)


async def fire_pending_jobs(context):
    """The album has stopped arriving: run whatever job survived."""
    for job in context.job_queue.live():
        context.job = job
        await job.callback(context)
    context.job_queue.jobs = []


def pending(context):
    return context.user_data.get("convert")


def staged_files():
    return sorted(p.name for p in pathlib.Path(STAGE).iterdir() if p.is_file())


# ---------------------------------------------------------------------------

async def run() -> None:
    # The language lookup is the one thing here that would reach a database.
    async def fixed_lang(user_id, context=None):
        return "en"
    i18n.get_lang = fixed_lang
    # LiveMessage would edit a message id this fake server does not have.
    class Status:
        @classmethod
        async def reply_to(cls, message, text, **kwargs):
            return cls()

        async def set(self, *a, **k):
            return self

        async def delete(self, *a, **k):
            return None
    bot.LiveMessage = Status

    print("=== one file on its own ===")
    context = new_context()
    update = photo_update(context, message_id=1)
    await deliver(update, context)
    state = pending(context)
    check("a single photo draws its menu straight away", len(update.message.replies) == 1)
    check("and holds exactly one file", state and len(state["paths"]) == 1, str(state))
    check("no job was scheduled for it", not context.job_queue.live())
    bot._discard_pending(context)

    print("\n=== an album of five ===")
    context = new_context()
    sent = []
    for index in range(5):
        update = photo_update(context, message_id=10 + index, group="G1")
        sent.append(update)
        await deliver(update, context)
    drawn = [len(update.message.replies) for update in sent]
    check("nothing has been drawn yet", sum(drawn) == 0, str(drawn))
    check("one job is armed, not five", len(context.job_queue.live()) == 1,
          str(len(context.job_queue.live())))
    check("and it is holding all five files",
          pending(context) and len(pending(context)["paths"]) == 5,
          str(pending(context) and len(pending(context)["paths"])))

    await fire_pending_jobs(context)
    state = pending(context)
    drawn = [len(update.message.replies) for update in sent]
    check("one menu is drawn, once", sum(drawn) == 1, str(drawn))
    # Under the LAST file of the album, deliberately: that is the bottom of
    # the album in the chat, so the menu lands where the user is looking.
    check("under the last file of the album, not the first", drawn[-1] == 1, str(drawn))
    last = sent[-1].message
    check("the menu offers PDF", any(
        "PDF" in [button.args[0] for row in keyboard.inline_keyboard for button in row]
        for keyboard in last.keyboards if keyboard is not None))
    check("the pending conversion still holds all five",
          state and len(state["paths"]) == 5, str(state and len(state["paths"])))
    check("its text says five files", "5" in last.replies[0], last.replies[0])

    print("\n=== a second album replaces the first ===")
    kept = list(state["paths"])
    second = photo_update(context, message_id=30, group="G2")
    await deliver(second, context)
    await fire_pending_jobs(context)
    check("the first album's files are gone from disk",
          not any(os.path.exists(path) for path in kept))
    check("and the pending conversion is the new one",
          pending(context)["group_id"] == "G2", str(pending(context)))
    bot._discard_pending(context)

    print("\n=== an album of two different formats ===")
    context = new_context()
    png = photo_update(context, message_id=40, group="G3", ext="png")
    await deliver(png, context)
    webp = photo_update(context, message_id=41, group="G3", ext="webp")
    await deliver(webp, context)
    check("the odd one out is refused with a sentence",
          webp.message.replies and "one at a time" in webp.message.replies[0].lower(),
          str(webp.message.replies))
    check("and the batch still holds only the first",
          len(pending(context)["paths"]) == 1, str(pending(context)["paths"]))
    check("the one that was refused was never downloaded", len(staged_files()) == 1,
          str(staged_files()))
    bot._discard_pending(context)

    print("\n=== abandoning a batch ===")
    context = new_context()
    for index in range(3):
        await deliver(photo_update(context, message_id=50 + index, group="G4"), context)
    paths = list(pending(context)["paths"])
    check("three files are staged", len(paths) == 3)
    bot._discard_pending(context)
    check("cancelling deletes every one of them, not just the last",
          not any(os.path.exists(path) for path in paths))
    check("and clears the pending conversion", pending(context) is None)

    print("\n=== nothing is left behind ===")
    # Every path above ended in a menu that was cancelled or replaced, so the
    # staging directory should hold nothing. A file left here is a file the
    # sweeper has to find later, and in production it is somebody's photo
    # sitting on a disk for fifteen minutes longer than it had to.
    check("the staging directory is empty", not staged_files(), str(staged_files()))


def main() -> int:
    try:
        asyncio.run(run())
    finally:
        for path in pathlib.Path(STAGE).glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            os.rmdir(STAGE)
        except OSError:
            pass
    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("an album is one conversion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
