# AnonBot

[↑ bots](../README.md)

Anonymous inboxes: a permanent link anyone can write to, and a threaded conversation after that. Settings prefix `ABOT_`, database schema `anon_bot`.

| File | What it is |
|---|---|
| [bot.py](bot.py) · [notes](bot.md) | the handlers and `main()`: links, relaying, replies, reactions, blocks |
| [db.py](db.py) · [notes](db.md) | AnonBot's tables, every person and chat in them as a keyed pseudonym |
| [i18n.py](i18n.py) · [notes](i18n.md) | every text AnonBot shows, in English, Uzbek and Russian |
| [anon_logic.py](anon_logic.py) · [notes](anon_logic.md) | the parts that need no Telegram: cutting long messages, the flood limit |
| [db_merge.py](db_merge.py) · [notes](db_merge.md) | one-off: merging two AnonBot databases |

Code every bot uses is in [shared/](../../shared/README.md).
