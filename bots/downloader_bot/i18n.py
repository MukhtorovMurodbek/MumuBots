"""Translation strings for DownloaderBot's end-user-facing text (English,
Uzbek, Russian). Deliberately duplicated per bot -- same "no shared files
between bots" independence as shared_features.py -- but the STRINGS content
here is specific to this bot's own commands and flows.

Admin-only output (/dbdump, /status) is intentionally NOT translated -- see
build_status_text/error_summary/detect_host_environment in
shared_features.py, left as plain English since only the bot owner reads
them. Text baked into rendered card images (cards.py) is also left in
English -- Pillow's built-in bitmap font (ImageFont.load_default) has no
Cyrillic glyphs, so translating it there would just draw tofu boxes.

The keys below split into two groups:
  - "Shared" keys (donate flow, sibling-bot blurb) exist under the exact
    same names in every bot's i18n.py, since shared_features.py is
    duplicated byte-identical across the family and calls t() with these
    names regardless of which bot it's running in.
  - Bot-specific keys, everything below the shared block, for this bot's
    own bot.py strings only.
"""
import asyncio

import db

SUPPORTED_LANGUAGES = ("en", "uz", "ru")
LANGUAGE_LABELS = {"en": "English 🇬🇧", "uz": "O'zbekcha 🇺🇿", "ru": "Русский 🇷🇺"}

# What every /start shows, whether or not the user already has a language.
# Deliberately not part of STRINGS: it is trilingual on purpose, so there
# is no single `lang` to look it up under.
LANGUAGE_PROMPT = (
    "👋 Welcome! / Xush kelibsiz! / Добро пожаловать!\n\n"
    "Please choose your language / Iltimos, tilni tanlang / "
    "Пожалуйста, выберите язык:"
)

