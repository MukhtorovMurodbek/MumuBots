from __future__ import annotations

import re
import secrets
from collections import namedtuple

Problem = namedtuple("Problem", "bot title meaning causes check")

MARK = "🆔 "
CODE_RE = re.compile(r"^[A-Z]{2}(?:-[A-Z0-9]+)+$")
INCIDENT_RE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
_INCIDENT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_AT_END = re.compile(r"\n\n?🆔 ([A-Z]{2}(?:-[A-Z0-9]+)+)(?:</[a-z]+>)*\s*$")

EVERY_PUBLIC_BOT = "every public bot"

PROBLEMS = {
    "FM-CRASH": Problem(
        EVERY_PUBLIC_BOT, "The bot crashed while handling a message",
        "A handler raised an exception nothing caught. The request was not done; the person was told so "
        "and offered a report.",
        "A bug in that handler; the shared database unreachable or timing out; Telegram rejecting a call in "
        "a way the code does not expect (a changed API, editing a message that was deleted).",
        "Search the bot's log for the incident id: the traceback is logged beside it. /events and the crash "
        "alert name the exception type; /status shows whether errors are piling up."),
    "FM-FLOOD": Problem(
        EVERY_PUBLIC_BOT, "Too many updates from one person",
        "One person sent more than FLOOD_UPDATES_PER_MINUTE updates (40 by default) inside a minute; the rest "
        "were ignored until the window cleared.",
        "Tapping buttons very fast, a client resending, forwarding a big selection, or a script.",
        "Usually nothing. If ordinary use trips it, raise FLOOD_UPDATES_PER_MINUTE on that bot."),
    "FM-DONATE-CURRENCY": Problem(
        EVERY_PUBLIC_BOT, "Unknown currency in /donate or /recharge",
        "The command named a currency the bot does not offer.",
        "A typo, or a currency that was never configured.",
        "Nothing, unless people keep asking for a currency that should be added to FIAT_CURRENCIES."),
    "FM-DONATE-UNAVAILABLE": Problem(
        EVERY_PUBLIC_BOT, "Currency offered but not set up",
        "The currency is known, but its payment provider token is missing, so no invoice can be made.",
        "The provider token environment variable is not set on this bot.",
        "Set that currency's provider token, or stop offering it."),
    "FM-DONATE-AMOUNT": Problem(
        EVERY_PUBLIC_BOT, "Invalid payment amount",
        "The amount was not a positive whole number, or a button carried an amount the bot never offered.",
        "A typo in /donate or /recharge; an old or tampered button.",
        "Nothing, unless it happens with the bot's own buttons -- then the offered amounts and the handler "
        "disagree (DONATE_STAR_OPTIONS)."),
    "FM-DONATE-TOO-MANY": Problem(
        EVERY_PUBLIC_BOT, "Payment above the per-payment limit",
        "The Stars amount was above MAX_DONATION_STARS.",
        "A large custom amount.",
        "Raise MAX_DONATION_STARS if larger single payments are wanted."),
    "FM-DONATE-RANGE": Problem(
        EVERY_PUBLIC_BOT, "Card payment outside the allowed range",
        "A fiat amount was below that currency's minimum or above its maximum.",
        "An amount outside the provider's limits.",
        "Correct the currency's limits in FIAT_CURRENCIES if they are wrong."),
    "FM-INVOICE": Problem(
        EVERY_PUBLIC_BOT, "Telegram refused to create an invoice",
        "send_invoice failed. The payment row was marked failed and nothing was charged.",
        "An amount Telegram rejects, a provider token that stopped working, or Telegram having a problem.",
        "The error Telegram returned is in the message and in the log at that time; check the provider token "
        "and the amount."),
    "FM-ERASE": Problem(
        EVERY_PUBLIC_BOT, "/deletemydata failed",
        "Erasing the person's data raised an error, so nothing was erased and they were asked to try again.",
        "The database unreachable, or a table the erase does not expect.",
        "The traceback is in the log; tests/erase_scenarios.py reproduces erasing against a real database."),
    "FM-UPDATING": Problem(
        EVERY_PUBLIC_BOT, "The bot is paused for an update",
        "New work was refused because an update is about to be deployed (/pause in ManagerBot).",
        "An announced update window, or a pause that was never lifted.",
        "If no update is under way, lift it with /finishupdates in ManagerBot."),
    "FM-UNKNOWN-COMMAND": Problem(
        EVERY_PUBLIC_BOT, "Unknown command",
        "A slash command this bot does not have.",
        "A typo, a command from an older version, or one that belongs to another bot.",
        "Nothing, unless an old command should be kept working as an alias."),
    "FM-FEEDBACK-LIMIT": Problem(
        EVERY_PUBLIC_BOT, "Too much feedback from one person today",
        "The person had already sent FEEDBACK_PER_DAY messages with /feedback in the last 24 hours, so "
        "this one was not taken.",
        "Several thoughts sent one at a time, or somebody using /feedback to message the operator over and "
        "over.",
        "Nothing; raise FEEDBACK_PER_DAY if ordinary people reach it."),
    "FM-FEEDBACK-FAILED": Problem(
        EVERY_PUBLIC_BOT, "Feedback could not be saved",
        "Writing what the person sent with /feedback to the database failed, so it did not reach the "
        "operator; the person was asked to send it again.",
        "The shared database unreachable or timing out.",
        "The traceback beside the incident in the log; /status for the database."),

    "CV-NO-EXTENSION": Problem(
        "ConvertBot", "File name without a usable extension",
        "The format could not be told from the file's name, so nothing was downloaded or charged.",
        "A file sent without an extension, or with one the bot does not recognise.",
        "Nothing, unless a common extension is missing from formats.ALIASES."),
    "CV-TG-LIMIT": Problem(
        "ConvertBot", "File larger than a bot may download",
        "Telegram declared the file over 20 MB, and nothing on this bot lifts the cloud Bot API's 20 MB download "
        "limit.",
        "A large file, on a bot without CBOT_API_ID and CBOT_API_HASH (or a local Bot API server).",
        "Set CBOT_API_ID and CBOT_API_HASH (my.telegram.org) so files over 20 MB come over MTProto -- "
        "big_files.py. The startup log line 'Largest upload' says which is in force."),
    "CV-GROUP-LIMIT": Problem(
        "ConvertBot", "Big file sent in a group",
        "A file over 20 MB was sent in a group. Files past the Bot API's limit come over MTProto, which this bot "
        "uses in private chats only, so the person was asked to send it privately.",
        "Using the bot in a group.",
        "Nothing; it is by design (big_files.usable)."),
    "CV-BIG-UNAVAILABLE": Problem(
        "ConvertBot", "The route for files over 20 MB is down",
        "A file over 20 MB was sent, and the MTProto connection that fetches those could not be made or used. "
        "Nothing was downloaded or charged. Files under 20 MB were unaffected.",
        "Telegram refusing the login (a wrong CBOT_API_ID or CBOT_API_HASH, a revoked token, a flood wait after "
        "too many logins), or the host's network. After a failure every big file is refused for five minutes "
        "rather than each waiting out a timeout.",
        "The bot's log: 'MTProto connection for big files failed' with Telegram's error beside it."),
    "CV-DOWNLOAD": Problem(
        "ConvertBot", "Downloading the file from Telegram failed",
        "get_file or the download raised, so nothing was converted and nothing charged.",
        "A network error, Telegram timing out, or the file no longer being available.",
        "The error text is in the message and the log; repeated failures point at the host's network."),
    "CV-UNSUPPORTED": Problem(
        "ConvertBot", "Format not converted",
        "The source format, or the target that was chosen, is not one this server can convert.",
        "An unsupported file; an old menu offering a format no longer available; a converter missing on this host.",
        "/formats on the bot; the startup log line 'Converters available' lists what was found."),
    "CV-TOO-BIG": Problem(
        "ConvertBot", "File over ConvertBot's size limit",
        "The file, or an album's files together, are larger than MAX_FILE_MB, so it was refused before "
        "anything was charged.",
        "A large file, or a large album.",
        "CONVERT_MAX_FILE_MB (100 by default when files over 20 MB can be fetched at all). Raise it only once "
        "the host has shown it converts that much inside CONVERT_TIME_LIMIT_SECONDS."),
    "CV-NO-TARGETS": Problem(
        "ConvertBot", "Nothing this file can become",
        "The format is readable, but no target format is available for it on this server.",
        "A converter library or an ffmpeg encoder missing on the host.",
        "The startup log line 'Converters available'; requirements.txt and nixpacks.toml."),
    "CV-NOTHING-SENDABLE": Problem(
        "ConvertBot", "Every result would be too big to send",
        "For a picture this large, every offered format would come out over what the bot may send back (50 MB, "
        "or more where results can go over MTProto), so nothing was offered or charged.",
        "A very high-resolution image, in a group or on a bot without CBOT_API_ID.",
        "Nothing; CBOT_API_ID and CBOT_API_HASH raise the send limit in private chats."),
    "CV-MEGAPIXELS": Problem(
        "ConvertBot", "Picture over the megapixel limit",
        "The image is larger than CONVERT_MAX_MEGAPIXELS, above which one decode needs more memory than the "
        "container has. Nothing was charged.",
        "A very high-resolution image.",
        "Raise CONVERT_MAX_MEGAPIXELS only together with the container's memory."),
    "CV-FORMAT-TOO-BIG": Problem(
        "ConvertBot", "That format would be too big to send",
        "A format was tapped whose result is estimated over the send limit, usually from an old menu.",
        "An old menu, or a size estimate that changed.",
        "jobs.unsendable_targets and LOSSLESS_BYTES_PER_PIXEL in jobs.py."),
    "CV-STORAGE-USER": Problem(
        "ConvertBot", "Person over their temporary storage share",
        "Files waiting, or kept for more formats, already use CONVERT_USER_STORAGE_MB for this person.",
        "Several large files kept at once.",
        "Nothing, unless the share is too small for real albums."),
    "CV-STORAGE-FULL": Problem(
        "ConvertBot", "Server short of temporary space",
        "Taking the file would leave less than CONVERT_MIN_FREE_MB free, or push everyone's files over "
        "CONVERT_STORAGE_MB.",
        "Many large conversions at once, or files left behind that the sweep has not removed yet.",
        "Disk use on the container; the sweep lines in the log ('Swept ... abandoned upload(s)')."),
    "CV-QUEUE-FULL": Problem(
        "ConvertBot", "Too many conversions waiting",
        "The person already has CONVERT_MAX_QUEUED_PER_USER conversions queued or running.",
        "Many formats chosen quickly.",
        "Nothing, unless the limit is too low for real use."),
    "CV-EXPIRED": Problem(
        "ConvertBot", "The file is no longer waiting",
        "A format was chosen for a file whose pending state is gone.",
        "More than CONVERT_PENDING_TTL_SECONDS since the file was sent, a restart, or the file already converted.",
        "Nothing, unless it happens within minutes of sending -- then pending state is being lost."),
    "CV-JOB-GONE": Problem(
        "ConvertBot", "That conversion has already finished",
        "Stop was tapped for a conversion that had already ended.",
        "Tapping Stop just as it finished.",
        "Nothing."),
    "CV-NO-CREDIT": Problem(
        "ConvertBot", "Balance did not cover the conversion when it started",
        "When the conversion reached the front of the queue the balance was below its price, so it did not run "
        "and nothing was charged.",
        "Credit spent on another conversion meanwhile, or bonus credit that expired while it waited.",
        "/balance <id> in ManagerBot shows the ledger if the person disputes it."),
    "CV-UNKNOWN-ORDER": Problem(
        "ConvertBot", "Payment for an order the bot does not know",
        "A pre-checkout query carried a payload that is neither a conversion nor a top-up; it was declined "
        "before anything was charged.",
        "A very old invoice, or an invoice made by another deployment of the bot.",
        "The payload is in the log; nothing was charged."),
    "CV-MIXED-BATCH": Problem(
        "ConvertBot", "Album of different formats",
        "Files sent together had different formats, which one conversion cannot take.",
        "A photo and a video in one album.",
        "Nothing."),
    "CV-UNRECOGNIZED": Problem(
        "ConvertBot", "Not something ConvertBot can use",
        "A message that is neither a file nor a format name.",
        "Text, a sticker or other media sent to ConvertBot.",
        "Nothing."),
    "CV-REFUSED": Problem(
        "ConvertBot", "The converter could not convert this file",
        "The worker raised ConversionError: the file could not be read or converted as asked. Its ⚡ went back "
        "to the balance.",
        "A damaged or unusual file, or something the converter does not support (an encrypted PDF, an unusual "
        "codec).",
        "The converter's own message is in the log beside the incident id."),
    "CV-TIMEOUT": Problem(
        "ConvertBot", "Conversion ran past its time limit",
        "The worker was still running at CONVERT_TIME_LIMIT_SECONDS (60 s for a free conversion) and was killed. "
        "Its ⚡ went back to the balance and an alert went to the operator.",
        "A very large or complex file (a long video, many pages), a slow encoder such as AVIF, or a host short "
        "of CPU.",
        "The operator's alert gives size, formats and run time; compare with /usage for CPU pressure at that time."),
    "CV-TOO-SLOW": Problem(
        "ConvertBot", "Stopped early: would not finish in time",
        "A conversion reported its progress for PROJECT_AFTER_S and was on course to take longer than its "
        "time limit (by more than PROJECT_MARGIN), so it was stopped then rather than at the limit. Its ⚡ went "
        "back to the balance, and the person was told roughly how long it would have taken.",
        "A long or high-resolution video (4K, 60 fps, HDR) on a small host, or a slow encoder such as AV1 or AVIF.",
        "Nothing, if it is rare. If it is common, the host is short of CPU for what people send: /usage, "
        "FFMPEG_THREADS, or CONVERT_TIME_LIMIT_SECONDS with the pricing test run again."),
    "CV-CRASH": Problem(
        "ConvertBot", "The conversion worker crashed",
        "The worker process died without a result -- most often killed for memory. Its ⚡ went back to the "
        "balance and an alert went to the operator.",
        "An out-of-memory kill on a very large image, or a crash inside a native decoder.",
        "The log line beside the incident id has the exit code (-9 or 137 means killed); /usage shows memory."),
    "CV-NO-MEMORY": Problem(
        "ConvertBot", "Ran out of memory",
        "ffmpeg was killed for memory while converting (SIGKILL: exit -9 or 137), or the worker hit a "
        "MemoryError. The file is not at fault. Its ⚡ went back to the balance, and the person was told "
        "that a lower resolution will convert.",
        "A 4K or HDR video on a host with 1 GB a container: tone-mapping one 4K frame to SDR takes about "
        "100 MB, and the encoder holds several. Measured on Railway, 2026-09-29.",
        "Nothing, if it is rare. If it is common, the host needs more memory a service -- or such video "
        "converted at 1080p (CHANGES_v2.0.0.md, Railway, measured)."),
    "CV-OUTPUT-TOO-BIG": Problem(
        "ConvertBot", "The result was over its send limit",
        "The conversion finished, but the file is larger than this job may send back: 50 MB, or where results "
        "go over MTProto, twice what the job was priced on (jobs.send_limit_for). Its ⚡ went back to the "
        "balance and an alert went to the operator.",
        "A lossless or high-resolution target (PNG, TIFF, GIF) for a large input.",
        "If one format keeps doing this, add it to the unsendable estimates in jobs.py."),
    "CV-SEND-FAILED": Problem(
        "ConvertBot", "Sending the result failed",
        "The file was converted but uploading it to the person failed. Its ⚡ went back to the balance and an "
        "alert went to the operator.",
        "Telegram rejecting the upload, a network error or timeout, or the person having blocked the bot.",
        "The exception type and text are in the operator's alert and the log."),
    "CV-RESTARTED": Problem(
        "ConvertBot", "The bot restarted during the conversion",
        "The process stopped while the conversion was running. Its ⚡ went back to the balance.",
        "A redeploy, a manual restart, or the container being killed.",
        "The deploy history at that time, and /events for the restart."),

    "DL-QUOTA-HOUR": Problem(
        "DownloaderBot", "Hourly download limit reached",
        "The person has used their downloads for the last hour.",
        "Heavy use.",
        "The hourly allowance settings; people who have donated get a higher one."),
    "DL-QUOTA-DAY": Problem(
        "DownloaderBot", "Daily download limit reached",
        "The person has used their downloads for the day.",
        "Heavy use.",
        "The daily allowance settings; people who have donated get a higher one."),
    "DL-QUEUE-FULL": Problem(
        "DownloaderBot", "Too many links waiting",
        "The person already has DBOT_MAX_PENDING_PER_USER links in progress.",
        "Many links sent at once.",
        "Nothing, unless the limit is too low."),
    "DL-NO-SPACE": Problem(
        "DownloaderBot", "Server short of temporary space",
        "Free disk was below DBOT_MIN_FREE_MB, so the download did not start.",
        "Several large downloads at once, or files left behind.",
        "Disk use on the container."),
    "DL-DELIVERY": Problem(
        "DownloaderBot", "Sending the downloaded file failed",
        "The media was fetched, but uploading it to Telegram failed.",
        "A file over Telegram's limit after all, a network error, or Telegram rejecting the media.",
        "The provider and exception are in the log ('Delivering ... failed')."),
    "DL-BLOCKED": Problem(
        "DownloaderBot", "The site refused every route",
        "Every provider was refused by the platform: blocked, rate limited, or asked to log in.",
        "The platform limiting the server's IP, expired cookies, or a change on the platform.",
        "/providers and /probe in ManagerBot show which routes are failing."),
    "DL-NOT-FOUND": Problem(
        "DownloaderBot", "Post not found",
        "The platform says the post does not exist or is not public.",
        "A deleted post, a private account, or a mistyped link.",
        "Nothing, unless public posts come back as missing -- then a resolver is misreading the page."),
    "DL-TOO-BIG": Problem(
        "DownloaderBot", "Media over what Telegram allows",
        "The media is larger than a bot may send.",
        "A long, high-quality video.",
        "Nothing on the cloud API."),
    "DL-LARGE-OFF": Problem(
        "DownloaderBot", "Over the free ceiling, large files off",
        "The media is over DBOT_MAX_DOWNLOAD_MB, and this person has not switched on large files, which cost "
        "credit. They were told the price and offered the switch; nothing past the ceiling was fetched.",
        "A long or high-quality video.",
        "Nothing; it is by design (large_files.py). Default off, as decided."),
    "DL-LARGE-PRIVATE": Problem(
        "DownloaderBot", "A large file asked for in a group",
        "A file over the free ceiling was asked for in a group; large files go over MTProto, which this bot "
        "uses in private chats only, so the person was sent to a private chat.",
        "Using the bot in a group.",
        "Nothing; it is by design (big_files.usable)."),
    "DL-LARGE-NO-CREDIT": Problem(
        "DownloaderBot", "Large files on, no credit for one",
        "A file over the free ceiling, large files switched on, and a balance under the least one costs "
        "(large_files.first_price()). Nothing was fetched or charged.",
        "An empty balance.",
        "Nothing; the person was told to top up."),
    "DL-LARGE-TOO-BIG": Problem(
        "DownloaderBot", "Over even the large-file ceiling",
        "The media is over DBOT_LARGE_MAX_MB -- 2000 MB, Telegram's own limit for a bot, unless it was set "
        "lower. Nothing was charged.",
        "A very long, high-quality video.",
        "Nothing, unless DBOT_LARGE_MAX_MB was set below 2000."),
    "DL-LARGE-SHORT": Problem(
        "DownloaderBot", "A large file cost more than the balance",
        "The large file arrived, its price was worked out from its size, and the balance did not cover it. "
        "Nothing was charged and the file was deleted.",
        "A balance that covered the least a large file costs, but not this one.",
        "Nothing; the person was told the price and to top up."),
    "DL-LARGE-SEND": Problem(
        "DownloaderBot", "A large file was charged for and could not be sent",
        "A large file was fetched and charged for, and sending it over MTProto failed. The credit went back.",
        "The MTProto route failing mid-send (a flood wait, the connection dropping), or a file Telegram refused.",
        "The traceback in the log beside the incident id; big_files' own warning line if the route was down."),
    "DL-ALL-ROUTES": Problem(
        "DownloaderBot", "Every download route failed",
        "Every provider in the chain failed, for a reason other than blocked, missing or too big.",
        "Provider outages, a platform change that broke the resolvers, or network trouble.",
        "/providers for failure counts; the per-provider log lines; tests/test_resolvers.py."),
    "DL-REDDIT": Problem(
        "DownloaderBot", "Reddit post could not be fetched",
        "Reading the Reddit post's data failed.",
        "Reddit rate limiting, or a changed response.",
        "The error text in the message and the log."),
    "DL-TWITTER-CARD": Problem(
        "DownloaderBot", "X/Twitter post content unavailable",
        "The post's content could not be fetched, so only the link was sent back.",
        "X blocking the routes, or a changed page.",
        "/providers for the X routes."),
    "DL-UNRECOGNIZED": Problem(
        "DownloaderBot", "Not a supported link",
        "The message was not a link from a supported platform.",
        "Text, or a link from an unsupported platform.",
        "Nothing, unless a supported platform's link format changed (platforms.detect_platform)."),
    "DL-BUTTON-EXPIRED": Problem(
        "DownloaderBot", "Re-send button too old",
        "The compressed/uncompressed button refers to a download that is no longer remembered.",
        "An old message, or a restart.",
        "Nothing."),
    "DL-MERGE-TOO-BIG": Problem(
        "DownloaderBot", "An album too big to put in one file",
        "The 📄 PDF or 🗜 ZIP button was tapped on an album with more than DBOT_MERGE_MAX_ITEMS files (20), or "
        "whose files came to more than DBOT_MERGE_MAX_MB (48) when fetched, or whose PDF or ZIP came out over "
        "the 50 MB the Bot API takes; or one picture was over 20 megapixels (too many for a PDF page). "
        "Fetching stopped at the file that passed the limit and nothing was built.",
        "A large carousel or a post of long videos; a very high-resolution photograph.",
        "Nothing; the person still has the files from the delivery. Raise the two DBOT_MERGE_* limits if "
        "ordinary albums are being refused (merge.py)."),
    "DL-MERGE-NO-PHOTOS": Problem(
        "DownloaderBot", "A PDF asked for, fewer than two photos found",
        "The 📄 PDF button was tapped, and when the post was fetched again fewer than two photos came back -- "
        "no PDF was built.",
        "The post was edited or deleted since it was delivered, or a different route answered this time "
        "and returned only a video or a cover picture.",
        "Nothing, unless it happens to albums that are still up: then compare what the routes return for "
        "that link (/probe in ManagerBot)."),
    "DL-MERGE-FAILED": Problem(
        "DownloaderBot", "Building or sending an album's PDF or ZIP failed",
        "The files were fetched but the single PDF or ZIP could not be built or uploaded; everything was "
        "deleted and the person was offered a report.",
        "For a PDF, a file that is not an image although the post called it a photo, an animated picture, or "
        "one in a format other than JPEG, PNG, WEBP and GIF (all refused unread); Pillow running out of "
        "memory on a large picture; a full disk; or Telegram refusing the upload.",
        "The traceback in the log beside the incident id ('Merging ... failed'); merge.py."),

    "ST-WHOMADE-UNKNOWN": Problem(
        "StickerBot", "Pack not made through this bot",
        "/whomade found no record of the pack.",
        "A pack made elsewhere, or a mistyped name or link.",
        "Nothing."),
    "ST-COEDIT-LINK": Problem(
        "StickerBot", "Co-editing link not valid",
        "The link's token matches no pack.",
        "The pack's creator reset the link, or it was mistyped.",
        "Nothing."),
    "ST-COEDIT-GONE": Problem(
        "StickerBot", "Pack no longer exists",
        "The pack a co-editing link points at has no creator on record.",
        "The pack was deleted.",
        "Nothing."),
    "ST-OTHERS-PACK": Problem(
        "StickerBot", "Not the person's pack",
        "A pack button was tapped by someone who does not own the pack.",
        "A forwarded menu, or an old button.",
        "Nothing."),
    "ST-CREATOR-ONLY": Problem(
        "StickerBot", "Only the pack's creator can do that",
        "Renaming, co-editing settings and deleting belong to the pack's creator.",
        "A co-editor tapping a creator's button.",
        "Nothing."),
    "ST-RENAME-STATE": Problem(
        "StickerBot", "Rename lost track of the pack",
        "A new name arrived, but the pack it was meant for is no longer known.",
        "A restart, or a long gap between tapping Rename and sending the name.",
        "Nothing, unless it is frequent -- then state is being lost."),
    "ST-RENAME-FAILED": Problem(
        "StickerBot", "Telegram refused the rename",
        "set_sticker_set_title raised.",
        "A title Telegram rejects, the pack deleted meanwhile, or a network error.",
        "The error text is in the message and the log."),
    "ST-DELETE-FAILED": Problem(
        "StickerBot", "Telegram refused to delete the pack",
        "delete_sticker_set raised.",
        "The pack already deleted, or a network error.",
        "The error text is in the message and the log."),
    "ST-TITLE-EMPTY": Problem(
        "StickerBot", "Empty pack name",
        "The name sent was empty once trimmed.",
        "Whitespace, or an empty message.",
        "Nothing."),
    "ST-REMOVE-FAILED": Problem(
        "StickerBot", "Removing a sticker failed",
        "delete_sticker_from_set raised.",
        "The sticker already gone, or a network error.",
        "The error text is in the message and the log."),
    "ST-IMAGE": Problem(
        "StickerBot", "Picture could not be made into a sticker",
        "Processing the image raised.",
        "A damaged or unusual image file.",
        "The error text in the message and the log."),
    "ST-VIDEO-CONVERT": Problem(
        "StickerBot", "Video could not be made into a sticker",
        "The video could not be encoded into a sticker Telegram accepts.",
        "A clip too long or too detailed to fit 256 KB, an empty file, or ffmpeg missing on the host.",
        "The reason is the first line of the message; the startup log says whether ffmpeg was found."),
    "ST-VIDEO-FAILED": Problem(
        "StickerBot", "Video conversion crashed",
        "An unexpected error while converting a video.",
        "A bug, or an unusual file.",
        "The traceback in the log ('Video conversion failed')."),
    "ST-ANIMATED": Problem(
        "StickerBot", "Animated (Lottie) sticker not supported",
        "A .tgs animated sticker was sent, which the bot cannot copy.",
        "An animated sticker.",
        "Nothing."),
    "ST-IMPORT-SOURCE": Problem(
        "StickerBot", "Not a sticker pack name or link",
        "/import was given something that is neither a pack name nor a t.me/addstickers link.",
        "A typo.",
        "Nothing."),
    "ST-IMPORT": Problem(
        "StickerBot", "Import could not read the pack",
        "Importing failed: the Telegram pack was not found, or the WhatsApp archive was not a valid .zip or had "
        "no usable images.",
        "A private or mistyped pack name, or a damaged or unexpected archive.",
        "The message says which; nothing to do unless valid packs fail (import_utils.py)."),
    "ST-TG-TIMEOUT": Problem(
        "StickerBot", "Telegram did not confirm in time",
        "Adding a sticker timed out; it may have been added anyway.",
        "A slow upload, most often a video sticker.",
        "Nothing, unless constant -- then the host's network."),
    "ST-TG-BAD-NAME": Problem(
        "StickerBot", "Telegram rejected the pack's internal name",
        "The generated set name was invalid.",
        "A title starting with a digit or a symbol.",
        "Nothing; the message explains the workaround."),
    "ST-TG-NAME-TAKEN": Problem(
        "StickerBot", "Pack name already taken",
        "The generated set name collided with an existing pack.",
        "Chance.",
        "Nothing."),
    "ST-TG-PACK-FULL": Problem(
        "StickerBot", "Pack is full",
        "The pack reached Telegram's sticker limit (120).",
        "A full pack.",
        "Nothing."),
    "ST-TG-BAD-FORMAT": Problem(
        "StickerBot", "Telegram rejected the sticker file",
        "The prepared file was not accepted for this pack.",
        "A format mismatch, such as a video sticker into an older static pack.",
        "The Telegram error text in the log."),
    "ST-TG-REJECTED": Problem(
        "StickerBot", "Telegram rejected the sticker",
        "Adding a sticker failed with an error the bot has no specific explanation for.",
        "Any other Telegram API error.",
        "The Telegram error text is in the message and the log."),
    "ST-UNRECOGNIZED": Problem(
        "StickerBot", "Message not understood",
        "Something StickerBot has no use for outside a pack session.",
        "Text or media sent without starting a pack.",
        "Nothing."),
    "ST-NOTHING-ADDED": Problem(
        "StickerBot", "/done with nothing added",
        "/done was sent before any sticker was added.",
        "Finishing too early.",
        "Nothing."),
    "ST-NO-STICKER": Problem(
        "StickerBot", "Emoji with no sticker to tag",
        "An emoji arrived before any sticker was added in this session.",
        "Sending the emoji first.",
        "Nothing."),
    "ST-RETAG": Problem(
        "StickerBot", "Changing the emoji failed",
        "set_sticker_emoji_list raised.",
        "The sticker removed meanwhile, or a network error.",
        "The error text is in the message and the log."),

    "AN-LINK-INVALID": Problem(
        "AnonBot", "Inbox link not valid",
        "The link's token matches no inbox.",
        "The inbox holder reset the link, or it was mistyped.",
        "Nothing."),
    "AN-LINK-BLOCKED": Problem(
        "AnonBot", "Blocked by the inbox holder",
        "The person who opened the link is on the inbox holder's block list.",
        "The inbox holder blocked them.",
        "Nothing."),
    "AN-LINK-PAUSED": Problem(
        "AnonBot", "Inbox paused",
        "The inbox holder has paused new conversations.",
        "/pause by the inbox holder.",
        "Nothing."),
    "AN-INBOX-GONE": Problem(
        "AnonBot", "Inbox no longer exists",
        "The inbox is gone, usually through /deletemydata.",
        "The inbox holder erased their data.",
        "Nothing."),
    "AN-SENDER-BLOCKED": Problem(
        "AnonBot", "Message not delivered: blocked",
        "The guest is blocked by the inbox holder, so the message was not relayed.",
        "The inbox holder blocked this guest.",
        "Nothing."),
    "AN-NOT-STARTED": Problem(
        "AnonBot", "Recipient has not started the bot",
        "Telegram refused the delivery (Forbidden).",
        "The recipient never started the bot, or has blocked it.",
        "Nothing."),
    "AN-DELIVERY": Problem(
        "AnonBot", "Relaying the message failed",
        "Telegram rejected the copy for another reason (BadRequest).",
        "An unsupported message type, an oversized caption, or a changed API.",
        "The error text is in the message and the log ('Failed relaying follower message')."),
    "AN-REPLY-NO-MATCH": Problem(
        "AnonBot", "Reply resolved to someone else's conversation",
        "A reply matched a conversation the sender does not own; it was not sent.",
        "Should not happen -- relay rows are written per chat.",
        "The anon_relay rows for that chat; tests/anon_scenarios.py group P checks the pairing."),
    "AN-NEEDS-REPLY": Problem(
        "AnonBot", "Message did not say which conversation",
        "A message that was not a reply arrived while conversations are open.",
        "Typing without swiping to reply.",
        "Nothing."),
    "AN-OPENING-NEEDS-REPLY": Problem(
        "AnonBot", "First message has to answer the opening line",
        "The conversation a link tap opened has not started, and something else arrived in between.",
        "A message from another conversation arriving after the tap.",
        "Nothing."),
    "AN-AMBIGUOUS": Problem(
        "AnonBot", "Several conversations open, none named",
        "A message without a reply while several conversations are open.",
        "Typing without replying.",
        "Nothing."),
    "AN-REPLY-BLOCKED": Problem(
        "AnonBot", "Reply not delivered: guest unavailable",
        "Telegram refused the inbox holder's reply (Forbidden).",
        "The guest blocked the bot or deleted their account.",
        "Nothing."),
    "AN-REPLY-FAILED": Problem(
        "AnonBot", "Relaying the reply failed",
        "Telegram rejected the inbox holder's reply for another reason.",
        "An unsupported message type or an oversized caption.",
        "The error text is in the message and the log ('Failed relaying the inbox holder's reply')."),
    "AN-REPLY-STALE": Problem(
        "AnonBot", "Reply to a message with no record",
        "The replied-to message is in no tracked conversation.",
        "A reply to the person's own message, to a bot notice, or to a message older than RELAY_RETENTION_DAYS.",
        "Nothing."),
    "AN-TOO-FAST": Problem(
        "AnonBot", "Messages sent too fast",
        "More than ABOT_MSGS_PER_MINUTE messages inside a minute.",
        "Rapid sending, or a script.",
        "Raise ABOT_MSGS_PER_MINUTE if ordinary use trips it."),
    "AN-EDIT-NOT-RELAYED": Problem(
        "AnonBot", "Edits are not relayed",
        "An already relayed message was edited; the other side still has the original.",
        "Editing after sending.",
        "Nothing."),
    "AN-CONV-GONE": Problem(
        "AnonBot", "Conversation no longer on record",
        "A conversation button refers to a conversation that was not found or is not the person's.",
        "An old button, or /deletemydata.",
        "Nothing."),
    "AN-OTHERS-CONV": Problem(
        "AnonBot", "Not the person's conversation",
        "A button was tapped for a conversation the tapper is not part of.",
        "A forwarded message with buttons.",
        "Nothing."),
    "AN-NO-ANCHOR": Problem(
        "AnonBot", "Conversation start no longer available",
        "Jumping to the start is impossible because the records of its messages were pruned.",
        "More than RELAY_RETENTION_DAYS of silence in the conversation.",
        "Nothing."),
    "AN-WHICH-UNKNOWN": Problem(
        "AnonBot", "Message belongs to no open conversation",
        "/which or /archive was used on a message with no conversation.",
        "The person's own old message, or an archived conversation.",
        "Nothing."),
    "AN-ARCHIVE-HOLDER-ONLY": Problem(
        "AnonBot", "Only the inbox holder archives",
        "A guest tried to archive a conversation.",
        "A guest using the inbox holder's command.",
        "Nothing."),
    "AN-UNADDRESSED": Problem(
        "AnonBot", "Message with nowhere to go",
        "A message from somebody with no inbox and no open conversation.",
        "Writing to the bot without having tapped a link.",
        "Nothing."),
    "AN-REACTION-NOT-PASSED": Problem(
        "AnonBot", "Reaction not passed on",
        "A reaction on a message in a conversation could not be shown to the other person: a Telegram "
        "Premium custom-emoji reaction, an emoji Telegram does not let bots use, or a second or third "
        "reaction on one message (a bot shows one). The person saw a note for a few seconds.",
        "A Premium user reacting with a custom emoji, or with more than one reaction.",
        "Nothing; it is Telegram's limit on what a bot may react with (setMessageReaction)."),
}

