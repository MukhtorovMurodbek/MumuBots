"""DownloaderBot's large files: over 48 MB, only with a yes, for credit.

    python tests/test_large_files.py

The owner, 2026-09-28: "Make downloader bot be able to download and send
files over 50 MB, not for free of course, with a switch to allow or not allow
the bot to download those large files, with default set to off. This is for
the user to not spend their credit unwillingly. Also bot should notify that a
download this big will cost credit." What each of those means, driven through
the real _resolve_and_send with the routes, the wallet and Telegram faked:

  off by default; a file over the free ceiling is then refused with the price
  and a button to switch it on -- nothing past the ceiling is fetched, nothing
  is charged -- and the button switches it on;
  on, the bot says it will cost credit before it fetches anything, fetches up
  to the large ceiling, charges ⚡10 per 100 MB begun once the size is known,
  sends past 50 MB over MTProto, and leaves nothing on disk;
  a balance that cannot cover the least a large file costs fetches nothing; one
  that cannot cover this one is charged nothing; a send that fails is refunded;
  a group is sent to a private chat; past 2000 MB is refused, free of charge;
  and a file found too big halfway through downloading takes the same path.
"""
import asyncio
import os
import pathlib
import sys
import tempfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
MIRROR = HERE / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR))
sys.path.insert(0, str(HERE / "stubs"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="downloaderbot-large-"))
os.environ.update(ENV_FILES="off", DBOT_TOKEN="123:test", DBOT_USERNAME="test", DATABASE_URL="postgresql://stub",
                  DBOT_WORK_DIR=str(SCRATCH), DBOT_API_ID="12345", DBOT_API_HASH="0123456789abcdef",
                  DBOT_SENT_FILES="0")      # the file-id store has its own test (test_sent_files.py)
for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "DBOT_LARGE_MAX_MB", "DBOT_LARGE_CREDITS_PER_100MB",
            "DBOT_MAX_DOWNLOAD_MB"):
    os.environ.pop(key, None)

import big_files       # noqa: E402
import bot             # noqa: E402
import i18n            # noqa: E402
import large_files     # noqa: E402
import net             # noqa: E402
import problems        # noqa: E402
import resolvers       # noqa: E402

MB = 1024 * 1024
PRIVATE, GROUP, USER = 7, -100123, 7
failures = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
# The world, faked
# ---------------------------------------------------------------------------

class World:
    def __init__(self, size_mb=0, large_on=False, balance=0, send_fails=False, too_big_even_large=False,
                 too_big_while_downloading=False):
        self.size = int(size_mb * MB)
        self.large_on, self.balance = large_on, balance
        self.send_fails, self.too_big_even_large = send_fails, too_big_even_large
        self.too_big_while_downloading = too_big_while_downloading
        self.resolves, self.downloads, self.mtproto, self.bot_api, self.ledger = [], [], [], [], []
        self.shown = []


W = World()


async def fake_resolve(platform, url, max_mb=None):
    W.resolves.append(max_mb)
    fits = W.size <= (max_mb or large_files.FREE_MAX_MB) * MB
    if not fits and (max_mb is None or W.too_big_even_large) and not W.too_big_while_downloading:
        raise resolvers.NothingWorked(platform, {"ytdlp": resolvers.ProviderFailed("over the ceiling", "too_big")})
    return resolvers.Resolved(platform, "fake", [resolvers.MediaItem("video", url="https://cdn/x.mp4",
                                                                     filename="clip.mp4")])


async def fake_download(item, work_dir=None, max_mb=None):
    W.downloads.append(max_mb)
    if W.size > (max_mb or large_files.FREE_MAX_MB) * MB:
        raise net.TooLarge("That file is too big to send through Telegram.")
    path = SCRATCH / f"file-{len(W.downloads)}.mp4"
    with open(path, "wb") as handle:
        handle.truncate(W.size)                   # the size, without writing the bytes
    return str(path)


async def fake_send_document(chat_id, path, filename, caption="", progress=None):
    if W.send_fails:
        raise RuntimeError("FLOOD_WAIT_X")
    W.mtproto.append((chat_id, os.path.getsize(path), filename))


async def fake_send_media(update, context, items_with_paths, lang, **kwargs):
    W.bot_api.append([os.path.getsize(path) for _, path in items_with_paths])


def fake_spend(user_id, amount, reason="spend", detail=None):
    if W.balance < amount:
        return None
    W.balance -= amount
    W.ledger.append((reason, -amount))
    return W.balance