STRINGS = {
    "en": {
        "transfer_waiting": "⏳ Another big file is being moved. Yours starts right after.",
        "quota_hour": (
            "You've reached the limit of {limit} downloads an hour ({used} used). The next one frees up in about {minutes} min."
        ),
        "quota_day": (
            "You've reached today's limit of {limit} downloads ({used} used). It frees up in about {minutes} min."
        ),
        "quota_donor_hint": (
            "Need more? Anyone who has ever donated, any amount, gets a much higher limit for good: /donate"
        ),
        "flood_wait": "Too fast — wait about {seconds} s and carry on.",
        # ---- shared keys (same name in every bot's i18n.py) ----
        "sibling_blurb": "Also part of this bot family, see below \U0001f447",
        "donation_nudge": (
            "💙 Find this bot useful? /donate helps pay for hosting. Totally optional."
        ),
        "donate_unknown_currency": 'Unknown currency "{currency}" — try xtr or usd.',
        "donate_currency_not_configured": "{currency} donations aren't set up on this bot yet — try Stars instead.",
        "donate_invalid_amount": "That's not a valid amount — try e.g. /donate 500 or /donate 5 usd.",
        "donate_prompt": (
            "Thank you! It goes toward hosting costs. Pick an amount, or Custom for your own."
        ),
        "donate_custom_button": "✏️ Custom {symbol}",
        "donate_too_many_stars": "That's a lot of stars! Keep it under {max} ⭐ per donation.",
        "donate_out_of_range": "{currency} donations need to be between {lo} and {hi} {symbol}.",
        "donate_invoice_title": "Contribute to hosting",
        "donate_invoice_description": "Helps pay for hosting, and adds {credited} ⚡ of credit for ConvertBot.",
        "donate_invoice_label": "Hosting contribution",
        "donate_invoice_description_fiat": 'A one-time voluntary donation towards hosting costs. Thank you!',
        "donate_prompt_credit": "Each ⭐ gives ⚡ credit for ConvertBot. Your next {left} ⭐ give {each} ⚡ each ({mult}×): {rate} ⚡ plus a bonus that expires {days} days after payment. ⚡ can NOT be withdrawn or turned back into Stars.",
        "donate_prompt_credit_base": "Each ⭐ gives {rate} ⚡ of credit for ConvertBot. ⚡ can NOT be withdrawn or turned back into Stars.",
        "donate_invoice_error": "⚠️ Telegram wouldn't create that invoice: {error}",
        "stars_unit": "Stars",
        "donate_custom_ask": "How many {unit} would you like to donate? Reply with a number.",
        "donate_invalid_amount_retry": "That's not a valid amount — send /donate to try again.",
        "donate_thanks": "🙏 Thank you for the {amount} ⭐ — genuinely appreciated!",
        "topup_thanks": "🙏 Thank you for {stars} ⭐!\n\nYou got {total} ⚡ of credit in {convert_bot} for conversions. Balance there: {balance} ⚡.",
        "topup_thanks_bonus": 'Of that, {bonus} ⚡ is bonus credit and expires on {date}.',
        "credit_cannot_be_withdrawn": "⚡ is credit for ConvertBot conversions and can NOT be withdrawn or turned back into Stars. Payment problem? /paysupport",
        "topup_refunded": "⚠️ Your {stars} ⭐ payment went through, but the ⚡ didn't reach your balance, so the {stars} ⭐ were refunded. Please try again in a few minutes.",
        "topup_failed": "⚠️ Your {stars} ⭐ payment went through, but the ⚡ didn't reach your balance. Nothing is lost — the owner will add it by hand. Follow up: /paysupport",
        "paysupport_text": "💳 Help with a payment\n\nPayments are FINAL: Stars are NOT refunded, and ⚡ can NOT be withdrawn or turned back into Stars. Only a payment that couldn't become credit is refunded, automatically and in full.\n\nCharged with no credit, or charged twice? Write to {contact} with the date, the amount and your Telegram ID, {user_id}. It's put right with credit.\n\n/balance lists your payments.",
        "report_button": '🐞 Add my details',
        "problem_logged_note": "Anonymous report is saved",
        "report_disclaimer": "📨 Add your details to this report?\n\nAlready saved, without you: the bot, error code {code}, incident {incident}, the time and the version.\n\nSend adds your Telegram ID, @username, language, and whether this is a private chat or a group. Never your messages or files.\n\nIt helps find your case in the logs. Your choice.",
        "report_send": '📨 Send my details',
        "report_cancel": '✖️ Cancel',
        "report_sent": "✅ Thank you — added to incident {incident}.",
        "report_already": "Your details are already on incident {incident}.",
        "report_cancelled": "Cancelled — nothing about you was sent.",
        "report_failed": "⚠️ The report couldn't be sent right now. Please try again later.",
        "report_invalid": 'This button no longer works.',
        "crash_notice": "⚠️ Something went wrong on my side, so that wasn't done. Please try again.",
        "sandbox_notice": "🧪 Test mode — no real Stars were charged for this.",
        "balance_header": "⚡ Your balance: {balance}",
        "balance_totals": "Paid {paid} ⭐ in total · credited {credited} ⚡ · spent {spent} ⚡",
        "balance_rate": 'Your next {left} ⭐ earn {each} ⚡ each ({mult}×).',
        "balance_rate_base": '1 ⭐ buys {rate} ⚡.',
        "balance_bonus_line": 'Of that, {bonus} ⚡ is bonus credit — {soon} ⚡ of it expires on {date}.',
        "balance_recent": "Recent:",
        "balance_empty_hint": "/donate adds credit whenever you want some.",
        "bot_short_description": (
            "Paste a public Instagram, TikTok, Pinterest, Reddit or X link."
        ),
        "bot_description": (
            "Paste a link to a public post and the media comes back. No command needed.\n"
            "\n"
            "Instagram, TikTok, Pinterest, Reddit and X. Carousels arrive whole, and a text post "
            "comes back as a rendered card.\n"
            "\n"
            "It fetches what is already public — it is not a way around a private account. "
            "English, Uzbek and Russian. /privacy says what it keeps."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Privacy",
        "privacy_kept_heading": "What this bot keeps:",
        "privacy_stored": (
            "• your Telegram ID, language, and caption and quality settings\n• your links: each while it's fetched, then the last five, for their buttons; dropped after 12 hours without using the bot\n• the last download, for 10 minutes, for its buttons and the same link sent again; your next link, /deletemydata or a restart deletes it sooner, and it never stays past 12 hours\n• Telegram's file IDs of posts already sent, filed under a scrambled post name, not under who asked, so a post can be resent without downloading it again; checked at least every 12 hours that the post is still public, deleted after 90 days unused\n• one row per download, with only the time, for the daily limit; deleted after 2 days\n• a timestamp each time you use the bot, to count users\n• daily button-tap counts for the whole bot, not per person\n• donations: the amount and Telegram's payment ID\n• whatever the bot is in the middle of doing with you, until it's done\n• in a group: its ID and /settings, until the bot leaves. It checks every message for a link and keeps nothing from the rest; a downloaded link counts as the poster's (the limit row, and the last download for 10 minutes)\n\nOtherwise the media isn't kept: it's fetched, sent and deleted."
        ),
        "privacy_problems_heading": "When something goes wrong:",
        "privacy_problems": (
            "Every error is logged without you: its code, an incident number, the time and the bot's version.\n\n\"Add my details\" under an error attaches your Telegram ID, @username, language and chat type (private or group) to that one incident — only if you tap Send. Never your messages or files. They are cleared after 30 days, or at once with /deletemydata. Error records are deleted after 180 days."
        ),
        "privacy_seen_by_heading": "Who else sees it:",
        "privacy_seen_by": (
            "• Telegram, which carries every message\n• the hosting provider and the database the bot uses"
        ),
        "privacy_others": (
            "• the site you linked, and the download services the bot falls back on. They get the link and see the bot's server, not you; what they log is up to their own policies"
        ),
        "privacy_kept_for_heading": "How long it stays:",
        "privacy_kept_for": "Settings stay until you erase them or stop using the bot. Usage times go after about three months. Payment records and your ⚡ balance stay longer, for payment disputes.\n\nNothing is sold, used for ads, or given to anyone not named above.",
        "privacy_your_choices": (
            "What you can do:\n/deletemydata — erase what this bot holds on you\n/terms — the rules\n\nBlocking the bot erases nothing, so send /deletemydata first."
        ),
        "terms_heading": "📜 Terms",
        "terms_use": (
            "Use it within the law and Telegram's terms. Harassing anyone or pushing past the bot's limits gets an account blocked."
        ),
        "terms_specific": (
            "Downloads: only what's already public — it's not a way into private accounts. Downloading doesn't make something yours; what you do with it is between you, its owner and the law. There's a daily limit per person, so the bot stays usable for everyone."
        ),
        "terms_money": "Money: /donate is voluntary and helps pay for hosting. Stars also add ⚡ credit for ConvertBot: 2 ⚡ per ⭐, or 6 for your first 500 Stars and 4 for the next 500; the part above 2 is bonus that expires 90 days after payment. Payments are FINAL: Stars are NOT refunded and credit can NOT be withdrawn — except a payment that couldn't become credit, which is refunded automatically. Telegram handles payments; the bot never sees a card. Problems: /paysupport",
        "terms_no_warranty": (
            "No promises: one person runs this for free, and it can be slow, wrong or down without warning. Keep your own copies of what matters."
        ),
        "policy_full_text": "Full text: {url}",
        "policy_contact": "Questions, complaints or a data request: {contact}",
        "delete_data_confirm": "⚠️ This erases what this bot holds on you. There is no undo.",
        "delete_data_consequences": "Your language, settings, download records and the held last download go; your daily limit resets. Nothing older was ever kept.\n\nPayment records stay, without your username, for payment disputes. Your ⚡ balance stays too.",
        "delete_data_button_yes": "🗑 Erase it",
        "delete_data_button_no": "↩️ Keep my data",
        "delete_data_kept": "Nothing was erased.",
        "delete_data_done": (
            "🗑 Done — {rows} record(s) erased. Send /start any time to begin fresh."
        ),
        "delete_data_failed": (
            "Couldn't erase it — something went wrong on my side. Please try again in a few minutes."
        ),
        "language_set_confirmation": "✅ Language set to English.",
        "cancel_header": "\u274c Cancelled:",
        "cancel_nothing": "Nothing to cancel.",
        "cancel_ask": "What should I stop? Here's what I'm waiting on:",
        "cancel_kept": "OK — nothing cancelled.",
        "cancel_reply_box_freed": "Your reply box is free again.",
        "cancel_button_all": "❌ All of it",
        "cancel_button_none": "↩️ Nothing, keep going",
        "cancel_button_donation": "💸 Donation amount",
        "cancel_item_donation": "the donation amount I asked you for",
        "cancel_item_stale_prompt": "an old unanswered question",
        "feedback_ask": (
            "💬 What would you like to tell the person who runs these bots? Reply to this message.\n\nIt's sent without your name, so they can't answer. /cancel to stop."
        ),
        "feedback_placeholder": "Your feedback",
        "feedback_text_only": "Please send text as a reply to this message, or /cancel.",
        "feedback_thanks": "✅ Thank you — sent anonymously.",
        "feedback_limit": "That's {per_day} today, the daily limit. Thank you — write again tomorrow.",
        "feedback_failed": "Not sent — something went wrong on my side. Please try again later.",
        "cancel_button_feedback": "💬 Feedback",
        "cancel_item_feedback": "the feedback I asked you for",
        "help_brief": (
            "How it works:\n\n• Paste a link and I send back what's behind it — no command needed. Instagram, TikTok, X, Pinterest and Reddit.\n• /settings: the caption, the file quality, and large files (over 48 MB, for ⚡ credit).\n• Ideas or problems: /feedback\n\nMain commands: /settings · /balance · /cancel\nEvery command: the full guide, below or /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Full guide",
        "help_brief_button": "↩️ Short version",
        "help_page_everything": "The full guide: /fullhelp",
        "help_page_gone": "That help page is gone. Send /help.",
        "help_btn_download": "🔗 Sending a link",
        "helppage_download": (
            "🔗 Sending a link — how downloading works\n"
            "\n"
            "Paste a link to a post — no command needed — and I send back what's in it:\n"
            "  - Instagram: reels, photos, whole carousels\n"
            "  - TikTok: videos without the watermark, photo slideshows\n"
            "  - Twitter/X: video and photos, or a clean picture of a text post\n"
            "  - Pinterest: the pin, at full size\n"
            "  - Reddit: the media of a media post, or a picture of a text post\n"
            "\n"
            "{quota}\n"
            "• A download counts when it starts, even if it then fails. The 📄/🖼 button and the 📄 PDF / 🗜 ZIP buttons under a delivery count as one download each. The same link sent again while I'm still fetching it gets a 👌 and doesn't count.\n"
            "• Up to {waiting} of your links can be on the way at once; I fetch {at_once} at a time, and the rest wait their turn.\n"
            "\n"
            "Size: free up to {free_mb} MB. {large}\n"
            "\n"
            "Under a delivery:\n"
            "• \"📄 Get it as an uncompressed file\" (or \"🖼 Get it compressed instead\") sends the same post again in the other quality; each button works once.\n"
            "• Under an album: 📄 PDF puts its photos in one PDF, 🗜 ZIP puts everything in one archive. Up to {merge_items} files and {merge_mb} MB.\n"
            "• /settings: the caption, the quality, and large files.\n"
            "\n"
            "Example: paste a TikTok link like https://www.tiktok.com/@name/video/123 — the video comes back without the watermark."
        ),
        "helppage_download_quota": "It's free, up to {per_day} downloads in any 24 hours and {per_hour} in any hour. The count looks back from now; it doesn't reset at midnight. When you reach it, I say when the next one frees up. Anyone who has ever paid through /donate, any amount, gets {donor_day} a day and {donor_hour} an hour, for good.",
        "helppage_download_quota_off": "It's free.",
        "helppage_download_large": "Bigger, up to {max} MB, only if you turn on large files in /settings, in a private chat: ⚡{first} up to 100 MB, and each 100 MB after that adds less (⚡{top} for {max} MB). The ⚡ is taken only once the file has arrived, never if it fails.",
        "helppage_download_large_off": "Anything bigger is refused.",
        "helppage_settings": (
            "⚙️ /settings — how your downloads arrive\n"
            "\n"
            "/settings shows your switches and how each is set. Tap one to change it; it stays that way for every later download.\n"
            "• Caption (on at first): a \"via @{username}\" line under each download. Also /caption on or /caption off.\n"
            "• Lossless (off at first). On: downloads come as files, exactly as the source had them, with no Telegram compression — but they don't play or show in the chat until you open them. Off: they come as photos and videos that play in the chat, compressed by Telegram. Also /lossless on or /lossless off. For one download only, use the 📄/🖼 button under it instead.\n"
            "{large}\n"
            "Example: send /lossless on, then an Instagram link: the photos come as files, at full quality."
        ),
        "helppage_settings_large": "• Large files (off at first). On: a download over {free_mb} MB is still fetched, up to {max} MB, and sent as a file for ⚡ credit — ⚡{first} up to 100 MB, ⚡{top} for {max} MB — taken only once it has arrived. Off: anything over {free_mb} MB is refused, and nothing is charged. In a private chat only.",
        "privacy_feedback_heading": "If you send /feedback:",
        "privacy_feedback": (
            "It reaches the person who runs these bots with the bot's name and your language — not your name, username or ID. They can't reply. A copy is kept for 30 days."
        ),
        # ---- downloader_bot-specific keys ----
        "start_greeting": (
            "Hey! Send me a link (Instagram, TikTok, Pinterest, Reddit, or "
            "Twitter/X) and I'll grab it for you.\n\n"
        ),
        "help_text": "Paste a link any time — no command needed:\n  - Instagram: reels, photos, whole carousels\n  - TikTok: videos without the watermark, and photo slideshows\n  - Twitter/X: video and photos, or a picture of a text post\n  - Pinterest: the pin, at full size\n  - Reddit: the media, or a picture of a text post\n\nUnder an album: 📄 PDF puts its photos into one PDF, 🗜 ZIP puts everything into one archive.\n\nCommands:\n/settings - caption, quality and large files (over 48 MB, for ⚡)\n/caption on|off - the \"via @{username}\" caption\n/lossless on|off - downloads as uncompressed files\n/cancel - stop something I'm waiting for\n/feedback - write to whoever runs these bots (anonymous)\n/donate - help pay for hosting (optional)\n/balance - your ⚡ credit\n/paysupport - trouble with a payment\n/en, /uz, /rus - switch language (or /language)\n/privacy - what I keep about you\n/terms - what I may be used for\n/deletemydata - delete everything I hold on you\n/start - start over\n/fullhelp - this guide (/help - the short one)\n\n⚠️ Payments are final — Stars are NOT refunded, and ⚡ credit can NOT be withdrawn.\n\n",
        "caption_state_on": "ON",
        "caption_state_off": "OFF",
        "settings_heading": "⚙️ Your settings. Tap to change any of them.",
        "caption_status": "Caption is currently {state}.",
        "caption_turned": "Download caption turned {state}.",
        "caption_toggle_answer": "Caption turned {state}.",
        "lossless_state_on": "ON",
        "lossless_state_off": "OFF",
        "lossless_status": "Lossless is {state}.\n\nON: downloads come as original files — no compression, but no preview in the chat.\nOFF: photos and videos that play in the chat, compressed by Telegram.",
        "lossless_turned": "Lossless downloads turned {state}.",
        "lossless_toggle_answer": "Lossless turned {state}.",
        "large_state_on": "ON",
        "large_state_off": "OFF",
        "large_status": "📦 Large files are {state}.\n\nON: downloads over {free} MB, up to {max} MB, come as files — ⚡{per} up to 100 MB, ⚡{top} for {max} MB. Charged only once the file arrives.\nOFF: anything over {free} MB is refused; nothing is charged.",
        "large_toggle_answer": "Large files: {state}.",
        "large_files_turned_on": "📦 Large files are on. Send the link again — ⚡{per} up to 100 MB, charged once it arrives. /settings turns it off.",
        "large_files_turn_on_button": "📦 Turn on large files",
        "large_files_off": "📦 This one is over {free} MB. Switch on large files below (or in /settings), then send the link again — ⚡{per} up to 100 MB.",
        "large_files_private_only": "📦 This one is bigger than {free} MB. Files that big only come in a private chat with me — send me the link there.",
        "large_files_no_credit": "📦 This one is bigger than {free} MB, which costs at least ⚡{per}, and your balance is ⚡{balance}. Top up with /donate, then send the link again.",
        "large_files_fetching": "📦 Over {free} MB — getting it as a large file (⚡{per} up to 100 MB, charged once it arrives).",
        "large_files_too_big": "📦 This one is bigger than {max} MB, the most Telegram lets me send. Try a shorter clip.",
        "large_files_short": "📦 It's {mb} MB (⚡{price}) and your balance is ⚡{balance}, so nothing was charged. Top up with /donate and send the link again.",
        "large_files_sending": "📦 {mb} MB is here. Sending it...",
        "large_files_send_failed": "📦 I got it, but couldn't send it to you. The ⚡{price} is back — your balance is ⚡{balance}.",
        "large_files_charged": "📦 {mb} MB, sent. ⚡{price} for it — your balance is now ⚡{balance}.",
        "download_credit_caption": "⬇️ via @{username}",
        "downloading": "Downloading...",
        "queued": "⏳ Busy with another download — yours starts in a moment.",
        "download_queue_full": 'You already have {count} links on the way. Send more when some of them arrive.',
        "download_short_on_space": "I'm short on temporary space right now — send that link again in a few minutes.",
        "restarting_send_again": "🔄 I'm being updated — send that again in a few seconds.",
        "update_soon_try_later": "🔧 I'm about to be updated — please try again in about {minutes} min. I'll message you when I'm back.",
        "update_soon_try_later_soon": "🔧 I'm being updated — please try again shortly. I'll message you when I'm back.",
        "update_will_reset": "🔧 I'm about to be updated, and what you're doing now will be reset. You can start again in a few minutes.",
        "update_done_try_now": "✅ Update done — try again now.",
        "gift_granted": "🎁 We passed 100 users — thank you! Here's {amount} ⚡ of free credit, good until {date}: about {conversions} paid conversions in {convert} or big downloads in {download}. /balance",
        "gift_claimed": "🎁 Gift: {amount} ⚡ of free credit — Mumu bots passed {users} users, thank you!\nGood until {date} (UTC): about {conversions} paid conversions in {convert} or big downloads in {download}.\n/balance",
        "download_failed": "Couldn't download that: {error}",
        "download_blocked": "🚫 The site turned me away — it's not your link. Try again in a bit.",
        "download_missing": "🔍 Couldn't find that post — it may be deleted or private.",
        "download_too_big": "📦 That one is bigger than Telegram lets me send. Try a shorter clip.",
        "download_all_routes_failed": "😕 That didn't work. Try again in a minute — if it keeps failing, it's the site, not the link.",
        "fetching": "Fetching...",
        "reddit_fetch_failed": "Couldn't fetch that Reddit post: {error}",
        "twitter_fetch_failed_link": "Couldn't fetch that post's content right now — here's the link: {url}",
        "unrecognized_message": (
            "That's not a link I know. Paste an Instagram, TikTok, Pinterest, Reddit or X link, or see /help."
        ),
        "redeliver_as_file": "📄 Get it as an uncompressed file",
        "redeliver_as_compressed": "🖼 Get it compressed instead",
        "redeliver_expired": (
            "This button has expired — send the link again."
        ),
        "album_delivered": "{count} files above.",
        "merge_too_big": "📦 Too much for one file (up to {items} files, {mb} MB). Everything is in the chat above.",
        "merge_picture_too_big": "📦 One picture is too big for a PDF (over {mp} MP). The pictures are in the chat above.",
        "merge_no_photos": "📄 A PDF needs at least two photos. The files are in the chat above.",
        "merge_failed": "😕 Couldn't pack them into one file. The files above are fine — send the link again to retry.",
        "unknown_command": "I don't recognize that command. Send /help to see what I can do.",
        # 2.2.0: the quality picker and group chats (bot.py).
        "quality_heading": '📹 {title}',
        "quality_paid_note": '⚡ — over {free} MB: paid with ⚡ credit (/settings, large files).',
        "quality_choose": 'Choose a quality ↓',
        "quality_more_button": '🎵 MP3 · ✂️ Trim · more ↗️',
        "quality_expired": 'That choice has expired — send the link again.',
        "quality_picked": '⏳ {height}p — fetching...',
        "group_hello": "👋 Hi! Post an Instagram, TikTok, Pinterest, Reddit or X link here and I'll send the video.\n\n/settings — what I download here, and how.",
        "group_cant_see": '⚠️ Right now I only see messages addressed to me. To see the links people post, make me an admin of this group.',
        "group_settings_text": '⚙️ What I do in this group — tap to change.\n\nQuality applies where the site offers a choice; other links come as the site sends them. Files over {free} MB are not sent in groups.',
        "group_on": '✅ Downloading links',
        "group_paused": '⏸ Paused — tap to resume',
        "group_other_videos": '🎬 Other video sites',
        "group_quality_best": 'Best',
        "group_as_video": '📹 Sent as videos',
        "group_as_file": '📄 Sent as files',
        "group_credit_on": '🏷 Credit line: on',
        "group_credit_off": '🏷 Credit line: off',
        "group_anyone": '🔓 Anyone can change these',
        "group_admins_only": '🔒 Only admins can change these',
        "group_admins_alert": "Only this group's admins can change that.",
        "group_saved": 'Saved ✅',
    },
    "uz": {
        "transfer_waiting": "⏳ Boshqa katta fayl ko'chirilmoqda. Sizniki undan keyin boshlanadi.",
        "quota_hour": "Soatlik limitga yetdingiz: {limit} ta ({used} ta ishlatildi). Keyingisi taxminan {minutes} daqiqadan keyin.",
        "quota_day": "Bugungi limitga yetdingiz: {limit} ta ({used} ta ishlatildi). Taxminan {minutes} daqiqadan keyin yana mumkin.",
        "quota_donor_hint": "Ko'proq kerakmi? Bir marta bo'lsa ham xayriya qilgan har kimga limit doimiy ravishda ancha oshiriladi: /donate",
        "flood_wait": "Juda tez — taxminan {seconds} soniya kutib, davom eting.",
        "sibling_blurb": 'Oilamizdagi boshqa botlar pastda 👇',
        "donation_nudge": "💙 Bot foydali bo'ldimi? /donate server xarajatlariga yordam beradi. Mutlaqo ixtiyoriy.",
        "donate_unknown_currency": '"{currency}" degan valyuta yo\'q — xtr yoki usd deb yozing.',
        "donate_currency_not_configured": "Bu botda hozircha {currency} bilan xayriya qilib bo'lmaydi — Stars'dan foydalaning.",
        "donate_invalid_amount": "Miqdor noto'g'ri — masalan, /donate 500 yoki /donate 5 usd deb yozing.",
        "donate_prompt": "Rahmat! Bu server xarajatlariga ketadi. Miqdorni tanlang yoki o'zingiz yozish uchun «Boshqa»ni bosing.",
        "donate_custom_button": "✏️ Boshqa {symbol}",
        "donate_too_many_stars": "Bu juda ko'p! Bir martalik xayriya {max} ⭐ dan oshmasin.",
        "donate_out_of_range": "{currency} bilan xayriya {lo} dan {hi} {symbol} gacha bo'lishi kerak.",
        "donate_invoice_title": 'Server xarajatlariga hissa',
        "donate_invoice_description": "Server xarajatlariga yordam beradi va ConvertBot uchun {credited} ⚡ kredit qo'shadi.",
        "donate_invoice_label": 'Xayriya',
        "donate_invoice_description_fiat": 'Server xarajatlari uchun bir martalik ixtiyoriy xayriya. Rahmat!',
        "donate_prompt_credit": "Har bir ⭐ ConvertBot uchun ⚡ kredit beradi. Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×): {rate} ⚡ va to'lovdan {days} kun o'tgach yo'qoladigan bonus. ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI.",
        "donate_prompt_credit_base": "Har bir ⭐ ConvertBot uchun {rate} ⚡ kredit beradi. ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI.",
        "donate_invoice_error": "⚠️ Telegram to'lov hisobini yaratmadi: {error}",
        "stars_unit": "Stars (yulduzcha)",
        "donate_custom_ask": 'Qancha {unit} xayriya qilmoqchisiz? Faqat sonni yozib yuboring.',
        "donate_invalid_amount_retry": "Miqdor noto'g'ri — qaytadan urinish uchun /donate yuboring.",
        "donate_thanks": '🙏 {amount} ⭐ uchun katta rahmat!',
        "topup_thanks": "🙏 {stars} ⭐ uchun rahmat!\n\n{convert_bot} da konvertatsiyalar uchun {total} ⚡ kredit oldingiz. U yerdagi balans: {balance} ⚡.",
        "topup_thanks_bonus": 'Shundan {bonus} ⚡ — bonus kredit, uning muddati {date} kuni tugaydi.',
        "credit_cannot_be_withdrawn": "⚡ — ConvertBot'dagi konvertatsiyalar uchun kredit, uni yechib olib yoki Stars'ga aylantirib BO'LMAYDI. To'lov bilan muammo? /paysupport",
        "topup_refunded": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin ⚡ balansingizga tushmadi, shuning uchun {stars} ⭐ qaytarildi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "topup_failed": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin ⚡ balansingizga tushmadi. Hech narsa yo'qolmaydi — egasi uni qo'lda qo'shib qo'yadi. Kuzatish: /paysupport",
        "paysupport_text": "💳 To'lov bo'yicha yordam\n\nTo'lovlar QAYTARILMAYDI: Stars qaytarilMAYDI, ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI. Faqat kreditga aylanmagan to'lov avtomatik ravishda to'liq qaytariladi.\n\nPul yechildi-yu kredit kelmadimi yoki ikki marta yechildimi? {contact} ga sana, miqdor va Telegram ID raqamingizni ({user_id}) yozing. Kredit bilan to'g'rilab beriladi.\n\n/balance to'lovlaringizni ko'rsatadi.",
        "report_button": "🐞 Ma'lumotlarimni qo'shish",
        "problem_logged_note": "Anonim hisobot saqlandi",
        "report_disclaimer": "📨 Bu xabarga ma'lumotlaringizni qo'shasizmi?\n\nSizsiz saqlangan: bot, xato kodi {code}, hodisa {incident}, vaqt va versiya.\n\n«Yuborish» Telegram ID, @username, til va chat shaxsiy yoki guruh ekanini qo'shadi. Xabarlaringiz va fayllaringiz hech qachon qo'shilmaydi.\n\nBu sizning holatingizni loglardan topishga yordam beradi. Tanlov sizda.",
        "report_send": "📨 Ma'lumotlarimni yuborish",
        "report_cancel": '✖️ Bekor qilish',
        "report_sent": "✅ Rahmat — {incident} hodisasiga qo'shildi.",
        "report_already": "Ma'lumotlaringiz {incident} hodisasida allaqachon bor.",
        "report_cancelled": "Bekor qilindi — siz haqingizda hech narsa yuborilmadi.",
        "report_failed": "⚠️ Hozir xabarni yuborib bo'lmadi. Keyinroq qayta urinib ko'ring.",
        "report_invalid": 'Bu tugma endi ishlamaydi.',
        "crash_notice": "⚠️ Men tomonda xatolik yuz berdi, shuning uchun bajarilmadi. Qaytadan urinib ko'ring.",
        "sandbox_notice": '🧪 Test rejimi — haqiqiy Stars yechilmadi.',
        "balance_header": "⚡ Balansingiz: {balance}",
        "balance_totals": "Jami {paid} ⭐ to'landi · {credited} ⚡ qo'shildi · {spent} ⚡ sarflandi",
        "balance_rate": 'Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×).',
        "balance_rate_base": '1 ⭐ = {rate} ⚡.',
        "balance_bonus_line": 'Shundan {bonus} ⚡ — bonus kredit; {soon} ⚡ ning muddati {date} kuni tugaydi.',
        "balance_recent": 'Oxirgi amallar:',
        "balance_empty_hint": '/donate orqali istalgan paytda kredit olishingiz mumkin.',
        "bot_short_description": (
            "Instagram, TikTok, Pinterest, Reddit yoki X havolasini tashlang."
        ),
        "bot_description": "Ochiq postning havolasini yuboring — fayl qaytib keladi. Buyruq shart emas.\n\nInstagram, TikTok, Pinterest, Reddit va X. Karusel to'liq keladi, matnli post esa chiroyli kartochka bo'lib qaytadi.\n\nBot faqat ochiq bo'lgan narsani oladi — yopiq akkauntlarni chetlab o'tmaydi. Ingliz, o'zbek va rus tillarida. Nima saqlanishi: /privacy",
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Maxfiylik",
        "privacy_kept_heading": "Bu bot nimalarni saqlaydi:",
        "privacy_stored": "• Telegram ID raqamingiz, tilingiz, izoh va sifat sozlamalaringiz\n• havolalaringiz: har biri yuklanayotganda, keyin tugmalari ishlashi uchun oxirgi beshtasi; botdan 12 soat foydalanilmasa o'chiriladi\n• oxirgi yuklama — 10 daqiqa, tugmalari va xuddi shu havola qayta yuborilganda ishlashi uchun; keyingi havolangiz, /deletemydata yoki qayta ishga tushish uni ertaroq o'chiradi, 12 soatdan ortiq hech qachon qolmaydi\n• yuborilgan postlarning Telegram fayl ID lari — kim so'raganiga emas, postning shifrlangan nomiga bog'langan, postni qayta yuklamay yuborish uchun; post hali ochiqligi kamida 12 soatda bir tekshiriladi, 90 kun ishlatilmasa o'chiriladi\n• har bir yuklama uchun bitta yozuv, faqat vaqti bilan — kunlik limit uchun; 2 kundan keyin o'chiriladi\n• har safar botdan foydalanganingiz vaqti — foydalanuvchilarni sanash uchun\n• butun bot bo'yicha kunlik tugma bosishlar soni, odamlar bo'yicha emas\n• xayriyalar: miqdor va Telegram to'lov ID si\n• bot siz bilan hozir bajarayotgan ish, u tugaguncha\n• guruhda: uning ID si va /settings, bot guruhdan chiqquncha. U har bir xabarda havola qidiradi, qolganidan hech narsa saqlamaydi; yuklangan havola uni yuborgan odamniki hisoblanadi (limit yozuvi va 10 daqiqalik oxirgi yuklama)\n\nBoshqa hollarda media saqlanmaydi: yuklanadi, yuboriladi va o'chiriladi.",
        "privacy_problems_heading": "Nimadir noto'g'ri ketganda:",
        "privacy_problems": "Har bir xato sizsiz yoziladi: kodi, hodisa raqami, vaqti va bot versiyasi.\n\nXato ostidagi «Ma'lumotlarimni qo'shish» o'sha hodisaga Telegram ID, @username, til va chat turini (shaxsiy yoki guruh) qo'shadi — faqat «Yuborish»ni bossangiz. Xabarlar va fayllar hech qachon qo'shilmaydi. Ular 30 kundan keyin yoki /deletemydata bilan darhol o'chiriladi. Xato yozuvlari 180 kundan keyin o'chiriladi.",
        "privacy_seen_by_heading": "Yana kim ko'ra oladi:",
        "privacy_seen_by": "• Telegram — barcha xabarlarni u yetkazadi\n• hosting provayderi va bot ishlatadigan ma'lumotlar bazasi",
        "privacy_others": "• siz havola bergan sayt va bot zaxira sifatida foydalanadigan yuklash xizmatlari. Ular havolani oladi va sizni emas, bot serverini ko'radi; nimani yozib qolishlari o'z siyosatlariga bog'liq",
        "privacy_kept_for_heading": "Qancha vaqt saqlanadi:",
        "privacy_kept_for": "Sozlamalar siz o'chirmaguningizcha yoki botdan foydalanishni to'xtatmaguningizcha saqlanadi. Foydalanish vaqtlari taxminan uch oydan keyin o'chiriladi. To'lov yozuvlari va ⚡ balansingiz to'lov nizolari uchun uzoqroq saqlanadi.\n\nHech narsa sotilmaydi, reklamaga ishlatilmaydi va yuqorida aytilmaganlarga berilmaydi.",
        "privacy_your_choices": "Nima qila olasiz:\n/deletemydata — bot siz haqingizda saqlagan narsalarni o'chirish\n/terms — qoidalar\n\nBotni bloklash hech narsani o'chirmaydi, shuning uchun avval /deletemydata yuboring.",
        "terms_heading": "📜 Shartlar",
        "terms_use": "Botdan qonun va Telegram shartlari doirasida foydalaning. Kimnidir bezovta qilgan yoki bot chegaralarini buzgan akkaunt bloklanadi.",
        "terms_specific": "Yuklamalar: faqat allaqachon ochiq narsalar — bu yopiq akkauntlarga kirish yo'li emas. Yuklab olish narsani sizniki qilmaydi; u bilan nima qilishingiz siz, uning egasi va qonun o'rtasidagi masala. Bot hammaga yetishi uchun har bir odamga kunlik limit bor.",
        "terms_money": "Pul: /donate ixtiyoriy va server xarajatlariga yordam beradi. Stars ConvertBot uchun ⚡ kredit ham beradi: har ⭐ uchun 2 ⚡, ilk 500 Stars uchun 6 va keyingi 500 uchun 4; 2 dan ortig'i bonus bo'lib, to'lovdan 90 kun o'tgach yo'qoladi. To'lovlar QAYTARILMAYDI: Stars qaytarilMAYDI, kreditni yechib olib BO'LMAYDI — kreditga aylanmagan to'lov bundan mustasno, u avtomatik qaytariladi. To'lovlarni Telegram amalga oshiradi; bot karta raqamini ko'rmaydi. Muammo: /paysupport",
        "terms_no_warranty": "Kafolat yo'q: buni bir kishi bepul yuritadi, u sekin ishlashi, xato qilishi yoki ogohlantirishsiz to'xtashi mumkin. Muhim narsalarning nusxasini o'zingizda saqlang.",
        "policy_full_text": "To'liq matn: {url}",
        "policy_contact": "Savol, shikoyat yoki ma'lumot so'rovi uchun: {contact}",
        "delete_data_confirm": "⚠️ Bot siz haqingizda saqlagan ma'lumotlar o'chiriladi. Buni ortga qaytarib bo'lmaydi.",
        "delete_data_consequences": "Tilingiz, sozlamalaringiz, yuklama yozuvlari va saqlanayotgan oxirgi yuklama o'chadi; kunlik limit yangilanadi. Undan eskisi hech qachon saqlanmagan.\n\nTo'lov yozuvlari to'lov nizolari uchun username'ingizsiz qoladi. ⚡ balansingiz ham qoladi.",
        "delete_data_button_yes": "🗑 O'chirilsin",
        "delete_data_button_no": "↩️ Ma'lumotlarim qolsin",
        "delete_data_kept": "Hech narsa o'chirilmadi.",
        "delete_data_done": "🗑 Tayyor — {rows} ta yozuv o'chirildi. Yangidan boshlash uchun istalgan vaqt /start yuboring.",
        "delete_data_failed": "O'chirib bo'lmadi — men tomonda xatolik yuz berdi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "language_set_confirmation": "✅ Til o'zbekchaga o'zgartirildi.",
        "cancel_header": "\u274c Bekor qilindi:",
        "cancel_nothing": "Bekor qilinadigan narsa yo'q.",
        "cancel_ask": 'Qaysi birini bekor qilay? Hozir quyidagilar kutilmoqda:',
        "cancel_kept": "Yaxshi — hech narsa bekor qilinmadi.",
        "cancel_reply_box_freed": "Xabar yozish maydoni endi bo'sh.",
        "cancel_button_all": "❌ Hammasini",
        "cancel_button_none": '↩️ Hech birini, davom etamiz',
        "cancel_button_donation": "💸 Xayriya miqdori",
        "cancel_item_donation": 'kiritilishi kutilayotgan xayriya miqdori',
        "cancel_item_stale_prompt": "javobsiz qolgan eski savol",
        "feedback_ask": (
            "💬 Bu botlarni yuritayotgan odamga nima demoqchisiz? Shu xabarga javob qilib yozing.\n\nXabar ismingizsiz yuboriladi, shuning uchun sizga javob yoza olmaydi. To'xtatish: /cancel"
        ),
        "feedback_placeholder": "Fikringiz",
        "feedback_text_only": "Shu xabarga javob qilib matn yuboring yoki /cancel.",
        "feedback_thanks": "✅ Rahmat — anonim yuborildi.",
        "feedback_limit": "Bugun {per_day} ta bo'ldi, bu kunlik chegara. Rahmat — ertaga yana yozing.",
        "feedback_failed": "Yuborilmadi — men tomonda xatolik yuz berdi. Keyinroq qayta urinib ko'ring.",
        "cancel_button_feedback": "💬 Fikr-mulohaza",
        "cancel_item_feedback": "men so'ragan fikr-mulohaza",
        "help_brief": (
            "Bot qanday ishlaydi:\n\n• Havola yuboring — ichidagini yuboraman, buyruq kerak emas. Instagram, TikTok, X, Pinterest va Reddit.\n• /settings: izoh, fayl sifati va katta fayllar (48 MB dan katta, ⚡ evaziga).\n• Taklif yoki muammo: /feedback\n\nAsosiy buyruqlar: /settings · /balance · /cancel\nBarcha buyruqlar: to'liq qo'llanma, pastda yoki /fullhelp.\n\n"
        ),
        "help_full_button": "📖 To'liq qo'llanma",
        "help_brief_button": "↩️ Qisqa variant",
        "help_page_everything": "To'liq qo'llanma: /fullhelp",
        "help_page_gone": "Bu yordam sahifasi endi yo'q. /help yuboring.",
        "help_btn_download": "🔗 Havola yuborish",
        "helppage_download": (
            "🔗 Havola yuborish — yuklab olish qanday ishlaydi\n"
            "\n"
            "Postning havolasini yuboring — buyruq kerak emas — ichidagisini sizga yuboraman:\n"
            "  - Instagram: reels, rasmlar, butun karusellar\n"
            "  - TikTok: suv belgisisiz videolar, rasmli slayd-shoular\n"
            "  - Twitter/X: video va rasmlar yoki matnli post uchun toza rasm-karta\n"
            "  - Pinterest: pin, to'liq o'lchamda\n"
            "  - Reddit: media post bo'lsa media, matnli bo'lsa rasm-karta\n"
            "\n"
            "{quota}\n"
            "• Yuklab olish boshlanganda hisoblanadi, keyin xato bo'lsa ham. Yetkazilgan fayl ostidagi 📄/🖼 tugmasi hamda 📄 PDF / 🗜 ZIP tugmalari har biri bitta yuklab olish hisoblanadi. Hali yuklab olayotgan havolani qayta yuborsangiz, 👌 olasiz va u hisoblanmaydi.\n"
            "• Bir vaqtda {waiting} tagacha havolangiz yo'lda bo'lishi mumkin; bir paytda {at_once} tasini yuklayman, qolganlari navbat kutadi.\n"
            "\n"
            "Hajm: {free_mb} MB gacha bepul. {large}\n"
            "\n"
            "Yetkazilgan fayl ostida:\n"
            "• \"📄 Siqilmagan fayl sifatida olish\" (yoki \"🖼 Siqilgan holda olish\") o'sha postni boshqa sifatda yana yuboradi; har bir tugma bir marta ishlaydi.\n"
            "• Albom ostida: 📄 PDF uning rasmlarini bitta PDF'ga, 🗜 ZIP hammasini bitta arxivga joylaydi. {merge_items} tagacha fayl va {merge_mb} MB gacha.\n"
            "• /settings: izoh, sifat va katta fayllar.\n"
            "\n"
            "Misol: https://www.tiktok.com/@name/video/123 kabi TikTok havolasini yuboring — video suv belgisisiz keladi."
        ),
        "helppage_download_quota": "Bepul: istalgan 24 soatda {per_day} ta va istalgan bir soatda {per_hour} ta yuklab olish. Hisob hozirdan orqaga qarab yuritiladi, yarim tunda nolga tushmaydi. Chegaraga yetsangiz, keyingisi qachon bo'shashini aytaman. /donate orqali bir marta bo'lsa ham, istalgan miqdorda to'lagan har kim uchun chegara butunlay kuniga {donor_day} ta va soatiga {donor_hour} ta bo'ladi.",
        "helppage_download_quota_off": "Bepul.",
        "helppage_download_large": "Kattarog'i, {max} MB gacha, faqat /settings da katta fayllarni yoqsangiz va shaxsiy chatda: 100 MB gacha ⚡{first}, keyingi har 100 MB arzonroq ({max} MB uchun ⚡{top}). ⚡ fayl yetib kelgandan keyingina yechiladi, xato bo'lsa umuman yechilmaydi.",
        "helppage_download_large_off": "Undan kattasi rad etiladi.",
        "helppage_settings": (
            "⚙️ /settings — yuklamalaringiz qanday kelishi\n"
            "\n"
            "/settings kalitlaringizni va ularning holatini ko'rsatadi. O'zgartirish uchun birini bosing; keyingi barcha yuklamalar uchun shunday qoladi.\n"
            "• Izoh (dastlab yoniq): har bir yuklama ostida \"@{username} orqali\" qatori. Yana: /caption on yoki /caption off.\n"
            "• Siqilmagan rejim (dastlab o'chiq). Yoniq: fayllar asl sifatida, hujjat ko'rinishida keladi, Telegram ularni siqmaydi — lekin ochmaguningizcha chatda ko'rinmaydi va ijro etilmaydi. O'chiq: chatda ko'rinadigan rasm va video bo'lib keladi, Telegram ularni siqadi. Yana: /lossless on yoki /lossless off. Faqat bitta yuklama uchun uning ostidagi 📄/🖼 tugmasidan foydalaning.\n"
            "{large}\n"
            "Misol: /lossless on yuboring, keyin Instagram havolasini: rasmlar to'liq sifatda, fayl bo'lib keladi."
        ),
        "helppage_settings_large": "• Katta fayllar (dastlab o'chiq). Yoniq: {free_mb} MB dan katta yuklama ham {max} MB gacha olinadi va ⚡ kredit evaziga fayl sifatida yuboriladi — 100 MB gacha ⚡{first}, {max} MB uchun ⚡{top} — fayl yetib kelgandan keyingina yechiladi. O'chiq: {free_mb} MB dan kattasi rad etiladi va hech narsa yechilmaydi. Faqat shaxsiy chatda.",
        "privacy_feedback_heading": "/feedback yuborsangiz:",
        "privacy_feedback": (
            "U botlarni yuritayotgan odamga bot nomi va tilingiz bilan yetib boradi — ismingiz, username yoki ID raqamingiz bilan emas. U javob yoza olmaydi. Nusxasi 30 kun saqlanadi."
        ),
        "start_greeting": (
            "Salom! Menga havola yuboring (Instagram, TikTok, Pinterest, Reddit "
            "yoki Twitter/X) va men uni siz uchun yuklab beraman.\n\n"
        ),
        "help_text": "Istalgan vaqt havola yuboring — buyruq kerak emas:\n  - Instagram: reels, rasmlar, butun karusel\n  - TikTok: suv belgisiz video va rasm-slaydlar\n  - Twitter/X: video va rasmlar yoki matnli post rasmi\n  - Pinterest: pin, asl o'lchamda\n  - Reddit: media yoki matnli post rasmi\n\nAlbom ostida: 📄 PDF rasmlarni bitta PDF ga jamlaydi, 🗜 ZIP hammasini bitta arxivga soladi.\n\nBuyruqlar:\n/settings - izoh, sifat va katta fayllar (48 MB dan katta, ⚡ evaziga)\n/caption on|off - \"via @{username}\" yozuvi\n/lossless on|off - fayllarni siqilmagan holda olish\n/cancel - men kutayotgan narsani to'xtatish\n/feedback - botlarni yuritayotgan odamga yozish (anonim)\n/donate - server xarajatlariga yordam (ixtiyoriy)\n/balance - ⚡ kreditingiz\n/paysupport - to'lov bilan muammo\n/en, /uz, /rus - tilni almashtirish (yoki /language)\n/privacy - siz haqingizda nimalar saqlanadi\n/terms - botdan nima uchun foydalanish mumkin\n/deletemydata - siz haqingizdagi hamma narsani o'chirish\n/start - boshidan boshlash\n/fullhelp - shu qo'llanma (/help - qisqasi)\n\n⚠️ To'lovlar qaytarilmaydi — Stars qaytarilMAYDI, ⚡ kreditni yechib olib BO'LMAYDI.\n\n",
        "caption_state_on": "YONIQ",
        "caption_state_off": "O'CHIQ",
        "settings_heading": "⚙️ Sozlamalaringiz. O'zgartirish uchun bosing.",
        "caption_status": "Izoh hozir {state}.",
        "caption_turned": 'Fayl izohi endi {state}.',
        "caption_toggle_answer": 'Izoh endi {state}.',
        "lossless_state_on": "YONIQ",
        "lossless_state_off": "O'CHIQ",
        "lossless_status": "Siqilmagan rejim {state}.\n\nYONIQ: fayllar asl holida keladi — siqilmaydi, lekin chatda ko'rinmaydi.\nO'CHIQ: chatda ko'rinadigan rasm va video, Telegram siqadi.",
        "lossless_turned": 'Siqilmagan holda yuklash endi {state}.',
        "lossless_toggle_answer": 'Siqilmagan rejim endi {state}.',
        "large_state_on": "YONIQ",
        "large_state_off": "O'CHIQ",
        "large_status": "📦 Katta fayllar {state}.\n\nYONIQ: {free} MB dan katta, {max} MB gacha yuklamalar fayl bo'lib keladi — 100 MB gacha ⚡{per}, {max} MB uchun ⚡{top}. Kredit fayl yetib kelgandan keyingina yechiladi.\nO'CHIQ: {free} MB dan kattasi rad etiladi; hech narsa yechilmaydi.",
        "large_toggle_answer": "Katta fayllar: {state}.",
        "large_files_turned_on": "📦 Katta fayllar yoqildi. Havolani qayta yuboring — 100 MB gacha ⚡{per}, fayl yetib kelgach yechiladi. O'chirish: /settings",
        "large_files_turn_on_button": "📦 Katta fayllarni yoqish",
        "large_files_off": "📦 Bu fayl {free} MB dan katta. Pastda (yoki /settings da) katta fayllarni yoqing, keyin havolani qayta yuboring — 100 MB gacha ⚡{per}.",
        "large_files_private_only": "📦 Bu fayl {free} MB dan katta. Bunday fayllarni faqat men bilan shaxsiy chatda yuboraman — havolani o'sha yerga tashlang.",
        "large_files_no_credit": "📦 Bu fayl {free} MB dan katta, narxi kamida ⚡{per}, balansingizda esa ⚡{balance} bor. /donate orqali to'ldiring, keyin havolani yana yuboring.",
        "large_files_fetching": "📦 {free} MB dan katta — katta fayl sifatida olinmoqda (100 MB gacha ⚡{per}, yetib kelgach yechiladi).",
        "large_files_too_big": "📦 Bu fayl {max} MB dan katta — Telegram menga bundan kattasini yuborishga ruxsat bermaydi. Qisqaroq videoni sinab ko'ring.",
        "large_files_short": "📦 Fayl {mb} MB (⚡{price}), balansingiz esa ⚡{balance}, shuning uchun hech narsa yechilmadi. /donate bilan to'ldiring va havolani qayta yuboring.",
        "large_files_sending": "📦 {mb} MB tayyor. Yuboryapman...",
        "large_files_send_failed": "📦 Faylni oldim, lekin sizga yubora olmadim. ⚡{price} qaytarildi — balansingiz ⚡{balance}.",
        "large_files_charged": "📦 {mb} MB yuborildi. Buning uchun ⚡{price} yechildi — balansingiz endi ⚡{balance}.",
        "download_credit_caption": "⬇️ @{username} orqali",
        "downloading": "Yuklanmoqda...",
        "queued": '⏳ Boshqa fayl yuklanmoqda — sizniki birozdan keyin boshlanadi.',
        "download_queue_full": 'Sizning {count} ta havolangiz yuklanmoqda. Ulardan biri kelgach, yana yuboring.',
        "download_short_on_space": 'Hozir vaqtinchalik joy yetishmayapti — havolani bir necha daqiqadan keyin qayta yuboring.',
        "restarting_send_again": "🔄 Bot yangilanmoqda — bir necha soniyadan keyin qayta yuboring.",
        "update_soon_try_later": "🔧 Bot hozir yangilanadi — taxminan {minutes} daqiqadan keyin qayta urinib ko'ring. Ishga tushgach, xabar beraman.",
        "update_soon_try_later_soon": "🔧 Bot yangilanmoqda — birozdan keyin qayta urinib ko'ring. Ishga tushgach, xabar beraman.",
        "update_will_reset": "🔧 Bot hozir yangilanadi va hozirgi ishingiz to'xtaydi. Bir necha daqiqadan keyin qaytadan boshlashingiz mumkin.",
        "update_done_try_now": "✅ Yangilanish tugadi — endi qayta urinib ko'ring.",
        "gift_granted": "🎁 100 foydalanuvchidan oshdik — rahmat! Sizga {amount} ⚡ bepul kredit, {date} gacha amal qiladi: {convert} da taxminan {conversions} ta pullik konvertatsiya yoki {download} da katta fayllar. /balance",
        "gift_claimed": "🎁 Sovg'a: {amount} ⚡ bepul kredit — Mumu botlari {users} foydalanuvchidan oshdi, rahmat!\n{date} (UTC) gacha amal qiladi: {convert} da taxminan {conversions} ta pullik konvertatsiya yoki {download} da katta fayllar.\n/balance",
        "download_failed": "Buni yuklab bo'lmadi: {error}",
        "download_blocked": "🚫 Sayt meni qaytardi — havolangizda muammo yo'q. Birozdan keyin urinib ko'ring.",
        "download_missing": "🔍 Bu post topilmadi — o'chirilgan yoki yopiq bo'lishi mumkin.",
        "download_too_big": '📦 Bu fayl Telegram ruxsat beradigan hajmdan katta. Qisqaroq video sinab ko‘ring.',
        "download_all_routes_failed": "😕 Bo'lmadi. Bir daqiqadan keyin urinib ko'ring — takrorlanaversa, muammo saytda, havolada emas.",
        "fetching": "Olinmoqda...",
        "reddit_fetch_failed": "Bu Reddit postini olib bo'lmadi: {error}",
        "twitter_fetch_failed_link": "Hozircha bu post mazmunini olib bo'lmadi — mana havola: {url}",
        "unrecognized_message": "Bu men biladigan havola emas. Instagram, TikTok, Pinterest, Reddit yoki X havolasini yuboring yoki /help ni ko'ring.",
        "redeliver_as_file": "📄 Siqilmagan fayl sifatida olish",
        "redeliver_as_compressed": "🖼 Siqilgan holda olish",
        "redeliver_expired": "Bu tugmaning muddati o'tgan — havolani qayta yuboring.",
        "album_delivered": "Yuqorida {count} ta fayl.",
        "merge_too_big": "📦 Bitta fayl uchun juda ko'p ({items} ta fayl, {mb} MB gacha). Hammasi yuqoridagi chatda.",
        "merge_picture_too_big": "📦 Rasmlardan biri PDF uchun juda katta ({mp} MP dan ortiq). Rasmlar yuqoridagi chatda.",
        "merge_no_photos": "📄 PDF uchun kamida ikkita rasm kerak. Fayllar yuqoridagi chatda.",
        "merge_failed": "😕 Ularni bitta faylga jamlab bo'lmadi. Yuqoridagi fayllar joyida — qayta urinish uchun havolani yana yuboring.",
        "unknown_command": "Bunday buyruq yo'q. Bot nimalar qila olishini bilish uchun /help yuboring.",
        # 2.2.0: the quality picker and group chats (bot.py).
        "quality_heading": '📹 {title}',
        "quality_paid_note": '⚡ — {free} MB dan katta: ⚡ kredit bilan pullik (/settings, katta fayllar).',
        "quality_choose": 'Sifatni tanlang ↓',
        "quality_more_button": '🎵 MP3 · ✂️ Kesish · boshqalar ↗️',
        "quality_expired": 'Bu tanlov eskirgan — havolani qaytadan yuboring.',
        "quality_picked": '⏳ {height}p — olinmoqda...',
        "group_hello": "👋 Salom! Bu yerga Instagram, TikTok, Pinterest, Reddit yoki X havolasini yuboring — videoni yuboraman.\n\n/settings — bu yerda nimani va qanday yuklashim.",
        "group_cant_see": "⚠️ Hozir faqat menga yozilgan xabarlarni ko'raman. Odamlar yuborgan havolalarni ko'rishim uchun meni guruh admini qiling.",
        "group_settings_text": "⚙️ Bu guruhda nima qilaman — o'zgartirish uchun bosing.\n\nSifat sayt tanlov bergan joyda ishlaydi; boshqa havolalar sayt qanday bersa, shunday keladi. {free} MB dan katta fayllar guruhlarda yuborilmaydi.",
        "group_on": '✅ Havolalarni yuklayapman',
        "group_paused": "⏸ To'xtatilgan — davom ettirish uchun bosing",
        "group_other_videos": '🎬 Boshqa video saytlar',
        "group_quality_best": 'Eng yaxshi',
        "group_as_video": '📹 Video sifatida',
        "group_as_file": '📄 Fayl sifatida',
        "group_credit_on": '🏷 Bot nomi yozuvi: yoqilgan',
        "group_credit_off": "🏷 Bot nomi yozuvi: o'chirilgan",
        "group_anyone": "🔓 Har kim o'zgartira oladi",
        "group_admins_only": "🔒 Faqat adminlar o'zgartiradi",
        "group_admins_alert": "Buni faqat guruh adminlari o'zgartira oladi.",
        "group_saved": 'Saqlandi ✅',
    },
    "ru": {
        "transfer_waiting": "⏳ Сейчас переносится другой большой файл. Ваш начнётся сразу после.",
        "quota_hour": (
            "Достигнут предел: {limit} загрузок в час (использовано {used}). Следующая — примерно через {minutes} мин."
        ),
        "quota_day": (
            "Достигнут дневной предел: {limit} загрузок (использовано {used}). Снова можно примерно через {minutes} мин."
        ),
        "quota_donor_hint": (
            "Нужно больше? Все, кто хоть раз сделал пожертвование (любое), получают намного больший предел навсегда: /donate"
        ),
        "flood_wait": "Слишком быстро — подождите около {seconds} с и продолжайте.",
        "sibling_blurb": "Тоже часть этой семьи ботов, смотрите ниже \U0001f447",
        "donation_nudge": (
            "💙 Бот полезен? /donate помогает оплачивать хостинг. Совершенно необязательно."
        ),
        "donate_unknown_currency": 'Неизвестная валюта "{currency}" — попробуйте xtr или usd.',
        "donate_currency_not_configured": "Пожертвования в {currency} на этом боте пока не настроены — попробуйте Stars.",
        "donate_invalid_amount": "Это некорректная сумма — попробуйте, например, /donate 500 или /donate 5 usd.",
        "donate_prompt": (
            "Спасибо! Это идёт на хостинг. Выберите сумму или «Другое», чтобы ввести свою."
        ),
        "donate_custom_button": "✏️ Другое {symbol}",
        "donate_too_many_stars": "Это очень много звёзд! Пусть будет меньше {max} ⭐ за одно пожертвование.",
        "donate_out_of_range": "Пожертвования в {currency} должны быть в диапазоне от {lo} до {hi} {symbol}.",
        "donate_invoice_title": "Поддержать хостинг",
        "donate_invoice_description": "Помогает оплачивать хостинг и добавляет {credited} ⚡ кредита для ConvertBot.",
        "donate_invoice_label": "Вклад в хостинг",
        "donate_invoice_description_fiat": 'Разовое добровольное пожертвование на хостинг. Спасибо!',
        "donate_prompt_credit": "Каждая ⭐ даёт ⚡ кредит для ConvertBot. Следующие {left} ⭐ дают по {each} ⚡ ({mult}×): {rate} ⚡ плюс бонус, который сгорает через {days} дней после оплаты. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.",
        "donate_prompt_credit_base": "Каждая ⭐ даёт {rate} ⚡ кредита для ConvertBot. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.",
        "donate_invoice_error": "⚠️ Telegram не смог создать этот счёт: {error}",
        "stars_unit": "Stars (звёзды)",
        "donate_custom_ask": "Сколько {unit} вы хотите пожертвовать? Ответьте числом.",
        "donate_invalid_amount_retry": "Это некорректная сумма — отправьте /donate, чтобы попробовать снова.",
        "donate_thanks": "🙏 Спасибо за {amount} ⭐ — это по-настоящему ценно!",
        "topup_thanks": "🙏 Спасибо за {stars} ⭐!\n\nВы получили {total} ⚡ кредита в {convert_bot} на конвертации. Баланс там: {balance} ⚡.",
        "topup_thanks_bonus": 'Из них {bonus} ⚡ — бонусный кредит, он сгорит {date}.',
        "credit_cannot_be_withdrawn": "⚡ — кредит на конвертации в ConvertBot, его НЕЛЬЗЯ вывести или обменять обратно на Stars. Проблема с оплатой? /paysupport",
        "topup_refunded": "⚠️ Платёж на {stars} ⭐ прошёл, но ⚡ не дошёл до баланса, поэтому {stars} ⭐ вернули. Попробуйте снова через несколько минут.",
        "topup_failed": "⚠️ Платёж на {stars} ⭐ прошёл, но ⚡ не дошёл до баланса. Ничего не потеряно — владелец добавит его вручную. Уточнить: /paysupport",
        "paysupport_text": "💳 Помощь с оплатой\n\nПлатежи окончательны: Stars НЕ возвращаются, ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars. Возвращается только платёж, который не удалось превратить в кредит, — автоматически и полностью.\n\nДеньги списаны, а кредита нет, или списали дважды? Напишите на {contact}: дата, сумма и ваш Telegram ID, {user_id}. Всё исправят кредитом.\n\n/balance показывает ваши платежи.",
        "report_button": '🐞 Добавить мои данные',
        "problem_logged_note": 'Анонимный отчёт сохранён',
        "report_disclaimer": "📨 Добавить ваши данные к этому отчёту?\n\nУже сохранено без вас: бот, код ошибки {code}, инцидент {incident}, время и версия.\n\n«Отправить» добавит ваш Telegram ID, @username, язык и то, личный это чат или группа. Никогда — ваши сообщения или файлы.\n\nЭто помогает найти ваш случай в логах. Решать вам.",
        "report_send": '📨 Отправить мои данные',
        "report_cancel": '✖️ Отмена',
        "report_sent": "✅ Спасибо — добавлено к инциденту {incident}.",
        "report_already": "Ваши данные уже есть в инциденте {incident}.",
        "report_cancelled": "Отменено — ничего о вас не отправлено.",
        "report_failed": '⚠️ Сейчас не удалось отправить сообщение. Попробуйте позже.',
        "report_invalid": 'Эта кнопка больше не работает.',
        "crash_notice": "⚠️ Что-то пошло не так с моей стороны, поэтому это не сделано. Попробуйте ещё раз.",
        "sandbox_notice": "🧪 Тестовый режим — настоящие Stars не списывались.",
        "balance_header": "⚡ Ваш баланс: {balance}",
        "balance_totals": "Всего оплачено {paid} ⭐ · начислено {credited} ⚡ · потрачено {spent} ⚡",
        "balance_rate": 'Следующие {left} ⭐ дают по {each} ⚡ ({mult}×).',
        "balance_rate_base": '1 ⭐ даёт {rate} ⚡.',
        "balance_bonus_line": 'Из них {bonus} ⚡ — бонусный кредит; {soon} ⚡ сгорит {date}.',
        "balance_recent": "Последние операции:",
        "balance_empty_hint": "/donate добавит кредит в любой момент.",
        "bot_short_description": (
            "Пришлите ссылку на пост в Instagram, TikTok, Pinterest, Reddit или X."
        ),
        "bot_description": (
            "Пришлите ссылку на открытый пост — вернётся сам файл. Команда не нужна.\n"
            "\n"
            "Instagram, TikTok, Pinterest, Reddit и X. Карусель приходит целиком, а текстовый "
            "пост возвращается аккуратной карточкой.\n"
            "\n"
            "Бот достаёт то, что и так открыто, — это не обход закрытого аккаунта. Английский, "
            "узбекский и русский. /privacy — что бот хранит."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Конфиденциальность",
        "privacy_kept_heading": "Что бот хранит:",
        "privacy_stored": (
            "• ваш Telegram ID, язык, настройки подписи и качества\n• ваши ссылки: каждая — пока скачивается, затем последние пять — для их кнопок; удаляются через 12 часов без использования бота\n• последняя загрузка — 10 минут, для её кнопок и повторной отправки той же ссылки; следующая ссылка, /deletemydata или перезапуск удаляют её раньше, дольше 12 часов она не хранится\n• ID файлов Telegram уже отправленных постов — по зашифрованному имени поста, а не по тому, кто просил, чтобы отправить пост снова без скачивания; не реже раза в 12 часов проверяется, что пост ещё открыт, неиспользуемые удаляются через 90 дней\n• одна запись на загрузку, только время — для дневного лимита; удаляется через 2 дня\n• время каждого использования бота — чтобы считать пользователей\n• дневное число нажатий кнопок по всему боту, не по людям\n• пожертвования: сумма и ID платежа в Telegram\n• то, что бот сейчас делает с вами, пока это не закончится\n• в группе: её ID и /settings, пока бот в ней. Он ищет ссылки в каждом сообщении и ничего не хранит из остальных; скачанная ссылка считается ссылкой отправителя (запись лимита и последняя загрузка на 10 минут)\n\nВ остальном медиа не хранятся: скачиваются, отправляются и удаляются."
        ),
        "privacy_problems_heading": "Когда что-то ломается:",
        "privacy_problems": "Каждая ошибка записывается без вас: код, номер инцидента, время и версия бота.\n\n«Добавить мои данные» под ошибкой прикрепляет к этому инциденту ваш Telegram ID, @username, язык и тип чата (личный или группа) — только если нажать «Отправить». Сообщения и файлы — никогда. Это стирается через 30 дней или сразу через /deletemydata. Записи об ошибках удаляются через 180 дней.",
        "privacy_seen_by_heading": "Кто ещё это видит:",
        "privacy_seen_by": (
            "• Telegram, через который идут все сообщения\n• хостинг-провайдер и база данных бота"
        ),
        "privacy_others": (
            "• сайт по вашей ссылке и запасные сервисы загрузки, которыми пользуется бот. Они получают ссылку и видят сервер бота, а не вас; что они записывают — по их собственным правилам"
        ),
        "privacy_kept_for_heading": "Сколько это хранится:",
        "privacy_kept_for": "Настройки хранятся, пока вы их не удалите или не перестанете пользоваться ботом. Время использования удаляется примерно через три месяца. Платёжные записи и ваш ⚡ баланс хранятся дольше — на случай споров об оплате.\n\nНичего не продаётся, не используется для рекламы и не передаётся никому, кроме названных выше.",
        "privacy_your_choices": (
            "Что вы можете сделать:\n/deletemydata — удалить всё, что бот о вас хранит\n/terms — правила\n\nБлокировка бота ничего не удаляет, поэтому сначала отправьте /deletemydata."
        ),
        "terms_heading": "📜 Условия",
        "terms_use": (
            "Пользуйтесь ботом в рамках закона и правил Telegram. За травлю или обход ограничений бота аккаунт блокируется."
        ),
        "terms_specific": (
            "Загрузки: только то, что уже открыто, — это не способ попасть в закрытые аккаунты. Скачанное не становится вашим; что вы с ним делаете — дело ваше, его владельца и закона. У каждого есть дневной лимит, чтобы бот оставался доступен всем."
        ),
        "terms_money": "Деньги: /donate добровольный и помогает оплачивать хостинг. Stars также дают ⚡ кредит для ConvertBot: 2 ⚡ за ⭐, или 6 за первые 500 Stars и 4 за следующие 500; всё сверх 2 — бонус, который сгорает через 90 дней после оплаты. Платежи окончательны: Stars НЕ возвращаются, кредит НЕЛЬЗЯ вывести — кроме платежа, который не удалось превратить в кредит, он возвращается автоматически. Платежи проводит Telegram; бот не видит номер карты. Проблемы: /paysupport",
        "terms_no_warranty": (
            "Без гарантий: бота бесплатно ведёт один человек, он может тормозить, ошибаться или отключиться без предупреждения. Храните копии важного у себя."
        ),
        "policy_full_text": "Полный текст: {url}",
        "policy_contact": "Вопросы, жалобы или запрос по данным: {contact}",
        "delete_data_confirm": "⚠️ Это сотрёт всё, что бот хранит о вас. Отменить будет нельзя.",
        "delete_data_consequences": "Удалятся язык, настройки, записи о загрузках и сохранённая последняя загрузка; дневной лимит сбросится. Более старое никогда не хранилось.\n\nПлатёжные записи останутся без вашего username — на случай споров об оплате. ⚡ баланс тоже останется.",
        "delete_data_button_yes": "🗑 Стереть",
        "delete_data_button_no": "↩️ Оставить мои данные",
        "delete_data_kept": "Ничего не стёрто.",
        "delete_data_done": (
            "🗑 Готово — удалено записей: {rows}. Отправьте /start, когда захотите начать заново."
        ),
        "delete_data_failed": (
            "Не удалось удалить — что-то пошло не так с моей стороны. Попробуйте через несколько минут."
        ),
        "language_set_confirmation": "✅ Язык изменён на русский.",
        "cancel_header": "\u274c \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e:",
        "cancel_nothing": "Отменять нечего.",
        "cancel_ask": "Что остановить? Вот что я жду:",
        "cancel_kept": "Хорошо — ничего не отменено.",
        "cancel_reply_box_freed": "Поле ответа снова свободно.",
        "cancel_button_all": "❌ Всё",
        "cancel_button_none": "↩️ Ничего, продолжаем",
        "cancel_button_donation": "💸 Сумма пожертвования",
        "cancel_item_donation": "\u0441\u0443\u043c\u043c\u0430 \u043f\u043e\u0436\u0435\u0440\u0442\u0432\u043e\u0432\u0430\u043d\u0438\u044f, \u043a\u043e\u0442\u043e\u0440\u0443\u044e \u044f \u0437\u0430\u043f\u0440\u043e\u0441\u0438\u043b",
        "cancel_item_stale_prompt": "старый вопрос без ответа",
        "feedback_ask": (
            "💬 Что вы хотите сказать тому, кто ведёт этих ботов? Ответьте на это сообщение.\n\nОно уходит без вашего имени, поэтому ответить вам не смогут. Остановить: /cancel"
        ),
        "feedback_placeholder": "Ваш отзыв",
        "feedback_text_only": "Отправьте текст в ответ на это сообщение или /cancel.",
        "feedback_thanks": "✅ Спасибо — отправлено анонимно.",
        "feedback_limit": "Сегодня уже {per_day} — это дневной предел. Спасибо — напишите завтра.",
        "feedback_failed": "Не отправлено — что-то пошло не так с моей стороны. Попробуйте позже.",
        "cancel_button_feedback": "💬 Отзыв",
        "cancel_item_feedback": "отзыв, который я просил написать",
        "help_brief": (
            "Как это устроено:\n\n• Пришлите ссылку — я верну то, что по ней, команда не нужна. Instagram, TikTok, X, Pinterest и Reddit.\n• /settings: подпись, качество файлов и большие файлы (больше 48 МБ, за ⚡).\n• Идеи или проблемы: /feedback\n\nОсновные команды: /settings · /balance · /cancel\nВсе команды: полное руководство, ниже или /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Полное руководство",
        "help_brief_button": "↩️ Кратко",
        "help_page_everything": "Полное руководство: /fullhelp",
        "help_page_gone": "Этой страницы справки больше нет. Отправьте /help.",
        "help_btn_download": "🔗 Отправка ссылки",
        "helppage_download": (
            "🔗 Отправка ссылки — как устроено скачивание\n"
            "\n"
            "Пришлите ссылку на пост — команда не нужна, — и я пришлю, что в нём:\n"
            "  - Instagram: рилсы, фото, карусели целиком\n"
            "  - TikTok: видео без водяного знака, фото-слайдшоу\n"
            "  - Twitter/X: видео и фото или аккуратная картинка текстового поста\n"
            "  - Pinterest: пин в полном размере\n"
            "  - Reddit: медиа из медиапоста или картинка текстового поста\n"
            "\n"
            "{quota}\n"
            "• Загрузка засчитывается, когда начинается, даже если потом не удалась. Кнопка 📄/🖼 и кнопки 📄 PDF / 🗜 ZIP под доставкой — тоже по одной загрузке. Ту же ссылку, отправленную снова, пока я её ещё качаю, я отмечу 👌 и не засчитаю.\n"
            "• В пути одновременно может быть до {waiting} ваших ссылок; качаю я по {at_once} за раз, остальные ждут очереди.\n"
            "\n"
            "Размер: бесплатно до {free_mb} МБ. {large}\n"
            "\n"
            "Под доставкой:\n"
            "• «📄 Получить несжатым файлом» (или «🖼 Получить сжатым») присылает тот же пост ещё раз в другом качестве; каждая кнопка срабатывает один раз.\n"
            "• Под альбомом: 📄 PDF собирает его фото в один PDF, 🗜 ZIP — всё в один архив. До {merge_items} файлов и {merge_mb} МБ.\n"
            "• /settings: подпись, качество и большие файлы.\n"
            "\n"
            "Пример: пришлите ссылку TikTok вроде https://www.tiktok.com/@name/video/123 — видео придёт без водяного знака."
        ),
        "helppage_download_quota": "Бесплатно — до {per_day} загрузок за любые 24 часа и до {per_hour} за любой час. Счёт идёт назад от текущего момента и не обнуляется в полночь. Когда упрётесь в лимит, я скажу, когда освободится следующая загрузка. Каждый, кто хоть раз заплатил через /donate, любую сумму, получает навсегда {donor_day} в день и {donor_hour} в час.",
        "helppage_download_quota_off": "Бесплатно.",
        "helppage_download_large": "Больше — до {max} МБ — только если включить большие файлы в /settings, в личном чате: ⚡{first} до 100 МБ, каждые следующие 100 МБ дешевле ({max} МБ — ⚡{top}). ⚡ списывается, только когда файл уже скачан, и никогда — если не вышло.",
        "helppage_download_large_off": "Больше — не принимаю.",
        "helppage_settings": (
            "⚙️ /settings — в каком виде приходят загрузки\n"
            "\n"
            "/settings показывает ваши переключатели и как они стоят. Нажмите любой, чтобы изменить; так и останется для всех следующих загрузок.\n"
            "• Подпись (сначала вкл): строка «через @{username}» под каждой загрузкой. Ещё: /caption on или /caption off.\n"
            "• Режим без потерь (сначала выкл). Вкл: загрузки приходят файлом, ровно такими, как в источнике, без сжатия Telegram, — но не проигрываются в чате, пока вы их не откроете. Выкл: приходят фото и видео, которые играют прямо в чате, сжатые Telegram. Ещё: /lossless on или /lossless off. Только для одной загрузки — кнопка 📄/🖼 под ней.\n"
            "{large}\n"
            "Пример: отправьте /lossless on, потом ссылку на Instagram — фото придут файлами, в полном качестве."
        ),
        "helppage_settings_large": "• Большие файлы (сначала выкл). Вкл: загрузку больше {free_mb} МБ я всё равно скачаю, до {max} МБ, и пришлю файлом за ⚡ кредит — ⚡{first} до 100 МБ, ⚡{top} за {max} МБ, — списываю, только когда файл уже скачан. Выкл: всё больше {free_mb} МБ отклоняется, и ничего не списывается. Только в личном чате.",
        "privacy_feedback_heading": "Если вы отправляете /feedback:",
        "privacy_feedback": (
            "Оно приходит тому, кто ведёт ботов, с названием бота и вашим языком — без имени, username и ID. Ответить он не может. Копия хранится 30 дней."
        ),
        "start_greeting": (
            "Здравствуйте! Пришлите мне ссылку (Instagram, TikTok, Pinterest, Reddit "
            "или Twitter/X), и я скачаю это для вас.\n\n"
        ),
        "help_text": "Присылайте ссылку в любое время — команда не нужна:\n  - Instagram: reels, фото, карусели целиком\n  - TikTok: видео без водяного знака и фото-слайдшоу\n  - Twitter/X: видео и фото или картинка текстового поста\n  - Pinterest: пин в полном размере\n  - Reddit: медиа или картинка текстового поста\n\nПод альбомом: 📄 PDF собирает фото в один PDF, 🗜 ZIP — всё в один архив.\n\nКоманды:\n/settings - подпись, качество и большие файлы (больше 48 МБ, за ⚡)\n/caption on|off - подпись \"via @{username}\"\n/lossless on|off - загрузки несжатыми файлами\n/cancel - остановить то, чего я жду\n/feedback - написать тому, кто ведёт ботов (анонимно)\n/donate - помочь с хостингом (необязательно)\n/balance - ваш ⚡ кредит\n/paysupport - проблема с оплатой\n/en, /uz, /rus - сменить язык (или /language)\n/privacy - что я храню о вас\n/terms - для чего можно использовать бота\n/deletemydata - удалить всё, что я о вас храню\n/start - начать сначала\n/fullhelp - это руководство (/help - краткое)\n\n⚠️ Платежи окончательны — Stars НЕ возвращаются, ⚡ кредит НЕЛЬЗЯ вывести.\n\n",
        "caption_state_on": "ВКЛ",
        "caption_state_off": "ВЫКЛ",
        "settings_heading": "⚙️ Ваши настройки. Нажмите, чтобы изменить.",
        "caption_status": "Подпись сейчас {state}.",
        "caption_turned": "Подпись к загрузкам теперь {state}.",
        "caption_toggle_answer": "Подпись теперь {state}.",
        "lossless_state_on": "ВКЛ",
        "lossless_state_off": "ВЫКЛ",
        "lossless_status": "Режим без потерь {state}.\n\nВКЛ: загрузки приходят исходными файлами — без сжатия, но без превью в чате.\nВЫКЛ: фото и видео, которые играют в чате, со сжатием Telegram.",
        "lossless_turned": "Загрузка без потерь теперь {state}.",
        "lossless_toggle_answer": "Режим без потерь теперь {state}.",
        "large_state_on": "ВКЛ",
        "large_state_off": "ВЫКЛ",
        "large_status": "📦 Большие файлы {state}.\n\nВКЛ: загрузки больше {free} МБ, до {max} МБ, приходят файлом — ⚡{per} до 100 МБ, ⚡{top} за {max} МБ. Списывается, только когда файл пришёл.\nВЫКЛ: всё больше {free} МБ отклоняется; ничего не списывается.",
        "large_toggle_answer": "Большие файлы: {state}.",
        "large_files_turned_on": "📦 Большие файлы включены. Пришлите ссылку снова — ⚡{per} до 100 МБ, списывается, когда файл придёт. Выключить: /settings",
        "large_files_turn_on_button": "📦 Включить большие файлы",
        "large_files_off": "📦 Этот файл больше {free} МБ. Включите большие файлы кнопкой ниже (или в /settings) и пришлите ссылку снова — ⚡{per} до 100 МБ.",
        "large_files_private_only": "📦 Этот файл больше {free} МБ. Такие файлы я присылаю только в личном чате — отправьте ссылку туда.",
        "large_files_no_credit": "📦 Этот файл больше {free} МБ, это стоит минимум ⚡{per}, а на балансе ⚡{balance}. Пополните через /donate и пришлите ссылку ещё раз.",
        "large_files_fetching": "📦 Больше {free} МБ — скачиваю как большой файл (⚡{per} до 100 МБ, списывается, когда придёт).",
        "large_files_too_big": "📦 Этот файл больше {max} МБ — больше Telegram мне отправлять не позволяет. Попробуйте ролик покороче.",
        "large_files_short": "📦 Файл на {mb} МБ (⚡{price}), а на балансе ⚡{balance}, поэтому ничего не списано. Пополните через /donate и пришлите ссылку снова.",
        "large_files_sending": "📦 {mb} МБ скачано. Отправляю...",
        "large_files_send_failed": "📦 Я скачал файл, но не смог его вам отправить. ⚡{price} вернулись — на балансе ⚡{balance}.",
        "large_files_charged": "📦 {mb} МБ, отправлено. За это ⚡{price} — на балансе теперь ⚡{balance}.",
        "download_credit_caption": "⬇️ через @{username}",
        "downloading": "Скачивание...",
        "queued": "⏳ Занят другой загрузкой — ваша начнётся через мгновение.",
        "download_queue_full": 'У вас уже {count} ссылок в работе. Присылайте ещё, когда какие-то придут.',
        "download_short_on_space": 'Сейчас не хватает временного места — пришлите ссылку снова через пару минут.',
        "restarting_send_again": "🔄 Меня обновляют — отправьте это снова через несколько секунд.",
        "update_soon_try_later": "🔧 Меня сейчас обновят — попробуйте снова примерно через {minutes} мин. Я напишу, когда вернусь.",
        "update_soon_try_later_soon": "🔧 Меня обновляют — попробуйте чуть позже. Я напишу, когда вернусь.",
        "update_will_reset": "🔧 Меня сейчас обновят, и то, что вы делаете, сбросится. Начать заново можно через несколько минут.",
        "update_done_try_now": "✅ Обновление готово — попробуйте снова.",
        "gift_granted": "🎁 Нас уже больше 100 — спасибо! Вам {amount} ⚡ бесплатного кредита до {date}: это около {conversions} платных конвертаций в {convert} или больших загрузок в {download}. /balance",
        "gift_claimed": "🎁 Подарок: {amount} ⚡ бесплатного кредита — у Mumu-ботов больше {users} пользователей, спасибо!\nДействует до {date} (UTC): около {conversions} платных конвертаций в {convert} или больших загрузок в {download}.\n/balance",
        "download_failed": "Не удалось это скачать: {error}",
        "download_blocked": "🚫 Сайт меня не пустил — дело не в вашей ссылке. Попробуйте чуть позже.",
        "download_missing": "🔍 Не нашёл этот пост — возможно, он удалён или закрыт.",
        "download_too_big": '📦 Это больше, чем Telegram позволяет мне отправить. Попробуйте ролик покороче.',
        "download_all_routes_failed": "😕 Не получилось. Попробуйте через минуту — если не выходит снова, дело в сайте, а не в ссылке.",
        "fetching": "Загрузка...",
        "reddit_fetch_failed": "Не удалось получить этот пост Reddit: {error}",
        "twitter_fetch_failed_link": "Не удалось получить содержимое поста прямо сейчас — вот ссылка: {url}",
        "unrecognized_message": (
            "Такую ссылку я не знаю. Пришлите ссылку из Instagram, TikTok, Pinterest, Reddit или X, или загляните в /help."
        ),
        "redeliver_as_file": "📄 Получить несжатым файлом",
        "redeliver_as_compressed": "🖼 Получить сжатым",
        "redeliver_expired": (
            "Срок этой кнопки истёк — пришлите ссылку снова."
        ),
        "album_delivered": "Выше {count} файл(ов).",
        "merge_too_big": "📦 Слишком много для одного файла (до {items} файлов, {mb} МБ). Всё есть в чате выше.",
        "merge_picture_too_big": "📦 Одна из картинок слишком большая для PDF (больше {mp} МП). Картинки — в чате выше.",
        "merge_no_photos": "📄 Для PDF нужно хотя бы два фото. Файлы — в чате выше.",
        "merge_failed": "😕 Не удалось собрать их в один файл. Файлы выше в порядке — пришлите ссылку снова, чтобы повторить.",
        "unknown_command": "Я не знаю такую команду. Отправьте /help, чтобы увидеть, что я умею.",
        # 2.2.0: the quality picker and group chats (bot.py).
        "quality_heading": '📹 {title}',
        "quality_paid_note": '⚡ — больше {free} МБ: платно, ⚡ кредитом (/settings, большие файлы).',
        "quality_choose": 'Выберите качество ↓',
        "quality_more_button": '🎵 MP3 · ✂️ Обрезка · другое ↗️',
        "quality_expired": 'Этот выбор устарел — отправьте ссылку ещё раз.',
        "quality_picked": '⏳ {height}p — загружаю...',
        "group_hello": "👋 Привет! Отправьте сюда ссылку из Instagram, TikTok, Pinterest, Reddit или X — и я пришлю видео.\n\n/settings — что и как я скачиваю здесь.",
        "group_cant_see": '⚠️ Сейчас я вижу только сообщения, адресованные мне. Чтобы видеть ссылки участников, сделайте меня админом группы.',
        "group_settings_text": '⚙️ Что я делаю в этой группе — нажмите, чтобы изменить.\n\nКачество работает там, где сайт даёт выбор; остальные ссылки приходят такими, какими их отдаёт сайт. Файлы больше {free} МБ в группах не отправляются.',
        "group_on": '✅ Скачиваю ссылки',
        "group_paused": '⏸ На паузе — нажмите, чтобы продолжить',
        "group_other_videos": '🎬 Другие видеосайты',
        "group_quality_best": 'Лучшее',
        "group_as_video": '📹 Отправляю видео',
        "group_as_file": '📄 Отправляю файлом',
        "group_credit_on": '🏷 Подпись бота: вкл',
        "group_credit_off": '🏷 Подпись бота: выкл',
        "group_anyone": '🔓 Менять может любой',
        "group_admins_only": '🔒 Менять могут только админы',
        "group_admins_alert": 'Это могут менять только админы группы.',
        "group_saved": 'Сохранено ✅',
    },
}


# Every problem a person can run into ends with its code, and the code is what
# puts a "Report the issue" button under it -- see problems.py. Imported here,
# below the tables, because it is pure data and nothing above needs it.
import problems  # noqa: E402

_BOT = "downloader_bot"


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    template = table.get(key) or STRINGS["en"].get(key, key)
    text = template.format(**kwargs) if kwargs else template
    return text + problems.code_line(problems.code_for(_BOT, key))


async def get_lang(user_id: int, context) -> str:
    """Cached in context.user_data to avoid a DB round-trip on every handler
    call. Falls back to "en" for a user who hasn't chosen a language yet
    (only reachable outside /start's first-run gate, e.g. someone who sends
    a link before ever running /start)."""
    cached = context.user_data.get("lang")
    if cached:
        return cached
    lang = await asyncio.to_thread(db.get_user_language, user_id) or "en"
    context.user_data["lang"] = lang
    return lang


# ---------------------------------------------------------------------------
# The slash menu, in the other two languages
# ---------------------------------------------------------------------------
# English lives in BOT_COMMANDS in bot.py, where the menu can be read by
# reading the file. These are the same commands for a client whose language is
# Uzbek or Russian; shared_features.publish_commands() sends one list per
# language and Telegram picks the matching one.
#
# Deliberately absent: /language, /en, /uz and /rus. Each of the three is
# written in the language it switches TO, and /language is written in all
# three at once, because they are the way back for somebody who chose the
# wrong one. Translating them would make the menu of a Russian client offer
# three lines of Russian, one of which is the only route out.
#
# A command missing from here keeps its English description rather than
# vanishing from that language's menu -- a half-translated menu is a menu
# with commands missing, and a missing command reads as a bot that cannot do
# the thing. Kept honest by tests/test_menu.py, which fails on a command that
# has no entry here and on an entry naming a command that no longer exists.

COMMAND_MENU = {
    "uz": {
        "start": "Bu bot nima qiladi va qanday boshlash kerak",
        "settings": "Izoh va fayl sifati sozlamalari",
        "cancel": "Kutayotgan amalimni to'xtatish",
        "help": "Nimalar qila olaman",
        "feedback": "Taklif yoki muammo haqida yozish",
        "balance": "⚡ kreditingiz",
        "donate": "Server xarajatlariga hissa qo'shish",
        "paysupport": "To'lov bilan muammo",
        "privacy": "Siz haqingizda nimalarni saqlayman",
        "terms": "Botdan foydalanish shartlari",
        "deletemydata": "Siz haqingizdagi hamma narsani o'chirish",
    },
    "ru": {
        "start": "Что этот бот делает и с чего начать",
        "settings": "Подпись и качество файлов",
        "cancel": "Остановить то, чего я жду",
        "help": "Что я умею",
        "feedback": "Предложить идею или сообщить о проблеме",
        "balance": "Ваш ⚡ кредит",
        "donate": "Помочь с расходами на хостинг",
        "paysupport": "Проблема с оплатой",
        "privacy": "Что я о вас храню",
        "terms": "Для чего можно пользоваться ботом",
        "deletemydata": "Удалить всё, что я о вас храню",
    },
}

# The group-chat menu (bot.py's GROUP_COMMANDS), in the other two languages.
GROUP_COMMAND_MENU = {
    "uz": {"settings": "Bu guruhda nimani yuklayman", "help": "Guruhda qanday ishlayman"},
    "ru": {"settings": "Что я скачиваю в этой группе", "help": "Как я работаю в группе"},
}