KEYS = {
    "shared": {
        "crash_notice": "FM-CRASH",
        "flood_wait": "FM-FLOOD",
        "donate_unknown_currency": "FM-DONATE-CURRENCY",
        "donate_currency_not_configured": "FM-DONATE-UNAVAILABLE",
        "donate_invalid_amount": "FM-DONATE-AMOUNT",
        "donate_invalid_amount_retry": "FM-DONATE-AMOUNT",
        "donate_too_many_stars": "FM-DONATE-TOO-MANY",
        "donate_out_of_range": "FM-DONATE-RANGE",
        "donate_invoice_error": "FM-INVOICE",
        "delete_data_failed": "FM-ERASE",
        "update_soon_try_later": "FM-UPDATING",
        "update_soon_try_later_soon": "FM-UPDATING",
        "unknown_command": "FM-UNKNOWN-COMMAND",
        "feedback_limit": "FM-FEEDBACK-LIMIT",
        "feedback_failed": "FM-FEEDBACK-FAILED",
    },
    "convert_bot": {
        "unknown_extension": "CV-NO-EXTENSION",
        "file_too_large_download": "CV-TG-LIMIT",
        "file_too_large_in_group": "CV-GROUP-LIMIT",
        "big_download_unavailable": "CV-BIG-UNAVAILABLE",
        "download_failed": "CV-DOWNLOAD",
        "unsupported_format": "CV-UNSUPPORTED",
        "file_too_large_convert": "CV-TOO-BIG",
        "no_target_formats": "CV-NO-TARGETS",
        "nothing_sendable": "CV-NOTHING-SENDABLE",
        "too_many_megapixels": "CV-MEGAPIXELS",
        "too_big_to_send_alert": "CV-FORMAT-TOO-BIG",
        "storage_full_user": "CV-STORAGE-USER",
        "storage_full_global": "CV-STORAGE-FULL",
        "queue_full": "CV-QUEUE-FULL",
        "conversion_expired": "CV-EXPIRED",
        "job_not_running": "CV-JOB-GONE",
        "job_no_credit": "CV-NO-CREDIT",
        "unknown_order": "CV-UNKNOWN-ORDER",
        "batch_mixed_formats": "CV-MIXED-BATCH",
        "unrecognized_message": "CV-UNRECOGNIZED",
    },
    "downloader_bot": {
        "download_queue_full": "DL-QUEUE-FULL",
        "download_short_on_space": "DL-NO-SPACE",
        "download_failed": "DL-DELIVERY",
        "download_blocked": "DL-BLOCKED",
        "download_missing": "DL-NOT-FOUND",
        "download_too_big": "DL-TOO-BIG",
        "download_all_routes_failed": "DL-ALL-ROUTES",
        "reddit_fetch_failed": "DL-REDDIT",
        "twitter_fetch_failed_link": "DL-TWITTER-CARD",
        "unrecognized_message": "DL-UNRECOGNIZED",
        "redeliver_expired": "DL-BUTTON-EXPIRED",
        "merge_too_big": "DL-MERGE-TOO-BIG",
        "merge_picture_too_big": "DL-MERGE-TOO-BIG",
        "merge_no_photos": "DL-MERGE-NO-PHOTOS",
        "merge_failed": "DL-MERGE-FAILED",
        "large_files_off": "DL-LARGE-OFF",
        "large_files_private_only": "DL-LARGE-PRIVATE",
        "large_files_no_credit": "DL-LARGE-NO-CREDIT",
        "large_files_too_big": "DL-LARGE-TOO-BIG",
        "large_files_short": "DL-LARGE-SHORT",
        "large_files_send_failed": "DL-LARGE-SEND",
    },
    "sticker_bot": {
        "whomade_not_found": "ST-WHOMADE-UNKNOWN",
        "coedit_link_invalid": "ST-COEDIT-LINK",
        "coedit_pack_gone": "ST-COEDIT-GONE",
        "not_your_pack": "ST-OTHERS-PACK",
        "only_owner_coedit": "ST-CREATOR-ONLY",
        "only_owner_rename": "ST-CREATOR-ONLY",
        "only_owner_delete": "ST-CREATOR-ONLY",
        "rename_broken_state": "ST-RENAME-STATE",
        "renamed_failed": "ST-RENAME-FAILED",
        "delete_failed": "ST-DELETE-FAILED",
        "title_empty": "ST-TITLE-EMPTY",
        "remove_failed": "ST-REMOVE-FAILED",
        "image_process_failed": "ST-IMAGE",
        "video_convert_failed_redirect": "ST-VIDEO-CONVERT",
        "video_convert_generic_failed": "ST-VIDEO-FAILED",
        "animated_not_supported": "ST-ANIMATED",
        "import_invalid_source": "ST-IMPORT-SOURCE",
        "unrecognized": "ST-UNRECOGNIZED",
        "nothing_added_yet": "ST-NOTHING-ADDED",
        "no_sticker_to_tag": "ST-NO-STICKER",
        "retag_failed": "ST-RETAG",
    },
    "anon_bot": {
        "follow_link_invalid": "AN-LINK-INVALID",
        "follow_link_blocked": "AN-LINK-BLOCKED",
        "follow_link_paused": "AN-LINK-PAUSED",
        "inbox_gone": "AN-INBOX-GONE",
        "delivery_blocked": "AN-SENDER-BLOCKED",
        "delivery_forbidden": "AN-NOT-STARTED",
        "delivery_failed": "AN-DELIVERY",
        "reply_no_match": "AN-REPLY-NO-MATCH",
        "must_reply": "AN-NEEDS-REPLY",
        "must_reply_opening": "AN-OPENING-NEEDS-REPLY",
        "must_reply_ambiguous": "AN-AMBIGUOUS",
        "reply_forbidden": "AN-REPLY-BLOCKED",
        "reply_failed": "AN-REPLY-FAILED",
        "reply_stale": "AN-REPLY-STALE",
        "too_fast": "AN-TOO-FAST",
        "edit_not_relayed": "AN-EDIT-NOT-RELAYED",
        "conv_gone": "AN-CONV-GONE",
        "not_your_conversation": "AN-OTHERS-CONV",
        "jump_no_anchor": "AN-NO-ANCHOR",
        "which_unknown": "AN-WHICH-UNKNOWN",
        "archive_not_owner": "AN-ARCHIVE-HOLDER-ONLY",
        "generic_nudge": "AN-UNADDRESSED",
        "reaction_not_passed": "AN-REACTION-NOT-PASSED",
        "reaction_one_only": "AN-REACTION-NOT-PASSED",
    },
}

