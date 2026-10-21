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
        "quota_hour": (
            "You've had {used} downloads in the last hour, which is this bot's limit "
            "({limit}/hour) — it's there so everyone gets a turn on a small server. "
            "The next one frees up in about {minutes} minute(s)."
        ),
        "quota_day": (
            "You've had {used} downloads today, which is this bot's daily limit "
            "({limit}) — it's there so everyone gets a turn on a small server. "
            "It frees up again in about {minutes} minute(s)."
        ),
        "quota_donor_hint": (
            "If you need more: anyone who has ever chipped in via /donate gets a much "
            "higher limit, permanently. One donation of any size is enough."
        ),
        "flood_wait": "You're going faster than I can keep up with — give it about {seconds} second(s) and carry on.",
        # ---- shared keys (same name in every bot's i18n.py) ----
        "sibling_blurb": "Also part of this bot family, see below \U0001f447",
        "donation_nudge": (
            "💙 If this bot's been useful: hosting/API costs are covered by whoever's "
            "running it, and /donate is a totally optional way to help keep it alive. "
            "No pressure either way!"
        ),
        "donate_unknown_currency": 'Unknown currency "{currency}" — try xtr or usd.',
        "donate_currency_not_configured": "{currency} donations aren't set up on this bot yet — try Stars instead.",
        "donate_invalid_amount": "That's not a valid amount — try e.g. /donate 500 or /donate 5 usd.",
        "donate_prompt": (
            "Thank you for contributing — it goes directly toward this bot's "
            "hosting and API costs. Choose an amount below, or Custom to enter "
            "your own (you can also send /donate <number> [usd] directly)."
        ),
        "donate_custom_button": "✏️ Custom {symbol}",
        "donate_too_many_stars": "That's a lot of stars! Keep it under {max} ⭐ per donation.",
        "donate_out_of_range": "{currency} donations need to be between {lo} and {hi} {symbol}.",
        "donate_invoice_title": "Contribute to hosting",
        "donate_invoice_description": "Goes towards what this bot costs to run, and adds {credited} ⚡ of credit to your balance for conversions in ConvertBot.",
        "donate_invoice_label": "Hosting contribution",
        "donate_invoice_description_fiat": 'A one-time voluntary donation towards hosting costs. Thank you!',
        "donate_prompt_credit": 'Stars you pay become ⚡ credit for conversions in ConvertBot. Your next {left} ⭐ earn {each} ⚡ each ({mult}×): {rate} ⚡ of ordinary credit plus a bonus that expires {days} days after payment. ⚡ can NOT be withdrawn or turned back into Stars.',
        "donate_prompt_credit_base": 'Stars you pay become ⚡ credit for conversions in ConvertBot, {rate} ⚡ per ⭐. ⚡ can NOT be withdrawn or turned back into Stars.',
        "donate_invoice_error": "⚠️ Telegram wouldn't create that invoice: {error}",
        "stars_unit": "Stars",
        "donate_custom_ask": "How many {unit} would you like to donate? Reply with a number.",
        "donate_invalid_amount_retry": "That's not a valid amount — send /donate to try again.",
        "donate_thanks": "🙏 Thank you for the {amount} ⭐ — genuinely appreciated!",
        "topup_thanks": "🙏 Thank you for donating {stars} ⭐ — it helps keep the bots running.\n\nAs a thank-you, you've received {total} ⚡ of credit in {convert_bot} to use on file conversions. Your credit there: {balance} ⚡.",
        "topup_thanks_bonus": 'Of that, {bonus} ⚡ is bonus credit and expires on {date}.',
        "credit_cannot_be_withdrawn": '⚡ is credit for conversions in ConvertBot, and can NOT be withdrawn or turned back into Stars. A problem with a payment? /paysupport',
        "paysupport_text": '💳 Help with a payment\n\nPayments are FINAL: Stars are NOT refunded, and ⚡ credit can NOT be withdrawn or turned back into Stars.\n\nIf a payment went wrong — you were charged and no credit arrived, or you were charged twice — write to {contact} with the date, the amount and your Telegram id, {user_id}. It will be checked and put right with credit.\n\n/balance lists every payment and what it added.',
        "report_button": '🐞 Add my details',
        "problem_logged_note": "This is already written down for the bot's owner — the code, the time and the version, and nothing about you.",
        "report_disclaimer": "📨 Add your own details to this problem?\n\nAlready recorded, with you left out of it: the bot's name, the error code {code}, the incident number {incident}, when it happened and the bot's version.\n\nTapping Send adds four things: your Telegram user ID, your @username if you have one, the language you picked, and whether this chat is private or a group. Not what you wrote, and not the file you sent.\n\nIt lets your case be found in the logs, which is usually what makes a rare problem fixable. Entirely your choice.",
        "report_send": '📨 Send my details',
        "report_cancel": '✖️ Cancel',
        "report_sent": '✅ Thank you — your details are on incident {incident} now, which is what makes this one findable.',
        "report_already": 'Your details are already on incident {incident}. Nothing more was sent.',
        "report_cancelled": 'Cancelled — nothing about you was sent. The problem itself stays logged.',
        "report_failed": "⚠️ The report couldn't be sent right now. Please try again later.",
        "report_invalid": 'This button no longer works.',
        "crash_notice": "⚠️ Something went wrong on the bot's side while handling that, so it wasn't done. Please try again in a moment.",
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
            "• your Telegram user id, your language, and your caption and quality settings\n"
            "• the links you send: each one while it is fetched, then the last five, so the "
            "buttons under those downloads work. They are dropped after 12 hours without using "
            "the bot\n"
            "• what it has just sent you, for 5 minutes, so the button under it and the same "
            "link sent again are answered without fetching it twice. Sending a different link, "
            "/deletemydata or a restart deletes it sooner. All of it is deleted no later than 12 "
            "hours after the download\n"
            "• references to files the bot has already sent (Telegram's file IDs), filed under a "
            "scrambled name of the post and not under who asked for it. They let the bot send the "
            "same post again without downloading it. Before re-using one, the bot checks at least "
            "every 12 hours that the post is still public. References nobody uses are deleted "
            "after 90 days\n"
            "• one row per download — which platform and when, which is what the daily allowance "
            "counts\n"
            "• a timestamp each time you use the bot, so its owner can tell whether anyone is "
            "using it\n"
            "• how many times each button is tapped, counted per day for the whole bot, with nothing about who tapped it\n"
            "• a record of any donation: the amount and Telegram's payment id\n"
            "• whatever the bot is in the middle of doing with you, until it is finished\n"
            "\n"
            "Apart from those 5 minutes the media itself is not kept. It is fetched, sent to you, "
            "and deleted."
        ),
        "privacy_problems_heading": "When something goes wrong:",
        "privacy_problems": (
            "Every error this bot shows anybody is written down by itself: the error code, an "
            "incident number, when it happened and the bot's version. Nothing in that is about "
            "you — not your id, not your name, not what you sent. A fault nobody reports is "
            "still a fault, which is why it does not wait to be asked.\n"
            "\n"
            "\"Add my details\" under an error offers to attach four things to that one "
            "incident: your Telegram user id, your @username if you have one, the language you "
            "picked, and whether the chat is private or a group. All four are named on screen "
            "first, and nothing is sent unless you tap Send. What you wrote and the files you "
            "sent are never part of it.\n"
            "\n"
            "Those four are cleared after 30 days on their own, and /deletemydata clears them "
            "straight away. The record of the error stays — it still happened — it just stops "
            "saying who hit it. The records themselves are deleted after 180 days."
        ),
        "privacy_seen_by_heading": "Who else sees it:",
        "privacy_seen_by": (
            "• Telegram, which carries every message both ways and sets its own terms\n"
            "• the hosting provider this bot runs on, and the database it writes to"
        ),
        "privacy_others": (
            "• the site you linked to, and the download services this bot falls back on when a "
            "site will not answer it directly. They get the link and see a request from this "
            "bot's server rather than from you; what they log is their business, under their own "
            "policies and not this one."
        ),
        "privacy_kept_for_heading": "How long it stays:",
        "privacy_kept_for": 'Settings and anything the bot is holding for you stay until you erase them or stop using it. Counted use is dropped after about three months. Payment records and your ⚡ balance are kept longer, because a dispute about a payment is settled against them.\n\nNothing here is sold, rented or used for advertising, and nothing goes to anyone not named above.',
        "privacy_your_choices": (
            "What you can do:\n"
            "/deletemydata — erase what this bot holds on you\n"
            "/terms — what the bot may be used for\n"
            "\n"
            "Blocking the bot in Telegram stops it talking to you but erases nothing, so send "
            "/deletemydata first if you want both."
        ),
        "terms_heading": "📜 Terms",
        "terms_use": (
            "Use it for what it is for, within the law and within Telegram's own terms. Do not "
            "use it to harass anyone, and do not drive it past the limits it sets — an account "
            "doing either is blocked."
        ),
        "terms_specific": (
            "Downloads: this fetches what is already public and hands it to you. It is not a way "
            "around a private account and does not try to be one. What you do afterwards with "
            "someone else's photo, video or post is between you, them and the law — downloading "
            "something does not make it yours. There is a daily allowance per person, so that one "
            "person cannot make the bot unusable for everybody else."
        ),
        "terms_money": 'Money: /donate is voluntary and goes towards what the bots cost to run. A payment in Stars also adds ⚡ credit for conversions in ConvertBot: 2 ⚡ per ⭐, or 6 for your first 500 Stars ever and 4 for the next 500. The part above 2 is bonus credit and expires 90 days after the payment. Payments are FINAL: Stars are NOT refunded, and credit can NOT be withdrawn or turned back into Stars. Telegram handles every payment and the bot never sees a card number. A payment that went wrong: /paysupport.',
        "terms_no_warranty": (
            "No promises: one person runs this, it is free, and it can be slow, wrong, or off "
            "entirely without warning. Keep your own copy of anything that matters."
        ),
        "policy_full_text": "Full text: {url}",
        "policy_contact": "Questions, complaints or a data request: {contact}",
        "delete_data_confirm": "⚠️ This erases what this bot holds on you. There is no undo.",
        "delete_data_consequences": 'Your language, your caption and quality settings, and every record of what you downloaded and when all go. Your daily allowance resets with them. It also deletes the last download if it is still being held; nothing older was ever kept here.\n\nDonation records stay, without your username, because a dispute about a payment is settled against them. Your ⚡ balance stays too, and is still yours if you come back.',
        "delete_data_button_yes": "🗑 Erase it",
        "delete_data_button_no": "↩️ Keep my data",
        "delete_data_kept": "Nothing was erased.",
        "delete_data_done": (
            "🗑 Done — {rows} record(s) erased.\n"
            "\n"
            "Send /start whenever you like; the bot will treat you as new."
        ),
        "delete_data_failed": (
            "Couldn't erase that just now — something went wrong at my end. Please try again in "
            "a few minutes."
        ),
        "language_set_confirmation": "✅ Language set to English.",
        "cancel_header": "\u274c Cancelled:",
        "cancel_nothing": "Nothing to cancel — I wasn't waiting on anything from you.",
        "cancel_ask": "What should I stop? Here's what I'm waiting on:",
        "cancel_kept": "Alright — nothing cancelled.",
        "cancel_reply_box_freed": "Your reply box is free again.",
        "cancel_button_all": "❌ All of it",
        "cancel_button_none": "↩️ Nothing, keep going",
        "cancel_button_donation": "💸 Donation amount",
        "cancel_item_donation": "the donation amount I asked you for",
        "cancel_item_stale_prompt": "a leftover prompt that was still waiting on an answer",
        "feedback_ask": (
            "💬 What would you like to tell the person who runs these bots? A suggestion, a "
            "problem, anything — write it in one message, as a reply to this one.\n"
            "\n"
            "Only they read it, and they can't write back: it is sent without your name or id. "
            "/cancel to stop."
        ),
        "feedback_placeholder": "Your feedback",
        "feedback_text_only": "That one has to be in words — write your feedback as a text reply to this message, or send /cancel.",
        "feedback_thanks": "✅ Thank you — sent. Only the person who runs these bots reads it, and they can't reply: nothing in it says who you are.",
        "feedback_limit": "You've sent {per_day} messages with /feedback today, which is as many as I take in a day. Thank you — write again tomorrow.",
        "feedback_failed": "Your feedback wasn't sent — something went wrong on my side. Please try again in a few minutes.",
        "cancel_button_feedback": "💬 Feedback",
        "cancel_item_feedback": "the feedback I asked you for",
        "help_brief": (
            "How it works:\n"
            "\n"
            "• Paste a link and I send back what's behind it — no command needed. Instagram, TikTok, X, Pinterest and Reddit.\n"
            "• /settings: the credit caption, the file quality, and large files over 48 MB, which cost ⚡ credit and only once you switch them on.\n"
            "• Ideas or problems: /feedback — only the person who runs these bots reads it, and they can't reply.\n"
            "\n"
            "Main commands: /settings · /balance · /cancel\n"
            "Every command: the full guide, below or /fullhelp.\n"
            "\n"
        ),
        "help_full_button": "📖 Full guide",
        "help_brief_button": "↩️ Short version",
        "help_page_everything": "The full guide: /fullhelp",
        "help_page_gone": "That help page no longer exists. Send /help to see the current ones.",
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
            "What you write reaches the person who runs these bots as a Telegram message, with "
            "which bot it came from and your language — not your name, your username or your id. "
            "Only they read it, and they cannot reply to it. A copy is kept in the bots' "
            "database for 30 days and then deleted."
        ),
        # ---- downloader_bot-specific keys ----
        "start_greeting": (
            "Hey! Send me a link (Instagram, TikTok, Pinterest, Reddit, or "
            "Twitter/X) and I'll grab it for you.\n\n"
        ),
        "help_text": 'Paste a link any time and I\'ll grab it — no command needed:\n  - Instagram: reels, photos, whole carousels\n  - TikTok: videos without the watermark, and photo slideshows\n  - Twitter/X: video and photos, or a clean image card for a text post\n  - Pinterest: the pin, at full size\n  - Reddit: the media if it\'s a media post, or a card if it\'s text\n\nUnder an album I add two buttons: 📄 PDF puts its photos into one PDF, 🗜 ZIP puts everything into one archive.\n\nCommands:\n/settings - the caption, the file quality, and large files (over 48 MB, for ⚡), on one screen\n/caption on|off - the "via @{username}" credit caption, on its own\n/lossless on|off - get downloads as uncompressed files\n/donate - chip in for hosting costs (totally optional)\n/cancel - stop something I\'m waiting on you for (I\'ll ask which)\n/feedback - suggest something or report a problem; only whoever runs these bots reads it, and they cannot reply\n/en, /uz, /rus - switch language (or /language, which asks)\n/balance - your ⚡ credit\n/paysupport - trouble with a payment\n/privacy - what I keep about you\n/terms - what I may be used for\n/deletemydata - delete everything I hold on you\n/start - what I do, and how to start\n/fullhelp - this full guide (/help - the short one)\n\n⚠️ NOTE: Payments are final — Stars paid through /donate are NOT refunded, and the ⚡ credit they add can NOT be withdrawn.\n\n',
        "caption_state_on": "ON",
        "caption_state_off": "OFF",
        "settings_heading": "⚙️ Your settings. Tap to change any of them.",
        "caption_status": "Caption is currently {state}.",
        "caption_turned": "Download caption turned {state}.",
        "caption_toggle_answer": "Caption turned {state}.",
        "lossless_state_on": "ON",
        "lossless_state_off": "OFF",
        "lossless_status": "Lossless is currently {state}.\n\nON: downloads arrive as files, exactly as the source had them — no Telegram re-compression, but they don't play or preview in the chat until you open them.\nOFF: downloads arrive as photos and videos that play inline, compressed by Telegram.",
        "lossless_turned": "Lossless downloads turned {state}.",
        "lossless_toggle_answer": "Lossless turned {state}.",
        "large_state_on": "ON",
        "large_state_off": "OFF",
        "large_status": "📦 Large files are {state}.\n\nON: a download over {free} MB is still fetched, up to {max} MB, and sent as a file — ⚡{per} up to 100 MB, less for each 100 MB after (⚡{top} for {max} MB), charged only once it has arrived and never if it fails.\nOFF: anything over {free} MB is refused, and nothing is charged.",
        "large_toggle_answer": "Large files: {state}.",
        "large_files_turned_on": "📦 Large files are on. Send the link again and I'll get it — ⚡{per} up to 100 MB, less for each 100 MB after, charged once it has arrived. /settings turns it off again.",
        "large_files_turn_on_button": "📦 Turn on large files",
        "large_files_off": "📦 This one is bigger than {free} MB. I can still get it for you, as a file — ⚡{per} up to 100 MB, less for each 100 MB after — but only once you've switched on large files. Tap below (or /settings), then send the link again.",
        "large_files_private_only": "📦 This one is bigger than {free} MB. Files that big only come in a private chat with me — send me the link there.",
        "large_files_no_credit": "📦 This one is bigger than {free} MB, which costs at least ⚡{per}, and your balance is ⚡{balance}. Top up with /donate, then send the link again.",
        "large_files_fetching": "📦 Bigger than {free} MB — fetching it as a large file. ⚡{per} up to 100 MB, less for each 100 MB after, charged once it has arrived.",
        "large_files_too_big": "📦 This one is bigger than {max} MB, the most Telegram lets me send. Try a shorter clip.",
        "large_files_short": "📦 It came to {mb} MB, which costs ⚡{price}, and your balance is ⚡{balance} — so nothing was charged. Top up with /donate and send the link again.",
        "large_files_sending": "📦 {mb} MB is here. Sending it...",
        "large_files_send_failed": "📦 I got it, but couldn't send it to you. The ⚡{price} is back — your balance is ⚡{balance}.",
        "large_files_charged": "📦 {mb} MB, sent. ⚡{price} for it — your balance is now ⚡{balance}.",
        "download_credit_caption": "⬇️ via @{username}",
        "downloading": "Downloading...",
        "queued": "⏳ Busy with another download — yours starts in a moment.",
        "download_queue_full": 'You already have {count} links on the way. Send more when some of them arrive.',
        "download_short_on_space": "I'm short on temporary space right now — send that link again in a few minutes.",
        "restarting_send_again": "🔄 I'm being updated right now — give me a few seconds and send that again.",
        "update_soon_try_later": "🔧 I'm being updated in a moment, so I can't start anything new right now — please try again in about {minutes} minute(s). I'll message you when I'm back.",
        "update_soon_try_later_soon": "🔧 I'm being updated right now, so I can't start anything new — please try again shortly. I'll message you when I'm back.",
        "update_will_reset": "🔧 Heads up: I'm about to be updated, and what you have going right now will be reset. You'll be able to start it again in a few minutes.",
        "update_done_try_now": '✅ The update is done — go ahead and try again now.',
        "gift_granted": '🎁 We have passed 100 users, thank you! You get {amount} ⚡ of free credit, good until {date}. It pays for about {conversions} paid conversions in {convert} or big-file downloads in {download}. Your balance: /balance',
        "gift_claimed": "🎁 Gift received: {amount} ⚡ of free credit! Mumu bots have passed {users} users, and this is our thank-you.\nIt is good until {date} (UTC) and pays for about {conversions} paid conversions in {convert} or big-file downloads in {download}.\nYour balance: /balance",
        "download_failed": "Couldn't download that: {error}",
        "download_blocked": "🚫 I tried every way I have of getting that one and the site turned all of them away. That is about where the request came from, not about your link. Try again in a bit — this usually sorts itself out.",
        "download_missing": "🔍 I couldn't find that post. Usually that means it was deleted, or it is from a private account. Public posts still work.",
        "download_too_big": "📦 That one is bigger than Telegram lets me send. Try a shorter clip.",
        "download_all_routes_failed": "😕 That one didn't work, and I tried every way I know. Give it a minute and send it again — if it keeps failing, it is the site, not the link.",
        "fetching": "Fetching...",
        "reddit_fetch_failed": "Couldn't fetch that Reddit post: {error}",
        "twitter_fetch_failed_link": "Couldn't fetch that post's content right now — here's the link: {url}",
        "unrecognized_message": (
            "That doesn't look like a link I recognize — Instagram, TikTok, "
            "Pinterest, Reddit, or Twitter/X — paste one to download it, or "
            "/help for commands."
        ),
        "redeliver_as_file": "📄 Get it as an uncompressed file",
        "redeliver_as_compressed": "🖼 Get it compressed instead",
        "redeliver_expired": (
            "That one is too old for the button now — send the link again, or use "
            "/lossless to change how everything arrives."
        ),
        "album_delivered": "{count} files above.",
        "merge_too_big": "📦 That's too much to pack into one file — I can do up to {items} files and {mb} MB in all. Everything is still in the chat above.",
        "merge_picture_too_big": "📦 One of the pictures is too big for a PDF (over {mp} megapixels), so there's no PDF this time. The pictures are in the chat above.",
        "merge_no_photos": "📄 A PDF needs at least two photos, and I found fewer than that in the post this time. Send the link again, or take the files from the chat above.",
        "merge_failed": "😕 Something went wrong while I was putting them into one file, so there's no PDF or ZIP this time. The files above are fine — send the link again to retry.",
        "unknown_command": "I don't recognize that command. Send /help to see what I can do.",
    },
    "uz": {
        "quota_hour": "So'nggi bir soatda {used} ta fayl yuklab oldingiz — bu soatlik limit ({limit} ta). Kichik serverda hammaga navbat yetishi uchun shunday. Keyingisi taxminan {minutes} daqiqadan keyin mumkin bo'ladi.",
        "quota_day": 'Bugun {used} ta fayl yuklab oldingiz — bu kunlik limit ({limit} ta). Kichik serverda hammaga navbat yetishi uchun shunday. Taxminan {minutes} daqiqadan keyin yana yuklab olishingiz mumkin.',
        "quota_donor_hint": "Ko'proq kerakmi? /donate orqali bir marta bo'lsa ham hissa qo'shganlar uchun limit butunlay ancha yuqori bo'ladi. Miqdori ahamiyatsiz.",
        "flood_wait": 'Juda tez yuboryapsiz — {seconds} soniyacha kutib, keyin davom eting.',
        "sibling_blurb": 'Oilamizdagi boshqa botlar pastda 👇',
        "donation_nudge": "💙 Bot sizga foydali bo'lgan bo'lsa: server va API xarajatlarini botni yuritayotgan odam o'z hisobidan qoplaydi. /donate orqali bunga hissa qo'shishingiz mumkin — bu mutlaqo ixtiyoriy.",
        "donate_unknown_currency": '"{currency}" degan valyuta yo\'q — xtr yoki usd deb yozing.',
        "donate_currency_not_configured": "Bu botda hozircha {currency} bilan xayriya qilib bo'lmaydi — Stars'dan foydalaning.",
        "donate_invalid_amount": "Miqdor noto'g'ri — masalan, /donate 500 yoki /donate 5 usd deb yozing.",
        "donate_prompt": "Hissangiz uchun rahmat — u to'g'ridan-to'g'ri botning server va API xarajatlariga ketadi. Quyidagi miqdorlardan birini tanlang yoki o'zingiz yozish uchun «Boshqa»ni bosing (/donate <son> [usd] deb ham yuborishingiz mumkin).",
        "donate_custom_button": "✏️ Boshqa {symbol}",
        "donate_too_many_stars": "Bu juda ko'p! Bir martalik xayriya {max} ⭐ dan oshmasin.",
        "donate_out_of_range": "{currency} bilan xayriya {lo} dan {hi} {symbol} gacha bo'lishi kerak.",
        "donate_invoice_title": 'Server xarajatlariga hissa',
        "donate_invoice_description": "Botlar xarajatlariga ketadi va ConvertBot'dagi konvertatsiyalar uchun balansingizga {credited} ⚡ kredit qo'shadi.",
        "donate_invoice_label": 'Xayriya',
        "donate_invoice_description_fiat": 'Server xarajatlari uchun bir martalik ixtiyoriy xayriya. Rahmat!',
        "donate_prompt_credit": "To'lagan Stars'ingiz ConvertBot'da konvertatsiyalarga sarflanadigan ⚡ kreditga aylanadi. Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×): {rate} ⚡ oddiy kredit, qolgani esa to'lovdan {days} kun o'tgach muddati tugaydigan bonus. ⚡ ni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI.",
        "donate_prompt_credit_base": "To'lagan har bir ⭐ ConvertBot'da konvertatsiyalarga sarflanadigan {rate} ⚡ kreditga aylanadi. ⚡ ni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI.",
        "donate_invoice_error": "⚠️ Telegram to'lov hisobini yaratmadi: {error}",
        "stars_unit": "Stars (yulduzcha)",
        "donate_custom_ask": 'Qancha {unit} xayriya qilmoqchisiz? Faqat sonni yozib yuboring.',
        "donate_invalid_amount_retry": "Miqdor noto'g'ri — qaytadan urinish uchun /donate yuboring.",
        "donate_thanks": '🙏 {amount} ⭐ uchun katta rahmat!',
        "topup_thanks": "🙏 {stars} ⭐ xayriyangiz uchun rahmat — bu botlarning ishlashiga yordam beradi.\n\nMinnatdorchilik sifatida {convert_bot} botida fayllarni o'girish uchun {total} ⚡ kredit oldingiz. U yerdagi balansingiz: {balance} ⚡.",
        "topup_thanks_bonus": 'Shundan {bonus} ⚡ — bonus kredit, uning muddati {date} kuni tugaydi.',
        "credit_cannot_be_withdrawn": "⚡ — ConvertBot'dagi konvertatsiyalar uchun kredit: uni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. To'lovda muammo bo'lsa: /paysupport",
        "paysupport_text": "💳 To'lov bo'yicha yordam\n\nTo'lovlar QAYTARILMAYDI: Stars qaytarib berilmaydi, ⚡ kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI.\n\nTo'lovda xatolik bo'lgan bo'lsa — pul yechilgan-u, kredit tushmagan bo'lsa yoki ikki marta yechilgan bo'lsa — {contact} manziliga to'lov sanasi, miqdori va Telegram ID raqamingizni ({user_id}) yozib yuboring. Tekshirilib, kredit bilan to'g'rilab beriladi.\n\n/balance har bir to'lovni va u qancha kredit qo'shganini ko'rsatadi.",
        "report_button": "🐞 Ma'lumotlarimni qo'shish",
        "problem_logged_note": "Bu muammo bot egasi uchun allaqachon yozib olindi — kodi, vaqti va bot versiyasi. Siz haqingizda hech narsa yo'q.",
        "report_disclaimer": "📨 Bu muammoga o'zingiz haqingizdagi ma'lumotni ham qo'shasizmi?\n\nSizsiz yozib olingani: bot nomi, {code} xato kodi, {incident} hodisa raqami, qachon yuz bergani va bot versiyasi.\n\n\"Yuborish\" tugmasi to'rt narsani qo'shadi: Telegram ID raqamingiz, bo'lsa @username'ingiz, siz tanlagan til va bu suhbat shaxsiymi yoki guruhmi. Yozganingiz ham, yuborgan faylingiz ham emas.\n\nShunda sizning holatingizni loglardan topib bo'ladi — kam uchraydigan muammoni odatda shu tuzatib beradi. To'liq o'zingiz hal qilasiz.",
        "report_send": "📨 Ma'lumotlarimni yuborish",
        "report_cancel": '✖️ Bekor qilish',
        "report_sent": "✅ Rahmat — ma'lumotlaringiz {incident} hodisasiga biriktirildi, endi uni topish oson.",
        "report_already": "Ma'lumotlaringiz {incident} hodisasiga allaqachon biriktirilgan. Boshqa hech narsa yuborilmadi.",
        "report_cancelled": "Bekor qilindi — siz haqingizda hech narsa yuborilmadi. Muammoning o'zi yozib olingan holicha qoladi.",
        "report_failed": "⚠️ Hozir xabarni yuborib bo'lmadi. Keyinroq qayta urinib ko'ring.",
        "report_invalid": 'Bu tugma endi ishlamaydi.',
        "crash_notice": "⚠️ Buni bajarishda bot tomonida xatolik yuz berdi, shuning uchun amal bajarilmadi. Birozdan keyin qayta urinib ko'ring.",
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
        "privacy_stored": "• Telegram ID raqamingiz, tilingiz, izoh va sifat sozlamalaringiz\n• siz yuborgan havolalar: har biri u bo'yicha fayl olinayotgan paytda, keyin esa oxirgi beshtasi — o'sha yuklamalar ostidagi tugmalar ishlashi uchun. Botdan 12 soat foydalanmasangiz, ular o'chiriladi\n• bot sizga hozirgina yuborgan fayl — 5 daqiqa davomida, uning ostidagi tugma va qayta yuborilgan o'sha havola faylni qaytadan yuklamasdan javob berishi uchun. Boshqa havola yuborsangiz, /deletemydata buyrug'ini bersangiz yoki bot qayta ishga tushsa, u oldinroq o'chiriladi. Bularning hammasi yuklab olinganidan keyin 12 soatdan kechiktirmay o'chiriladi\n• bot avval yuborgan fayllarning Telegram fayl ID'lari — postning qaytarib bo'lmaydigan kodi ostida saqlanadi, kim so'ragani esa saqlanmaydi. Ular yordamida bot o'sha postni qayta yuklab olmasdan yana yubora oladi. Fayl ID'sidan qayta foydalanishdan oldin bot post hali ham ochiq ekanini kamida har 12 soatda tekshiradi. Hech kim foydalanmagan fayl ID'lari 90 kundan keyin o'chiriladi\n• har bir yuklab olish uchun bitta yozuv: qaysi platformadan va qachon; kunlik limit shu asosida hisoblanadi\n• botdan foydalangan vaqtingiz — bot egasi botdan umuman foydalanilayotganini bilishi uchun\n• har bir tugma necha marta bosilgani — butun bot bo'yicha kunlik umumiy hisob, kim bosgani haqida hech qanday ma'lumotsiz\n• xayriya qilsangiz, uning yozuvi: miqdori va Telegram to'lov raqami\n• bot siz uchun bajarayotgan ish — u tugagunicha\n\nShu 5 daqiqadan tashqari faylning o'zi saqlanmaydi: u olinadi, sizga yuboriladi va o'chiriladi.",
        "privacy_problems_heading": "Nimadir noto'g'ri ketganda:",
        "privacy_problems": "Bot kimgadir xato ko'rsatsa, uni o'zi yozib qo'yadi: xato kodi, hodisa raqami, qachon yuz bergani va bot versiyasi. Bularda siz haqingizda hech narsa yo'q — ID raqamingiz ham, ismingiz ham, nima yuborganingiz ham. Hech kim xabar bermagan nosozlik ham nosozligicha qoladi, shuning uchun bot so'ralishini kutmaydi.\n\nXato ostidagi \"Ma'lumotlarimni qo'shish\" tugmasi o'sha bitta hodisaga to'rt narsani biriktirishni taklif qiladi: Telegram ID raqamingiz, bo'lsa @username'ingiz, siz tanlagan til va bu suhbat shaxsiymi yoki guruhmi. To'rttasi ham avval ekranda aytiladi va \"Yuborish\"ni bosmasangiz hech narsa yuborilmaydi. Yozganlaringiz va yuborgan fayllaringiz esa bunga hech qachon kirmaydi.\n\nBu to'rttasi 30 kundan keyin o'z-o'zidan o'chiriladi, /deletemydata esa darhol o'chiradi. Xatolik yozuvining o'zi qoladi — u haqiqatan yuz bergan — shunchaki endi kim duch kelgani yozilmaydi. Yozuvlarning o'zi 180 kundan keyin o'chiriladi.",
        "privacy_seen_by_heading": "Yana kim ko'ra oladi:",
        "privacy_seen_by": "• Telegram — barcha xabarlar u orqali o'tadi va u o'z qoidalari asosida ishlaydi\n• bot joylashgan hosting va bot foydalanadigan ma'lumotlar bazasi",
        "privacy_others": "• siz havolasini yuborgan sayt, hamda sayt to'g'ridan-to'g'ri javob bermaganda bot murojaat qiladigan yuklab olish xizmatlari. Ular havolani oladi va so'rov sizdan emas, bot serveridan kelganini ko'radi; ular nimani qayd etishi o'z qoidalariga bog'liq, bu botning qoidalariga emas.",
        "privacy_kept_for_heading": "Qancha vaqt saqlanadi:",
        "privacy_kept_for": "Sozlamalaringiz va bot siz uchun saqlab turgan narsalar ularni o'chirmaguningizcha yoki botdan foydalanishni to'xtatmaguningizcha turadi. Foydalanish qaydlari taxminan uch oydan keyin o'chiriladi. To'lov yozuvlari va ⚡ balansingiz esa uzoqroq saqlanadi, chunki to'lov bo'yicha nizolar ular asosida hal qilinadi.\n\nBu ma'lumotlar sotilmaydi, ijaraga berilmaydi, reklamada ishlatilmaydi va yuqorida aytilganlardan boshqa hech kimga berilmaydi.",
        "privacy_your_choices": "Nima qilishingiz mumkin:\n/deletemydata — bot siz haqingizda saqlagan ma'lumotlarni o'chirish\n/terms — botdan foydalanish shartlari\n\nBotni Telegramda bloklasangiz, u sizga yozmay qo'yadi, lekin hech narsa o'chmaydi. Ikkalasini ham xohlasangiz, avval /deletemydata yuboring.",
        "terms_heading": "📜 Shartlar",
        "terms_use": "Botdan maqsadiga ko'ra, qonun va Telegram qoidalari doirasida foydalaning. Uni boshqalarni bezovta qilish uchun ishlatmang va belgilangan cheklovlardan oshirib yuklamang — aks holda akkaunt bloklanadi.",
        "terms_specific": "Yuklab olish haqida: bot faqat hammaga ochiq bo'lgan narsani olib beradi. Yopiq akkauntlarni chetlab o'tmaydi va bunga urinmaydi ham. Birovning surati, videosi yoki posti bilan keyin nima qilishingiz — siz, o'sha odam va qonun o'rtasidagi masala; yuklab olganingiz uni sizniki qilmaydi. Bitta odam botni boshqalar uchun band qilib qo'ymasligi uchun har bir kishiga kunlik limit bor.",
        "terms_money": "Pul haqida: /donate — ixtiyoriy, mablag' botlar xarajatlariga ketadi. Stars'dagi to'lov ConvertBot'da konvertatsiyalar uchun ⚡ kredit ham beradi: har ⭐ uchun 2 ⚡, umumiy hisobda birinchi 500 ta Stars uchun esa 6 ⚡ dan, keyingi 500 tasi uchun 4 ⚡ dan. 2 ⚡ dan ortig'i bonus kredit bo'lib, to'lovdan 90 kun o'tgach muddati tugaydi. To'lovlar QAYTARILMAYDI: Stars qaytarib berilmaydi, kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. Har bir to'lovni Telegram o'tkazadi, bot karta raqamingizni ko'rmaydi. To'lovda muammo bo'lsa: /paysupport.",
        "terms_no_warranty": "Kafolat yo'q: botni bir kishi yuritadi va u oldindan ogohlantirmasdan sekinlashishi, xato qilishi yoki butunlay to'xtab qolishi mumkin. Siz uchun muhim narsalarning nusxasini o'zingizda saqlang.",
        "policy_full_text": "To'liq matn: {url}",
        "policy_contact": "Savol, shikoyat yoki ma'lumot so'rovi uchun: {contact}",
        "delete_data_confirm": "⚠️ Bot siz haqingizda saqlagan ma'lumotlar o'chiriladi. Buni ortga qaytarib bo'lmaydi.",
        "delete_data_consequences": "Til, izoh va sifat sozlamalaringiz hamda nimani qachon yuklab olganingiz haqidagi qaydlar o'chiriladi. Kunlik limitingiz ham yangilanadi. Oxirgi yuklama hali saqlanayotgan bo'lsa, u ham o'chiriladi; undan oldingilari bu yerda umuman saqlanmagan.\n\nXayriya yozuvlari foydalanuvchi nomingizsiz saqlanib qoladi, chunki to'lov bo'yicha nizolar ular asosida hal qilinadi. ⚡ balansingiz ham saqlanadi — qaytib kelsangiz, u o'z joyida bo'ladi.",
        "delete_data_button_yes": "🗑 O'chirilsin",
        "delete_data_button_no": "↩️ Ma'lumotlarim qolsin",
        "delete_data_kept": "Hech narsa o'chirilmadi.",
        "delete_data_done": "🗑 Bajarildi — {rows} ta yozuv o'chirildi.\n\nIstalgan payt /start yuborsangiz, bot sizni yangi foydalanuvchi sifatida kutib oladi.",
        "delete_data_failed": "Hozir o'chirib bo'lmadi — bot tomonida xatolik yuz berdi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "language_set_confirmation": "✅ Til o'zbekchaga o'zgartirildi.",
        "cancel_header": "\u274c Bekor qilindi:",
        "cancel_nothing": "Bekor qilinadigan amal yo'q — hozir hech narsa kutilmayapti.",
        "cancel_ask": 'Qaysi birini bekor qilay? Hozir quyidagilar kutilmoqda:',
        "cancel_kept": "Yaxshi — hech narsa bekor qilinmadi.",
        "cancel_reply_box_freed": "Xabar yozish maydoni endi bo'sh.",
        "cancel_button_all": "❌ Hammasini",
        "cancel_button_none": '↩️ Hech birini, davom etamiz',
        "cancel_button_donation": "💸 Xayriya miqdori",
        "cancel_item_donation": 'kiritilishi kutilayotgan xayriya miqdori',
        "cancel_item_stale_prompt": "javobsiz qolgan eski so'rov",
        "feedback_ask": (
            "💬 Bu botlarni yuritayotgan odamga nima demoqchisiz? Taklif, muammo — nima bo'lsa "
            "ham, bitta xabarda, shu xabarga javob qilib yozing.\n"
            "\n"
            "Uni faqat o'sha odam o'qiydi va sizga javob yoza olmaydi: xabar ismingiz va ID "
            "raqamingizsiz yuboriladi. To'xtatish: /cancel"
        ),
        "feedback_placeholder": "Fikringiz",
        "feedback_text_only": "Bu yerga faqat matn qabul qilinadi — fikringizni shu xabarga javob qilib so'z bilan yozing yoki /cancel yuboring.",
        "feedback_thanks": "✅ Rahmat — yuborildi. Uni faqat bu botlarni yuritayotgan odam o'qiydi va javob yoza olmaydi: xabarda kimligingiz ko'rsatilmagan.",
        "feedback_limit": "Bugun /feedback orqali {per_day} ta xabar yubordingiz — bir kunda shuncha qabul qilaman. Rahmat, ertaga yana yozishingiz mumkin.",
        "feedback_failed": "Fikringiz yuborilmadi — bot tomonida nimadir buzildi. Iltimos, bir necha daqiqadan keyin qayta urinib ko'ring.",
        "cancel_button_feedback": "💬 Fikr-mulohaza",
        "cancel_item_feedback": "men so'ragan fikr-mulohaza",
        "help_brief": (
            "Bot qanday ishlaydi:\n"
            "\n"
            "• Havola yuboring — ortidagi faylni yuboraman, buyruq kerak emas. Instagram, TikTok, X, Pinterest va Reddit.\n"
            "• /settings: «via» izohi, fayl sifati va 48 MB dan katta fayllar — ular ⚡ kredit turadi va faqat o'zingiz yoqsangiz ishlaydi.\n"
            "• Taklif yoki muammo: /feedback — uni faqat bu botlarni yuritayotgan odam o'qiydi, javob qaytmaydi.\n"
            "\n"
            "Asosiy buyruqlar: /settings · /balance · /cancel\n"
            "Barcha buyruqlar: to'liq qo'llanmada — pastdagi tugma yoki /fullhelp.\n"
            "\n"
        ),
        "help_full_button": "📖 To'liq qo'llanma",
        "help_brief_button": "↩️ Qisqa variant",
        "help_page_everything": "To'liq qo'llanma: /fullhelp",
        "help_page_gone": "Bu yordam sahifasi endi yo'q. Hozirgilarini ko'rish uchun /help yuboring.",
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
            "Yozganingiz bu botlarni yuritayotgan odamga Telegram xabari sifatida yetib boradi — "
            "qaysi botdan kelgani va tilingiz bilan birga, lekin ismingiz, foydalanuvchi nomingiz "
            "va ID raqamingizsiz. Uni faqat o'sha odam o'qiydi va unga javob yoza olmaydi. "
            "Nusxasi botlar bazasida 30 kun saqlanadi, keyin o'chiriladi."
        ),
        "start_greeting": (
            "Salom! Menga havola yuboring (Instagram, TikTok, Pinterest, Reddit "
            "yoki Twitter/X) va men uni siz uchun yuklab beraman.\n\n"
        ),
        "help_text": 'Havola yuboring — faylni olib beraman, buyruq kerak emas:\n  - Instagram: reels, rasmlar, butun karusel\n  - TikTok: suv belgisiz video va rasm-slaydlar\n  - Twitter/X: video va rasmlar, matnli post uchun esa rasm-kartochka\n  - Pinterest: pin, asl o\'lchamda\n  - Reddit: media post bo\'lsa — media, matnli bo\'lsa — kartochka\n\nAlbom ostida ikkita tugma chiqadi: 📄 PDF — rasmlarni bitta PDF faylga jamlaydi, 🗜 ZIP — hamma narsani bitta arxivga soladi.\n\nBuyruqlar:\n/settings - izoh, fayl sifati va katta fayllar (48 MB dan katta, ⚡ evaziga) — hammasi bitta ekranda\n/caption on|off - fayllar ostidagi "via @{username}" yozuvini yoqish/o\'chirish\n/lossless on|off - fayllarni siqilmagan holda olish\n/donate - server xarajatlariga hissa qo\'shish (ixtiyoriy)\n/cancel - joriy amalni bekor qilish (bir nechta bo\'lsa, qaysi birini so\'rayman)\n/feedback - taklif yozish yoki muammo haqida xabar berish; uni faqat botlarni yuritayotgan odam o\'qiydi, javob qaytmaydi\n/en, /uz, /rus - tilni almashtirish (yoki /language)\n/balance - ⚡ kreditingiz\n/paysupport - to\'lov bilan muammo\n/privacy - siz haqingizda nimalarni saqlayman\n/terms - botdan foydalanish shartlari\n/deletemydata - siz haqingizdagi hamma narsani o\'chirish\n/start - bu bot nima qiladi va qanday boshlash kerak\n/fullhelp - shu to\'liq qo\'llanma (/help - qisqasi)\n\n⚠️ DIQQAT: To\'lovlar QAYTARILMAYDI — /donate orqali to\'langan Stars qaytarib berilmaydi, ular bergan ⚡ kreditni esa yechib olib BO\'LMAYDI.\n\n',
        "caption_state_on": "YONIQ",
        "caption_state_off": "O'CHIQ",
        "settings_heading": "⚙️ Sozlamalaringiz. O'zgartirish uchun bosing.",
        "caption_status": "Izoh hozir {state}.",
        "caption_turned": 'Fayl izohi endi {state}.',
        "caption_toggle_answer": 'Izoh endi {state}.',
        "lossless_state_on": "YONIQ",
        "lossless_state_off": "O'CHIQ",
        "lossless_status": "Siqilmagan rejim hozir {state}.\n\nYONIQ: fayllar asl sifatida, hujjat ko'rinishida keladi — Telegram ularni siqmaydi, lekin ochmaguningizcha chatda ko'rinmaydi va ijro etilmaydi.\nO'CHIQ: fayllar chatda darhol ko'rinadigan rasm va video bo'lib keladi, lekin Telegram ularni siqadi.",
        "lossless_turned": 'Siqilmagan holda yuklash endi {state}.',
        "lossless_toggle_answer": 'Siqilmagan rejim endi {state}.',
        "large_state_on": "YONIQ",
        "large_state_off": "O'CHIQ",
        "large_status": "📦 Katta fayllar hozir {state}.\n\nYONIQ: {free} MB dan katta yuklamani ham {max} MB gacha olib kelaman va fayl sifatida yuboraman — 100 MB gacha ⚡{per}, keyingi har 100 MB undan arzonroq ({max} MB uchun ⚡{top}). Kredit fayl yetib kelgandan keyingina yechiladi, xato bo'lsa umuman yechilmaydi.\nO'CHIQ: {free} MB dan kattasi rad etiladi va hech narsa yechilmaydi.",
        "large_toggle_answer": "Katta fayllar: {state}.",
        "large_files_turned_on": "📦 Katta fayllar yoqildi. Havolani yana yuboring — olib kelaman. 100 MB gacha ⚡{per}, keyingi har 100 MB undan arzonroq; kredit fayl yetib kelgach yechiladi. O'chirish uchun: /settings.",
        "large_files_turn_on_button": "📦 Katta fayllarni yoqish",
        "large_files_off": "📦 Bu fayl {free} MB dan katta. Uni baribir fayl qilib olib bera olaman — 100 MB gacha ⚡{per}, keyingi har 100 MB undan arzonroq, lekin avval katta fayllarni yoqishingiz kerak. Pastdagi tugmani bosing (yoki /settings), keyin havolani yana yuboring.",
        "large_files_private_only": "📦 Bu fayl {free} MB dan katta. Bunday fayllarni faqat men bilan shaxsiy chatda yuboraman — havolani o'sha yerga tashlang.",
        "large_files_no_credit": "📦 Bu fayl {free} MB dan katta, narxi kamida ⚡{per}, balansingizda esa ⚡{balance} bor. /donate orqali to'ldiring, keyin havolani yana yuboring.",
        "large_files_fetching": "📦 {free} MB dan katta — katta fayl sifatida olib kelyapman. 100 MB gacha ⚡{per}, keyingi har 100 MB undan arzonroq; kredit fayl yetib kelgach yechiladi.",
        "large_files_too_big": "📦 Bu fayl {max} MB dan katta — Telegram menga bundan kattasini yuborishga ruxsat bermaydi. Qisqaroq videoni sinab ko'ring.",
        "large_files_short": "📦 Fayl {mb} MB chiqdi, narxi ⚡{price}, balansingizda esa ⚡{balance} — shuning uchun hech narsa yechilmadi. /donate orqali to'ldiring va havolani yana yuboring.",
        "large_files_sending": "📦 {mb} MB tayyor. Yuboryapman...",
        "large_files_send_failed": "📦 Faylni oldim, lekin sizga yubora olmadim. ⚡{price} qaytarildi — balansingiz ⚡{balance}.",
        "large_files_charged": "📦 {mb} MB yuborildi. Buning uchun ⚡{price} yechildi — balansingiz endi ⚡{balance}.",
        "download_credit_caption": "⬇️ @{username} orqali",
        "downloading": "Yuklanmoqda...",
        "queued": '⏳ Boshqa fayl yuklanmoqda — sizniki birozdan keyin boshlanadi.',
        "download_queue_full": 'Sizning {count} ta havolangiz yuklanmoqda. Ulardan biri kelgach, yana yuboring.',
        "download_short_on_space": 'Hozir vaqtinchalik joy yetishmayapti — havolani bir necha daqiqadan keyin qayta yuboring.',
        "restarting_send_again": '🔄 Bot hozir yangilanmoqda — bir necha soniyadan keyin qaytadan yuboring.',
        "update_soon_try_later": "🔧 Bot tez orada yangilanadi, shuning uchun yangi ishni boshlab bo'lmaydi — taxminan {minutes} daqiqadan keyin qayta urinib ko'ring. Ishga tushgach, o'zim xabar beraman.",
        "update_soon_try_later_soon": "🔧 Bot hozir yangilanmoqda, shuning uchun yangi ishni boshlab bo'lmaydi — birozdan keyin qayta urinib ko'ring. Ishga tushgach, o'zim xabar beraman.",
        "update_will_reset": "🔧 Diqqat: bot yangilanadi va hozir bajarilayotgan ishingiz to'xtab qoladi. Bir necha daqiqadan keyin qaytadan boshlashingiz mumkin.",
        "update_done_try_now": "✅ Yangilanish tugadi — endi qaytadan urinib ko'rishingiz mumkin.",
        "gift_granted": "🎁 Bizda 100 dan ortiq foydalanuvchi bo'ldi, rahmat! Sizga {amount} ⚡ bepul kredit berildi, u {date} gacha amal qiladi. Bu {convert} orqali taxminan {conversions} ta pullik konvertatsiya yoki {download} orqali katta fayllarni yuklab olish uchun yetadi. Balansingiz: /balance",
        "gift_claimed": "🎁 Sovg'a: {amount} ⚡ bepul kredit! Mumu botlarida {users} dan ortiq foydalanuvchi bo'ldi, bu bizning minnatdorchiligimiz.\nU {date} gacha (UTC) amal qiladi va {convert} orqali taxminan {conversions} ta pullik konvertatsiya yoki {download} orqali katta fayllarni yuklab olish uchun yetadi.\nBalansingiz: /balance",
        "download_failed": "Buni yuklab bo'lmadi: {error}",
        "download_blocked": '🚫 Buni olishning barcha yo‘llarini sinab ko‘rdim, sayt hammasini rad etdi. Bu havolangizga emas, so‘rov qayerdan kelganiga bog‘liq. Birozdan so‘ng qayta urinib ko‘ring — odatda o‘zi tuzalib ketadi.',
        "download_missing": '🔍 Bu postni topa olmadim. Odatda bu post o‘chirilgan yoki yopiq akkauntdan ekanini bildiradi. Ochiq postlar ishlayveradi.',
        "download_too_big": '📦 Bu fayl Telegram ruxsat beradigan hajmdan katta. Qisqaroq video sinab ko‘ring.',
        "download_all_routes_failed": '😕 Bu ishlamadi, men bilgan barcha yo‘llarni sinab ko‘rdim. Bir daqiqadan so‘ng yana yuboring — agar takrorlansa, muammo havolada emas, saytda.',
        "fetching": "Olinmoqda...",
        "reddit_fetch_failed": "Bu Reddit postini olib bo'lmadi: {error}",
        "twitter_fetch_failed_link": "Hozircha bu post mazmunini olib bo'lmadi — mana havola: {url}",
        "unrecognized_message": "Bu tanish havolaga o'xshamayapti. Instagram, TikTok, Pinterest, Reddit yoki Twitter/X havolasini yuboring — buyruqlar ro'yxati: /help",
        "redeliver_as_file": "📄 Siqilmagan fayl sifatida olish",
        "redeliver_as_compressed": "🖼 Siqilgan holda olish",
        "redeliver_expired": "Bu tugmaning muddati o'tgan — havolani qaytadan yuboring yoki fayllar qanday kelishini /lossless orqali o'zgartiring.",
        "album_delivered": "Yuqorida {count} ta fayl.",
        "merge_too_big": "📦 Bunchasini bitta faylga joylab bo'lmaydi — ko'pi bilan {items} ta fayl va jami {mb} MB. Fayllarning o'zi yuqorida turibdi.",
        "merge_picture_too_big": "📦 Rasmlardan biri PDF uchun juda katta ({mp} megapikseldan katta), shuning uchun bu safar PDF chiqmaydi. Rasmlarning o'zi yuqorida turibdi.",
        "merge_no_photos": "📄 PDF uchun kamida ikkita rasm kerak, postda esa bu safar ikkitadan kam rasm topildi. Havolani qayta yuboring yoki fayllarni yuqoridan oling.",
        "merge_failed": "😕 Fayllarni bitta qilib yig'ishda nimadir xato ketdi, shuning uchun bu safar PDF ham, ZIP ham chiqmadi. Yuqoridagi fayllar joyida — qayta urinish uchun havolani yana yuboring.",
        "unknown_command": "Bunday buyruq yo'q. Bot nimalar qila olishini bilish uchun /help yuboring.",
    },
    "ru": {
        "quota_hour": (
            "За последний час вы скачали {used} — это предел этого бота ({limit} в час). "
            "Он нужен, чтобы на маленьком сервере хватало всем. Следующая попытка "
            "освободится примерно через {minutes} минут(ы)."
        ),
        "quota_day": (
            "Сегодня вы скачали {used} — это дневной предел бота ({limit}). Он нужен, "
            "чтобы на маленьком сервере хватало всем. Снова откроется примерно через "
            "{minutes} минут(ы)."
        ),
        "quota_donor_hint": (
            "Если нужно больше: у всех, кто хоть раз поддержал бота через /donate, "
            "предел заметно выше и навсегда. Сумма значения не имеет."
        ),
        "flood_wait": "Вы отправляете быстрее, чем я успеваю — подождите примерно {seconds} секунд(ы) и продолжайте.",
        "sibling_blurb": "Тоже часть этой семьи ботов, смотрите ниже \U0001f447",
        "donation_nudge": (
            "💙 Если этот бот оказался полезным: расходы на хостинг/API покрывает тот, "
            "кто его запустил, а /donate — это совершенно необязательный способ помочь "
            "ему остаться на плаву. Никакого давления в любом случае!"
        ),
        "donate_unknown_currency": 'Неизвестная валюта "{currency}" — попробуйте xtr или usd.',
        "donate_currency_not_configured": "Пожертвования в {currency} на этом боте пока не настроены — попробуйте Stars.",
        "donate_invalid_amount": "Это некорректная сумма — попробуйте, например, /donate 500 или /donate 5 usd.",
        "donate_prompt": (
            "Спасибо за вклад — эти средства идут прямо на хостинг и API этого "
            "бота. Выберите сумму ниже или нажмите «Другое», чтобы ввести свою "
            "(также можно сразу отправить /donate <число> [usd])."
        ),
        "donate_custom_button": "✏️ Другое {symbol}",
        "donate_too_many_stars": "Это очень много звёзд! Пусть будет меньше {max} ⭐ за одно пожертвование.",
        "donate_out_of_range": "Пожертвования в {currency} должны быть в диапазоне от {lo} до {hi} {symbol}.",
        "donate_invoice_title": "Поддержать хостинг",
        "donate_invoice_description": "Идёт на расходы по работе бота и добавляет {credited} ⚡ на ваш баланс для конвертаций в ConvertBot.",
        "donate_invoice_label": "Вклад в хостинг",
        "donate_invoice_description_fiat": 'Разовое добровольное пожертвование на хостинг. Спасибо!',
        "donate_prompt_credit": 'Оплаченные Stars превращаются в ⚡ кредит на конвертации в ConvertBot. Следующие {left} ⭐ дают по {each} ⚡ ({mult}×): {rate} ⚡ обычного кредита и бонус, который сгорает через {days} дней после оплаты. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.',
        "donate_prompt_credit_base": 'Оплаченные Stars превращаются в ⚡ кредит на конвертации в ConvertBot, {rate} ⚡ за ⭐. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.',
        "donate_invoice_error": "⚠️ Telegram не смог создать этот счёт: {error}",
        "stars_unit": "Stars (звёзды)",
        "donate_custom_ask": "Сколько {unit} вы хотите пожертвовать? Ответьте числом.",
        "donate_invalid_amount_retry": "Это некорректная сумма — отправьте /donate, чтобы попробовать снова.",
        "donate_thanks": "🙏 Спасибо за {amount} ⭐ — это по-настоящему ценно!",
        "topup_thanks": '🙏 Спасибо за пожертвование в {stars} ⭐ — это помогает ботам работать.\n\nВ благодарность вы получили {total} ⚡ кредита в {convert_bot} — его можно тратить на конвертацию файлов. Ваш баланс там: {balance} ⚡.',
        "topup_thanks_bonus": 'Из них {bonus} ⚡ — бонусный кредит, он сгорит {date}.',
        "credit_cannot_be_withdrawn": '⚡ — это кредит на конвертации в ConvertBot, его НЕЛЬЗЯ вывести или обменять обратно на Stars. Проблема с платежом? /paysupport',
        "paysupport_text": '💳 Помощь с платежом\n\nПлатежи ОКОНЧАТЕЛЬНЫЕ: Stars НЕ возвращаются, а ⚡ кредит НЕЛЬЗЯ вывести или обменять обратно на Stars.\n\nЕсли с платежом что-то пошло не так — деньги списали, а кредит не пришёл, или списали дважды, — напишите на {contact}: дату, сумму и ваш Telegram ID, {user_id}. Это проверят и исправят кредитом.\n\n/balance показывает каждый платёж и что он добавил.',
        "report_button": '🐞 Добавить мои данные',
        "problem_logged_note": 'Эта проблема уже записана для владельца бота — код, время и версия. О вас там ничего нет.',
        "report_disclaimer": '📨 Добавить к этой проблеме ваши данные?\n\nУже записано, без вас: название бота, код ошибки {code}, номер случая {incident}, когда это произошло и версия бота.\n\nКнопка «Отправить» добавит четыре вещи: ваш Telegram ID, ваш @username, если он есть, выбранный вами язык и то, личный это чат или группа. Ни того, что вы написали, ни отправленного файла.\n\nТогда ваш случай можно будет найти в логах — обычно именно это и позволяет починить редкую проблему. Решать только вам.',
        "report_send": '📨 Отправить мои данные',
        "report_cancel": '✖️ Отмена',
        "report_sent": '✅ Спасибо — ваши данные теперь на случае {incident}, так его легко найти.',
        "report_already": 'Ваши данные уже на случае {incident}. Больше ничего не отправлено.',
        "report_cancelled": 'Отменено — о вас ничего не отправлено. Сама проблема остаётся записанной.',
        "report_failed": '⚠️ Сейчас не удалось отправить сообщение. Попробуйте позже.',
        "report_invalid": 'Эта кнопка больше не работает.',
        "crash_notice": '⚠️ При обработке произошла ошибка на стороне бота, поэтому ничего не сделано. Попробуйте ещё раз чуть позже.',
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
            "• ваш числовой id в Telegram, язык и настройки подписи и качества\n"
            "• ссылки, которые вы присылаете: каждую — пока загружается то, что по ней, затем "
            "последние пять, чтобы работали кнопки под этими загрузками. Они удаляются, если вы "
            "не пользуетесь ботом 12 часов\n"
            "• то, что бот только что вам отправил, — 5 минут, чтобы кнопка под ним и та же ссылка, "
            "присланная снова, срабатывали без повторной загрузки. Другая ссылка, /deletemydata "
            "или перезапуск бота удаляют это раньше. Всё это удаляется не позже чем через "
            "12 часов после скачивания\n"
            "• file ID Telegram для файлов, которые бот уже отправлял, — под необратимым кодом "
            "поста, без сведений о том, кто его запрашивал. Они позволяют отправить тот же пост "
            "ещё раз, не скачивая его заново. Прежде чем снова использовать file ID, бот не "
            "реже чем раз в 12 часов проверяет, что пост всё ещё публичный. File ID, которыми "
            "никто не пользуется, удаляются через 90 дней\n"
            "• по одной записи на скачивание: с какой площадки и когда — по ним считается "
            "дневной лимит\n"
            "• отметку времени на каждое обращение к боту — чтобы владелец видел, пользуется ли "
            "ботом хоть кто-нибудь\n"
            "• сколько раз нажимают каждую кнопку — общий счёт за день по всему боту, без сведений о том, кто нажимал\n"
            "• запись о пожертвовании: сумму и платёжный id Telegram\n"
            "• то, что бот в этот момент для вас делает — пока не закончит\n"
            "\n"
            "Кроме этих 5 минут, сами файлы не хранятся: они загружаются, отправляются вам и "
            "удаляются."
        ),
        "privacy_problems_heading": "Когда что-то ломается:",
        "privacy_problems": "Каждую ошибку, которую бот кому-то показывает, он записывает сам: код ошибки, номер случая, когда это произошло и версию бота. О вас там нет ничего — ни вашего id, ни имени, ни того, что вы отправили. Сбой, о котором никто не сообщил, остаётся сбоем, поэтому бот не ждёт, пока его попросят.\n\nКнопка «Добавить мои данные» под ошибкой предлагает привязать к этому случаю четыре вещи: ваш Telegram id, ваш @username, если он есть, выбранный вами язык и то, личный это чат или группа. Все четыре сначала названы на экране, и ничего не отправляется, пока вы не нажмёте «Отправить». Того, что вы написали, и отправленных файлов там не бывает никогда.\n\nЭти четыре стираются сами через 30 дней, а /deletemydata стирает их сразу. Запись об ошибке остаётся — она действительно произошла — просто перестаёт говорить, кто на неё наткнулся. Сами записи удаляются через 180 дней.",
        "privacy_seen_by_heading": "Кто ещё это видит:",
        "privacy_seen_by": (
            "• Telegram — он передаёт каждое сообщение в обе стороны и действует по своим "
            "правилам\n"
            "• хостинг, на котором работает бот, и база данных, в которую он пишет"
        ),
        "privacy_others": (
            "• сайт, на который ведёт ссылка, и сервисы скачивания, к которым бот обращается, "
            "когда сайт не отвечает ему напрямую. Они получают ссылку и видят запрос с сервера "
            "бота, а не от вас; что они у себя пишут в логи — их дело и их правила, а не эти."
        ),
        "privacy_kept_for_heading": "Сколько это хранится:",
        "privacy_kept_for": 'Настройки и всё, что бот держит для вас, остаются, пока вы их не сотрёте или не перестанете пользоваться ботом. Отметки об использовании удаляются примерно через три месяца. Записи о платежах и баланс ⚡ хранятся дольше — по ним разбираются споры о платежах.\n\nНичего из этого не продаётся, не сдаётся в аренду и не используется для рекламы, и никому кроме перечисленных выше не передаётся.',
        "privacy_your_choices": (
            "Что можно сделать:\n"
            "/deletemydata — стереть всё, что бот хранит о вас\n"
            "/terms — для чего ботом можно пользоваться\n"
            "\n"
            "Блокировка бота в Telegram остановит его сообщения, но ничего не сотрёт — если "
            "нужно и то и другое, сначала отправьте /deletemydata."
        ),
        "terms_heading": "📜 Условия",
        "terms_use": (
            "Пользуйтесь ботом по назначению, в рамках закона и правил самого Telegram. Не "
            "используйте его, чтобы кого-то донимать, и не нагружайте сверх заданных лимитов — за то "
            "и другое аккаунт блокируется."
        ),
        "terms_specific": (
            "О скачивании: бот достаёт то, что и так открыто, и отдаёт вам. Это не обход "
            "закрытого аккаунта, и он даже не пытается им быть. Что вы потом сделаете с чужим "
            "фото, видео или постом — это между вами, автором и законом: скачать не значит "
            "присвоить. На каждого есть дневной лимит, чтобы один человек не сделал бота "
            "бесполезным для всех остальных."
        ),
        "terms_money": 'О деньгах: /donate — дело добровольное, деньги идут на то, во что обходятся боты. Платёж в Stars ещё и добавляет ⚡ кредит на конвертации в ConvertBot: 2 ⚡ за ⭐, а за ваши первые 500 Stars — 6 и за следующие 500 — 4. Всё сверх 2 — бонусный кредит, он сгорает через 90 дней после платежа. Платежи ОКОНЧАТЕЛЬНЫЕ: Stars НЕ возвращаются, а кредит НЕЛЬЗЯ вывести или обменять обратно на Stars. Все платежи проводит Telegram, бот никогда не видит номер карты. Проблема с платежом — /paysupport.',
        "terms_no_warranty": (
            "Без обещаний: бота ведёт один человек, он бесплатный и может тормозить, ошибаться "
            "или вовсе не работать без предупреждения. Держите свою копию всего, что вам важно."
        ),
        "policy_full_text": "Полный текст: {url}",
        "policy_contact": "Вопросы, жалобы или запрос по данным: {contact}",
        "delete_data_confirm": "⚠️ Это сотрёт всё, что бот хранит о вас. Отменить будет нельзя.",
        "delete_data_consequences": 'Язык, настройки подписи и качества и все записи о том, что и когда вы скачивали, будут стёрты. Дневной лимит вместе с ними обнулится. Заодно удаляется последняя загрузка, если бот ещё её хранит; более ранние загрузки здесь никогда не хранились.\n\nЗаписи о пожертвованиях останутся, без username, потому что по ним разбираются споры о платежах. Баланс ⚡ тоже останется — он ваш, если вернётесь.',
        "delete_data_button_yes": "🗑 Стереть",
        "delete_data_button_no": "↩️ Оставить мои данные",
        "delete_data_kept": "Ничего не стёрто.",
        "delete_data_done": (
            "🗑 Готово — стёрто записей: {rows}.\n"
            "\n"
            "Отправьте /start когда захотите; бот примет вас как нового."
        ),
        "delete_data_failed": (
            "Сейчас стереть не получилось — что-то сломалось на моей стороне. Попробуйте ещё раз "
            "через несколько минут."
        ),
        "language_set_confirmation": "✅ Язык изменён на русский.",
        "cancel_header": "\u274c \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e:",
        "cancel_nothing": "\u041e\u0442\u043c\u0435\u043d\u044f\u0442\u044c \u043d\u0435\u0447\u0435\u0433\u043e — \u044f \u043d\u0438\u0447\u0435\u0433\u043e \u043e\u0442 \u0432\u0430\u0441 \u043d\u0435 \u0436\u0434\u0430\u043b.",
        "cancel_ask": "Что остановить? Вот что я жду:",
        "cancel_kept": "Хорошо — ничего не отменено.",
        "cancel_reply_box_freed": "Поле ответа снова свободно.",
        "cancel_button_all": "❌ Всё",
        "cancel_button_none": "↩️ Ничего, продолжаем",
        "cancel_button_donation": "💸 Сумма пожертвования",
        "cancel_item_donation": "\u0441\u0443\u043c\u043c\u0430 \u043f\u043e\u0436\u0435\u0440\u0442\u0432\u043e\u0432\u0430\u043d\u0438\u044f, \u043a\u043e\u0442\u043e\u0440\u0443\u044e \u044f \u0437\u0430\u043f\u0440\u043e\u0441\u0438\u043b",
        "cancel_item_stale_prompt": "старый запрос, который всё ещё ждал ответа",
        "feedback_ask": (
            "💬 Что вы хотите сказать тому, кто ведёт этих ботов? Идея, проблема, что угодно — "
            "напишите одним сообщением в ответ на это.\n"
            "\n"
            "Его прочитает только он, и ответить вам он не сможет: сообщение уходит без вашего "
            "имени и id. Остановить: /cancel"
        ),
        "feedback_placeholder": "Ваш отзыв",
        "feedback_text_only": "Сюда нужен текст — напишите отзыв словами в ответ на это сообщение или отправьте /cancel.",
        "feedback_thanks": "✅ Спасибо — отправлено. Это прочитает только тот, кто ведёт этих ботов, и ответить он не сможет: в сообщении нет ничего о том, кто вы.",
        "feedback_limit": "Через /feedback сегодня пришло уже {per_day} ваших сообщений — больше за день я не принимаю. Спасибо, напишите завтра.",
        "feedback_failed": "Отзыв не отправлен — что-то сломалось на моей стороне. Попробуйте ещё раз через несколько минут.",
        "cancel_button_feedback": "💬 Отзыв",
        "cancel_item_feedback": "отзыв, который я просил написать",
        "help_brief": (
            "Как это устроено:\n"
            "\n"
            "• Пришлите ссылку — пришлю то, что по ней лежит, без всяких команд. Instagram, TikTok, X, Pinterest и Reddit.\n"
            "• /settings: подпись «via», качество файлов и большие файлы больше 48 МБ — они стоят ⚡ кредит и работают, только если их включить.\n"
            "• Идеи или проблемы: /feedback — это прочитает только тот, кто ведёт этих ботов, ответа не будет.\n"
            "\n"
            "Основные команды: /settings · /balance · /cancel\n"
            "Все команды — в полном руководстве: кнопка ниже или /fullhelp.\n"
            "\n"
        ),
        "help_full_button": "📖 Полное руководство",
        "help_brief_button": "↩️ Кратко",
        "help_page_everything": "Полное руководство: /fullhelp",
        "help_page_gone": "Этой страницы справки больше нет. Отправьте /help, чтобы увидеть актуальные.",
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
            "Написанное уходит тому, кто ведёт этих ботов, сообщением в Telegram — вместе с "
            "названием бота и вашим языком, но без имени, username и id. Копия хранится в базе "
            "ботов 30 дней, потом удаляется. Читает его только он, и ответить на него он не может."
        ),
        "start_greeting": (
            "Здравствуйте! Пришлите мне ссылку (Instagram, TikTok, Pinterest, Reddit "
            "или Twitter/X), и я скачаю это для вас.\n\n"
        ),
        "help_text": 'Просто пришлите ссылку в любое время — я её заберу, команда не нужна:\n  - Instagram: reels, фото, карусели целиком\n  - TikTok: видео без водяного знака и фото-слайдшоу\n  - Twitter/X: видео и фото, а для текстового поста — аккуратная карточка\n  - Pinterest: пин в полном размере\n  - Reddit: медиа, если это медиа-пост, или карточка, если это текст\n\nПод альбомом появятся две кнопки: 📄 PDF собирает фото в один PDF, 🗜 ZIP — всё в один архив.\n\nКоманды:\n/settings - подпись, качество файлов и большие файлы (больше 48 МБ, за ⚡), всё на одном экране\n/caption on|off - подпись "via @{username}" на загрузках, отдельно\n/lossless on|off - получать загрузки несжатыми файлами\n/donate - помочь с расходами на хостинг (совершенно необязательно)\n/cancel - остановить то, чего я от вас жду (спрошу, что именно)\n/feedback - предложить идею или сообщить о проблеме; это прочитает только тот, кто ведёт этих ботов, ответа не будет\n/en, /uz, /rus - сменить язык (или /language — он спрашивает)\n/balance - ваш ⚡ кредит\n/paysupport - проблема с оплатой\n/privacy - что я о вас храню\n/terms - для чего можно пользоваться ботом\n/deletemydata - удалить всё, что я о вас храню\n/start - что этот бот делает и с чего начать\n/fullhelp - это полное руководство (/help - краткое)\n\n⚠️ ВНИМАНИЕ: платежи окончательные — Stars, оплаченные через /donate, НЕ возвращаются, а добавленный ими ⚡ кредит НЕЛЬЗЯ вывести.\n\n',
        "caption_state_on": "ВКЛ",
        "caption_state_off": "ВЫКЛ",
        "settings_heading": "⚙️ Ваши настройки. Нажмите, чтобы изменить.",
        "caption_status": "Подпись сейчас {state}.",
        "caption_turned": "Подпись к загрузкам теперь {state}.",
        "caption_toggle_answer": "Подпись теперь {state}.",
        "lossless_state_on": "ВКЛ",
        "lossless_state_off": "ВЫКЛ",
        "lossless_status": "Режим без потерь сейчас {state}.\n\nВКЛ: загрузки приходят файлом, ровно такими, какими были в источнике — Telegram их не пережимает, но они не проигрываются в чате, пока вы их не откроете.\nВЫКЛ: загрузки приходят фото и видео, которые играют прямо в чате, со сжатием Telegram.",
        "lossless_turned": "Загрузка без потерь теперь {state}.",
        "lossless_toggle_answer": "Режим без потерь теперь {state}.",
        "large_state_on": "ВКЛ",
        "large_state_off": "ВЫКЛ",
        "large_status": "📦 Большие файлы сейчас {state}.\n\nВКЛ: загрузку больше {free} МБ я всё равно скачаю — до {max} МБ — и пришлю файлом: ⚡{per} до 100 МБ, каждые следующие 100 МБ дешевле ({max} МБ — ⚡{top}). Списываю, только когда файл уже скачан, и ничего — если не получилось.\nВЫКЛ: всё, что больше {free} МБ, отклоняется, и ничего не списывается.",
        "large_toggle_answer": "Большие файлы: {state}.",
        "large_files_turned_on": "📦 Большие файлы включены. Пришлите ссылку ещё раз — я скачаю. ⚡{per} до 100 МБ, каждые следующие 100 МБ дешевле; спишу, когда файл уже будет у меня. Выключить: /settings.",
        "large_files_turn_on_button": "📦 Включить большие файлы",
        "large_files_off": "📦 Этот файл больше {free} МБ. Я всё равно могу прислать его файлом — ⚡{per} до 100 МБ, каждые следующие 100 МБ дешевле, — но только если вы включите большие файлы. Нажмите кнопку ниже (или /settings), потом пришлите ссылку ещё раз.",
        "large_files_private_only": "📦 Этот файл больше {free} МБ. Такие файлы я присылаю только в личном чате — отправьте ссылку туда.",
        "large_files_no_credit": "📦 Этот файл больше {free} МБ, это стоит минимум ⚡{per}, а на балансе ⚡{balance}. Пополните через /donate и пришлите ссылку ещё раз.",
        "large_files_fetching": "📦 Больше {free} МБ — скачиваю как большой файл. ⚡{per} до 100 МБ, каждые следующие 100 МБ дешевле; спишу, когда он будет у меня.",
        "large_files_too_big": "📦 Этот файл больше {max} МБ — больше Telegram мне отправлять не позволяет. Попробуйте ролик покороче.",
        "large_files_short": "📦 Получилось {mb} МБ, это ⚡{price}, а на балансе ⚡{balance} — поэтому ничего не списано. Пополните через /donate и пришлите ссылку ещё раз.",
        "large_files_sending": "📦 {mb} МБ скачано. Отправляю...",
        "large_files_send_failed": "📦 Я скачал файл, но не смог его вам отправить. ⚡{price} вернулись — на балансе ⚡{balance}.",
        "large_files_charged": "📦 {mb} МБ, отправлено. За это ⚡{price} — на балансе теперь ⚡{balance}.",
        "download_credit_caption": "⬇️ через @{username}",
        "downloading": "Скачивание...",
        "queued": "⏳ Занят другой загрузкой — ваша начнётся через мгновение.",
        "download_queue_full": 'У вас уже {count} ссылок в работе. Присылайте ещё, когда какие-то придут.',
        "download_short_on_space": 'Сейчас не хватает временного места — пришлите ссылку снова через пару минут.',
        "restarting_send_again": "🔄 Сейчас обновляюсь — подождите несколько секунд и отправьте ещё раз.",
        "update_soon_try_later": '🔧 Сейчас меня обновляют, поэтому я не могу начать ничего нового — попробуйте снова примерно через {minutes} мин. Я напишу, когда вернусь.',
        "update_soon_try_later_soon": '🔧 Сейчас меня обновляют, поэтому я не могу начать ничего нового — попробуйте снова чуть позже. Я напишу, когда вернусь.',
        "update_will_reset": '🔧 Внимание: меня скоро обновят, и то, что вы сейчас начали, будет сброшено. Через несколько минут сможете начать заново.',
        "update_done_try_now": '✅ Обновление завершено — можете пробовать снова.',
        "gift_granted": '🎁 Нас уже больше 100 — спасибо! Вам начислено {amount} ⚡ бесплатного кредита, он действует до {date}. Этого хватит примерно на {conversions} платных конвертаций в {convert} или на скачивание больших файлов в {download}. Ваш баланс: /balance',
        "gift_claimed": "🎁 Подарок: {amount} ⚡ бесплатного кредита! У Mumu ботов уже больше {users} пользователей — это наше спасибо.\nОн действует до {date} (UTC). Этого хватит на платные конвертации (около {conversions}) в {convert} или на скачивание больших файлов в {download}.\nВаш баланс: /balance",
        "download_failed": "Не удалось это скачать: {error}",
        "download_blocked": '🚫 Я перебрал все способы достать это, и сайт отказал всем. Дело не в ссылке, а в том, откуда пришёл запрос. Попробуйте через некоторое время — обычно это проходит само.',
        "download_missing": '🔍 Не нашёл этот пост. Обычно это значит, что его удалили или он из закрытого аккаунта. Публичные посты работают.',
        "download_too_big": '📦 Это больше, чем Telegram позволяет мне отправить. Попробуйте ролик покороче.',
        "download_all_routes_failed": '😕 Не получилось, а я пробовал все способы. Подождите минуту и пришлите снова — если повторяется, дело в сайте, а не в ссылке.',
        "fetching": "Загрузка...",
        "reddit_fetch_failed": "Не удалось получить этот пост Reddit: {error}",
        "twitter_fetch_failed_link": "Не удалось получить содержимое поста прямо сейчас — вот ссылка: {url}",
        "unrecognized_message": (
            "Это не похоже на ссылку, которую я узнаю — Instagram, TikTok, "
            "Pinterest, Reddit или Twitter/X — пришлите одну из них, чтобы скачать, "
            "или /help для списка команд."
        ),
        "redeliver_as_file": "📄 Получить несжатым файлом",
        "redeliver_as_compressed": "🖼 Получить сжатым",
        "redeliver_expired": (
            "Для кнопки это уже слишком старая загрузка — пришлите ссылку заново или "
            "поменяйте способ доставки через /lossless."
        ),
        "album_delivered": "Выше {count} файл(ов).",
        "merge_too_big": "📦 Слишком много, чтобы собрать в один файл. Мой предел: файлов — {items}, общий размер — {mb} МБ. Всё это по-прежнему в чате выше.",
        "merge_picture_too_big": "📦 Одно из фото слишком большое для PDF (больше {mp} мегапикселей), поэтому в этот раз PDF не получится. Сами фото — в чате выше.",
        "merge_no_photos": "📄 Для PDF нужно хотя бы два фото, а в этом посте сейчас нашлось меньше. Пришлите ссылку заново или возьмите файлы из чата выше.",
        "merge_failed": "😕 Что-то пошло не так, пока я собирал всё в один файл, — ни PDF, ни ZIP в этот раз не вышло. Файлы выше целы; пришлите ссылку заново, чтобы попробовать ещё раз.",
        "unknown_command": "Я не знаю такую команду. Отправьте /help, чтобы увидеть, что я умею.",
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
