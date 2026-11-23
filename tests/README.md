# tests

[↑ MumuBots](../README.md)

The test suite. It needs a Postgres it may create and drop a database named `mumu_tests` on: `docker compose up -d`
starts one, or `TEST_ADMIN_URL` points elsewhere. `conftest.py` loads the bots the way main.py does
(`main.register()`), so a test imports `"<bot>_bot.<name>"`, never a bot module by its bare name.

```bash
pip install -r requirements-dev.txt
pytest tests
```

Tests keep their comments inline: they are not shipped, and a test reads best with its reasons beside it.
The four tests of the keys and the walls are the exception: their notes are kept privately, like those of
`shared/security/` and `scripts/security/`.

| File | What it checks |
|---|---|
| [test_channels.py](test_channels.py) | DownloaderBot in channels: a post that is text with a link gets the media put into it, or posted anew when it cannot hold i… |
| [test_credit_and_copyright.py](test_credit_and_copyright.py) | Since 2.2.0: the copyright note under every file DownloaderBot sends, a person told when the operator adds credit, "can't be sent" naming both ceilings. |
| [test_fresh_read.py](test_fresh_read.py) | 2.1.7's fix, ported: the one video site is read afresh before its file is fetched. |
| [test_note_and_edits.py](test_note_and_edits.py) | Since 2.2.1: DownloaderBot's copyright note turned off per chat with /disclaimer; AnonBot's edits and /delete carried to the other side, switched with /editing. |
| [test_gift_claim.py](test_gift_claim.py) | Since 2.2.0: the 220 ⚡ gift is claimed with the offer's button or /gift, within 3 days, and spent until 1 December. |
| [test_hardening.py](test_hardening.py) | The walls around programs that open strangers' files, and secrets kept out of reach (notes kept privately). |
| [test_manager_soft_stop.py](test_manager_soft_stop.py) | ManagerBot's half of the soft stop (since 2.1.6): /shutdown and /restart ask first, the request carries its time, and the watchdog says "stopped as… |
| [test_office_and_first_download.py](test_office_and_first_download.py) | What 2.1.7 brought, ported to 2.2.0: ConvertBot's first download no longer times out (getFile is given time and retried), failures that pass say "t… |
| [test_offline_key.py](test_offline_key.py) | Backups encrypted to the offline key (notes kept privately). |
| [test_poll_lease.py](test_poll_lease.py) | The poll lease: one process at a time polls for a bot, behind any connection pooler. |
| [test_profile_texts.py](test_profile_texts.py) | Every bot's profile texts fit Telegram's limits (setMyDescription 512, setMyShortDescription 120), in every language. 2.1.7 went out with three of… |
| [test_quality_picker.py](test_quality_picker.py) | DownloaderBot's quality picker: the card, quality names, the paid row, counting on a tap, and just the sound. |
| [test_release_220.py](test_release_220.py) | Scenarios for what changed in 2.2.0. |
| [test_release_220_requests.py](test_release_220_requests.py) | Scenarios for the operator's 2.2.0 requests of 2026-10-28: StickerBot's per-sticker replies, emoji by reply, public packs and groups; DownloaderBot's… |
| [test_reports_and_stats.py](test_reports_and_stats.py) | Since 2.1.6: an error says an anonymous technical report was saved, and "Send more details" is offered only where who it was matters, or once a fau… |
| [test_reserve.py](test_reserve.py) | Reserve copies, and a restore that forgets again whoever erased since (notes kept privately). |
| [test_rotate_data_key.py](test_rotate_data_key.py) | Replacing the data key (notes kept privately). |
| [test_sent_files.py](test_sent_files.py) | DownloaderBot's file-id store (sent_files.py) kept small and cheap as it grows: 16-byte keys, the ids as bytes, one row per… |
| [test_soft_stop.py](test_soft_stop.py) | The soft stop behind ManagerBot's /shutdown and /restart (since 2.1.6): a service, or one bot of it, goes down only when nobody is using it -- and… |
| [test_sticker_pack_buttons.py](test_sticker_pack_buttons.py) | StickerBot since 2.1.6 (MumuCombined), in 2.2.0: the pack buttons under the message box, pictures sent with no pack open held for a pack choice, a… |
| [test_sticker_run.py](test_sticker_run.py) | What 2.1.2-2.1.4 brought that 2.2.0 keeps: StickerBot's co… |
| [test_sticker_telegram_limits.py](test_sticker_telegram_limits.py) | StickerBot and Telegram's limits and hiccups (2.1.4, ported the same day). What production showed between 2026-10-29 and 11-04: an album met the fl… |
| [test_update_queue.py](test_update_queue.py) | A crowd never holds more than UPDATES_IN_MEMORY_MAX updates in one bot's memory: polling waits while the queue and the… |
| [test_video_relays.py](test_video_relays.py) | The relays behind the one platform the bot does not advertise (2.1.5). |