JOB_ENDINGS = {
    "refused": "CV-REFUSED", "timeout": "CV-TIMEOUT", "too_slow": "CV-TOO-SLOW", "crash": "CV-CRASH",
    "no_memory": "CV-NO-MEMORY",
    "too_large": "CV-OUTPUT-TOO-BIG", "send_failed": "CV-SEND-FAILED", "interrupted": "CV-RESTARTED",
}
STICKER_ERRORS = {
    "err_timed_out": "ST-TG-TIMEOUT", "err_invalid_name": "ST-TG-BAD-NAME", "err_name_occupied": "ST-TG-NAME-TAKEN",
    "err_too_many_stickers": "ST-TG-PACK-FULL", "err_bad_format": "ST-TG-BAD-FORMAT", "err_generic": "ST-TG-REJECTED",
}
ASSEMBLED = set(JOB_ENDINGS.values()) | set(STICKER_ERRORS.values()) | {"ST-IMPORT", "DL-QUOTA-HOUR", "DL-QUOTA-DAY"}

ALERT_KEYS = {
    "shared": {"flood_wait", "donate_invalid_amount"},
    "convert_bot": {"unsupported_format", "conversion_expired", "too_big_to_send_alert", "queue_full",
                    "job_not_running", "unknown_order"},
    "downloader_bot": {"redeliver_expired"},
    "sticker_bot": {"not_your_pack", "only_owner_coedit", "only_owner_rename", "only_owner_delete"},
    "anon_bot": {"conv_gone", "not_your_conversation"},
}


