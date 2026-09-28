<img src="logo.svg" alt="AnonBot" width="72" align="right">

# AnonBot

A Telegram bot that gives each of its users a permanent personal link.
Anyone who opens that link can send the owner an anonymous message, and the
owner can answer — as a real, threaded, two-way conversation, with the
sender's identity never revealed to them.

The owner is not anonymous; it is their own chat. Only the person writing to
them is.

Runs as its own process and its own deployment, from one repository with
its four siblings: the modules every bot shares live once, in `../shared`,
and each bot's Dockerfile builds from the repository root to take them with
it. It shares a Postgres database with four sibling
bots only in the sense that its tables live in a schema of their own inside
it (`DB_SCHEMA`); no other bot reads or writes them. The one shared area is
`family.*`, where the bot posts a heartbeat and any crash so a monitoring bot
can watch it — `FAMILY_BUS=off` disables that entirely.

---

## Commands

| command | what it does |
|---|---|
| `/start` | Instructions. The first `/start` from a new user asks which language to use, once; afterwards it prints the instructions in the language on record. |
| `/link` | The user's permanent inbox link. Carries Pause / Resume and New-link buttons, so the three commands below are rarely typed. |
| `/newlink` | Issues a new link and invalidates the old one. Conversations already open keep working — they are routed by conversation, not by token. |
| `/pause`, `/resume` | Stop or allow *new* conversations. Open ones are unaffected. |
| `/conversations` | The owner's own threads: how many are open, one card per conversation, a button that goes to where a conversation starts, and a button that archives one. |
| `/which` | Sent as a reply to any message: says which conversation that message belongs to. |
| `/archive` | Sent as a reply to any message: files that conversation away, after one confirmation. The same thing `/conversations` does from a card, without the walk to it. |
| `/blocked` | Lists blocked senders by conversation number, each with an Undo button. |
| `/stats` | How many distinct people have written, and how many conversations. |
| `/export` | Hands back one conversation, or all of them: copied back into the chat exactly as they were, or written into a document that names nobody. [PRIVACY.md](PRIVACY.md) says what each way reads and keeps. |
| `/cancel` | Asks which of the things the bot is waiting on should stop, one button each, and stops nothing until one is chosen. |
| `/donate` | Voluntary contribution towards hosting, paid in Telegram Stars. |
| `/balance` | The ⚡ credit balance — one balance shared by the family's four bots — and everything it was spent on. |
| `/paysupport` | How to reach the operator about a payment that went wrong. |
| `/language`, `/en`, `/uz`, `/rus` | Switch language. Each reprints the instructions in the language chosen. |
| `/help` | The instructions on their own. |
| `/privacy` | What the bot holds about the person asking, who else sees it, and how long it stays. |
| `/terms` | What the bot may be used for, and where the money stands. |
| `/deletemydata` | Erases what the bot holds about the person asking, after one confirmation. |

Opening somebody's link is `/start q_<token>`, which the bot handles
automatically; it is not a command anyone types.

Three further commands — `/dbdump`, `/messageas` and `/status` — are
restricted to the account ids in `ABOT_ADMIN_ID`. To everyone else they
answer exactly as a misspelt command does, so their existence is not
disclosed.

---

## Privacy and terms

The bot holds personal data from the first message it receives: a Telegram
user id is the only thing a bot can address a person by, so there is no
opting out of that one while the bot is in use. `/privacy` says what else is
kept, who else sees it and how long it stays; `/terms` says what the bot may
be used for; `/deletemydata` erases it, immediately and without a form.

All three speak whichever of the three languages the person has chosen.
The long forms are [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md), which
is also what the privacy-policy link in Telegram's own bot settings points
at.

---

## How a conversation is routed

The problem this bot solves is that one person's inbox link is posted
somewhere public, so a single chat ends up holding conversations with many
different strangers at once. Every message therefore has to say which
conversation it belongs to, and the bot has to be certain before it delivers
anything — a message sent to the wrong stranger is the one failure that
cannot be taken back.

**Every message is a reply, and Telegram's own reply feature is what says
so.** Swiping a message, or holding it and choosing Reply, produces a Telegram
reply, which carries the id of the message being answered. That is the only
route into a conversation. The ↩️ button under each message explains the
gesture and sends nothing itself.

**`anon_relay` maps `(chat_id, message_id) → conversation_id`** for every
message the bot delivers, into either chat. A reply is resolved through that
lookup rather than through "whichever button was tapped last", so it is
correct with several conversations interleaved and answered in any order. A
matched row says which *conversation*; the sender's own account id says which
*direction*.

**Each row also names its counterpart in the other chat**, which is what
makes an answer to an older message arrive on that message. A conversation
exists twice, once in each chat, and Telegram numbers messages per chat — so
the two sides never agree on what a message is called, and one uncaptioned
sticker is two bubbles on the receiving side against one on the sending
side. Recording the pairing at the moment of relaying is the only way to
know it: it cannot be worked out afterwards from ids that count at different
rates.

**Both sides are held to the same rule.** Either party may answer any
conversation they are part of, for as long as its rows live — 180 days of
silence in that conversation.

**One message per conversation is exempt: the one that opens it.** It has
nothing above it to answer. The exemption applies only while the conversation
just opened is still the newest thing in the chat, so that a message meant for
one thread cannot open another. When something has happened in between, the
line announcing the conversation is still a valid thing to reply to — it is a
relay row from the moment it is sent — and the refusal points at it.

