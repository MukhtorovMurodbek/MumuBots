"""ManagerBot's half of /feedback, driven end to end.

    python tests/feedback_scenarios.py

The public bots' half -- asking, taking the answer, never taking a reply
that was meant for somebody else -- is tests/anon_scenarios.py group T. This
is what happens next: ManagerBot's real Application, on tests/sim.py's
in-process Telegram, with feedback rows in a real Postgres as a public bot
leaves them.

    each piece reaches the owner as it arrives, by default, and says nothing
    about who sent it; muted alerts hold it back; the switch moves it to the
    22:00 report, which then carries it; a long one is cut with a way to the
    rest; a long night of it does not cost the report; strangers get nothing.

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(ROOT / "tests"))
os.chdir(ROOT / "manager_bot")
sys.path.insert(0, str(ROOT / "manager_bot"))
sys.path.insert(1, str(ROOT / "shared"))

OWNER, STRANGER = 5001, 7777
os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test"))
os.environ["DB_SCHEMA"] = "manager_bot_sim"
os.environ["MBOT_TOKEN"] = "1:AA"
os.environ["MBOT_USERNAME"] = "managerbot_test"
os.environ["MBOT_ADMIN_ID"] = str(OWNER)
os.environ["DEPLOY_SAFETY"] = "off"
os.environ["MBOT_ROLLCALL_SECONDS"] = "0"

import sim  # noqa: E402
import db  # noqa: E402
import family_link  # noqa: E402
import bot as mb  # noqa: E402
from telegram.ext import CallbackContext  # noqa: E402

failures: list[str] = []
_checks = 0


def check(label: str, condition: bool, detail: str = "") -> bool:
    global _checks
    _checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}" + ("" if condition else f"\n       {detail}"))
    if not condition:
        failures.append(label)
    return bool(condition)


def add_feedback(bot_id: str, lang: str, body: str) -> int:
    """A row exactly as family_link.save_feedback leaves one in a public bot."""
    with db.pooled() as conn:
        row = conn.execute("INSERT INTO family.feedback (bot_id, lang, body) VALUES (%s, %s, %s) "
                           "RETURNING id", (bot_id, lang, body)).fetchone()
        conn.commit()
    return int(row[0])


def pending() -> int:
    with db.pooled() as conn:
        return conn.execute("SELECT count(*) FROM family.feedback WHERE delivered_at IS NULL").fetchone()[0]


def texts(person) -> list[str]:
    return [m.get("text") or "" for m in person.chat]


async def main() -> int:
    tele = sim.FakeTelegram(bot_username="managerbot_test")
    s, _ = await sim.build(mb, tele)
    owner, stranger = s.person(OWNER, "Owner"), s.person(STRANGER, "Stranger")
    context = CallbackContext(s.app)
    with db.pooled() as conn:
        conn.execute("DELETE FROM family.feedback")
        conn.execute("DELETE FROM family.settings WHERE key IN (%s, 'alerts')",
                     (family_link.FEEDBACK_DELIVERY_KEY,))
        conn.commit()
    mb._feedback_mode_cache.update(value=None, read_at=0.0)

    print("\n== as it arrives, by default ==")
    first = add_feedback("convertbot", "uz", "HEIC → PNG <faster> & cheaper, please")
    await mb.event_pump(context)
    got = [t for t in texts(owner) if t.startswith("💬 <b>Feedback</b>")]
    check("a new piece reaches the owner on the next sweep", len(got) == 1, str(texts(owner)[-2:]))
    check("with the bot, the language and the words, escaped",
          bool(got) and "ConvertBot · uz" in got[0]
          and "HEIC → PNG &lt;faster&gt; &amp; cheaper, please" in got[0], got[:1])
    check("and nothing about who wrote it",
          bool(got) and not any(w in got[0] for w in ("user", "@", str(STRANGER))), got[:1])
    check("it is marked as sent", pending() == 0, pending())
    await mb.event_pump(context)
    check("and not sent twice", len([t for t in texts(owner) if t.startswith("💬 <b>Feedback</b>")]) == 1)

    print("\n== muted alerts hold it ==")
    db.set_setting("alerts", "off")
    add_feedback("anonbot", "ru", "muted one")
    before = len(owner.chat)
    await mb.event_pump(context)
    check("nothing arrives while alerts are off", len(owner.chat) == before, str(texts(owner)[-1:]))
    check("and it waits, unsent", pending() == 1, pending())
    await mb.nightly_problem_report(context)
    report = owner.chat[-1].get("text") or ""
    check("the 22:00 report carries what the mute held back",
          "💬 <b>Feedback — 1</b>" in report and "muted one" in report, report[-300:])
    check("after which nothing is waiting", pending() == 0, pending())
    db.set_setting("alerts", "on")

    print("\n== the switch ==")
    await owner.send("/feedback")
    overview = owner.chat[-1]
    check("/feedback shows the latest and how they arrive",
          "Each piece reaches you as it arrives" in (overview.get("text") or "")
          and "muted one" in (overview.get("text") or ""), (overview.get("text") or "")[:300])
    check("with the switch under it", any("As it arrives" in b for b in owner.buttons(overview["message_id"])),
          owner.buttons(overview["message_id"]))
    await owner.tap("With the 22:00 report", overview["message_id"])
    check("one tap moves it to the nightly report",
          db.get_setting(family_link.FEEDBACK_DELIVERY_KEY) == family_link.FEEDBACK_NIGHTLY
          and "with the 22:00 report" in ((tele.find(OWNER, overview["message_id"]) or {}).get("text") or ""),
          db.get_setting(family_link.FEEDBACK_DELIVERY_KEY))
    add_feedback("stickerbot", "en", "kept for tonight")
    before = len(owner.chat)
    await mb.event_pump(context)
    check("so a new piece does not arrive on its own", len(owner.chat) == before, str(texts(owner)[-1:]))
    await owner.send("/problems")
    check("/problems on demand only counts the day's feedback, and sends nothing on",
          "3 feedback message(s) in the last 24h" in (owner.chat[-1].get("text") or "")
          and "kept for tonight" not in (owner.chat[-1].get("text") or "") and pending() == 1,
          (owner.chat[-1].get("text") or "")[-200:])
    await mb.nightly_problem_report(context)
    report = owner.chat[-1].get("text") or ""
    check("the nightly report carries it", "kept for tonight" in report and "StickerBot · en" in report,
          report[-300:])
    check("and it is sent", pending() == 0, pending())
    await owner.send("/feedback instant")
    check("/feedback instant switches back",
          db.get_setting(family_link.FEEDBACK_DELIVERY_KEY) == family_link.FEEDBACK_INSTANT)

    print("\n== long ones ==")
    long_id = add_feedback("downloaderbot", "en", "word " * 900)
    await mb.event_pump(context)
    last = owner.chat[-1].get("text") or ""
    check("a very long piece is cut, with where to read the rest",
          f"/feedback {long_id}</code> for the rest" in last and len(last) < 4096, len(last))
    await owner.send(f"/feedback {long_id}")
    check("/feedback <id> shows all of it",
          any(("word " * 800) in (m.get("text") or "") for m in owner.chat[-3:])
          or any(m.get("document") for m in owner.chat[-3:]), str(texts(owner)[-1][:80]))

    db.set_setting(family_link.FEEDBACK_DELIVERY_KEY, family_link.FEEDBACK_NIGHTLY)
    mb._feedback_mode_cache.update(value=None, read_at=0.0)
    for n in range(4):
        add_feedback("anonbot", "en", f"night {n} " + "z" * 2500)
    before = len(owner.chat)
    await mb.nightly_problem_report(context)
    new = [m.get("text") or "" for m in owner.chat[before:]]
    check("a long night of feedback goes in messages of its own",
          len(new) >= 3 and any(t.startswith("💬 <b>Feedback — 4</b>") for t in new), [t[:40] for t in new])
    check("each under Telegram's limit, and every piece in one of them",
          all(len(t) <= 4096 for t in new) and all(any(f"night {n}" in t for t in new) for n in range(4)))
    check("and the problems report still arrives whole",
          any(t.startswith("🌙 <b>Problems") for t in new), [t[:40] for t in new])
    db.set_setting(family_link.FEEDBACK_DELIVERY_KEY, family_link.FEEDBACK_INSTANT)

    print("\n== strangers ==")
    await stranger.send("/feedback")
    check("a stranger gets one flat sentence and no feedback",
          (stranger.chat[-1].get("text") or "") == "This is a private bot.", stranger.transcript())

    with db.pooled() as conn:
        conn.execute("DELETE FROM family.feedback")
        conn.execute("DELETE FROM family.settings WHERE key IN (%s, 'alerts')",
                     (family_link.FEEDBACK_DELIVERY_KEY,))
        conn.commit()
    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    db.close_pool()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