def fake_move(user_id, delta, reason, detail=None, stars_paid=0):
    W.balance += delta
    W.ledger.append((reason, delta))
    return W.balance


class Status:
    async def set(self, bot_, text, **kwargs):
        W.shown.append((text, kwargs.get("reply_markup")))
        return self

    async def delete(self, *a, **k):
        return None


@asynccontextmanager
async def no_busy(*args, **kwargs):
    yield


async def english(user_id, context=None):
    return "en"

resolvers.resolve, resolvers.download = fake_resolve, fake_download
big_files.send_document = fake_send_document
bot._send_media = fake_send_media
bot.family_link.star_balance = lambda user_id: W.balance
bot.family_link.spend_stars = fake_spend
bot.family_link.move_stars = fake_move
bot.get_large_files_enabled = lambda user_id: W.large_on
bot.set_large_files_enabled = lambda user_id, enabled: setattr(W, "large_on", enabled)
bot.get_caption_enabled = lambda user_id: False
bot.get_lossless_enabled = lambda user_id: False
bot._remember_delivery = lambda *a, **k: "token"
bot.lifecycle.busy = no_busy
i18n.get_lang = english
big_files.CONFIGURED = large_files.CONFIGURED = True


def update_in(chat_id):
    message = SimpleNamespace(chat_id=chat_id, message_id=1)
    return SimpleNamespace(effective_user=SimpleNamespace(id=USER), effective_chat=SimpleNamespace(id=chat_id),
                           effective_message=message, message=message)


async def download(world, chat_id=PRIVATE):
    global W
    W = world
    context = SimpleNamespace(bot=SimpleNamespace(), user_data={})
    sent = await bot._resolve_and_send(update_in(chat_id), context, "youtube", "https://youtu.be/x", status=Status())
    return sent


def texts():
    return [text for text, _ in W.shown]


def t(key, **fields):
    return i18n.t("en", key, **fields)


def left_on_disk():
    return [p.name for p in SCRATCH.glob("file-*")]


