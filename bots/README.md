# bots

[↑ MumuBots](../README.md)

One folder per bot. A folder's name is also the bot's database schema, so it stays as it is.

| Bot | Folder | Settings prefix | What it does |
|---|---|---|---|
| StickerBot | [sticker_bot](sticker_bot/README.md) | `SBOT_` | sticker packs from pictures, GIFs, videos and other stickers |
| ConvertBot | [convert_bot](convert_bot/README.md) | `CBOT_` | files from one format to another, and text from pictures |
| DownloaderBot | [downloader_bot](downloader_bot/README.md) | `DBOT_` | the media behind Instagram, TikTok, X, Pinterest and Reddit links |
| AnonBot | [anon_bot](anon_bot/README.md) | `ABOT_` | anonymous inboxes and conversations |
| ManagerBot | [manager_bot](manager_bot/README.md) | `MBOT_` | the operator's panel: status, usage, problem reports, broadcasts, backups, restarts |

Every bot folder has the same core: `bot.py` (the handlers and `main()`, which builds the Application and starts
polling), `db.py` (the bot's tables, in its own schema), `i18n.py` (its texts in English, Uzbek and Russian;
ManagerBot has none) and `db_merge.py` (a one-off merge of two databases, run with `main.py --module`). The rest is
the bot's own. Code every bot uses is in [shared/](../shared/README.md); each bot gets its own copy when it loads.

Each `.py` has a same-named `.md` beside it with its notes.