BRIEF = {
    "FM-CRASH": "a handler failed with an error nothing caught; the person was told",
    "FM-FLOOD": "someone sent more updates in a minute than the flood gate allows; the rest were ignored",
    "FM-DONATE-CURRENCY": "/donate or /recharge named a currency the bot doesn't offer",
    "FM-DONATE-UNAVAILABLE": "a currency is offered but its payment token isn't set",
    "FM-DONATE-AMOUNT": "a payment amount wasn't a positive whole number, or came from an old button",
    "FM-DONATE-TOO-MANY": "a Stars payment above the per-payment maximum",
    "FM-DONATE-RANGE": "a card payment below or above that currency's range",
    "FM-INVOICE": "Telegram refused to create an invoice; nothing was charged",
    "FM-ERASE": "/deletemydata failed, nothing was erased; they were asked to retry",
    "FM-UPDATING": "turned away because the bots are paused for an update",
    "FM-UNKNOWN-COMMAND": "a slash command this bot doesn't have",
    "FM-FEEDBACK-LIMIT": "more /feedback in a day than allowed; this one wasn't taken",
    "FM-FEEDBACK-FAILED": "/feedback couldn't be saved; they were asked to send it again",
    "CV-NO-EXTENSION": "a file whose format couldn't be told from its name",
    "CV-TG-LIMIT": "a file over Telegram's 20 MB bot download limit",
    "CV-GROUP-LIMIT": "a file over 20 MB sent in a group, where big files aren't taken",
    "CV-BIG-UNAVAILABLE": "a file over 20 MB, and the route for big files was down",
    "CV-DOWNLOAD": "fetching the file from Telegram failed; nothing converted or charged",
    "CV-UNSUPPORTED": "a source or target format this server doesn't convert",
    "CV-TOO-BIG": "a file (or album) over ConvertBot's size limit",
    "CV-NO-TARGETS": "a readable format with nothing available to convert it to",
    "CV-NOTHING-SENDABLE": "a picture so big that every result would be over the send limit",
    "CV-MEGAPIXELS": "a picture over the megapixel limit (it would run out of memory)",
    "CV-FORMAT-TOO-BIG": "a target tapped whose result would be over the send limit (old menu)",
    "CV-STORAGE-USER": "their waiting files already fill their storage share",
    "CV-STORAGE-FULL": "the server is short of temporary space",
    "CV-QUEUE-FULL": "they already have the most conversions waiting",
    "CV-EXPIRED": "a format chosen for a file no longer waiting",
    "CV-JOB-GONE": "Stop tapped on a conversion that had already ended",
    "CV-NO-CREDIT": "the balance didn't cover the conversion when its turn came; not charged",
    "CV-UNKNOWN-ORDER": "a payment for an order the bot doesn't know; declined, not charged",
    "CV-MIXED-BATCH": "an album of different formats, which one conversion can't take",
    "CV-UNRECOGNIZED": "a message that's neither a file nor a format name",
    "CV-REFUSED": "the converter couldn't read or convert the file as asked",
    "CV-TIMEOUT": "a conversion ran past its time limit and was stopped",
    "CV-TOO-SLOW": "a conversion was on course to miss its time limit and was stopped early",
    "CV-CRASH": "the conversion worker died without a result (usually memory)",
    "CV-NO-MEMORY": "the conversion ran out of memory",
    "CV-OUTPUT-TOO-BIG": "the result came out over what this conversion may send back",
    "CV-SEND-FAILED": "the file converted but uploading it failed; ⚡ refunded",
    "CV-RESTARTED": "the bot restarted mid-conversion; ⚡ refunded",
    "DL-QUOTA-HOUR": "they used up their downloads for the hour",
    "DL-QUOTA-DAY": "they used up their downloads for the day",
    "DL-QUEUE-FULL": "they already have the most links in progress",
    "DL-NO-SPACE": "the server is short of disk space; the download didn't start",
    "DL-DELIVERY": "the media was fetched but uploading it to Telegram failed",
    "DL-BLOCKED": "the site refused every route (blocked, rate-limited or asked to log in)",
    "DL-NOT-FOUND": "the site says the post doesn't exist or isn't public",
    "DL-TOO-BIG": "the media is bigger than a bot may send",
    "DL-LARGE-OFF": "over the free size, and they haven't switched on large files",
    "DL-LARGE-PRIVATE": "a large file asked for in a group (large files are private-only)",
    "DL-LARGE-NO-CREDIT": "large files on, but the balance is below the cheapest one",
    "DL-LARGE-TOO-BIG": "over even the large-file ceiling (Telegram's 2000 MB at most)",
    "DL-LARGE-SHORT": "the large file cost more than the balance once its size was known",
    "DL-LARGE-SEND": "a large file was charged for and couldn't be sent; credit returned",
    "DL-ALL-ROUTES": "every download route failed, for some other reason",
    "DL-REDDIT": "a Reddit post's data couldn't be read",
    "DL-TWITTER-CARD": "an X/Twitter post's content couldn't be fetched; only the link went back",
    "DL-UNRECOGNIZED": "not a link from a supported site",
    "DL-BUTTON-EXPIRED": "a compressed/uncompressed button for a download no longer remembered",
    "DL-MERGE-TOO-BIG": "PDF/ZIP tapped on an album with too many or too big files",
    "DL-MERGE-NO-PHOTOS": "PDF tapped, but fewer than two photos came back",
    "DL-MERGE-FAILED": "an album's PDF or ZIP couldn't be built or sent",
    "ST-WHOMADE-UNKNOWN": "/whomade on a pack not made through this bot",
    "ST-COEDIT-LINK": "a co-editing link that matches no pack (reset or mistyped)",
    "ST-COEDIT-GONE": "a co-editing link to a pack that no longer exists",
    "ST-OTHERS-PACK": "a pack button tapped by someone who doesn't own the pack",
    "ST-CREATOR-ONLY": "a co-editor tried rename, co-edit settings or delete (creator only)",
    "ST-RENAME-STATE": "a new pack name arrived after rename lost track of the pack",
    "ST-RENAME-FAILED": "Telegram refused to rename a pack",
    "ST-DELETE-FAILED": "Telegram refused to delete a pack",
    "ST-TITLE-EMPTY": "an empty pack name",
    "ST-REMOVE-FAILED": "removing a sticker from a pack failed",
    "ST-IMAGE": "a picture couldn't be made into a sticker",
    "ST-VIDEO-CONVERT": "a video couldn't be encoded into a sticker Telegram accepts",
    "ST-VIDEO-FAILED": "an unexpected error while converting a video",
    "ST-ANIMATED": "an animated (.tgs) sticker sent, which the bot can't copy",
    "ST-IMPORT-SOURCE": "/import given something that isn't a pack name or link",
    "ST-IMPORT": "an import found no pack, or a WhatsApp archive with no usable images",
    "ST-TG-TIMEOUT": "Telegram didn't confirm adding a sticker in time",
    "ST-TG-BAD-NAME": "Telegram rejected the pack's generated internal name",
    "ST-TG-NAME-TAKEN": "the pack's generated internal name was already taken",
    "ST-TG-PACK-FULL": "the pack hit Telegram's 120-sticker limit",
    "ST-TG-BAD-FORMAT": "Telegram didn't accept the prepared sticker file",
    "ST-TG-REJECTED": "Telegram rejected a sticker for a reason the bot doesn't know",
    "ST-UNRECOGNIZED": "something sent outside a pack session that the bot has no use for",
    "ST-NOTHING-ADDED": "/done before any sticker was added",
    "ST-NO-STICKER": "an emoji sent before any sticker was added",
    "ST-RETAG": "changing a sticker's emoji failed",
    "AN-LINK-INVALID": "an inbox link that matches no inbox",
    "AN-LINK-BLOCKED": "someone the inbox holder blocked opened their link",
    "AN-LINK-PAUSED": "an inbox link opened while that inbox is paused",
    "AN-INBOX-GONE": "a link to an inbox that was deleted",
    "AN-SENDER-BLOCKED": "a guest the inbox holder blocked wrote; not relayed",
    "AN-NOT-STARTED": "the other person has stopped or never started the bot",
    "AN-DELIVERY": "Telegram rejected relaying a message",
    "AN-REPLY-NO-MATCH": "a reply matched someone else's conversation; not sent",
    "AN-NEEDS-REPLY": "a message that wasn't a reply while conversations are open",
    "AN-OPENING-NEEDS-REPLY": "something else arrived before the first message of a new conversation",
    "AN-AMBIGUOUS": "a message without a reply while several conversations are open",
    "AN-REPLY-BLOCKED": "the guest stopped the bot, so the inbox holder's reply couldn't go",
    "AN-REPLY-FAILED": "Telegram rejected relaying the inbox holder's reply",
    "AN-REPLY-STALE": "a reply to a message no conversation knows",
    "AN-TOO-FAST": "more messages in a minute than AnonBot allows",
    "AN-EDIT-NOT-RELAYED": "a relayed message was edited (edits don't travel)",
    "AN-CONV-GONE": "a button for a conversation that's gone or isn't theirs",
    "AN-OTHERS-CONV": "a button for a conversation they're not part of",
    "AN-NO-ANCHOR": "jump-to-start on a conversation whose first messages were pruned",
    "AN-WHICH-UNKNOWN": "/which or /archive on a message in no conversation",
    "AN-ARCHIVE-HOLDER-ONLY": "a guest tried to archive (inbox holder only)",
    "AN-UNADDRESSED": "a message from someone with no inbox and no open conversation",
    "AN-REACTION-NOT-PASSED": "a reaction the bot can't show the other side (premium emoji, or a second one)",
}


