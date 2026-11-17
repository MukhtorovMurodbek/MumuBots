# DownloaderBot

[↑ bots](../README.md)

Sends back the media behind a link to a public post, in a private chat, a group or a channel. Settings prefix `DBOT_`, database schema `downloader_bot`.

| File | What it is |
|---|---|
| [bot.py](bot.py) · [notes](bot.md) | the handlers and `main()`: links, the quality picker, settings, groups, channels |
| [db.py](db.py) · [notes](db.md) | DownloaderBot's tables: settings, the daily allowance, group and channel settings |
| [i18n.py](i18n.py) · [notes](i18n.md) | every text DownloaderBot shows, in English, Uzbek and Russian |
| [platforms.py](platforms.py) · [notes](platforms.md) | which site a link belongs to, and the two pages read for their text |
| [resolvers.py](resolvers.py) · [notes](resolvers.md) | a link turned into files, through whichever route still works |
| [video.py](video.py) · [notes](video.md) | the yt-dlp route |
| [net.py](net.py) · [notes](net.md) | one HTTP client for the process, and the check that only public addresses are fetched |
| [routewatch.py](routewatch.py) · [notes](routewatch.md) | a daily check of every download route |
| [delivery_cache.py](delivery_cache.py) · [notes](delivery_cache.md) | the last download, kept for ten minutes for its buttons |
| [sent_files.py](sent_files.py) · [notes](sent_files.md) | Telegram's file ids of posts already sent, so a post goes again without a download |
| [large_files.py](large_files.py) · [notes](large_files.md) | downloads over 48 MB: off until switched on, then paid for |
| [merge.py](merge.py) · [notes](merge.md) | an album as one PDF or ZIP |
| [cards.py](cards.py) · [notes](cards.md) | a text post as a picture |
| [groups.py](groups.py) · [notes](groups.md) | what a group's settings are, and how a tap changes them |
| [channels.py](channels.py) · [notes](channels.md) | the same for a channel |
| [probe.py](probe.py) · [notes](probe.md) | a command-line check of every route against sample links |
| [db_merge.py](db_merge.py) · [notes](db_merge.md) | one-off: merging two DownloaderBot databases |

Code every bot uses is in [shared/](../../shared/README.md).