**An album is one message.** Several photos sharing a `media_group_id` are
routed by the first of them and announced once, rather than being treated as
three unrelated messages.

**Nothing is delivered on a guess, and nothing is kept.** A message that
names no conversation is refused: not sent, and not stored anywhere, including
where exactly one destination would have been possible. The refusal says which
message to reply to and is sent as a reply to it.

**No message content is stored.** The bot keeps which accounts are talking,
which message id belongs to which conversation, and nothing else. What anyone
writes is relayed and exists only in the two Telegram chats, where each side
can delete their own copy.

**Whose words are whose.** Everything a person wrote arrives in italics;
everything the bot says for itself stays upright. Both land in the same
stream of bubbles, and that one difference separates them at a glance.

**No pseudonyms, no identifiers.** Nothing shown to either side is derived
from the other's account — including the data inside buttons, which travels
to the client. The owner's threads are numbered, because one chat holds all
of them and they have to be told apart; that number is the owner's index for
their own inbox and is never shown to a guest, for whom it would be both
meaningless and a count of the owner's other correspondents.

---

## Keeping track of many conversations at once

`/conversations` is the owner's view of their own inbox. It opens on a count
of what is open and a button per thread, most recently active first, and
pages when there are more than six. Tapping one opens its card: when the
conversation started, when it was last spoken in, and two things that can be
done with it.

**Go to the start** puts a bookmark in the chat, quoting that conversation's
earliest surviving message. Tapping the quote scrolls there — a private chat
with a bot has no message links, so a reply-quote is the only way to point at
something further up. The bookmark also belongs to that conversation, so
replying to it writes into the thread without having to find a message from
it first.

**Archive** is filing and nothing else. An archived conversation leaves the
open list and the open count; nothing is refused because of it, and the guest
is never told. Anything either side sends into an archived thread brings it
straight back, because a message arriving means it was not finished. It can
be jumped to, and un-archived by hand, for as long as it is on record at all.
Archiving is not blocking: `/blocked` is what stops a person writing.

Conversations are archived automatically once they have been silent long
enough for their reply anchors to be discarded — at that point neither side
can write into the thread any more, so the count of what is open stays a
count of what can still be reached.

`/which`, sent as a reply to any message, says which conversation that
message is in. Every message the bot delivers already carries a header
saying so; `/which` answers for everything else in the chat — the owner's own
outgoing messages, the second and third photo of an album, the later bubbles
of a message too long for one.

`/archive`, sent the same way, files that conversation out of the open list
after one confirmation naming it. Archiving is reversible without doing
anything: a thread that somebody writes into comes straight back, and the
other side is never told it was filed.

### Limits

An inbox link is meant to be posted publicly, so both a per-minute message
limit and a cooldown between opening conversations apply per account, and a
ceiling on updates per minute applies before any handler runs. All are
configurable; see `.env.example`.

---

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in ABOT_TOKEN and ABOT_USERNAME
python bot.py
```

`ABOT_TOKEN` and `ABOT_USERNAME` come from
[@BotFather](https://t.me/BotFather). `ABOT_ADMIN_ID` is optional and takes
one or more numeric account ids.

The bot needs a Postgres database. `DATABASE_URL` points at it and
`DB_SCHEMA` (default `anon_bot`) selects the schema; the schema and its
tables are created on first run.

### Deploying

Set the same values as environment variables on the host and run
`python bot.py`. `railway.json` and `nixpacks.toml` configure a Railway
deployment; neither is required elsewhere.

A deployment replaces the running container, and the bot is built so that
costs nothing visible. The new process waits on a Postgres advisory lock
until the old one has stopped polling, so Telegram never sees two consumers
of one token. Open conversations and half-written state are restored from a
`runtime_state` table. Anyone whose message was mid-flight is told, rather
than left waiting. Updates sent during the gap are held by Telegram and
delivered on the first poll — nothing is lost. `DEPLOY_SAFETY=off` disables
all of it.

### Keeping two databases in sync

`db_merge.py` reconciles a local database with a remote one additively —
it never deletes or overwrites. It also remaps `anon_conversations`' internal
ids across the two so the tables that reference them stay correct.

```bash
python db_merge.py --from local --into cloud --dry-run
python db_merge.py --from local --into cloud
```

---

## Files

| file | |
|---|---|
| `bot.py` | Handlers, and the router that decides where a message goes |
| `anon_logic.py` | Message relaying, text splitting, styling, flood control |
| `db.py` | This bot's schema, queries and connection pool |
| `i18n.py` | English, Uzbek and Russian strings |
| `family_link.py` | Heartbeats, crash reporting, and the command queue a monitoring bot uses |
| `lifecycle.py` | Surviving a redeploy: one poller at a time, state in Postgres |
| `live_message.py` | When a bot message may keep evolving in place |
| `shared_features.py` | Donations, logging, activity tracking, flood control |
| `db_merge.py` | Reconciles two databases, additively |

`family_link.py`, `lifecycle.py`, `live_message.py` and `shared_features.py`
are shared with the sibling bots by being copied rather than imported: each
bot is a separate deployment, so nothing crosses a repository boundary.

## Requirements

Python 3.11 or newer, and Postgres 16 or newer.

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).

This is the licence the AGPL'd PyMuPDF asks for, and §13 of it is the reason:
anybody who interacts with this software over a network must be offered its
source. A Telegram bot is exactly that case, since nobody using it ever holds
a copy. The source is here, which satisfies §13 for this deployment; anybody
running a modified version as a service has to publish their changes too.