RENAMED = {
    "ST-OWNER-ONLY": "ST-CREATOR-ONLY", "AN-ARCHIVE-OWNER-ONLY": "AN-ARCHIVE-HOLDER-ONLY",
    "ST-NOT-YOURS": "ST-OTHERS-PACK", "AN-NOT-YOURS": "AN-OTHERS-CONV",
}


def current(code):
    return RENAMED.get(code, code)


def lookup(code):
    return PROBLEMS.get(current(code))


def brief(code) -> str:
    return BRIEF.get(current(code), "")

SIMPLE = frozenset({
    "FM-FLOOD", "FM-DONATE-CURRENCY", "FM-DONATE-AMOUNT", "FM-DONATE-TOO-MANY", "FM-DONATE-RANGE",
    "FM-UPDATING", "FM-UNKNOWN-COMMAND", "FM-FEEDBACK-LIMIT",
    "CV-NO-EXTENSION", "CV-TG-LIMIT", "CV-GROUP-LIMIT", "CV-UNSUPPORTED", "CV-TOO-BIG", "CV-NOTHING-SENDABLE",
    "CV-MEGAPIXELS", "CV-FORMAT-TOO-BIG", "CV-STORAGE-USER", "CV-QUEUE-FULL", "CV-EXPIRED",
    "CV-JOB-GONE", "CV-NO-CREDIT", "CV-MIXED-BATCH", "CV-UNRECOGNIZED", "CV-TOO-SLOW", "CV-NO-MEMORY",
    "DL-QUOTA-HOUR", "DL-QUOTA-DAY", "DL-QUEUE-FULL", "DL-TOO-BIG", "DL-UNRECOGNIZED",
    "DL-LARGE-OFF", "DL-LARGE-PRIVATE", "DL-LARGE-NO-CREDIT", "DL-LARGE-TOO-BIG", "DL-LARGE-SHORT",
    "DL-BUTTON-EXPIRED", "DL-MERGE-TOO-BIG", "DL-MERGE-NO-PHOTOS",
    "ST-WHOMADE-UNKNOWN", "ST-COEDIT-LINK", "ST-COEDIT-GONE", "ST-OTHERS-PACK", "ST-CREATOR-ONLY",
    "ST-TITLE-EMPTY", "ST-ANIMATED", "ST-IMPORT-SOURCE", "ST-TG-PACK-FULL", "ST-UNRECOGNIZED",
    "ST-NOTHING-ADDED", "ST-NO-STICKER",
    "AN-LINK-INVALID", "AN-LINK-BLOCKED", "AN-LINK-PAUSED", "AN-INBOX-GONE", "AN-SENDER-BLOCKED",
    "AN-NOT-STARTED", "AN-NEEDS-REPLY", "AN-OPENING-NEEDS-REPLY", "AN-AMBIGUOUS", "AN-REPLY-BLOCKED",
    "AN-REPLY-STALE", "AN-TOO-FAST", "AN-EDIT-NOT-RELAYED", "AN-CONV-GONE", "AN-OTHERS-CONV",
    "AN-NO-ANCHOR", "AN-WHICH-UNKNOWN", "AN-ARCHIVE-HOLDER-ONLY",
    "AN-UNADDRESSED", "AN-REACTION-NOT-PASSED",
})


