# StickerBot

[↑ bots](../README.md)

Makes Telegram sticker packs out of pictures, GIFs, videos and other stickers, in a private chat or in a group. Settings prefix `SBOT_`, database schema `sticker_bot`.

| File | What it is |
|---|---|
| [bot.py](bot.py) · [notes](bot.md) | the handlers and `main()`: packs, adding, emoji, co-editing links, payments |
| [db.py](db.py) · [notes](db.md) | StickerBot's tables: packs, co-editors, share links, group members |
| [i18n.py](i18n.py) · [notes](i18n.md) | every text StickerBot shows, in English, Uzbek and Russian |
| [group_mode.py](group_mode.py) · [notes](group_mode.md) | StickerBot in group chats, and retagging a sticker by replying to it |
| [sticker_ops.py](sticker_ops.py) · [notes](sticker_ops.md) | adding a sticker and changing its emoji, for private chats and groups alike |
| [image_utils.py](image_utils.py) · [notes](image_utils.md) | any picture as a PNG that Telegram accepts as a static sticker |
| [video_sticker.py](video_sticker.py) · [notes](video_sticker.md) | GIFs and videos as video stickers that fit Telegram's limits |
| [emoji_utils.py](emoji_utils.py) · [notes](emoji_utils.md) | telling an emoji message from anything else |
| [import_utils.py](import_utils.py) · [notes](import_utils.md) | copying stickers in from another pack, or from a WhatsApp pack file |
| [db_merge.py](db_merge.py) · [notes](db_merge.md) | one-off: merging two StickerBot databases |

Code every bot uses is in [shared/](../../shared/README.md).