async def main():
    free, per = large_files.FREE_MAX_MB, large_files.first_price()
    price = large_files.price_for

    print("== the price ==")
    check("⚡7 for anything up to 100 MB", price(1) == 7 and price(100 * MB) == 7 and per == 7,
          (price(1), price(100 * MB)))
    check("more for every 100 MB begun after, but less each time: 101 MB is ⚡12, 2000 MB ⚡76",
          price(100 * MB + 1) == 12 and price(2000 * MB) == 76 and large_files.top_price() == 76,
          [price(n * MB) for n in (101, 2000)])
    check("up to Telegram's 2000 MB, from a free ceiling of 48", large_files.LARGE_MAX_MB == 2000 and free == 48)

    print("\n== off: the default ==")
    sent = await download(World(size_mb=60))
    check("a file over the free ceiling is not sent", not sent and not W.mtproto and not W.bot_api)
    offer = W.shown[-1]
    check("the person is told what it would cost, and where the switch is",
          offer[0] == t("large_files_off", free=free, per=per), texts()[-1:])
    button = offer[1].inline_keyboard[0][0] if offer[1] else None
    data = getattr(button, "callback_data", None) or getattr(button, "kwargs", {}).get("callback_data")
    check("with a button that switches it on", data == "large:offer", data)
    check("nothing past the free ceiling was fetched", W.resolves == [None] and W.downloads == [], W.resolves)
    check("and nothing was charged", not W.ledger)
    check("which carries DL-LARGE-OFF, with no Report button -- the person can act on it",
          offer[0].endswith("DL-LARGE-OFF") and not problems.reportable("DL-LARGE-OFF"))

    print("\n== the switch ==")
    W.large_on = False
    edits = []
    query = SimpleNamespace(data="large:offer", message=SimpleNamespace(),
                            answer=lambda text=None: asyncio.sleep(0))
    real_edit = bot.edit_in_place
    bot.edit_in_place = lambda message, bot_, text, **kw: edits.append(text) or asyncio.sleep(0)
    try:
        await bot.large_toggle_callback(SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=USER)),
                                        SimpleNamespace(bot=None, user_data={}))
        check("the button under the refusal switches large files on", W.large_on is True)
        check("and says to send the link again", edits and edits[-1] == t("large_files_turned_on", per=per), edits)
        query.data = "large:off"
        await bot.large_toggle_callback(SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=USER)),
                                        SimpleNamespace(bot=None, user_data={}))
        check("and /settings switches it off again", W.large_on is False)
    finally:
        bot.edit_in_place = real_edit

    print("\n== on ==")
    sent = await download(World(size_mb=250, large_on=True, balance=100))
    check("the file is sent", sent, texts())
    fetching = t("large_files_fetching", free=free, per=per)
    check("the person is told it costs credit before anything is fetched",
          fetching in texts() and texts().index(fetching) < len(texts()) - 1, texts())
    check("it is fetched up to the large ceiling", W.resolves == [None, 2000] and W.downloads == [2000],
          (W.resolves, W.downloads))
    check("charged once it is here, by the 100 MB begun: 250 MB is ⚡16",
          W.ledger == [("spend", -price(250 * MB))] and W.balance == 100 - price(250 * MB) and price(250 * MB) == 16,
          W.ledger)
    check("sent over MTProto, being past 50 MB", W.mtproto and W.mtproto[0][1] == 250 * MB and not W.bot_api,
          (W.mtproto, W.bot_api))
    check("and the person is told what it cost and what is left",
          texts()[-1] == t("large_files_charged", mb=250, price=16, balance=84), texts()[-1:])
    check("nothing is left on disk", not left_on_disk(), left_on_disk())

    sent = await download(World(size_mb=49, large_on=True, balance=100))
    check("a file just over the free ceiling but under 50 MB goes through the Bot API, for ⚡7",
          sent and W.bot_api == [[49 * MB]] and not W.mtproto and W.ledger == [("spend", -7)], (W.bot_api, W.ledger))

    print("\n== when it cannot ==")
    sent = await download(World(size_mb=60, large_on=True, balance=5))
    check("a balance under the least it costs: nothing fetched", not sent and W.resolves == [None] and not W.ledger,
          W.resolves)
    check("and said, with the balance", texts()[-1] == t("large_files_no_credit", free=free, per=per, balance=5))

    sent = await download(World(size_mb=450, large_on=True, balance=20))
    check("a balance that covers the least but not this one: nothing charged",
          not sent and not W.ledger and W.balance == 20, W.ledger)
    check("the price said, and the file deleted",
          texts()[-1] == t("large_files_short", mb=450, price=price(450 * MB), balance=20)
          and price(450 * MB) > 20 and not left_on_disk(), texts()[-1:])

    sent = await download(World(size_mb=300, large_on=True, balance=100, send_fails=True))
    check("a send that fails gives the credit back", not sent and W.ledger == [("spend", -16), ("refund", 16)]
          and W.balance == 100, W.ledger)
    check("and says so", texts()[-1].startswith(t("large_files_send_failed", price=16, balance=100))
          and texts()[-1].endswith("DL-LARGE-SEND") and problems.reportable("DL-LARGE-SEND"), texts()[-1:])
    check("and leaves nothing on disk", not left_on_disk(), left_on_disk())

    sent = await download(World(size_mb=2500, large_on=True, balance=1000, too_big_even_large=True))
    check("past 2000 MB: refused, free of charge", not sent and not W.ledger
          and texts()[-1] == t("large_files_too_big", max=2000), texts()[-1:])

    sent = await download(World(size_mb=80, large_on=True, balance=100), chat_id=GROUP)
    check("in a group: sent to a private chat, nothing fetched past the ceiling",
          not sent and W.resolves == [None] and texts()[-1] == t("large_files_private_only", free=free),
          texts()[-1:])

    print("\n== too big found while downloading ==")
    sent = await download(World(size_mb=120, large_on=True, balance=100, too_big_while_downloading=True))
    check("a direct download that runs past the free ceiling takes the same path",
          sent and W.downloads == [None, 2000] and W.ledger == [("spend", -12)] and W.mtproto,
          (W.downloads, W.ledger))
    check("and leaves nothing behind", not left_on_disk(), left_on_disk())

    print("\n== an API id and hash Telegram refuses ==")
    # What big_files.verify() does as the bot starts, when Telegram refuses
    # the login: every module that worked a limit out from it is told.
    for hook in big_files._on_disabled:
        hook()
    check("large files are off, however the person set the switch", large_files.CONFIGURED is False)
    sent = await download(World(size_mb=60, large_on=True, balance=100))
    check("a file over the free ceiling gets the plain too-big sentence, as before large files existed",
          not sent and not W.ledger and not W.mtproto and W.resolves == [None]
          and texts()[-1].startswith(t("download_too_big").split("{")[0][:40]), (W.resolves, texts()[-1:]))
    sent = await download(World(size_mb=30, large_on=True, balance=100))
    check("and one under it is sent, free, as ever", sent and W.bot_api and not W.ledger, (W.bot_api, W.ledger))

    print(f"\nFAILURES: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