URGENT, FAULT, REFUSED = 1, 2, 3

URGENT_CODES = frozenset({
    "FM-CRASH", "CV-CRASH",
    "FM-INVOICE",
    "FM-ERASE",
    "CV-STORAGE-FULL", "DL-NO-SPACE",
    "CV-RESTARTED",
    "AN-DELIVERY", "AN-REPLY-FAILED",
})


def level(code) -> int:
    code = current(code)
    if code in URGENT_CODES:
        return URGENT
    if code in SIMPLE:
        return REFUSED
    return FAULT


LEVEL_NAMES = {URGENT: "urgent", FAULT: "fault", REFUSED: "explained"}
LEVEL_ICONS = {URGENT: "🚨", FAULT: "⚠️", REFUSED: "•"}


GUIDED, LIMIT, GONE, OTHER_SIDE, PAUSED = "guided", "limit", "gone", "other_side", "paused"

KINDS = {
    GUIDED: frozenset({
        "FM-UNKNOWN-COMMAND", "FM-DONATE-CURRENCY", "FM-DONATE-AMOUNT",
        "CV-NO-EXTENSION", "CV-MIXED-BATCH", "CV-UNRECOGNIZED",
        "DL-UNRECOGNIZED",
        "ST-WHOMADE-UNKNOWN", "ST-TITLE-EMPTY", "ST-IMPORT-SOURCE", "ST-UNRECOGNIZED",
        "ST-NOTHING-ADDED", "ST-NO-STICKER",
        "AN-NEEDS-REPLY", "AN-OPENING-NEEDS-REPLY", "AN-AMBIGUOUS", "AN-REPLY-STALE",
        "AN-EDIT-NOT-RELAYED", "AN-WHICH-UNKNOWN", "AN-ARCHIVE-HOLDER-ONLY",
        "AN-UNADDRESSED",
    }),
    LIMIT: frozenset({
        "FM-FLOOD", "FM-DONATE-TOO-MANY", "FM-DONATE-RANGE", "FM-FEEDBACK-LIMIT",
        "CV-TG-LIMIT", "CV-GROUP-LIMIT", "CV-UNSUPPORTED", "CV-TOO-BIG", "CV-NOTHING-SENDABLE",
        "CV-MEGAPIXELS", "CV-FORMAT-TOO-BIG", "CV-STORAGE-USER", "CV-QUEUE-FULL", "CV-NO-CREDIT",
        "CV-TOO-SLOW", "CV-NO-MEMORY",
        "DL-QUOTA-HOUR", "DL-QUOTA-DAY", "DL-QUEUE-FULL", "DL-TOO-BIG", "DL-LARGE-OFF",
        "DL-LARGE-PRIVATE", "DL-LARGE-NO-CREDIT", "DL-LARGE-TOO-BIG", "DL-LARGE-SHORT",
        "DL-MERGE-TOO-BIG",
        "ST-ANIMATED", "ST-TG-PACK-FULL",
        "AN-TOO-FAST", "AN-REACTION-NOT-PASSED",
    }),
    GONE: frozenset({
        "CV-EXPIRED", "CV-JOB-GONE", "DL-BUTTON-EXPIRED", "DL-MERGE-NO-PHOTOS",
        "ST-COEDIT-LINK", "ST-COEDIT-GONE", "ST-OTHERS-PACK", "ST-CREATOR-ONLY",
        "AN-LINK-INVALID", "AN-CONV-GONE", "AN-OTHERS-CONV", "AN-NO-ANCHOR",
    }),
    OTHER_SIDE: frozenset({
        "AN-LINK-BLOCKED", "AN-LINK-PAUSED", "AN-INBOX-GONE", "AN-SENDER-BLOCKED",
        "AN-NOT-STARTED", "AN-REPLY-BLOCKED",
    }),
    PAUSED: frozenset({"FM-UPDATING"}),
}

