"""An update the owner announces in advance: who gets turned away, what they
are told, and who is owed a message when it is over.

Everything that touches Postgres is replaced with a dict here -- the SQL is
four statements and a real database would only test psycopg. What is worth
pinning down is the decision each one is made on, because getting any of them
backwards is invisible until an update is already running."""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".mirror", "sticker_bot"))
sys.path.insert(0, os.path.join(HERE, "stubs"))
os.environ["DATABASE_URL"] = "postgresql://stub"
os.environ.setdefault("SBOT_TOKEN", "1:AA")
os.environ.setdefault("SBOT_USERNAME", "x")

import i18n
import lifecycle
import family_link
import shared_features

failures = []
def equals(name, got, want):
    """Named for what it does, because the rest of this suite spells an
    assertion check(name, ok, detail) -- and a comparison written in that
    shape by mistake passes whenever `got` is truthy, which is a test that
    reports success without testing anything."""
    if got != want: failures.append(f"{name}: got {got!r}, want {want!r}")
    else: print(f"  ok  {name}")


# ---- the flag, and the estimate that is only ever an estimate ----
WAITLIST = {}

def fake_hold(user_id, chat_id, bot_id=None):
    WAITLIST.setdefault(bot_id or "stickerbot", {}).setdefault(user_id, chat_id)

def fake_take(bot_id):
    return sorted(WAITLIST.pop(bot_id, {}).items())

lifecycle.hold_for_update = fake_hold
lifecycle.take_held = fake_take


def paused_for(minutes):
    lifecycle._maintenance_on = True
    lifecycle._maintenance_until = (
        None if minutes is None
        else datetime.now(timezone.utc) + timedelta(minutes=minutes)
    )


def open_again():
    lifecycle._maintenance_on = False
    lifecycle._maintenance_until = None
    lifecycle._draining = False


async def main():
    open_again()
    equals("nothing is paused to start with", lifecycle.is_paused(), False)

    # A stop signal and an announced update both stop new work, and only the
    # first of them means "this process is going away".
    lifecycle._draining = True
    equals("a stop signal pauses new work", lifecycle.is_paused(), True)
    equals("...but is not maintenance", lifecycle.in_maintenance(), False)
    open_again()

    paused_for(10)
    equals("an announced update pauses new work", lifecycle.is_paused(), True)
    equals("...and is maintenance", lifecycle.in_maintenance(), True)
    equals("...and quotes a whole number of minutes",
          lifecycle.maintenance_minutes_left(), 10)

    # The estimate running out does NOT reopen the bot. Only /finishupdates
    # does -- which is the whole reason the owner asked for a manual end.
    paused_for(-5)
    equals("an estimate that has passed stops being quoted",
          lifecycle.maintenance_minutes_left(), None)
    equals("...and the bot stays shut anyway", lifecycle.is_paused(), True)

    open_again()
    equals("with nothing announced there is no estimate",
          lifecycle.maintenance_minutes_left(), None)

    # ---- what a user is actually told ----
    open_again()
    equals("nothing to say when the bot is open",
          await shared_features.refuse_new_work("en", 7, 7), None)

    lifecycle._draining = True
    WAITLIST.clear()
    drain = await shared_features.refuse_new_work("en", 7, 7)
    equals("a stop signal answers 'send it again in a moment'",
          drain, i18n.t("en", "restarting_send_again"))
    # Nobody is coming back to tell them, so nobody is written down: the
    # sentence they got is already the whole promise.
    equals("...and does not write anybody down", WAITLIST, {})
    open_again()

    paused_for(10)
    WAITLIST.clear()
    said = await shared_features.refuse_new_work("uz", 42, 99)
    equals("an announced update answers with the estimate, in their language",
          said, i18n.t("uz", "update_soon_try_later", minutes=10))
    equals("...and writes them down against the chat to answer in",
          WAITLIST, {"stickerbot": {42: 99}})

    # Trying four times during one update is one person to apologise to.
    for _ in range(3):
        await shared_features.refuse_new_work("uz", 42, 99)
    equals("...once, however many times they try",
          WAITLIST, {"stickerbot": {42: 99}})

    paused_for(-1)
    said = await shared_features.refuse_new_work("ru", 8, 8)
    equals("past the estimate it stops promising a time",
          said, i18n.t("ru", "update_soon_try_later_soon"))
    open_again()

    # ---- the four sentences, and the bot that has no translations ----
    equals("a public bot translates them",
          family_link.phrase("update_done_try_now", "ru"),
          i18n.t("ru", "update_done_try_now"))
    equals("an unknown language still says something",
          bool(family_link.phrase("update_done_try_now", "de")), True)
    real_i18n = family_link._i18n
    family_link._i18n = lambda: None          # ManagerBot has no i18n.py
    equals("a bot with no translations falls back to English",
          family_link.phrase("update_done_try_now"),
          family_link._PLAIN["update_done_try_now"])
    equals("...including the one that takes a number",
          "7" in family_link.phrase("update_soon_try_later", minutes=7), True)
    family_link._i18n = real_i18n

    # ---- who gets told it is over ----
    WAITLIST.clear()
    paused_for(5)
    await shared_features.refuse_new_work("en", 1, 11)
    await shared_features.refuse_new_work("en", 2, 22)
    equals("everyone turned away is waiting", fake_take("stickerbot"), [(1, 11), (2, 22)])
    equals("and taking them empties the list -- a second /finishupdates is quiet",
          fake_take("stickerbot"), [])
    open_again()

    # ---- warnbusy warns the people whose work really is about to vanish ----
    told = []
    class Bot:
        async def send_message(self, chat_id, text, **kw):
            told.append((chat_id, text))
    class Ctx:
        bot = Bot()

    family_link._bot_id = "stickerbot"
    family_link.BROADCAST_PER_SECOND = 0      # no pacing in a test
    out, _, _ = await family_link._cmd_warnbusy(Ctx(), [])
    equals("nothing in flight means nobody is warned", told, [])
    equals("...and it says so", "Nobody is mid-anything" in out, True)

    async with lifecycle.busy(555, "you were converting something"):
        async with lifecycle.busy(555, "and something else"):
            await family_link._cmd_warnbusy(Ctx(), [])
    # One person, two jobs, one warning -- they are a person, not a queue.
    equals("someone mid-something is warned once", len(told), 1)
    equals("...in the chat that is waiting", told[0][0], 555)

    print()
    if failures:
        print("FAILURES:")
        for f in failures: print("  -", f)
        return 1
    print("all maintenance checks passed")
    return 0

sys.exit(asyncio.get_event_loop().run_until_complete(main()))