KIND_ORDER = (GUIDED, LIMIT, GONE, OTHER_SIDE, PAUSED)

KIND_LABELS = {
    GUIDED: ("💡", "Shown how to use it",
             "sent something the bot couldn't take, and were told what to do instead"),
    LIMIT: ("📏", "Hit a limit",
            "too big, too many, too fast, not enough ⚡, or something not offered — each by design"),
    GONE: ("⌛", "Out of date, or not theirs",
           "an old button or link, a record that had gone, or somebody else's button"),
    OTHER_SIDE: ("🚫", "Blocked or unavailable",
                 "the other person blocked them, paused or deleted their inbox, or never "
                 "started the bot"),
    PAUSED: ("⏸", "Turned away during an update",
             "came while /pause was on, and were written down for /resume"),
}


def kind(code) -> "str | None":
    code = current(code)
    if level(code) != REFUSED:
        return None
    for name, codes in KINDS.items():
        if code in codes:
            return name
    return GUIDED


def kind_label(code) -> str:
    found = kind(code)
    if found is None:
        return f"{LEVEL_ICONS.get(level(code), '•')} {LEVEL_NAMES.get(level(code), '?')}"
    icon, heading, _ = KIND_LABELS[found]
    return f"{icon} {heading[0].lower()}{heading[1:]}"


def urgent(code) -> bool:
    return level(code) == URGENT


def reportable(code) -> bool:
    return is_code(code) and current(code) not in SIMPLE


WANTS_DETAILS = frozenset({
    "FM-CRASH", "CV-CRASH", "ST-VIDEO-FAILED",
    "FM-ERASE", "FM-FEEDBACK-FAILED",
    "AN-REPLY-NO-MATCH",
})


def wants_details(code) -> bool:
    return current(code) in WANTS_DETAILS


def code_for(bot: str, key: str) -> "str | None":
    return KEYS.get(bot, {}).get(key) or KEYS["shared"].get(key)


def code_line(code: "str | None") -> str:
    return f"\n\n{MARK}{code}" if code else ""


def find_code(text: "str | None") -> "str | None":
    match = _AT_END.search(text or "")
    return match.group(1) if match and match.group(1) in PROBLEMS else None


def strip_code(text: str) -> str:
    match = _AT_END.search(text or "")
    return text[:match.start()] if match else text


def new_incident() -> str:
    return "".join(secrets.choice(_INCIDENT_ALPHABET) for _ in range(6))


def is_code(value) -> bool:
    return isinstance(value, str) and current(value) in PROBLEMS


def is_incident(value) -> bool:
    return isinstance(value, str) and bool(INCIDENT_RE.match(value))


def decode(code: str) -> str:
    code = current(code)
    problem = PROBLEMS.get(code)
    if problem is None:
        return f"{code}: not a known code."
    return (f"{code} — {problem.title} ({problem.bot})\n\n"
            f"What happened: {problem.meaning}\n"
            f"Likely causes: {problem.causes}\n"
            f"What to check: {problem.check}\n"
            f"Details button: {'offered' if wants_details(code) else 'offered when it keeps happening to one person' if reportable(code) else 'not offered, the message explains itself'}")
